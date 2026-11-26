from kbsync.chunking import Chunk
from kbsync.convert import Article
from kbsync.sync import LocalArticle, RemoteFile, group_remote, pick_stale, plan_delta


def local(id_, h="h1", n_chunks=2):
    art = Article(id=id_, title=f"T{id_}", url=f"https://x/articles/{id_}-t", updated_at="2026-11-25T00:00:00Z", body_html="")
    chunks = tuple(Chunk(index=i, filename=f"t__{i + 1:02d}.md", text="x", tokens=1) for i in range(n_chunks))
    return LocalArticle(article=art, markdown="md", content_hash=h, chunks=chunks)


def remote(id_, h="h1", n_chunks=2, status="completed", created_at=1, indexes=None):
    idx = range(n_chunks) if indexes is None else indexes
    return [RemoteFile(file_id=f"file-{id_}-{i}-{created_at}", article_id=id_, content_hash=h, chunk_index=i,
                       chunk_count=n_chunks, status=status, created_at=created_at) for i in idx]


def ids(items):
    """Article ids from LocalArticle, (LocalArticle, files) or (article_id, files) items."""
    out = []
    for item in items:
        head = item[0] if isinstance(item, tuple) else item
        out.append(head.article.id if isinstance(head, LocalArticle) else head)
    return out


def test_new_changed_and_unchanged_articles():
    delta = plan_delta(
        [local(1), local(2, h="new"), local(3)],
        {1: remote(1), 2: remote(2, h="old")},
        max_new=0,
    )
    assert ids(delta.skipped) == [1]
    assert ids(delta.updated) == [2]
    assert ids(delta.added) == [3]
    assert delta.removed == [] and delta.deferred == []


def test_updated_carries_old_files_for_deletion():
    old = remote(2, h="old")
    delta = plan_delta([local(2, h="new")], {2: old}, max_new=0)
    (art, files), = delta.updated
    assert files == old


def test_incomplete_failed_or_duplicate_groups_are_resynced():
    delta = plan_delta(
        [local(1), local(2), local(3)],
        {
            1: remote(1, indexes=[0]),  # a chunk is missing
            2: remote(2, status="failed"),  # processing failed
            3: remote(3) + remote(3, created_at=2),  # duplicates from a crashed run
        },
        max_new=0,
    )
    assert ids(delta.updated) == [1, 2, 3]


def test_chunk_count_change_with_same_hash_is_resynced():
    delta = plan_delta([local(1, n_chunks=3)], {1: remote(1, n_chunks=2)}, max_new=0)
    assert ids(delta.updated) == [1]


def test_articles_gone_upstream_are_removed():
    delta = plan_delta([local(1), local(2)], {1: remote(1), 2: remote(2), 9: remote(9)}, max_new=0)
    assert [aid for aid, _ in delta.removed] == [9]
    assert not delta.removal_guard_tripped


def test_removal_guard_blocks_mass_deletion():
    remote_groups = {i: remote(i) for i in range(1, 11)}
    delta = plan_delta([local(1), local(2)], remote_groups, max_new=0)
    assert delta.removed == []
    assert delta.removal_guard_tripped


def test_max_new_caps_additions_oldest_id_first_and_defers_rest():
    delta = plan_delta([local(30), local(10), local(20), local(5)], {5: remote(5, h="old")}, max_new=2)
    assert ids(delta.added) == [10, 20]
    assert ids(delta.deferred) == [30]
    assert ids(delta.updated) == [5]  # updates are never capped


def test_group_remote_parses_attributes_and_ignores_unmanaged_files():
    class VSFile:
        def __init__(self, id_, attributes, status="completed", created_at=1):
            self.id, self.attributes, self.status, self.created_at = id_, attributes, status, created_at

    files = [
        VSFile("file-a", {"article_id": "7", "content_hash": "h", "chunk_index": 1, "chunk_count": 2}),
        VSFile("file-b", {"article_id": "7", "content_hash": "h", "chunk_index": 0, "chunk_count": 2}),
        VSFile("file-c", None),
        VSFile("file-d", {"something": "else"}),
    ]
    groups, unmanaged = group_remote(files)
    assert list(groups) == [7]
    assert [f.chunk_index for f in groups[7]] == [0, 1]
    assert unmanaged == 2


def test_pick_stale_selects_first_n_in_sync_articles_by_id():
    groups = {3: remote(3), 1: remote(1), 2: remote(2, h="old"), 4: remote(4)}
    picked = pick_stale([local(1), local(2), local(3), local(4)], groups, n=2)
    assert picked == [1, 3]
