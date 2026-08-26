"""Tests for the stage that turns retrieved chunks into an answer.

The model is a fake throughout, so what is asserted is the two things this
stage is actually responsible for: what the model is shown, and what is done
with what it says. Whether the answer is any good is a question about the
prompt, not about this code.

The rule the whole stage rests on is that nothing here reranks. Every result
the executor returned is shown, in the order it returned them, and the only
trimming is what a context window forces - a duplicate chunk, an over-long
chunk, and the cap. Each of those is tested for what it leaves behind as much
as for what it removes.
"""

from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.exceptions import LLMError
from app.entities.chat.message_role import MessageRole
from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.chat.message import ChatMessage
from app.models.retrieval.execution_result import (
    RetrievalExecutionResult,
    StepExecutionResult,
)
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.answering.answer_generator import (
    MAX_HISTORY_MESSAGES,
    MAX_SOURCE_CHARACTERS,
    MAX_SOURCES,
    NOTHING_RETRIEVED,
    TRUNCATION_MARKER,
    AnswerGenerator,
)

RESOLVED_QUERY = "How does authentication work in this application?"
ANSWER = "It is a JWT bearer token checked by AuthMiddleware [1]."


# ------------------------------------------------------------------- fakes


class FakeLLM:
    """Answers with fixed content, and keeps every prompt it was given."""

    def __init__(self, reply=None) -> None:
        self.reply = AIMessage(content=ANSWER) if reply is None else reply
        self.prompts: list[list] = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return self.reply


class BrokenLLM:
    """The model is unreachable, and says so in words a client must not see."""

    def invoke(self, messages):
        raise RuntimeError("api key sk-secret-value rejected by the endpoint")


def analysis(**overrides) -> PromptAnalysis:
    fields = {
        "resolved_query": RESOLVED_QUERY,
        "intent": QueryIntent.IMPLEMENTATION_UNDERSTANDING,
        "improved_query": "authentication middleware JWT implementation",
    }
    return PromptAnalysis(**{**fields, **overrides})


def result(content: str, **overrides) -> RetrievalResult:
    fields = {
        "chunk_id": uuid4(),
        "content": content,
        "score": 0.2,
        "source": SourceType.GITHUB,
        "resource_type": ResourceType.GITHUB_FILE,
        "resource_title": "src/auth/middleware.ts",
        "external_id": "src/auth/middleware.ts",
    }
    return RetrievalResult(**{**fields, **overrides})


def execution(*steps: list[RetrievalResult]) -> RetrievalExecutionResult:
    """One step per list of results, in the order given."""
    return RetrievalExecutionResult(
        steps=[
            StepExecutionResult(
                step_id=f"step_{position}",
                source=SourceType.GITHUB,
                goal="Find the authentication implementation",
                executed_query="authentication middleware JWT",
                results=results,
            )
            for position, results in enumerate(steps, start=1)
        ]
    )


def prompt_text(llm: FakeLLM) -> str:
    """What the model was asked, as one string."""
    return llm.prompts[0][-1].content


def generate(llm, execution_result=None, history=None, given_analysis=None):
    return AnswerGenerator(llm).generate(
        analysis=given_analysis or analysis(),
        execution=execution_result,
        history=history,
    )


# ------------------------------------------------------------- the answer


def test_the_answer_is_what_the_model_said() -> None:
    answer = generate(FakeLLM(), execution([result("class AuthMiddleware {}")]))

    assert answer.answer == ANSWER


def test_the_answer_is_trimmed_of_the_whitespace_around_it() -> None:
    answer = generate(FakeLLM(AIMessage(content=f"\n\n{ANSWER}\n")))

    assert answer.answer == ANSWER


def test_an_answer_returned_as_content_blocks_is_read_as_text() -> None:
    """Some deployments answer in blocks rather than a plain string."""
    reply = AIMessage(content=[{"type": "text", "text": ANSWER}])

    assert generate(FakeLLM(reply)).answer == ANSWER


def test_the_question_the_model_is_asked_is_the_resolved_one() -> None:
    """The follow-up has already been resolved into a whole question."""
    llm = FakeLLM()

    generate(llm, execution([result("class AuthMiddleware {}")]))

    assert RESOLVED_QUERY in prompt_text(llm)


