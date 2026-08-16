# Context Core — Ingestion API

Stage one of a RAG ingestion pipeline. Four independent sources, each ending in
chunks that a later phase can embed.

**GitHub** takes a token and a repository, pulls the TypeScript source out
through the GitHub REST API, filters the noise, and parses what is left into
logical code chunks with Tree-sitter.

```
HTTP request  ->  GitHub connector  ->  repository tree
                                             |
                                        file filter
                                             |
                                    fetch accepted files
                                             |
                                       RepositoryFile
                                             |
                                   Tree-sitter parser
                                             |
                                        CodeChunk[]
                                             |
                                    debug JSON response
```

**Jira** takes Atlassian credentials and a project key, pulls that project's
Epics and Stories through the Jira Cloud REST API, flattens their Atlassian
Document Format descriptions into plain text, and resolves Epic ↔ Story links.

```
HTTP request  ->  Jira connector  ->  Jira Cloud REST v3
                                             |
                                   Epics + Stories (paged)
                                             |
                                        JiraParser
                                             |
                                        JiraIssue[]
                                             |
                            parent/child relationship construction
                                             |
                                        JiraChunker
                                             |
                                        JiraChunk[]
                                             |
                                    debug JSON response
```

**Confluence** takes Atlassian credentials and a space key, resolves that space,
pulls only its pages through the Confluence Cloud REST v2 API, and flattens their
storage-format markup into readable plain text.

```
HTTP request  ->  Confluence connector  ->  Confluence Cloud REST v2
                                             |
                                        resolve space
                                      TR -> space id 6422530
                                             |
                                 pages in that space (cursored)
                                             |
                                     ConfluenceParser
                                             |
                                storage XHTML -> plain text
                                             |
                                     ConfluencePage[]
                                             |
                                     ConfluenceChunker
                                             |
                                     ConfluenceChunk[]
                                             |
                                    debug JSON response
```

**Slack** takes a bot token and one channel ID, reads that channel's message
history through the Slack Web API, and keeps the messages a person or an app
actually wrote — dropping thread replies, channel events and everything else
Slack files under "message".

```
HTTP request  ->  Slack connector  ->  conversations.history
                                             |
                                    selected channel only
                                     (cursor paginated)
                                             |
                                       SlackParser
                                             |
                              thread replies + events dropped
                                             |
                                      SlackMessage[]
                                             |
                                       SlackChunker
                                             |
                                       SlackChunk[]
                                             |
                                    debug JSON response
```

The four pipelines are deliberately kept separate; [docs/architecture.md](docs/architecture.md)
explains why, and where each one's boundary sits.

**One endpoint reaches all four.** `POST /api/v1/ingestData/{source}` takes the
same body whatever it is pointed at, with the per-connector part in `config`. It
records the connection as an `ExternalDataSource`, answers `202` immediately, and
runs the source's own pipeline afterwards — stamping the team, department and
access scope onto every item and chunk on the way out.

```
POST /api/v1/ingestData/github   ->  202 { external_data_source_id, status }
                                          |
                                          `-> background
                                                GitHub | Jira | Confluence | Slack
                                                pipeline, unchanged
                                                    |
                                              permission fields
                                                    |
                                            app/data/runs/<id>.json
```

The four endpoints above still exist and still answer with a whole run inline,
which is what makes them useful for debugging one connector.
[docs/ingestion-endpoint.md](docs/ingestion-endpoint.md) has the request shape,
the per-source `config` keys and what the run file holds.

## What it does not do — yet

Deliberately absent, so the ingestion path stays small enough to understand and
control end to end:

- no vector store, no pgvector
- no database of any kind — the `ExternalDataSource` a run records is built and
  never inserted, and a run's output goes to a file
- no retrieval, reranking or LLM calls
- no authentication — the team and user a request names are trusted as sent
- no credential table, and the access token sits on the source in plain text
- no queue, no webhooks and no incremental indexing; the background run is
  FastAPI's own `BackgroundTasks` and does not survive a restart
- no `git clone` — everything goes through the GitHub API
- no Jira comments, attachments, changelogs, sprints or assignees
- no Confluence attachments, comments, blog posts, labels or page history
- no splitting of a long Confluence page into several chunks
- no Slack threads, reactions, emoji metadata, files, attachments or blocks
- no Slack user, profile or channel-name resolution, and no channel discovery
- no grouping of neighbouring Slack messages into one conversational chunk
- no Slack Events API, Socket Mode, webhooks or incremental sync
- no LangChain or LlamaIndex

`CodeChunk[]`, `JiraChunk[]`, `ConfluenceChunk[]` and `SlackChunk[]` are the
handover points. A later phase can embed and store those objects without
touching a connector, the filter or a parser.

## Quickstart

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

```bash
pytest app/tests -v          # 1,192 tests, no network, no credentials
```

Full detail in [docs/getting-started.md](docs/getting-started.md).

## Documentation

| Page | What's in it |
| --- | --- |
| [docs/getting-started.md](docs/getting-started.md) | Install, run the API, run the tests |
| [docs/architecture.md](docs/architecture.md) | Module layout, the boundaries each pipeline stops at, why the four are kept separate |
| [docs/ingestion-endpoint.md](docs/ingestion-endpoint.md) | `POST /api/v1/ingestData/{source}` — the request shape, the per-source `config` keys, what gets recorded, and what the background run writes |
| [docs/connectors/github.md](docs/connectors/github.md) | `POST /api/v1/github/ingest` — request and response, file filtering rules, Tree-sitter parser behaviour, source fidelity, errors |
| [docs/connectors/jira.md](docs/connectors/jira.md) | `POST /api/v1/jira/ingest` — scoped tokens and the Atlassian gateway, the JQL, Epic ↔ Story linking without N+1 calls, ADF flattening, errors |
| [docs/connectors/confluence.md](docs/connectors/confluence.md) | `POST /api/v1/confluence/ingest` — space resolution and confinement, cursor pagination, storage-format flattening, errors |
| [docs/connectors/slack.md](docs/connectors/slack.md) | `POST /api/v1/slack/ingest` — scopes, channel confinement, which messages become chunks, message text handling, ordering, errors |
| [docs/logging.md](docs/logging.md) | What a run prints for each source, and why log volume tracks the unit that costs a round trip |
| [docs/security.md](docs/security.md) | How tokens are held, and what never reaches a log or a response |
| [docs/testing.md](docs/testing.md) | The test suite and what each of the 22 modules covers |

Every connector page ends with a checklist for verifying that source against a
real repository, project, space or channel.
