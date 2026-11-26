"""Zendesk article HTML -> clean Markdown.

Two stages:
1. BeautifulSoup pre-clean: drop noise, resolve in-page anchors to heading slugs,
   turn embeds into links, turn one-column "callout" tables into blockquotes,
   strip presentational attributes.
2. markdownify with ATX headings, fenced code and inferred table headers,
   followed by whitespace normalisation.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment, Tag
from markdownify import MarkdownConverter

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
DROP_TAGS = ("script", "style", "noscript", "form", "button", "input", "select", "textarea", "nav", "header", "footer", "aside", "svg")
UNWRAP_TAGS = ("span", "font", "figure", "u", "center")
ALLOWED_ATTRS = {
    "a": ("href", "title"),
    "img": ("src", "alt", "title"),
    "pre": ("class",),
    "code": ("class",),
    "td": ("colspan", "rowspan"),
    "th": ("colspan", "rowspan"),
}
NO_LANGUAGE = {"auto", "plaintext", "text", "none"}
TAG_LIKE = re.compile(r"<(?=[A-Za-z/!?])")
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    url: str
    updated_at: str
    body_html: str

    @property
    def slug(self) -> str:
        return article_slug(self.url)


def article_slug(html_url: str) -> str:
    """`.../articles/360051014713-How-to-Use-YouTube` -> `how-to-use-youtube`."""
    tail = urlparse(html_url).path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"^\d+-?", "", tail).lower() or tail


def github_slug(text: str) -> str:
    """Heading id as GitHub renders it: lowercase, drop punctuation, spaces -> '-'."""
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


class _Slugger:
    """Assigns GitHub-style slugs, suffixing duplicates with -1, -2, ..."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def slug(self, text: str) -> str:
        base = github_slug(text)
        n = self._seen.get(base)
        self._seen[base] = 0 if n is None else n + 1
        return base if n is None else f"{base}-{n + 1}"


def render_article(article: Article) -> str:
    origin = urlparse(article.url)
    base_url = f"{origin.scheme}://{origin.netloc}" if origin.netloc else None
    body = body_to_markdown(article.body_html, title=article.title, base_url=base_url)
    return f"# {article.title}\n\nArticle URL: {article.url}\n\n{body}"


def body_to_markdown(body_html: str, title: str | None = None, base_url: str | None = None) -> str:
    """base_url (e.g. https://support.optisigns.com) resolves site-relative image
    sources so images still render outside the help center; link hrefs are kept as-is."""
    soup = BeautifulSoup(body_html or "", "html.parser")
    slugger = _Slugger()
    if title:
        slugger.slug(title)

    _drop_noise(soup)
    _embeds_to_links(soup)
    _absolutize_images(soup, base_url)
    _resolve_anchors(soup, slugger)
    _callout_tables_to_blockquotes(soup)
    for tag in soup.find_all(UNWRAP_TAGS):
        tag.unwrap()
    _strip_attributes(soup)
    _drop_empty_blocks(soup)

    text = _Converter(
        heading_style="ATX",
        bullets="-",
        escape_underscores=False,
        newline_style="BACKSLASH",
        keep_inline_images_in=["td", "th", "a"],
        table_infer_header=True,
        code_language_callback=_code_language,
    ).convert_soup(soup)
    return _normalize_whitespace(text)


class _Converter(MarkdownConverter):
    """Backslash-escape a literal `<` that starts a tag-like word in plain text
    (`<your tenant>`, `<iframe ...>`), so Markdown renderers show it instead of
    treating it as HTML and dropping it. Code spans and blocks are never escaped."""

    def escape(self, text, parent_tags):
        return TAG_LIKE.sub(r"\\<", super().escape(text, parent_tags))


# --- pre-clean steps ----------------------------------------------------------

