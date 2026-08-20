"""Searches Jira issues, epics and stories."""

from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult


class JiraRetriever:

    # TODO: embed the query, search chunks scoped to JIRA and this access
    # context, and map the rows onto RetrievalResult.
    def retrieve(
        self, query: str, top_k: int, access: AccessContext
    ) -> list[RetrievalResult]:
        return []
