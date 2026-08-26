# The common ingestion endpoint

[← Documentation](README.md)

```
POST /api/v1/ingestData/{external_source}
```

where `external_source` is `github`, `jira`, `confluence` or `slack`,
case-insensitively.

One endpoint for all four sources, and the first one that records the connection
it ingested from rather than treating a run as a one-off. It answers `202` and
does the work afterwards.

The four per-source endpoints are unchanged and still work. The difference is
what each is *for*: `POST /api/v1/github/ingest` hands a whole run back inline,
which is how you debug a connector against a small repository; this one hands
back an id, which is what survives a run that takes minutes.

## The request

Every source sends the same shape. What differs between them lives in `config`,
the same trade [`external_data_sources.config`](entities.md#external_data_sources)
makes — one JSON object rather than a union of four connectors' fields.

```json
{
  "title": "TrackIt API",
  "team_id": "11111111-1111-1111-1111-111111111111",
  "department_id": "22222222-2222-2222-2222-222222222222",
  "access_scope": "TEAM",
  "created_by_user_id": "33333333-3333-3333-3333-333333333333",
  "source_type": "GITHUB",
  "config": { "repository": "my-org/backend", "branch": "main" },
  "token": "ghp_..."
}
```

| Field | Notes |
| --- | --- |
| `title` | The display name for this connection — the repository, project, space or channel as a person would refer to it. Becomes `external_data_sources.name`. Not unique. |
| `team_id` | Who owns what this run produces. |
| `department_id` | Carried onto everything the run produces, for a `DEPARTMENT` scope. |
| `access_scope` | `TEAM` (the default), `DEPARTMENT` or `ORGANIZATION`. |
| `created_by_user_id` | Who connected it. Authorship, **not** who may read the result. |
| `source_type` | Must agree with the path segment. Carried in the body too, so the stored row does not depend on how the request was routed. |
| `config` | Where the connector points. Per source, below. Never a secret. |
| `token` | The access token. Stored on the source so a later sync can re-run without asking again. |

Unknown fields are rejected, so a typo fails at the boundary instead of being
silently dropped.

### What each source needs in `config`

| Source | Required | Optional |
| --- | --- | --- |
| `github` | `repository` | `branch` — defaults to the repository's default branch |
| `jira` | `site_url`, `email`, `project_key` | |
| `confluence` | `site_url`, `email`, `space_key` | |
| `slack` | `channel_id` | |

A key that is present but blank fails the same way an absent one does. Left
through, it would fail much further away — inside a connector, as a confusing
404 about a repository nobody asked for.

## The response

```json
{
  "external_data_source_id": "476fca4f-9243-4304-8e20-b3e404c6b072",
  "sync_run_id": "cccd011c-f434-4ab6-8a11-c1a96742fab7",
  "source_type": "GITHUB",
  "title": "TrackIt API",
  "status": "PIPELINE_STARTED"
}
```

`202`, not `200`: the work has been accepted and has not been done.

Both ids name rows that already exist. The `external_data_sources` row and its
`sync_runs` row are written and committed before the response leaves, so a caller
holding either id can look it up immediately — the run will read `PENDING` until
the pipeline picks it up.

`external_data_source_id` is the handle for the *connection* and outlives every
run made against it; `sync_run_id` is the handle for this particular run.

**There is no file path in this response, deliberately.** Where a run writes its
output is scaffolding for this phase — see below — and a caller handed a server
path would have to unlearn it. The id is the handle, and it is the one that
keeps working once runs are read back out of a database.

## What happens next

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
                            -> resources + chunks rows
                            -> sync run -> COMPLETED, last_synced_at
                            -> app/data/runs/<source>_<id>.json
```

The pipeline runs on FastAPI's threadpool, after the response has been sent —
the four per-source services are synchronous, and running one on the event loop
would stall every other request for the length of the ingestion.

**It opens its own database session**, taking a session factory —
`SessionLocal` in production, a fake in the tests — rather than a `Session`.

Not because the request's session is unusable: on FastAPI 0.141.1 a dependency
with `yield` is torn down on the *outer* of two exit stacks
(`fastapi/routing.py`), which exits after `await response(...)` and therefore
after the background tasks, so a handed-over session would work today. Three
reasons not to hand it over anyway:

- **That ordering is not a contract.** FastAPI 0.106.0 moved yield-teardown to
  *before* background tasks and documented that dependency resources must not be
  used in them; this version has it after again. An upgrade that flips it back
  would not fail loudly — `Session.close()` does not disable a session, it
  silently reconnects on next use — so a run would half-commit on a connection
  nobody expected.
- **Pool pressure.** A handed-over session stays checked out from the route body
  until teardown, which is the whole length of the run. The pool holds 5, so five
  concurrent ingestions would block the sixth. The pipeline's own session releases
  its connection at every `commit()` and holds none across the minutes of network
  work.
- **One owner.** `get_db` closes it in a `finally` and the pipeline closes it in a
  `finally`; a session with two owners is a session nobody owns.

### What gets written, and when

Two transactions at request time and two more in the pipeline, in this order:

| When | Rows |
| --- | --- |
| before the `202` | `external_data_sources`, one `sync_runs` row at `PENDING` |
| pipeline start | that run moves to `RUNNING` with a `started_at` |
| after the ingestion | every `resources` row, every `chunks` row, the run at `COMPLETED` with its counters, and `external_data_sources.last_synced_at` — **one transaction** |
| on failure | the run at `FAILED` with a client-safe `error_message`, and nothing else |

The third one is a single transaction on purpose. `chunks` names its resource by
`(external_data_source_id, external_id)` rather than by a `resource_id`, so the
resources are flushed first and the chunks follow in the same transaction —
a run cannot leave chunks pointing at resources that were never written, and a
failure halfway through leaves neither. `last_synced_at` is written there too,
because it means *the last ingestion that completed* and the two facts should
not be able to disagree.

Re-running against the same source is not supported yet. A second run writes the
same `(external_data_source_id, external_id)` pairs and collides on the unique
constraint, which is recorded as a `FAILED` run. The re-ingestion strategy that
fixes this is in [todo.md](todo.md).

It calls those services unchanged. Nothing about GitHub's orchestration, Jira's
Epic/Story linking, Confluence's space confinement or Slack's filtering is
different on this path; the only thing added afterwards is the permission stamp.

### Permissions

`team_id`, `department_id` and `access_scope` are written onto every item and
every chunk the run produced, from the request that started it. Neither a
connector nor a parser nor a chunker touches them — permissions come from the
request, never from GitHub, Jira, Confluence or Slack.

Doing it in one place, after the chunker, is also the only arrangement in which
a chunk's copy of those three columns cannot drift from its resource's. The
drift is real and [todo.md](todo.md) describes the case that still causes it: a
resource whose scope is *changed later* has to have its chunks rewritten, and
nothing does that yet.

### The run file

`app/data/runs/<source>_<external_data_source_id>.json`, gitignored.

```json
{
  "source": { "external_data_source_id": "...", "name": "TrackIt API", "config": {} },
  "sync_run_id": "cccd011c-f434-4ab6-8a11-c1a96742fab7",
  "status": "COMPLETED",
  "started_at": "2026-08-16T14:28:38.954+00:00",
  "completed_at": "2026-08-16T14:28:38.993+00:00",
  "result": { "resource_files": [], "chunks": [], "counts": {} }
}
```

`result` is the same JSON the source's own endpoint returns, through the same
projection — one definition of what a run looks like, whether it arrived over
HTTP or was written to a file. It is written with every item and every chunk,
each carrying its complete text and its complete vector.

**The difference from a per-source run is what the items know about themselves.**
Every resource file and every chunk here carries `team_id`, `department_id`,
`access_scope` and `external_data_source_id`; through `POST /api/v1/github/ingest`
all four are null. No connector can answer any of them — a parser is handed a
repository, not a team and not a connected source — so they are declared together
on `PermissionScope` and stamped once, over the resource files and the chunks
alike, by `_apply_source_context` in
`app/background/pipeline/ingestion_pipeline.py`.

`external_data_source_id` is the one that makes the run writable: it is half of
the `(external_data_source_id, external_id)` pair that ties a chunk to its
resource, and the source row is committed before the run is even scheduled, so
it is in hand long before the first resource is written.

**The `source` block never contains `token`.** It is assembled field by field
rather than dumped, precisely so that column cannot ride along by accident.

A run that fails writes the same file with `"status": "FAILED"`, a null `result`
and the client-safe error message. Nothing re-raises — a background task has no
client left to answer, so a failure that vanished would be invisible.

**The file is scaffolding, not a feature.** The rows are now the record — the
run's own row in `sync_runs`, its output in `resources` and `chunks` — and the
file is written beside them because it is still the quickest way to read a whole
run without a query. It goes away once the tables have been trusted for a while.

## Errors

| Status | When |
| --- | --- |
| `404` | The path segment is not one of the four sources. The message names what is supported. |
| `400` | `source_type` disagrees with the path, `config` is missing a key this source needs, or `team_id` / `department_id` / `created_by_user_id` names no existing row. The message names what was wrong. |
| `422` | The body is unusable — a malformed UUID, a blank title, an unknown field. |

The foreign-key `400` is the one that only exists now that the row is written.
This endpoint is not authenticated yet, so those three ids are taken from the
body on trust;
the insert is the first thing that checks them, and a caller who invented one
gets a `400` rather than a `500`.

Everything after the `202` fails into the `sync_runs` row and the run file rather
than into a response, because by then there is nothing left to answer.

## What this does not do yet

- **No `SourceCredentials` row**, and the token is held in plain text on the
  source. Both are in [todo.md](todo.md).
- **No re-ingestion.** A second run against the same source collides on
  `resources`' unique constraint and is recorded as `FAILED`. Deciding what a
  second run *should* do — skip, replace, or version — is in [todo.md](todo.md).
- **No read endpoint.** `external_data_source_id` and `sync_run_id` are the
  handles a future one will take; today they are read with SQL.
- **No authentication.** `created_by_user_id` and `team_id` are taken from the
  body and trusted, because there is nothing yet to check them against — only
  that the rows exist.
