import json

import main as entry
from kbsync.config import Config
from kbsync.convert import Article
from kbsync.report import RunReport


def cfg(tmp_path, api_key=None):
    return Config(api_key=api_key, vector_store_id=None, vector_store_name="t", help_center_url="https://x",
                  articles_dir=tmp_path / "articles", out_dir=tmp_path / "out", max_new_per_run=100)


def fake_articles(_url):
    return [Article(id=i, title=f"Title {i}", url=f"https://x/hc/en-us/articles/{i}-Title-{i}",
                    updated_at="2026-11-25T00:00:00Z", body_html=f"<h2>Part</h2><p>Body {i}</p>") for i in (1, 2)]


def test_dry_run_writes_markdown_chunks_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "fetch_articles", fake_articles)
    (tmp_path / "articles").mkdir()
    (tmp_path / "articles" / "gone-upstream.md").write_text("old")

    assert entry.run(cfg(tmp_path), dry_run=True, mark_stale=0) == 0

    assert sorted(p.name for p in (tmp_path / "articles").glob("*.md")) == ["title-1.md", "title-2.md"]
    assert (tmp_path / "articles" / "title-1.md").read_text().startswith("# Title 1\n\nArticle URL: https://x/hc/en-us/articles/1-Title-1")
    assert sorted(p.name for p in (tmp_path / "out" / "chunks").glob("*.md")) == ["title-1__01.md", "title-2__01.md"]
    report = json.loads((tmp_path / "out" / "run_report.json").read_text())
    assert report["articles_scraped"] == 2 and report["chunks_built"] == 2 and report["dry_run"] is True


def test_missing_api_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "fetch_articles", fake_articles)
    assert entry.run(cfg(tmp_path), dry_run=False, mark_stale=0) == 1


def test_step_summary_flags_demo_runs():
    report = RunReport(started_at="2026-11-26T06:17:00Z", mark_stale=3, vector_store_id="vs_1")
    report.counts.update(updated=3, skipped=393)
    text = report.step_summary()
    assert "Demo run" in text and "| updated | 3 |" in text
    assert "updated=3 skipped=393" in report.summary_line()
