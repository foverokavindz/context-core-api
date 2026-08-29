from typing import TYPE_CHECKING
from uuid import UUID

from pgvector.sqlalchemy import VECTOR, Vector
from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope

if TYPE_CHECKING:
    from app.entities.chat.citation import Citation
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.department import Department
    from app.entities.teams.team import Team

EMBEDDING_DIMENSIONS = 1536 # the width of text-embedding-3-small and -ada-002. Changing the model changes this number, and that is a migration either way


class Chunk(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "chunks"

    external_data_source_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    ) 
    external_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    ) 
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False) 
    chunk_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False) 
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(1536),
        nullable=True,
    ) 
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True) 
    chunk_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) 
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
    ) 
    __table_args__ = (
        ForeignKeyConstraint(
            ["external_data_source_id", "external_id"],
            ["resources.external_data_source_id", "resources.external_id"],
            name="fk_chunks_resource",
        ),
        UniqueConstraint("external_data_source_id", "external_id", "chunk_index"),
    ) 
    resource: Mapped["Resource | None"] = relationship(back_populates="chunks") 
    team: Mapped["Team | None"] = relationship(back_populates="chunks")
    department: Mapped["Department | None"] = relationship(back_populates="chunks")
    citations: Mapped[list["Citation"]] = relationship(back_populates="chunk") 