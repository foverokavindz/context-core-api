from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.employee import CreateEmployeeRequest, EmployeeResponse
from app.services.employee_service import EmployeeService

router = APIRouter(prefix="/api/v1", tags=["employees"])


@router.post(
    "/employees",
    status_code=status.HTTP_201_CREATED,
    response_model=EmployeeResponse,
)
def create_employee(
    request: CreateEmployeeRequest,
    session: Session = Depends(get_db),
) -> EmployeeResponse:
    return EmployeeService(session).create_employee(request)


@router.get(
    "/employees",
    status_code=status.HTTP_200_OK,
    response_model=list[EmployeeResponse],
)
def list_employees(
    session: Session = Depends(get_db),
) -> list[EmployeeResponse]:
    return EmployeeService(session).list_employees()
