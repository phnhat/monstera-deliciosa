"""Scrape support articles -> clean Markdown -> heading-aware chunks -> OpenAI vector store.

Runs once and exits: 0 on success, 1 on any failure.

    python main.py                  # all phases: fetch -> convert -> chunk -> plan -> upload
    python main.py --dry-run        # fetch -> convert -> chunk only; no API key needed
    python main.py --mark-stale 3   # demo: mark 3 synced articles stale first, so this run shows updated=3
    python main.py <phase>          # one phase: fetch | convert | chunk | plan | upload
                                    # (the daily CI workflow runs each phase as its own job)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from kbsync import pipeline
from kbsync.config import Config

log = logging.getLogger("kbsync")


def setup_logging(out_dir: Path, filename: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(out_dir / filename, mode="w")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run(cfg: Config, phase: str = "all", dry_run: bool = False, mark_stale: int = 0) -> int:
    if phase == "all":
        phases = pipeline.PHASES[:3] if dry_run else pipeline.PHASES
    else:
        phases = (phase,)
    started, ok, current = time.monotonic(), True, None
    try:
        for current in phases:
            t0 = time.monotonic()
            log.info("== %s ==", current)
            if current == "fetch":
                pipeline.fetch(cfg)
            elif current == "convert":
                pipeline.convert(cfg)
            elif current == "chunk":
                pipeline.chunk(cfg)
            elif current == "plan":
                pipeline.plan(cfg, mark_stale=mark_stale)
            elif current == "upload":
                ok = pipeline.upload(cfg)
            log.info("== %s done in %.1fs ==", current, time.monotonic() - t0)
        if dry_run and phase == "all":
            log.info("Dry run: skipping vector store sync")
            pipeline.dry_run_report(cfg)
    except pipeline.PhaseError as e:
        log.error("Phase '%s' cannot run: %s", current, e)
        ok = False
    except Exception:
        log.exception("Phase '%s' failed", current)
        ok = False
    log.info("Run %s in %.1fs", "succeeded" if ok else "FAILED", time.monotonic() - started)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", nargs="?", default="all", choices=("all", *pipeline.PHASES),
                        help="run a single phase (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="scrape, convert and chunk only")
    parser.add_argument("--mark-stale", type=int, default=0, metavar="N",
                        help="demo: mark N synced articles stale before planning")
    args = parser.parse_args(argv)
    if args.mark_stale and args.phase not in ("all", "plan"):
        parser.error("--mark-stale only applies to the 'plan' phase (or a full run)")
    cfg = Config.from_env()
    setup_logging(cfg.out_dir, "sync.log" if args.phase == "all" else f"{args.phase}.log")
    return run(cfg, args.phase, dry_run=args.dry_run, mark_stale=args.mark_stale)


if __name__ == "__main__":
    sys.exit(main())
