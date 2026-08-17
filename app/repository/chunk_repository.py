"""Turns the four sources' chunk models into `chunks` rows."""

from sqlalchemy.orm import Session

from app.entities.chunks.chunk import Chunk
from app.models.confluence.chunk import ConfluenceChunk
from app.models.github.chunk import CodeChunk
from app.models.jira.chunk import JiraChunk
from app.models.slack.chunk import SlackChunk

SourceChunk = CodeChunk | JiraChunk | ConfluenceChunk | SlackChunk

# The fields that become real columns.
_NOT_METADATA = frozenset(
    {
        "external_data_source_id",
        "external_id",
        "chunk_index",
        "chunk_type",
        "content",
        "embedding",
        "embedding_model",
        "access_scope",
        "team_id",
        "department_id",
    }
)


class ChunkRepository:
    """Writes `chunks` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_new_chunks(self, chunks: list[SourceChunk]) -> list[Chunk]:
        """Insert one row per chunk and return them.

        Call ResourceRepository.add_new_resources first.
        """
        rows = [
            Chunk(
                external_data_source_id=chunk.external_data_source_id,
                external_id=chunk.external_id,
                chunk_index=chunk.chunk_index,
                chunk_type=chunk.chunk_type,
                content=chunk.content,
                embedding=chunk.embedding,
                embedding_model=chunk.embedding_model,
                chunk_metadata=chunk.model_dump(mode="json", exclude=_NOT_METADATA),
                access_scope=chunk.access_scope,
                team_id=chunk.team_id,
                department_id=chunk.department_id,
            )
            for chunk in chunks
        ]

        self.session.add_all(rows)
        self.session.flush()
        return rows