def _drop_noise(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for hr in soup.find_all("hr"):
        hr.decompose()
    for br in soup.select("pre br"):
        br.replace_with("\n")


def _absolutize_images(soup: BeautifulSoup, base_url: str | None) -> None:
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if src.startswith("//"):
            img["src"] = "https:" + src
        elif src.startswith("/") and base_url:
            img["src"] = base_url.rstrip("/") + src


def _embed_target(src: str) -> tuple[str, str | None]:
    """Return (canonical url, provider label or None for non-video embeds)."""
    if src.startswith("//"):
        src = "https:" + src
    if m := re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", src):
        return f"https://www.youtube.com/watch?v={m.group(1)}", "YouTube"
    if m := re.search(r"player\.vimeo\.com/video/(\d+)", src):
        return f"https://vimeo.com/{m.group(1)}", "Vimeo"
    if "wistia" in src:
        return src, "Wistia"
    return src, None


def _embeds_to_links(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["iframe", "video"]):
        src = tag.get("src")
        if not src and tag.name == "video" and (source := tag.find("source")):
            src = source.get("src")
        if not src:
            tag.decompose()
            continue
        title = (tag.get("title") or "").strip()
        if tag.name == "video":
            href, label = src, f"Video: {title}" if title else "Video"
        else:
            href, provider = _embed_target(src)
            if provider:
                label = f"Video: {title}" if title else f"Video ({provider})"
            else:
                label = f"Embedded content: {title}" if title else f"Embedded content ({urlparse(href).netloc})"
        link = soup.new_tag("a", href=href)
        link.string = label
        tag.replace_with(link)


def _anchor_name(tag: Tag) -> str | None:
    if tag.name == "a" and not tag.get("href"):
        return tag.get("name") or tag.get("id")
    return None


def _anchor_key(name: str) -> str:
    """Normalise anchor names; source HTML sometimes double-escapes `&` in hrefs."""
    return html.unescape(html.unescape(name))


def _resolve_anchors(soup: BeautifulSoup, slugger: _Slugger) -> None:
    """Map `<a name=X>` targets (and heading ids) to the slug of their heading,
    rewrite `href="#X"` links accordingly, then remove the target tags."""
    mapping: dict[str, str] = {}
    pending: list[str] = []
    for tag in soup.find_all(True):
        if tag.name in HEADINGS:
            slug = slugger.slug(" ".join(tag.get_text().split()))
            names = pending + [tag.get("id")] + [_anchor_name(a) for a in tag.find_all("a")]
            mapping.update({_anchor_key(n): slug for n in names if n})
            pending = []
        elif (name := _anchor_name(tag)) and not tag.find_parent(HEADINGS):
            pending.append(name)

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("#") and (key := _anchor_key(href[1:])) in mapping:
            link["href"] = "#" + mapping[key]

    for target in [a for a in soup.find_all("a") if _anchor_name(a)]:
        if target.get_text(strip=True):
            target.unwrap()
        else:
            target.decompose()


def _callout_tables_to_blockquotes(soup: BeautifulSoup) -> None:
    for table in reversed(soup.find_all("table")):
        rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
        cells = [r.find_all(["td", "th"], recursive=False) for r in rows]
        if not rows or any(len(c) > 1 for c in cells):
            continue
        quote = soup.new_tag("blockquote")
        for row_cells in cells:
            if not row_cells:
                continue
            block = soup.new_tag("div")
            for child in list(row_cells[0].contents):
                block.append(child.extract())
            quote.append(block)
        table.replace_with(quote)


def _strip_attributes(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(True):
        allowed = ALLOWED_ATTRS.get(tag.name, ())
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in allowed}


def _drop_empty_blocks(soup: BeautifulSoup) -> None:
    for tag in reversed(soup.find_all(["p", "div", "li", "blockquote"])):
        if tag.find(["img", "a", "table", "pre", "code", "iframe", "video"]):
            continue
        if not tag.get_text().replace("\xa0", " ").translate(ZERO_WIDTH).strip():
            tag.decompose()


def _code_language(el: Tag) -> str | None:
    for node in [el, *el.find_all("code")]:
        for cls in node.get("class") or []:
            if cls.startswith("language-"):
                lang = cls.removeprefix("language-")
                return None if lang in NO_LANGUAGE else lang
    return None


# --- post-processing ------------------------------------------------------------

def _normalize_whitespace(text: str) -> str:
    text = text.translate(ZERO_WIDTH).replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n" if text else ""
