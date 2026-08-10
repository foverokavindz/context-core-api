"""Tests for the Confluence storage-format flattener.

Nothing here touches the network, a Confluence page or a chunk. The subject is
one pure function: markup in, readable text out.

Every assertion is an exact string comparison rather than a substring check.
The point of this module is the *shape* of the text - where the blank lines
fall, whether a bullet is indented under its parent, whether a code block kept
its newlines - and a substring check would pass on all of that being wrong.

The recurring question underneath is: would a human reading this text learn what
the page said? Not: does it look like the original page.
"""

import pytest

from app.ingestion.confluence_storage import MAX_STORAGE_DEPTH, storage_to_text

# ------------------------------------------------------------------ nothing


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        123,
        [],
        {},
        {"value": "<p>Hello</p>"},  # the wrapper, not the markup inside it
        True,
    ],
)
def test_anything_that_is_not_markup_becomes_empty_text(value: object) -> None:
    assert storage_to_text(value) == ""


def test_markup_with_no_text_becomes_empty_text() -> None:
    assert storage_to_text("<p></p><p>  </p><hr/>") == ""


# --------------------------------------------------------------- paragraphs


def test_a_paragraph_is_its_text() -> None:
    assert storage_to_text("<p>Hello world</p>") == "Hello world"


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    assert storage_to_text("<p>First</p><p>Second</p>") == "First\n\nSecond"


def test_the_whitespace_between_tags_is_not_content() -> None:
    """Storage format is XHTML, so its indentation is formatting, not text."""
    markup = "<p>First</p>\n    \n    <p>Second</p>\n"
    assert storage_to_text(markup) == "First\n\nSecond"


def test_a_run_of_inline_text_stays_one_sentence() -> None:
    markup = "<p>Hello <strong>brave</strong> new world</p>"
    assert storage_to_text(markup) == "Hello brave new world"


def test_a_hard_break_starts_a_new_line_not_a_new_paragraph() -> None:
    markup = "<p>Line one<br/>Line two</p>"
    assert storage_to_text(markup) == "Line one\nLine two"


# ----------------------------------------------------------------- headings


def test_a_heading_reads_as_a_line_above_its_section() -> None:
    markup = "<h2>Architecture</h2><p>System details.</p>"
    assert storage_to_text(markup) == "Architecture\n\nSystem details."


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_every_heading_level_renders_the_same_way(level: int) -> None:
    """The level is dropped on purpose - an embedding cannot use it."""
    markup = f"<h{level}>Deployment</h{level}>"
    assert storage_to_text(markup) == "Deployment"


# -------------------------------------------------------------------- lists


def test_a_bullet_list_is_one_item_per_line() -> None:
    markup = "<ul><li>API</li><li>Database</li></ul>"
    assert storage_to_text(markup) == "- API\n- Database"


def test_an_ordered_list_is_numbered() -> None:
    markup = "<ol><li>Login</li><li>Select project</li></ol>"
    assert storage_to_text(markup) == "1. Login\n2. Select project"


def test_an_ordered_list_honours_its_start_attribute() -> None:
    markup = '<ol start="3"><li>Third</li><li>Fourth</li></ol>'
    assert storage_to_text(markup) == "3. Third\n4. Fourth"


def test_an_unusable_start_attribute_falls_back_to_one() -> None:
    markup = '<ol start="later"><li>First</li></ol>'
    assert storage_to_text(markup) == "1. First"


def test_a_nested_list_is_indented_under_its_parent_item() -> None:
    markup = "<ul><li>Outer<ul><li>Inner</li></ul></li></ul>"
    assert storage_to_text(markup) == "- Outer\n  - Inner"


def test_a_nested_ordered_list_indents_by_its_own_marker_width() -> None:
    markup = "<ol><li>Outer<ol><li>Inner</li></ol></li></ol>"
    assert storage_to_text(markup) == "1. Outer\n   1. Inner"


