from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entities import User


class UserRepository:
    """Reads `users` rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        return self.session.scalars(statement).first()
