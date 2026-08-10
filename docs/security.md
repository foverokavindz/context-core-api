# Security

[← Documentation](README.md)

Every token is held as a pydantic `SecretStr`, which renders as `**********` in
every repr, log line and serialisation. Each is unwrapped exactly once — on the
line that constructs the GitHub client, the line that builds the Jira or
Confluence client's Basic-auth pair, or the line that builds Slack's
`Authorization: Bearer` header — and never assigned to an attribute, so nothing
a traceback or a debug log could print holds it.

None is ever logged, persisted, included in a response, or written to disk. The
connector is closed as soon as fetching finishes, so the authenticated session
does not outlive the request. Parsing happens afterwards, without it.

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
