"""Tests for the query-understanding stage.

The model is faked at the one seam that matters: `with_structured_output`. That
is the whole of this component's contract with LangChain, so faking it there
leaves the real prompt, the real history conversion and the real error handling
running, and needs no network and no credentials.

What is asserted is deliberately split in two. The processor does not decide what
an analysis says - the model does - so tests about intents and entities check
that a model's answer survives the round trip intact. The tests worth more are
the ones about what is *sent*: the message order, the trimming, and the fact that
a person's words reach the model unaltered and reach the log not at all.
"""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.exceptions import EmptyQueryError, LLMError
from app.entities.chat.message_role import MessageRole
from app.entities.data_sources.source_type import SourceType
from app.models.chat.message import ChatMessage
from app.models.retrieval.ontology.entity_type import EntityType
from app.models.retrieval.ontology.information_need import InformationNeed
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.prompt_analysis import ExtractedEntity, PromptAnalysis
from app.retrieval.prompt_processing.processor import (
    MAX_HISTORY_MESSAGES,
    PromptProcessor,
)

BROAD_QUERY = (
    "To improve authentication I need to understand previous tickets, "
    "implementations and architecture."
)


# ------------------------------------------------------------------- fakes


class FakeStructuredModel:
    """What `with_structured_output` hands back: something with `invoke`.

    Records the messages it was called with, so a test can assert on the prompt
    that was actually built rather than on the processor's intentions.
    """

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list] = []

    def invoke(self, messages: list) -> object:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeChatModel:
    """A chat model that never leaves the process."""

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.structured = FakeStructuredModel(answer, error)
        self.schema: type | None = None

    def with_structured_output(self, schema: type) -> FakeStructuredModel:
        self.schema = schema
        return self.structured


def analysis(**overrides) -> PromptAnalysis:
    """A valid analysis, with any field replaced."""
    fields = {
        "resolved_query": "Understand the previous authentication work.",
        "intent": QueryIntent.BROAD_CONTEXT,
        "entities": [ExtractedEntity(type=EntityType.FEATURE, value="authentication")],
        "information_needs": [
            InformationNeed.PREVIOUS_WORK,
            InformationNeed.IMPLEMENTATION,
            InformationNeed.ARCHITECTURE,
        ],
        "improved_query": "authentication previous changes implementation architecture",
    }
    return PromptAnalysis(**{**fields, **overrides})


def processor_for(answer: object = None, error: Exception | None = None):
    """A processor over a fake model, plus the fake, for asserting on both."""
    model = FakeChatModel(answer, error)
    return PromptProcessor(model), model


# ------------------------------------------------------- what comes back out


def test_the_analysis_schema_is_what_the_model_is_asked_for() -> None:
    _, model = processor_for(analysis())
    assert model.schema is PromptAnalysis


def test_a_broad_request_keeps_its_intent_entities_and_information_needs() -> None:
    processor, _ = processor_for(analysis())

    result = processor.process(query=BROAD_QUERY)

    assert result.intent is QueryIntent.BROAD_CONTEXT
    assert result.entities == [
        ExtractedEntity(type=EntityType.FEATURE, value="authentication")
    ]
    assert result.information_needs == [
        InformationNeed.PREVIOUS_WORK,
        InformationNeed.IMPLEMENTATION,
        InformationNeed.ARCHITECTURE,
    ]


def test_a_broad_request_names_no_source_of_its_own() -> None:
    """Choosing where to look is the Planner's job, and this is where that starts."""
    processor, _ = processor_for(analysis())

    assert processor.process(query=BROAD_QUERY).explicit_sources == []


def test_an_explicitly_named_source_is_carried_through() -> None:
    processor, _ = processor_for(
        analysis(explicit_sources=[SourceType.JIRA], intent=QueryIntent.CHANGE_HISTORY)
    )

    result = processor.process(query="Search Jira for authentication tickets.")

    assert result.explicit_sources == [SourceType.JIRA]


def test_a_conversation_only_request_does_not_require_retrieval() -> None:
    processor, _ = processor_for(
        analysis(
            intent=QueryIntent.GENERAL_QUESTION,
            retrieval_required=False,
            entities=[],
            information_needs=[],
        )
    )

    assert processor.process(query="Thanks!").retrieval_required is False


# ------------------------------------------------------------- what goes in


def test_the_system_prompt_comes_first_and_the_question_last() -> None:
    processor, model = processor_for(analysis())

    processor.process(query=BROAD_QUERY)

    sent = model.structured.calls[0]
    assert isinstance(sent[0], SystemMessage)
    assert "query-understanding component" in sent[0].content
    assert isinstance(sent[-1], HumanMessage)
    assert sent[-1].content == BROAD_QUERY