def test_an_empty_item_does_not_consume_a_number() -> None:
    markup = "<ol><li>First</li><li></li><li>Second</li></ol>"
    assert storage_to_text(markup) == "1. First\n2. Second"


def test_a_list_item_holding_paragraphs_stays_one_item() -> None:
    markup = "<ul><li><p>First half</p><p>Second half</p></li></ul>"
    assert storage_to_text(markup) == "- First half\n  Second half"


def test_a_list_and_a_paragraph_are_separated_by_a_blank_line() -> None:
    markup = "<p>The services:</p><ul><li>API</li></ul><p>And that is all.</p>"
    expected = "The services:\n\n- API\n\nAnd that is all."
    assert storage_to_text(markup) == expected


def test_only_direct_items_are_counted_by_a_list() -> None:
    """The nested list renders once, through its parent item - not twice."""
    markup = "<ul><li>A<ul><li>B</li></ul></li><li>C</li></ul>"
    assert storage_to_text(markup) == "- A\n  - B\n- C"


# ------------------------------------------------------------------- tables


def test_a_table_keeps_its_cells_on_their_own_row() -> None:
    markup = (
        "<table><tbody>"
        "<tr><th>Service</th><th>Port</th></tr>"
        "<tr><td>API</td><td>8000</td></tr>"
        "</tbody></table>"
    )
    assert storage_to_text(markup) == "Service | Port\nAPI | 8000"


def test_a_table_without_a_body_element_still_renders() -> None:
    markup = "<table><tr><td>API</td><td>8000</td></tr></table>"
    assert storage_to_text(markup) == "API | 8000"


def test_a_cell_holding_markup_is_flattened_onto_its_row() -> None:
    markup = (
        "<table><tr>"
        "<td><p>The API</p><p>service</p></td><td><strong>8000</strong></td>"
        "</tr></table>"
    )
    assert storage_to_text(markup) == "The API service | 8000"


def test_an_empty_row_is_dropped() -> None:
    markup = "<table><tr><td></td><td></td></tr><tr><td>API</td></tr></table>"
    assert storage_to_text(markup) == "API"


def test_a_nested_table_does_not_repeat_its_rows_in_the_outer_one() -> None:
    markup = (
        "<table><tr><td>"
        "<table><tr><td>Inner</td></tr></table>"
        "</td><td>Outer</td></tr></table>"
    )
    assert storage_to_text(markup) == "Inner | Outer"


# -------------------------------------------------------------------- links


def test_a_link_keeps_its_text_and_drops_its_url() -> None:
    markup = '<p>See the <a href="https://wiki/auth">Authentication guide</a>.</p>'
    assert storage_to_text(markup) == "See the Authentication guide."


def test_a_link_url_never_reaches_the_text() -> None:
    markup = '<a href="https://wiki/auth?token=secret">Guide</a>'
    assert storage_to_text(markup) == "Guide"


# --------------------------------------------------------------- formatting


@pytest.mark.parametrize("tag", ["strong", "b", "em", "i", "u", "code", "span"])
def test_formatting_tags_keep_their_text_and_drop_their_markers(tag: str) -> None:
    assert storage_to_text(f"<p>An <{tag}>important</{tag}> point</p>") == (
        "An important point"
    )


def test_a_blockquote_keeps_its_text_and_loses_its_quoting() -> None:
    markup = "<blockquote><p>Ship it on Friday.</p></blockquote>"
    assert storage_to_text(markup) == "Ship it on Friday."


# ------------------------------------------------------------- code blocks


def test_a_code_block_keeps_its_newlines_and_indentation() -> None:
    markup = "<pre>def total(items):\n    return sum(items)</pre>"
    assert storage_to_text(markup) == "def total(items):\n    return sum(items)"


