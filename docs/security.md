# Security

[← Documentation](README.md)

Every token arrives as a pydantic `SecretStr`, which renders as `**********` in
every repr, log line and serialisation. On the four per-source endpoints each is
unwrapped exactly once — on the line that constructs the GitHub client, the line
that builds the Jira or Confluence client's Basic-auth pair, or the line that
builds Slack's `Authorization: Bearer` header — and never assigned to an
attribute, so nothing a traceback or a debug log could print holds it.

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

Nothing else about the rule is relaxed, and three things hold it in place:

- **It is not written to disk.** Nothing persists the entity — there is no
  engine — and the run file's `source` block is assembled field by field rather
  than dumped, precisely so this attribute cannot ride along by accident. The
  test suite asserts the token's literal value appears nowhere in a run file,
  for a successful run and a failed one alike.
- **It is not returned.** `IngestStartedResponse` carries an id, a source type, a
  title and a status. The test suite asserts the token appears nowhere in the
  response body.
- **It is not logged.** The controller and the service log the source id, its
  type and its display name, and no code path logs a request body.

The one thing that *is* now true and was not before: a process holding an
`ExternalDataSource` is holding a usable credential in plain text, so a memory
dump or a future careless `model_dump` of that object would expose it. That is
the cost of the shortcut, and it is why the shortcut has an entry in
[todo.md](todo.md) rather than only a comment in the code.

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

There is also **no authentication on any endpoint**. `team_id`,
`department_id` and `created_by_user_id` are taken from the request body and
trusted as sent, so the permission fields stamped onto a run's chunks record
what the caller *claimed* rather than what anyone verified. They are the right
columns to filter on later; they are not a security boundary until something
checks them.
