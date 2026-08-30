<p align="center">
  <img src="public/images/Context%20Core%20cover.png" alt="Context Core" width="100%" />
</p>

<h1 align="center">Context Core</h1>

<p align="center">
  <strong>Organizational Context Engine for Software Product Companies</strong>
</p>

<p align="center">
  <img src="https://shieldcn.dev/badge/Python-3.11+-blue?logo=python&variant=secondary" alt="Python 3.11+" />
  <img src="https://shieldcn.dev/badge/FastAPI-0.1-green?logo=fastapi&variant=secondary" alt="FastAPI" />
  <img src="https://shieldcn.dev/badge/PostgreSQL-pgvector-blue?logo=postgresql&variant=secondary" alt="PostgreSQL + pgvector" />
  <img src="https://shieldcn.dev/badge/SQLAlchemy-2.0-red?logo=sqlalchemy&variant=secondary" alt="SQLAlchemy 2.0" />
  <img src="https://shieldcn.dev/badge/Azure-OpenAI-blue?logo=microsoftazure&variant=secondary" alt="Azure OpenAI" />
  <a href="https://github.com/foverokavindz/context-core-api"><img src="https://shieldcn.dev/github/last-commit/foverokavindz/context-core-api?variant=secondary" alt="last commit" /></a>
</p>

---

## Overview

Context Core connects repositories, tickets, documentation, discussions, internal policies and
organizational knowledge into a single **role-aware context layer**. New and existing engineers use
it to understand projects, requirements, architecture, historical decisions and change impact
through evidence-backed answers — with citations, scoped to what their role is actually allowed to
see.

HR controls and maintains the authoritative documents. AI agents reach the same engine through MCP
or REST and receive a compact, task-specific context package, eliminating the repeated work of
searching, ranking, validating and synthesizing information across multiple systems.

**This is not** a typical chatbot, enterprise search, document RAG, or a coding assistant.