def test_the_answering_instructions_come_first() -> None:
    llm = FakeLLM()

    generate(llm)

    assert isinstance(llm.prompts[0][0], SystemMessage)
    assert "cite" in llm.prompts[0][0].content.lower()


def test_the_model_is_asked_for_markdown() -> None:
    """The frontend renders the answer, so the answer has to be written for it."""
    llm = FakeLLM()

    generate(llm)

    instructions = llm.prompts[0][0].content.lower()
    assert "markdown" in instructions
    assert "fenced block" in instructions


def test_a_markdown_answer_comes_back_as_it_was_written() -> None:
    """Headings, fences and citation markers all survive untouched."""
    written = "\n".join(
        [
            "## How authentication works",
            "",
            "`AuthMiddleware` validates the token [1].",
            "",
            "```ts",
            "export const verify = (token: string) => jwt.verify(token, key)",
            "```",
            "",
            "- `src/auth/middleware.ts` - validation [1]",
            "",
        ]
    )

    answer = generate(FakeLLM(AIMessage(content=written)))

    assert answer.answer == written.strip()


# ------------------------------------------------------ what is shown, and how


def test_every_retrieved_chunk_is_shown_to_the_model() -> None:
    """Nothing reranks, so nothing is dropped for being weak."""
    llm = FakeLLM()
    results = [result("first chunk"), result("second chunk"), result("third chunk")]

    generate(llm, execution(results))

    shown = prompt_text(llm)
    assert all(chunk.content in shown for chunk in results)


def test_the_chunks_are_numbered_in_the_order_the_plan_produced_them() -> None:
    llm = FakeLLM()

    generate(llm, execution([result("earlier step")], [result("later step")]))

    shown = prompt_text(llm)
    assert shown.index("[1] ") < shown.index("earlier step")
    assert shown.index("[2] ") < shown.index("later step")
    assert shown.index("earlier step") < shown.index("later step")


def test_the_sources_returned_are_the_ones_the_numbering_points_at() -> None:
    """[2] in the answer has to mean the second source, or a citation is a lie."""
    first, second = result("earlier step"), result("later step")

    answer = generate(FakeLLM(), execution([first], [second]))

    assert [source.chunk_id for source in answer.sources] == [
        first.chunk_id,
        second.chunk_id,
    ]


def test_a_chunk_two_steps_both_found_is_shown_once_where_it_was_first_seen() -> None:
    chunk_id = uuid4()
    first = result("shared chunk", chunk_id=chunk_id)
    again = result("shared chunk", chunk_id=chunk_id)
    other = result("other chunk")
    llm = FakeLLM()

    answer = generate(llm, execution([first, other], [again]))

    assert [source.chunk_id for source in answer.sources] == [chunk_id, other.chunk_id]
    assert prompt_text(llm).count("shared chunk") == 1


def test_a_source_is_labelled_with_where_it_came_from() -> None:
    llm = FakeLLM()
    ticket = result(
        "Refresh tokens rotate on use.",
        source=SourceType.JIRA,
        resource_type=ResourceType.JIRA_ISSUE,
        resource_title="Add refresh token rotation",
        external_id="TRACK-25",
    )

    generate(llm, execution([ticket]))

    shown = prompt_text(llm)
    assert "JIRA" in shown
    assert "JIRA_ISSUE" in shown
    assert "TRACK-25" in shown
    assert "Add refresh token rotation" in shown


def test_a_source_missing_its_labels_is_still_shown() -> None:
    """A chunk whose resource was never linked is still worth reading."""
    llm = FakeLLM()
    bare = result(
        "orphaned chunk",
        resource_type=None,
        resource_title=None,
        external_id=None,
    )

    answer = generate(llm, execution([bare]))

    assert "orphaned chunk" in prompt_text(llm)
    assert len(answer.sources) == 1


def test_an_over_long_chunk_is_cut_rather_than_dropped() -> None:
    llm = FakeLLM()
    long_chunk = result("x" * (MAX_SOURCE_CHARACTERS + 500))

    answer = generate(llm, execution([long_chunk]))

    shown = prompt_text(llm)
    assert TRUNCATION_MARKER in shown
    assert "x" * (MAX_SOURCE_CHARACTERS + 1) not in shown
    # Cut for the model only - the source itself is returned whole.
    assert answer.sources[0].content == long_chunk.content


