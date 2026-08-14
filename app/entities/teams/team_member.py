from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.teams.member_role import MemberRole

if TYPE_CHECKING:
    from app.entities.organization.user import User
    from app.entities.teams.team import Team


class TeamMember(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "team_members"

    team_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
    ) # unique, so a user is in at most one team - moving teams replaces the row rather than adding one

    member_role: Mapped[MemberRole] = mapped_column(
        SAEnum(
            MemberRole,
            name="member_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=MemberRole.TEAM_MEMBER,
        server_default=MemberRole.TEAM_MEMBER.value,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ) # when the person joined the team, which a service may backdate - created_at is when the row was written

    team: Mapped["Team"] = relationship(back_populates="team_members")
    user: Mapped["User"] = relationship(back_populates="team_membership")
