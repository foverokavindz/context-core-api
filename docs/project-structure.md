# Project Structure

[← Documentation](README.md) · Entities: [entity-reference.md](entity-reference.md) · Design rationale: [architecture.md](architecture.md)

What each folder is for, which layer may call which, and where the two live
request flows go. This page is the map; [architecture.md](architecture.md)
argues *why* the four source pipelines are kept apart, and
[entity-reference.md](entity-reference.md) is the table-by-table schema.

## What the application is

A FastAPI service in two halves that meet in PostgreSQL:

- **Ingestion** — connect an external system (GitHub, Jira, Confluence, Slack),
  pull its items, flatten them to text, cut them into chunks, embed the chunks,
  and persist `resources` + `chunks`.
- **Retrieval** — embed a query with the same model and return the nearest
  chunks. Retrieval only; nothing is generated from what it finds.

Stack: FastAPI · Pydantic v2 · SQLAlchemy 2.0 (`Mapped`/`mapped_column`) ·
PostgreSQL + `pgvector` via `psycopg` 3 · Alembic · OpenAI/Azure embeddings ·
Tree-sitter (TypeScript) · PyGithub / httpx · pytest.

## Layers

```
HTTP  ─▶  api/ · controllers/     shape only: parse, validate, map to a status
            │
            ▼
          services/               the use case; owns the transaction boundary
            │
            ├──▶ background/pipeline/    long work, after the response
            │        │
            │        └──▶ connectors/ ─▶ ingestion/ (parser · chunker · embedder)
            │
            └──▶ repository/      the ONLY place that reads or writes a table
                     │
                     ▼
                  entities/       SQLAlchemy rows          core/db/  engine, Session
```

Two rules hold the layering up:

1. **Only `repository/` touches `entities/` as tables.** No connector, parser or
   chunker imports a table; the four pipelines still end at a Pydantic DTO. (Two
   *enums* — `SourceType`, `ResourceAccessScope` — are imported more widely, on
   purpose: an enum is not a table, and copying the values would create a second
   list to keep in step.)
2. **No repository commits.** The caller owns the transaction, which is what
   lets a whole run's resources, chunks, run status and `last_synced_at` land
   together.

## Folder by folder

### Root

| Path | What it is |
| --- | --- |
| `app/` | The application package |
| `migrations/` | Alembic environment + `versions/` (the real migration scripts) |
| `alembic.ini` | Points `script_location` at `migrations/`; the URL is overridden at runtime |
| `scripts/seed_dev.py` | Seeds the department/team/user rows the ingestion endpoint's NOT NULL FKs need. Fixed ids, safe to re-run, dev only |
| `docs/` | This documentation, plus `connectors/` per source and a Postman collection |
| `test.py` | A scratch script for poking the embedding deployment — not part of the suite |
| `pyproject.toml` | Dependencies, the explicit `[tool.setuptools] packages` list, `testpaths = app/tests` |

### `app/main.py`
Creates the `FastAPI` app, configures logging, includes the routers and
registers **one** exception handler on the `IngestionError` base class, so every
source's errors map to their own HTTP status through a single path. Exposes
`GET /health`. The four per-source routers are currently **commented out** —
only the ingestion and retrieval routers are mounted.

