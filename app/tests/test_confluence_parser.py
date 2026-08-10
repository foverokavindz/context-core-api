"""Tests for the raw-page-JSON -> ConfluencePage boundary.

No network here: every payload is a literal shaped the way the REST API returns
one, with the field names and nesting Confluence actually uses.

The parser is where a hostile or merely incomplete response has to stop being
dangerous. Confluence omits what an account cannot see, returns null for what is
unset, and returns no body at all unless one was asked for - so most of these
tests remove exactly one field and check the page survives anyway. The single
exception is the page id, which is the one thing worth failing over.
"""

import pytest

from app.ingestion.confluence_parser import ConfluenceParser

SPACE_KEY = "TR"
SPACE_ID = "6422530"
SPACE_NAME = "TrackIt"


@pytest.fixture
def parser() -> ConfluenceParser:
    return ConfluenceParser()


def make_raw(
    page_id: object = "111",
    *,
    title: object = "Authentication",
    parent_id: object = None,
    status: object = "current",
    version: object = 7,
    body: object = "<p>TrackIt uses JWT authentication.</p>",
) -> dict:
    """One raw page, shaped as the v2 API returns it.

    Every field is overridable - including with None, which removes it - so a
    test can take away exactly the one thing it is about.
    """
    raw: dict = {
        "id": page_id,
        "title": title,
        "spaceId": SPACE_ID,
        "parentId": parent_id,
        "parentType": "page" if parent_id else None,
        "status": status,
        "authorId": "6250f960ad6b7e006aaaa013",
        "createdAt": "2026-08-06T16:31:33.535Z",
    }

    if version is not None:
        raw["version"] = {"number": version, "message": "", "minorEdit": False}
    if body is not None:
        raw["body"] = {"storage": {"representation": "storage", "value": body}}

    return raw


def parse(parser: ConfluenceParser, raw: dict):
    """Parse one page with the resolved space every test shares."""
    return parser.parse(
        raw, space_key=SPACE_KEY, space_id=SPACE_ID, space_name=SPACE_NAME
    )


# --------------------------------------------------------------- happy path


def test_the_core_fields_are_extracted(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw("111", title="Authentication", version=7))

    assert page.page_id == "111"
    assert page.title == "Authentication"
    assert page.status == "current"
    assert page.version_number == 7


def test_the_resolved_space_is_stamped_onto_the_page(
    parser: ConfluenceParser,
) -> None:
    page = parse(parser, make_raw())

    assert page.space_key == SPACE_KEY
    assert page.space_id == SPACE_ID
    assert page.space_name == SPACE_NAME


def test_the_space_comes_from_the_run_not_from_the_payload(
    parser: ConfluenceParser,
) -> None:
    """A page claiming another space cannot widen a run past the space asked for."""
    raw = make_raw()
    raw["spaceId"] = "9999999"

    page = parse(parser, raw)

    assert page.space_id == SPACE_ID


def test_the_space_name_is_optional(parser: ConfluenceParser) -> None:
    page = parser.parse(make_raw(), space_key=SPACE_KEY, space_id=SPACE_ID)

    assert page.space_name is None


def test_the_body_arrives_as_plain_text(parser: ConfluenceParser) -> None:
    raw = make_raw(body="<h2>Auth</h2><p>TrackIt uses JWT.</p>")

    assert parse(parser, raw).content == "Auth\n\nTrackIt uses JWT."


def test_no_storage_markup_survives_the_boundary(parser: ConfluenceParser) -> None:
    raw = make_raw(
        body=(
            "<h2>Auth</h2><p>JWT</p>"
            '<ac:structured-macro ac:name="note">'
            "<ac:rich-text-body><p>Careful</p></ac:rich-text-body>"
            "</ac:structured-macro>"
        )
    )

    content = parse(parser, raw).content

    assert "<" not in content
    assert "ac:" not in content
    assert "Careful" in content


# ------------------------------------------------------------- relationships


def test_a_child_page_keeps_its_parent(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw("102", parent_id="101"))

    assert page.parent_id == "101"


def test_a_root_page_has_no_parent(parser: ConfluenceParser) -> None:
    """Confluence returns a literal null for parentId on a space's home page."""
    page = parse(parser, make_raw("101", parent_id=None))

    assert page.parent_id is None


def test_a_missing_parent_field_is_not_a_parent(parser: ConfluenceParser) -> None:
    raw = make_raw()
    del raw["parentId"]

    assert parse(parser, raw).parent_id is None


def test_no_child_list_is_invented(parser: ConfluenceParser) -> None:
    """Unlike Jira, hierarchy is kept one-directional - see ConfluencePage."""
    page = parse(parser, make_raw("102", parent_id="101"))

    assert not hasattr(page, "child_pages")


# ------------------------------------------------------------------ page id


def test_a_page_with_no_id_is_rejected(parser: ConfluenceParser) -> None:
    """The one unrecoverable payload: nothing else can identify a page."""
    with pytest.raises(ValueError):
        parse(parser, make_raw(None))


@pytest.mark.parametrize("page_id", [None, "", "   ", [], {}, True])
def test_an_unusable_page_id_is_rejected(
    parser: ConfluenceParser, page_id: object
) -> None:
    with pytest.raises(ValueError):
        parse(parser, make_raw(page_id))


def test_a_missing_id_key_is_rejected(parser: ConfluenceParser) -> None:
    raw = make_raw()
    del raw["id"]

    with pytest.raises(ValueError):
        parse(parser, raw)


def test_a_numeric_id_is_accepted_as_a_string(parser: ConfluenceParser) -> None:
    """Ids are strings in v2, but nothing is gained by losing a page over that."""
    assert parse(parser, make_raw(6422643)).page_id == "6422643"


