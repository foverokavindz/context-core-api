# Logging

[← Documentation](README.md)

A run narrates itself at INFO, so you can see progress instead of watching a
silent request. Nothing needs configuring — `app/main.py` sets the level.

## GitHub

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
INFO  Embedding with deployment text-embedding-3-small
INFO  Embedding 441 chunks in 15 request(s) of at most 30
INFO  [1/15] embedding 30 chunks (18422 characters)
INFO  [2/15] embedding 30 chunks (24907 characters)
...
INFO  [15/15] embedding 21 chunks (9145 characters)
INFO  Embedded 441 chunks into 1536-dimension vectors with text-embedding-3-small (15 embedding API calls) in 12.4s
```

Four things worth knowing:

- **One line per file, logged before the download.** A slow or stalled fetch is
  attributable to a named file rather than to silence.
- **The API-call count** is `4 + one per file`. Since a token gets 5,000 calls
  an hour, this is what makes a later `429` explicable rather than mysterious.
  The embedding line carries its own count for the same reason, against a
  different quota.
- **Nothing is logged per chunk or per symbol.** Log volume tracks files, not
  the code inside them — a file with twenty methods still gets one line. Parsing
  detail stays at DEBUG.
- **Embedding logs per batch, not per chunk**, which is the same principle
  applied to the unit that costs a round trip: 441 chunks are 15 requests and
  therefore 15 lines. Each is logged *before* its request, so a stalled or
  throttled call is attributable to a numbered batch.

**Chunk content is never logged at any level.** Not the source, not a preview of
it, not at DEBUG — the same rule Confluence applies to page bodies and Slack to
message text, and for the same reason: this is somebody's private source code.
Neither the API key nor the GitHub token is ever logged, put in a URL, or folded
into a message a client sees.

On a large repository that is one INFO line per file. To quiet just that stream
while keeping the rest:

```python
logging.getLogger("app.connectors.github_connector").setLevel(logging.WARNING)
logging.getLogger("app.ingestion.embedding_service").setLevel(logging.WARNING)
```

A failed embedding batch logs at ERROR before it raises, naming the batch number
and what was wrong with it — a count that did not match what was sent, or a
vector of the wrong width. Those two are the failures that would otherwise put
the wrong vector on the wrong chunk, so they are worth finding in a log
afterwards rather than only in a 502.

## Jira

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
INFO  Embedding 124 chunks in 5 request(s) of at most 30
INFO  [1/5] embedding 30 chunks (18422 characters)
...
INFO  Embedded 124 chunks into 1536-dimension vectors with text-embedding-3-small (5 embedding API calls) in 3.1s
```

**Log volume tracks pages, not issues.** A hundred issues arrive in one API
call, so a per-issue line would say nothing about where time went; per-issue
detail stays at DEBUG. This is the same principle as GitHub's one-line-per-file,
applied to the unit that actually costs a round trip.

