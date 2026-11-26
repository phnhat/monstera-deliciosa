from kbsync.convert import Article, article_slug, body_to_markdown, github_slug, render_article


def md(html: str, title: str | None = None) -> str:
    return body_to_markdown(html, title=title)


# --- slugs -------------------------------------------------------------------

def test_article_slug_strips_numeric_id_and_lowercases():
    url = "https://support.optisigns.com/hc/en-us/articles/360051014713-How-to-Use-YouTube-with-OptiSigns"
    assert article_slug(url) == "how-to-use-youtube-with-optisigns"


def test_github_slug_matches_github_heading_ids():
    assert github_slug("What’s Needed?") == "whats-needed"
    assert github_slug("Step 1: Pair Your Screen") == "step-1-pair-your-screen"
    assert github_slug("  OptiBot & AI Designer ") == "optibot--ai-designer"


# --- article rendering -------------------------------------------------------

def test_render_article_puts_title_and_article_url_on_top():
    art = Article(
        id=1, title="How to Use YouTube", url="https://support.optisigns.com/hc/en-us/articles/1-How-to-Use-YouTube",
        updated_at="2026-11-25T00:00:00Z", body_html="<p>Hello</p>",
    )
    assert render_article(art) == (
        "# How to Use YouTube\n\n"
        "Article URL: https://support.optisigns.com/hc/en-us/articles/1-How-to-Use-YouTube\n\n"
        "Hello\n"
    )


# --- headings & anchors --------------------------------------------------------

def test_headings_are_atx_and_inline_spans_unwrapped():
    out = md('<h2 id="h_01X"><span style="color: #434343;">What’s Needed?</span></h2><p>Text</p>')
    assert out.startswith("## What’s Needed?\n")
    assert "span" not in out and "style" not in out


def test_named_anchor_before_heading_is_rewritten_to_heading_slug():
    html = (
        '<ul><li data-list-item-id="e1"><a href="#Needed">What you need</a></li></ul>'
        '<p><a name="Needed"></a></p><h2 id="h_01J7">What’s Needed?</h2><p>Body</p>'
    )
    out = md(html)
    assert "[What you need](#whats-needed)" in out
    assert "<a" not in out and "name=" not in out


def test_heading_id_anchor_is_rewritten():
    out = md('<p><a href="#h_01ABC">Jump</a></p><h2 id="h_01ABC">Setup Steps</h2>')
    assert "[Jump](#setup-steps)" in out


def test_duplicate_headings_get_github_suffixes():
    html = (
        '<p><a href="#A">a</a> <a href="#B">b</a></p>'
        '<p><a name="A"></a></p><h2>Steps</h2><p>x</p>'
        '<p><a name="B"></a></p><h2>Steps</h2><p>y</p>'
    )
    out = md(html)
    assert "[a](#steps)" in out and "[b](#steps-1)" in out


def test_title_counts_towards_duplicate_slugs():
    out = md('<p><a href="#S">s</a></p><p><a name="S"></a></p><h2>Billing</h2>', title="Billing")
    assert "[s](#billing-1)" in out


def test_double_escaped_anchor_still_resolves():
    html = '<p><a href="#Playlist&amp;amp;Schedule">x</a></p><p><a name="Playlist&amp;Schedule"></a></p><h2>Playlist and Schedule</h2>'
    assert "[x](#playlist-and-schedule)" in md(html)


def test_unresolvable_anchor_is_left_unchanged():
    assert "[Missing](#nowhere)" in md('<p><a href="#nowhere">Missing</a></p>')


# --- links, images, embeds ---------------------------------------------------

def test_absolute_relative_and_mailto_links_are_preserved_verbatim():
    html = (
        '<p><a href="https://support.optisigns.com/hc/en-us/articles/360041587014#View" target="_blank" rel="noopener">Billing</a>'
        ' <a href="/hc/en-us/articles/123">Rel</a> <a href="mailto:support@optisigns.com">mail</a></p>'
    )
    out = md(html)
    assert "[Billing](https://support.optisigns.com/hc/en-us/articles/360041587014#View)" in out
    assert "[Rel](/hc/en-us/articles/123)" in out
    assert "[mail](mailto:support@optisigns.com)" in out


