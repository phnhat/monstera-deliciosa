"""Read articles from the public Zendesk Help Center API.

Using the API (rather than scraping rendered pages) means we only ever receive the
article body: site navigation, headers, footers and promos are never in the input.
"""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from kbsync.convert import Article

log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "kb-sync/1.0"
    return session


def fetch_articles(base_url: str, locale: str = "en-us", session=None) -> list[Article]:
    """Fetch every published article, oldest id first."""
    session = session or make_session()
    url = f"{base_url.rstrip('/')}/api/v2/help_center/{locale}/articles.json?per_page=100"
    articles: list[Article] = []
    while url:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for a in data.get("articles", []):
            if a.get("draft"):
                continue
            articles.append(Article(
                id=int(a["id"]),
                title=a["title"].strip(),
                url=a["html_url"],
                updated_at=a.get("updated_at") or "",
                body_html=a.get("body") or "",
            ))
        url = data.get("next_page")
    articles.sort(key=lambda art: art.id)
    log.info("Fetched %d published articles from %s", len(articles), base_url)
    return articles
