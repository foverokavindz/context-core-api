from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import Team


class TeamRepository:
    """Reads and writes `teams` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, team: Team) -> Team:
        self.session.add(team)
        self.session.flush()
        return team

    def list_all(self) -> list[Team]:
        statement = select(Team).order_by(Team.department_id, Team.name, Team.id)
        return list(self.session.scalars(statement).all())

    def get_by_id(self, team_id: UUID) -> Team | None:
        return self.session.get(Team, team_id)

    def get_by_department_and_name(
        self, department_id: UUID, name: str
    ) -> Team | None:
        statement = select(Team).where(
            Team.department_id == department_id,
            Team.name == name,
        )
        return self.session.scalars(statement).first()
