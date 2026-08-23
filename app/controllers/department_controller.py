from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.department import CreateDepartmentRequest, DepartmentResponse
from app.services.department_service import DepartmentService

router = APIRouter(prefix="/api/v1", tags=["departments"])


@router.post(
    "/departments",
    status_code=status.HTTP_201_CREATED,
    response_model=DepartmentResponse,
)
def create_department(
    request: CreateDepartmentRequest,
    session: Session = Depends(get_db),
) -> DepartmentResponse:
    return DepartmentService(session).create_department(request)


@router.get(
    "/departments",
    status_code=status.HTTP_200_OK,
    response_model=list[DepartmentResponse],
)
def list_departments(
    session: Session = Depends(get_db),
) -> list[DepartmentResponse]:
    return DepartmentService(session).list_departments()
