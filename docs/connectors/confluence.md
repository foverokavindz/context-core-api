# Confluence

[← Documentation](../README.md)

Takes Atlassian credentials and a space key, resolves that space, pulls only its
pages through the Confluence Cloud REST v2 API, and flattens their
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

## Calling the endpoint

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

Requests go through Atlassian's gateway for the same reason
[Jira's](jira.md#calling-the-endpoint) do, and the cloud ID is resolved the same
way:

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

## What gets fetched

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

## Storage format

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
are dropped, heading levels are lost. Contrast
[`CodeChunk.content`](github.md#source-fidelity), which guarantees an exact byte
slice of the original file — there is no such guarantee here, because what an
embedding model needs from a wiki page is the prose.

## Chunking

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

## Errors

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
