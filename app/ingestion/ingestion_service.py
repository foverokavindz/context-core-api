
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.connectors.base import BaseSourceConnector
from app.connectors.github_connector import GitHubConnector
from app.ingestion.embedding_service import ChunkEmbedder, embed_into
from app.ingestion.file_filter import FileFilter
from app.ingestion.parser.base import ParserRegistry
from app.ingestion.parser.typescript_parser import TypeScriptTreeSitterParser
from app.models.github.chunk import CodeChunk
from app.models.github.file import RepositoryFile
from app.models.github.response import MAX_FILES_PER_INGESTION

logger = logging.getLogger(__name__)

ConnectorFactory = Callable[[SecretStr, str], BaseSourceConnector]


@dataclass
class IngestionResult:
    """The complete outcome of one run.
    """

    repository: str
    branch: str
    commit_sha: str

    discovered_files: int
    accepted_files: int
    parsed_files: int
    truncated: bool = False

    files: list[RepositoryFile] = field(default_factory=list)
    chunks: list[CodeChunk] = field(default_factory=list)

    errors: list[tuple[str, str]] = field(default_factory=list)

    embedded_chunks: int = 0
    embedding_batches: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_truncated_inputs: int = 0

    @property
    def generated_chunks(self) -> int:
        return len(self.chunks)


def _default_registry() -> ParserRegistry:
    """The parsers available today: TypeScript and TSX."""
    return ParserRegistry([TypeScriptTreeSitterParser()])


class GitHubIngestionService:
    """Runs a repository through the ingestion pipeline."""

    def __init__(
        self,
        *,
        file_filter: FileFilter | None = None,
        registry: ParserRegistry | None = None,
        connector_factory: ConnectorFactory = GitHubConnector,
        max_files: int = MAX_FILES_PER_INGESTION,
        embedder: ChunkEmbedder | None = None,
    ) -> None:
        self.file_filter = file_filter or FileFilter()
        self.registry = registry or _default_registry()
        self.connector_factory = connector_factory
        self.max_files = max_files
        self.embedder = embedder

    def ingest(
        self,
        token: SecretStr,
        repository: str,
        branch: str | None = None,
        max_files: int | None = None,
        embed: bool = True,
    ) -> IngestionResult:
        """Ingest one repository branch and return everything that was produced.
        """
        logger.info(
            "Ingesting %s (branch: %s)", repository, branch or "repository default"
        )
        started = time.monotonic()

        with self.connector_factory(token, repository) as connector:
            snapshot = connector.get_files(
                branch=branch,
                path_filter=self.file_filter.should_include,
                max_files=self.max_files if max_files is None else max_files,
            )

        # all the files from github repo
        result = IngestionResult(
            repository=snapshot.repository,
            branch=snapshot.branch,
            commit_sha=snapshot.commit_sha,
            discovered_files=snapshot.discovered_paths,
            accepted_files=len(snapshot.files),
            parsed_files=0,
            truncated=snapshot.truncated,
            files=snapshot.files,
            errors=list(snapshot.errors),
        )

        for file in snapshot.files:
            # parse each file, adding any errors to the result and returning the chunks
            result.chunks.extend(self._parse_file(file, result.errors))

        result.parsed_files = self._count_parsed(snapshot.files, result)

        logger.info(
            "Generated %d code chunks from %d files in %.1fs",
            len(result.chunks),
            result.parsed_files,
            time.monotonic() - started,
        )

        embed_into(result, self.embedder, embed=embed)

        return result

    # --------------------------------------------------------------- internal

    def _parse_file(
        self, file: RepositoryFile, errors: list[tuple[str, str]]
    ) -> list[CodeChunk]:
        """Parse one file, recording rather than raising anything that goes wrong."""
        parser = self.registry.parser_for(file.extension)
        if parser is None:
            errors.append((file.file_path, "No parser is registered for this file type."))
            return []

        logger.debug("Parsing %s", file.file_path)

        warnings: list[str] = []
        try:
            chunks = parser.parse(file, warnings)
        except Exception:

            logger.exception("Parser failed on %s", file.file_path)
            errors.append((file.file_path, "The file could not be parsed."))
            return []

        for warning in warnings:
            errors.append((file.file_path, warning))

        return chunks

    @staticmethod
    def _count_parsed(files: list[RepositoryFile], result: IngestionResult) -> int:
        """How many files produced at least one chunk."""
        produced = {chunk.file_path for chunk in result.chunks}
        return sum(1 for file in files if file.file_path in produced)
