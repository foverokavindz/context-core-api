from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.common.api_response import ApiResponse
from app.models.department import CreateDepartmentRequest, DepartmentResponse
from app.services.department_service import DepartmentService

router = APIRouter(prefix="/api/v1", tags=["departments"])


@router.post(
    "/departments",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[DepartmentResponse],
)
def create_department(
    request: CreateDepartmentRequest,
    session: Session = Depends(get_db),
) -> ApiResponse[DepartmentResponse]:
    department = DepartmentService(session).create_department(request)
    return ApiResponse[DepartmentResponse].ok(department)


@router.get(
    "/departments",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[DepartmentResponse]],
)
def list_departments(
    session: Session = Depends(get_db),
) -> ApiResponse[list[DepartmentResponse]]:
    departments = DepartmentService(session).list_departments()
    return ApiResponse[list[DepartmentResponse]].ok(departments)
