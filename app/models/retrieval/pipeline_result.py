"""What one run of the retrieval pipeline produced."""

from pydantic import BaseModel

from app.models.retrieval.answer import GeneratedAnswer
from app.models.retrieval.execution_result import RetrievalExecutionResult
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_plan import RetrievalPlan


class RetrievalPipelineResult(BaseModel):

    analysis: PromptAnalysis
    plan: RetrievalPlan | None = None
    execution: RetrievalExecutionResult | None = None
    answer: GeneratedAnswer | None = None
