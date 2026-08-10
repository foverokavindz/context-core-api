# Jira

[← Documentation](../README.md)

Takes Atlassian credentials and a project key, pulls that project's Epics and
Stories through the Jira Cloud REST API, flattens their Atlassian Document
Format descriptions into plain text, and resolves Epic ↔ Story links.

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

## Calling the endpoint

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

## What gets fetched

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

## Relationships without N+1 calls

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

## Descriptions

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

This is a *flattening*, not a faithful renderer. Contrast
[`CodeChunk.content`](github.md#source-fidelity), which guarantees an exact byte
slice of the original file — there is no such guarantee here, because what an
embedding model needs from a Jira description is the prose, not the markup.

## Chunking

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

## Errors

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
