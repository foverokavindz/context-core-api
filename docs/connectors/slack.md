# Slack

[← Documentation](../README.md)

Takes a bot token and one channel ID, reads that channel's message history
through the Slack Web API, and keeps the messages a person or an app actually
wrote — dropping thread replies, channel events and everything else Slack files
under "message".

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

## Calling the endpoint

```
POST /api/v1/slack/ingest
```

```json
{
	"token": "xoxb-your-slack-bot-token",
	"channel_id": "C0123456789"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `token` | yes | A Slack bot token (`xoxb-…`). Held in memory for the request only. |
| `channel_id` | yes | The conversation to ingest, e.g. `C0123456789`. |
| `full` | no | `true` returns **every** message and chunk, untruncated, instead of a sample. |
| `max_messages` | no | Overrides how many history items this run retrieves (default 500). |

**It is the channel ID, not the channel name.** There is no channel lookup in
this pipeline, so `#engineering` will not work — right-click the channel in
Slack, *Copy link*, and take the `C…` at the end of the URL. The caller is
expected to already know which channel it wants.

The validation is deliberately loose: `[A-Za-z0-9]{2,32}`. Real conversation IDs
start `C` (public), `G` (private) or `D` (direct message), and Slack has changed
their length more than once, so pinning the prefix or the width would reject
valid channels for nothing. It can afford to be wide — the ID is sent as a URL
query parameter and percent-encoded, so unlike Jira's JQL there is no string for
it to escape out of. Slack makes the final decision.

## Scopes

Create an app at <https://api.slack.com/apps>, add a bot token scope, install it
to the workspace, then **invite the bot to the channel**.

| Channel type | Scope |
| --- | --- |
| Public channel | `channels:history` |
| Private channel | `groups:history` |

That is the whole list. No `users:read`, no `reactions:read`, no `files:read`,
no `emoji:read`, no `channels:read` — this version calls none of those APIs, so
asking for their scopes would be permission the app cannot justify.

A bot that has the right scope but was never invited gets a **403**, not a 401:
the token is fine, the membership is missing.

```bash
curl -X POST http://localhost:8000/api/v1/slack/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "token": "YOUR_SLACK_BOT_TOKEN",
    "channel_id": "C0123456789"
  }'
```

> **Never commit a real token.** Paste it into the request at call time. It is
> not read from a file, not written to one, and not stored anywhere.

Note that this stage does **not** yet embed or persist Slack chunks.
`SlackChunk[]` comes back in the response and goes nowhere else.

## Expected response

```json
{
	"channel_id": "C0123456789",

	"retrieved_messages": 120,
	"parsed_messages": 87,
	"generated_chunks": 87,

	"truncated": false,

	"resource_files": [
		{
			"channel_id": "C0123456789",
			"message_ts": "1754810101.100100",
			"author_id": "U0000000001",
			"text": "We should update the authentication flow.",
			"team_id": null,
			"department_id": null,
			"access_scope": "TEAM"
		}
	],

	"chunks": [
		{
			"channel_id": "C0123456789",
			"message_ts": "1754810101.100100",
			"author_id": "U0000000001",
			"content": "We should update the authentication flow."
		}
	],

	"errors": []
}
```

**`parsed_messages` being well below `retrieved_messages` is the normal result
here, and is the one way this response reads differently from the other three.**
For GitHub, Jira and Confluence a gap in the funnel means something failed. For
Slack it means the filter did its job: a real channel's history is mostly joins,
leaves, topic changes and thread replies, and dropping them is the point. The
gap is not reported in `errors[]` for exactly that reason — one entry per join
notice would bury the entries that matter.

`generated_chunks` always equals `parsed_messages`, because one message makes one
chunk.

## What gets fetched

**Only the channel you named.** `conversations.history` is the only Slack method
this pipeline calls, and `channel=<your id>` is on every request in the walk —
including the second and third pages, not just the first.

```
channel_id = C0123456789
        |
GET /api/conversations.history?channel=C0123456789&limit=200
        |
response_metadata.next_cursor
        |
GET /api/conversations.history?channel=C0123456789&limit=200&cursor=...
```

There is no `oldest`, `latest`, `inclusive`, `cursor` or second-channel field on
the request, and sending an unknown field is a `422`, so a run cannot be widened
mid-flight.

