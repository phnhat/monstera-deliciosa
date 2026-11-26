import re

from kbsync.chunking import TARGET_TOKENS, MAX_TOKENS, chunk_article, content_hash, count_tokens

TITLE = "How to Use YouTube"
URL = "https://support.optisigns.com/hc/en-us/articles/1-How-to-Use-YouTube"


def article(body: str) -> str:
    return f"# {TITLE}\n\nArticle URL: {URL}\n\n{body}"


def para(n_words: int, word: str = "signage") -> str:
    return " ".join([word] * n_words)


def chunks_of(body: str):
    return chunk_article(article(body), title=TITLE, url=URL, slug="how-to-use-youtube")


def test_every_chunk_starts_with_title_section_and_article_url():
    body = f"Intro text.\n\n## Setup\n\n{para(600)}\n\n## Deploy\n\n{para(600)}"
    chunks = chunks_of(body)
    assert len(chunks) >= 2
    for c in chunks:
        first, second = c.text.splitlines()[:2]
        assert first.startswith(f"# {TITLE} › ")
        assert second == f"Article URL: {URL}"


def test_small_article_is_one_chunk_with_original_header_stripped():
    chunks = chunks_of("Just a short intro.\n\n## Steps\n\nDo the thing.")
    assert len(chunks) == 1
    text = chunks[0].text
    assert text.count("Article URL:") == 1
    assert text.startswith(f"# {TITLE} › Overview · Steps\n")
    assert "## Steps\n\nDo the thing." in text


def test_large_sections_become_separate_chunks_labelled_by_heading():
    body = f"## Setup\n\n{para(500)}\n\n## Deploy\n\n{para(500)}\n\n### Options\n\n{para(500)}"
    labels = [c.text.splitlines()[0].split(" › ", 1)[1] for c in chunks_of(body)]
    assert labels == ["Setup", "Deploy", "Deploy › Options"]


def test_oversized_section_is_split_at_paragraphs_within_cap():
    paragraphs = [para(150, w) for w in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")]
    chunks = chunks_of("## Long\n\n" + "\n\n".join(paragraphs))
    assert len(chunks) >= 2
    assert all(c.tokens <= MAX_TOKENS for c in chunks)
    joined = "\n\n".join(c.text for c in chunks)
    for p in paragraphs:
        assert p in joined  # no paragraph cut in half


def test_huge_code_block_is_split_with_balanced_fences():
    code = "\n".join(f"line_{i} = call(value_{i}, other_{i})" for i in range(400))
    chunks = chunks_of(f"## Script\n\n```json\n{code}\n```")
    assert len(chunks) >= 2
    for c in chunks:
        assert c.tokens <= MAX_TOKENS
        assert c.text.count("```") % 2 == 0
        assert "```json\n" in c.text


def test_huge_table_is_split_and_repeats_header_row():
    rows = "\n".join(f"| Plan {i} | {para(12)} |" for i in range(200))
    chunks = chunks_of(f"## Pricing\n\n| Plan | Notes |\n| --- | --- |\n{rows}")
    assert len(chunks) >= 2
    for c in chunks:
        assert c.tokens <= MAX_TOKENS
        assert "| Plan | Notes |\n| --- | --- |" in c.text


def test_hash_lines_inside_code_fence_are_not_headings():
    body = "## Real\n\n```\n## not a heading\n```\n\nAfter."
    chunks = chunks_of(body)
    assert len(chunks) == 1
    assert " › Real\n" in chunks[0].text.splitlines()[0] + "\n"


def test_tiny_sections_are_merged_up_to_target():
    body = "\n\n".join(f"## Step {i}\n\n{para(20)}" for i in range(10))
    chunks = chunks_of(body)
    assert len(chunks) == 1
    assert chunks[0].tokens <= TARGET_TOKENS


def test_merged_labels_group_subsections_under_their_parent():
    body = f"## Create\n\n{para(30)}\n\n### Display\n\n{para(30)}\n\n### Playback\n\n{para(30)}\n\n## Deploy\n\n{para(30)}"
    (chunk,) = chunks_of(body)
    assert chunk.text.splitlines()[0] == f"# {TITLE} › Create › Display, Playback · Deploy"


def test_filenames_are_slug_and_two_digit_index():
    chunks = chunks_of(f"## A\n\n{para(600)}\n\n## B\n\n{para(600)}")
    assert [c.filename for c in chunks] == [f"how-to-use-youtube__{i + 1:02d}.md" for i in range(len(chunks))]
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunking_is_deterministic():
    body = f"Intro\n\n## A\n\n{para(900)}\n\n## B\n\nshort"
    assert chunks_of(body) == chunks_of(body)


def test_content_hash_is_stable_and_content_sensitive():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert re.fullmatch(r"[0-9a-f]{64}", content_hash("abc"))


def test_count_tokens_counts():
    assert 0 < count_tokens("hello world") < 5
