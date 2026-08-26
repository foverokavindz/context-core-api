"""Tests for the search service, the stage that coordinates and decides little.

Two seams are faked and nothing else is: the embedder, so no Azure deployment is
called, and the session factory, so no database is opened. What is left is the
only thing this stage owns - that the query is embedded once, that *that* vector
is what the repository is asked with, that the session is opened and closed
around exactly one search, and that the three ways of having nothing to search
for cost neither an embedding call nor a connection.

The repository is reached by patching the name this module imported rather than
by injecting one, because the service deliberately builds its repository around
a session it owns; injecting one would test a seam that does not exist.
"""

from uuid import uuid4

import pytest

from app.entities.data_sources.source_type import SourceType
from app.ingestion.embedding_service import EMBEDDING_DIMENSIONS
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.search import knowledge_search_service as module
from app.retrieval.search.knowledge_search_service import KnowledgeSearchService

ACCESS = AccessContext(user_id=uuid4(), team_id=uuid4(), department_id=uuid4())

QUERY = "JWT expiration refresh token rotation authentication middleware"


# ------------------------------------------------------------------- fakes


def _vector_for(text: str) -> list[float]:
    """A deterministic vector that identifies the text that produced it.

    The same trick test_embedding_service.py uses: the first value fingerprints
    the input, which is what lets a test assert the repository was asked with
    *this query's* vector rather than merely with some vector.
    """
    return [float(len(text))] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


class RecordingEmbedder:
    """Stands in for ChunkEmbedder, recording every text it is sent."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.vectors is not None:
            return self.vectors
        return [_vector_for(text) for text in texts]


class FakeSession:
    """A session that records rather than connects."""

    def __init__(self) -> None:
        self.closed = 0
        self.commits = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed += 1


class RecordingSessionFactory:
    """Hands out fake sessions and keeps every one it made."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


class RecordingChunkRepository:
    """Stands in for the real repository, recording how it was searched."""

    calls: list[dict] = []
    results: list[RetrievalResult] = []
    raises: Exception | None = None

    def __init__(self, session) -> None:
        self.session = session

    def search_by_embedding(self, embedding, source, access, top_k):
        type(self).calls.append(
            {
                "embedding": embedding,
                "source": source,
                "access": access,
                "top_k": top_k,
                "session": self.session,
            }
        )
        if type(self).raises is not None:
            raise type(self).raises
        return type(self).results


@pytest.fixture
def repository(monkeypatch) -> type[RecordingChunkRepository]:
    """The repository the service builds, replaced for the length of one test."""
    RecordingChunkRepository.calls = []
    RecordingChunkRepository.results = []
    RecordingChunkRepository.raises = None
    monkeypatch.setattr(module, "ChunkRepository", RecordingChunkRepository)
    return RecordingChunkRepository


def a_service(
    embedder: RecordingEmbedder | None = None,
) -> tuple[KnowledgeSearchService, RecordingEmbedder, RecordingSessionFactory]:
    """A service over fakes, plus both, for asserting on all three."""
    embedder = embedder or RecordingEmbedder()
    factory = RecordingSessionFactory()
    return KnowledgeSearchService(embedder, factory), embedder, factory


def a_result(**overrides) -> RetrievalResult:
    fields = {
        "chunk_id": uuid4(),
        "content": "export function verifyToken(token: string) {}",
        "source": SourceType.GITHUB,
    }
    return RetrievalResult(**{**fields, **overrides})


# --------------------------------------------------------------- embedding


def test_the_query_is_embedded_once(repository) -> None:
    service, embedder, _ = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)

    assert embedder.calls == [[QUERY]]


def test_the_repository_is_asked_with_this_querys_vector(repository) -> None:
    service, _, _ = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)

    assert repository.calls[0]["embedding"] == _vector_for(QUERY)


def test_a_different_query_produces_a_different_vector(repository) -> None:
    service, _, _ = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)
    service.search(query="redis", source=SourceType.GITHUB, top_k=10, access=ACCESS)

    assert repository.calls[0]["embedding"] != repository.calls[1]["embedding"]


