"""The one shape every source retriever has."""

from typing import Protocol

from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult


class SourceRetriever(Protocol):

    def retrieve(
        self, query: str, top_k: int, access: AccessContext
    ) -> list[RetrievalResult]:
        ...
