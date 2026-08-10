"""Tests for the Jira issue parser.

Jira omits fields an account cannot see and returns null for anything unset, so
most of these cover a field being absent rather than being present. The parser's
contract is that only a missing issue key is fatal.
"""

import pytest

from app.ingestion.jira_parser import JiraParser
from app.models.jira_issue import UNKNOWN_ISSUE_TYPE


@pytest.fixture
def parser() -> JiraParser:
    return JiraParser()


def make_raw(
    key: str = "TRACK-25",
    *,
    issue_type: str | None = "Story",
    summary: str | None = "Allow employees to edit timesheets",
    description: object = "Employees should be able to edit their own entries.",
    status: str | None = "In Progress",
    parent: str | None = "TRACK-10",
    project: str | None = "TRACK",
) -> dict:
    """One raw issue, shaped as the REST API returns it.

    Every field is nullable so a test can remove exactly the one it is about.
    """
    fields: dict = {"description": description}
    if summary is not None:
        fields["summary"] = summary
    if issue_type is not None:
        fields["issuetype"] = {"id": "10001", "name": issue_type}
    if status is not None:
        fields["status"] = {
            "name": status,
            "statusCategory": {"key": "indeterminate", "name": "In Progress"},
        }
    if parent is not None:
        fields["parent"] = {"id": "10010", "key": parent}
    if project is not None:
        fields["project"] = {"id": "10000", "key": project, "name": "Tracker"}

    return {"id": "10025", "key": key, "self": "https://x/rest/api/3/issue/10025",
            "fields": fields}


def adf(*paragraphs: str) -> dict:
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]}
            for p in paragraphs
        ],
    }


# --------------------------------------------------------------- happy path


def test_the_core_fields_are_extracted(parser: JiraParser) -> None:
    issue = parser.parse(make_raw())

    assert issue.key == "TRACK-25"
    assert issue.project_key == "TRACK"
    assert issue.issue_type == "Story"
    assert issue.summary == "Allow employees to edit timesheets"
    assert issue.description == "Employees should be able to edit their own entries."
    assert issue.status == "In Progress"
    assert issue.parent_key == "TRACK-10"


def test_an_epic_has_no_parent(parser: JiraParser) -> None:
    issue = parser.parse(make_raw("TRACK-10", issue_type="Epic", parent=None))

    assert issue.is_epic is True
    assert issue.parent_key is None


def test_child_issues_start_empty(parser: JiraParser) -> None:
    """Linking is the ingestion service's job, not the parser's - it needs the
    whole run to be parsed before it can resolve anything."""
    assert parser.parse(make_raw("TRACK-10", issue_type="Epic")).child_issues == []


def test_summary_whitespace_is_stripped(parser: JiraParser) -> None:
    assert parser.parse(make_raw(summary="  Edit timesheets  ")).summary == (
        "Edit timesheets"
    )


def test_the_status_name_is_taken_not_the_category(parser: JiraParser) -> None:
    """"In Progress" is a category shared by many statuses; the name is the one
    that says what this issue is actually doing."""
    raw = make_raw()
    raw["fields"]["status"] = {
        "name": "Ready for Review",
        "statusCategory": {"key": "indeterminate", "name": "In Progress"},
    }

    assert parser.parse(raw).status == "Ready for Review"


# -------------------------------------------------------------- descriptions


def test_an_adf_description_is_flattened_to_text(parser: JiraParser) -> None:
    issue = parser.parse(make_raw(description=adf("First para", "Second para")))

    assert issue.description == "First para\n\nSecond para"


def test_a_plain_string_description_is_kept(parser: JiraParser) -> None:
    assert parser.parse(make_raw(description="just a string")).description == (
        "just a string"
    )


def test_a_null_description_becomes_none(parser: JiraParser) -> None:
    assert parser.parse(make_raw(description=None)).description is None


def test_an_empty_adf_description_becomes_none(parser: JiraParser) -> None:
    """A document that flattens to nothing is the same as no description, so the
    chunker renders its placeholder rather than an empty section."""
    assert parser.parse(make_raw(description=adf())).description is None