def test_images_are_kept_with_alt_text():
    out = md('<p><img src="https://support.optisigns.com/hc/article_attachments/5" alt="Invoice page" width="495"></p>')
    assert out.strip() == "![Invoice page](https://support.optisigns.com/hc/article_attachments/5)"


def test_youtube_iframe_becomes_watch_link():
    out = md('<p><iframe src="//www.youtube-nocookie.com/embed/GyWZd10ure0" width="560" allowfullscreen=""></iframe></p>')
    assert out.strip() == "[Video (YouTube)](https://www.youtube.com/watch?v=GyWZd10ure0)"


def test_other_iframe_becomes_labelled_embed_link():
    out = md('<iframe src="https://www.canva.com/design/DAF/view?embed" title="Menu board"></iframe>')
    assert out.strip() == "[Embedded content: Menu board](https://www.canva.com/design/DAF/view?embed)"


def test_video_tag_becomes_link_using_title():
    html = '<p><video title="How to Turn Off AI" controls="controls" src="https://support.optisigns.com/hc/article_attachments/9"></video></p>'
    assert md(html).strip() == "[Video: How to Turn Off AI](https://support.optisigns.com/hc/article_attachments/9)"


# --- code ----------------------------------------------------------------------

def test_pre_code_becomes_fenced_block_without_auto_language():
    html = '<pre class="wysiwyg-code-block"><code class="language-auto">https://optisigns-mcp.optisigns.com/mcp\n</code></pre>'
    assert "```\nhttps://optisigns-mcp.optisigns.com/mcp\n```" in md(html)


def test_pre_code_keeps_real_language():
    html = '<pre><code class="language-json">{"a_b": 1}</code></pre>'
    assert '```json\n{"a_b": 1}\n```' in md(html)


def test_line_breaks_inside_code_block_become_plain_newlines():
    html = "<pre><code>query{<br>  devices<br>}</code></pre>"
    assert "```\nquery{\n  devices\n}\n```" in md(html)


def test_inline_code_is_backticked():
    assert "`Settings > Preferences`" in md("<p>Open <code>Settings &gt; Preferences</code></p>")


# --- tables --------------------------------------------------------------------

def test_single_column_callout_table_becomes_blockquote():
    html = (
        '<figure class="wysiwyg-table"><table><tbody>'
        '<tr><td><p class="wysiwyg-text-align-center"><strong>TIP</strong></p></td></tr>'
        '<tr><td>Find invoices under <strong>Billing History</strong>.</td></tr>'
        '</tbody></table></figure>'
    )
    out = md(html)
    assert "> **TIP**" in out
    assert "> Find invoices under **Billing History**." in out
    assert "|" not in out


def test_multi_column_table_becomes_markdown_table_with_inferred_header():
    html = "<table><tbody><tr><td>Plan</td><td>Price</td></tr><tr><td>Standard</td><td>$10</td></tr></tbody></table>"
    out = md(html)
    assert "| Plan | Price |" in out
    assert "| --- | --- |" in out
    assert "| Standard | $10 |" in out


# --- noise ---------------------------------------------------------------------

def test_noise_is_removed_and_whitespace_normalized():
    html = (
        "<script>alert(1)</script><style>.x{}</style><!-- comment -->"
        '<p data-x="1">First&nbsp;line​</p><p></p><p>&nbsp;</p><hr><p><br></p><p>Second</p>'
    )
    out = md(html)
    assert out == "First line\n\nSecond\n"


def test_underscores_are_not_escaped_in_text():
    assert "file_name" in md("<p>Use file_name here</p>")


def test_line_breaks_inside_paragraph_are_kept_without_trailing_spaces():
    out = md("<p>Line one<br>Line two</p>")
    assert out == "Line one\\\nLine two\n"
    assert not any(line.endswith(" ") for line in out.splitlines())
