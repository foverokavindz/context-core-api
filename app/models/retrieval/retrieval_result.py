from uuid import UUID

from pydantic import BaseModel, Field

from app.entities.data_sources.source_type import SourceType


class RetrievalResult(BaseModel):

    chunk_id: UUID

    content: str

    score: float | None = None

    source: SourceType

    external_data_source_id: UUID | None = None
    external_id: str | None = None

    resource_id: UUID | None = None
    resource_title: str | None = None

    chunk_metadata: dict = Field(default_factory=dict)
    resource_metadata: dict = Field(default_factory=dict)
