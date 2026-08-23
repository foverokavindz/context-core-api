"""FastAPI application: creates the app, wires up routers, exposes /health.

Logging note: every log statement in this application is written to be safe to
ship. Tokens are held as pydantic SecretStr values, which render as `**********`
wherever they are formatted, and no code path logs a request body or an
Authorization header.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.confluence_routes import router as confluence_router
from app.api.github_routes import router as github_router
from app.api.jira_routes import router as jira_router
from app.api.slack_routes import router as slack_router
from app.controllers.chat_controller import router as chat_router
from app.controllers.department_controller import router as department_router
from app.controllers.employee_controller import router as employee_router
from app.controllers.ingestion_controller import router as ingestion_router
from app.controllers.team_controller import router as team_router
from app.controllers.workspace_controller import router as workspace_router
from app.core.exceptions import (
    IngestionError,
    OrganizationError,
    WorkspaceAlreadyExistsError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Context Core Ingestion API",
    version="0.1.0",
    description=(
        "Stage one of the ingestion pipeline. GitHub: pulls TypeScript source "
        "out of a repository, filters it, and parses it into code chunks with "
        "Tree-sitter. Jira: pulls a project's Epics and Stories, flattens their "
        "descriptions and links them, one chunk per issue. Confluence: pulls one "
        "space's pages, flattens their storage markup into readable text, one "
        "chunk per page. Slack: pulls one channel's message history, drops "
        "thread replies and channel events, one chunk per message. Every run is "
        "embedded and persisted as resources and chunks. Chat: one question "
        "runs the retrieval pipeline - understood, planned, executed - and a "
        "chat model writes the answer from what was found. No reranking yet."
    ),
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


@app.exception_handler(WorkspaceAlreadyExistsError)
def handle_workspace_already_exists(
    request: Request, exc: WorkspaceAlreadyExistsError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.message})


@app.exception_handler(OrganizationError)
def handle_organization_error(
    request: Request, exc: OrganizationError
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness check. No outside system is contacted."""
    return {"status": "ok"}
