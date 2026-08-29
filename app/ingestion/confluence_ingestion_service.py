
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.connectors.confluence_connector import ConfluenceConnector
from app.ingestion.confluence_chunker import ConfluenceChunker
from app.ingestion.confluence_parser import ConfluenceParser
from app.ingestion.embedding_service import ChunkEmbedder, embed_into
from app.models.confluence.chunk import ConfluenceChunk
from app.models.confluence.page import ConfluencePage
from app.models.confluence.response import MAX_PAGES_PER_INGESTION

logger = logging.getLogger(__name__)

# Builds the connector for a run. Injectable so tests can substitute a double
# without patching module internals or needing a real Confluence site.
ConfluenceConnectorFactory = Callable[[str, str, SecretStr], ConfluenceConnector]


@dataclass
class ConfluenceIngestionResult:
    """The complete outcome of one run.
    """

    site_url: str

    space_id: str
    space_key: str
    space_name: str | None = None

    retrieved_pages: int = 0

    truncated: bool = False

    pages: list[ConfluencePage] = field(default_factory=list)
    chunks: list[ConfluenceChunk] = field(default_factory=list)

    errors: list[tuple[str, str]] = field(default_factory=list)

    embedded_chunks: int = 0
    embedding_batches: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_truncated_inputs: int = 0

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
        embedder: ChunkEmbedder | None = None,
    ) -> None:
        self.parser = parser or ConfluenceParser()
        self.chunker = chunker or ConfluenceChunker()
        self.connector_factory = connector_factory
        self.max_pages = max_pages
        self.embedder = embedder

    def ingest(
        self,
        site_url: str,
        email: str,
        api_token: SecretStr,
        space_key: str,
        max_pages: int | None = None,
        embed: bool = True,
    ) -> ConfluenceIngestionResult:
        """Ingest one Confluence space and return everything that was produced.
        """
        logger.info(
            "Ingesting Confluence space %s from %s", space_key, site_url
        )
        started = time.monotonic()

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

        embed_into(result, self.embedder, embed=embed)

        return result
