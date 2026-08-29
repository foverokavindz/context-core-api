
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.connectors.jira_connector import JiraConnector
from app.ingestion.embedding_service import ChunkEmbedder, embed_into
from app.ingestion.jira_chunker import JiraChunker
from app.ingestion.jira_parser import JiraParser
from app.models.jira.chunk import JiraChunk
from app.models.jira.issue import JiraIssue
from app.models.jira.response import MAX_ISSUES_PER_INGESTION

logger = logging.getLogger(__name__)

JiraConnectorFactory = Callable[[str, str, SecretStr], JiraConnector]


@dataclass
class JiraIngestionResult:
    """The complete outcome of one run.
    """

    site_url: str
    project_key: str

    retrieved_issues: int

    truncated: bool = False

    issues: list[JiraIssue] = field(default_factory=list)
    chunks: list[JiraChunk] = field(default_factory=list)

    errors: list[tuple[str, str]] = field(default_factory=list)

    embedded_chunks: int = 0
    embedding_batches: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_truncated_inputs: int = 0

    @property
    def generated_chunks(self) -> int:
        return len(self.chunks)

    @property
    def epics(self) -> int:
        return sum(1 for issue in self.issues if issue.is_epic)

    @property
    def stories(self) -> int:
        return sum(1 for issue in self.issues if issue.is_story)


class JiraIngestionService:
    """Runs the Jira pipeline end to end for one project."""

    def __init__(
        self,
        *,
        parser: JiraParser | None = None,
        chunker: JiraChunker | None = None,
        connector_factory: JiraConnectorFactory = JiraConnector,
        max_issues: int = MAX_ISSUES_PER_INGESTION,
        embedder: ChunkEmbedder | None = None,
    ) -> None:
        self.parser = parser or JiraParser()
        self.chunker = chunker or JiraChunker()
        self.connector_factory = connector_factory
        self.max_issues = max_issues

        self.embedder = embedder

    def ingest(
        self,
        site_url: str,
        email: str,
        api_token: SecretStr,
        project_key: str,
        max_issues: int | None = None,
        embed: bool = True,
    ) -> JiraIngestionResult:
        """Ingest one Jira project and return everything that was produced.
        """
        logger.info("Ingesting Jira project %s from %s", project_key, site_url)
        started = time.monotonic()

        with self.connector_factory(site_url, email, api_token) as connector:
            snapshot = connector.get_issues(
                project_key,
                max_issues=self.max_issues if max_issues is None else max_issues,
            )

        result = JiraIngestionResult(
            site_url=snapshot.site_url,
            project_key=snapshot.project_key,
            retrieved_issues=snapshot.retrieved_issues,
            truncated=snapshot.truncated,
            errors=list(snapshot.errors),
        )

        result.issues = self.parser.parse_many(snapshot.issues, result.errors)
        linked = self._link_parents(result.issues, project_key, result.errors)
        result.chunks = self.chunker.chunk_many(result.issues)

        logger.info(
            "Ingested %d Jira issues (%d epics, %d stories, %d linked to an epic) "
            "into %d chunks in %.1fs",
            len(result.issues),
            result.epics,
            result.stories,
            linked,
            result.generated_chunks,
            time.monotonic() - started,
        )

        embed_into(result, self.embedder, embed=embed)

        return result

    # --------------------------------------------------------------- internal

    @staticmethod
    def _link_parents(
        issues: list[JiraIssue], project_key: str, errors: list[tuple[str, str]]
    ) -> int:
        """Fill in each Epic's children from its Stories' parent pointers.
        """
        by_key = {issue.key: issue for issue in issues}
        children: dict[str, set[str]] = {}
        orphans = 0

        for issue in issues:
            if not issue.parent_key or issue.parent_key == issue.key:
                # A self-referencing parent is malformed; ignoring it is safer
                # than letting an issue become its own child.
                continue

            parent = by_key.get(issue.parent_key)
            if parent is not None and parent.is_epic:
                children.setdefault(parent.key, set()).add(issue.key)
            else:
                orphans += 1
                logger.debug(
                    "Issue %s references parent %s, which is not an epic in this "
                    "ingestion",
                    issue.key,
                    issue.parent_key,
                )

        for key, child_keys in children.items():
            by_key[key].child_issues = sorted(child_keys)

        if orphans:

            logger.info(
                "%d issues reference a parent outside this ingestion", orphans
            )
            errors.append(
                (
                    project_key,
                    f"{orphans} issue(s) reference a parent that was not part of "
                    "this ingestion.",
                )
            )

        linked = sum(len(child_keys) for child_keys in children.values())
        logger.info("Linked %d issues to %d epics", linked, len(children))
        return linked
