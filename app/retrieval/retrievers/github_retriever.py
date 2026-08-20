"""Searches GitHub source code."""

from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult


class GitHubRetriever:
    # TODO: embed the query, search chunks scoped to GITHUB and this access
    # context, and map the rows onto RetrievalResult.
    def retrieve(
        self, query: str, top_k: int, access: AccessContext
    ) -> list[RetrievalResult]:
        return []
