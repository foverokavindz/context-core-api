from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.data_sources.external_data_source import ExternalDataSource
    from app.entities.data_sources.source_credentials import SourceCredentials
    from app.entities.chunks.chunk import Chunk
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.department import Department
    from app.entities.organization.user import User
    from app.entities.teams.team_member import TeamMember


class Team(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "teams"

    department_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("departments.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    __table_args__ = (UniqueConstraint("department_id", "name"),) 
    department: Mapped["Department"] = relationship(back_populates="teams")
    creator: Mapped["User | None"] = relationship(back_populates="created_teams") 
    team_members: Mapped[list["TeamMember"]] = relationship(back_populates="team")

    source_credentials: Mapped[list["SourceCredentials"]] = relationship(back_populates="team") 
    external_data_sources: Mapped[list["ExternalDataSource"]] = relationship(back_populates="team")

    resources: Mapped[list["Resource"]] = relationship(back_populates="team") 
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="team") 