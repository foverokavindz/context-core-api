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
├── connectors/
│   ├── base.py                   BaseSourceConnector, SourceSnapshot
│   ├── github_connector.py       the only module that imports PyGithub
│   ├── jira_connector.py         Jira's endpoints, auth and pagination
│   ├── confluence_connector.py   Confluence's endpoints, auth and pagination
│   └── slack_connector.py        conversations.history, bearer auth, cursors
├── ingestion/
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
├── models/
│   ├── github_request.py         GitHubIngestRequest
│   ├── repository_file.py        RepositoryFile  <- the GitHub boundary
│   ├── code_chunk.py             CodeChunk
│   ├── ingest_response.py        GitHub response DTOs + limits
│   ├── jira_request.py           JiraIngestRequest
│   ├── jira_issue.py             JiraIssue  <- the Jira boundary
│   ├── jira_chunk.py             JiraChunk
│   ├── jira_response.py          Jira response DTOs + limits
│   ├── confluence_request.py     ConfluenceIngestRequest
│   ├── confluence_page.py        ConfluencePage  <- the Confluence boundary
│   ├── confluence_chunk.py       ConfluenceChunk
│   ├── confluence_response.py    Confluence response DTOs + limits
│   ├── slack_request.py          SlackIngestRequest
│   ├── slack_message.py          SlackMessage  <- the Slack boundary
│   ├── slack_chunk.py            SlackChunk
│   └── slack_response.py         Slack response DTOs + limits
├── core/exceptions.py            error types and their HTTP statuses
└── tests/
```

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
