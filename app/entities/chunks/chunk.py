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
from app.entities.chunks.chunk_type import ChunkType
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
    ) # no index of its own - the unique constraint below leads with this column and already builds that btree. Not a ForeignKey() on its own line: it is half of the composite key below, and declaring it singly as well would emit a second, redundant constraint

    external_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    ) # 512 for the same reason resources.external_id is: a file path is the long case. No index of its own either - unlike resources.external_id, which is the *last* column of its constraint and so needs one, this is the second of three, and "every chunk of this resource" is a prefix match on (external_data_source_id, external_id) that the constraint's btree already answers. Looking a chunk up by external_id without knowing its source is not a query anything makes

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
        VECTOR(1536),
        nullable=True,
    ) # a real vector(1536) on PostgreSQL and JSON elsewhere, the same trade config and the metadata columns make. Nullable: a chunk exists from the moment it is written and is embedded after
  
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True) # which model produced the vector, so a model change can be found rather than guessed at

    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True) # room for a sha256 hex digest. Nothing here hashes anything - the ingestion service does, to skip re-embedding content that did not change

    chunk_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # what the chunk models carry beyond the columns above: symbol_name and start_line for code, and the item's own fields - repository and branch, space_key, project_key. A deliberate second copy of what the resource row already holds, so a retrieval hit is self-describing without a join back to resources. The resource stays the source of truth; nothing keeps this copy in step, see docs/todo.md

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
        ForeignKeyConstraint(
            ["external_data_source_id", "external_id"],
            ["resources.external_data_source_id", "resources.external_id"],
            name="fk_chunks_resource",
        ),
        UniqueConstraint("external_data_source_id", "external_id", "chunk_index"),
    ) # the first replaces what resource_id used to do with one column: a chunk belongs to the resource sharing its (source, external_id) pair, which is exactly what resources declares unique. The second scopes chunk_index to that same pair rather than to external_id alone - external_id only means something inside its source, so two repositories each holding a README.md would otherwise collide at every index. Neither constraint reaches a document-origin chunk, whose columns are both NULL; see docs/todo.md

    resource: Mapped["Resource | None"] = relationship(back_populates="chunks") # optional, unlike the old resource_id which was NOT NULL: the columns this joins on are nullable, so a document-origin chunk resolves to None here rather than to a row

    team: Mapped["Team | None"] = relationship(back_populates="chunks")
    department: Mapped["Department | None"] = relationship(back_populates="chunks")
    citations: Mapped[list["Citation"]] = relationship(back_populates="chunk") # every time this chunk was cited in an answer, which is a record of what retrieval actually found useful and not something the chunk needs to know to be embedded