## Confluence

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
INFO  Embedding 124 chunks in 5 request(s) of at most 30
INFO  [1/5] embedding 30 chunks (98455 characters)
...
INFO  Embedded 124 chunks into 1536-dimension vectors with text-embedding-3-small (5 embedding API calls) in 4.6s
```

**Log volume tracks batches, not pages, and page bodies are never logged at
all** — not even at DEBUG, where only page ids appear. A wiki page can be tens of
kilobytes of prose, and putting that in a log would be noise in the best case and
a leak of internal documentation in the worst.

## Slack

A Slack run is the shortest of the four, since there is nothing to resolve first:

```
INFO  Ingesting Slack channel C0123456789
INFO  Slack: reading channel history
INFO  Slack: reading channel history
INFO  Slack history page 1 returned 200 messages
INFO  Slack history page 2 returned 35 messages
INFO  Retrieved 235 Slack history items from 2 page(s) (2 Slack API calls)
INFO  Parsed 128 Slack messages
INFO  Generated 128 Slack chunks
INFO  Ingested Slack channel C0123456789 (235 history items retrieved, 128 parsed) into 128 chunks in 1.4s
INFO  Embedding 128 chunks in 5 request(s) of at most 30
INFO  [1/5] embedding 30 chunks (2841 characters)
...
INFO  Embedded 128 chunks into 1536-dimension vectors with text-embedding-3-small (5 embedding API calls) in 2.2s
```

The two counts in the `Ingested Slack channel` line are both worth reading. 235
is what Slack served; 128 is what survived the filter. **A large gap there is the
pipeline working**, not a problem — it is the joins, leaves and thread replies
going where they belong.

**Message text is never logged at any level, not even DEBUG**, where only
timestamps appear. This is the same rule Confluence applies to page bodies and it
matters more here: a wiki page is documentation somebody chose to write down,
while a channel is a conversation nobody expected to be quoted from.

## Embedding

The last two or three lines of all four runs above come from the same place:
`app.ingestion.embedding_service`, which every pipeline calls after its chunker.
So the rules described under GitHub apply identically to Jira, Confluence and
Slack — one line per request rather than per chunk, logged before the request is
made, and never any chunk text at any level.

What differs between the four is only the size of the batches, and the character
counts in those lines are the quickest way to see it. A Confluence batch of 30
wiki pages is the largest single request this application sends; a Slack batch of
30 one-line messages is the smallest by an order of magnitude. Both cost exactly
one round trip, which is why the count that matters is requests rather than
chunks.

That size difference has one consequence worth watching for, and it has only
ever been seen on Confluence:

```
INFO  2 chunk(s) exceeded 24000 characters and were truncated for embedding; their stored content is unchanged
```

A page past `MAX_EMBEDDING_INPUT_CHARS` is embedded on its opening section, so
its vector represents the beginning and not the end — the chunk stays findable by
what it starts with. The stored `content` is untouched, and the count is
reported to the caller as `counts.truncated_inputs` as well as logged, because
this is quiet data loss rather than a failure and nothing else would surface it.
A real 11-page space has hit this: two pages of 36k and 32k characters, roughly a
fifth of that corpus not reaching the model. The fix is splitting long pages into
several chunks, which `confluence_chunk.py` already describes as the next step
after the one-chunk-per-page baseline.

When a run reports no vectors, one of three lines says why:

```
INFO  Embedding skipped at the caller's request        the request set "embed": false
INFO  No embedder is configured; chunks have no vectors  a service built without one
                                                       (nothing at all)  the run produced no chunks
```

## The common ingestion endpoint

`POST /api/v1/ingestData/{external_source}` wraps one of the four runs above in
four lines of its own — two before it and two after:

```
INFO  app.services.ingestion_service: Ingestion accepted for GITHUB source 476fca4f-… (TrackIt API)
INFO  app.background.pipeline.ingestion_pipeline: Ingestion run starting for GITHUB source 476fca4f-… (TrackIt API)
   ... the whole GitHub run above, unchanged ...
INFO  app.background.pipeline.ingestion_pipeline: Ingestion run finished for GITHUB source 476fca4f-…: 97 resource files, 441 chunks
INFO  app.background.pipeline.ingestion_pipeline: Ingestion run written to github_476fca4f-….json
```

Three things worth knowing:

- **"Accepted" and "starting" are two different moments**, and the gap between
  them is the response being sent. A run that never logs "starting" was accepted
  and then never scheduled, which is a different fault from one that starts and
  fails.
- **Every line names the source id**, which is the same id the caller was handed
  and the same one in the run file's name. Interleaved runs stay attributable.
- **The display name is logged; the config and the token are not.** The name is
  something a person chose for this connection, so it is safe and it is what
  makes a log line readable. `config` can carry a site URL with an account's
  organisation in it, and the token is a credential — see
  [security.md](security.md).

A failed run logs one line instead of the last two, and the exception type is
named rather than its message:

```
ERROR app.background.pipeline.ingestion_pipeline: Ingestion run failed for GITHUB source 476fca4f-…: RepositoryNotFoundError
INFO  app.background.pipeline.ingestion_pipeline: Ingestion run written to github_476fca4f-….json
```

The message the caller would have seen goes into the run file rather than the
log, because that is where the rest of the run already is.
