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
- **Retrieval** — understand a question, and (in time) answer it from what
  ingestion stored. Three stages exist so far: the prompt processor, which turns
  a question into a structured `PromptAnalysis`; the planner, which turns that
  into a `RetrievalPlan`; and the executor, which runs that plan against the
  four source retrievers. Those retrievers are entry points only — they find
  nothing yet, so nothing is retrieved or generated.

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
only the ingestion and chat routers are mounted. Retrieval has no endpoint
yet; it is reached from Python.

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
| `chat_controller.py` | `POST /api/v1/chats`, `POST /api/v1/chats/{id}/query` | Open a conversation, and record a question against one. No answer is generated yet |

Controllers know which sources exist and what a valid request looks like. They
know nothing about chunks.

### `app/services/` — use cases
| File | Job |
| --- | --- |
| `ingestion_service.py` | `start_ingestion(...)`: write the `ExternalDataSource`, queue a `PENDING` `SyncRun`, **commit**, hand the work to `BackgroundTasks`, return the two ids. An `IntegrityError` here means a bad `team_id`/`department_id`/`created_by_user_id` → 400 |
| `chat_service.py` | `create_chat(...)` / `send_query(...)`: the conversation itself — open a session, record a question against one the caller owns |
| `retrieval_service.py` | `start(...)`: hands a question and an `AccessContext` to the retrieval pipeline and returns the `RetrievalPipelineResult` — the analysis, and the plan and execution when retrieval was needed. `build_retrieval_service()` wires the default stack, sharing one chat model across all three model-calling stages. The one service that owns no transaction — it reads and writes no table |

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

### `app/retrieval/` — understand the question, then plan for it

| File | Job |
| --- | --- |
| `llm.py` | `build_chat_model()`: the chat model, from `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_CHAT_DEPLOYMENT`. A plain `ChatOpenAI` and not `AzureChatOpenAI`, for the reason `embedding_service.py` uses `OpenAI` and not `AzureOpenAI` — the base URL is Azure's OpenAI-compatible `/openai/v1` surface. A function rather than a module-level instance, so importing the package needs no credentials |
| `pipeline.py` | `RetrievalPipeline.run(...)`: the stages, in order. Three so far, and the planner and executor are both skipped outright when the analysis says no retrieval is required |
| `prompt_processing/prompt.py` | The system prompt: what the four sources hold, what this stage may and may not decide, and four worked examples. Written brace-free, because `ChatPromptTemplate` reads braces as variable syntax |
| `prompt_processing/processor.py` | `PromptProcessor.process(query, history)`: one model call, structured straight into `PromptAnalysis`. Takes its model rather than building one |
| `planning/prompt.py` | The planner's system prompt: what each source is best for, how information needs and entities shape a step, and three worked plans. Brace-free for the same reason |
| `planning/planner.py` | `RetrievalPlanner.plan(analysis)`: one model call, the analysis sent as JSON, structured straight into `RetrievalPlan`. Decides what to retrieve and retrieves nothing |
| `planning/validator.py` | `repair_plan(plan)`: the deterministic half. Drops duplicate step ids, steps past `MAX_STEPS` (4), self and dangling dependencies and any edge that closes a cycle, and clamps `top_k` to 5–15. It corrects rather than raises — a request is never failed over a fixable plan |
| `execution/executor.py` | `RetrievalExecutor.execute(plan, analysis, access)`: runs the plan and decides nothing about it. Repeatedly takes the steps whose dependencies have all completed, runs them together on a `ThreadPoolExecutor`, and records each as a `StepExecutionResult`. Fails fast — an unrunnable dependency, an unregistered source or a retriever that raises all become a `RetrievalExecutionError` rather than half an answer |
| `execution/prompt.py` | The enricher's system prompt: which terms are worth taking out of a dependency's results, and the instruction to improve a base query rather than replace it. Brace-free for the same reason |
| `execution/query_enricher.py` | `QueryEnricher.enrich(request)`: one model call, structured straight into `EnrichedQuery`. Only ever called for a step with a `depends_on` — an independent step already has the query the planner wrote |
| `retrievers/base.py` | `SourceRetriever` — a `Protocol`, not a base class, for the reason `EmbeddableChunk` is one. One method: `retrieve(query, top_k, access)` |
| `retrievers/{jira,github,slack,confluence}_retriever.py` | The four entry points. Each returns `[]`: the vector search, the chunk repository and the access filtering behind them are the next milestone, and an empty result is honest until there is an index to search |

