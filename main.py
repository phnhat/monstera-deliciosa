"""Scrape support articles -> clean Markdown -> heading-aware chunks -> OpenAI vector store.

Runs once and exits: 0 on success, 1 on any failure.

    python main.py               # full sync (needs OPENAI_API_KEY or API_KEY)
    python main.py --dry-run     # scrape + convert + chunk only, no API key needed
    python main.py --mark-stale 3   # demo: mark 3 synced articles stale first, so this run shows updated=3
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kbsync.chunking import chunk_article, content_hash
from kbsync.config import Config
from kbsync.convert import Article, render_article
from kbsync.report import RunReport
from kbsync.sync import LocalArticle, group_remote, pick_stale, plan_delta
from kbsync.zendesk import fetch_articles

log = logging.getLogger("kbsync")


def setup_logging(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(out_dir / "sync.log", mode="w")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    for noisy in ("httpx", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_local(articles: list[Article], articles_dir: Path, chunks_dir: Path) -> list[LocalArticle]:
    """Render Markdown + chunks; mirror them to disk (stale *.md files are removed)."""
    articles_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for stale in [*chunks_dir.glob("*.md")]:
        stale.unlink()
    local, keep = [], set()
    for art in articles:
        markdown = render_article(art)
        chunks = tuple(chunk_article(markdown, title=art.title, url=art.url, slug=art.slug))
        (articles_dir / f"{art.slug}.md").write_text(markdown, encoding="utf-8")
        keep.add(f"{art.slug}.md")
        for c in chunks:
            (chunks_dir / c.filename).write_text(c.text, encoding="utf-8")
        local.append(LocalArticle(article=art, markdown=markdown, content_hash=content_hash(markdown), chunks=chunks))
    for path in articles_dir.glob("*.md"):
        if path.name not in keep:
            path.unlink()
    return local


def run(cfg: Config, dry_run: bool, mark_stale: int) -> int:
    started = time.monotonic()
    report = RunReport(started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       dry_run=dry_run, mark_stale=mark_stale)
    try:
        articles = fetch_articles(cfg.help_center_url)
        local = build_local(articles, cfg.articles_dir, cfg.out_dir / "chunks")
        report.articles_scraped = len(local)
        report.chunks_built = sum(len(a.chunks) for a in local)
        log.info("Wrote %d Markdown files to %s/ and %d chunks to %s/",
                 len(local), cfg.articles_dir, report.chunks_built, cfg.out_dir / "chunks")

        if dry_run:
            log.info("Dry run: skipping vector store sync")
            return _finish(report, cfg, started, ok=True)
        if not cfg.api_key:
            log.error("OPENAI_API_KEY (or API_KEY) is not set; use --dry-run to only scrape")
            return _finish(report, cfg, started, ok=False)

        from openai import OpenAI  # imported lazily so --dry-run works without the SDK configured
        from kbsync.vector_store import Store, apply_delta, resolve_store

        client = OpenAI(api_key=cfg.api_key, max_retries=5)
        store = Store(client, resolve_store(client, cfg.vector_store_id, cfg.vector_store_name))
        report.vector_store_id = store.id

        groups, unmanaged = group_remote(store.list_files())
        log.info("Vector store %s holds %d articles (%d chunk files)%s", store.id, len(groups),
                 sum(map(len, groups.values())), f", {unmanaged} unmanaged files left untouched" if unmanaged else "")

        if mark_stale:
            picked = pick_stale(local, groups, mark_stale)
            store.mark_stale([f for aid in picked for f in groups[aid]])
            log.warning("DEMO: marked %d articles stale (ids %s); they will be re-uploaded as 'updated' below",
                        len(picked), picked)
            groups, _ = group_remote(store.list_files())

        delta = plan_delta(local, groups, cfg.max_new_per_run)
        report.removal_guard_tripped = delta.removal_guard_tripped
        if delta.removal_guard_tripped:
            log.warning("Removal guard: fetched %d articles but store holds %d; skipping deletions", len(local), len(groups))
        log.info("Plan: add=%d update=%d skip=%d remove=%d defer=%d (MAX_NEW_PER_RUN=%s)",
                 len(delta.added), len(delta.updated), len(delta.skipped), len(delta.removed),
                 len(delta.deferred), cfg.max_new_per_run or "unlimited")

        result = apply_delta(store, delta)
        for aid, reason in result.failed.items():
            log.error("Article %s failed: %s", aid, reason)

        slug = {a.article.id: a.article.slug for a in local}
        report.counts.update(
            added=len(result.added), updated=len(result.updated), skipped=len(delta.skipped),
            removed=len(result.removed), deferred=len(delta.deferred), failed=len(result.failed),
            md_files_embedded=len(result.added) + len(result.updated),
            chunks_embedded=result.files_uploaded,
            store_articles=len(groups) + len(result.added) - len(result.removed),
            store_chunks=store.file_count(),
        )
        report.details = {
            "added": [slug[i] for i in result.added],
            "updated": [slug[i] for i in result.updated],
            "removed": result.removed,
            "deferred": [a.article.slug for a in delta.deferred],
            "failed": {str(k): v for k, v in result.failed.items()},
            "files_deleted": result.files_deleted,
        }
        return _finish(report, cfg, started, ok=not result.failed)
    except Exception:
        log.exception("Sync failed")
        return _finish(report, cfg, started, ok=False)


def _finish(report: RunReport, cfg: Config, started: float, ok: bool) -> int:
    report.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report.duration_s = round(time.monotonic() - started, 1)
    report.write(cfg.out_dir)
    log.info(report.summary_line())
    log.info("Run %s in %.1fs", "succeeded" if ok else "FAILED", report.duration_s)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="scrape, convert and chunk only")
    parser.add_argument("--mark-stale", type=int, default=0, metavar="N",
                        help="demo: mark N synced articles stale before syncing")
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    setup_logging(cfg.out_dir)
    return run(cfg, dry_run=args.dry_run, mark_stale=args.mark_stale)


if __name__ == "__main__":
    sys.exit(main())
