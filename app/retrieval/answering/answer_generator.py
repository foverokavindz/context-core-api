import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.core.exceptions import LLMError
from app.entities.chat.message_role import MessageRole
from app.models.chat.message import ChatMessage
from app.models.retrieval.answer import GeneratedAnswer
from app.models.retrieval.execution_result import RetrievalExecutionResult
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.answering.prompt import PROMPT

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 6
MAX_SOURCES = 24
MAX_SOURCE_CHARACTERS = 2_000

TRUNCATION_MARKER = "\n[... truncated]"

NOTHING_RETRIEVED = "No sources were retrieved for this question."


class AnswerGenerator:
    """Writes one answer from one run's results."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm

    def generate(
        self,
        analysis: PromptAnalysis,
        execution: RetrievalExecutionResult | None = None,
        history: list[ChatMessage] | None = None,
    ) -> GeneratedAnswer:
        sources = _sources(execution)

        messages = PROMPT.format_messages(
            history=_history_messages(history),
            request=[HumanMessage(content=_request(analysis, sources))],
        )

        try:
            reply = self.llm.invoke(messages)

        except Exception as exc:
            logger.error("The chat model failed: %s", type(exc).__name__)
            raise LLMError() from exc

        answer = _text(reply)
        if not answer:
            logger.error(
                "The chat model returned %s, which held no answer to send on",
                type(reply).__name__,
            )
            raise LLMError()

        logger.info(
            "Answered a question in %d characters from %d source(s)",
            len(answer),
            len(sources),
        )
        return GeneratedAnswer(answer=answer, sources=sources)


def _sources(execution: RetrievalExecutionResult | None) -> list[RetrievalResult]:
    """Every result the plan produced, once each, in plan order.

    A chunk that two steps both found is one source, kept where it was first
    seen, so the numbering the answer cites stays stable.
    """
    if execution is None:
        return []

    seen: set = set()
    sources: list[RetrievalResult] = []

    for step in execution.steps:
        for result in step.results:
            if result.chunk_id in seen:
                continue
            seen.add(result.chunk_id)
            sources.append(result)

            if len(sources) == MAX_SOURCES:
                logger.info(
                    "Showing the model the first %d retrieved chunk(s); the rest "
                    "of this run's results do not fit",
                    MAX_SOURCES,
                )
                return sources

    return sources


def _request(analysis: PromptAnalysis, sources: list[RetrievalResult]) -> str:
    """The question and its sources, as one thing to read."""

    if not sources:
        return f"Question:\n{analysis.resolved_query}\n\nSources:\n{NOTHING_RETRIEVED}"

    blocks = "\n\n".join(
        _source_block(position, source)
        for position, source in enumerate(sources, start=1)
    )
    return f"Question:\n{analysis.resolved_query}\n\nSources:\n{blocks}"


def _source_block(position: int, source: RetrievalResult) -> str:
    """One numbered source: where it came from, what it is, and what it says."""

    label = " | ".join(
        part
        for part in (
            source.source.value,
            source.resource_type.value if source.resource_type else None,
            source.external_id,
            source.resource_title,
        )
        if part
    )
    return f"[{position}] {label}\n{_shortened(source.content)}"


def _shortened(content: str) -> str:
    """The chunk, cut to what the context window can hold."""
    if len(content) <= MAX_SOURCE_CHARACTERS:
        return content
    return content[:MAX_SOURCE_CHARACTERS] + TRUNCATION_MARKER


def _history_messages(history: list[ChatMessage] | None) -> list[BaseMessage]:
    """The most recent turns, as messages the model can read."""
    if not history:
        return []

    return [
        HumanMessage(content=message.content)
        if message.role is MessageRole.USER
        else AIMessage(content=message.content)
        for message in history[-MAX_HISTORY_MESSAGES:]
    ]


def _text(reply: object) -> str:
    """The words out of a reply, whether it came back as text or as blocks."""

    content = getattr(reply, "content", None)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        # Some deployments answer in content blocks rather than a plain string.
        return "\n".join(
            block if isinstance(block, str) else str(block.get("text", ""))
            for block in content
            if isinstance(block, str) or isinstance(block, dict)
        ).strip()

    return ""
