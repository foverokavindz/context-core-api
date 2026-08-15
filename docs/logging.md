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
```

The two counts in that last line are both worth reading. 235 is what Slack
served; 128 is what survived the filter. **A large gap there is the pipeline
working**, not a problem — it is the joins, leaves and thread replies going
where they belong.

**Message text is never logged at any level, not even DEBUG**, where only
timestamps appear. This is the same rule Confluence applies to page bodies and it
matters more here: a wiki page is documentation somebody chose to write down,
while a channel is a conversation nobody expected to be quoted from.
