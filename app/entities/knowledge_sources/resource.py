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
    ) # nullable because the other origin is a Document, which has no connected source - no index of its own, the unique constraint below leads with this column

    document_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("documents.id"),
        nullable=True,
        unique=True,
        index=True,
    ) # unique, so one uploaded file becomes one searchable item - a document that needs splitting produces chunks, not a second resource. NULLs stay distinct in SQL, so every source-origin row is unaffected

    resource_type: Mapped[ResourceType] = mapped_column(
        SAEnum(
            ResourceType,
            name="resource_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    ) # what the original item is, which is not what its source is - one GitHub source produces only GITHUB_FILE rows, but a query for "every file" spans every source

    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True) # how the source names the item: a file path, TRACK-25, a Confluence page id, a channel:timestamp pair. 512 because file paths are the long case

    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    version_key: Mapped[str | None] = mapped_column(String(255), nullable=True) # whatever the source uses to say "this changed" - a commit SHA, a page version, a checksum. One column rather than one per source, and nullable because not every source has one

    access_scope: Mapped[ResourceAccessScope] = mapped_column(
        SAEnum(
            ResourceAccessScope,
            name="resource_access_scope",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    ) # the resource is the source of truth for who may read it - the same three columns on Chunk are copies of these

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
    ) # both nullable, and which one is set follows access_scope: TEAM fills the first, DEPARTMENT the second, ORGANIZATION neither. Nothing in the schema enforces that pairing - see docs/todo.md

    resource_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # source-specific context that does not earn a column: repository and branch, issue_key, space_key, channel_id. Never a permission field - those are the three columns above

    __table_args__ = (
        UniqueConstraint("external_data_source_id", "external_id"),
        CheckConstraint(
            "(external_data_source_id IS NULL) <> (document_id IS NULL)",
            name="single_origin",
        ),
    ) # the first: one row per item per source. external_id alone is not unique - TRACK-25 means something only inside its Jira project, and NULLs are distinct in SQL, so document-origin rows are unaffected. The second: a resource was either fetched from a connected system or uploaded as a file, never both and never neither. Written as a comparison of two IS NULL tests rather than as num_nonnulls(), which is PostgreSQL's alone - this form compiles on SQLite too

    external_data_source: Mapped["ExternalDataSource | None"] = relationship(back_populates="resources")
    document: Mapped["Document | None"] = relationship(back_populates="resource") # the other origin, and the check constraint above means exactly one of the two is ever set
    team: Mapped["Team | None"] = relationship(back_populates="resources")
    department: Mapped["Department | None"] = relationship(back_populates="resources")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
    )
    
    citations: Mapped[list["Citation"]] = relationship(back_populates="resource") # every answer that named this resource as a source. No cascade: deleting a resource that has been cited is refused, because the citation is a record of what an answer was built on and outlives the thing it points at being re-ingested
