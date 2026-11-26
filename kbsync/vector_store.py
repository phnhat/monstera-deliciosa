"""OpenAI Vector Store operations: resolve the store, list it, upload chunk files,
retire old versions.

Upload flow per run: create File objects in parallel, then attach them with
`file_batches.create_and_poll` (per-file attributes + static 1024/0 chunking).
An article counts as uploaded only if every one of its chunks was attached;
only then are its old chunk files deleted, so a failed update keeps serving the
previous version.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import islice

import openai

from kbsync.chunking import OPENAI_CHUNKING, Chunk
from kbsync.sync import Delta, LocalArticle, RemoteFile

log = logging.getLogger(__name__)
BATCH_SIZE = 500  # API limit is 2000 files per batch
STALE_MARKER = "stale-demo"


def chunk_attributes(art: LocalArticle, chunk: Chunk) -> dict:
    a = art.article
    return {
        "article_id": str(a.id),
        "content_hash": art.content_hash,
        "chunk_index": chunk.index,
        "chunk_count": len(art.chunks),
        "slug": a.slug,
        "url": a.url[:512],
        "title": a.title[:512],
        "updated_at": a.updated_at,
    }


def resolve_store(client, store_id: str | None, name: str) -> str:
    if store_id:
        client.vector_stores.retrieve(store_id)  # fail fast on a wrong id
        return store_id
    for vs in client.vector_stores.list(limit=100):
        if vs.name == name:
            log.info("Using vector store %s (%s)", vs.id, name)
            return vs.id
    vs = client.vector_stores.create(name=name)
    log.info("Created vector store %s (%s) — set VECTOR_STORE_ID=%s to pin it", vs.id, name, vs.id)
    return vs.id


@dataclass
class ApplyResult:
    added: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)
    files_uploaded: int = 0
    files_deleted: int = 0


class Store:
    def __init__(self, client, vector_store_id: str, workers: int = 8):
        self.client = client
        self.id = vector_store_id
        self.workers = workers

    # --- reads -----------------------------------------------------------------
    def list_files(self) -> list:
        return list(self.client.vector_stores.files.list(vector_store_id=self.id, limit=100))

    def file_count(self) -> int:
        return self.client.vector_stores.retrieve(self.id).file_counts.completed

    # --- writes ----------------------------------------------------------------
    def upload(self, arts: list[LocalArticle]) -> tuple[set[int], dict[int, str], dict[int, list[str]]]:
        """Returns (ok article ids, failed {id: reason}, new file ids per article)."""
        failed: dict[int, str] = {}
        new_ids: dict[int, list[str]] = defaultdict(list)
        owner: dict[str, int] = {}
        entries: list[dict] = []

        jobs = [(art, chunk) for art in arts for chunk in art.chunks]
        with ThreadPoolExecutor(self.workers) as pool:
            results = list(pool.map(lambda job: self._create_file(job[1]), jobs))
        for (art, chunk), (file_id, error) in zip(jobs, results):
            aid = art.article.id
            if error:
                failed.setdefault(aid, f"file upload failed: {error}")
                continue
            new_ids[aid].append(file_id)
            owner[file_id] = aid
            entries.append({"file_id": file_id, "attributes": chunk_attributes(art, chunk),
                            "chunking_strategy": OPENAI_CHUNKING})

        it = iter(entries)
        while batch := list(islice(it, BATCH_SIZE)):
            self._attach(batch, owner, failed)

        ok = {art.article.id for art in arts} - failed.keys()
        return ok, failed, dict(new_ids)

    def _create_file(self, chunk: Chunk) -> tuple[str | None, str | None]:
        try:
            f = self.client.files.create(file=(chunk.filename, chunk.text.encode(), "text/markdown"), purpose="assistants")
            return f.id, None
        except openai.OpenAIError as e:
            return None, str(e)

    def _attach(self, batch: list[dict], owner: dict[str, int], failed: dict[int, str]) -> None:
        try:
            result = self.client.vector_stores.file_batches.create_and_poll(vector_store_id=self.id, files=batch)
        except openai.OpenAIError as e:
            for entry in batch:
                failed.setdefault(owner[entry["file_id"]], f"batch attach failed: {e}")
            return
        log.info("Attached batch %s: %s", result.id, result.file_counts)
        if result.status != "completed":
            for entry in batch:
                failed.setdefault(owner[entry["file_id"]], f"batch {result.status}")
            return
        if result.file_counts.failed or result.file_counts.cancelled:
            for status in ("failed", "cancelled"):
                for f in self.client.vector_stores.file_batches.list_files(
                        batch_id=result.id, vector_store_id=self.id, filter=status, limit=100):
                    reason = getattr(getattr(f, "last_error", None), "message", None) or status
                    failed.setdefault(owner.get(f.id, -1), f"chunk {status}: {reason}")

    def delete(self, file_ids: list[str]) -> int:
        if not file_ids:
            return 0
        with ThreadPoolExecutor(self.workers) as pool:
            return sum(pool.map(self._delete_one, file_ids))

    def _delete_one(self, file_id: str) -> int:
        for call in (lambda: self.client.vector_stores.files.delete(file_id=file_id, vector_store_id=self.id),
                     lambda: self.client.files.delete(file_id)):
            try:
                call()
            except openai.NotFoundError:
                pass  # already gone (e.g. never attached)
        return 1

    def mark_stale(self, files: list[RemoteFile]) -> int:
        for f in files:
            self.client.vector_stores.files.update(
                file_id=f.file_id, vector_store_id=self.id,
                attributes={**f.attributes, "content_hash": STALE_MARKER})
        return len(files)


def apply_delta(store: Store, delta: Delta) -> ApplyResult:
    result = ApplyResult()
    to_upload = delta.added + [art for art, _ in delta.updated]
    ok, failed, new_ids = store.upload(to_upload) if to_upload else (set(), {}, {})
    result.failed = failed
    result.files_uploaded = sum(len(ids) for aid, ids in new_ids.items() if aid in ok)

    # Remove partial uploads of failed articles, then retire old versions of
    # successfully updated ones, then drop articles that disappeared upstream.
    result.files_deleted += store.delete([fid for aid in failed for fid in new_ids.get(aid, [])])
    retired = [f.file_id for art, files in delta.updated if art.article.id in ok for f in files]
    result.files_deleted += store.delete(retired)
    result.files_deleted += store.delete([f.file_id for _, files in delta.removed for f in files])

    result.added = [a.article.id for a in delta.added if a.article.id in ok]
    result.updated = [a.article.id for a, _ in delta.updated if a.article.id in ok]
    result.removed = [aid for aid, _ in delta.removed]
    return result
