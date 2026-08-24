from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_current_user_context,
    get_token_service,
)
from app.core.db.dependencies import get_db
from app.models.auth import (
    AuthenticatedUserContext,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
)
from app.models.common.api_response import ApiResponse
from app.services.auth_service import AuthService
from app.services.token_service import TokenService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[LoginResponse],
)
def login(
    request: LoginRequest,
    session: Session = Depends(get_db),
    token_service: TokenService = Depends(get_token_service),
) -> ApiResponse[LoginResponse]:
    result = AuthService(session, token_service).login(request)
    return ApiResponse[LoginResponse].ok(result)


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[CurrentUserResponse],
)
def get_me(
    user_context: AuthenticatedUserContext = Depends(
        get_current_user_context
    ),
    session: Session = Depends(get_db),
    token_service: TokenService = Depends(get_token_service),
) -> ApiResponse[CurrentUserResponse]:
    user = AuthService(session, token_service).get_current_user(user_context)
    return ApiResponse[CurrentUserResponse].ok(user)