def test_the_code_macro_body_survives_its_cdata_wrapper() -> None:
    """Confluence's code macro keeps its source in CDATA, not in a text node."""
    markup = (
        '<ac:structured-macro ac:name="code">'
        "<ac:plain-text-body><![CDATA[SELECT *\n  FROM users;]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    assert storage_to_text(markup) == "SELECT *\n  FROM users;"


def test_code_sits_apart_from_the_prose_around_it() -> None:
    markup = "<p>Run this:</p><pre>npm test</pre><p>Then deploy.</p>"
    assert storage_to_text(markup) == "Run this:\n\nnpm test\n\nThen deploy."


# ------------------------------------------------------------------- macros


def test_a_macro_body_keeps_the_paragraphs_inside_it() -> None:
    markup = (
        '<ac:structured-macro ac:name="info">'
        "<ac:rich-text-body><p>Alpha</p><p>Beta</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    assert storage_to_text(markup) == "Alpha\n\nBeta"


def test_macro_configuration_is_not_treated_as_knowledge() -> None:
    """ac:parameter is how a macro is set up, not something the page says."""
    markup = (
        '<ac:structured-macro ac:name="chart">'
        '<ac:parameter ac:name="type">pie</ac:parameter>'
        '<ac:parameter ac:name="width">400</ac:parameter>'
        "<ac:rich-text-body><p>Quarterly split</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    assert storage_to_text(markup) == "Quarterly split"


def test_a_macro_with_nothing_readable_in_it_contributes_nothing() -> None:
    markup = (
        "<p>Before</p>"
        '<ac:structured-macro ac:name="toc">'
        '<ac:parameter ac:name="maxLevel">3</ac:parameter>'
        "</ac:structured-macro>"
        "<p>After</p>"
    )
    assert storage_to_text(markup) == "Before\n\nAfter"


def test_a_macro_wrapping_a_single_phrase_joins_the_sentence_around_it() -> None:
    """An inline macro should not split the paragraph it sits in."""
    markup = "<p>Status is <ac:emoticon>done</ac:emoticon> today</p>"
    assert storage_to_text(markup) == "Status is done today"


def test_a_link_to_another_page_keeps_its_body_text() -> None:
    markup = (
        "<p>See <ac:link>"
        '<ri:page ri:content-title="Deployment"/>'
        "<ac:plain-text-link-body><![CDATA[the deployment page]]>"
        "</ac:plain-text-link-body>"
        "</ac:link> for more.</p>"
    )
    assert "the deployment page" in storage_to_text(markup)


def test_an_element_nobody_has_heard_of_still_yields_its_text() -> None:
    markup = "<xyz:future-thing><p>survives</p></xyz:future-thing>"
    assert storage_to_text(markup) == "survives"


def test_an_unknown_element_never_raises() -> None:
    markup = '<ac:made-up ac:attr="1"><ac:also-made-up/></ac:made-up>'
    assert storage_to_text(markup) == ""


# -------------------------------------------------------------------- noise


def test_a_script_is_not_content() -> None:
    markup = "<p>Before</p><script>var secret = 1;</script><p>After</p>"
    assert storage_to_text(markup) == "Before\n\nAfter"


def test_a_stylesheet_is_not_content() -> None:
    markup = "<p>Before</p><style>p { color: red; }</style><p>After</p>"
    assert storage_to_text(markup) == "Before\n\nAfter"


def test_a_comment_is_not_content() -> None:
    markup = "<p>Before<!-- reviewer note -->After</p>"
    assert storage_to_text(markup) == "BeforeAfter"


def test_a_horizontal_rule_separates_rather_than_joins() -> None:
    markup = "<p>Before</p><hr/><p>After</p>"
    assert storage_to_text(markup) == "Before\n\nAfter"


def test_an_image_leaves_no_hole_between_the_paragraphs_around_it() -> None:
    markup = '<p>Before</p><ac:image><ri:attachment ri:filename="a.png"/></ac:image><p>After</p>'
    assert storage_to_text(markup) == "Before\n\nAfter"


def test_no_tag_name_survives_into_the_text() -> None:
    markup = (
        "<h1>Title</h1><p>Body</p><ul><li>Item</li></ul>"
        '<ac:structured-macro ac:name="note">'
        "<ac:rich-text-body><p>Careful</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    text = storage_to_text(markup)

    for fragment in ("<", ">", "ac:", "ri:", "structured-macro"):
        assert fragment not in text


# ------------------------------------------------------------ normalisation


def test_runs_of_blank_lines_collapse_to_one() -> None:
    markup = "<p>First</p><p></p><p></p><p></p><p>Second</p>"
    assert storage_to_text(markup) == "First\n\nSecond"


def test_windows_line_endings_become_unix_ones() -> None:
    assert storage_to_text("<pre>one\r\ntwo\rthree</pre>") == "one\ntwo\nthree"


def test_the_result_is_stripped_at_both_ends() -> None:
    assert storage_to_text("\n\n  <p>Body</p>  \n\n") == "Body"


def test_trailing_whitespace_is_removed_from_every_line() -> None:
    assert storage_to_text("<pre>one   \ntwo\t</pre>") == "one\ntwo"


# --------------------------------------------------------------- resilience


def test_unclosed_tags_do_not_lose_their_text() -> None:
    assert storage_to_text("<p>unclosed <strong>bold") == "unclosed bold"


def test_mismatched_tags_do_not_raise() -> None:
    assert storage_to_text("<p>one</div></p><em>two") == "one\n\ntwo"


def test_a_stray_closing_tag_is_ignored() -> None:
    assert storage_to_text("</p><p>Body</p>") == "Body"


def test_text_outside_any_element_is_still_text() -> None:
    assert storage_to_text("Bare words") == "Bare words"


def test_pathological_nesting_is_truncated_rather_than_fatal() -> None:
    """A RecursionError here would cost the whole space, not one page."""
    depth = MAX_STORAGE_DEPTH + 20
    markup = "<div>" * depth + "buried" + "</div>" * depth

    assert storage_to_text(markup) == ""


def test_nesting_within_the_limit_still_reaches_the_text() -> None:
    depth = MAX_STORAGE_DEPTH - 5
    markup = "<div>" * depth + "reachable" + "</div>" * depth

    assert storage_to_text(markup) == "reachable"


# ------------------------------------------------------------------ unicode


def test_unicode_survives_unchanged() -> None:
    markup = "<p>Café — naïve 日本語 🎉</p>"
    assert storage_to_text(markup) == "Café — naïve 日本語 🎉"


def test_html_entities_are_decoded() -> None:
    markup = "<p>Tom &amp; Jerry &lt;always&gt; &quot;win&quot;</p>"
    assert storage_to_text(markup) == 'Tom & Jerry <always> "win"'


def test_a_non_breaking_space_does_not_become_a_stray_line() -> None:
    markup = "<p>Before</p><p>&nbsp;</p><p>After</p>"
    assert storage_to_text(markup) == "Before\n\nAfter"


# ------------------------------------------------------------- a whole page


def test_a_realistic_page_reads_as_prose() -> None:
    """The end-to-end shape, as a single exact comparison."""
    markup = (
        "<h2>Authentication</h2>"
        "<p>TrackIt uses JWT authentication.</p>"
        "<ul><li>Users log in with email.</li><li>A JWT token is generated.</li></ul>"
        "<h2>Services</h2>"
        "<table><tbody>"
        "<tr><th>Service</th><th>Port</th></tr>"
        "<tr><td>API</td><td>8000</td></tr>"
        "</tbody></table>"
    )

    assert storage_to_text(markup) == (
        "Authentication\n"
        "\n"
        "TrackIt uses JWT authentication.\n"
        "\n"
        "- Users log in with email.\n"
        "- A JWT token is generated.\n"
        "\n"
        "Services\n"
        "\n"
        "Service | Port\n"
        "API | 8000"
    )
