"""What the query enricher is given, and the one thing it answers with."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.retrieval.retrieval_result import RetrievalResult


class QueryEnrichmentInput(BaseModel):

    original_query: str
    base_query: str | None = None

    next_step_goal: str

    dependency_results: list[RetrievalResult] = Field(default_factory=list)


class EnrichedQuery(BaseModel):

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        description="A concise semantic query for the next retrieval, drawn "
        "only from the terms the dependency results and the goal actually "
        "contain. A search query, never a sentence and never an answer.",
        examples=[
            "JWT expiration refresh token rotation AuthMiddleware token "
            "validation authentication implementation"
        ],
    )
