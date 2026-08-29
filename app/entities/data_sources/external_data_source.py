from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType

if TYPE_CHECKING:
    from app.entities.data_sources.source_credentials import SourceCredentials
    from app.entities.data_sources.sync_run import SyncRun
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.user import User
    from app.entities.teams.team import Team


class ExternalDataSource(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "external_data_sources"

    team_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )

    credential_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("source_credentials.id"),
        nullable=True,
        index=True,
    ) 
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False) 
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(
            SourceType,
            name="source_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[SourceStatus] = mapped_column(
        SAEnum(
            SourceStatus,
            name="source_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SourceStatus.ACTIVE,
        server_default=SourceStatus.ACTIVE.value,
        index=True,
    ) 
    config: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    token: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    ) 
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    ) 
    team: Mapped["Team"] = relationship(back_populates="external_data_sources")
    credential: Mapped["SourceCredentials | None"] = relationship(back_populates="external_data_sources")
    creator: Mapped["User"] = relationship(back_populates="created_external_data_sources") 
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="external_data_source")
    resources: Mapped[list["Resource"]] = relationship(back_populates="external_data_source") 
