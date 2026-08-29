
from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.search.knowledge_search_service import KnowledgeSearchService


class JiraRetriever:

    source = SourceType.JIRA

    def __init__(self, search_service: KnowledgeSearchService) -> None:
        self.search_service = search_service

    def retrieve(
        self, query: str, top_k: int, access: AccessContext
    ) -> list[RetrievalResult]:
        return self.search_service.search(
            query=query, source=self.source, top_k=top_k, access=access
        )
