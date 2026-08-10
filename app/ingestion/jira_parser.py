"""Raw Jira issue JSON -> JiraIssue.

    {"key": "TRACK-25", "fields": {...}}   ->   JiraIssue(key="TRACK-25", ...)

This is the only module that knows Jira's field names. The connector knows
Jira's endpoints and hands back whatever JSON came off the wire; everything
downstream sees only JiraIssue. Nothing here performs I/O - the parser never
calls Jira, which is what makes parent/child linking a local pass rather than
one request per issue.

Every extraction is defensive. Jira omits fields an account cannot see, returns
null where a value is unset, and occasionally returns a shape nobody expects, so
nothing here indexes into a nested dict without checking what it found. A
payload with no usable key is the single case that raises; the caller records it
and moves on.
"""

import logging

from app.ingestion.jira_adf import adf_to_text
from app.models.jira_issue import UNKNOWN_ISSUE_TYPE, JiraIssue

logger = logging.getLogger(__name__)

# One raw issue exactly as the REST API returned it. `object` rather than `Any`
# because every read below narrows with isinstance anyway, and `object` is what
# forces that to stay true.
JiraIssueJson = dict[str, object]


class JiraParser:
    """Turns Jira's issue payloads into our own model."""

    def parse(self, raw: JiraIssueJson) -> JiraIssue:
        """Normalise one raw issue.

        Raises ValueError when the payload carries no issue key. Without a key
        an issue cannot be linked, chunked or referred to, so there is nothing
        useful to salvage - and a silently invented key would be worse.
        """
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Jira issue payload has no key.")

        fields = _as_dict(raw.get("fields"))
        description = adf_to_text(fields.get("description")).strip()

        return JiraIssue(
            key=key,
            project_key=_nested_name(fields, "project", "key") or _project_from(key),
            issue_type=_nested_name(fields, "issuetype", "name") or UNKNOWN_ISSUE_TYPE,
            summary=_text(fields.get("summary")),
            description=description or None,
            status=_nested_name(fields, "status", "name"),
            parent_key=_nested_name(fields, "parent", "key"),
            # Left empty on purpose. Children are resolved by the ingestion
            # service once every issue in the run has been parsed.
            child_issues=[],
        )

    def parse_many(
        self, raw_issues: list[JiraIssueJson], errors: list[tuple[str, str]]
    ) -> list[JiraIssue]:
        """Normalise every issue, recording the ones that could not be read.

        One malformed payload costs one issue, never the run - the same contract
        the GitHub pipeline gives a file that will not decode.
        """
        issues: list[JiraIssue] = []

        for position, raw in enumerate(raw_issues, start=1):
            try:
                issues.append(self.parse(raw))
            except ValueError:
                # No key means nothing to name it by, so it is recorded by its
                # position in the response instead.
                logger.warning("Skipping Jira issue %d: payload has no key", position)
                errors.append(
                    (f"issue #{position}", "Jira returned an issue with no key.")
                )

        logger.info("Parsed %d Jira issues", len(issues))
        return issues


# --------------------------------------------------------------- extraction


def _as_dict(value: object) -> dict[str, object]:
    """A nested object, or an empty one.

    Covers a missing key, an explicit null, and a value of the wrong type in a
    single place, so no caller has to guard each of those separately.
    """
    return value if isinstance(value, dict) else {}


def _nested_name(fields: dict[str, object], holder: str, attribute: str) -> str | None:
    """Read fields.<holder>.<attribute>, tolerating every level being absent.

    This is how `fields.parent.key` is read without a chain that explodes when
    an issue simply has no parent - which is true of every Epic.
    """
    value = _as_dict(fields.get(holder)).get(attribute)
    if isinstance(value, str) and value:
        return value
    return None


def _text(value: object) -> str:
    """A trimmed string field, or an empty string."""
    return value.strip() if isinstance(value, str) else ""


def _project_from(key: str) -> str:
    """Recover the project key from an issue key.

    A fallback for the case where `fields.project` was not returned. Jira issue
    keys are always PROJECT-NUMBER, so the prefix is the project.
    """
    return key.split("-", 1)[0]
