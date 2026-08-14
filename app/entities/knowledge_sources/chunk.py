from typing import TYPE_CHECKING
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.knowledge_sources.chunk_type import ChunkType
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope

if TYPE_CHECKING:
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.department import Department
    from app.entities.teams.team import Team

EMBEDDING_DIMENSIONS = 1536 # the width of text-embedding-3-small and -ada-002. Changing the model changes this number, and that is a migration either way


class Chunk(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "chunks"

    resource_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("resources.id"),
        nullable=False,
    ) # no index of its own - the unique constraint below leads with this column and already builds that btree

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False) # the chunk's position within its resource, so a retrieved chunk can be put back in order

    chunk_type: Mapped[ChunkType | None] = mapped_column(
        SAEnum(
            ChunkType,
            name="chunk_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
        index=True,
    ) # nullable, because a chunker that only splits text has nothing meaningful to put here and a wrong value would be worse than none

    content: Mapped[str] = mapped_column(Text, nullable=False) # the text that gets embedded, kept beside its vector so retrieval reads one row

    embedding: Mapped[list[float] | None] = mapped_column(
        JSON().with_variant(Vector(EMBEDDING_DIMENSIONS), "postgresql"),
        nullable=True,
    ) # a real vector(1536) on PostgreSQL and JSON elsewhere, the same trade config and the metadata columns make. Nullable: a chunk exists from the moment it is written and is embedded after

    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True) # which model produced the vector, so a model change can be found rather than guessed at

    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True) # room for a sha256 hex digest. Nothing here hashes anything - the ingestion service does, to skip re-embedding content that did not change

    chunk_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # chunk-specific context only - symbol_name and start_line for code, not a second copy of the resource's repository and branch

    access_scope: Mapped[ResourceAccessScope] = mapped_column(
        SAEnum(
            ResourceAccessScope,
            name="resource_access_scope",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
    )

    department_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("departments.id"),
        nullable=True,
        index=True,
    ) # these three are copies of the resource's, denormalized so a vector search can filter chunk rows before it ranks them rather than joining resources first. The resource stays the source of truth, and nothing here keeps the copy in step - the ingestion service does, see docs/todo.md

    __table_args__ = (
        UniqueConstraint("resource_id", "chunk_index"),
    ) # a resource cannot hold the same chunk index twice, so a re-ingestion that writes chunk 3 again collides instead of duplicating

    resource: Mapped["Resource"] = relationship(back_populates="chunks")
    team: Mapped["Team | None"] = relationship(back_populates="chunks")
    department: Mapped["Department | None"] = relationship(back_populates="chunks")
