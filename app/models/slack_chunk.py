from app.models.permission_scope import PermissionScope


class SlackChunk(PermissionScope):

    channel_id: str
    message_ts: str
    author_id: str | None = None

    content: str

    embedding: list[float] | None = None
    embedding_model: str | None = None
