from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.entities.data_sources.sync_run import SyncRun
from app.entities.data_sources.sync_run_status import SyncRunStatus


class SyncRunRepository:
    """Reads and writes `sync_runs` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, external_data_source_id: UUID) -> SyncRun:
        """Queue a run. PENDING means recorded and not yet begun."""
        run = SyncRun(
            id=uuid4(),
            external_data_source_id=external_data_source_id,
            status=SyncRunStatus.PENDING,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def update_status(
        self,
        sync_run_id: UUID,
        status: SyncRunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        resources_processed: int | None = None,
        chunks_created: int | None = None,
        error_message: str | None = None,
    ) -> SyncRun | None:
        """Move a run to a new status, writing only the fields that were passed."""
        run = self.session.get(SyncRun, sync_run_id)
        if run is None:
            return None

        run.status = status

        if started_at is not None:
            run.started_at = started_at
        if completed_at is not None:
            run.completed_at = completed_at
        if resources_processed is not None:
            run.resources_processed = resources_processed
        if chunks_created is not None:
            run.chunks_created = chunks_created
        if error_message is not None:
            run.error_message = error_message

        self.session.flush()
        return run
