"""The one search behind all four retrievers."""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.db.session import SessionLocal
from app.entities.data_sources.source_type import SourceType
from app.ingestion.embedding_service import ChunkEmbedder
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
from app.repository.chunk_repository import ChunkRepository

logger = logging.getLogger(__name__)


class KnowledgeSearchService:

    def __init__(
        self,
        embedder: ChunkEmbedder,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.embedder = embedder
        self.session_factory = session_factory

    def search(
        self,
        query: str,
        source: SourceType,
        top_k: int,
        access: AccessContext,
    ) -> list[RetrievalResult]:

        if not query.strip() or top_k < 1:
            return []

        vectors = self.embedder.embed_texts([query])
        if not vectors:
            return []

        with self.session_factory() as session:
            results = ChunkRepository(session).search_by_embedding(
                embedding=vectors[0],
                source=source,
                access=access,
                top_k=top_k,
            )

        logger.info(
            "Found %d of at most %d chunk(s) in %s for team %s",
            len(results),
            top_k,
            source.value,
            access.team_id,
        )
        return results
