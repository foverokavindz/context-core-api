# Getting started

[← Documentation](README.md)

## Installation

Requires Python 3.11+. Verified on Python 3.14.5 (Windows), where every
dependency — including the Tree-sitter grammars — installs from a prebuilt
wheel, so no C compiler is needed.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
```

## The database

**Required.** `POST /api/v1/ingestData/{external_source}` writes rows now, and
the app refuses to start without a connection string rather than failing at the
first request.

Put it in `.env` at the repository root, which is gitignored:

```
DATABASE_URL=postgresql+psycopg://user:password@host:5432/contextcore
JWT_SECRET=replace-with-a-long-random-secret
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
CORS_ALLOWED_ORIGINS=http://localhost:5174
```

`JWT_SECRET` is required when calling the authentication endpoints. The
algorithm and access-token lifetime are optional and use the values shown.
`CORS_ALLOWED_ORIGINS` is an optional comma-separated list; local Vite origins
on ports 5173 and 5174 are allowed by default. Never commit the secret.

Then create the schema:

```bash
alembic upgrade head
```

That needs a PostgreSQL server with the `vector` extension available — the first
migration creates it, which takes a superuser or `rds_superuser` on RDS. See
[migrations.md](migrations.md).

### Seeding

`external_data_sources`, `resources` and `chunks` all carry foreign keys onto
`teams`, `departments` and `users`, and the ingestion endpoint still takes those
ids from the request body. So a fresh database needs one of each before it will
accept an ingestion:

```bash
python scripts/seed_dev.py
```

It creates one department, one user and one team under fixed ids, makes that
user the team's `TEAM_LEAD` member, prints the ids ready to paste into a request
body, and is safe to run twice. The development login is
`dev@contextcore.local` with the `DEV_SEED_PASSWORD` environment value, which
defaults to `Temporary123!`; the database stores only its Argon2 hash.

## Running it

```bash
uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Login with `POST /api/v1/auth/login`, then send the returned token as
`Authorization: Bearer <token>`. `GET /api/v1/auth/me` returns the current
database-backed user profile.

## Calling an endpoint

Each source has its own endpoint and its own page, covering the request fields,
the response shape, the error statuses and a by-hand verification checklist:

| Source | Endpoint | Page |
| --- | --- | --- |
| GitHub | `POST /api/v1/github/ingest` | [connectors/github.md](connectors/github.md) |
| Jira | `POST /api/v1/jira/ingest` | [connectors/jira.md](connectors/jira.md) |
| Confluence | `POST /api/v1/confluence/ingest` | [connectors/confluence.md](connectors/confluence.md) |
| Slack | `POST /api/v1/slack/ingest` | [connectors/slack.md](connectors/slack.md) |

Swagger UI at `/docs` is usually the easiest way to try one: open the endpoint,
**Try it out**, fill in the fields, **Execute**.

> **Never commit a real token.** Every endpoint takes its credential in the
> request body, at call time. Nothing is read from a file, written to one, or
> stored anywhere — see [security.md](security.md).

## Running the tests

```bash
pytest app/tests -v
```

1,079 tests, no network access and no credentials required. See
[testing.md](testing.md) for what each module covers.
