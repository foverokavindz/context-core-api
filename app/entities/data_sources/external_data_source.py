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
    ) # nullable because the source row is written first and its credential second, then the id comes back here - see docs/todo.md

    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False) # a display name, deliberately not unique - two teams may both call a connection "Backend Repo"

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
    ) # the state of the connection itself, which is not the state of any one ingestion run

    config: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # what the connector points at - repository and branch, site_url and project_key, channel_id - one column rather than a set per connector, and never a secret

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    ) # the last ingestion that completed, written by the ingestion service and by nothing in this entity

    team: Mapped["Team"] = relationship(back_populates="external_data_sources")
    credential: Mapped["SourceCredentials | None"] = relationship(back_populates="external_data_sources")
    creator: Mapped["User"] = relationship(back_populates="created_external_data_sources") # who connected the source, not who may read it
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="external_data_source")
    resources: Mapped[list["Resource"]] = relationship(back_populates="external_data_source") # what this source has produced, which a SyncRun counts but does not hold
