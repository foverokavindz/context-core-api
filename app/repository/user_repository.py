from uuid import UUID

from sqlalchemy.orm import Session

from app.entities import User


class UserRepository:
    """Reads `users` rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.session.get(User, user_id)
