from sqlalchemy.orm import Session

from app.core.exceptions import (
    ApplicationAuthError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
)
from app.core.security import verify_password
from app.entities import User
from app.models.auth import (
    AuthenticatedUserContext,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
)
from app.repository.user_repository import UserRepository
from app.services.token_service import TokenService


class AuthService:
    def __init__(self, session: Session, token_service: TokenService) -> None:
        self.users = UserRepository(session)
        self.tokens = token_service

    def login(self, request: LoginRequest) -> LoginResponse:
        user = self.users.get_by_email(request.email)
        if (
            user is None
            or not user.is_active
            or not verify_password(
                request.password.get_secret_value(), user.password_hash
            )
        ):
            raise InvalidCredentialsError()

        context = _to_context(user, InvalidCredentialsError)
        return LoginResponse(
            access_token=self.tokens.create_access_token(context),
            user=_to_current_user(user, context),
        )

    def get_current_user(
        self, context: AuthenticatedUserContext
    ) -> CurrentUserResponse:
        user = self.users.get_by_id(context.user_id)
        if user is None or not user.is_active:
            raise InvalidAccessTokenError()

        current_context = _to_context(user, InvalidAccessTokenError)
        return _to_current_user(user, current_context)


def _to_context(
    user: User,
    error_type: type[ApplicationAuthError],
) -> AuthenticatedUserContext:
    membership = user.team_membership
    if user.department_id is None or membership is None:
        raise error_type()

    return AuthenticatedUserContext(
        user_id=user.id,
        application_role=user.application_role,
        team_id=membership.team_id,
        department_id=user.department_id,
    )


def _to_current_user(
    user: User, context: AuthenticatedUserContext
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        application_role=user.application_role,
        team_id=context.team_id,
        department_id=context.department_id,
    )
