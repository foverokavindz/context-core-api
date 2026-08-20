"""Searches Slack channel history."""

from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult


class SlackRetriever:
    # TODO: embed the query, search chunks scoped to SLACK and this access
    # context, and map the rows onto RetrievalResult.
    def retrieve(
        self, query: str, top_k: int, access: AccessContext
    ) -> list[RetrievalResult]:
        return []