The person's own words never pass through the prompt template — the history and
the question are both `MessagesPlaceholder`s, so a question containing a brace is
asked rather than raising. History is the caller's to supply: the last
`MAX_HISTORY_MESSAGES` (6) turns travel with the question, nothing is summarized,
and nothing is loaded from the database here.

### `app/models/` — Pydantic DTOs (never tables)
One package per source with the same four names, plus the shared and
source-agnostic ones:

| Package | Contents |
| --- | --- |
| `common/` | `PermissionScope` (`team_id`, `department_id`, `access_scope`, `external_data_source_id`), `EmbeddingCounts` |
| `github/` `jira/` `confluence/` `slack/` | `request.py`, the boundary type (`file`/`issue`/`page`/`message`), `chunk.py`, `response.py` + limits |
| `ingestion/` | `IngestDataRequest` + `REQUIRED_CONFIG_KEYS`, `IngestStartedResponse` |
| `chat/` | `CreateChatRequest` / `SendQueryRequest` (`query` ≤ 4,000 chars) and their responses, `ChatMessage` (one turn, as the pipeline reads history), `LLMConfig` |
| `retrieval/` | `PromptAnalysis` + `ExtractedEntity`, `RetrievalPlan` + `RetrievalStep`, `RetrievalResult` (the contract the four retrievers will answer in — the chunk columns plus its resource's title, and deliberately no embedding), `StepExecutionResult` + `RetrievalExecutionResult`, `QueryEnrichmentInput` + `EnrichedQuery`, `AccessContext` (who is asking; carried, never yet acted on), and the `RetrievalPipelineResult` carrying the analysis, the plan and the execution, over `retrieval/ontology/` — the three closed vocabularies the prompt processor answers in: `QueryIntent`, `EntityType`, `InformationNeed`. Not database enums — nothing stores them, so they live with the DTO rather than in `entities/`. Every field description is written for the model to read: they become the JSON schema `with_structured_output` sends |

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
| `exceptions.py` | `IngestionError` base (`status_code`, `message`) and ~25 subclasses — auth/permission/not-found/rate-limit/API per source, plus `EmbeddingConfigurationError`, `EmbeddingError`, `LLMConfigurationError`, `LLMError`, `RetrievalExecutionError`, `EmptyQueryError`, `DatabaseConfigurationError`. Every message is written to be safe to hand to a client |
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

**Understand a question** (no endpoint yet — called from Python)
```
RetrievalService.start(query, access, history)
  retrieval/pipeline     two stages so far
  prompt_processing      system prompt + the last 6 turns + the question
  the chat model         one call, structured output
  → PromptAnalysis       resolved + improved query, intent, entities,
                         information needs, explicitly named sources
  planning               stops here when retrieval_required is false
  the chat model         one call, the analysis as JSON, structured output
  → repair_plan          duplicate ids, bad dependencies, cycles, top_k
  execution             the ready steps, together, then the ones that were
                         waiting on them
  query enrichment       one call per dependent step: what its dependencies
                         found becomes the query it is searched with
  the four retrievers    entry points — they find nothing yet
  ← RetrievalPipelineResult
                         the analysis, a RetrievalPlan of at most four steps
                         (source, goal, query, top_k, depends_on), and a
                         RetrievalExecutionResult recording what each step was
                         actually searched with and what came back
```

The plan is never mutated by the run. `RetrievalStep.query` stays as the planner
wrote it and the runtime query lives on `StepExecutionResult.executed_query`,
which is what makes a disappointing answer traceable to the search that produced
it.

The stages after them — the retrievers' own vector search, reranking, context
building, answer generation — are not written. Each stage deliberately
makes none of the next one's decisions: the prompt processor never chooses a
source from a topic and never orders work, the planner never retrieves, the
executor never changes a plan or chooses a source, and none of them invents a
ticket, repository or service the question did not mention.

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
