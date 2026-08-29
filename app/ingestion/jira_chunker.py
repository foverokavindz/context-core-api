
import logging

from app.models.jira.chunk import JiraChunk
from app.models.jira.issue import JiraIssue

logger = logging.getLogger(__name__)

NO_DESCRIPTION_TEXT = "(no description)"


class JiraChunker:
    """Renders issues as the text we expect to embed."""

    def chunk(self, issue: JiraIssue) -> JiraChunk:
        """Render one issue.
        """
        return JiraChunk(
            key=issue.key,
            project_key=issue.project_key,
            issue_type=issue.issue_type,
            summary=issue.summary,
            description=issue.description,
            status=issue.status,
            parent_key=issue.parent_key,
            child_issues=list(issue.child_issues),
            content=self._render_content(issue),
            external_id=issue.external_id,
        )

    def chunk_many(self, issues: list[JiraIssue]) -> list[JiraChunk]:
        """Render every issue, in order.
        """
        chunks = [self.chunk(issue) for issue in issues]
        logger.info("Generated %d Jira chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(issue: JiraIssue) -> str:
        """Lay out one issue as plain readable text.
        """
        lines = [
            f"Issue Key: {issue.key}",
            f"Issue Type: {issue.issue_type}",
            f"Project: {issue.project_key}",
            f"Summary: {issue.summary}",
        ]

        if issue.status:
            lines.append(f"Status: {issue.status}")
        if issue.parent_key:
            lines.append(f"Parent Epic: {issue.parent_key}")

        lines.append("")
        lines.append("Description:")
        lines.append(issue.description or NO_DESCRIPTION_TEXT)

        if issue.child_issues:
            lines.append("")
            lines.append("Child Issues:")
            lines.extend(issue.child_issues)

        return "\n".join(lines)
