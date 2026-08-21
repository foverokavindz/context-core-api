"""Turns the four sources' chunk models into `chunks` rows, and searches them."""

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session, defer

from app.entities.chunks.chunk import Chunk
from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource import Resource
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.models.confluence.chunk import ConfluenceChunk
from app.models.github.chunk import CodeChunk
from app.models.jira.chunk import JiraChunk
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
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

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_new_chunks(self, chunks: list[SourceChunk]) -> list[Chunk]:

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

    def search_by_embedding(
        self,
        embedding: list[float],
        source: SourceType,
        access: AccessContext,
        top_k: int,
    ) -> list[RetrievalResult]:

        rows = self.session.execute(
            _search_statement(embedding, source, access, top_k)
        ).all()
        return [_to_result(row, source) for row in rows]


def _search_statement(
    embedding: list[float],
    source: SourceType,
    access: AccessContext,
    top_k: int,
) -> Select:

    distance = Chunk.embedding.cosine_distance(embedding).label("distance")

    return (
        select(Chunk, Resource, ExternalDataSource.name, distance)
        .join(
            Resource,
            and_(
                Chunk.external_data_source_id == Resource.external_data_source_id,
                Chunk.external_id == Resource.external_id,
            ),
        )  
        .join(
            ExternalDataSource,
            Resource.external_data_source_id == ExternalDataSource.id,
        )
        .where(
           
            ExternalDataSource.source_type == source,
            ExternalDataSource.team_id == access.team_id,
            ExternalDataSource.status == SourceStatus.ACTIVE,
            Chunk.embedding.is_not(None),
            _readable_by(access),
        )
        .options(defer(Chunk.embedding))  
        .order_by(distance, Chunk.id) 
        .limit(top_k)
    )


def _readable_by(access: AccessContext):

    return or_(
        Resource.access_scope == ResourceAccessScope.ORGANIZATION,
        and_(
            Resource.access_scope == ResourceAccessScope.TEAM,
            Resource.team_id == access.team_id,
        ),
        and_(
            Resource.access_scope == ResourceAccessScope.DEPARTMENT,
            Resource.department_id == access.department_id,
        ),
    )


def _to_result(row, source: SourceType) -> RetrievalResult:
    chunk, resource, source_name, distance = row

    return RetrievalResult(
        chunk_id=chunk.id,
        content=chunk.content,
        score=1.0 - distance,
        source=source,
        source_name=source_name,
        external_data_source_id=chunk.external_data_source_id,
        external_id=chunk.external_id,
        resource_id=resource.id,
        resource_type=resource.resource_type,
        resource_title=resource.title,
        chunk_type=chunk.chunk_type,
        chunk_metadata=chunk.chunk_metadata or {},
        resource_metadata=resource.resource_metadata or {},
    )
