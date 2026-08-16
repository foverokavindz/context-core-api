# Documentation

[← Project README](../README.md)

Stage one of a RAG ingestion pipeline: four independent sources, each ending in
chunks that a later phase can embed.

## Start here

| Page | What's in it |
| --- | --- |
| [getting-started.md](getting-started.md) | Install, run the API, run the tests |
| [architecture.md](architecture.md) | Module layout, the boundaries each pipeline stops at, why the four are kept separate |
| [ingestion-endpoint.md](ingestion-endpoint.md) | `POST /api/v1/ingestData/{source}` — the one endpoint all four sources share, what it records, and what runs after it answers |

## The four sources

Each page is self-contained: how to call the endpoint, what gets fetched, how
the source format is flattened, how it is chunked, the error statuses, and a
checklist for verifying it against a real account. These four endpoints answer
with a whole run inline; the common endpoint above answers immediately and
ingests afterwards.

| Page | Endpoint | Ends in |
| --- | --- | --- |
| [connectors/github.md](connectors/github.md) | `POST /api/v1/github/ingest` | `CodeChunk[]` |
| [connectors/jira.md](connectors/jira.md) | `POST /api/v1/jira/ingest` | `JiraChunk[]` |
| [connectors/confluence.md](connectors/confluence.md) | `POST /api/v1/confluence/ingest` | `ConfluenceChunk[]` |
| [connectors/slack.md](connectors/slack.md) | `POST /api/v1/slack/ingest` | `SlackChunk[]` |

## Across all four

| Page | What's in it |
| --- | --- |
| [logging.md](logging.md) | What a run prints, and why log volume tracks the unit that costs a round trip |
| [security.md](security.md) | How tokens are held, and what never reaches a log or a response |
| [testing.md](testing.md) | The 1,192-test suite and what each module covers |

## Beyond ingestion

| Page | What's in it |
| --- | --- |
| [entities.md](entities.md) | The database layer: departments, job titles, users, teams, connected data sources, uploaded documents, the resources and chunks both produce, and the chat sessions and citations built on top of them |
| [todo.md](todo.md) | The rules those tables leave to a service layer that does not exist yet |
