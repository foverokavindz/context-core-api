"""JiraIssue -> JiraChunk.

    JiraIssue  ->  JiraChunker  ->  JiraChunk

One issue produces exactly one chunk. No splitting by tokens, characters,
paragraphs, headings or sentences - the simplest baseline that can work, so that
what a chunk contains is obvious before any smarter strategy is layered on. If
Jira descriptions turn out to be long enough to need splitting, that decision
belongs here and nowhere else.

This module is where all chunk formatting lives. The route does not build chunk
text and neither does the connector, so the shape of what gets embedded is
readable in one place.

The one rule worth stating twice: an Epic's chunk names its children by key and
never repeats their text. Each Story already has its own chunk, so copying its
description into its Epic would duplicate the same prose across two embeddings.
"""

import logging

from app.models.jira_chunk import JiraChunk
from app.models.jira_issue import JiraIssue

logger = logging.getLogger(__name__)

# Stands in for an empty description so a chunk never ends on a dangling
# "Description:" header with nothing under it.
NO_DESCRIPTION_TEXT = "(no description)"


class JiraChunker:
    """Renders issues as the text we expect to embed."""

    def chunk(self, issue: JiraIssue) -> JiraChunk:
        """Render one issue.

        The issue's own fields are carried onto the chunk as metadata as well as
        being rendered into `content`, so a chunk is self-describing once it
        leaves the pipeline.
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
        )

    def chunk_many(self, issues: list[JiraIssue]) -> list[JiraChunk]:
        """Render every issue, in order.

        len(chunks) == len(issues), always. Nothing here can skip an issue or
        emit two for one, which is what makes `generated_chunks` and
        `retrieved_issues` comparable in the response.
        """
        chunks = [self.chunk(issue) for issue in issues]
        logger.info("Generated %d Jira chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(issue: JiraIssue) -> str:
        """Lay out one issue as plain readable text.

        Absent values are omitted rather than rendered as "None": a Story with
        no status should not carry the word "None" into its embedding.
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

        # Keys only. `child_issues` is a list[str], so there is no code path
        # here that could reach a child's description even by accident.
        if issue.child_issues:
            lines.append("")
            lines.append("Child Issues:")
            lines.extend(issue.child_issues)

        return "\n".join(lines)
