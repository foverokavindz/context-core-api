"""FastAPI application: creates the app, wires up routers, exposes /health.

Logging note: every log statement in this application is written to be safe to
ship. Tokens are held as pydantic SecretStr values, which render as `**********`
wherever they are formatted, and no code path logs a request body or an
Authorization header.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.confluence_routes import router as confluence_router
from app.api.github_routes import router as github_router
from app.api.jira_routes import router as jira_router
from app.api.slack_routes import router as slack_router
from app.controllers.chat_controller import router as chat_router
from app.controllers.auth_controller import router as auth_router
from app.controllers.department_controller import router as department_router
from app.controllers.employee_controller import router as employee_router
from app.controllers.ingestion_controller import router as ingestion_router
from app.controllers.team_controller import router as team_router
from app.controllers.workspace_controller import router as workspace_router
from app.core.exceptions import (
    ApplicationAuthError,
    IngestionError,
    OrganizationError,
    WorkspaceAlreadyExistsError,
)
from app.core.responses import error_response, error_text
from app.models.common.api_response import ApiResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
)

def _cors_allowed_origins() -> list[str]:

    load_dotenv()
    configured = os.getenv("CORS_ALLOWED_ORIGINS")
    if configured is None:
        return list(DEFAULT_CORS_ALLOWED_ORIGINS)
    return [
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    ]

ERROR_RESPONSES = {
    400: {"model": ApiResponse[None], "description": "Bad request"},
    401: {"model": ApiResponse[None], "description": "Unauthorized"},
    403: {"model": ApiResponse[None], "description": "Forbidden"},
    404: {"model": ApiResponse[None], "description": "Not found"},
    409: {"model": ApiResponse[None], "description": "Conflict"},
    422: {"model": ApiResponse[None], "description": "Validation error"},
    429: {"model": ApiResponse[None], "description": "Too many requests"},
    500: {"model": ApiResponse[None], "description": "Internal server error"},
    502: {"model": ApiResponse[None], "description": "Upstream service error"},
}

app = FastAPI(
    title="Context Core Ingestion API",
    version="0.1.0",
    description=( "" ),
    responses=ERROR_RESPONSES,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(github_router)
# app.include_router(jira_router)
# app.include_router(confluence_router)
# app.include_router(slack_router)

app.include_router(ingestion_router)
app.include_router(chat_router)
app.include_router(workspace_router)
app.include_router(department_router)
app.include_router(team_router)
app.include_router(employee_router)
app.include_router(auth_router)


@app.exception_handler(ApplicationAuthError)
def handle_application_auth_error(
    request: Request, exc: ApplicationAuthError
) -> JSONResponse:
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if exc.status_code == 401
        else None
    )
    return error_response(
        exc.message,
        status_code=exc.status_code,
        headers=headers,
    )


@app.exception_handler(WorkspaceAlreadyExistsError)
def handle_workspace_already_exists(
    request: Request, exc: WorkspaceAlreadyExistsError
) -> JSONResponse:
    return error_response(exc.message, status_code=409)


@app.exception_handler(OrganizationError)
def handle_organization_error(
    request: Request, exc: OrganizationError
) -> JSONResponse:
    return error_response(
        exc.message,
        status_code=exc.status_code,
    )


@app.exception_handler(IngestionError)
def handle_ingestion_error(request: Request, exc: IngestionError) -> JSONResponse:
    """Turn a pipeline failure into the right HTTP status.

    Registered on the base class, so every source's errors - GitHub's, Jira's,
    Confluence's and Slack's alike - map through here. Each exception carries
    its own client-safe message; nothing from the upstream API's response body
    and no internal detail reaches the client through this path.
    """
    logger.info(
        "Ingestion failed with %s -> HTTP %d", type(exc).__name__, exc.status_code
    )
    return error_response(exc.message, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    issues: list[str] = []
    for issue in exc.errors():
        location = ".".join(str(part) for part in issue.get("loc", ()))
        prefix = f"{location}: " if location else ""
        issues.append(f"{prefix}{issue.get('msg', 'Invalid value')}")
    detail = "; ".join(issues) or "The request is invalid."
    return error_response(detail, status_code=422)


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return error_response(
        error_text(exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled %s during %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )
    return error_response(
        "An unexpected server error occurred.",
        status_code=500,
    )


@app.get(
    "/health",
    tags=["health"],
    response_model=ApiResponse[dict[str, str]],
)
def health() -> ApiResponse[dict[str, str]]:
    """Liveness check. No outside system is contacted."""
    return ApiResponse[dict[str, str]].ok({"status": "ok"})
