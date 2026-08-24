from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.common.api_response import ApiResponse
from app.models.employee import CreateEmployeeRequest, EmployeeResponse
from app.services.employee_service import EmployeeService

router = APIRouter(prefix="/api/v1", tags=["employees"])


@router.post(
    "/employees",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[EmployeeResponse],
)
def create_employee(
    request: CreateEmployeeRequest,
    session: Session = Depends(get_db),
) -> ApiResponse[EmployeeResponse]:
    employee = EmployeeService(session).create_employee(request)
    return ApiResponse[EmployeeResponse].ok(employee)


@router.get(
    "/employees",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[EmployeeResponse]],
)
def list_employees(
    session: Session = Depends(get_db),
) -> ApiResponse[list[EmployeeResponse]]:
    employees = EmployeeService(session).list_employees()
    return ApiResponse[list[EmployeeResponse]].ok(employees)
