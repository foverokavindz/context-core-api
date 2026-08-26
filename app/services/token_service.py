import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from pydantic import ValidationError

from app.core.exceptions import InvalidAccessTokenError, JWTConfigurationError
from app.models.auth import AuthenticatedUserContext

DEFAULT_JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60*24*7
SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
REQUIRED_ACCESS_TOKEN_CLAIMS = [
    "sub",
    "application_role",
    "team_id",
    "department_id",
    "iat",
    "exp",
]


class TokenService:
    def __init__(
        self,
        secret: str,
        algorithm: str = DEFAULT_JWT_ALGORITHM,
        access_token_expire_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
    ) -> None:
        if not secret:
            raise JWTConfigurationError()
        if algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise JWTConfigurationError()
        if access_token_expire_minutes <= 0:
            raise JWTConfigurationError()

        self._secret = secret
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes

    @classmethod
    def from_environment(cls) -> "TokenService":
        load_dotenv()

        secret = os.getenv("JWT_SECRET", "")
        algorithm = os.getenv("JWT_ALGORITHM", DEFAULT_JWT_ALGORITHM)
        raw_expiry = os.getenv(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
            str(DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        try:
            expiry = int(raw_expiry)
        except ValueError as exc:
            raise JWTConfigurationError() from exc

        return cls(secret, algorithm, expiry)

    def create_access_token(self, context: AuthenticatedUserContext) -> str:
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(
            minutes=self.access_token_expire_minutes
        )
        payload = {
            "sub": str(context.user_id),
            "application_role": context.application_role.value,
            "team_id": str(context.team_id),
            "department_id": str(context.department_id),
            "iat": issued_at,
            "exp": expires_at,
        }
        return jwt.encode(payload, self._secret, algorithm=self.algorithm)

    def decode_access_token(self, token: str) -> AuthenticatedUserContext:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self.algorithm],
                options={"require": REQUIRED_ACCESS_TOKEN_CLAIMS},
            )
            self._validate_claim_types(payload)
            return AuthenticatedUserContext(
                user_id=payload["sub"],
                application_role=payload["application_role"],
                team_id=payload["team_id"],
                department_id=payload["department_id"],
            )
        except (jwt.PyJWTError, ValidationError, KeyError, TypeError) as exc:
            raise InvalidAccessTokenError() from exc

    @staticmethod
    def _validate_claim_types(payload: dict) -> None:
        for claim in (
            "sub",
            "application_role",
            "team_id",
            "department_id",
        ):
            if not isinstance(payload.get(claim), str):
                raise TypeError(f"{claim} must be a string")

        for claim in ("iat", "exp"):
            if type(payload.get(claim)) is not int:
                raise TypeError(f"{claim} must be an integer")
