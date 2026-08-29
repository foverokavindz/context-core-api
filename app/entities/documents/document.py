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
    )
    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False) 
    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True) 
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True) 
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False) 
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False) 
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True) 
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
    ) 
    uploader: Mapped["User"] = relationship(back_populates="documents")
    resource: Mapped["Resource | None"] = relationship(back_populates="document") 