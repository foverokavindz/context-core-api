"""Orchestration for Confluence ingestion: fetch, parse, chunk.

    ConfluenceConnector -> raw JSON -> ConfluenceParser -> ConfluencePage[] ->
    ConfluenceChunk[]

This module owns the order of the pipeline and nothing else. It contains no HTTP
calls, no knowledge of Confluence's field names, and no chunk formatting - those
belong to the connector, the parser and the chunker respectively.

There is no linking pass, which is the one place this differs from the Jira
service. Jira needs one because it tells you a Story's parent but never an
Epic's children, and asking for them would cost a request per Epic. Confluence
reports parentId the same way, but nothing downstream wants the reverse
direction, so a page keeps its pointer upward and no children are reconstructed.
That is a smaller pipeline, not an unfinished one.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.connectors.confluence_connector import ConfluenceConnector
from app.ingestion.confluence_chunker import ConfluenceChunker
from app.ingestion.confluence_parser import ConfluenceParser
from app.models.confluence_chunk import ConfluenceChunk
from app.models.confluence_page import ConfluencePage
from app.models.confluence_response import MAX_PAGES_PER_INGESTION

logger = logging.getLogger(__name__)

# Builds the connector for a run. Injectable so tests can substitute a double
# without patching module internals or needing a real Confluence site.
ConfluenceConnectorFactory = Callable[[str, str, SecretStr], ConfluenceConnector]


@dataclass
class ConfluenceIngestionResult:
    """The complete outcome of one run.

    Holds *every* page and *every* chunk. The HTTP layer serialises only a
    sample of this, but the full set is what a later phase would hand to an
    embedding step - which is the whole point of keeping it intact here.
    """

    site_url: str

    space_id: str
    space_key: str
    space_name: str | None = None

    # What the connector actually retrieved, which is not the same as
    # len(pages): an unparseable payload is dropped but was still retrieved.
    retrieved_pages: int = 0

    # True when the run saw only part of the space. Independent of how much of
    # the result the response later serialises.
    truncated: bool = False

    pages: list[ConfluencePage] = field(default_factory=list)
    chunks: list[ConfluenceChunk] = field(default_factory=list)

    # (page id or space key, reason) for anything skipped or noted. Never fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def parsed_pages(self) -> int:
        return len(self.pages)

    @property
    def generated_chunks(self) -> int:
        return len(self.chunks)


class ConfluenceIngestionService:
    """Runs the Confluence pipeline end to end for one space."""

    def __init__(
        self,
        *,
        parser: ConfluenceParser | None = None,
        chunker: ConfluenceChunker | None = None,
        connector_factory: ConfluenceConnectorFactory = ConfluenceConnector,
        max_pages: int = MAX_PAGES_PER_INGESTION,
    ) -> None:
        self.parser = parser or ConfluenceParser()
        self.chunker = chunker or ConfluenceChunker()
        self.connector_factory = connector_factory
        self.max_pages = max_pages

    def ingest(
        self,
        site_url: str,
        email: str,
        api_token: SecretStr,
        space_key: str,
        max_pages: int | None = None,
    ) -> ConfluenceIngestionResult:
        """Ingest one Confluence space and return everything that was produced.

        `max_pages` overrides the service default for this run only.

        Raises IngestionError subclasses for whole-run failures - bad
        credentials, an invisible space, a rate limit. Problems with individual
        pages are collected into `errors` and do not stop the run.
        """
        logger.info(
            "Ingesting Confluence space %s from %s", space_key, site_url
        )
        started = time.monotonic()

        # The connector holds the caller's token, so it is closed as soon as the
        # fetching is done - parsing and chunking happen afterwards, without it.
        with self.connector_factory(site_url, email, api_token) as connector:
            snapshot = connector.get_pages(
                space_key,
                max_pages=self.max_pages if max_pages is None else max_pages,
            )

        result = ConfluenceIngestionResult(
            site_url=snapshot.site_url,
            space_id=snapshot.space_id,
            space_key=snapshot.space_key,
            space_name=snapshot.space_name,
            retrieved_pages=snapshot.retrieved_pages,
            truncated=snapshot.truncated,
            errors=list(snapshot.errors),
        )

        # The space comes from the snapshot, not from what the caller typed:
        # every page in the result is stamped with the space that was actually
        # resolved, so the response cannot claim a space the run did not read.
        result.pages = self.parser.parse_many(
            snapshot.pages,
            result.errors,
            space_key=snapshot.space_key,
            space_id=snapshot.space_id,
            space_name=snapshot.space_name,
        )
        result.chunks = self.chunker.chunk_many(result.pages)

        logger.info(
            "Ingested Confluence space %s (%d pages retrieved, %d parsed) into "
            "%d chunks in %.1fs",
            result.space_key,
            result.retrieved_pages,
            result.parsed_pages,
            result.generated_chunks,
            time.monotonic() - started,
        )
        return result
