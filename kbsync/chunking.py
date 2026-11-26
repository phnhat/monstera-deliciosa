"""Heading-aware chunking of article Markdown.

Each article is split at `##` / `###` headings. Small neighbouring sections are
merged up to TARGET_TOKENS, oversized ones are split at paragraph boundaries
(tables and code blocks by lines, repeating the table header / re-opening the
fence). Every chunk gets its own header:

    # <Article title> › <Section label>
    Article URL: <url>

so any retrieved chunk carries its context and a citable URL. Each chunk is
uploaded as its own file with OpenAI static chunking (1024 max, 0 overlap),
which keeps it intact: one file == one chunk.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

CHUNKER_VERSION = "1"  # bump to force re-upload of every article after changing rules
TARGET_TOKENS = 700
MAX_TOKENS = 900
OPENAI_CHUNKING = {"type": "static", "static": {"max_chunk_size_tokens": 1024, "chunk_overlap_tokens": 0}}
MAX_LABELS = 3


@dataclass(frozen=True)
class Chunk:
    index: int
    filename: str
    text: str
    tokens: int


@dataclass(frozen=True)
class _Piece:
    label: str
    text: str


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def content_hash(markdown: str) -> str:
    return hashlib.sha256(f"{CHUNKER_VERSION}\n{markdown}".encode()).hexdigest()


def chunk_article(markdown: str, *, title: str, url: str, slug: str,
                  target: int = TARGET_TOKENS) -> list[Chunk]:
    body = _strip_article_header(markdown)
    pieces: list[_Piece] = []
    for section in _split_sections(body):
        pieces.extend(_fit_section(section, title, url, target))

    groups: list[list[_Piece]] = []
    for piece in pieces:
        if groups and count_tokens(_render(groups[-1] + [piece], title, url)) <= target:
            groups[-1].append(piece)
        else:
            groups.append([piece])

    if not groups:
        groups = [[_Piece("Overview", "")]]
    chunks = []
    for i, group in enumerate(groups):
        text = _render(group, title, url)
        chunks.append(Chunk(index=i, filename=f"{slug}__{i + 1:02d}.md", text=text, tokens=count_tokens(text)))
    return chunks


# --- sections -----------------------------------------------------------------

def _strip_article_header(markdown: str) -> str:
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    if lines and lines[0].startswith("Article URL:"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _heading_text(raw: str) -> str:
    raw = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", raw)
    return " ".join(re.sub(r"[*_`]", "", raw).split())


def _is_fence(line: str) -> bool:
    return line.lstrip().startswith("```")


def _split_sections(body: str) -> list[_Piece]:
    """Split at ## and ### (### only once inside a ## section); text before the
    first ## is 'Overview'. Heading lines stay in the section text."""
    sections: list[_Piece] = []
    label, h2, buf, in_fence = "Overview", None, [], False

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append(_Piece(label, text))

    for line in body.splitlines():
        if _is_fence(line):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{2,3}) (.+?)\s*$", line)
        if m and (len(m.group(1)) == 2 or h2 is not None):
            flush()
            text = _heading_text(m.group(2))
            if len(m.group(1)) == 2:
                h2, label = text, text
            else:
                label = f"{h2} › {text}"
            buf = [line]
        else:
            buf.append(line)
    flush()
    return sections


# --- fitting oversized sections ---------------------------------------------------

def _header(title: str, url: str, label: str) -> str:
    return f"# {title} › {label}\nArticle URL: {url}\n\n"


def _merge_labels(labels: list[str]) -> str:
    """['Create', 'Create › Display', 'Deploy'] -> 'Create › Display · Deploy'."""
    tops: dict[str, list[str]] = {}
    for label in labels:
        top, _, sub = label.partition(" › ")
        subs = tops.setdefault(top, [])
        if sub and sub not in subs:
            subs.append(sub)
    parts = [f"{top} › {', '.join(subs)}" if subs else top for top, subs in tops.items()]
    if len(parts) > MAX_LABELS:
        parts = parts[:MAX_LABELS] + ["…"]
    return " · ".join(parts)


def _render(group: list[_Piece], title: str, url: str) -> str:
    body = "\n\n".join(p.text for p in group if p.text)
    return _header(title, url, _merge_labels([p.label for p in group])) + body + "\n"


def _fit_section(section: _Piece, title: str, url: str, target: int) -> list[_Piece]:
    budget = target - count_tokens(_header(title, url, section.label))
    if count_tokens(section.text) <= budget:
        return [section]
    blocks: list[str] = []
    for block in _blocks(section.text):
        blocks.extend(_split_block(block, budget) if count_tokens(block) > budget else [block])
    texts = _pack(blocks, budget, sep="\n\n")
    return [_Piece(section.label, t) for t in texts]


def _blocks(text: str) -> list[str]:
    """Paragraph-level blocks; a fenced code block is one block even across blank lines."""
    blocks, cur, in_fence = [], [], False
    for line in text.splitlines():
        if _is_fence(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _is_table(lines: list[str]) -> bool:
    return len(lines) >= 2 and lines[0].lstrip().startswith("|") and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[1]) is not None


def _split_block(block: str, budget: int) -> list[str]:
    lines = block.splitlines()
    if _is_fence(lines[0]):
        opener = lines[0]
        inner = lines[1:-1] if len(lines) > 1 and _is_fence(lines[-1]) else lines[1:]
        overhead = count_tokens(f"{opener}\n\n```")
        return [f"{opener}\n{body}\n```" for body in _pack(inner, budget - overhead, sep="\n")]
    if _is_table(lines):
        head = "\n".join(lines[:2])
        return [f"{head}\n{body}" for body in _pack(lines[2:], budget - count_tokens(head) - 1, sep="\n")]
    return _pack(lines, budget, sep="\n")


def _pack(parts: list[str], budget: int, sep: str) -> list[str]:
    """Greedily join parts with `sep` into strings of at most `budget` tokens.
    A single part over budget is split by words."""
    out: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    sep_tokens = count_tokens(sep)
    for part in parts:
        n = count_tokens(part)
        if n > budget:
            if cur:
                out.append(sep.join(cur))
                cur, cur_tokens = [], 0
            out.extend(_split_words(part, budget))
            continue
        if cur and cur_tokens + sep_tokens + n > budget:
            out.append(sep.join(cur))
            cur, cur_tokens = [], 0
        cur_tokens += (sep_tokens if cur else 0) + n
        cur.append(part)
    if cur:
        out.append(sep.join(cur))
    return out


def _split_words(text: str, budget: int) -> list[str]:
    out, cur = [], []
    for word in text.split(" "):
        if cur and count_tokens(" ".join(cur + [word])) > budget:
            out.append(" ".join(cur))
            cur = []
        cur.append(word)
    if cur:
        out.append(" ".join(cur))
    return out
