"""Tests for the pipeline, the service over it, and the analysis they carry.

Three small things, and the tests are about responsibilities rather than
implementation: that each layer hands the whole question on and hands the whole
answer back, that the analysis has the defaults the Planner will rely on, and
that none of it needs credentials until something actually calls a model.
"""

import pytest

from app.core.exceptions import LLMConfigurationError
from app.entities.chat.message_role import MessageRole
from app.models.chat.message import ChatMessage
from app.models.retrieval.ontology.entity_type import EntityType
from app.models.retrieval.ontology.information_need import InformationNeed
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.prompt_analysis import ExtractedEntity, PromptAnalysis
from app.retrieval.llm import build_chat_model
from app.retrieval.pipeline import RetrievalPipeline
from app.services.retrieval_service import RetrievalService

QUERY = (
    "To improve authentication I need to understand previous tickets, "
    "implementations and architecture."
)

HISTORY = [ChatMessage(role=MessageRole.USER, content="Explain TRACK-25.")]

CHAT_MODEL_VARIABLES = (
    "AZURE_OPENAI_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
)


# ------------------------------------------------------------------- fakes


class FakePromptProcessor:
    """Records the question it was asked and answers with a fixed analysis."""

    def __init__(self, answer: PromptAnalysis) -> None:
        self.answer = answer
        self.calls: list[tuple] = []

    def process(self, query: str, history=None) -> PromptAnalysis:
        self.calls.append((query, history))
        return self.answer


def analysis() -> PromptAnalysis:
    return PromptAnalysis(
        resolved_query="Understand the previous authentication work.",
        intent=QueryIntent.BROAD_CONTEXT,
        entities=[ExtractedEntity(type=EntityType.FEATURE, value="authentication")],
        information_needs=[
            InformationNeed.PREVIOUS_WORK,
            InformationNeed.IMPLEMENTATION,
            InformationNeed.ARCHITECTURE,
        ],
        improved_query="authentication previous changes implementation architecture",
    )


# ---------------------------------------------------------------- pipeline


def test_the_pipeline_asks_the_prompt_processor_and_returns_what_it_says() -> None:
    expected = analysis()
    processor = FakePromptProcessor(expected)

    result = RetrievalPipeline(processor).run(query=QUERY, history=HISTORY)

    assert result is expected
    assert processor.calls == [(QUERY, HISTORY)]


def test_the_pipeline_passes_a_missing_history_on_as_it_found_it() -> None:
    processor = FakePromptProcessor(analysis())

    RetrievalPipeline(processor).run(query=QUERY)

    assert processor.calls == [(QUERY, None)]


# ----------------------------------------------------------------- service


def test_the_service_starts_the_pipeline_and_returns_its_analysis() -> None:
    expected = analysis()
    processor = FakePromptProcessor(expected)
    service = RetrievalService(RetrievalPipeline(processor))

    result = service.start(query=QUERY, history=HISTORY)

    assert result is expected
    assert processor.calls == [(QUERY, HISTORY)]


def test_the_service_does_not_require_a_history() -> None:
    processor = FakePromptProcessor(analysis())

    RetrievalService(RetrievalPipeline(processor)).start(query=QUERY)

    assert processor.calls == [(QUERY, None)]


# ------------------------------------------------------- the analysis itself


def test_an_analysis_names_no_source_and_expects_retrieval_by_default() -> None:
    """The two defaults the Planner reads. Neither is decided here."""
    result = PromptAnalysis(
        resolved_query="What is this?",
        intent=QueryIntent.GENERAL_QUESTION,
        improved_query="this",
    )

    assert result.explicit_sources == []
    assert result.retrieval_required is True
    assert result.entities == []
    assert result.information_needs == []


def test_an_analysis_serializes_to_the_shape_the_planner_will_read() -> None:
    assert analysis().model_dump(mode="json") == {
        "resolved_query": "Understand the previous authentication work.",
        "intent": "BROAD_CONTEXT",
        "entities": [{"type": "FEATURE", "value": "authentication"}],
        "information_needs": ["PREVIOUS_WORK", "IMPLEMENTATION", "ARCHITECTURE"],
        "improved_query": (
            "authentication previous changes implementation architecture"
        ),
        "explicit_sources": [],
        "retrieval_required": True,
    }


# ----------------------------------------------------------- configuration


@pytest.mark.parametrize("missing", CHAT_MODEL_VARIABLES)
def test_a_chat_model_missing_any_variable_says_so_rather_than_being_built(
    monkeypatch, missing: str
) -> None:
    for name in CHAT_MODEL_VARIABLES:
        monkeypatch.setenv(name, "set")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(LLMConfigurationError) as caught:
        build_chat_model()

    assert caught.value.status_code == 500
    assert missing in caught.value.message


def test_the_configuration_error_names_variables_and_never_their_values(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-secret")
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT", raising=False)

    with pytest.raises(LLMConfigurationError) as caught:
        build_chat_model()

    assert "sk-secret" not in caught.value.message
    assert "example.openai.azure.com" not in caught.value.message


def test_the_chat_deployment_is_read_separately_from_the_embedding_one(
    monkeypatch,
) -> None:
    """The two name different models on the same resource."""
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "text-embedding-3-small")
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT", raising=False)

    with pytest.raises(LLMConfigurationError):
        build_chat_model()
