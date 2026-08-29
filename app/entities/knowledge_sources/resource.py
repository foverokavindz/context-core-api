from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.entities.knowledge_sources.resource_type import ResourceType

if TYPE_CHECKING:
    from app.entities.chat.citation import Citation
    from app.entities.chunks.chunk import Chunk
    from app.entities.data_sources.external_data_source import ExternalDataSource
    from app.entities.documents.document import Document
    from app.entities.organization.department import Department
    from app.entities.teams.team import Team


class Resource(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "resources"

    external_data_source_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("external_data_sources.id"),
        nullable=True,
    ) 
    document_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("documents.id"),
        nullable=True,
        unique=True,
        index=True,
    ) 
    resource_type: Mapped[ResourceType] = mapped_column(
        SAEnum(
            ResourceType,
            name="resource_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    ) 
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    version_key: Mapped[str | None] = mapped_column(String(255), nullable=True) 
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
    resource_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) 
    __table_args__ = (
        UniqueConstraint("external_data_source_id", "external_id"),
        CheckConstraint(
            "(external_data_source_id IS NULL) <> (document_id IS NULL)",
            name="single_origin",
        ),
    ) 
    external_data_source: Mapped["ExternalDataSource | None"] = relationship(back_populates="resources")
    document: Mapped["Document | None"] = relationship(back_populates="resource") 
    team: Mapped["Team | None"] = relationship(back_populates="resources")
    department: Mapped["Department | None"] = relationship(back_populates="resources")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
    )
    
    citations: Mapped[list["Citation"]] = relationship(back_populates="resource") 