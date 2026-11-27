from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    api_key: str | None
    vector_store_id: str | None
    vector_store_name: str
    help_center_url: str
    articles_dir: Path
    out_dir: Path
    max_new_per_run: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        return cls(
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY") or None,
            vector_store_id=os.getenv("VECTOR_STORE_ID") or None,
            vector_store_name=os.getenv("VECTOR_STORE_NAME") or "optibot-kb",
            help_center_url=os.getenv("HELP_CENTER_URL") or "https://support.optisigns.com",
            articles_dir=Path(os.getenv("OUTPUT_DIR") or "articles"),
            out_dir=Path(os.getenv("OUT_DIR") or "out"),
            max_new_per_run=_int("MAX_NEW_PER_RUN", 0),
        )
