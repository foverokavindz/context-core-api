from pydantic import BaseModel

from app.models.confluence_page import ConfluencePage
from app.models.embedding_counts import EmbeddingCounts
from app.models.permission_scope import PermissionScope

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_PAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

CHUNK_CONTENT_PREVIEW_CHARS = 600

MAX_PAGES_PER_INGESTION = 500


class ConfluenceChunkSample(PermissionScope):
    """One generated chunk, with its text possibly shortened for display."""

    page_id: str
    space_key: str
    title: str
    parent_id: str | None = None
    status: str | None = None
    content: str

    embedding: list[float] | None = None
    embedding_preview: list[float] | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None


class ConfluencePageError(BaseModel):

    page: str
    reason: str


class ConfluenceIngestResponse(BaseModel):

    site_url: str

    space_key: str
    space_id: str
    space_name: str | None = None

    retrieved_pages: int
    parsed_pages: int
    generated_chunks: int

    truncated: bool = False

    counts: EmbeddingCounts

    resource_files: list[ConfluencePage] = []

    sample_chunks: list[ConfluenceChunkSample] = []
    errors: list[ConfluencePageError] = []
