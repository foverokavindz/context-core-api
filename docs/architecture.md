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
├── core/exceptions.py            error types and their HTTP statuses
└── tests/
```

`models/` and `entities/` are not two names for the same thing. Everything in
`models/` is a pydantic DTO that lives for the length of one request; everything
in `entities/` is a table. No pipeline imports `entities/` for a *table*, and
nothing in it is wired into a connector — it is the first stone of the
application that will be built on top of ingestion, not part of ingestion
itself.

Two of its *enums* are now imported by `models/` and by the common ingestion
path: `SourceType` and `ResourceAccessScope`. An enum is not a table, and
copying either into `models/` would create a second list of the same values to
keep in step. `ExternalDataSource` is imported too, by the service and the
pipeline — and constructed, not persisted, because there is still no engine.

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
   resolve      record the       (the caller is done here)
   the path     connection
   validate     schedule
   config       the run
                     |
                     `-> background pipeline
                            the source's own ingestion service, unchanged
                            connector -> parser -> chunker -> embedder
                            permission fields onto every item and chunk
                            -> app/data/runs/<source>_<id>.json
```

The split is the point. The controller knows which sources exist and what each
one's config must contain; the service knows what a connection *is*; the
pipeline knows which of the four services to run. None of them knows what a
chunk is, and none of the four pipelines learned anything about the other three.

`ingestion_pipeline.py` is the only module that imports all four ingestion
services, and it does so in one `if`/`elif` rather than a registry — with four
branches, a lookup table would hide the only thing worth seeing, which is that
each branch makes the same call the source's own endpoint already makes.

Two things this path adds that the four endpoints do not have:

**A record of the run.** An `ExternalDataSource` is built from the request, with
the token on it. Nothing persists it — there is no session — so it is carried
into the pipeline and written into the run file. The `TODO` marking where
`session.add()` goes is in `services/ingestion_service.py`, and the reasons the
credential row is skipped for now are in [todo.md](todo.md).

**Permissions.** `PermissionScope` is the second thing after `embed_into` to
earn a place shared across all four sources, and it earned it the same way:
`resources` and `chunks` both carry `access_scope`, `team_id` and
`department_id`, the four item models and four chunk models all needed the same
three fields, and there was nothing to learn from seeing them written eight
times. The pipeline stamps them in one place after the chunker, which is also
the only way a chunk's copy cannot drift from its resource's.

The run file is scaffolding, not a feature. It exists so a run can be inspected
before there is a database to inspect instead, which is why the response does
not name it — a caller gets the `external_data_source_id` and nothing about
where the server put anything.

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