def test_a_whitespace_only_description_becomes_none(parser: JiraParser) -> None:
    assert parser.parse(make_raw(description="   \n  ")).description is None


# ------------------------------------------------------------- missing data


def test_a_missing_status_becomes_none(parser: JiraParser) -> None:
    assert parser.parse(make_raw(status=None)).status is None


def test_a_missing_parent_becomes_none(parser: JiraParser) -> None:
    assert parser.parse(make_raw(parent=None)).parent_key is None


def test_a_null_parent_becomes_none(parser: JiraParser) -> None:
    """`parent` present but null is what Jira actually sends for a top-level
    issue in some responses - it must not raise."""
    raw = make_raw()
    raw["fields"]["parent"] = None

    assert parser.parse(raw).parent_key is None


def test_a_missing_issue_type_becomes_a_placeholder(parser: JiraParser) -> None:
    issue = parser.parse(make_raw(issue_type=None))

    assert issue.issue_type == UNKNOWN_ISSUE_TYPE
    assert issue.is_epic is False
    assert issue.is_story is False


def test_a_missing_summary_becomes_empty(parser: JiraParser) -> None:
    assert parser.parse(make_raw(summary=None)).summary == ""


def test_the_project_key_falls_back_to_the_issue_key_prefix(
    parser: JiraParser,
) -> None:
    assert parser.parse(make_raw("TRACK-25", project=None)).project_key == "TRACK"


@pytest.mark.parametrize("fields", [None, {}, "not a dict", []])
def test_an_unusable_fields_object_is_tolerated(
    parser: JiraParser, fields: object
) -> None:
    """Only the key is truly required; everything else degrades to a default."""
    issue = parser.parse({"key": "TRACK-9", "fields": fields})

    assert issue.key == "TRACK-9"
    assert issue.project_key == "TRACK"
    assert issue.issue_type == UNKNOWN_ISSUE_TYPE
    assert issue.summary == ""
    assert issue.description is None
    assert issue.status is None
    assert issue.parent_key is None


def test_a_missing_fields_object_is_tolerated(parser: JiraParser) -> None:
    assert parser.parse({"key": "TRACK-9"}).key == "TRACK-9"


@pytest.mark.parametrize("raw", [{}, {"key": None}, {"key": ""}, {"key": 10025}])
def test_an_issue_without_a_key_is_rejected(parser: JiraParser, raw: dict) -> None:
    """The one fatal case: nothing can link, chunk or refer to a keyless issue."""
    with pytest.raises(ValueError):
        parser.parse(raw)


# ---------------------------------------------------------------- parse_many


def test_parse_many_normalises_every_issue(parser: JiraParser) -> None:
    errors: list[tuple[str, str]] = []

    issues = parser.parse_many(
        [make_raw("TRACK-10"), make_raw("TRACK-25"), make_raw("TRACK-26")], errors
    )

    assert [issue.key for issue in issues] == ["TRACK-10", "TRACK-25", "TRACK-26"]
    assert errors == []


def test_parse_many_reports_a_bad_payload_without_losing_the_others(
    parser: JiraParser,
) -> None:
    errors: list[tuple[str, str]] = []

    issues = parser.parse_many([make_raw("TRACK-10"), {}, make_raw("TRACK-26")], errors)

    assert [issue.key for issue in issues] == ["TRACK-10", "TRACK-26"]
    assert errors == [("issue #2", "Jira returned an issue with no key.")]


def test_parse_many_on_an_empty_list_is_not_an_error(parser: JiraParser) -> None:
    errors: list[tuple[str, str]] = []

    assert parser.parse_many([], errors) == []
    assert errors == []


def test_issue_descriptions_are_not_logged_at_info(
    parser: JiraParser, caplog
) -> None:
    """Descriptions can be long and can contain anything an author typed."""
    secret = "sentinel-description-text"

    with caplog.at_level("INFO", logger="app.ingestion.jira_parser"):
        parser.parse_many([make_raw(description=secret)], [])

    assert secret not in caplog.text
    assert "Parsed 1 Jira issues" in caplog.text
