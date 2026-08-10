"""Tests for the Jira chunker.

The chunk body is what eventually gets embedded, so most of these assert the
whole rendered string rather than searching it for a substring - a layout
regression should fail a test, not quietly change what a model sees.
"""

import pytest

from app.ingestion.jira_chunker import NO_DESCRIPTION_TEXT, JiraChunker
from app.models.jira_issue import JiraIssue


@pytest.fixture
def chunker() -> JiraChunker:
    return JiraChunker()


def make_issue(
    key: str = "TRACK-25",
    *,
    issue_type: str = "Story",
    summary: str = "Allow employees to edit timesheets",
    description: str | None = "Employees should be able to edit their own entries.",
    status: str | None = "In Progress",
    parent_key: str | None = "TRACK-10",
    child_issues: list[str] | None = None,
    project_key: str = "TRACK",
) -> JiraIssue:
    return JiraIssue(
        key=key,
        project_key=project_key,
        issue_type=issue_type,
        summary=summary,
        description=description,
        status=status,
        parent_key=parent_key,
        child_issues=child_issues or [],
    )


# -------------------------------------------------------------- chunk layout


def test_a_story_chunk_has_the_expected_shape(chunker: JiraChunker) -> None:
    content = chunker.chunk(make_issue()).content

    assert content == (
        "Issue Key: TRACK-25\n"
        "Issue Type: Story\n"
        "Project: TRACK\n"
        "Summary: Allow employees to edit timesheets\n"
        "Status: In Progress\n"
        "Parent Epic: TRACK-10\n"
        "\n"
        "Description:\n"
        "Employees should be able to edit their own entries."
    )


def test_an_epic_chunk_has_the_expected_shape(chunker: JiraChunker) -> None:
    epic = make_issue(
        "TRACK-10",
        issue_type="Epic",
        summary="Timesheet Management",
        description="Provide complete timesheet management capabilities.",
        parent_key=None,
        child_issues=["TRACK-21", "TRACK-25"],
    )

    assert chunker.chunk(epic).content == (
        "Issue Key: TRACK-10\n"
        "Issue Type: Epic\n"
        "Project: TRACK\n"
        "Summary: Timesheet Management\n"
        "Status: In Progress\n"
        "\n"
        "Description:\n"
        "Provide complete timesheet management capabilities.\n"
        "\n"
        "Child Issues:\n"
        "TRACK-21\n"
        "TRACK-25"
    )


def test_the_header_order_is_stable(chunker: JiraChunker) -> None:
    content = chunker.chunk(make_issue()).content

    assert [line.split(":")[0] for line in content.split("\n")[:6]] == [
        "Issue Key",
        "Issue Type",
        "Project",
        "Summary",
        "Status",
        "Parent Epic",
    ]


def test_a_chunk_does_not_end_with_a_newline(chunker: JiraChunker) -> None:
    """No added padding, the same discipline CodeChunk.content keeps."""
    assert not chunker.chunk(make_issue()).content.endswith("\n")


# ------------------------------------------------------------- relationships


def test_a_story_chunk_names_its_parent_epic(chunker: JiraChunker) -> None:
    assert "Parent Epic: TRACK-10" in chunker.chunk(make_issue()).content


def test_an_orphaned_parent_key_still_appears(chunker: JiraChunker) -> None:
    """The Epic falling outside this run does not make the pointer useless."""
    issue = make_issue(parent_key="TRACK-999")

    assert "Parent Epic: TRACK-999" in chunker.chunk(issue).content


def test_an_epic_chunk_lists_its_child_keys(chunker: JiraChunker) -> None:
    epic = make_issue(
        "TRACK-10",
        issue_type="Epic",
        parent_key=None,
        child_issues=["TRACK-21", "TRACK-25", "TRACK-28"],
    )

    content = chunker.chunk(epic).content
    assert content.endswith("Child Issues:\nTRACK-21\nTRACK-25\nTRACK-28")


