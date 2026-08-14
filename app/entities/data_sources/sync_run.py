from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.data_sources.sync_run_status import SyncRunStatus

if TYPE_CHECKING:
    from app.entities.data_sources.external_data_source import ExternalDataSource


class SyncRun(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "sync_runs"

    external_data_source_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("external_data_sources.id"),
        nullable=False,
        index=True,
    ) # the only foreign key here - the team is reached through the source, so it is not duplicated onto the run

    status: Mapped[SyncRunStatus] = mapped_column(
        SAEnum(
            SyncRunStatus,
            name="sync_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SyncRunStatus.PENDING,
        server_default=SyncRunStatus.PENDING.value,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    ) # both nullable, since a PENDING run has neither and a FAILED one may have only the first

    resources_processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    chunks_created: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    chunks_updated: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    chunks_deleted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # named run_metadata rather than metadata, which a declarative class already uses for Base.metadata

    external_data_source: Mapped["ExternalDataSource"] = relationship(back_populates="sync_runs")
