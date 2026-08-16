from app.models.common.permission_scope import PermissionScope


class SlackChunk(PermissionScope):

    channel_id: str
    message_ts: str
    author_id: str | None = None

    content: str

    external_id: str # = "channel_id:message_ts", the same value the message carries
    chunk_index: int = 0 # one message makes one chunk, so this is always the first

    embedding: list[float] | None = None
    embedding_model: str | None = None