def test_an_epic_chunk_never_contains_a_child_description(
    chunker: JiraChunker,
) -> None:
    """The reason child_issues holds keys and not objects: a Story's text lives
    in the Story's own chunk, and duplicating it here would embed it twice."""
    secret = "sentinel-child-description"
    child = make_issue("TRACK-25", description=secret)
    epic = make_issue(
        "TRACK-10",
        issue_type="Epic",
        parent_key=None,
        child_issues=[child.key],
    )

    content = chunker.chunk(epic).content
    assert secret not in content
    assert "TRACK-25" in content


def test_an_issue_without_children_has_no_child_section(
    chunker: JiraChunker,
) -> None:
    assert "Child Issues" not in chunker.chunk(make_issue()).content


def test_an_issue_without_a_parent_has_no_parent_line(chunker: JiraChunker) -> None:
    assert "Parent Epic" not in chunker.chunk(make_issue(parent_key=None)).content


# --------------------------------------------------------------- empty fields


def test_a_missing_description_is_rendered_as_a_placeholder(
    chunker: JiraChunker,
) -> None:
    content = chunker.chunk(make_issue(description=None)).content

    assert content.endswith(f"Description:\n{NO_DESCRIPTION_TEXT}")
    assert "None" not in content


def test_a_missing_status_omits_the_status_line(chunker: JiraChunker) -> None:
    content = chunker.chunk(make_issue(status=None)).content

    assert "Status" not in content
    assert "None" not in content


def test_an_issue_with_nothing_optional_still_renders(chunker: JiraChunker) -> None:
    issue = make_issue(
        "TRACK-9",
        issue_type="Task",
        summary="Tidy up",
        description=None,
        status=None,
        parent_key=None,
    )

    assert chunker.chunk(issue).content == (
        "Issue Key: TRACK-9\n"
        "Issue Type: Task\n"
        "Project: TRACK\n"
        "Summary: Tidy up\n"
        "\n"
        "Description:\n"
        f"{NO_DESCRIPTION_TEXT}"
    )


def test_a_multi_line_description_is_kept_whole(chunker: JiraChunker) -> None:
    """One issue is one chunk; a long description is never split across two."""
    description = "First paragraph.\n\n- one\n- two\n\nClosing paragraph."

    content = chunker.chunk(make_issue(description=description)).content
    assert description in content


# --------------------------------------------------------------- chunk_many


def test_chunk_many_produces_exactly_one_chunk_per_issue(
    chunker: JiraChunker,
) -> None:
    """The invariant this whole version rests on."""
    issues = [
        make_issue("TRACK-10", issue_type="Epic", parent_key=None,
                   child_issues=["TRACK-25"]),
        make_issue("TRACK-25"),
        make_issue("TRACK-26", description=None),
        make_issue("TRACK-27", status=None),
        make_issue("TRACK-28", parent_key="TRACK-999"),
    ]

    chunks = chunker.chunk_many(issues)

    assert len(chunks) == len(issues)


def test_chunk_many_preserves_order(chunker: JiraChunker) -> None:
    issues = [make_issue(f"TRACK-{i}") for i in range(5)]

    chunks = chunker.chunk_many(issues)

    assert [chunk.key for chunk in chunks] == [issue.key for issue in issues]


def test_an_empty_issue_list_produces_no_chunks(chunker: JiraChunker) -> None:
    assert chunker.chunk_many([]) == []


# ---------------------------------------------------------------- metadata


def test_the_chunk_carries_the_issue_fields_verbatim(chunker: JiraChunker) -> None:
    issue = make_issue(
        "TRACK-10", issue_type="Epic", parent_key=None, child_issues=["TRACK-25"]
    )

    chunk = chunker.chunk(issue)

    assert chunk.key == issue.key
    assert chunk.project_key == issue.project_key
    assert chunk.issue_type == issue.issue_type
    assert chunk.summary == issue.summary
    assert chunk.description == issue.description
    assert chunk.status == issue.status
    assert chunk.parent_key == issue.parent_key
    assert chunk.child_issues == issue.child_issues


def test_the_chunk_does_not_share_the_issues_child_list(
    chunker: JiraChunker,
) -> None:
    """A later change to the issue must not silently rewrite an emitted chunk."""
    issue = make_issue("TRACK-10", issue_type="Epic", child_issues=["TRACK-25"])
    chunk = chunker.chunk(issue)

    issue.child_issues.append("TRACK-26")

    assert chunk.child_issues == ["TRACK-25"]
