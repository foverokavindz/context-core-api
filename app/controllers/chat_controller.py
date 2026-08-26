import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.chat.request import CreateChatRequest, SendQueryRequest
from app.models.chat.response import (
    ChatHistorySessionResponse,
    ConversationSummaryResponse,
    CreateChatResponse,
    SendQueryResponse,
)
from app.models.common.api_response import ApiResponse
from app.services.chat_service import ChatService
from app.services.retrieval_service import RetrievalService, get_retrieval_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chats",
    status_code=201,
    response_model=ApiResponse[CreateChatResponse],
    summary="Open a chat session",
    response_description="The id of the conversation that was opened.",
)
def create_chat(
    request: CreateChatRequest,
    session: Session = Depends(get_db),
) -> ApiResponse[CreateChatResponse]:
    """Start a conversation for a user."""
    chat = ChatService(session).create_chat(request)
    return ApiResponse[CreateChatResponse].ok(chat)


@router.get(
    "/users/{user_id}/chats",
    status_code=200,
    response_model=ApiResponse[list[ChatHistorySessionResponse]],
    summary="Get a user's complete chat history",
    response_description=(
        "Every chat session belonging to the user, newest first, with each "
        "session's complete message history in chronological order."
    ),
)
def get_chat_history(
    user_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[list[ChatHistorySessionResponse]]:
    history = ChatService(session).get_chat_history(user_id)
    return ApiResponse[list[ChatHistorySessionResponse]].ok(history)


@router.get(
    "/users/{user_id}/conversations",
    status_code=200,
    response_model=ApiResponse[list[ConversationSummaryResponse]],
    summary="List a user's conversations",
    response_description=(
        "Every chat session belonging to the user, newest first, without "
        "loading or returning the messages inside each conversation."
    ),
)
def list_conversations(
    user_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[list[ConversationSummaryResponse]]:
    conversations = ChatService(session).list_conversations(user_id)
    return ApiResponse[list[ConversationSummaryResponse]].ok(conversations)


@router.post(
    "/chats/{chat_session_id}/query",
    status_code=200,
    response_model=ApiResponse[SendQueryResponse],
    summary="Ask a question in a chat session",
    response_description=(
        "The answer, the sources it was written from, and a trace of what the "
        "retrieval pipeline did to arrive at it. The question and the answer "
        "are both stored on the session."
    ),
)
def send_query(
    chat_session_id: UUID,
    request: SendQueryRequest,
    session: Session = Depends(get_db),
    retrieval: RetrievalService = Depends(get_retrieval_service),
) -> ApiResponse[SendQueryResponse]:

    answer = ChatService(session, retrieval).send_query(chat_session_id, request)
    return ApiResponse[SendQueryResponse].ok(answer)