def test_a_numeric_parent_id_is_accepted_as_a_string(
    parser: ConfluenceParser,
) -> None:
    assert parse(parser, make_raw("102", parent_id=101)).parent_id == "101"


# ------------------------------------------------------------- missing parts


def test_a_page_with_no_body_is_still_a_page(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw(body=None))

    assert page.page_id == "111"
    assert page.content == ""


def test_an_empty_body_produces_empty_content(parser: ConfluenceParser) -> None:
    assert parse(parser, make_raw(body="")).content == ""


@pytest.mark.parametrize(
    "body",
    [
        None,
        "not a dict",
        [],
        {},
        {"storage": None},
        {"storage": "not a dict"},
        {"storage": {}},
        {"storage": {"value": None}},
        {"storage": {"value": 42}},
        {"atlas_doc_format": {"value": "{}"}},  # a representation we did not ask for
    ],
)
def test_an_unreadable_body_becomes_empty_content(
    parser: ConfluenceParser, body: object
) -> None:
    raw = make_raw()
    raw["body"] = body

    assert parse(parser, raw).content == ""


def test_a_page_with_no_version_is_still_a_page(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw(version=None))

    assert page.page_id == "111"
    assert page.version_number is None


@pytest.mark.parametrize(
    "version",
    [None, "not a dict", {}, {"number": None}, {"number": "7"}, {"number": True}],
)
def test_an_unreadable_version_becomes_none(
    parser: ConfluenceParser, version: object
) -> None:
    raw = make_raw()
    raw["version"] = version

    assert parse(parser, raw).version_number is None


def test_a_page_with_no_status_is_still_a_page(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw(status=None))

    assert page.page_id == "111"
    assert page.status is None


def test_a_page_with_no_title_is_still_a_page(parser: ConfluenceParser) -> None:
    page = parse(parser, make_raw(title=None))

    assert page.page_id == "111"
    assert page.title == ""


def test_a_title_is_trimmed(parser: ConfluenceParser) -> None:
    assert parse(parser, make_raw(title="  Deployment  ")).title == "Deployment"


# ------------------------------------------------------------------- titles


@pytest.mark.parametrize(
    "title",
    [
        "TrackIt — System Architecture",
        "Phase 1 — Core Task Management: End-to-End Technical Overview",
        "Q&A / FAQ (v2)",
        "<script>alert(1)</script>",
        "Café ☕ 日本語",
        "100% \"done\" — really",
    ],
)
def test_a_title_is_carried_through_verbatim(
    parser: ConfluenceParser, title: str
) -> None:
    """Titles are data. Nothing here escapes, strips or rewrites them."""
    assert parse(parser, make_raw(title=title)).title == title


def test_unicode_content_survives(parser: ConfluenceParser) -> None:
    raw = make_raw(body="<p>Café — naïve 日本語 🎉</p>")

    assert parse(parser, raw).content == "Café — naïve 日本語 🎉"


# --------------------------------------------------------------- parse_many


def test_every_page_is_parsed(parser: ConfluenceParser) -> None:
    errors: list[tuple[str, str]] = []

    pages = parser.parse_many(
        [make_raw("1"), make_raw("2"), make_raw("3")],
        errors,
        space_key=SPACE_KEY,
        space_id=SPACE_ID,
        space_name=SPACE_NAME,
    )

    assert [page.page_id for page in pages] == ["1", "2", "3"]
    assert errors == []


def test_nothing_to_parse_is_not_an_error(parser: ConfluenceParser) -> None:
    errors: list[tuple[str, str]] = []

    pages = parser.parse_many([], errors, space_key=SPACE_KEY, space_id=SPACE_ID)

    assert pages == []
    assert errors == []


def test_one_unreadable_page_does_not_cost_the_others(
    parser: ConfluenceParser,
) -> None:
    errors: list[tuple[str, str]] = []

    pages = parser.parse_many(
        [make_raw("1"), make_raw(None), make_raw("3")],
        errors,
        space_key=SPACE_KEY,
        space_id=SPACE_ID,
    )

    assert [page.page_id for page in pages] == ["1", "3"]


def test_an_unreadable_page_is_recorded_by_its_position(
    parser: ConfluenceParser,
) -> None:
    """With no id there is nothing else to name it by."""
    errors: list[tuple[str, str]] = []

    parser.parse_many(
        [make_raw("1"), make_raw(None)],
        errors,
        space_key=SPACE_KEY,
        space_id=SPACE_ID,
    )

    assert errors == [("page #2", "Confluence returned a page with no id.")]


def test_the_resolved_space_reaches_every_page(parser: ConfluenceParser) -> None:
    pages = parser.parse_many(
        [make_raw("1"), make_raw("2")],
        [],
        space_key=SPACE_KEY,
        space_id=SPACE_ID,
        space_name=SPACE_NAME,
    )

    assert {page.space_key for page in pages} == {SPACE_KEY}
    assert {page.space_id for page in pages} == {SPACE_ID}


def test_the_parse_count_is_logged(parser: ConfluenceParser, caplog) -> None:
    with caplog.at_level("INFO", logger="app.ingestion.confluence_parser"):
        parser.parse_many(
            [make_raw("1"), make_raw("2")],
            [],
            space_key=SPACE_KEY,
            space_id=SPACE_ID,
        )

    assert "Parsed 2 Confluence pages" in caplog.text


def test_a_page_body_is_not_logged(parser: ConfluenceParser, caplog) -> None:
    """Log volume tracks pages, not prose - see the connector's docstring."""
    with caplog.at_level("DEBUG"):
        parser.parse_many(
            [make_raw("1", body="<p>sentinel-page-body-text</p>")],
            [],
            space_key=SPACE_KEY,
            space_id=SPACE_ID,
        )

    assert "sentinel-page-body-text" not in caplog.text