def test_no_new_embedding_provider_is_built() -> None:
    """The service takes an embedder; it never constructs its own client."""
    service, embedder, _ = a_service()

    assert service.embedder is embedder


# ------------------------------------------------------------ pass-through


@pytest.mark.parametrize("source", list(SourceType))
def test_the_source_reaches_the_repository(repository, source) -> None:
    service, _, _ = a_service()

    service.search(query=QUERY, source=source, top_k=10, access=ACCESS)

    assert repository.calls[0]["source"] is source


def test_the_access_context_and_top_k_reach_the_repository(repository) -> None:
    service, _, _ = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=4, access=ACCESS)

    assert repository.calls[0]["access"] is ACCESS
    assert repository.calls[0]["top_k"] == 4


def test_what_the_repository_finds_is_what_is_returned(repository) -> None:
    repository.results = [a_result(), a_result()]
    service, _, _ = a_service()

    results = service.search(
        query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
    )

    assert results == repository.results


# ------------------------------------------------------------- the session


def test_one_search_opens_and_closes_one_session(repository) -> None:
    """A session per search, because the executor searches on several threads."""
    service, _, factory = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)

    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed == 1


def test_two_searches_never_share_a_session(repository) -> None:
    service, _, factory = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)
    service.search(query=QUERY, source=SourceType.JIRA, top_k=10, access=ACCESS)

    first, second = factory.sessions
    assert first is not second


def test_the_session_is_closed_even_when_the_search_fails(repository) -> None:
    repository.raises = RuntimeError("connection lost")
    service, _, factory = a_service()

    with pytest.raises(RuntimeError):
        service.search(
            query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
        )

    assert factory.sessions[0].closed == 1


def test_searching_never_commits(repository) -> None:
    """Retrieval is read-only, and repositories do not commit."""
    service, _, factory = a_service()

    service.search(query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS)

    assert factory.sessions[0].commits == 0


# ----------------------------------------------------------- nothing to do


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_a_blank_query_finds_nothing_without_embedding_it(
    repository, query
) -> None:
    service, embedder, factory = a_service()

    results = service.search(
        query=query, source=SourceType.GITHUB, top_k=10, access=ACCESS
    )

    assert results == []
    assert embedder.calls == []
    assert factory.sessions == []


@pytest.mark.parametrize("top_k", [0, -1])
def test_asking_for_no_results_costs_nothing(repository, top_k) -> None:
    service, embedder, factory = a_service()

    results = service.search(
        query=QUERY, source=SourceType.GITHUB, top_k=top_k, access=ACCESS
    )

    assert results == []
    assert embedder.calls == []
    assert factory.sessions == []


def test_an_embedder_that_returns_nothing_opens_no_session(repository) -> None:
    service, _, factory = a_service(RecordingEmbedder(vectors=[]))

    results = service.search(
        query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
    )

    assert results == []
    assert factory.sessions == []
    assert repository.calls == []


def test_finding_nothing_is_an_empty_list_and_not_a_failure(repository) -> None:
    repository.results = []
    service, _, _ = a_service()

    assert (
        service.search(
            query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
        )
        == []
    )


# --------------------------------------------------------- what is logged


def test_the_log_says_what_was_searched_without_saying_what_was_asked(
    repository, caplog
) -> None:
    """A query is somebody's question; the count and the source are not."""
    repository.results = [a_result()]
    service, _, _ = a_service()

    with caplog.at_level("INFO"):
        service.search(
            query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
        )

    assert "GITHUB" in caplog.text
    assert QUERY not in caplog.text


def test_the_log_never_carries_a_vector(repository, caplog) -> None:
    repository.results = [a_result()]
    service, _, _ = a_service()

    with caplog.at_level("DEBUG"):
        service.search(
            query=QUERY, source=SourceType.GITHUB, top_k=10, access=ACCESS
        )

    assert str(_vector_for(QUERY)[0]) not in caplog.text