def test_a_question_containing_braces_is_asked_not_treated_as_a_template() -> None:
    """The prompt is a template; the person's words must never be one."""
    processor, model = processor_for(analysis())
    query = "What does {user_id} mean in the auth middleware?"

    processor.process(query=query)

    assert model.structured.calls[0][-1].content == query


def test_history_reaches_the_model_in_order_and_in_the_right_roles() -> None:
    processor, model = processor_for(
        analysis(
            resolved_query="What tests are related to the implementation of TRACK-25?",
            intent=QueryIntent.IMPLEMENTATION_UNDERSTANDING,
            entities=[ExtractedEntity(type=EntityType.JIRA_TICKET, value="TRACK-25")],
            information_needs=[InformationNeed.TESTS, InformationNeed.IMPLEMENTATION],
        )
    )
    history = [
        ChatMessage(role=MessageRole.USER, content="Explain TRACK-25 implementation."),
        ChatMessage(role=MessageRole.ASSISTANT, content="TRACK-25 adds a token cache."),
    ]

    result = processor.process(query="What about the tests?", history=history)

    sent = model.structured.calls[0]
    assert [type(message) for message in sent] == [
        SystemMessage,
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert sent[1].content == "Explain TRACK-25 implementation."
    assert sent[2].content == "TRACK-25 adds a token cache."
    assert "TRACK-25" in result.resolved_query


def test_only_the_most_recent_turns_travel_with_the_question() -> None:
    processor, model = processor_for(analysis())
    history = [
        ChatMessage(role=MessageRole.USER, content=f"turn {index}")
        for index in range(MAX_HISTORY_MESSAGES + 4)
    ]

    processor.process(query=BROAD_QUERY, history=history)

    # The system prompt, the trimmed history, and the question.
    sent = model.structured.calls[0]
    assert len(sent) == MAX_HISTORY_MESSAGES + 2
    assert sent[1].content == "turn 4"
    assert sent[-2].content == f"turn {MAX_HISTORY_MESSAGES + 3}"


@pytest.mark.parametrize("history", [None, []])
def test_a_question_without_history_is_asked_on_its_own(history) -> None:
    processor, model = processor_for(analysis())

    processor.process(query=BROAD_QUERY, history=history)

    sent = model.structured.calls[0]
    assert [type(message) for message in sent] == [SystemMessage, HumanMessage]


def test_the_query_is_stripped_before_it_is_sent() -> None:
    processor, model = processor_for(analysis())

    processor.process(query=f"  {BROAD_QUERY}\n")

    assert model.structured.calls[0][-1].content == BROAD_QUERY


# --------------------------------------------------------- what goes wrong


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_an_empty_query_is_rejected_without_calling_the_model(query: str) -> None:
    processor, model = processor_for(analysis())

    with pytest.raises(EmptyQueryError) as caught:
        processor.process(query=query)

    assert caught.value.status_code == 400
    assert model.structured.calls == []


def test_a_failing_model_does_not_leak_what_it_said() -> None:
    processor, _ = processor_for(error=RuntimeError("api key sk-secret is invalid"))

    with pytest.raises(LLMError) as caught:
        processor.process(query=BROAD_QUERY)

    assert caught.value.status_code == 502
    assert "sk-secret" not in caught.value.message


def test_a_failing_model_does_not_leak_what_it_said_into_the_log(caplog) -> None:
    processor, _ = processor_for(error=RuntimeError("api key sk-secret is invalid"))

    with caplog.at_level(logging.ERROR), pytest.raises(LLMError):
        processor.process(query=BROAD_QUERY)

    assert "RuntimeError" in caplog.text
    assert "sk-secret" not in caplog.text


@pytest.mark.parametrize("answer", [None, "BROAD_CONTEXT", {"intent": "BROAD_CONTEXT"}])
def test_an_answer_that_is_not_an_analysis_fails_rather_than_being_guessed_at(
    answer,
) -> None:
    processor, _ = processor_for(answer)

    with pytest.raises(LLMError):
        processor.process(query=BROAD_QUERY)


def test_the_question_itself_is_never_logged(caplog) -> None:
    """The rule chat_service.py follows for a query: its length, not its text."""
    processor, _ = processor_for(analysis())

    with caplog.at_level(logging.DEBUG):
        processor.process(query=BROAD_QUERY)

    assert BROAD_QUERY not in caplog.text
    assert str(len(BROAD_QUERY)) in caplog.text
