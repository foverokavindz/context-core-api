"""Tests for the four retrievers, the stage that decides one thing.

A retriever's whole job is to name its source and hand everything else on
unchanged, so the search service is the only seam faked here - no embedding, no
database, no SQL. What is left is exactly what these classes own: that GitHub
searches GITHUB and not JIRA, that the query, top_k and access context arrive as
they were given, and that whatever comes back is what is returned.

The four are parametrized together rather than tested one at a time, so a fifth
source cannot be added with its wiring left half done.
"""

from uuid import uuid4

import pytest

from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.retrievers.base import SourceRetriever
from app.retrieval.retrievers.confluence_retriever import ConfluenceRetriever
from app.retrieval.retrievers.github_retriever import GitHubRetriever
from app.retrieval.retrievers.jira_retriever import JiraRetriever
from app.retrieval.retrievers.slack_retriever import SlackRetriever

ACCESS = AccessContext(user_id=uuid4(), team_id=uuid4(), department_id=uuid4())

QUERY = "JWT expiration refresh token rotation authentication middleware"

# The registry the executor is built from, as a table so every test covers all
# four and none can be forgotten.
RETRIEVERS = [
    (GitHubRetriever, SourceType.GITHUB),
    (JiraRetriever, SourceType.JIRA),
    (SlackRetriever, SourceType.SLACK),
    (ConfluenceRetriever, SourceType.CONFLUENCE),
]


# ------------------------------------------------------------------- fakes


class RecordingSearchService:
    """Records how it was asked and answers with a fixed list."""

    def __init__(self, results: list[RetrievalResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[dict] = []

    def search(self, query, source, top_k, access) -> list[RetrievalResult]:
        self.calls.append(
            {"query": query, "source": source, "top_k": top_k, "access": access}
        )
        return self.results


def a_result(**overrides) -> RetrievalResult:
    fields = {
        "chunk_id": uuid4(),
        "content": "export function verifyToken(token: string) {}",
        "source": SourceType.GITHUB,
    }
    return RetrievalResult(**{**fields, **overrides})


# ------------------------------------------------------------ the source


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_each_retriever_searches_its_own_source(retriever_class, source) -> None:
    search = RecordingSearchService()

    retriever_class(search).retrieve(query=QUERY, top_k=10, access=ACCESS)

    assert search.calls[0]["source"] is source


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_the_source_is_declared_on_the_class(retriever_class, source) -> None:
    """So the registry and the search agree without running a search."""
    assert retriever_class.source is source


def test_the_four_retrievers_cover_every_source() -> None:
    """A new SourceType without a retriever should fail here, not in production."""
    assert {source for _, source in RETRIEVERS} == set(SourceType)


# -------------------------------------------------------------- pass-through


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_the_query_top_k_and_access_arrive_unchanged(
    retriever_class, source
) -> None:
    search = RecordingSearchService()

    retriever_class(search).retrieve(query=QUERY, top_k=3, access=ACCESS)

    call = search.calls[0]
    assert call["query"] == QUERY
    assert call["top_k"] == 3
    assert call["access"] is ACCESS


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_a_retriever_searches_once_per_call(retriever_class, source) -> None:
    search = RecordingSearchService()

    retriever_class(search).retrieve(query=QUERY, top_k=10, access=ACCESS)

    assert len(search.calls) == 1


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_what_the_search_finds_is_what_the_retriever_returns(
    retriever_class, source
) -> None:
    found = [a_result(), a_result()]
    search = RecordingSearchService(found)

    results = retriever_class(search).retrieve(
        query=QUERY, top_k=10, access=ACCESS
    )

    assert results == found


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_finding_nothing_is_an_empty_list_and_not_a_failure(
    retriever_class, source
) -> None:
    results = retriever_class(RecordingSearchService([])).retrieve(
        query=QUERY, top_k=10, access=ACCESS
    )

    assert results == []


# ---------------------------------------------------------------- the shape


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_every_retriever_satisfies_the_protocol(retriever_class, source) -> None:
    """The executor holds these as SourceRetriever, so the shape has to match."""
    retriever: SourceRetriever = retriever_class(RecordingSearchService())

    assert callable(retriever.retrieve)


@pytest.mark.parametrize(("retriever_class", "source"), RETRIEVERS)
def test_a_retriever_accepts_the_keywords_the_executor_calls_it_with(
    retriever_class, source
) -> None:
    """`_execute_step` calls retrieve(query=, top_k=, access=) by keyword."""
    search = RecordingSearchService()

    retriever_class(search).retrieve(query=QUERY, top_k=10, access=ACCESS)

    assert search.calls
