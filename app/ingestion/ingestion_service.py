"""Orchestrates one run: GitHub -> files -> filter -> parse -> chunks -> vectors.

This is where the pipeline is assembled. It owns the FileFilter, the parser
registry and the embedder, and hands the first two to the connector; the
connector knows how to talk to GitHub but not what we consider worth ingesting,
the parsers know how to read TypeScript but not where it came from, and the
embedder knows how to turn text into vectors but nothing about either.

The API route calls exactly one method here. Nothing in this module imports
FastAPI, nothing in it imports PyGithub, and nothing in it imports openai.
"""

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
from app.models.code_chunk import CodeChunk
from app.models.ingest_response import MAX_FILES_PER_INGESTION
from app.models.repository_file import RepositoryFile

logger = logging.getLogger(__name__)

# Builds the connector for a run. Injectable so tests can substitute a double
# without patching module internals or needing a real token.
ConnectorFactory = Callable[[SecretStr, str], BaseSourceConnector]


@dataclass
class IngestionResult:
    """The complete outcome of one run.

    Holds *every* file and *every* chunk. The HTTP layer serialises only a
    sample of this by default, but the full set is what the embedding step is
    handed - which is the whole point of keeping it intact here.
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

    # (path, reason) for anything skipped or degraded. Never fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)

    # What the embedding step did, or zeroes when it was skipped. The vectors
    # themselves live on the chunks above, not here - there is one list of
    # chunks in a run and it is the one the parser produced.
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
        # No default embedder on purpose. A service built without one parses and
        # chunks and stops there, which is what a test wants; the API wires a
        # real one in. An embedder constructed by mistake would be the kind of
        # default that quietly bills somebody.
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

        `max_files` overrides the service default for this run only. `embed`
        turns the embedding step off for a run that only wants chunks; it does
        nothing when the service was built without an embedder.

        Raises IngestionError subclasses for whole-run failures - bad
        credentials, missing repository, rate limit, and a failed embedding
        pass. Problems with individual files are collected into `errors` and do
        not stop the run: one unparseable file is not worth losing 400 good
        ones over, while half a set of vectors is worth nothing at all.
        """
        logger.info(
            "Ingesting %s (branch: %s)", repository, branch or "repository default"
        )
        started = time.monotonic()

        # The connector holds the caller's token, so it is closed as soon as the
        # fetching is done - parsing happens afterwards, without it.
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

        # The shared stage: identical for all four sources, so it lives beside
        # the embedder rather than being written out four times.
        embed_into(result, self.embedder, embed=embed)

        return result

    # --------------------------------------------------------------- internal

    def _parse_file(
        self, file: RepositoryFile, errors: list[tuple[str, str]]
    ) -> list[CodeChunk]:
        """Parse one file, recording rather than raising anything that goes wrong."""
        parser = self.registry.parser_for(file.extension)
        if parser is None:
            errors.append((file.path, "No parser is registered for this file type."))
            return []

        logger.debug("Parsing %s", file.path)

        warnings: list[str] = []
        try:
            chunks = parser.parse(file, warnings)
        except Exception:
            # One malformed file must not cost the other several hundred. The
            # traceback goes to the log; the client sees only the short reason.
            logger.exception("Parser failed on %s", file.path)
            errors.append((file.path, "The file could not be parsed."))
            return []

        for warning in warnings:
            errors.append((file.path, warning))

        return chunks

    @staticmethod
    def _count_parsed(files: list[RepositoryFile], result: IngestionResult) -> int:
        """How many files produced at least one chunk."""
        produced = {chunk.file_path for chunk in result.chunks}
        return sum(1 for file in files if file.path in produced)
