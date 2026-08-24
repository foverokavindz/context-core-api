from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.common.api_response import ApiResponse
from app.models.workspace import CreateWorkspaceRequest, WorkspaceResponse
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/api/v1", tags=["workspace"])


@router.post(
    "/workspace",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[WorkspaceResponse],
    summary="Create the application workspace",
)
def create_workspace(
    request: CreateWorkspaceRequest,
    session: Session = Depends(get_db),
) -> ApiResponse[WorkspaceResponse]:
    workspace = WorkspaceService(session).create_workspace(request)
    return ApiResponse[WorkspaceResponse].ok(workspace)
