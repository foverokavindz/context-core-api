from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.documents.document_status import DocumentStatus

if TYPE_CHECKING:
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.user import User


class Document(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "documents"

    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    ) # who put the file here, which is not who may read it - the resource this document produces carries that

    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False) # the name the file arrived under, kept unchanged so the upload can always be traced back to what the user actually sent

    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True) # a friendlier title, if anyone gave it one. Nullable rather than defaulted to the file name, so "nobody named this" stays distinguishable from "somebody named it after the file"

    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True) # what the upload claimed to be. Nullable because it comes from the client's Content-Type, which may be absent or simply wrong, and a parser that trusts it will find out either way

    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False) # bytes, taken from the stored object rather than the request. BigInteger because a 2 GB video in an HR folder should be a storage problem, not an overflow

    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False) # where the bytes are - a filesystem path or an object key. The bytes themselves are never in this table, and what does the storing is not decided yet

    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True) # room for a sha256 hex digest, the same width chunks.content_hash uses. Indexed so a re-upload of a file already held can be recognised rather than duplicated

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(
            DocumentStatus,
            name="document_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
        index=True,
    ) # how far ingestion has got with this file. Nothing here moves it along - see docs/todo.md

    uploader: Mapped["User"] = relationship(back_populates="documents")
    resource: Mapped["Resource | None"] = relationship(back_populates="document") # singular, because resources.document_id is unique: one uploaded file becomes one searchable item, and the pieces it splits into are that resource's chunks
