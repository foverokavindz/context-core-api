# Context Core — GitHub Ingestion API

Stage one of a RAG ingestion pipeline. It takes a GitHub token and a repository,
pulls the TypeScript source out through the GitHub REST API, filters the noise,
and parses what is left into logical code chunks with Tree-sitter.

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

## What it does not do — yet

Deliberately absent, so the ingestion path stays small enough to understand and
control end to end:

- no embeddings, no vector store, no pgvector
- no database of any kind
- no retrieval, reranking or LLM calls
- no background queues, webhooks or incremental indexing
- no `git clone` — everything goes through the GitHub API
- no LangChain or LlamaIndex

`CodeChunk[]` is the handover point. A later phase can embed and store those
objects without touching the connector, the filter or the parser.

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

## Calling the endpoint

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

## Architecture

```
app/
├── main.py                      FastAPI app, logging, error handler
├── api/github_routes.py         the endpoint (thin)
├── connectors/
│   ├── base.py                  BaseSourceConnector, SourceSnapshot
│   └── github_connector.py      the only module that imports PyGithub
├── ingestion/
│   ├── ingestion_service.py     orchestration
│   ├── file_filter.py           which paths are worth ingesting
│   └── parser/
│       ├── base.py              BaseParser + ParserRegistry
│       └── typescript_parser.py Tree-sitter TS/TSX
├── models/
│   ├── github_request.py        GitHubIngestRequest
│   ├── repository_file.py       RepositoryFile  <- the boundary
│   ├── code_chunk.py            CodeChunk
│   └── ingest_response.py       response DTOs + limits
├── core/exceptions.py           error types and their HTTP statuses
└── tests/
```

### The boundary that matters

```
GitHub  ->  GitHubConnector  ->  RepositoryFile  ->  filter/parser/chunks
                                 ^^^^^^^^^^^^^^
                            nothing past here knows about GitHub
```

`github_connector.py` is the only file importing PyGithub. The parser accepts a
`RepositoryFile` and nothing else. Adding a Jira or Confluence source later means
writing another `BaseSourceConnector` that produces `RepositoryFile` objects —
the filter, parsers and chunking need no changes.

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

## Security

The token is held as a pydantic `SecretStr`, which renders as `**********` in
every repr, log line and serialisation. It is unwrapped exactly once — on the
line that constructs the GitHub client — and never assigned to an attribute, so
nothing a traceback or a debug log could print holds it.

It is never logged, never persisted, never included in a response, and never
written to disk. The connector is closed as soon as fetching finishes, so the
authenticated session does not outlive the request. Parsing happens afterwards,
without it.

Sending a token in a request body is acceptable for this prototype. A production
deployment would use HTTPS and a proper credential-management mechanism; that is
explicitly out of scope here.

## Running tests

```bash
pytest app/tests -v
```

171 tests, no network access and no token required. PyGithub is replaced with
fakes that record which API calls were made — which is how the suite proves that
ignored files are never downloaded, and that a token never reaches a response,
a log or an error message.

| Module                      | Covers                                                                                                                    |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `test_file_filter.py`       | every include/exclude rule, plus segment- and suffix-matching regressions                                                 |
| `test_typescript_parser.py` | each symbol kind, parent links, exact source spans, line ranges, TSX, fallbacks, syntax errors, the line-index regression |
| `test_github_connector.py`  | branch resolution, filter-before-fetch, binary/UTF-8 skips, error mapping, token containment                              |
| `test_ingestion_service.py` | the real pipeline end to end with only the network faked                                                                  |
| `test_api.py`               | request validation, response projection, HTTP status mapping, token never echoed                                          |

## Manual verification against a real repository

1. `uvicorn app.main:app --reload`
2. Open <http://localhost:8000/docs> and call the endpoint with a real token, or
   use the `curl` command above.
3. Check that `discovered_files` is larger than `accepted_files` (the filter is
   working), that `.ts` and `.tsx` paths appear in `files`, and that
   `sample_chunks` contains a `method` with a `parent_symbol`.
4. Check the server log — it reports the repository, branch, commit and counts,
   and contains no credential.
