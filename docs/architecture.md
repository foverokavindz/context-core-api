# Architecture

[← Documentation](README.md)

The four pipelines are deliberately kept separate. A shared `SourceDocument`
abstraction will come once all of them have been used enough to show what they
actually have in common — guessing at it first would produce the wrong
abstraction. Jira and Confluence are both Atlassian and both resolve a cloud id
the same way, which is exactly the kind of overlap worth *seeing twice* before
factoring out. Slack pushes the other way: it is the first source where the
useful unit is a sentence rather than a document, and where most of what the API
returns is deliberately thrown away — which is exactly the kind of difference
worth seeing before deciding what all four share.

## Module layout

```
app/
├── main.py                       FastAPI app, logging, error handler
├── api/
│   ├── github_routes.py          the GitHub endpoint (thin)
│   ├── jira_routes.py            the Jira endpoint (thin)
│   ├── confluence_routes.py      the Confluence endpoint (thin)
│   └── slack_routes.py           the Slack endpoint (thin)
├── controllers/
│   └── ingestion_controller.py   the one endpoint all four sources share
├── services/
│   └── ingestion_service.py      records the connection, starts the run
├── background/pipeline/
│   └── ingestion_pipeline.py     runs it afterwards, writes the run file
├── connectors/
│   ├── base.py                   BaseSourceConnector, SourceSnapshot
│   ├── github_connector.py       the only module that imports PyGithub
│   ├── jira_connector.py         Jira's endpoints, auth and pagination
│   ├── confluence_connector.py   Confluence's endpoints, auth and pagination
│   └── slack_connector.py        conversations.history, bearer auth, cursors
├── ingestion/
│   ├── embedding_service.py      the only module that imports openai
│   ├── ingestion_service.py      GitHub orchestration
│   ├── file_filter.py            which paths are worth ingesting
│   ├── parser/
│   │   ├── base.py               BaseParser + ParserRegistry
│   │   └── typescript_parser.py  Tree-sitter TS/TSX
│   ├── jira_ingestion_service.py Jira orchestration + relationship linking
│   ├── jira_parser.py            the only module that knows Jira's field names
│   ├── jira_adf.py               ADF -> plain text
│   ├── jira_chunker.py           one issue -> one chunk
│   ├── confluence_ingestion_service.py  Confluence orchestration
│   ├── confluence_parser.py      the only module that knows Confluence's fields
│   ├── confluence_storage.py     storage-format XHTML -> plain text
│   ├── confluence_chunker.py     one page -> one chunk
│   ├── slack_ingestion_service.py       Slack orchestration
│   ├── slack_parser.py           the only module that knows Slack's fields
│   └── slack_chunker.py          one message -> one chunk
├── models/                       one package per source, same four names in each
│   ├── common/
│   │   ├── permission_scope.py   PermissionScope  <- the one shared mixin
│   │   └── embedding_counts.py   EmbeddingCounts
│   ├── github/
│   │   ├── request.py            GitHubIngestRequest
│   │   ├── file.py               RepositoryFile  <- the GitHub boundary
│   │   ├── chunk.py              CodeChunk
│   │   └── response.py           GitHub response DTOs + limits
│   ├── jira/
│   │   ├── request.py            JiraIngestRequest
│   │   ├── issue.py              JiraIssue  <- the Jira boundary
│   │   ├── chunk.py              JiraChunk
│   │   └── response.py           Jira response DTOs + limits
│   ├── confluence/
│   │   ├── request.py            ConfluenceIngestRequest
│   │   ├── page.py               ConfluencePage  <- the Confluence boundary
│   │   ├── chunk.py              ConfluenceChunk
│   │   └── response.py           Confluence response DTOs + limits
│   ├── slack/
│   │   ├── request.py            SlackIngestRequest
│   │   ├── message.py            SlackMessage  <- the Slack boundary
│   │   ├── chunk.py              SlackChunk
│   │   └── response.py           Slack response DTOs + limits
│   └── ingestion/                the source-agnostic endpoint, not a connector
│       ├── request.py            IngestDataRequest + REQUIRED_CONFIG_KEYS
│       └── response.py           IngestStartedResponse
├── entities/                     the database layer — see entities.md
│   ├── base.py                   Base, UUIDMixin, TimestampMixin
│   └── organization/             departments, job titles, users
├── repository/                   the only modules that write a table
│   ├── external_data_source_repository.py
│   ├── sync_run_repository.py
│   ├── resource_repository.py
│   └── chunk_repository.py
├── core/
│   ├── exceptions.py             error types and their HTTP statuses
│   └── db/                       engine, SessionLocal, the get_db dependency
└── tests/
```

