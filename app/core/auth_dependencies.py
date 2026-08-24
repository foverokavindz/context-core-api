from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import InvalidAccessTokenError
from app.models.auth import AuthenticatedUserContext
from app.services.token_service import TokenService

bearer_scheme = HTTPBearer(auto_error=False)


def get_token_service() -> TokenService:
    return TokenService.from_environment()


def get_current_user_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    token_service: TokenService = Depends(get_token_service),
) -> AuthenticatedUserContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise InvalidAccessTokenError()
    return token_service.decode_access_token(credentials.credentials)
