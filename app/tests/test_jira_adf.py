"""Tests for the ADF-to-text flattener.

Every assertion here is an exact string comparison rather than a substring
check. The whole point of this module is the shape of its output - where the
newlines land, what a bullet looks like, what an unknown node leaves behind -
and a substring check would pass on all of that being wrong.
"""

import pytest

from app.ingestion.jira_adf import MAX_ADF_DEPTH, adf_to_text


def doc(*content: dict) -> dict:
    """An ADF document wrapping the given block nodes."""
    return {"version": 1, "type": "doc", "content": list(content)}


def paragraph(*content: dict) -> dict:
    return {"type": "paragraph", "content": list(content)}


def text(value: str, marks: list[dict] | None = None) -> dict:
    node: dict = {"type": "text", "text": value}
    if marks is not None:
        node["marks"] = marks
    return node


def list_item(*content: dict) -> dict:
    return {"type": "listItem", "content": list(content)}


# ---------------------------------------------------------------- paragraphs


def test_a_single_paragraph_becomes_its_text() -> None:
    assert adf_to_text(doc(paragraph(text("Hello world")))) == "Hello world"


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    document = doc(paragraph(text("First")), paragraph(text("Second")))
    assert adf_to_text(document) == "First\n\nSecond"


def test_adjacent_text_runs_are_joined_without_a_separator() -> None:
    """A sentence split across nodes by a formatting mark is still a sentence."""
    document = doc(paragraph(text("Hello "), text("world")))
    assert adf_to_text(document) == "Hello world"


def test_a_heading_becomes_plain_text() -> None:
    """No leading '#'. This is plain text, not markdown."""
    heading = {"type": "heading", "attrs": {"level": 2}, "content": [text("Overview")]}
    assert adf_to_text(doc(heading, paragraph(text("Some detail")))) == (
        "Overview\n\nSome detail"
    )


def test_a_hard_break_becomes_a_single_newline() -> None:
    document = doc(
        paragraph(text("line one"), {"type": "hardBreak"}, text("line two"))
    )
    assert adf_to_text(document) == "line one\nline two"


# --------------------------------------------------------------------- lists


def test_a_bullet_list_is_rendered_with_dashes() -> None:
    bullets = {
        "type": "bulletList",
        "content": [
            list_item(paragraph(text("one"))),
            list_item(paragraph(text("two"))),
        ],
    }
    assert adf_to_text(doc(bullets)) == "- one\n- two"


def test_an_ordered_list_is_numbered_from_one() -> None:
    ordered = {
        "type": "orderedList",
        "content": [
            list_item(paragraph(text("first"))),
            list_item(paragraph(text("second"))),
        ],
    }
    assert adf_to_text(doc(ordered)) == "1. first\n2. second"


def test_an_ordered_list_honours_its_start_attribute() -> None:
    ordered = {
        "type": "orderedList",
        "attrs": {"order": 3},
        "content": [
            list_item(paragraph(text("first"))),
            list_item(paragraph(text("second"))),
        ],
    }
    assert adf_to_text(doc(ordered)) == "3. first\n4. second"


def test_a_nested_list_is_indented_under_its_parent_item() -> None:
    """Indentation comes from the parent's continuation prefix, not from depth."""
    inner = {
        "type": "bulletList",
        "content": [list_item(paragraph(text("Inner")))],
    }
    outer = {
        "type": "bulletList",
        "content": [list_item(paragraph(text("Outer")), inner)],
    }
    assert adf_to_text(doc(outer)) == "- Outer\n  - Inner"


def test_a_list_keeps_a_multi_paragraph_item_together() -> None:
    bullets = {
        "type": "bulletList",
        "content": [
            list_item(paragraph(text("one")), paragraph(text("still one"))),
            list_item(paragraph(text("two"))),
        ],
    }
    assert adf_to_text(doc(bullets)) == "- one\n  still one\n- two"


def test_an_empty_list_item_is_dropped() -> None:
    bullets = {
        "type": "bulletList",
        "content": [list_item(), list_item(paragraph(text("only one")))],
    }
    assert adf_to_text(doc(bullets)) == "- only one"


# ------------------------------------------------------------ other blocks


