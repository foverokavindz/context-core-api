from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.organization.department import Department
    from app.entities.organization.user import User


class JobTitle(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "job_titles"

    department_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("departments.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("department_id", "name"),) 
    department: Mapped["Department"] = relationship(back_populates="job_titles")
    users: Mapped[list["User"]] = relationship(back_populates="job_title")
