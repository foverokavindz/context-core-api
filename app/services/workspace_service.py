import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import WorkspaceAlreadyExistsError
from app.entities import Workspace
from app.models.workspace import CreateWorkspaceRequest, WorkspaceResponse
from app.repository.workspace_repository import WorkspaceRepository

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.workspaces = WorkspaceRepository(session)

    def create_workspace(
        self, request: CreateWorkspaceRequest
    ) -> WorkspaceResponse:
        if self.workspaces.get_existing() is not None:
            raise WorkspaceAlreadyExistsError()

        workspace = Workspace(
            id=uuid4(),
            company_name=request.company_name,
            subtitle=request.subtitle,
            description=request.description,
            logo=request.logo,
        )

        try:
            self.workspaces.create(workspace)
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            logger.exception("Could not create the workspace")
            raise HTTPException(
                status_code=500,
                detail="The workspace could not be created.",
            )

        return WorkspaceResponse.model_validate(workspace)
