"""What one run of the retrieval pipeline produced."""

from pydantic import BaseModel

from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_plan import RetrievalPlan


class RetrievalPipelineResult(BaseModel):

    analysis: PromptAnalysis
    plan: RetrievalPlan | None = None
