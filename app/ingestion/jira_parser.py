
import logging

from app.ingestion.jira_adf import adf_to_text
from app.models.jira.issue import UNKNOWN_ISSUE_TYPE, JiraIssue

logger = logging.getLogger(__name__)

JiraIssueJson = dict[str, object]


class JiraParser:
    """Turns Jira's issue payloads into our own model."""

    def parse(self, raw: JiraIssueJson) -> JiraIssue:
        """Normalise one raw issue.
        """
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Jira issue payload has no key.")

        fields = _as_dict(raw.get("fields"))
        description = adf_to_text(fields.get("description")).strip()
        summary = _text(fields.get("summary"))

        return JiraIssue(
            key=key,
            project_key=_nested_name(fields, "project", "key") or _project_from(key),
            issue_type=_nested_name(fields, "issuetype", "name") or UNKNOWN_ISSUE_TYPE,
            summary=summary,
            description=description or None,
            status=_nested_name(fields, "status", "name"),
            parent_key=_nested_name(fields, "parent", "key"),

            child_issues=[],

            external_id=key,
            title=summary,
        )

    def parse_many(
        self, raw_issues: list[JiraIssueJson], errors: list[tuple[str, str]]
    ) -> list[JiraIssue]:
        """Normalise every issue, recording the ones that could not be read.
        """
        issues: list[JiraIssue] = []

        for position, raw in enumerate(raw_issues, start=1):
            try:
                issues.append(self.parse(raw))
            except ValueError:

                logger.warning("Skipping Jira issue %d: payload has no key", position)
                errors.append(
                    (f"issue #{position}", "Jira returned an issue with no key.")
                )

        logger.info("Parsed %d Jira issues", len(issues))
        return issues


# --------------------------------------------------------------- extraction


def _as_dict(value: object) -> dict[str, object]:
    """A nested object, or an empty one.
    """
    return value if isinstance(value, dict) else {}


def _nested_name(fields: dict[str, object], holder: str, attribute: str) -> str | None:
    """Read fields.<holder>.<attribute>, tolerating every level being absent.
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
    """
    return key.split("-", 1)[0]
