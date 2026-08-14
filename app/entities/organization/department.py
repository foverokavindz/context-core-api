from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.knowledge_sources.chunk import Chunk
    from app.entities.knowledge_sources.resource import Resource
    from app.entities.organization.job_title import JobTitle
    from app.entities.organization.user import User
    from app.entities.teams.team import Team


class Department(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    job_titles: Mapped[list["JobTitle"]] = relationship(back_populates="department")
    users: Mapped[list["User"]] = relationship(back_populates="department")
    teams: Mapped[list["Team"]] = relationship(back_populates="department")

    resources: Mapped[list["Resource"]] = relationship(back_populates="department") # knowledge the whole department may read, such as an HR policy, and not the union of its teams' resources
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="department") # the denormalized half, the same as Team.chunks
