"""Pipeline phases. Each phase reads the previous phase's files from disk and
writes its own, so phases can run in one process (`main.py`) or as separate
CI jobs that hand files to each other as artifacts.

    fetch    Zendesk API            -> out/raw/articles.json
    convert  raw articles           -> <OUTPUT_DIR>/<slug>.md
    chunk    Markdown               -> out/chunks/<slug>__NN.md + out/chunks/index.json
    plan     chunks + vector store  -> out/plan.json            (needs API key)
    upload   plan + chunks          -> out/run_report.json      (needs API key)
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from kbsync.chunking import Chunk, chunk_article, content_hash
from kbsync.config import Config
from kbsync.convert import Article, render_article
from kbsync.report import RunReport
from kbsync.sync import Delta, LocalArticle, RemoteFile, group_remote, pick_stale, plan_delta
from kbsync.zendesk import fetch_articles

log = logging.getLogger("kbsync")
PHASES = ("fetch", "convert", "chunk", "plan", "upload")


class PhaseError(RuntimeError):
    """A phase cannot run (e.g. missing input or API key)."""


# --- file layout ------------------------------------------------------------------

def raw_path(cfg: Config) -> Path:
    return cfg.out_dir / "raw" / "articles.json"


def chunks_dir(cfg: Config) -> Path:
    return cfg.out_dir / "chunks"


def index_path(cfg: Config) -> Path:
    return chunks_dir(cfg) / "index.json"


def plan_path(cfg: Config) -> Path:
    return cfg.out_dir / "plan.json"


def _read_json(path: Path, produced_by: str):
    if not path.exists():
        raise PhaseError(f"{path} not found; run the '{produced_by}' phase first")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def step_summary(markdown: str) -> None:
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown.rstrip() + "\n\n")


def load_articles(cfg: Config) -> list[Article]:
    return [Article(**a) for a in _read_json(raw_path(cfg), "fetch")]


def load_local(cfg: Config) -> list[LocalArticle]:
    local = []
    for entry in _read_json(index_path(cfg), "chunk"):
        chunks = tuple(
            Chunk(index=c["index"], filename=c["filename"], tokens=c["tokens"],
                  text=(chunks_dir(cfg) / c["filename"]).read_text(encoding="utf-8"))
            for c in entry["chunks"])
        art = Article(id=entry["id"], title=entry["title"], url=entry["url"],
                      updated_at=entry["updated_at"], body_html="")
        local.append(LocalArticle(article=art, markdown="", content_hash=entry["content_hash"], chunks=chunks))
    return local


# --- phases -----------------------------------------------------------------------

def fetch(cfg: Config) -> None:
    articles = fetch_articles(cfg.help_center_url)
    _write_json(raw_path(cfg), [asdict(a) for a in articles])
    log.info("Saved %d articles to %s", len(articles), raw_path(cfg))
    step_summary(f"**Fetch:** {len(articles)} published articles from `{cfg.help_center_url}`.")


def convert(cfg: Config) -> None:
    articles = load_articles(cfg)
    cfg.articles_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for art in articles:
        (cfg.articles_dir / f"{art.slug}.md").write_text(render_article(art), encoding="utf-8")
        keep.add(f"{art.slug}.md")
    stale = [p for p in cfg.articles_dir.glob("*.md") if p.name not in keep]
    for path in stale:
        path.unlink()
    log.info("Wrote %d Markdown files to %s/ (%d stale files removed)", len(keep), cfg.articles_dir, len(stale))
    step_summary(f"**Convert:** {len(keep)} Markdown files.")


def chunk(cfg: Config) -> None:
    articles = load_articles(cfg)
    out = chunks_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    index, sizes = [], []
    for art in articles:
        md_path = cfg.articles_dir / f"{art.slug}.md"
        if not md_path.exists():
            raise PhaseError(f"{md_path} not found; run the 'convert' phase first")
        markdown = md_path.read_text(encoding="utf-8")
        chunks = chunk_article(markdown, title=art.title, url=art.url, slug=art.slug)
        for c in chunks:
            (out / c.filename).write_text(c.text, encoding="utf-8")
            sizes.append(c.tokens)
        index.append({"id": art.id, "slug": art.slug, "title": art.title, "url": art.url,
                      "updated_at": art.updated_at, "content_hash": content_hash(markdown),
                      "chunks": [{"index": c.index, "filename": c.filename, "tokens": c.tokens} for c in chunks]})
    _write_json(index_path(cfg), index)
    median = int(statistics.median(sizes)) if sizes else 0
    log.info("Built %d chunks for %d articles (median %d tokens, max %d)", len(sizes), len(index), median, max(sizes, default=0))
    step_summary(f"**Chunk:** {len(sizes)} chunks for {len(index)} articles (median {median} tokens, max {max(sizes, default=0)}).")


def _open_store(cfg: Config, store_id: str | None = None):
    if not cfg.api_key:
        raise PhaseError("OPENAI_API_KEY (or API_KEY) is not set; use --dry-run to only scrape")
    from openai import OpenAI  # imported lazily so offline phases don't need it configured
    from kbsync.vector_store import Store, resolve_store

    client = OpenAI(api_key=cfg.api_key, max_retries=5)
    return Store(client, store_id or resolve_store(client, cfg.vector_store_id, cfg.vector_store_name))


def plan(cfg: Config, mark_stale: int = 0, store=None) -> Delta:
    local = load_local(cfg)
    store = store or _open_store(cfg)
    groups, unmanaged = group_remote(store.list_files())
    log.info("Vector store %s holds %d articles (%d chunk files)%s", store.id, len(groups),
             sum(map(len, groups.values())), f", {unmanaged} unmanaged files left untouched" if unmanaged else "")

    stale_ids: list[int] = []
    if mark_stale:
        stale_ids = pick_stale(local, groups, mark_stale)
        store.mark_stale([f for aid in stale_ids for f in groups[aid]])
        log.warning("DEMO: marked %d articles stale (ids %s); they will be re-uploaded as 'updated'",
                    len(stale_ids), stale_ids)
        groups, _ = group_remote(store.list_files())

    delta = plan_delta(local, groups, cfg.max_new_per_run)
    if delta.removal_guard_tripped:
        log.warning("Removal guard: fetched %d articles but store holds %d; skipping deletions", len(local), len(groups))
    log.info("Plan: add=%d update=%d skip=%d remove=%d defer=%d (MAX_NEW_PER_RUN=%s)",
             len(delta.added), len(delta.updated), len(delta.skipped), len(delta.removed),
             len(delta.deferred), cfg.max_new_per_run or "unlimited")

    _write_json(plan_path(cfg), {
        "vector_store_id": store.id,
        "max_new_per_run": cfg.max_new_per_run,
        "mark_stale": stale_ids,
        "removal_guard_tripped": delta.removal_guard_tripped,
        "store_before": {"articles": len(groups), "chunks": sum(map(len, groups.values()))},
        "added": [a.article.id for a in delta.added],
        "updated": {str(a.article.id): [asdict(f) for f in files] for a, files in delta.updated},
        "skipped": [a.article.id for a in delta.skipped],
        "removed": {str(aid): [asdict(f) for f in files] for aid, files in delta.removed},
        "deferred": [a.article.id for a in delta.deferred],
    })
    demo = f" (demo: {len(stale_ids)} marked stale first)" if stale_ids else ""
    step_summary(f"**Compare:** add {len(delta.added)}, update {len(delta.updated)}{demo}, skip {len(delta.skipped)}, "
                 f"remove {len(delta.removed)}, defer {len(delta.deferred)}.")
    return delta


def _delta_from_plan(data: dict, local: list[LocalArticle]) -> Delta:
    by_id = {a.article.id: a for a in local}
    remote = lambda files: [RemoteFile(**f) for f in files]  # noqa: E731
    return Delta(
        added=[by_id[i] for i in data["added"]],
        updated=[(by_id[int(i)], remote(files)) for i, files in data["updated"].items()],
        skipped=[by_id[i] for i in data["skipped"]],
        removed=[(int(i), remote(files)) for i, files in data["removed"].items()],
        deferred=[by_id[i] for i in data["deferred"]],
        removal_guard_tripped=data["removal_guard_tripped"],
    )


def upload(cfg: Config, store=None) -> bool:
    from kbsync.vector_store import apply_delta

    data = _read_json(plan_path(cfg), "plan")
    local = load_local(cfg)
    delta = _delta_from_plan(data, local)
    store = store or _open_store(cfg, data["vector_store_id"])
    result = apply_delta(store, delta)
    for aid, reason in result.failed.items():
        log.error("Article %s failed: %s", aid, reason)

    slug = {a.article.id: a.article.slug for a in local}
    before = data["store_before"]
    report = new_report(cfg, mark_stale=len(data["mark_stale"]))
    report.vector_store_id = data["vector_store_id"]
    report.articles_scraped = len(local)
    report.chunks_built = sum(len(a.chunks) for a in local)
    report.removal_guard_tripped = data["removal_guard_tripped"]
    report.counts.update(
        added=len(result.added), updated=len(result.updated), skipped=len(delta.skipped),
        removed=len(result.removed), deferred=len(delta.deferred), failed=len(result.failed),
        md_files_embedded=len(result.added) + len(result.updated),
        chunks_embedded=result.files_uploaded,
        store_articles=before["articles"] + len(result.added) - len(result.removed),
        # computed, not read back: the API's file_counts lag behind deletions for a few seconds
        store_chunks=before["chunks"] + result.files_uploaded - result.files_retired,
    )
    report.details = {
        "added": [slug[i] for i in result.added],
        "updated": [slug[i] for i in result.updated],
        "removed": result.removed,
        "deferred": [a.article.slug for a in delta.deferred],
        "marked_stale_demo": data["mark_stale"],
        "failed": {str(k): v for k, v in result.failed.items()},
        "files_deleted": result.files_deleted,
    }
    finish_report(report, cfg)
    return not result.failed


# --- report helpers ----------------------------------------------------------------

def new_report(cfg: Config, dry_run: bool = False, mark_stale: int = 0) -> RunReport:
    return RunReport(dry_run=dry_run, mark_stale=mark_stale)


def finish_report(report: RunReport, cfg: Config) -> None:
    report.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report.write(cfg.out_dir)
    log.info(report.summary_line())


def dry_run_report(cfg: Config) -> None:
    local = load_local(cfg)
    report = new_report(cfg, dry_run=True)
    report.articles_scraped = len(local)
    report.chunks_built = sum(len(a.chunks) for a in local)
    finish_report(report, cfg)
