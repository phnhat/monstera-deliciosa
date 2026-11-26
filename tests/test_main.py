import json
from types import SimpleNamespace as NS

import pytest

import main as entry
from kbsync import pipeline
from kbsync.config import Config
from kbsync.convert import Article
from kbsync.report import RunReport
from kbsync.vector_store import chunk_attributes


def cfg(tmp_path, api_key=None, max_new=100):
    return Config(api_key=api_key, vector_store_id=None, vector_store_name="t", help_center_url="https://x",
                  articles_dir=tmp_path / "articles", out_dir=tmp_path / "out", max_new_per_run=max_new)


def fake_articles(_url):
    return [Article(id=i, title=f"Title {i}", url=f"https://x/hc/en-us/articles/{i}-Title-{i}",
                    updated_at="2026-11-25T00:00:00Z", body_html=f"<h2>Part</h2><p>Body {i}</p>") for i in (1, 2, 3)]


class FakeStore:
    """In-memory stand-in for kbsync.vector_store.Store."""

    id = "vs_test"

    def __init__(self):
        self.files, self._n = {}, 0

    def list_files(self):
        return list(self.files.values())

    def upload(self, arts):
        new_ids = {}
        for art in arts:
            for c in art.chunks:
                self._n += 1
                fid = f"file-{self._n}"
                self.files[fid] = NS(id=fid, attributes=chunk_attributes(art, c), status="completed", created_at=self._n)
                new_ids.setdefault(art.article.id, []).append(fid)
        return {a.article.id for a in arts}, {}, new_ids

    def delete(self, ids):
        for fid in ids:
            self.files.pop(fid, None)
        return len(ids)

    def mark_stale(self, files):
        for f in files:
            self.files[f.file_id].attributes = {**f.attributes, "content_hash": "stale-demo"}
        return len(files)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_articles", fake_articles)


def report(c):
    return json.loads((c.out_dir / "run_report.json").read_text())


def test_dry_run_writes_markdown_chunks_and_report(tmp_path):
    c = cfg(tmp_path)
    c.articles_dir.mkdir()
    (c.articles_dir / "gone-upstream.md").write_text("old")

    assert entry.run(c, dry_run=True) == 0

    assert sorted(p.name for p in c.articles_dir.glob("*.md")) == ["title-1.md", "title-2.md", "title-3.md"]
    assert (c.articles_dir / "title-1.md").read_text().startswith("# Title 1\n\nArticle URL: https://x/hc/en-us/articles/1-Title-1")
    assert sorted(p.name for p in pipeline.chunks_dir(c).glob("*.md")) == ["title-1__01.md", "title-2__01.md", "title-3__01.md"]
    r = report(c)
    assert r["articles_scraped"] == 3 and r["chunks_built"] == 3 and r["dry_run"] is True


def test_missing_api_key_fails_loudly(tmp_path):
    assert entry.run(cfg(tmp_path)) == 1


def test_phase_without_its_input_fails_cleanly(tmp_path):
    assert entry.run(cfg(tmp_path), phase="convert") == 1


def test_mark_stale_is_rejected_for_phases_other_than_plan():
    with pytest.raises(SystemExit):
        entry.main(["convert", "--mark-stale", "2"])


def test_phases_run_as_separate_steps_sync_only_the_delta(tmp_path):
    c, store = cfg(tmp_path, api_key="sk-test", max_new=2), FakeStore()
    for phase in ("fetch", "convert", "chunk"):
        assert entry.run(c, phase=phase) == 0

    # first run: backfill capped at 2 new articles
    pipeline.plan(c, store=store)
    assert pipeline.upload(c, store=store)
    r = report(c)
    assert (r["counts"]["added"], r["counts"]["deferred"], r["counts"]["store_chunks"]) == (2, 1, len(store.files))

    # second run: the rest is added, nothing re-uploaded
    pipeline.plan(c, store=store)
    pipeline.upload(c, store=store)
    r = report(c)
    assert (r["counts"]["added"], r["counts"]["skipped"], r["counts"]["chunks_embedded"]) == (1, 2, 1)

    # demo run: one article marked stale -> updated, old chunk replaced (not duplicated)
    files_before = len(store.files)
    pipeline.plan(c, mark_stale=1, store=store)
    pipeline.upload(c, store=store)
    r = report(c)
    assert (r["counts"]["updated"], r["counts"]["skipped"]) == (1, 2)
    assert r["mark_stale"] == 1 and r["details"]["marked_stale_demo"] == [1]
    assert len(store.files) == files_before == r["counts"]["store_chunks"]


def test_step_summary_flags_demo_runs():
    rep = RunReport(generated_at="2026-11-26T06:17:00Z", mark_stale=3, vector_store_id="vs_1")
    rep.counts.update(updated=3, skipped=393)
    text = rep.step_summary()
    assert "Demo run" in text and "| updated | 3 |" in text
    assert "updated=3 skipped=393" in rep.summary_line()
