# Security

[← Documentation](README.md)

Every connector token arrives as a pydantic `SecretStr`, which renders as
`**********` in every repr, log line and serialisation. On the four per-source
endpoints each is unwrapped exactly once — on the line that constructs the
GitHub client, the line that builds the Jira or Confluence client's Basic-auth
pair, or the line that builds Slack's `Authorization: Bearer` header — and never
assigned to an attribute, so nothing a traceback or a debug log could print
holds it.

On those four paths a token is never logged, persisted, included in a response,
or written to disk. The connector is closed as soon as fetching finishes, so the
authenticated session does not outlive the request. Parsing happens afterwards,
without it.

## The exception, and its boundaries

`POST /api/v1/ingestData/{external_source}` breaks the "never assigned to an
attribute" half of that rule, knowingly. It writes the unwrapped token onto
`ExternalDataSource.token`, so that a later sync can re-run an ingestion without
asking the user for the token again. The value is stored as sent — not hashed,
which would make it useless for that purpose, and not encrypted, which is the
thing that has not been built. Its proper home is a `SourceCredentials` row and
`encrypted_secret`; both halves of that debt are in [todo.md](todo.md).

**That row is now written.** `external_data_sources.token` used to hold a secret
only for the lifetime of a process; since the persistence layer landed it holds
one on disk, in a column, in plain text. A database dump contains usable tokens,
and so does a replica, a backup and anything a `SELECT *` reaches. This is the
single largest security debt in the project and it is in [todo.md](todo.md)
under *Credentials*; nothing below reduces it.

What is *not* relaxed, and the three things that hold it in place:

- **It reaches the database and nowhere else on disk.** The run file's `source`
  block is assembled field by field rather than dumped, precisely so this
  attribute cannot ride along by accident. The test suite asserts the token's
  literal value appears nowhere in a run file, for a successful run and a failed
  one alike.
- **It is not returned.** `IngestStartedResponse` carries two ids, a source type,
  a title and a status. The test suite asserts the token appears nowhere in the
  response body.
- **It is not logged.** The controller, the service and the pipeline log the
  source id, the run id, the source type and the display name, and no code path
  logs a request body. Database failures are the newest way this could break and
  are handled explicitly: a `SQLAlchemyError` is logged in full server-side, but
  what reaches `sync_runs.error_message` is a fixed string — never `str(exc)`,
  which for a connection error carries the `DATABASE_URL` and its password.

A process holding an `ExternalDataSource` is also holding a usable credential in
plain text, so a memory dump or a careless `model_dump` of that object would
expose it. That is the same cost in a different place.

For Jira and Confluence specifically: the account email is treated as a
credential too and is kept out of log lines, which name the site, the project or
the space instead. Request headers are never logged — that is where the Basic
credential rides. Upstream error bodies *are* logged, capped at 500 characters,
but never returned. `site_url` must be `https://`, since the credential travels
on every request. Both connectors resolve the cloud ID through a **separate
unauthenticated client**, so the one request that does not need a credential
cannot acquire one.

For Slack specifically: the token rides in an `Authorization: Bearer` header and
**never as a query parameter**, which Slack's Web API would also accept. A URL
ends up in proxy logs, access logs and browser history; a header does not. The
test suite asserts both that the header is sent and that the secret never
appears anywhere in a URL. Slack's error strings — `not_in_channel`,
`missing_scope` — are logged but never returned, since they describe a
workspace's configuration rather than the caller's request. Message text is kept
out of logs entirely, at every level.

Sending a token in a request body is acceptable for this prototype. A production
deployment would use HTTPS and a proper credential-management mechanism; that is
explicitly out of scope here.

## Application authentication

`POST /api/v1/auth/login` verifies the submitted password against the user's
Argon2 hash and returns a signed JWT access token. The signing secret comes from
`JWT_SECRET`; it is never returned or logged. Tokens carry only the user ID,
application role, team ID, department ID, and issued/expiry timestamps.

`GET /api/v1/auth/me` is protected by the reusable bearer-token dependency and
reloads the user from the database. Other endpoints are not migrated in this
milestone: ingestion and retrieval requests that contain `team_id`,
`department_id`, or `created_by_user_id` still trust those client-supplied
values. The JWT context makes that migration possible, but is not yet a resource
authorization boundary.
