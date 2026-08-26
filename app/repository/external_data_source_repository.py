from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType
from app.models.ingestion.request import IngestDataRequest


class ExternalDataSourceRepository:
    """Reads and writes `external_data_sources` rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, request: IngestDataRequest, source_type: SourceType
    ) -> ExternalDataSource:
        """Record a new connection and return it, with its id already set."""

        source = ExternalDataSource(
            id=uuid4(),
            team_id=request.team_id,
            created_by_user_id=request.created_by_user_id,
            name=request.title,
            source_type=source_type,
            status=SourceStatus.ACTIVE,
            config=dict(request.config),
            token=request.token.get_secret_value(),
        )
        self.session.add(source)
        self.session.flush()
        
        return source

    def mark_synced(self, source_id: UUID, synced_at: datetime) -> None:
        """Record the last ingestion that completed.     """
        source = self.session.get(ExternalDataSource, source_id)
        if source is not None:
            source.last_synced_at = synced_at

    def get_by_id(self, source_id: UUID) -> ExternalDataSource | None:
        return self.session.get(ExternalDataSource, source_id)

    def list_by_team(self, team_id: UUID) -> list[ExternalDataSource]:
        """Every source this team has connected, newest first."""
        statement = (
            select(ExternalDataSource)
            .where(ExternalDataSource.team_id == team_id)
            .order_by(ExternalDataSource.created_at.desc(), ExternalDataSource.id)
        )  
        return list(self.session.scalars(statement).all())