def test_a_chunk_that_fits_is_shown_untouched() -> None:
    llm = FakeLLM()

    generate(llm, execution([result("y" * MAX_SOURCE_CHARACTERS)]))

    assert TRUNCATION_MARKER not in prompt_text(llm)


def test_more_chunks_than_fit_stop_at_the_cap() -> None:
    llm = FakeLLM()
    results = [result(f"chunk {position}") for position in range(MAX_SOURCES + 5)]

    answer = generate(llm, execution(results))

    assert len(answer.sources) == MAX_SOURCES
    assert answer.sources == results[:MAX_SOURCES]


# --------------------------------------------------------- nothing retrieved


def test_a_run_that_retrieved_nothing_says_so_rather_than_pretending() -> None:
    llm = FakeLLM()

    answer = generate(llm, execution([]))

    assert NOTHING_RETRIEVED in prompt_text(llm)
    assert answer.sources == []


def test_a_run_that_never_retrieved_at_all_still_asks_the_model() -> None:
    """Thanks, or an aside: there is no search, but there is still a reply."""
    llm = FakeLLM()

    answer = generate(llm, None, given_analysis=analysis(retrieval_required=False))

    assert answer.answer == ANSWER
    assert answer.sources == []
    assert NOTHING_RETRIEVED in prompt_text(llm)


# ----------------------------------------------------------------- history


def test_the_conversation_so_far_is_given_to_the_model() -> None:
    llm = FakeLLM()
    history = [
        ChatMessage(role=MessageRole.USER, content="Explain TRACK-25."),
        ChatMessage(role=MessageRole.ASSISTANT, content="It adds token rotation."),
    ]

    generate(llm, execution([result("chunk")]), history=history)

    system, first, second, request = llm.prompts[0]
    assert isinstance(system, SystemMessage)
    assert isinstance(first, HumanMessage) and first.content == "Explain TRACK-25."
    assert isinstance(second, AIMessage) and second.content == "It adds token rotation."
    assert isinstance(request, HumanMessage)


def test_only_the_most_recent_turns_are_given() -> None:
    llm = FakeLLM()
    history = [
        ChatMessage(role=MessageRole.USER, content=f"turn {position}")
        for position in range(MAX_HISTORY_MESSAGES + 4)
    ]

    generate(llm, history=history)

    # The system message and the request bracket the history.
    given = llm.prompts[0][1:-1]
    assert len(given) == MAX_HISTORY_MESSAGES
    assert given[0].content == f"turn {len(history) - MAX_HISTORY_MESSAGES}"


def test_no_history_is_no_extra_messages() -> None:
    llm = FakeLLM()

    generate(llm, execution([result("chunk")]))

    assert len(llm.prompts[0]) == 2


# ------------------------------------------------------------ when it fails


def test_a_model_that_fails_is_reported_as_a_model_failure() -> None:
    with pytest.raises(LLMError) as caught:
        generate(BrokenLLM(), execution([result("chunk")]))

    assert caught.value.status_code == 502


def test_the_vendors_own_words_never_reach_the_client() -> None:
    with pytest.raises(LLMError) as caught:
        generate(BrokenLLM())

    assert "sk-secret-value" not in caught.value.message


@pytest.mark.parametrize(
    "reply",
    [AIMessage(content=""), AIMessage(content="   "), AIMessage(content=[]), object()],
)
def test_an_answer_with_no_words_in_it_is_a_failure(reply) -> None:
    """An empty answer is worse than an error: it looks like one."""
    with pytest.raises(LLMError):
        generate(FakeLLM(reply), execution([result("chunk")]))


def test_the_model_is_asked_exactly_once() -> None:
    llm = FakeLLM()

    generate(llm, execution([result("chunk")], [result("another")]))

    assert len(llm.prompts) == 1


def test_the_source_ids_are_the_ones_that_were_retrieved() -> None:
    """The frontend links on these, so they are passed through untouched."""
    chunk_id = uuid4()

    answer = generate(FakeLLM(), execution([result("chunk", chunk_id=chunk_id)]))

    assert isinstance(answer.sources[0].chunk_id, UUID)
    assert answer.sources[0].chunk_id == chunk_id
