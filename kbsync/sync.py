"""Delta planning: compare freshly scraped articles with what the vector store holds.

State lives on the vector-store files themselves (attributes), so the job is
stateless: every run lists the store, groups chunk files by article_id and
decides per article whether to add, update, skip, remove or defer it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from kbsync.chunking import Chunk
from kbsync.convert import Article

REMOVAL_GUARD_RATIO = 0.5


@dataclass(frozen=True)
class LocalArticle:
    article: Article
    markdown: str
    content_hash: str
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True)
class RemoteFile:
    file_id: str
    article_id: int
    content_hash: str
    chunk_index: int
    chunk_count: int
    status: str
    created_at: int
    attributes: dict = field(default_factory=dict, compare=False)


@dataclass
class Delta:
    added: list[LocalArticle] = field(default_factory=list)
    updated: list[tuple[LocalArticle, list[RemoteFile]]] = field(default_factory=list)
    skipped: list[LocalArticle] = field(default_factory=list)
    removed: list[tuple[int, list[RemoteFile]]] = field(default_factory=list)
    deferred: list[LocalArticle] = field(default_factory=list)
    removal_guard_tripped: bool = False


def group_remote(vs_files) -> tuple[dict[int, list[RemoteFile]], int]:
    """Group vector-store files by article_id attribute. Files without our
    attributes are counted as unmanaged and never touched."""
    groups: dict[int, list[RemoteFile]] = defaultdict(list)
    unmanaged = 0
    for f in vs_files:
        attrs = f.attributes or {}
        if "article_id" not in attrs or "content_hash" not in attrs:
            unmanaged += 1
            continue
        groups[int(attrs["article_id"])].append(RemoteFile(
            file_id=f.id,
            article_id=int(attrs["article_id"]),
            content_hash=str(attrs["content_hash"]),
            chunk_index=int(attrs.get("chunk_index", -1)),
            chunk_count=int(attrs.get("chunk_count", -1)),
            status=f.status,
            created_at=int(f.created_at or 0),
            attributes=dict(attrs),
        ))
    for files in groups.values():
        files.sort(key=lambda r: (r.chunk_index, r.created_at, r.file_id))
    return dict(groups), unmanaged


def in_sync(art: LocalArticle, files: list[RemoteFile]) -> bool:
    n = len(art.chunks)
    return (
        len(files) == n
        and sorted(f.chunk_index for f in files) == list(range(n))
        and all(f.status == "completed" and f.content_hash == art.content_hash and f.chunk_count == n for f in files)
    )


def plan_delta(local: list[LocalArticle], remote: dict[int, list[RemoteFile]], max_new: int) -> Delta:
    """max_new: cap on new articles per run (0 = unlimited); oldest article id first."""
    delta = Delta()
    for art in sorted(local, key=lambda a: a.article.id):
        files = remote.get(art.article.id)
        if files is None:
            if max_new and len(delta.added) >= max_new:
                delta.deferred.append(art)
            else:
                delta.added.append(art)
        elif in_sync(art, files):
            delta.skipped.append(art)
        else:
            delta.updated.append((art, files))

    local_ids = {a.article.id for a in local}
    gone = [(aid, files) for aid, files in sorted(remote.items()) if aid not in local_ids]
    if gone and len(local) < REMOVAL_GUARD_RATIO * len(remote):
        delta.removal_guard_tripped = True
    else:
        delta.removed = gone
    return delta


def pick_stale(local: list[LocalArticle], remote: dict[int, list[RemoteFile]], n: int) -> list[int]:
    """Demo helper: the first n (by id) articles that are fully in sync."""
    by_id = {a.article.id: a for a in local}
    picked = [aid for aid in sorted(remote) if aid in by_id and in_sync(by_id[aid], remote[aid])]
    return picked[:n]
