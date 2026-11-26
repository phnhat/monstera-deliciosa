from ask import strip_citation_markers


def test_strip_citation_markers_removes_inline_file_markers():
    text = "Click **Save**. fileciteturn0file0turn0file1\n3. Next step"
    assert strip_citation_markers(text) == "Click **Save**.\n3. Next step"


def test_strip_citation_markers_leaves_plain_text_alone():
    assert strip_citation_markers("Article URL: https://x/1") == "Article URL: https://x/1"