def test_a_blockquote_keeps_its_text() -> None:
    quote = {"type": "blockquote", "content": [paragraph(text("quoted text"))]}
    assert adf_to_text(doc(quote)) == "quoted text"


def test_a_code_block_keeps_its_source() -> None:
    code = {
        "type": "codeBlock",
        "attrs": {"language": "typescript"},
        "content": [text("const a = 1;")],
    }
    assert adf_to_text(doc(code)) == "const a = 1;"


def test_text_marks_are_ignored_but_the_text_survives() -> None:
    document = doc(paragraph(text("bold", marks=[{"type": "strong"}])))
    assert adf_to_text(document) == "bold"


def test_a_link_keeps_its_text_and_drops_the_url() -> None:
    link = text(
        "the docs",
        marks=[{"type": "link", "attrs": {"href": "https://example.com"}}],
    )
    assert adf_to_text(doc(paragraph(link))) == "the docs"


# ------------------------------------------------------------ unknown nodes


def test_an_unknown_block_node_is_traversed_not_dropped() -> None:
    """A panel is a wrapper; the paragraph inside it is the content that matters."""
    panel = {
        "type": "panel",
        "attrs": {"panelType": "warning"},
        "content": [paragraph(text("warning"))],
    }
    assert adf_to_text(doc(panel)) == "warning"


def test_a_deeply_wrapped_unknown_node_still_yields_its_text() -> None:
    cell = {"type": "tableCell", "content": [paragraph(text("in a cell"))]}
    row = {"type": "tableRow", "content": [cell]}
    table = {"type": "table", "content": [row]}
    assert adf_to_text(doc(table)) == "in a cell"


def test_an_unknown_leaf_node_does_not_crash() -> None:
    assert adf_to_text(doc({"type": "mediaSingle"})) == ""


def test_an_unknown_leaf_between_paragraphs_leaves_no_hole() -> None:
    document = doc(paragraph(text("A")), {"type": "rule"}, paragraph(text("B")))
    assert adf_to_text(document) == "A\n\nB"


def test_an_inline_mention_uses_its_attribute_text() -> None:
    mention = {"type": "mention", "attrs": {"id": "557058", "text": "@Alice"}}
    assert adf_to_text(doc(paragraph(text("cc "), mention))) == "cc @Alice"


def test_an_unknown_node_prefers_its_own_text_over_its_attributes() -> None:
    node = {"type": "someFuture", "text": "direct", "attrs": {"text": "indirect"}}
    assert adf_to_text(doc(paragraph(node))) == "direct"


# --------------------------------------------------------------- edge cases


def test_a_plain_string_passes_through_unchanged() -> None:
    """Older APIs and webhook payloads still send a bare string."""
    assert adf_to_text("  already plain text  ") == "  already plain text  "


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"type": "doc"},
        {"type": "doc", "content": []},
        {"type": "doc", "content": None},
        {"type": "doc", "content": "oops"},
        [],
        42,
    ],
)
def test_unusable_values_become_an_empty_string(value: object) -> None:
    assert adf_to_text(value) == ""


def test_a_text_node_with_a_non_string_text_is_tolerated() -> None:
    assert adf_to_text(doc(paragraph({"type": "text", "text": None}))) == ""


def test_runs_of_blank_lines_are_collapsed() -> None:
    document = doc(
        paragraph(text("A")),
        paragraph(),
        paragraph(),
        paragraph(text("B")),
    )
    assert adf_to_text(document) == "A\n\nB"


def test_leading_and_trailing_whitespace_is_stripped() -> None:
    assert adf_to_text(doc(paragraph(text("  padded  ")))) == "padded"


def test_carriage_returns_are_normalised() -> None:
    assert adf_to_text(doc(paragraph(text("one\r\ntwo")))) == "one\ntwo"


def test_a_deeply_nested_document_does_not_recurse_forever() -> None:
    """A pathological payload must truncate, not raise RecursionError."""
    node: dict = {"type": "someWrapper", "content": [paragraph(text("buried"))]}
    for _ in range(MAX_ADF_DEPTH * 4):
        node = {"type": "someWrapper", "content": [node]}

    assert adf_to_text(doc(node)) == ""
