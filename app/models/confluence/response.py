from pydantic import BaseModel

from app.models.common.embedding_counts import EmbeddingCounts
from app.models.confluence.chunk import ConfluenceChunk
from app.models.confluence.page import ConfluencePage

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_PAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_PAGES_PER_INGESTION = 500


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

    chunks: list[ConfluenceChunk] = []
    errors: list[ConfluencePageError] = []
