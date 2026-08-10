"""FastAPI application: creates the app, wires up routers, exposes /health.

Logging note: every log statement in this application is written to be safe to
ship. Tokens are held as pydantic SecretStr values, which render as `**********`
wherever they are formatted, and no code path logs a request body or an
Authorization header.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.github_routes import router as github_router
from app.api.jira_routes import router as jira_router
from app.core.exceptions import IngestionError

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
        "descriptions and links them, one chunk per issue. No embeddings, no "
        "database, no retrieval - yet."
    ),
)

app.include_router(github_router)
app.include_router(jira_router)


@app.exception_handler(IngestionError)
def handle_ingestion_error(request: Request, exc: IngestionError) -> JSONResponse:
    """Turn a pipeline failure into the right HTTP status.

    Registered on the base class, so every source's errors - GitHub's and
    Jira's alike - map through here. Each exception carries its own client-safe
    message; nothing from the upstream API's response body and no internal
    detail reaches the client through this path.
    """
    logger.info(
        "Ingestion failed with %s -> HTTP %d", type(exc).__name__, exc.status_code
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness check. No outside system is contacted."""
    return {"status": "ok"}
