from pydantic import computed_field

from app.models.common.permission_scope import PermissionScope


class SlackChunk(PermissionScope):

    channel_id: str
    message_ts: str
    author_id: str | None = None

    content: str

    external_id: str 
    chunk_index: int = 0 

    embedding: list[float] | None = None
    embedding_model: str | None = None

    @computed_field # type: ignore[prop-decorator]
    @property
    def chunk_type(self) -> str:
        """What `chunks.chunk_type` gets. One message is one chunk, always."""
        return "MESSAGE"
