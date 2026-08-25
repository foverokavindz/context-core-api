from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.entities.chat.message_role import MessageRole
from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.retrieval.ontology.query_intent import QueryIntent

QUERY_ANSWERED = "ANSWERED"

# How much of a cited chunk the response carries. Enough for the frontend to
# show why a source was cited, not so much that one answer ships the corpus.
SNIPPET_CHARACTERS = 400


class CreateChatResponse(BaseModel):

    chat_session_id: UUID


class ChatHistoryMessageResponse(BaseModel):

    message_id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    updated_at: datetime


class ChatHistorySessionResponse(BaseModel):

    chat_session_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[ChatHistoryMessageResponse] = Field(default_factory=list)


class AnswerSource(BaseModel):
    """One retrieved chunk the answer was written from.

    The position in `SendQueryResponse.sources` is the number the answer cites,
    so [2] is the second entry of that list.
    """

    chunk_id: UUID

    source: SourceType
    resource_type: ResourceType | None = None
    resource_title: str | None = None
    external_id: str | None = None

    score: float | None = Field(
        default=None,
        description="The vector distance the search came back with. Shown as "
        "it was found - nothing reranks it.",
    )

    snippet: str = Field(
        description="The opening of the chunk, for the frontend to show under "
        "the citation. The retrieved text exactly as it was stored - source "
        "code, ticket text or a message - and not Markdown, so it wants a "
        "plain or preformatted block rather than a Markdown renderer.",
    )


class RetrievalStepTrace(BaseModel):
    """What one step of the plan actually searched for, and how much it found."""

    step_id: str
    source: SourceType
    goal: str
    executed_query: str
    result_count: int


class RetrievalTrace(BaseModel):
    """How the answer was arrived at.

    Here to make the pipeline visible while it is being tested by hand: it is
    the only way to see, from the frontend, that a weak answer came from a bad
    plan rather than a bad model. Expected to go behind a flag, or away, once
    the pipeline is trusted.
    """

    resolved_query: str
    intent: QueryIntent
    retrieval_required: bool

    plan_goal: str | None = None
    steps: list[RetrievalStepTrace] = Field(default_factory=list)


class SendQueryResponse(BaseModel):

    chat_session_id: UUID

    message_id: UUID = Field(description="The stored question.")
    answer_message_id: UUID = Field(description="The stored answer.")

    status: str = QUERY_ANSWERED

    answer: str = Field(
        description="The reply, written as Markdown - headings, paragraphs, "
        "bullet lists and fenced code blocks - with plain [1] and [2, 5] "
        "citation markers pointing into `sources` by position. Render it as "
        "Markdown; it is written to be read that way and shows as syntax if it "
        "is not.",
    )

    sources: list[AnswerSource] = Field(default_factory=list)

    retrieval: RetrievalTrace | None = None
