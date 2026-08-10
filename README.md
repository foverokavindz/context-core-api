# Context Core — Ingestion API

Stage one of a RAG ingestion pipeline. Three independent sources, each ending in
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

The three pipelines are deliberately kept separate. A shared `SourceDocument`
abstraction will come once all of them have been used enough to show what they
actually have in common — guessing at it first would produce the wrong
abstraction. Jira and Confluence are both Atlassian and both resolve a cloud id
the same way, which is exactly the kind of overlap worth *seeing twice* before
factoring out.

## What it does not do — yet

Deliberately absent, so the ingestion path stays small enough to understand and
control end to end:

- no embeddings, no vector store, no pgvector
- no database of any kind
- no retrieval, reranking or LLM calls
- no background queues, webhooks or incremental indexing
- no `git clone` — everything goes through the GitHub API
- no Jira comments, attachments, changelogs, sprints or assignees
- no Confluence attachments, comments, blog posts, labels or page history
- no splitting of a long Confluence page into several chunks
- no LangChain or LlamaIndex

`CodeChunk[]`, `JiraChunk[]` and `ConfluenceChunk[]` are the handover points. A
later phase can embed and store those objects without touching a connector, the
filter or a parser.

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

## Calling the GitHub endpoint

```
POST /api/v1/github/ingest
```

```json
{
	"token": "github-access-token",
	"repository": "my-organization/my-repository",
	"branch": "main"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `token` | yes | GitHub access token. Held in memory for the request only. |
| `repository` | yes | `owner/name`. |
| `branch` | no | Defaults to the repository's default branch. |
| `full` | no | `true` returns **every** file and chunk, untruncated, instead of a sample. |
| `max_files` | no | Overrides how many accepted files this run downloads (default 500). |

The token needs read access to the repository; a fine-grained token with
**Contents: Read** is enough, and works for private organisation repositories.

```bash
curl -X POST http://localhost:8000/api/v1/github/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "token": "YOUR_GITHUB_TOKEN",
    "repository": "ORG/REPOSITORY",
    "branch": "main"
  }'
```

Swagger UI at `/docs` is usually easier: open the endpoint, **Try it out**, fill
in the three fields, **Execute**.

> **Never commit a real token.** Paste it into the request at call time. It is
> not read from a file, not written to one, and not stored anywhere.

### Expected response

```json
{
	"repository": "my-org/backend",
	"branch": "main",
	"commit_sha": "abc123",

	"discovered_files": 240,
	"accepted_files": 87,
	"parsed_files": 87,
	"generated_chunks": 356,
	"truncated": false,

	"files": [{ "path": "src/auth/AuthService.ts", "language": "typescript", "size": 2450 }],

	"sample_chunks": [
		{
			"file_path": "src/auth/AuthService.ts",
			"symbol_type": "method",
			"symbol_name": "login",
			"parent_symbol": "AuthService",
			"start_line": 25,
			"end_line": 62,
			"content": "async login(email: string, password: string) { ... }"
		}
	],

	"errors": [{ "file": "src/assets/logo.ts", "reason": "Skipped: file appears to be binary." }]
}
```

The counts are complete; the lists are samples. A real repository would return
megabytes of source otherwise. Limits live in `app/models/ingest_response.py`:

| Constant                      | Default | Meaning                       |
| ----------------------------- | ------- | ----------------------------- |
| `SAMPLE_FILES_LIMIT`          | 10      | files listed in the response  |
| `SAMPLE_CHUNKS_LIMIT`         | 20      | chunks listed in the response |
| `CHUNK_CONTENT_PREVIEW_CHARS` | 600     | source shown per sample chunk |
| `MAX_FILES_PER_INGESTION`     | 500     | files downloaded per request  |

The internal `IngestionResult` always holds **every** file and **every** chunk —
only the HTTP projection is sampled.

> **The whole repository is always processed.** A response showing 10 files and
> 20 chunks while reporting `accepted_files: 98` and `generated_chunks: 441` is
> not a partial run — it is the complete run, sampled for display. The counts
> are the truth; `files` and `sample_chunks` are a window onto it.

To see everything, send `"full": true`:

```bash
curl -X POST http://localhost:8000/api/v1/github/ingest \
  -H "Content-Type: application/json" \
  -d '{"token":"YOUR_TOKEN","repository":"ORG/REPO","full":true}'