**`conversations.replies` is never called, under any circumstances.** That is
the guarantee that keeps a run inside one channel: without it, a thread root
would be an invitation to fan out into an unbounded number of extra requests.
Slack sometimes echoes a thread reply back into channel history anyway — a
"also send to channel" broadcast — and those are dropped by the parser rather
than followed up.

**Pagination follows the cursor, not `has_more`.** Slack reports "more to come"
two ways and only one of them can be acted on, so an absent or empty
`response_metadata.next_cursor` ends the walk whatever `has_more` claims. A
repeated cursor, an empty page promising more, and a page ceiling of 100 all end
the walk rather than looping.

## Which messages become chunks

The rule is a short allow-list, applied in this order:

| Item | Outcome |
| --- | --- |
| `type` is not `message` | skip — an event, not a message |
| has a `subtype` other than `bot_message` | skip — system / channel event |
| `thread_ts` present and different from `ts` | skip — a thread reply |
| no usable `ts` | skip — nothing to identify it by |
| `text` empty once normalised | skip — nothing to embed |
| everything else | **keep** |

**The subtype rule is an allow-list of two, not a deny-list of thirty.** Slack
uses `subtype` for everything that is technically a message and is really a
channel event — joins, leaves, topic and purpose changes, renames, archivals,
edits, deletions, tombstones, file shares, huddles — with more arriving every
release. Naming them all would be a race, and losing it means a new event type
silently becomes ingested content. Allowing only "absent" and `bot_message`
inverts that: anything new is skipped until somebody decides otherwise. The
known cost is `file_share` — a file uploaded *with* a comment — which is skipped
along with the rest.

A thread **root** is kept. It has `ts == thread_ts`, and it is an ordinary
channel message that happens to have been replied to.

Reactions, files, attachments, blocks, edit history, pinned state and client
metadata are all simply never read. They are not stripped out — nothing looks at
them, and no extra API call is made to expand any of them.

## Message text

Kept close to what was written. Whitespace is tidied — line endings normalised,
trailing spaces removed, runs of three or more newlines collapsed to one blank
line — and Slack's three escapes are undone:

```
wire:  "if (a &lt; b &amp;&amp; c &gt; d)"
chunk: "if (a < b && c > d)"
```

Exactly those three, and not `html.unescape`: Slack documents that it escapes
`&`, `<` and `>` and nothing else, so `&nbsp;` and `&copy;` in a Slack message
are literally what somebody typed. `&amp;` is replaced last, or `&amp;lt;` —
which is how Slack transmits a typed `&lt;` — would collapse all the way to `<`.

Everything else survives verbatim:

```
cc <@U0000000001> please review <#C0123456789|engineering>
docs at <https://example.com|the wiki> :tada:
```

No API call resolves `<@U0000000001>` into a person's name, no emoji is looked
up, and `:tada:` stays as the author wrote it. **Emoji and reaction *metadata*
are ignored; what people actually wrote is not rewritten.**

## Chunking

One message, one chunk. No grouping of neighbouring messages, no splitting of a
long one — the simplest baseline that can work, so `generated_chunks` equals
`parsed_messages` always.

```
We should move authentication validation into the service layer.
```

That is the entire chunk. **There is no rendered header, which is where this
differs from every other source in this repository.** A Confluence page gets a
`Space:` and `Page:` prefix because a wiki page is kilobytes of prose and the
context costs it nothing. A Slack message is frequently one sentence, and
prefixing it with a channel ID, a user ID and a float timestamp would leave most
of the embedded text describing where the message came from rather than what it
said. `channel_id`, `message_ts` and `author_id` ride on the `SlackChunk` as
metadata instead, which is where a retrieval layer wants them anyway.

Grouping is the interesting open question for a chat source — "yes, agreed"
means nothing on its own — and it is deliberately left for later, because a
baseline is what a windowing strategy would have to be measured against.

## Ordering

`conversations.history` returns newest first. The connector reverses that after
the walk, so messages come back **oldest to newest**, which makes debug output
and tests readable.

Because Slack serves the recent end first, a run capped by `max_messages` holds
the channel's **most recent** messages — presented oldest-first. Sorting is
numeric rather than lexicographic, since `"999.000100"` sorts after
`"1000.000100"` as text and before it as a number.

