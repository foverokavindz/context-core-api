from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.organization.job_title import JobTitle
    from app.entities.organization.user import User


class Department(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    job_titles: Mapped[list["JobTitle"]] = relationship(back_populates="department")
    users: Mapped[list["User"]] = relationship(back_populates="department")