```

That returns all accepted files and all chunks with untruncated bodies. The
counts are identical either way — only the serialised detail changes.

`truncated: true` is the separate signal that the run really did see only part
of the repository: either `max_files` was reached, or GitHub truncated its own
tree listing.

### Errors

| Situation                                        | Status |
| ------------------------------------------------ | ------ |
| Invalid, expired or revoked token                | 401    |
| Token lacks permission for the repository        | 401    |
| Repository not found or not visible to the token | 404    |
| Branch does not exist                            | 404    |
| GitHub rate limit exhausted                      | 429    |
| GitHub unreachable or returning an error         | 502    |
| Malformed request body                           | 422    |

Problems with a _single file_ never fail the run. Binary files, invalid UTF-8,
download failures and unparseable sources are collected into `errors[]` and the
rest of the repository is ingested normally.

**Rate limits fail fast.** PyGithub's default retry policy treats a rate limit
as retryable and sleeps until `X-RateLimit-Reset` — up to an hour, ten times
over — which would stall a synchronous HTTP request instead of answering it.
The connector therefore supplies a plain `urllib3` retry policy that retries
only transient 5xx and connection failures, so a rate limit returns **429 in
well under a second**. Verified against a genuinely exhausted limit.

## Calling the Jira endpoint

```
POST /api/v1/jira/ingest
```

```json
{
	"site_url": "https://your-company.atlassian.net",
	"email": "developer@example.com",
	"api_token": "jira-api-token",
	"project_key": "TRACK"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `site_url` | yes | Your Jira Cloud site root. `https://` only; a trailing slash is normalised away. |
| `email` | yes | The Atlassian account the token belongs to. Used as the HTTP Basic username. |
| `api_token` | yes | An Atlassian API token. Held in memory for the request only. |
| `project_key` | yes | The project to ingest, e.g. `TRACK`. |
| `full` | no | `true` returns **every** issue and chunk, untruncated, instead of a sample. |
| `max_issues` | no | Overrides how many issues this run retrieves (default 500). |

Create a token at <https://id.atlassian.com/manage-profile/security/api-tokens>.
The account needs **Browse Projects** permission on the project.

**Scoped tokens are supported, and are the reason requests go through
Atlassian's gateway.** A token created with scopes cannot authenticate against
`https://your-site.atlassian.net/rest/...` at all — it returns 401 no matter how
correct it is. It only works against `api.atlassian.com` with the site's cloud
ID in the path, so every run begins by reading that ID from the site's public
`/_edge/tenant_info` endpoint:

```
https://your-site.atlassian.net/_edge/tenant_info   (public, no credential)
        -> {"cloudId": "c57406ca-..."}
        -> https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...
```

You still supply your ordinary site URL; the connector does the rest. A scoped
token needs `read:jira-user` (for `/myself`) and `read:jira-work` (for the
project lookup and the search).

```bash
curl -X POST http://localhost:8000/api/v1/jira/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "site_url": "https://YOUR-SITE.atlassian.net",
    "email": "YOUR-ATLASSIAN-EMAIL",
    "api_token": "YOUR-API-TOKEN",
    "project_key": "TRACK"
  }'
```

> **Never commit a real API token.** Paste it into the request at call time. It
> is not read from a file, not written to one, and not stored anywhere.

Note that this stage does **not** yet embed or persist Jira chunks. `JiraChunk[]`
comes back in the response and goes nowhere else.

### What gets fetched

The JQL is built by the application, never by the caller — sending a `jql` field
is a `422`, so a request cannot reach outside the project it named:

```
project = "TRACK" AND issuetype in ("Epic", "Story") ORDER BY created ASC
```

Only six fields are requested (`summary`, `description`, `status`, `issuetype`,
`parent`, `project`), because an unqualified search returns every custom field a
site has ever defined. `parent` earns its place twice over — see below.

Four endpoints are used, in this order:

| Endpoint | Why |
| --- | --- |
| `GET /_edge/tenant_info` | The site's cloud ID. Public, and the only request that carries no credential — it uses a separate unauthenticated client so it cannot acquire one. |
| `GET /rest/api/3/myself` | Confirms the credentials work before anything else runs. |
| `GET /rest/api/3/project/{key}` | Turns an invisible or misspelled project into a clean `404`. |
| `POST /rest/api/3/search/jql` | The enhanced search endpoint, paged with `nextPageToken` until `isLast`. |

The last three go through the gateway. The tenant lookup is not counted in the
"Jira API calls" total, which tracks the authenticated calls a rate limit
applies to; it gets its own log line instead.

### Relationships without N+1 calls

Jira tells you a Story's parent but never an Epic's children. Asking for them
would cost one extra API request per Epic. Since `parent` is one of the six
requested fields, a run already has everything it needs in memory, so children
are resolved by a single local pass:

```
TRACK-25.parent_key = TRACK-10        ->      TRACK-10.child_issues = [
TRACK-26.parent_key = TRACK-10                    "TRACK-25",
                                                  "TRACK-26",
                                              ]
```

Child lists are sorted and de-duplicated, so the same project always produces
the same chunk text. A Story whose Epic fell outside the run **keeps its
`parent_key`** — a dangling pointer is more useful than a silently dropped one,
and it is exactly what a capped run produces. Those are summarised in `errors[]`
as one entry, not one per Story.

### Descriptions

Jira Cloud returns descriptions as Atlassian Document Format — a JSON tree. None
of that belongs in a chunk, so `app/ingestion/jira_adf.py` flattens it:

```json
{ "type": "doc", "content": [
  { "type": "paragraph", "content": [
    { "type": "text", "text": "Employees can edit timesheets." }]}]}
```

becomes `Employees can edit timesheets.` Paragraphs, headings, hard breaks,
bullet and numbered lists (including nesting), blockquotes and code blocks are
all handled. Formatting marks are dropped and their text kept. An unrecognised
node never crashes the run: its text is salvaged, or its children are traversed.

This is a *flattening*, not a faithful renderer. Contrast `CodeChunk.content`,
which guarantees an exact byte slice of the original file — there is no such
guarantee here, because what an embedding model needs from a Jira description is
the prose, not the markup.

### Chunking

One issue, one chunk. No splitting by tokens, characters, paragraphs, headings
or sentences — the simplest baseline that can work, so `generated_chunks` equals
`retrieved_issues` unless an issue failed to parse.

```
Issue Key: TRACK-10
Issue Type: Epic
Project: TRACK
Summary: Timesheet Management
Status: In Progress

Description:
Provide complete timesheet management capabilities.

Child Issues:
TRACK-21
TRACK-25
```

A Story instead carries a `Parent Epic:` line. `Status:` and `Parent Epic:` are
omitted entirely when absent rather than rendered as `None`.

**An Epic's chunk names its children by key and never repeats their text.** Each
Story already has its own chunk, so copying its description into its Epic would
embed the same prose twice.

### Jira errors

| Situation | Status |
| --- | --- |
| Invalid, expired or revoked API token | 401 |
| Account authenticated but lacks permission, or a scoped token missing `read:jira-user` / `read:jira-work` | 403 |
| Site or project not found, or not visible to the account | 404 |
| Jira rate limit | 429 |
| Jira unreachable, timed out, or returning an unreadable body | 502 |
| Malformed request body, or a `jql` field | 422 |

Jira's own error body never reaches the client — it is logged server-side and
the client gets one of our fixed messages. Nothing sleeps waiting for a rate
limit to reset; a `429` comes straight back.

`truncated: true` means the *ingestion* stopped early because `max_issues` was
reached while Jira still had results. That is a different thing from `full:
false`, which only shortens the *response*. Sampling never sets `truncated`.

## Calling the Confluence endpoint

```
POST /api/v1/confluence/ingest
```

```json
{
	"site_url": "https://your-company.atlassian.net",
	"email": "developer@example.com",
	"api_token": "atlassian-api-token",
	"space_key": "TR"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `site_url` | yes | Your Atlassian Cloud site root. `https://` only; a trailing slash is normalised away. Not the `/wiki` path — the connector adds that. |
| `email` | yes | The Atlassian account the token belongs to. Used as the HTTP Basic username. |
| `api_token` | yes | An Atlassian API token. Held in memory for the request only. |
| `space_key` | yes | The space to ingest, e.g. `TR`. |
| `full` | no | `true` returns **every** page and chunk, untruncated, instead of a sample. |
| `max_pages` | no | Overrides how many pages this run retrieves (default 500). |

**A Confluence space key is not a Jira project key, and the validation reflects
that.** Real keys include two-letter ones like `TR`, single-character ones, and
personal spaces keyed `~<account id>`, so the pattern is `[A-Za-z0-9~._-]{1,255}`
rather than Jira's narrow `[A-Z][A-Z0-9]{1,9}`. It can afford to be wider: the
key is sent as a URL query parameter and percent-encoded, so unlike JQL there is
no string for it to escape out of. The key is matched case-insensitively, so
`tr` finds `TR`.

Create a token at <https://id.atlassian.com/manage-profile/security/api-tokens>.
A scoped token needs `read:space:confluence` and `read:page:confluence`, and
nothing else — this pipeline never writes.

Requests go through Atlassian's gateway for the same reason Jira's do, and the
cloud ID is resolved the same way:

```
https://your-site.atlassian.net/_edge/tenant_info   (public, no credential)
        -> {"cloudId": "c57406ca-..."}
        -> https://api.atlassian.com/ex/confluence/{cloudId}/wiki/api/v2/...
```

```bash
curl -X POST http://localhost:8000/api/v1/confluence/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "site_url": "https://YOUR-SITE.atlassian.net",
    "email": "YOUR-ATLASSIAN-EMAIL",
    "api_token": "YOUR-API-TOKEN",
    "space_key": "TR"
  }'
```

> **Never commit a real API token.** Paste it into the request at call time. It
> is not read from a file, not written to one, and not stored anywhere.

Note that this stage does **not** yet embed or persist Confluence chunks.
`ConfluenceChunk[]` comes back in the response and goes nowhere else.

### What gets fetched

**Only pages belonging to the space you named.** The key is resolved to a space
ID first, and that ID is what every page request filters on — the run never
fetches the site's pages and narrows them down afterwards:

```
space_key = TR
        |
GET /wiki/api/v2/spaces?keys=TR     ->  space_id = 6422530
        |
GET /wiki/api/v2/pages?space-id=6422530&status=current&body-format=storage
```

There is no `cql` field and no way to widen a run mid-flight; sending an unknown
field is a `422`. `status=current` keeps drafts, archived and trashed pages out —
this is a picture of what a space says now, not an archive.

Three endpoints are used, in this order:

| Endpoint | Why |
| --- | --- |
| `GET /_edge/tenant_info` | The site's cloud ID. Public, and the only request that carries no credential — it uses a separate unauthenticated client so it cannot acquire one. |
| `GET /wiki/api/v2/spaces?keys=…` | Turns the key into the space ID every page request is filtered by. Confluence applies the filter, so a site's full space list is never fetched. |
| `GET /wiki/api/v2/pages?space-id=…` | The pages themselves, followed by cursor until `_links.next` runs out. |

The last two go through the gateway. The tenant lookup is not counted in the
"Confluence API calls" total, which tracks the authenticated calls a rate limit
applies to; it gets its own log line instead.

**Pagination follows the cursor, not the link.** Confluence returns `_links.next`
as a relative URL; the connector extracts only the `cursor` value from it and
re-sends that against its own path and its own parameters. Following the URL
directly would be shorter, and would also let an upstream response drop the
`space-id` filter or raise the limit past the cap. A repeated cursor, an empty
batch promising more, and a malformed `next` all end the walk rather than
looping.

### Storage format

Confluence returns page bodies as *storage format*: XHTML-like markup mixing
ordinary HTML with Confluence's own namespaced elements. None of that belongs in
an embedding, so it is flattened with BeautifulSoup before it crosses the
`ConfluencePage` boundary:

```html
<h2>Authentication</h2>
<p>TrackIt uses JWT authentication.</p>
<ul><li>Users log in with email.</li><li>A JWT token is generated.</li></ul>
```

becomes

```
Authentication

TrackIt uses JWT authentication.

- Users log in with email.
- A JWT token is generated.
```

Paragraphs, headings, `<br>`, ordered and nested lists, blockquotes, tables
(as `cell | cell` rows), links, formatting tags and code blocks are all handled.
Two rules do most of the work:

- **Unknown elements are rendered by rendering their children.** `ac:structured-macro`,
  `ac:rich-text-body`, `ac:link`, `ri:page` and whatever Confluence ships next
  are handled by *not* being handled — the readable text inside them survives and
  nothing raises. `ac:parameter` is the one exception: it is a macro's
  configuration rather than something the page says, so it is dropped along with
  `<script>` and `<style>`.
- **Blocks and inline runs are told apart by what a node produced**, not by a
  hard-coded tag list. A `<span>` of plain text joins the sentence around it; a
  macro body full of paragraphs becomes paragraphs.

This is a *flattening*, not a faithful renderer. Emphasis is dropped, link URLs
are dropped, heading levels are lost. Contrast `CodeChunk.content`, which
guarantees an exact byte slice of the original file — there is no such guarantee
here, because what an embedding model needs from a wiki page is the prose.

### Chunking

One page, one chunk. No splitting by headings, tokens, characters, paragraphs or
sections — the simplest baseline that can work, so `generated_chunks` equals
`parsed_pages` unless a page failed to parse.

```
Space: TrackIt (TR)
Page: Authentication

Content:
TrackIt uses JWT authentication.

Users log in using their email and password.
After successful authentication, the system issues a JWT.
```

The space line falls back to just the key when no name was resolved, and an
empty page renders `(no content)` rather than a dangling header.

**Identifiers stay out of the chunk text.** `page_id`, `parent_id` and
`version_number` all ride on the `ConfluenceChunk` as metadata, because that is
what a retrieval layer filters and deduplicates on — but a version number is not
something a reader asks a question about, and putting it in the prose would only
dilute what the page says. `version_number` is kept purely as provenance: it is
what a future incremental sync would compare, and nothing reads it today.

Page hierarchy is one-directional. A page keeps its `parent_id`, and no reverse
`child_pages` list is built — unlike Jira, nothing downstream needs one, and it
would cost a pass to invent.

### Confluence errors

| Situation | Status |
| --- | --- |
| Invalid, expired or revoked API token, or a token that cannot reach this site | 401 |
| Account authenticated but lacks permission, or a scoped token missing `read:space:confluence` / `read:page:confluence` | 403 |
| Site not found, or a space that does not exist or is not visible to the account | 404 |
| Confluence rate limit | 429 |
| Confluence unreachable, timed out, or returning an unreadable body | 502 |
| Malformed request body, or an unknown field | 422 |

**A bad credential is told apart from a missing space by the response shape, not
by the status**, and this was measured against the real gateway rather than
assumed. `api.atlassian.com` answers **404 for the entire
`/ex/confluence/{cloudId}` prefix** whenever it will not accept a credential — a
wrong token, an absent one and a token for another site all look identical, and
`401` never appears at all. (Jira's `/myself` *does* return a clean `401`, which
is why its connector can validate credentials with one; Confluence's v1 user
endpoint returns `403`, so it would not help either.)

The distinction is made a sharper way instead: `/spaces?keys=…` is a *filtered
collection*, so a space that genuinely does not exist comes back as `200` with an
empty result list. That makes a `404` at the space lookup mean "the gateway would
not talk to us" — reported as `401` — and an empty result mean "no such space, or
not yours" — reported as `404`, without saying which, because Confluence hides
spaces an account may not read rather than refusing them.

Confluence's own error body never reaches the client — it is logged server-side
and the client gets one of our fixed messages. Nothing sleeps waiting for a rate
limit to reset; a `429` comes straight back.

`truncated: true` means the *ingestion* stopped early because `max_pages` was
reached while the space still had pages. That is a different thing from `full:
false`, which only shortens the *response*. Sampling never sets `truncated`.

## Architecture

```
app/
├── main.py                       FastAPI app, logging, error handler
├── api/
│   ├── github_routes.py          the GitHub endpoint (thin)
│   ├── jira_routes.py            the Jira endpoint (thin)
│   └── confluence_routes.py      the Confluence endpoint (thin)
├── connectors/
│   ├── base.py                   BaseSourceConnector, SourceSnapshot
│   ├── github_connector.py       the only module that imports PyGithub
│   ├── jira_connector.py         Jira's endpoints, auth and pagination
│   └── confluence_connector.py   Confluence's endpoints, auth and pagination
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
│   └── confluence_chunker.py     one page -> one chunk
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
│   └── confluence_response.py    Confluence response DTOs + limits
├── core/exceptions.py            error types and their HTTP statuses
└── tests/
```

### The boundaries that matter

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
```

`github_connector.py` is the only file importing PyGithub. `jira_connector.py`
and `confluence_connector.py` are the only files in their pipelines importing
httpx, and the only ones that know their APIs' endpoints; `jira_parser.py` and
`confluence_parser.py` are the only ones that know their field names. By the
time an issue is a `JiraIssue` its description is plain text — no ADF survives
that boundary — and by the time a page is a `ConfluencePage` its body is plain
text, with no `<p>`, no `<h2>` and no `ac:structured-macro` past it.

No pipeline imports another. Neither `JiraConnector` nor `ConfluenceConnector`
implements `BaseSourceConnector`: that contract is repository-shaped
(`get_files(branch=…)` returning a `commit_sha`), and neither an issue nor a wiki
page has a branch or a commit. Forcing dummy values through it would make the
abstraction worse, not better — which is also why `ConfluenceSnapshot` is its own
dataclass rather than a reused `SourceSnapshot`.

### How a repository is walked

```
repository -> branch (or default) -> HEAD commit SHA -> commit's tree SHA
           -> recursive git tree -> filter paths -> fetch only what survived
```

The recursive tree API returns every path in one call, which is what makes it
possible to filter _before_ downloading. Ignored files never cost an API call.
The commit is resolved to its tree SHA explicitly, because the tree endpoint
takes a tree SHA — the commit SHA is what gets stamped onto every file and chunk.

## File filtering rules

Configured by `FileFilterConfig` in `app/ingestion/file_filter.py`. Nothing is
hard-coded at a call site; widening the filter is a config change.

| Rule                | Default                                                                                      |
| ------------------- | -------------------------------------------------------------------------------------------- |
| Allowed extensions  | `.ts`, `.tsx`                                                                                |
| Ignored directories | `node_modules`, `dist`, `build`, `coverage`, `.git`, `.next`, `out`, `vendor`, `tmp`, `temp` |
| Ignored filenames   | `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`                                           |
| Ignored suffixes    | `*.min.js`, `*.min.ts`, `*.map`                                                              |
| Declaration files   | `*.d.ts` excluded (`exclude_declaration_files`)                                              |
| Test files          | `*.test.ts(x)`, `*.spec.ts(x)` excluded (`exclude_test_files`)                               |
| Max file size       | 1 MB (`max_file_size_bytes`)                                                                 |

Two details worth knowing:

- Directories match on **whole path segments**, so `src/distribution/x.ts` is
  kept — it is not a build `dist/`.
- Suffix rules require the dot, so `src/latest.ts` is kept despite ending in
  `test.ts`.

Declaration and test files are excluded because they restate or describe the
real source rather than being it. Both are switches, not assumptions baked into
the pipeline.

## Parser behaviour

Tree-sitter, not regex. `.ts` uses the TypeScript grammar and `.tsx` the TSX
grammar — the distinction matters, because `<T>` is a type assertion in one and
a JSX element in the other.

Recognised symbols:

| Symbol                                                       | `symbol_type` |
| ------------------------------------------------------------ | ------------- |
| `class`, `abstract class`                                    | `class`       |
| methods, getters, setters, constructors, abstract signatures | `method`      |
| `function`, `function*`                                      | `function`    |
| `const x = () => {}`                                         | `function`    |
| `handler = () => {}` inside a class                          | `method`      |
| `interface`                                                  | `interface`   |
| `enum`                                                       | `enum`        |
| `type X = ...`                                               | `type_alias`  |

- `parent_symbol` holds the enclosing class, or the namespace for symbols
  declared inside one. It is `null` at the top level.
- Chunk spans start at the `export` keyword (and any decorators), so modifiers
  survive into the chunk's content.
- We descend into classes and namespaces, but **never into a function body** — a
  helper declared inside a function stays part of that function's chunk instead
  of competing with it.

### Chunking strategy

Boundaries come from the AST, never from a character count. A function is never
split because it got long.

For a class we emit the class **and** each of its methods:

```typescript
export class AuthService {   // -> chunk: class  AuthService
    login()  { ... }         // -> chunk: method AuthService.login
    logout() { ... }         // -> chunk: method AuthService.logout
}
```

**Known tradeoff:** the class chunk contains the whole class, so every method
body appears twice — once inside the class chunk, once on its own. This is
deliberate: full class context stays retrievable alongside the granular methods.
If that duplication becomes a problem for embedding cost or retrieval quality,
set `ChunkingConfig(emit_full_class_body=False)`; the class chunk then shrinks to
its declaration header through the opening brace, keeping the
`extends`/`implements` context with no duplicated bodies. Both modes are tested.

### Source fidelity

Every chunk's `content` is an exact byte-slice of the original file, taken from
Tree-sitter node byte ranges — never rebuilt from the syntax tree. `start_line`
and `end_line` are 1-based and inclusive, and the test suite asserts that the
text at those lines is the chunk's text.

Line numbers are derived from byte offsets by `SourceIndex` (a precomputed
newline table plus `bisect`), **not** read off `Node.start_point`. That is a
deliberate workaround, not a preference:

> On `tree-sitter==0.26.0` with CPython 3.14, reading `Point.row` corrupts the
> heap. It appears to work at first and then segfaults the interpreter once
> enough allocation has happened — during a later parse, or inside an unrelated
> garbage collection. Indexing the point (`start_point[0]`) is unaffected, so
> the bug is in the attribute accessor. This was found by an end-to-end run
> against a real repository; a bisection down to pure `tree-sitter` calls
> confirmed the application code was not at fault.

`SourceIndex` avoids the API entirely and gives one definition of "line" for
both whole nodes and partial spans. `test_source_index_agrees_with_tree_sitter_points`
checks the mapping node-for-node against the grammar's own positions, and
`test_repeated_parsing_stays_stable` is the regression guard.

One intentional difference from tree-sitter's convention: for a span ending
immediately after a newline, tree-sitter points at the empty line beyond it,
while a chunk reports the last line that actually holds text.

### Fallback and resilience

- A file yielding no recognised symbol — a file of constants, a re-export barrel
  — produces one whole-file chunk with `symbol_type: "file"` and
  `symbol_name: null`, so nothing is silently dropped.
- Tree-sitter recovers from syntax errors. A partially broken file still yields
  the symbols that parsed, and the problem is reported in `errors[]`.

## Logging

A run narrates itself at INFO, so you can see progress instead of watching a
silent request. Nothing needs configuring — `app/main.py` sets the level.

```
INFO  Ingesting Asteron-Labs/TrackIt (branch: repository default)
INFO  GitHub: reading repository metadata
INFO  Resolved branch: main
INFO  GitHub: reading branch head
INFO  Resolved commit: e8d0838e10b0cd94de885912245111ec689060bb
INFO  GitHub: resolving commit tree
INFO  GitHub: reading recursive git tree
INFO  Discovered 169 files
INFO  98 files passed filter
INFO  Downloading 98 files from GitHub
INFO  [1/98] backend/src/app.ts (4.1 KB)
INFO  [2/98] backend/src/common/authorization/scope.service.ts (787 B)
...
INFO  [98/98] frontend/src/main.tsx (612 B)
INFO  Skipping backend/src/assets/logo.ts: binary file
INFO  Downloaded 97 files, skipped 1 (102 GitHub API calls)
INFO  Generated 441 code chunks from 97 files in 41.2s
```

Three things worth knowing:

- **One line per file, logged before the download.** A slow or stalled fetch is
  attributable to a named file rather than to silence.
- **The API-call count** is `4 + one per file`. Since a token gets 5,000 calls
  an hour, this is what makes a later `429` explicable rather than mysterious.
- **Nothing is logged per chunk or per symbol.** Log volume tracks files, not
  the code inside them — a file with twenty methods still gets one line. Parsing
  detail stays at DEBUG.

On a large repository that is one INFO line per file. To quiet just that stream
while keeping the rest:

```python
logging.getLogger("app.connectors.github_connector").setLevel(logging.WARNING)
```

A Jira run narrates itself the same way:

```
INFO  Ingesting Jira project TRACK from https://your-company.atlassian.net
INFO  Jira: resolving the Jira site
INFO  Resolved Jira cloud id for https://your-company.atlassian.net
INFO  Jira: validating credentials
INFO  Jira authentication successful
INFO  Jira: reading project TRACK
INFO  Searching Epics and Stories in TRACK
INFO  Jira: searching issues
INFO  Jira page 1 returned 100 issues
INFO  Jira page 2 returned 24 issues
INFO  Retrieved 124 Jira issues from 2 page(s) (4 Jira API calls)
INFO  Parsed 124 Jira issues
INFO  Linked 112 issues to 12 epics
INFO  Generated 124 Jira chunks
INFO  Ingested 124 Jira issues (12 epics, 112 stories, 112 linked to an epic) into 124 chunks in 2.4s
```

**Log volume tracks pages, not issues.** A hundred issues arrive in one API
call, so a per-issue line would say nothing about where time went; per-issue
detail stays at DEBUG. This is the same principle as GitHub's one-line-per-file,
applied to the unit that actually costs a round trip.

A Confluence run reads the same way, one line per batch:

```
INFO  Ingesting Confluence space TR from https://your-company.atlassian.net
INFO  Confluence: resolving the Confluence site
INFO  Resolved Atlassian cloud id for https://your-company.atlassian.net
INFO  Confluence: resolving space TR
INFO  Resolved Confluence space TR (6422530)
INFO  Confluence: reading pages in TR
INFO  Confluence page batch 1 returned 100 pages
INFO  Confluence page batch 2 returned 24 pages
INFO  Retrieved 124 Confluence pages from 2 batch(es) (3 Confluence API calls)
INFO  Parsed 124 Confluence pages
INFO  Generated 124 Confluence chunks
INFO  Ingested Confluence space TR (124 pages retrieved, 124 parsed) into 124 chunks in 2.1s
```

**Log volume tracks batches, not pages, and page bodies are never logged at
all** — not even at DEBUG, where only page ids appear. A wiki page can be tens of
kilobytes of prose, and putting that in a log would be noise in the best case and
a leak of internal documentation in the worst.

## Security

Every token is held as a pydantic `SecretStr`, which renders as `**********` in
every repr, log line and serialisation. Each is unwrapped exactly once — on the
line that constructs the GitHub client, or the line that builds the Jira or
Confluence client's Basic-auth pair — and never assigned to an attribute, so
nothing a traceback or a debug log could print holds it.

None is ever logged, persisted, included in a response, or written to disk. The
connector is closed as soon as fetching finishes, so the authenticated session
does not outlive the request. Parsing happens afterwards, without it.

For Jira and Confluence specifically: the account email is treated as a
credential too and is kept out of log lines, which name the site, the project or
the space instead. Request headers are never logged — that is where the Basic
credential rides. Upstream error bodies *are* logged, capped at 500 characters,
but never returned. `site_url` must be `https://`, since the credential travels
on every request. Both connectors resolve the cloud ID through a **separate
unauthenticated client**, so the one request that does not need a credential
cannot acquire one.

Sending a token in a request body is acceptable for this prototype. A production
deployment would use HTTPS and a proper credential-management mechanism; that is
explicitly out of scope here.

## Running tests

```bash
pytest app/tests -v
```

775 tests, no network access and no credentials required. PyGithub is replaced
with fakes, and Jira and Confluence with an `httpx.MockTransport`, all of which
record which API calls were made — which is how the suite proves that ignored
files are never downloaded, that the Jira issue cap shrinks the *request* rather
than trimming the answer, that every Confluence page request carries the resolved
space ID, and that a token never reaches a response, a log or an error message.

| Module                           | Covers                                                                                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `test_file_filter.py`            | every include/exclude rule, plus segment- and suffix-matching regressions                                                 |
| `test_typescript_parser.py`      | each symbol kind, parent links, exact source spans, line ranges, TSX, fallbacks, syntax errors, the line-index regression |
| `test_github_connector.py`       | branch resolution, filter-before-fetch, binary/UTF-8 skips, error mapping, token containment                              |
| `test_ingestion_service.py`      | the real GitHub pipeline end to end with only the network faked                                                           |
| `test_api.py`                    | request validation, response projection, HTTP status mapping, token never echoed                                          |
| `test_jira_adf.py`               | every ADF node kind, nesting, unknown nodes, malformed shapes, recursion depth — all exact-string assertions              |
| `test_jira_parser.py`            | each field and each fallback, null tolerance, the one fatal case (no issue key)                                           |
| `test_jira_chunker.py`           | the chunk template, omitted lines, one-chunk-per-issue, an Epic never carrying a child's description                      |
| `test_jira_connector.py`         | cloud-ID resolution and the gateway, the JQL, the six fields, pagination and its three loop guards, the cap, error mapping, credential containment |
| `test_jira_ingestion_service.py` | the real Jira pipeline end to end; linking, orphans, and the no-N+1 guarantee                                             |
| `test_jira_api.py`               | request validation, sampling vs truncation, HTTP status mapping, token never echoed                                       |
| `test_confluence_storage.py`     | every storage element kind, macros, nesting, noise removal, malformed markup, recursion depth — all exact-string assertions |
| `test_confluence_parser.py`      | each field and each fallback, null tolerance, the one fatal case (no page id)                                            |
| `test_confluence_chunker.py`     | the chunk template, omitted lines, one-chunk-per-page, ids kept out of the text                                          |
| `test_confluence_connector.py`   | cloud-ID resolution and the gateway, the space lookup, **the space-ID confinement**, cursor pagination and its guards, the cap, error mapping, credential containment |
| `test_confluence_ingestion_service.py` | the real Confluence pipeline end to end with only the network faked                                                |
| `test_confluence_api.py`         | request validation, sampling vs truncation, HTTP status mapping, token never echoed                                      |

The Jira and Confluence connector tests drive a **real** `httpx.Client` through a
mock transport, so base-URL joining, the Basic-auth header and query-string
encoding are all genuinely exercised rather than patched out.

## Manual verification against a real repository

1. `uvicorn app.main:app --reload`
2. Open <http://localhost:8000/docs> and call the endpoint with a real token, or
   use the `curl` command above.
3. Check that `discovered_files` is larger than `accepted_files` (the filter is
   working), that `.ts` and `.tsx` paths appear in `files`, and that
   `sample_chunks` contains a `method` with a `parent_symbol`.
4. Check the server log — it reports the repository, branch, commit and counts,
   and contains no credential.

## Manual verification against a real Jira project

1. `uvicorn app.main:app --reload`
2. Open <http://localhost:8000/docs> and call `/api/v1/jira/ingest` with a real
   site, email, token and project key, or use the `curl` command above.
3. Check that `epics + stories == retrieved_issues`, and that
   `generated_chunks == retrieved_issues`.
4. Check that Stories carry a `parent_key` and Epics carry a populated
   `child_issues`.
5. Check that descriptions read as plain text — no `{"type": "doc" ...}`
   anywhere in the response.
6. Check that an Epic's `sample_chunks` entry lists its children's *keys* and
   none of their description text.
7. Set `max_issues` below the project size and confirm `truncated: true`; set it
   at or above and confirm `truncated: false`.
8. Check the server log — it reports the site, project, pages and counts, and
   contains no token, no `Authorization` header and no email.

## Manual verification against a real Confluence space

1. `uvicorn app.main:app --reload`
2. Open <http://localhost:8000/docs> and call `/api/v1/confluence/ingest` with a
   real site, email, token and space key, or use the `curl` command above.
3. Check that the resolved space is the one you meant: `space_key`, `space_id`
   and `space_name` are all echoed back.
4. Check that `retrieved_pages == parsed_pages == generated_chunks`.
5. Check that **every** entry in `pages` carries the `space_key` you asked for,
   and that no page from another space appears.
6. Check that page content reads as plain text — no `<p>`, no `<h1>`, no
   `ac:structured-macro` anywhere in the response.
7. Check that titles look right, that a space's home page has `parent_id: null`,
   and that nested pages carry a real `parent_id`.
8. Call it again with `"full": true` and confirm every page and every chunk comes
   back, with chunk text no longer ending in `... [truncated]`.
9. Set `max_pages` below the space size and confirm `truncated: true`; set it at
   or above and confirm `truncated: false`.
10. Use a deliberately wrong token and confirm a `401`; use a space key the
    account cannot see and confirm a sanitised `404` that does not disclose
    whether the space exists.
11. Check the server log — it reports the site, space, batches and counts, and
    contains no token, no `Authorization` header, no email, and no page body.