This repository is the **ingestion and retrieval engine** — the connectors, the pipeline, the vector
store and the REST API. The web client lives in
[context-core-client](https://github.com/foverokavindz/context-core-client).

---

## At a glance

**The pipeline** — how an external system becomes a chunk with a vector, and how a question becomes
an answer with citations.

![Pipeline architecture](public/digrams/Pipelines%20architecture%20digram.svg)

**The data model** — every table the engine writes, and how the permission columns reach the chunk.

![Database tables](public/digrams/Database%20table%20digram.svg)

---

## The Problem

> **Knowledge exists. Context does not.**

Software product companies generate knowledge continuously — across code, documents, tickets, chats,
emails and databases. And it isn't only engineering: HR, marketing, R&D, customer feedback,
operational records and internal meetings all produce it too. The *connections* between all of it
live only in the experience of individual employees.

That knowledge is scattered across systems, constantly changing, and disconnected. Requirements,
implementations, reasoning and decisions each live somewhere different, and nothing records the link
between them — so people rebuild that connection by hand, before every task. AI adoption makes this
worse rather than better: an agent handed access to your tools still has to search every system,
retrieve far too much, judge relevance, re-rank, filter and compress. It pays the same cost as a
person — in tool calls, latency and tokens — on every single run.

**Knowledge is generated → it fragments → people rebuild context, repeatedly → time, knowledge and
decision quality are lost → AI multiplies the cost.**

The symptoms: scattered knowledge · repeated effort · frequent interruptions to seniors ·
dependency on experienced employees · slow onboarding · reduced productivity · poor AI context.

---

## What the Business Actually Gets

| Value | What it means |
| --- | --- |
| **Faster onboarding** | New engineers explore architecture, decisions and ownership on their own — without waiting on someone senior. |
| **Fewer expert interruptions** | Tribal knowledge becomes self-serve, so routine questions stop reaching your most experienced people. |
| **A unified understanding** | Doesn't replace GitHub, Jira, Slack or Confluence — it acts as a supportive context layer above them. |
| **Less context-gathering time** | Instead of manually combining information, users get connected, evidence-backed answers with citations. |
| **Role-specific AI assistance** | Context is served according to the user's role and the privileges they actually hold. |
| **Supports curiosity** | Engineers can dive deep into the product to build domain knowledge, with fewer senior interruptions. |
| **Continuously refreshed context** | Sources synchronize through scheduled ingestion runs and webhook triggers. |
| **Reduced agent token usage** | A compact, task-specific package replaces the repeated work of searching, ranking, validating and synthesizing. |
| **Foundation for org intelligence** | Connecting engineering, HR and business systems into one layer becomes something leadership can eventually query. |

---

## Core Features

- **Workspace, Departments, Teams & Roles** — teams, projects, users and access scopes; the boundary every query is evaluated against, giving permission-aware answers.
- **Core Ingestion** — repository import (or live GitHub), ticket import, Markdown/text/PDF upload and HR docs, with job tracking, retries and re-indexing.
- **Traceability** — every piece of content carries source type, owner, location, version, authority, update date, access scope and processing state.
- **Indexing** — embeddings via pgvector, keyword/full-text search, metadata filters and incremental handling of unchanged content.
- **Hybrid Retrieval** — keyword + vector + metadata + optional relationship expansion, with merge, dedupe and ranking.
- **Project & Component Understanding** — onboarding and feature explanations that connect code with non-code evidence (docs, tickets, decisions).
- **Ticket Context Package** ⭐ — *the core differentiator*: a structured package per ticket covering requirements, code, decisions, dependencies, risks, tests and ownership.
- **Agent REST API / MCP Connection** — connect an agent from an IDE or anywhere: scoped agent identity and key, task input, token budget, structured JSON output and request logging.
- **HR Document Management** — upload, approval and currentness tracking, visibility control, versioning and re-index.
- **Evaluation & Observability** — retrieval, citation and permission metrics, plus traces, latency and token logs.

---

## Feature Status

Honest state of this engine today. Every ✅ names the module that proves it.

| Area | Status | Where it lives |
| --- | --- | --- |
| Ingestion — GitHub, Jira, Confluence, Slack | ✅ **Shipped** | `app/connectors/`, `app/ingestion/`, `app/background/pipeline/` |
| Embedding & storage — 1536-dim vectors in pgvector | ✅ **Shipped** | `app/ingestion/embedding_service.py`, `app/repository/chunk_repository.py` |
| **Organization — roles, departments, teams** | ✅ **Shipped** | `app/controllers/{workspace,department,team,employee}_controller.py` |
| Authentication — JWT login and `/auth/me` | ✅ **Shipped** | `app/services/token_service.py`, `app/core/auth_dependencies.py` |
| Permission-scoped retrieval | ✅ **Shipped** | `app/repository/chunk_repository.py` — scope filtered in SQL, *before* ranking |
| Chat — analyse → plan → execute → answer, with citations and a retrieval trace | ✅ **Shipped** | `app/retrieval/pipeline.py`, `app/services/chat_service.py` |
| Data source & sync-run visibility | ✅ **Shipped** | `app/controllers/data_source_controller.py` |
| Hybrid retrieval — the keyword half | 🚧 **Vector only today** | `app/retrieval/search/knowledge_search_service.py` — cosine distance; no BM25, no fusion yet |
| Reranking | 🚧 Coming soon | Deliberately absent — see [Architecture](#architecture) |
| ANN index on `chunks.embedding` | 🚧 Coming soon | Sequential scan for now, deferred until there is data to tune against |
| Authentication on every endpoint | 🚧 Coming soon | Only `/auth/me` is protected; see the limitation note under [Endpoint Walkthrough](#endpoint-walkthrough) |
| Document / HR portal ingestion | 🚧 Coming soon | `app/entities/documents/` exists; no controller yet |
| Agent REST API / MCP connection | 🚧 Coming soon | Planned as a wrapper over the proven REST endpoints |
| Automated evaluation harness | 🚧 Coming soon | Testing to date is manual and scenario-based — see [Evaluation](#evaluation) |

---

## Demo & Links

The demo media and the design prototype live with the client repository.

| | |
| --- | --- |
| 🎬 **Product demonstration** | [context-core-demo.mp4](https://github.com/foverokavindz/context-core-client/blob/main/public/assets/videos/context-core-demo.mp4) *(~50 MB)* |
| 🎬 **Prototype walkthrough** | [prototype-walkthrough.mp4](https://github.com/foverokavindz/context-core-client/blob/main/public/assets/videos/prototype-walkthrough.mp4) |
| 🎨 **Interactive prototype** | [View the prototype](https://claude.ai/code/artifact/829d399d-8f59-4a43-9266-b38ff53aebd6) |
| 🖥️ **Screenshots** | [context-core-client README](https://github.com/foverokavindz/context-core-client#demo--screens) |

---

## Architecture

A FastAPI **modular monolith** in two halves that meet in PostgreSQL:

- **Ingestion** — connect an external system, pull its items, flatten them to text, cut them into
  chunks, embed the chunks, and persist `resources` + `chunks`.
- **Retrieval** — understand a question, plan what to search for, run that plan against the four
  source retrievers, and write an answer from what came back.

**Why one Postgres with `pgvector` rather than a separate vector database.** A run's resources, its
chunks, its sync-run status and the source's `last_synced_at` all land in **one transaction** — a
half-ingested corpus is never visible. Permission columns are copied onto `chunks` so access scope
is filtered in SQL *before* ranking, rather than retrieving broadly and discarding afterwards. And
there is one thing to run, back up and reason about. The cost is that vector search is a sequential
scan until an ANN index is added; that is deliberate while the corpus is small enough to make
correctness easier to see than speed.

### Layering

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

Two rules hold it up:

1. **Only `repository/` touches `entities/` as tables.** No connector, parser or chunker imports a
   table; the four pipelines end at a Pydantic DTO.
2. **No repository commits.** The caller owns the transaction — which is what lets a whole run land
   together.

### Ingest

```
POST /api/v1/ingestData/{source}
  ingestion_controller   resolve SourceType · validate config
  ingestion_service      ExternalDataSource + PENDING SyncRun · commit
  → 202 { external_data_source_id, sync_run_id }
  ingestion_pipeline     RUNNING → connector → parser → chunker → embedder
                         → PermissionScope stamped on every item and chunk
                         → resources + chunks + sync run COMPLETED
                         (one transaction, its own session)
```

### Answer a question

```
POST /api/v1/chats/{id}/query
  chat_service           the question is stored and committed FIRST
  prompt_processing      one model call  → PromptAnalysis
                         resolved query, intent, entities, information needs
  planning               one model call  → RetrievalPlan (≤ 4 steps)
                         repair_plan(): dedupe ids, drop cycles, clamp top_k
  execution              ready steps run together on a thread pool
    query_enricher       one model call per dependent step
    the four retrievers  embed the query → pgvector cosine search,
                         scope-filtered in SQL before ranking
  answering              one model call  → GeneratedAnswer, Markdown, cited
  ← the answer is stored as a second message and returned with its
    sources in citation order and a RetrievalTrace of how it got there
```

The one stage still missing between search and answer is **reranking**: what the search returns is
what the model is shown, in plan order, with nothing dropped for being weak. That is deliberate for
now — it is the only way to tell, from a disappointing answer, whether the fault was a bad plan, a
bad search or a bad read of good sources. The `RetrievalTrace` on the response exists for the same
reason, and is expected to go behind a flag once the pipeline is trusted.

> **Note:** `app/api/` holds four per-source debug routers that run one connector synchronously and
> return the whole run inline. They are **commented out in `app/main.py`** and are not part of the
> live surface — useful when developing a single connector, useless against a real repository.

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.11+ (verified on 3.14.5) |
| Web framework | FastAPI + Uvicorn |
| Validation & DTOs | Pydantic v2 |
| ORM | SQLAlchemy 2.0 (`Mapped` / `mapped_column`) |
| Migrations | Alembic |
| Database | PostgreSQL with the `vector` extension |
| Vector search | `pgvector` — cosine distance over `vector(1536)` |
| Driver | `psycopg` 3 (`psycopg[binary]`) |
| Embeddings | Azure OpenAI via the `openai` SDK on the OpenAI-compatible base URL |
| Chat model | `langchain-openai` `ChatOpenAI`, pointed at the Azure deployment |
| LLM orchestration | `langchain-core` — structured output for four pipeline stages |
| Code parsing | Tree-sitter + `tree-sitter-typescript` |
| Connectors | PyGithub (GitHub), httpx (Jira, Confluence, Slack), BeautifulSoup |
| Auth | PyJWT (HS256) + `pwdlib[argon2]` |
| Tests | pytest — 1,550 tests, no network, no credentials (see [Evaluation](#evaluation)) |
| Frontend | [context-core-client](https://github.com/foverokavindz/context-core-client) — React 19, TypeScript, Vite, MUI |

---

## Getting Started

### Prerequisites

- **Python 3.11+** (the venv used in development is 3.14.5 on Windows)
- **PostgreSQL** with the `vector` extension available — `CREATE EXTENSION IF NOT EXISTS vector;`
- **An Azure OpenAI resource** with two deployments: one embedding model producing **1536-dimension**
  vectors, and one chat model
- **Connector credentials** for whichever sources you intend to ingest — a GitHub token, an Atlassian
  email + API token for Jira and Confluence, a Slack bot token

### Install and run

```bash
git clone https://github.com/foverokavindz/context-core-api.git
cd context-core-api

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
cp .env.example .env            # then fill it in

alembic upgrade head            # migrations/versions/ -> real tables
python scripts/seed_dev.py      # the department / team / user rows the FKs need
uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

> **The migrations are what build the schema.** `Base.metadata.create_all()` is deliberately never
> called, and [`docs/schema.sql`](docs/schema.sql) is a regenerated *reference dump* of the mappers —
> read it, don't run it.

### Environment variables

Names and purposes only — values belong in your own `.env`, which is git-ignored.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string. Read in `app/core/db/session.py`. |
| `AZURE_OPENAI_BASE_URL` | The Azure OpenAI-compatible base URL, shared by embeddings and chat. |
| `AZURE_OPENAI_API_KEY` | Key for that resource. |
| `AZURE_OPENAI_DEPLOYMENT` | The **embedding** deployment. Must produce 1536-dim vectors — a mismatch raises `EmbeddingError`. |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | The **chat** deployment used by all four model-calling stages. |
| `JWT_SECRET` | Signing secret for access tokens. |
| `JWT_ALGORITHM` | Defaults to `HS256`; `HS384` and `HS512` are also accepted. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime. Defaults to 7 days. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origins. Defaults to the Vite dev ports (5173 / 5174). |

---

## Scripts

| Command | What it does |
| --- | --- |
| `uvicorn app.main:app --reload` | Start the API with hot reload on `:8000` |
| `alembic upgrade head` | Apply every migration in `migrations/versions/` |
| `alembic revision --autogenerate -m "..."` | Generate a migration from the entities |
| `pytest` | Run the suite (`testpaths` is already set to `app/tests`) |
| `pytest app/tests -v` | The same, verbose, one line per test |
| `python scripts/seed_dev.py` | Seed the department / team / user rows ingestion's NOT NULL FKs need. Fixed ids, safe to re-run, dev only |
| `python scripts/hash_password.py` | Produce an argon2 hash for seeding a user by hand |

---

## Endpoint Walkthrough

Every route is mounted under **`/api/v1`**. Responses share one envelope — see
[API response contract](#api-response-contract) below.

**Set the organization up first.** Ingestion's foreign keys need a workspace, a department, a team
and a user to exist:

```http
POST /api/v1/workspace      → 201  the single workspace (409 if one already exists)
POST /api/v1/departments    → 201  name-unique
GET  /api/v1/departments    → 200  all departments
POST /api/v1/teams          → 201  validates the department; unique per (department, name)
GET  /api/v1/teams          → 200  all teams
POST /api/v1/employees      → 201  creates a User (argon2) + TeamMember in one transaction
GET  /api/v1/employees      → 200  user + membership + team, joined
POST /api/v1/auth/login     → 200  email + password, returns a JWT
GET  /api/v1/auth/me        → 200  the bearer token's identity
```

**Connect a source.** One endpoint reaches all four connectors; the per-connector part goes in
`config`, and the credential in its own `token` field:

```http
POST /api/v1/ingestData/{external_source}  → 202 { external_data_source_id, sync_run_id }
```

| `{external_source}` | Required `config` keys |
| --- | --- |
| `github` | `repository` (plus optional `branch`) |
| `jira` | `site_url`, `email`, `project_key` |
| `confluence` | `site_url`, `email`, `space_key` |
| `slack` | `channel_id` |

The body also carries `title`, `team_id`, `department_id`, `access_scope`, `created_by_user_id` and
`source_type` (which must agree with the URL). The endpoint records the connection, queues a
`PENDING` sync run, commits, and answers `202` immediately — the pipeline then runs in the
background and stamps the team, department and access scope onto every resource and chunk.

**Watch the run, then see what got indexed:**

```http
GET /api/v1/syncRuns/{syncRunId}                → 200  one run's status
GET /api/v1/dataSources?team_id={teamId}        → 200  a team's connected sources
GET /api/v1/dataSources/stats?team_id={teamId}  → 200  counts per team
GET /api/v1/dataSources/{id}                    → 200  one source — has_token, never the token
GET /api/v1/dataSources/{id}/syncRuns           → 200  that source's run history
GET /api/v1/dataSources/{id}/resources          → 200  the indexed resources
```

**Ask a question.** Open a session, then query against it:

```http
POST /api/v1/chats                        → 201  creates a chat session
POST /api/v1/chats/{chatSessionId}/query  → 200  the answer
GET  /api/v1/users/{userId}/chats         → 200  chat history
GET  /api/v1/users/{userId}/conversations → 200  conversation summaries
```

The query endpoint runs the whole retrieval pipeline synchronously and holds the connection until
the answer exists — several model calls and a vector search per plan step. Nothing is streamed yet.
The response carries more than prose: the **answer** as Markdown, its **sources in citation order**
(so `[2]` is the second of them), and a **`RetrievalTrace`** recording what was understood, what was
planned, and what each step was actually searched with.

> **Known limitation:** only `GET /api/v1/auth/me` is authenticated. Every other endpoint trusts the
> `user_id`, `team_id` and `department_id` supplied in its request body — and that is what the
> row-level permission filtering is evaluated against. The JWT already carries those claims; wiring
> them through as the `AccessContext` is on the roadmap.

### API response contract

Every application JSON endpoint returns the same outer envelope, with the payload nested under
`data`:

```json
{
  "success": true,
  "data": { "status": "ok" },
  "message": null,
  "error": null,
  "timestamp": "2026-08-24T12:58:18.893367Z"
}
```

Framework errors, request-validation errors and application errors use the same shape with
`success: false`, `data: null`, and a client-safe string in `error`. Every exception carries its own
status code, so `main.py` maps a pipeline failure to the right HTTP status through a single handler.
The `timestamp` is always an ISO 8601 UTC string.

---

## Project Structure

```
app/
├── main.py              FastAPI app, routers, one exception handler per error family, GET /health
├── api/                 four per-source debug routers — COMMENTED OUT in main.py
├── controllers/         the mounted endpoints: ingestion · data sources · chat ·
│                        workspace · departments · teams · employees · auth
├── services/            the use cases; each owns its transaction boundary
├── background/
│   └── pipeline/        ingestion_pipeline.py — everything that happens after the 202
├── connectors/          the outside world: GitHub · Jira · Confluence · Slack
├── ingestion/           flatten, chunk, embed
│   └── parser/          Tree-sitter registry + the TypeScript parser
├── retrieval/           understand the question, plan for it, run it, answer it
│   ├── prompt_processing/   question → PromptAnalysis
│   ├── planning/            analysis → RetrievalPlan (+ deterministic repair)
│   ├── execution/           runs the plan; enriches dependent steps
│   ├── search/              KnowledgeSearchService — embed, then pgvector search
│   ├── retrievers/          one thin entry point per source
│   └── answering/           every retrieved chunk → a cited Markdown answer
├── models/              Pydantic DTOs, never tables — one package per source,
│                        plus common/ · ingestion/ · chat/ · retrieval/
├── entities/            SQLAlchemy rows: organization · teams · data_sources ·
│                        documents · knowledge_sources · chunks · chat
├── repository/          the ONLY place that reads or writes a table
├── core/                exceptions · db/session · db/dependencies · security · auth_dependencies
├── data/                sample responses and runs/<source>_<id>.json snapshots
└── tests/               37 modules, 1,550 tests

alembic/ · migrations/   the Alembic environment and versions/ — what actually builds the schema
scripts/                 seed_dev.py · hash_password.py
docs/                    the legacy documentation set (see below)
public/                  cover image and the two architecture diagrams
```

---

## Roadmap

### 1 — Planned to improve next

- Add the keyword half of hybrid retrieval, then merge and rerank
- An ANN index on `chunks.embedding`, tuned against real corpus size
- Derive the `AccessContext` from the JWT on every endpoint, not from the request body
- Implement document ingestion and the HR document portal
- Prevent content duplication on re-ingestion; idempotency checks for ingestion requests
- More pipeline stages surfaced on the sync-run status
- Retire the 233 stale tests left behind by the commented-out per-source routers

### 2 — Features to be added

- Scheduled knowledge refresh and webhook-triggered sync
- A durable job queue, so a run survives a restart
- Observability support — traces, latency and token logs
- MCP wrapper over the proven REST endpoints
- Answer caching
- Deeper reasoning capabilities, and tools that give agents more capability
- Feedback-based optimization

### 3 — Required before real-world use

- Multi-tenant support
- Microsoft AD integration for workspaces
- Encrypted credential storage instead of a plain-text token on the source
- More external connectors for broader compatibility
- Improved performance, accuracy, token efficiency and robustness

---

## How AI Helped Build This

- **Research partner** — Perplexity / ChatGPT / Claude / Gemini: deep research and brainstorming, identifying multiple possible scenarios and viewing the problem from perspectives that would have been hard to reach alone inside a hackathon timeline.
- **Planner** — ChatGPT: MoSCoW scoping, a four-week plan, engineering scenarios and acceptance criteria, risks and trade-offs, epics and user stories.
- **Learning and AI engineering** — ChatGPT / Claude: technical mentor while learning and implementing unfamiliar AI-engineering concepts.
- **Realistic test data** — Codex: generating the realistic organizational dataset needed to test Context Core.
- **UI, branding and product design** — Lovable / Claude Design: exploring design iterations and prototyping core screens before spending development time on frontend implementation.
- **Implementation & testing** — Claude Code / Codex + sub-agents: a spec-driven workflow — give the spec with objective, acceptance criteria and use cases, require a plan before any code.

---

## Evaluation

**A controlled environment, not a live company.** A full dummy project (TrackIT) was built from
scratch — code repos, epics, tickets, Confluence docs and simulated Slack threads — written around
consistent feature scenarios, so every retrieval result had a known-correct answer to check against.

**Persona-based scenario testing.** Evaluation scenarios were written for two personas — a new
joiner and an existing engineer — then queried manually and read end to end.

**Stage-by-stage pipeline inspection.** Every ingestion run writes a full JSON snapshot per source
under `app/data/runs/`, used to eyeball real chunk content and metadata before trusting it. Each
source — GitHub, Jira, Confluence, Slack — has dedicated tests per pipeline stage, followed by a
full end-to-end pass.

**An automated suite that needs nothing.** `pytest` collects **1,550 tests across 37 modules** with
no network access and no credentials — connectors are exercised through `httpx.MockTransport` and
fakes, so the whole surface stays testable without an Atlassian site or a Slack workspace.

**Connector and permission checks, done by hand.** Real external connections (GitHub, Jira,
Confluence, Slack) were verified manually, and permission-scoped retrieval was confirmed to behave
correctly across roles.

### The suite is not green

**1,317 pass and 233 fail** on a clean run. The failures are known and clustered, not scattered:

| Modules | Failures | Why |
| --- | --- | --- |
| `test_api.py`, `test_jira_api.py`, `test_confluence_api.py`, `test_slack_api.py` | 224 | They exercise the four per-source debug routers, which are **commented out in `app/main.py`** — so every request 404s. The tests were never updated when the routers came down. |
| `test_ingestion_controller.py` | 8 | Signature drift — the tests still pass `session_factory=` to `run_ingestion_pipeline()`. |
| `test_api_response.py` | 1 | A CORS assertion that no longer matches the configured origins. |

None of these track a defect in the live surface — the ingestion pipeline, retrieval, chat,
organization and auth modules all pass. They are stale tests for a route set that was deliberately
retired, and clearing them is on the roadmap. Quoting a green suite here would be more flattering
and less true.

### What's not measured yet

No formal ranking, precision/recall scoring, token-usage tracking or cost calculation — that's the
honest gap. Time went into getting ingestion and retrieval solid end-to-end and shipping a working
frontend, over building an automated eval harness.

---

## Challenges & Honest Limitations

- **Code chunking & language support** — splitting code files while preserving syntactic structure needs language-aware parsing. Only TypeScript is supported today, through a language-support library.
- **Four separate connector integrations** — GitHub, Jira, Confluence and Slack each have different APIs, auth models and data shapes. Each had to be understood, connected and fitted into a common ingestion contract.
- **Learning while building** — the ingestion and retrieval pipeline was implemented while simultaneously learning the underlying AI concepts and tooling, within a month.
- **Simulated dataset instead of a real project** — a dummy dataset (TrackIT) was used rather than integrating a real-world project, to keep cross-source complexity manageable within MVP scope.
- **Onboarding a new source isn't plug-and-play** — every additional source needs its own configuration, auth, field mapping and chunking rules before it fits the shared pipeline.
- **LLM budget constraints** — development and testing ran on smaller, cheaper models rather than frontier models due to credit limits; a constraint on iteration speed and eval quality, not just cost.
- **Token-efficient pipeline engineering** — every stage was deliberately engineered to minimize token spend: batching embeddings, and keeping permission filtering, exact-ID lookup and merging entirely deterministic and off the LLM, calling the model only for synthesis. This is what makes the product's own token-saving claim real.
- **Retrieval is vector-only so far** — the search is pgvector cosine distance. The keyword half of "hybrid", the merge and the rerank are designed but not built, so a question whose answer hinges on an exact identifier can still be out-searched by a semantically closer neighbour.
- **No ANN index yet** — `search_by_embedding` is a sequential scan, deferred on purpose until there is enough real data to tune an index against. Correct first, fast second.
- **The connector token is stored in plain text** on the external data source, and there is no credential table. It is never logged, never returned and never written to a run file — but it is not encrypted at rest.
- **The background run is FastAPI's own `BackgroundTasks`** — it does not survive a restart, and there is no queue, no retry and no incremental sync behind it.

---

## Legacy documentation

The `docs/` set was written during earlier phases of the project, when this was an ingestion-only
service. Several pages still describe that earlier state — `docs/README.md` opens with "stage one of
a RAG ingestion pipeline", and `docs/testing.md` still quotes 1,192 tests. **This README is the
current source of truth**; the pages below remain useful for the depth they go into.

| Page | What's in it |
| --- | --- |
| [docs/README.md](docs/README.md) | The original documentation index |
| [docs/getting-started.md](docs/getting-started.md) | Install, run the API, run the tests |
| [docs/project-structure.md](docs/project-structure.md) | Folder by folder, the layering rules, and the two live request flows in detail |
| [docs/architecture.md](docs/architecture.md) | Why the four source pipelines are kept separate, and where each one's boundary sits |
| [docs/entity-reference.md](docs/entity-reference.md) | Condensed table-by-table map: every table, column, enum and relation |
| [docs/entities.md](docs/entities.md) | The long-form rationale behind the database layer, and DTOs versus rows |
| [docs/ingestion-endpoint.md](docs/ingestion-endpoint.md) | `POST /api/v1/ingestData/{source}` — request shape, per-source `config`, what the run writes |
| [docs/migrations.md](docs/migrations.md) | The Alembic setup, and how it relates to `schema.sql` |
| [docs/schema.sql](docs/schema.sql) | A regenerated DDL dump of the mappers — reference, not the migration mechanism |
| [docs/logging.md](docs/logging.md) | What a run prints per source, and why log volume tracks the unit that costs a round trip |
| [docs/security.md](docs/security.md) | How connector tokens are held as `SecretStr`, and what never reaches a log or a response |
| [docs/testing.md](docs/testing.md) | The suite and the fakes / `httpx.MockTransport` strategy behind it |
| [docs/todo.md](docs/todo.md) | Invariants the schema deliberately leaves to the service layer |
| [docs/connectors/github.md](docs/connectors/github.md) | File filtering rules, Tree-sitter parser behaviour, source fidelity, errors |
| [docs/connectors/jira.md](docs/connectors/jira.md) | Scoped tokens, the JQL, Epic ↔ Story linking without N+1 calls, ADF flattening |
| [docs/connectors/confluence.md](docs/connectors/confluence.md) | Space resolution and confinement, cursor pagination, storage-format flattening |
| [docs/connectors/slack.md](docs/connectors/slack.md) | Scopes, channel confinement, which messages become chunks, ordering |
| [docs/postman/context-core.postman_collection.json](docs/postman/context-core.postman_collection.json) | A Postman collection for the endpoints |

Every connector page ends with a checklist for verifying that source against a real repository,
project, space or channel.

---

## Related Repositories

| Repository | Description |
| --- | --- |
| [context-core-client](https://github.com/foverokavindz/context-core-client) | Web client — chat with citations and the retrieval trace, data-source management, and the product screens |
