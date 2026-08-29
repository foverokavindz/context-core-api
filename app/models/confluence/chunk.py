from pydantic import computed_field

from app.models.common.permission_scope import PermissionScope

class ConfluenceChunk(PermissionScope):
    page_id: str

    space_id: str
    space_key: str
    space_name: str | None = None

    title: str

    parent_id: str | None = None
    status: str | None = None
    version_number: int | None = None

    content: str

    external_id: str
    chunk_index: int = 0 

    embedding: list[float] | None = None
    embedding_model: str | None = None

    @computed_field 
    @property
    def chunk_type(self) -> str:
        """What `chunks.chunk_type` gets. Constant while one page makes one chunk.
        """
        return "PAGE"
