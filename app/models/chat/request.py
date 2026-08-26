from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.chat.llm_config import LLMConfig

MAX_QUERY_LENGTH = 4_000
MAX_TITLE_LENGTH = 255 

# Both strip before they measure, so a value of nothing but whitespace is
# rejected rather than stored as an empty string.
ChatTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH
    ),
]
ChatQuery = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_QUERY_LENGTH
    ),
]


class CreateChatRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    user_id: UUID = Field(
        description="Whose conversation this is. Supplied by the caller for "
        "now - it becomes the authenticated identity once there is one.",
    )

    title: ChatTitle | None = Field(
        default=None,
        description="What to call the conversation. Left unset until something "
        "names it.",
        examples=["Authentication investigation"],
    )


class SendQueryRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    query: ChatQuery = Field(
        description="The question, as the person typed it.",
        examples=["How does authentication work in this application?"],
    )

    user_id: UUID = Field(
        description="Who is asking. Must own the chat session in the URL path.",
    )

    team_id: UUID
    department_id: UUID

    llm_config: LLMConfig = Field(default_factory=LLMConfig)