`models/` and `entities/` are not two names for the same thing. Everything in
`models/` is a pydantic DTO that lives for the length of one request; everything
in `entities/` is a table. No connector, parser or chunker imports `entities/`
for a *table* — the four pipelines still end at a DTO, exactly as they did.

`repository/` is where the two meet, and it is the only place they do. One class
per table, each taking a `Session`, and each turning DTOs into rows: a
`RepositoryFile` becomes a `resources` row, a `CodeChunk` becomes a `chunks`
row. That mapping is written once rather than four times, because by the time
all four sources existed it was clear they agreed on the fields a row needs —
`external_id`, `title`, `version_key`, `resource_type`, and the permission trio
— and disagreed only on the remainder, which is what `resource_metadata` and
`chunk_metadata` are for. The remainder is derived from `model_dump()`, so a
connector that grows a field gets it stored without a change in `repository/`.

**No repository commits.** The caller owns the transaction, which is what lets a
whole run's resources, chunks, run status and `last_synced_at` land in one.

Two of `entities/`' *enums* are imported by `models/` and by the common ingestion
path: `SourceType` and `ResourceAccessScope`. An enum is not a table, and
copying either into `models/` would create a second list of the same values to
keep in step.

## The common ingestion path

The four endpoints above each run a whole pipeline synchronously and hand the
result back inline, which is what makes them useful for debugging one connector
and useless against a real repository — a few hundred files take minutes, which
is longer than an HTTP client will wait.

`POST /api/v1/ingestData/{external_source}` is the other shape. One body for all
four sources, with the per-connector part in `config`, and three stages that run
at two different times:

```
controller  ->  service  ->  202 Accepted
   resolve      write the        (the caller is done here)
   the path     source row
   validate     queue a PENDING
   config       sync run
                commit
                     |
                     `-> background pipeline
                            sync run -> RUNNING
                            the source's own ingestion service, unchanged
                            connector -> parser -> chunker -> embedder
                            permission fields onto every item and chunk
                            -> resources + chunks + COMPLETED, in one transaction
                            -> app/data/runs/<source>_<id>.json
```

The split is the point. The controller knows which sources exist and what each
one's config must contain; the service knows what a connection *is*; the
pipeline knows which of the four services to run, and the repositories know what
a row looks like. None of the first three knows what a chunk is, and none of the
four pipelines learned anything about the other three.

**The pipeline opens its own session.** It takes a session factory rather than a
`Session`, so it owns one end to end: it opens it, commits it, closes it, and
releases its connection back to the pool across the minutes of network work.
Handing over the request's session would work on the current FastAPI and would
stop working silently on a version that tears dependencies down earlier — see
[ingestion-endpoint.md](ingestion-endpoint.md) for the measurement. The factory
is also the seam the tests substitute a fake at.

`ingestion_pipeline.py` is the only module that imports all four ingestion
services, and it does so in one `if`/`elif` rather than a registry — with four
branches, a lookup table would hide the only thing worth seeing, which is that
each branch makes the same call the source's own endpoint already makes.

Two things this path adds that the four endpoints do not have:

**A record of the run.** An `ExternalDataSource` is written from the request,
with the token on it, and a `SyncRun` is queued against it — both committed
before the `202`, so the two ids the caller gets back name rows that exist. The
pipeline then walks that run through `RUNNING` to `COMPLETED` or `FAILED` and
writes what it produced into `resources` and `chunks`. The reasons the credential
row is still skipped, and the token still in plain text, are in
[todo.md](todo.md).

**Permissions.** `PermissionScope` is the second thing after `embed_into` to
earn a place shared across all four sources, and it earned it the same way:
`resources` and `chunks` both carry `access_scope`, `team_id` and
`department_id`, the four item models and four chunk models all needed the same
three fields, and there was nothing to learn from seeing them written eight
times. The pipeline stamps them in one place after the chunker, which is also
the only way a chunk's copy cannot drift from its resource's.

The run file is scaffolding, not a feature. The rows are the record now; the
file is written beside them because it is still the quickest way to read a whole
run without a query, and the response has never named it — a caller gets the
`external_data_source_id` and the `sync_run_id`, and nothing about where the
server put anything.

## The boundaries that matter

```
GitHub  ->  GitHubConnector  ->  RepositoryFile  ->  filter/parser/chunks
                                 ^^^^^^^^^^^^^^
                            nothing past here knows about GitHub

