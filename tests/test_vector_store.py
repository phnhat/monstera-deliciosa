from types import SimpleNamespace as NS

from kbsync.chunking import Chunk
from kbsync.convert import Article
from kbsync.sync import Delta, LocalArticle, RemoteFile
from kbsync.vector_store import Store, apply_delta, chunk_attributes


class FakeClient:
    """Records calls in order; batch attach fails for any file whose name contains 'bad'."""

    def __init__(self):
        self.calls = []
        self.names = {}
        self._n = 0
        self.files = NS(create=self._file_create, delete=lambda file_id: self.calls.append(("files.delete", file_id)))
        self.vector_stores = NS(
            files=NS(
                delete=lambda file_id, vector_store_id: self.calls.append(("vs.delete", file_id)),
                update=lambda file_id, vector_store_id, attributes: self.calls.append(("vs.update", file_id, attributes)),
            ),
            file_batches=NS(create_and_poll=self._batch, list_files=self._batch_files),
        )
        self.batches = {}

    def _file_create(self, file, purpose):
        name, data, mime = file
        assert purpose == "assistants" and mime == "text/markdown" and data
        self._n += 1
        fid = f"file-new-{self._n}"
        self.names[fid] = name
        self.calls.append(("files.create", name))
        return NS(id=fid)

    def _batch(self, vector_store_id, files):
        files = list(files)
        self.calls.append(("batch", [f["file_id"] for f in files]))
        for f in files:
            assert f["chunking_strategy"]["static"] == {"max_chunk_size_tokens": 1024, "chunk_overlap_tokens": 0}
        bad = [f["file_id"] for f in files if "bad" in self.names[f["file_id"]]]
        bid = f"batch-{len(self.batches)}"
        self.batches[bid] = bad
        return NS(id=bid, status="completed", file_counts=NS(failed=len(bad), cancelled=0, completed=len(files) - len(bad)))

    def _batch_files(self, batch_id, vector_store_id, filter, limit=100):
        if filter != "failed":
            return []
        return [NS(id=fid, last_error=NS(message="unsupported_file")) for fid in self.batches[batch_id]]


def local(id_, slug, n=2, h="new"):
    art = Article(id=id_, title=f"T{id_}", url=f"https://x/articles/{id_}-{slug}", updated_at="2026-11-25T00:00:00Z", body_html="")
    chunks = tuple(Chunk(index=i, filename=f"{slug}__{i + 1:02d}.md", text=f"chunk {i}", tokens=2) for i in range(n))
    return LocalArticle(article=art, markdown="md", content_hash=h, chunks=chunks)


def old_files(id_, n=2):
    return [RemoteFile(file_id=f"file-old-{id_}-{i}", article_id=id_, content_hash="old", chunk_index=i,
                       chunk_count=n, status="completed", created_at=1, attributes={"article_id": str(id_)}) for i in range(n)]


def test_chunk_attributes_carry_delta_state():
    art = local(42, "pair-a-screen")
    attrs = chunk_attributes(art, art.chunks[1])
    assert attrs == {
        "article_id": "42", "content_hash": "new", "chunk_index": 1, "chunk_count": 2,
        "slug": "pair-a-screen", "url": "https://x/articles/42-pair-a-screen", "title": "T42",
        "updated_at": "2026-11-25T00:00:00Z",
    }


def test_update_uploads_new_chunks_before_deleting_old_ones():
    client = FakeClient()
    delta = Delta(updated=[(local(2, "two"), old_files(2))])
    result = apply_delta(Store(client, "vs_1", workers=1), delta)

    kinds = [c[0] for c in client.calls]
    last_upload = max(i for i, k in enumerate(kinds) if k == "batch")
    first_delete = min(i for i, k in enumerate(kinds) if k.endswith("delete"))
    assert last_upload < first_delete
    deleted = {c[1] for c in client.calls if c[0] == "vs.delete"}
    assert deleted == {"file-old-2-0", "file-old-2-1"}
    assert result.updated == [2] and result.failed == {}
    assert result.files_uploaded == 2 and result.files_deleted == 2 and result.files_retired == 2


def test_failed_upload_keeps_old_version_and_cleans_partial_new_files():
    client = FakeClient()
    good, bad = local(1, "good"), local(2, "bad")
    delta = Delta(added=[good], updated=[(bad, old_files(2))])
    result = apply_delta(Store(client, "vs_1", workers=1), delta)

    assert result.added == [1]
    assert result.updated == []
    assert set(result.failed) == {2}
    vs_deleted = {c[1] for c in client.calls if c[0] == "vs.delete"}
    assert not any(fid.startswith("file-old-2") for fid in vs_deleted)  # old version still served
    new_bad = {fid for fid, name in client.names.items() if name.startswith("bad__")}
    assert new_bad <= vs_deleted  # partial new upload removed
    assert result.files_retired == 0  # nothing that was live got removed


def test_removed_articles_delete_all_their_files():
    client = FakeClient()
    result = apply_delta(Store(client, "vs_1", workers=1), Delta(removed=[(9, old_files(9, n=3))]))
    assert result.removed == [9]
    assert result.files_retired == 3
    assert sorted(c[1] for c in client.calls if c[0] == "files.delete") == [f"file-old-9-{i}" for i in range(3)]


def test_mark_stale_overwrites_content_hash_keeping_other_attributes():
    client = FakeClient()
    files = old_files(5)
    Store(client, "vs_1").mark_stale(files)
    updates = [c for c in client.calls if c[0] == "vs.update"]
    assert [u[1] for u in updates] == ["file-old-5-0", "file-old-5-1"]
    assert all(u[2] == {"article_id": "5", "content_hash": "stale-demo"} for u in updates)
