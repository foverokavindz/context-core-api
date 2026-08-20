"""What the planner decided to retrieve, and in what order."""

from pydantic import BaseModel, ConfigDict, Field

from app.entities.data_sources.source_type import SourceType

DEFAULT_TOP_K = 10
MIN_TOP_K = 5
MAX_TOP_K = 15


class RetrievalStep(BaseModel):

    id: str = Field(
        description="A short snake_case name for this step, unique within the "
        "plan. Other steps refer to it by this.",
        examples=["authentication_history", "redis_decision"],
    )

    source: SourceType = Field(
        description="Where to search. The source whose content best answers "
        "this step's goal.",
    )

    goal: str = Field(
        description="What this one search is for, in a single line.",
        examples=["Find previous authentication-related work"],
    )

    query: str | None = Field(
        default=None,
        description="A concise semantic query, when enough is already known to "
        "write one. Null when a step this one depends on has to run first.",
        examples=["authentication previous changes requirements"],
    )

    top_k: int = Field(
        default=DEFAULT_TOP_K,
        description=f"How many results this step should return. Between "
        f"{MIN_TOP_K} and {MAX_TOP_K}; {DEFAULT_TOP_K} unless the goal is "
        f"unusually narrow or unusually broad.",
    )

    depends_on: list[str] = Field(
        default_factory=list,
        description="The ids of steps whose results would materially improve "
        "this one's query. Empty whenever this step can be searched directly.",
    )


class RetrievalPlan(BaseModel):

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(
        description="What the whole plan is trying to establish, in one line.",
        examples=[
            "Understand previous authentication work, implementation and architecture."
        ],
    )

    steps: list[RetrievalStep] = Field(
        default_factory=list,
        description="The smallest set of searches that answers the goal. "
        "Independent wherever a step does not genuinely need another's results.",
    )
