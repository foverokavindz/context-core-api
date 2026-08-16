# GitHub

[← Documentation](../README.md)

Takes a token and a repository, pulls the TypeScript source out through the
GitHub REST API, filters the noise, and parses what is left into logical code
chunks with Tree-sitter.

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

## Expected response

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

	"resource_files": [
		{
			"repository": "my-org/backend",
			"branch": "main",
			"commit_sha": "abc123",
			"path": "src/auth/AuthService.ts",
			"file_name": "AuthService.ts",
			"extension": ".ts",
			"file_sha": "9f2c1ab",
			"language": "typescript",
			"size": 2450,
			"team_id": null,
			"department_id": null,
			"access_scope": "TEAM"
		}
	],

	"chunks": [
		{
			"repository": "my-org/backend",
			"branch": "main",
			"commit_sha": "abc123",
			"file_path": "src/auth/AuthService.ts",
			"file_name": "AuthService.ts",
			"extension": ".ts",
			"file_sha": "9f2c1ab",
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

| Constant                  | Default | Meaning                       |
| ------------------------- | ------- | ----------------------------- |
| `SAMPLE_FILES_LIMIT`      | 10      | files listed in the response  |
| `SAMPLE_CHUNKS_LIMIT`     | 20      | chunks listed in the response |
| `MAX_FILES_PER_INGESTION` | 500     | files downloaded per request  |

The internal `IngestionResult` always holds **every** file and **every** chunk —
only the HTTP projection is sampled, and it samples by *count* alone. Whatever
is listed is listed whole: full source in `content`, the complete 1536-float
vector in `embedding`. There is no preview form of either.

> **The whole repository is always processed.** A response showing 10 files and
> 20 chunks while reporting `accepted_files: 98` and `generated_chunks: 441` is
> not a partial run — it is the complete run, sampled for display. The counts
> are the truth; `resource_files` and `chunks` are a window onto it.

> **`repository`, `branch` and `commit_sha` repeat on every file and chunk.**
> They are also reported once at the top level, and for a single-branch run the
> values are identical — the repetition is so one entry lifted out of either
> list still says which commit of which branch it was read from.
> `file_sha` is the blob SHA: it is what tells a re-ingestion that a file's
> contents changed, and it is the natural `version_key` for the `resources` row.

> **`resource_files` is the same key on all four endpoints.** A GitHub file, a
> Jira issue, a Confluence page and a Slack message all arrive under it — each
> one becomes a `resources` row, which is what the name is for.

> **The three permission fields are null here, and that is correct.** This
> endpoint takes a token and a repository and knows nothing about a team, so
> `team_id` and `department_id` serialise as null and `access_scope` as its
> default. `POST /api/v1/ingestData/github` is the path that fills them in —
> see [../architecture.md](../architecture.md).

To see everything, send `"full": true`:

```bash
curl -X POST http://localhost:8000/api/v1/github/ingest \
  -H "Content-Type: application/json" \
  -d '{"token":"YOUR_TOKEN","repository":"ORG/REPO","full":true}'
```

That returns all accepted files and all chunks instead of the sampled ten and
twenty. The counts are identical either way — only how many entries are
serialised changes. Expect tens of megabytes on a real repository, since every
chunk carries its own vector.

`truncated: true` is the separate signal that the run really did see only part
of the repository: either `max_files` was reached, or GitHub truncated its own
tree listing.

## Errors

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

## How a repository is walked

```
repository -> branch (or default) -> HEAD commit SHA -> commit's tree SHA
           -> recursive git tree -> filter paths -> fetch only what survived
```

The recursive tree API returns every path in one call, which is what makes it
possible to filter _before_ downloading. Ignored files never cost an API call.
The commit is resolved to its tree SHA explicitly, because the tree endpoint
takes a tree SHA — the commit SHA is what gets stamped onto every file and chunk.

## Manual verification against a real repository

1. `uvicorn app.main:app --reload`
2. Open <http://localhost:8000/docs> and call the endpoint with a real token, or
   use the `curl` command above.
3. Check that `discovered_files` is larger than `accepted_files` (the filter is
   working), that `.ts` and `.tsx` paths appear in `resource_files`, and that
   `chunks` contains a `method` with a `parent_symbol`.
4. Check the server log — it reports the repository, branch, commit and counts,
   and contains no credential.