### `app/api/` — per-source routes (debug shape)
`github_routes.py`, `jira_routes.py`, `confluence_routes.py`, `slack_routes.py`.
Thin routers that run one pipeline **synchronously** and return the whole result
inline. Useful for debugging a single connector, useless against a real
repository (minutes > an HTTP client's patience). Each also holds a
`to_response(...)` mapper that the background pipeline still imports.

### `app/controllers/` — the mounted endpoints
| File | Endpoint | Job |
| --- | --- | --- |
| `ingestion_controller.py` | `POST /api/v1/ingestData/{external_source}` → `202` | Resolve the path segment to a `SourceType` (404 if unknown), check `source_type` matches the URL and that `config` carries `REQUIRED_CONFIG_KEYS` for it (400), then delegate |
| `retrieval_controller.py` | `POST /api/v1/retrieve` | Delegate to `retrieve(...)`. Builds one module-level `ChunkEmbedder` and hands it over as a **dependency**, so tests override it with `app.dependency_overrides` rather than patching a global |

Controllers know which sources exist and what a valid request looks like. They
know nothing about chunks.

### `app/services/` — use cases
| File | Job |
| --- | --- |
| `ingestion_service.py` | `start_ingestion(...)`: write the `ExternalDataSource`, queue a `PENDING` `SyncRun`, **commit**, hand the work to `BackgroundTasks`, return the two ids. An `IntegrityError` here means a bad `team_id`/`department_id`/`created_by_user_id` → 400 |
| `retrieval_service.py` | `retrieve(...)`: embed the query, call `ChunkRepository.search_by_embedding`, project matches onto `RetrievedChunk`. Converts the request's `min_score` into the repository's `max_distance` (`1 - score`) — the repository speaks distance, because that is what the database compares |

Both commit *before* they answer, so the ids a caller receives name rows that
exist.

### `app/background/pipeline/`
`ingestion_pipeline.py` — everything that happens after the `202`:

```
sync run → RUNNING
  the source's own ingestion service, unchanged:
  connector → parser → chunker → embedder
  stamp PermissionScope onto every item and chunk
→ resources + chunks + sync run COMPLETED, in ONE transaction
→ app/data/runs/<source>_<id>.json
```

It is the only module importing all four ingestion services, chosen with a plain
`if`/`elif`. **It opens its own session** — it takes a session factory
(`SessionLocal` by default) rather than the request's `Session`, so it owns one
end to end across minutes of network work; that factory is also the seam tests
substitute. Failures move the run to `FAILED` with a client-safe message.

### `app/connectors/` — the outside world
`base.py` (`BaseSourceConnector`, `SourceSnapshot`) plus one connector per
source. `github_connector.py` is the only file importing PyGithub; the other
three are the only files in their pipelines importing httpx and the only ones
that know their APIs' endpoints, auth and pagination. Each ends at a boundary
DTO — `RepositoryFile`, `JiraIssue`, `ConfluencePage`, `SlackMessage` — and
nothing past that boundary knows which system it came from.

### `app/ingestion/` — flatten, chunk, embed
| Group | Files |
| --- | --- |
| Orchestration | `ingestion_service.py` (GitHub), `jira_ingestion_service.py`, `confluence_ingestion_service.py`, `slack_ingestion_service.py` |
| Format flattening | `jira_parser.py` + `jira_adf.py` (ADF → text), `confluence_parser.py` + `confluence_storage.py` (storage XHTML → text), `slack_parser.py`, `parser/` (`base.py` registry + `typescript_parser.py`, Tree-sitter) |
| Selection | `file_filter.py` — which paths are worth ingesting |
| Chunking | `jira_chunker.py`, `confluence_chunker.py`, `slack_chunker.py` (one item → one chunk); GitHub chunks per parsed symbol |
| Shared | `embedding_service.py` — the **only** module importing `openai` |

`embedding_service.py` is the single deliberate exception to keeping the
pipelines apart. It talks to a structural `Protocol`:

```python
class EmbeddableChunk(Protocol):
    content: str
    embedding: list[float] | None
    embedding_model: str | None
```

`CodeChunk`, `JiraChunk`, `ConfluenceChunk` and `SlackChunk` satisfy it without
inheriting anything, and every service calls `embed_into(result, embedder)` in
one line after its chunker.

### `app/models/` — Pydantic DTOs (never tables)
One package per source with the same four names, plus the shared and
source-agnostic ones:

| Package | Contents |
| --- | --- |
| `common/` | `PermissionScope` (`team_id`, `department_id`, `access_scope`, `external_data_source_id`), `EmbeddingCounts` |
| `github/` `jira/` `confluence/` `slack/` | `request.py`, the boundary type (`file`/`issue`/`page`/`message`), `chunk.py`, `response.py` + limits |
| `ingestion/` | `IngestDataRequest` + `REQUIRED_CONFIG_KEYS`, `IngestStartedResponse` |
| `retrieval/` | `RetrieveRequest` (`query` ≤ 4,000 chars, `top_k` ≤ 50, scope filters, `min_score`), `RetrievedChunk` / `RetrieveResponse` — the chunk **without** its 1536-float vector |

### `app/entities/` — the database layer
Seven groups: `organization/`, `teams/`, `data_sources/`, `documents/`,
`knowledge_sources/`, `chunks/`, `chat/`, over `base.py` (`Base`, `UUIDMixin`,
`TimestampMixin`). Import **the package**, not a group: the groups hold
relationships into each other, so importing one alone leaves mappers
unresolvable — which is exactly why `migrations/env.py` does `import app.entities`.
Full column/enum/constraint detail: [entity-reference.md](entity-reference.md).

### `app/repository/` — where DTOs become rows
| File | Surface |
| --- | --- |
| `external_data_source_repository.py` | `create(request, source_type)`, `mark_synced(source_id, at)` |
| `sync_run_repository.py` | `create(source_id)` → PENDING, `update_status(...)` writing only the fields passed |
| `resource_repository.py` | `add_new_resources(items)` — any source's item model → `resources` rows |
| `chunk_repository.py` | `add_new_chunks(chunks)`, and `search_by_embedding(...)` — the only vector query in the application |

The DTO→row mapping is written once, not four times: all four sources agreed on
`external_id`, `title`, `version_key`, `resource_type` and the permission trio,
and disagreed only on the remainder — which is derived from `model_dump()` into
`resource_metadata` / `chunk_metadata`. A connector that grows a field gets it
stored with no change here.

`search_by_embedding` ranks by **cosine distance**, filters (team, department,
source, `max_distance`) *before* ranking — the reason `chunks` carries its own
copy of the permission columns — outer-joins `resources` for a title and type,
skips rows with no vector, and orders by `(distance, id)` for a stable tie-break.
There is no ANN index yet; it is a sequential scan, deferred on purpose until
there is real data to tune against ([todo.md](todo.md)).

### `app/core/`
| Path | What it holds |
| --- | --- |
| `exceptions.py` | `IngestionError` base (`status_code`, `message`) and ~25 subclasses — auth/permission/not-found/rate-limit/API per source, plus `EmbeddingConfigurationError`, `EmbeddingError`, `DatabaseConfigurationError`. Every message is written to be safe to hand to a client |
| `db/session.py` | Reads `DATABASE_URL` (via `python-dotenv`), builds the `engine` (`pool_pre_ping=True`) and `SessionLocal` (`autoflush=False`, `expire_on_commit=False`) |
| `db/dependencies.py` | `get_db()` — per-request session, closed in `finally` |

### `app/data/` and `app/tests/`
`data/` holds sample responses and `runs/<source>_<id>.json` — scaffolding, not a
feature: the rows are the record, and no response ever names the file.
`tests/` is one module per unit (connector, parser, chunker, ingestion service,
API) run with `pytest` (`testpaths` is already set, so bare `pytest` works).

## The two request flows

**Ingest**
```
POST /api/v1/ingestData/{source}
  ingestion_controller   resolve SourceType · validate config
  ingestion_service      ExternalDataSource + PENDING SyncRun · commit
  → 202 { external_data_source_id, sync_run_id }
  ingestion_pipeline     RUNNING → connector → parser → chunker → embedder
                         → PermissionScope → resources + chunks → COMPLETED
                         (one transaction, its own session)
```

**Retrieve**
```
POST /api/v1/retrieve
  retrieval_controller   validate (top_k ≤ 50, query ≤ 4,000 chars)
  retrieval_service      embed the query (the only outbound call)
  chunk_repository       filter → cosine-rank → outer-join resources
  ← RetrieveResponse     RetrievedChunk[] with score = 1 − distance, no vectors
```

## Conventions worth copying

- **DTO in, DTO out.** Entities never leave the service layer as themselves.
- **Commit at the use case**, never in a repository.
- **One boundary type per source**, and nothing past it names the source.
- **Errors carry their own status.** Raise an `IngestionError` subclass; the
  handler in `main.py` does the rest.
- **Nothing sensitive is logged** — no request bodies, no auth headers, no chunk
  content, and no query text (its length only). Tokens are `SecretStr` and
  render as `**********`.
- **Enums are native PG enums** declared with `values_callable`, so the stored
  value is the member value.

## Running it

```bash
pip install -e ".[dev]"       # DATABASE_URL + the embedding vars go in .env
alembic upgrade head          # migrations/versions/ → real tables
python scripts/seed_dev.py    # the department/team/user rows the FKs need
uvicorn app.main:app --reload # /docs for the OpenAPI page
pytest                        # app/tests
```

`Base.metadata.create_all()` is deliberately never called — the tables on a
server are the ones the migrations created. See
[migrations.md](migrations.md) and [getting-started.md](getting-started.md).
