from pydantic import BaseModel, Field

from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.retrieval_result import RetrievalResult


class StepExecutionResult(BaseModel):

    step_id: str
    source: SourceType
    goal: str

    executed_query: str

    results: list[RetrievalResult] = Field(default_factory=list)


class RetrievalExecutionResult(BaseModel):

    steps: list[StepExecutionResult] = Field(default_factory=list)
