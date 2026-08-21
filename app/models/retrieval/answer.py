"""The answer one run of the pipeline produced, and what it rested on."""

from pydantic import BaseModel, Field

from app.models.retrieval.retrieval_result import RetrievalResult


class GeneratedAnswer(BaseModel):

    answer: str = Field(
        description="The reply, in the person's own language, written from the "
        "retrieved sources alone. Markdown - headings, paragraphs, bullets and "
        "fenced code blocks - because that is how the frontend renders it.",
    )

    sources: list[RetrievalResult] = Field(
        default_factory=list,
        description="The chunks the model was shown, in the order it was shown "
        "them - so the [1] and [2] in the answer point at this list by "
        "position. Chosen by the executor, not by the model: nothing is "
        "reranked and nothing is dropped after the fact.",
    )
