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

## Running it

```bash
uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

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
