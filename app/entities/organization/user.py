from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Uuid, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.organization.application_role import ApplicationRole

if TYPE_CHECKING:
    from app.entities.organization.department import Department
    from app.entities.organization.job_title import JobTitle


class User(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)

    username: Mapped[str | None] = mapped_column(String(150), nullable=True, unique=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)

    department_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("departments.id"),
        nullable=True,
        index=True,
    )
    job_title_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("job_titles.id"),
        nullable=True,
        index=True,
    )

    application_role: Mapped[ApplicationRole] = mapped_column(
        SAEnum(
            ApplicationRole,
            name="application_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ApplicationRole.EMPLOYEE,
        server_default=ApplicationRole.EMPLOYEE.value,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    department: Mapped["Department | None"] = relationship(back_populates="users") # populate the department relationship with the User model
    job_title: Mapped["JobTitle | None"] = relationship(back_populates="users") # populate the job_title relationship with the User model
