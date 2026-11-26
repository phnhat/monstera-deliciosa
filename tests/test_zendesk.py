from kbsync.zendesk import fetch_articles


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResponse(self.pages[url])


def raw(id_, draft=False, body="<p>x</p>"):
    return {
        "id": id_, "title": f"T{id_}", "html_url": f"https://h/hc/en-us/articles/{id_}-T{id_}",
        "updated_at": "2026-11-25T00:00:00Z", "draft": draft, "body": body,
    }


def test_fetch_follows_next_page_skips_drafts_and_sorts_by_id():
    first = "https://support.example.com/api/v2/help_center/en-us/articles.json?per_page=100"
    second = first + "&page=2"
    session = FakeSession({
        first: {"articles": [raw(30), raw(10, draft=True)], "next_page": second},
        second: {"articles": [raw(20), raw(40, body=None)], "next_page": None},
    })
    arts = fetch_articles("https://support.example.com", session=session)
    assert [a.id for a in arts] == [20, 30, 40]
    assert session.urls == [first, second]
    assert arts[0].url == "https://h/hc/en-us/articles/20-T20"
    assert arts[2].body_html == ""
