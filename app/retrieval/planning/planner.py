
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.core.exceptions import LLMError
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_plan import RetrievalPlan
from app.retrieval.planning.prompt import PROMPT
from app.retrieval.planning.validator import repair_plan

logger = logging.getLogger(__name__)


class RetrievalPlanner:
    """Decides what to retrieve."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm.with_structured_output(RetrievalPlan)

    def plan(self, analysis: PromptAnalysis) -> RetrievalPlan:
        messages = PROMPT.format_messages(
            analysis=[HumanMessage(content=analysis.model_dump_json(indent=2))],
        )

        try:
            plan = self.llm.invoke(messages)

        except Exception as exc:
            logger.error("The chat model failed: %s", type(exc).__name__)
            raise LLMError() from exc

        if not isinstance(plan, RetrievalPlan):
            logger.error(
                "The chat model returned %s rather than a RetrievalPlan",
                type(plan).__name__,
            )
            raise LLMError()

        return repair_plan(plan)
