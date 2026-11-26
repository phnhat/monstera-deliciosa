"""Run report: one SUMMARY log line, out/run_report.json, and a GitHub step summary."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

COUNT_KEYS = ("added", "updated", "skipped", "removed", "deferred", "failed",
              "md_files_embedded", "chunks_embedded", "store_articles", "store_chunks")


@dataclass
class RunReport:
    started_at: str
    vector_store_id: str | None = None
    dry_run: bool = False
    mark_stale: int = 0
    articles_scraped: int = 0
    chunks_built: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNT_KEYS, 0))
    details: dict[str, object] = field(default_factory=dict)
    removal_guard_tripped: bool = False
    finished_at: str | None = None
    duration_s: float | None = None

    def summary_line(self) -> str:
        counts = " ".join(f"{k}={self.counts[k]}" for k in COUNT_KEYS)
        return (f"SUMMARY scraped={self.articles_scraped} chunks_built={self.chunks_built} {counts} "
                f"vector_store={self.vector_store_id or '-'}{' DRY-RUN' if self.dry_run else ''}")

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=False)

    def step_summary(self) -> str:
        rows = "\n".join(f"| {k} | {self.counts[k]} |" for k in COUNT_KEYS)
        notes = []
        if self.mark_stale:
            notes.append(f"> **Demo run:** {self.mark_stale} article(s) were deliberately marked stale before syncing, "
                         "so `updated` reflects that demo, not an upstream edit.")
        if self.removal_guard_tripped:
            notes.append("> **Removal guard tripped:** fetch returned far fewer articles than the store holds; deletions skipped.")
        return (f"### KB sync — {self.started_at}\n\n"
                f"Scraped **{self.articles_scraped}** articles → **{self.chunks_built}** chunks. "
                f"Vector store `{self.vector_store_id or '-'}`.\n\n"
                + ("\n\n".join(notes) + "\n\n" if notes else "")
                + f"| count | value |\n| --- | --- |\n{rows}\n")

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run_report.json").write_text(self.to_json())
        if path := os.getenv("GITHUB_STEP_SUMMARY"):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(self.step_summary())
