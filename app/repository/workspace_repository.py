from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import Workspace


class WorkspaceRepository:
    """Reads and writes `workspaces` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_existing(self) -> Workspace | None:
        return self.session.execute(select(Workspace).limit(1)).scalar_one_or_none()

    def create(self, workspace: Workspace) -> Workspace:
        self.session.add(workspace)
        self.session.flush()
        return workspace
