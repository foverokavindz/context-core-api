from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.data_sources.credential_type import CredentialType

if TYPE_CHECKING:
    from app.entities.data_sources.external_data_source import ExternalDataSource
    from app.entities.teams.team import Team


class SourceCredentials(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "source_credentials"

    team_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )

    credential_type: Mapped[CredentialType] = mapped_column(
        SAEnum(
            CredentialType,
            name="credential_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    ) 
    secret_reference: Mapped[str | None] = mapped_column(String(512), nullable=True) 

    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True) 
    credential_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) 
    team: Mapped["Team"] = relationship(back_populates="source_credentials")
    external_data_sources: Mapped[list["ExternalDataSource"]] = relationship(back_populates="credential")
