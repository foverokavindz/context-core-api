import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.chat.request import CreateChatRequest, SendQueryRequest
from app.models.chat.response import CreateChatResponse, SendQueryResponse
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chats",
    status_code=201,
    response_model=CreateChatResponse,
    summary="Open a chat session",
    response_description="The id of the conversation that was opened.",
)
def create_chat(
    request: CreateChatRequest,
    session: Session = Depends(get_db),
) -> CreateChatResponse:
    """Start a conversation for a user."""
    return ChatService(session).create_chat(request)


@router.post(
    "/chats/{chat_session_id}/query",
    status_code=202,
    response_model=SendQueryResponse,
    summary="Send a query to a chat session",
    response_description=(
        "Confirmation that the question was stored. No answer is generated - "
        "there is no retrieval pipeline behind this yet."
    ),
)
def send_query(
    chat_session_id: UUID,
    request: SendQueryRequest,
    session: Session = Depends(get_db),
) -> SendQueryResponse:
    """Record a user's question against one of their chat sessions."""
    return ChatService(session).send_query(chat_session_id, request)