## Errors

**Slack reports failure in the body, not in the status**, and this is the one
real structural difference between this connector and the other three:

```json
HTTP 200 OK
{ "ok": false, "error": "channel_not_found" }
```

A status-only reading of that response would treat a missing channel, a revoked
token and a missing scope as three successful runs that happened to find no
messages. Every response therefore goes through an `ok` check before anything is
allowed to read the body.

| Situation | Slack `error` | Status |
| --- | --- | --- |
| Invalid, expired or revoked token | `invalid_auth`, `not_authed`, `token_revoked`, `token_expired`, `account_inactive` | 401 |
| App missing `channels:history` / `groups:history`, or the bot is not in the channel | `missing_scope`, `not_in_channel`, `no_permission`, `access_denied` | 403 |
| Channel does not exist, or the token cannot see it | `channel_not_found` | 404 |
| Slack rate limit | `ratelimited`, or HTTP 429 | 429 |
| Slack unreachable, timed out, unreadable body, or an unrecognised error | anything else | 502 |
| Malformed request body, or an unknown field | — | 422 |

`channel_not_found` covers both a channel that does not exist and one this token
cannot see. Slack does not distinguish them and neither do we — the same
situation the Confluence space lookup and GitHub's private repositories describe.

An error string Slack has not invented yet becomes a **502**, not a 401.
Reporting an unknown failure as an authentication problem would send an operator
to reissue a token that was never at fault.

Slack's own error body never reaches the client — it is logged server-side and
the client gets one of our fixed messages, so `not_in_channel` and
`missing_scope` do not leak a workspace's configuration into a response.

**Rate limits fail fast.** Slack sends a `Retry-After` with its 429. That value
is logged and never obeyed: sleeping until a Slack cooldown expires would hold a
synchronous HTTP request open for tens of seconds. The `429` comes straight back
and the caller decides.

`truncated: true` means the *ingestion* stopped early because `max_messages` was
reached while the channel still had history. That is a different thing from
`full: false`, which only shortens the *response*, and a different thing again
from `parsed_messages < retrieved_messages`, which is the filter working.
Sampling never sets `truncated`.

## Manual verification against a real Slack channel

1. `uvicorn app.main:app --reload`
2. Create a Slack app, give it `channels:history` (or `groups:history`), install
   it to the workspace, and **invite the bot to the channel** — a bot with the
   scope but no membership is a `403`, which is worth seeing once deliberately.
3. Copy the channel link from Slack and take the `C…` id off the end of the URL.
4. Open <http://localhost:8000/docs> and call `/api/v1/slack/ingest`, or use the
   `curl` command above.
5. Check that `generated_chunks == parsed_messages`, and that
   `parsed_messages` is **lower** than `retrieved_messages` on any channel with
   real activity — that is the filter working, not a fault.
6. Check that **every** entry in `resource_files` carries the `channel_id` you asked
   for, and that no message from another channel appears.
7. Find a thread in the channel. Confirm its **root** appears in `resource_files` and
   that **none of its replies do**, including any that were broadcast back into
   the channel.
8. Confirm no `channel_join`, `channel_leave` or topic-change text appears
   anywhere in the response.
9. Check that `chunks[i].content` is the message text *alone* — no `C0…`,
   no `U0…`, no timestamp in the prose. Those belong in the fields beside it.
10. React to a message with an emoji and re-run: the response must be identical.
    Post a message containing `:tada:` and confirm the shortcode survives in the
    text — reaction *metadata* is ignored, what people wrote is not.
11. Check that messages come back oldest-first.
12. Call it again with `"full": true` and confirm every message and every chunk
    comes back, rather than the sampled ten and twenty.
13. Set `max_messages` below the channel's history and confirm `truncated: true`
    **and** that what came back is the most recent end of the conversation; set
    it at or above and confirm `truncated: false`.
14. Use a deliberately wrong token and confirm a `401`; a channel the bot is not
    in and confirm a `403`; a plausible but nonexistent id and confirm a `404`.
15. Check the server log — it reports the channel, the pages and the counts, and
    contains no token, no `Bearer`, no `Authorization` header, and **no message
    text**.