Jira    ->  JiraConnector    ->  JiraIssue       ->  linking/chunker
                                 ^^^^^^^^^
                             nothing past here knows about Jira

Confluence -> ConfluenceConnector -> ConfluencePage -> chunker
                                     ^^^^^^^^^^^^^^
                          nothing past here knows about Confluence

Slack   -> SlackConnector   ->  SlackMessage    ->  chunker
                                ^^^^^^^^^^^^
                            nothing past here knows about Slack
```

`github_connector.py` is the only file importing PyGithub. `jira_connector.py`,
`confluence_connector.py` and `slack_connector.py` are the only files in their
pipelines importing httpx, and the only ones that know their APIs' endpoints;
`jira_parser.py`, `confluence_parser.py` and `slack_parser.py` are the only ones
that know their field names. By the time an issue is a `JiraIssue` its
description is plain text — no ADF survives that boundary — by the time a page is
a `ConfluencePage` its body is plain text, with no `<p>`, no `<h2>` and no
`ac:structured-macro` past it, and by the time a message is a `SlackMessage` its
`subtype`, `thread_ts`, `bot_id`, `blocks` and `reactions` are all gone.

## The one thing the four pipelines share

`embedding_service.py` is the exception to the separation above, and it earned
the exception rather than being granted it. Four sources produced four chunk
models, and by the time all four existed the thing they had in common was
visible instead of guessed at: a chunk has `content`, and after embedding it has
a vector. That is the whole overlap, and it is written down as a `Protocol`
rather than a base class:

```python
class EmbeddableChunk(Protocol):
    content: str
    embedding: list[float] | None
    embedding_model: str | None
```

`CodeChunk`, `JiraChunk`, `ConfluenceChunk` and `SlackChunk` satisfy it without
inheriting anything, so the four models stay independent and the embedder stays
ignorant of which source it is holding. `embed_into(result, embedder)` is the
stage itself — every service calls it in one line after its chunker, and there is
nowhere for the four to drift apart.

Note what is *not* shared. There is still no `SourceDocument`, no shared
connector base beyond `BaseSourceConnector`, and no common request or response
model apart from `EmbeddingCounts`. Embedding was factored out because it is
identical; the rest is not, and seeing it four times is still the cheaper
mistake.

No pipeline imports another. None of `JiraConnector`, `ConfluenceConnector` or
`SlackConnector` implements `BaseSourceConnector`: that contract is
repository-shaped (`get_files(branch=…)` returning a `commit_sha`), and neither
an issue, a wiki page nor a chat message has a branch or a commit. Forcing dummy
values through it would make the abstraction worse, not better — which is also
why `ConfluenceSnapshot` and `SlackSnapshot` are their own dataclasses rather
than a reused `SourceSnapshot`.

`SlackConnector` is also the shortest of the four, and the difference is
instructive: Slack's Web API has one host and one path prefix, so there is no
site to resolve, no cloud id to look up and no second unauthenticated client.
What it spends that saving on instead is the `ok: false` check, which the other
three do not need at all.

## Where each pipeline is documented

The per-source detail lives with its connector:

| Source | Boundary type | Page |
| --- | --- | --- |
| GitHub | `RepositoryFile` | [connectors/github.md](connectors/github.md) |
| Jira | `JiraIssue` | [connectors/jira.md](connectors/jira.md) |
| Confluence | `ConfluencePage` | [connectors/confluence.md](connectors/confluence.md) |
| Slack | `SlackMessage` | [connectors/slack.md](connectors/slack.md) |
