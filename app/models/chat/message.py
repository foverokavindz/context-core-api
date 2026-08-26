from pydantic import BaseModel

from app.entities.chat.message_role import MessageRole


class ChatMessage(BaseModel):

    role: MessageRole
    content: str
