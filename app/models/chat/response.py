from uuid import UUID

from pydantic import BaseModel

QUERY_RECEIVED = "RECEIVED"


class CreateChatResponse(BaseModel):

    chat_session_id: UUID


class SendQueryResponse(BaseModel):

    chat_session_id: UUID
    message_id: UUID
    status: str = QUERY_RECEIVED
