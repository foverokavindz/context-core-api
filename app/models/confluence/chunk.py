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

    external_id: str # = page_id, the same value the page carries
    chunk_index: int = 0 # one page makes one chunk today; a chunker that splits a long page would number them here

    embedding: list[float] | None = None
    embedding_model: str | None = None
