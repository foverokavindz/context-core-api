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
    ) # which provider this authenticates against, and a branch in whatever code eventually uses it - Basic with an email for Jira and Confluence, a bearer token for GitHub, an xoxb header for Slack

    secret_reference: Mapped[str | None] = mapped_column(String(512), nullable=True) # a pointer into a future secret manager, never the secret itself

    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True) # ciphertext only - there is no plain-token column here and there will never be one

    credential_metadata: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    ) # non-secret context such as auth_type or account_email, so a row can be identified without decrypting anything

    team: Mapped["Team"] = relationship(back_populates="source_credentials")
    external_data_sources: Mapped[list["ExternalDataSource"]] = relationship(back_populates="credential")
