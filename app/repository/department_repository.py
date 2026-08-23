from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import Department


class DepartmentRepository:
    """Reads and writes `departments` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, department: Department) -> Department:
        self.session.add(department)
        self.session.flush()
        return department

    def list_all(self) -> list[Department]:
        statement = select(Department).order_by(Department.name, Department.id)
        return list(self.session.scalars(statement).all())

    def get_by_id(self, department_id: UUID) -> Department | None:
        return self.session.get(Department, department_id)

    def get_by_name(self, name: str) -> Department | None:
        statement = select(Department).where(Department.name == name)
        return self.session.scalars(statement).first()
