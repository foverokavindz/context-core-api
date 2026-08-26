import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import DepartmentAlreadyExistsError
from app.entities import Department
from app.models.department import CreateDepartmentRequest, DepartmentResponse
from app.repository.department_repository import DepartmentRepository

logger = logging.getLogger(__name__)


class DepartmentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.departments = DepartmentRepository(session)

    def create_department(
        self, request: CreateDepartmentRequest
    ) -> DepartmentResponse:
        if self.departments.get_by_name(request.name) is not None:
            raise DepartmentAlreadyExistsError()

        department = Department(
            id=uuid4(),
            name=request.name,
            description=request.description,
        )

        try:
            self.departments.create(department)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise DepartmentAlreadyExistsError()
        except SQLAlchemyError:
            self.session.rollback()
            logger.exception("Could not create a department")
            raise HTTPException(500, "The department could not be created.")

        return DepartmentResponse.model_validate(department)

    def list_departments(self) -> list[DepartmentResponse]:
        return [
            DepartmentResponse.model_validate(department)
            for department in self.departments.list_all()
        ]
