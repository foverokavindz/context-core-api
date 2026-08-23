from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import Team, TeamMember, User

EmployeeRecord = tuple[User, TeamMember, Team]


class EmployeeRepository:
    """Persists and lists employees as a user with one team membership."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user: User, membership: TeamMember) -> tuple[User, TeamMember]:
        self.session.add_all([user, membership])
        self.session.flush()
        return user, membership

    def list_all(self) -> list[EmployeeRecord]:
        statement = (
            select(User, TeamMember, Team)
            .join(TeamMember, TeamMember.user_id == User.id)
            .join(Team, Team.id == TeamMember.team_id)
            .order_by(User.last_name, User.first_name, User.id)
        )
        return [tuple(row) for row in self.session.execute(statement).all()]

    def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        return self.session.scalars(statement).first()
