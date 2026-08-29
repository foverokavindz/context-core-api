
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.core.exceptions import EmptyQueryError, LLMError
from app.entities.chat.message_role import MessageRole
from app.models.chat.message import ChatMessage
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.retrieval.prompt_processing.prompt import PROMPT

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 6


class PromptProcessor:
    """Understands one question."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm.with_structured_output(PromptAnalysis)

    def process(
        self, query: str, history: list[ChatMessage] | None = None
    ) -> PromptAnalysis:
        query = query.strip()
        if not query:
            raise EmptyQueryError()

        messages = PROMPT.format_messages(
            history=_history_messages(history),
            query=[HumanMessage(content=query)],
        )

        try:
            analysis = self.llm.invoke(messages)

            logger.debug(
                "The chat model returned %s for a %d-character query",
                type(analysis).__name__,
                len(query),
            )

        except Exception as exc:
            
            logger.error("The chat model failed: %s", type(exc).__name__)
            raise LLMError() from exc

        if not isinstance(analysis, PromptAnalysis):
            logger.error(
                "The chat model returned %s rather than a PromptAnalysis",
                type(analysis).__name__,
            )
            raise LLMError()

        logger.info(
            "Understood a %d-character query as %s, needing %s",
            len(query),
            analysis.intent.value,
            ", ".join(need.value for need in analysis.information_needs) or "nothing",
        )

        #TODO : Need analysis validator
        return analysis


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
