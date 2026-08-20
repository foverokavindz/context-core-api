"""Errors that the ingestion pipeline raises, and the HTTP status each maps to.

Every message here is written to be safe to hand straight to an API client. The
upstream API's own error text - GitHub's or Jira's - is deliberately NOT folded
into these messages; it is logged server-side instead. That keeps two things out
of responses: internal detail, and any chance of echoing back credentials.

The GitHub errors come first, then the Jira ones, then the Confluence ones, then
the Slack ones, then the embedding ones, then the chat-model ones, and the
database one last. They are
kept as separate classes
rather than shared because the wording a client sees should name the system that
actually failed, and because the vendors do not agree on what a given status
means - GitHub's 403 is ambiguous, Jira's is not.

Jira and Confluence are both Atlassian, and their statuses do line up. They still
get their own classes: a run ingests one or the other, never both, so a message
naming the wrong product would be actively misleading.

The Slack family is the odd one out, and not because of its wording. The other
three vendors report failure with an HTTP status; Slack answers `200 OK` with
`{"ok": false, "error": "invalid_auth"}` in the body. So a Slack error is chosen
by matching that error *string*, not by reading a status code. The classes look
the same from the outside, which is the point - what varies is how the connector
arrives at one.
"""


class IngestionError(Exception):
    """Base class for every failure the ingestion pipeline reports to a client.

    `status_code` is the HTTP status the API returns. `message` is the entire
    body the client sees, so it must never contain a token, a header, or an
    internal stack detail.
    """

    status_code: int = 500
    default_message: str = "Ingestion failed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class AuthenticationError(IngestionError):
    """The token was missing, malformed, expired or revoked."""

    status_code = 401
    default_message = (
        "GitHub rejected the supplied credentials. "
        "Check that the token is valid and has not expired."
    )


class RepositoryNotFoundError(IngestionError):
    """No such repository, or the token cannot see it.

    GitHub deliberately returns 404 rather than 403 for private repositories a
    token may not read, so these two cases are genuinely indistinguishable here.
    """

    status_code = 404
    default_message = (
        "Repository not found, or the supplied token does not have access to it."
    )


class BranchNotFoundError(IngestionError):
    """The requested branch does not exist on the repository."""

    status_code = 404
    default_message = "The requested branch does not exist in this repository."


class RateLimitError(IngestionError):
    """The GitHub API rate limit was exhausted."""

    status_code = 429
    default_message = (
        "The GitHub API rate limit has been exhausted. Wait for the limit to "
        "reset before retrying."
    )


class SourceUnavailableError(IngestionError):
    """GitHub could not be reached, or returned something we cannot act on."""

    status_code = 502
    default_message = "The GitHub API could not be reached or returned an error."


class UnsupportedFileError(IngestionError):
    """No parser is registered for this file type.

    Raised per file and recorded against that file - it never aborts a run.
    """

    status_code = 400
    default_message = "No parser is available for this file type."


class JiraAuthenticationError(IngestionError):
    """Jira rejected the email/API-token pair itself.

    Jira Cloud answers 401 when the credentials are wrong, expired or revoked,
    and 403 when they are valid but insufficient - so unlike GitHub there is no
    need to inspect a response body to tell the two apart.
    """

    status_code = 401
    default_message = (
        "Jira rejected the supplied credentials. Check the email address and "
        "that the API token is valid and has not been revoked."
    )


class JiraPermissionError(IngestionError):
    """The account authenticated, but is not allowed to do this.

    The only 403 in this codebase. GitHub's 403 deliberately becomes an
    AuthenticationError because it also means "rate limited"; Jira's does not,
    so it keeps its own status.
    """

    status_code = 403
    default_message = (
        "The supplied Jira account does not have permission to read this project."
    )


class JiraNotFoundError(IngestionError):
    """No such Jira site or project, or the account cannot see it.

    Jira hides projects an account may not read rather than returning 403, so
    those two cases are genuinely indistinguishable here - the same situation
    RepositoryNotFoundError describes for private GitHub repositories.
    """

    status_code = 404
    default_message = (
        "The requested Jira project does not exist, or the account cannot see it."
    )


class JiraRateLimitError(IngestionError):
    """Jira is throttling this account.

    Returned to the caller immediately. Nothing in the connector sleeps waiting
    for a limit to reset - that would stall a request for minutes.
    """

    status_code = 429
    default_message = "Jira is rate limiting this account. Wait before retrying."


class JiraApiError(IngestionError):
    """Jira could not be reached, or returned something we cannot act on.

    Covers network failures, timeouts, unreadable bodies, and any status the
    other Jira errors do not claim.
    """

    status_code = 502
    default_message = "The Jira API could not be reached or returned an error."


class ConfluenceAuthenticationError(IngestionError):
    """Confluence rejected the email/API-token pair itself.

    Confluence Cloud answers 401 for wrong, expired or revoked credentials and
    403 for valid-but-insufficient ones, the same split Jira makes - so the
    status alone is enough to tell them apart.
    """

    status_code = 401
    default_message = (
        "Confluence rejected the supplied credentials. Check the email address "
        "and that the API token is valid and has not been revoked."
    )


class ConfluencePermissionError(IngestionError):
    """The account authenticated, but is not allowed to do this.

    A scoped token missing `read:space:confluence` or `read:page:confluence`
    lands here rather than on the 401 above: the credential is real, it simply
    does not carry the scope this ingestion needs.
    """

    status_code = 403
    default_message = (
        "The supplied Atlassian account does not have permission to read this "
        "Confluence space."
    )


class ConfluenceNotFoundError(IngestionError):
    """No such Confluence site or space, or the account cannot see it.

    Confluence omits spaces an account may not read from a keyed lookup rather
    than returning 403, so an invisible space and a nonexistent one are
    genuinely indistinguishable here - the same situation JiraNotFoundError and
    RepositoryNotFoundError describe.
    """

    status_code = 404
    default_message = (
        "The requested Confluence space does not exist, or the account cannot "
        "see it."
    )


class ConfluenceRateLimitError(IngestionError):
    """Confluence is throttling this account.

    Returned to the caller immediately. Nothing in the connector sleeps waiting
    for a limit to reset - that would stall a request for minutes.
    """

    status_code = 429
    default_message = (
        "Confluence is rate limiting this account. Wait before retrying."
    )


class ConfluenceApiError(IngestionError):
    """Confluence could not be reached, or returned something we cannot act on.

    Covers network failures, timeouts, unreadable bodies, and any status the
    other Confluence errors do not claim.
    """

    status_code = 502
    default_message = (
        "The Confluence API could not be reached or returned an error."
    )


class SlackAuthenticationError(IngestionError):
    """Slack rejected the bot token itself.

    Reached through `invalid_auth`, `not_authed`, `token_revoked`,
    `token_expired` and `account_inactive` - all of which Slack reports in the
    body of a 200 response rather than as a 401. The connector is what turns
    them into this; nothing downstream sees the string.
    """

    status_code = 401
    default_message = (
        "Slack rejected the supplied token. Check that it is a valid bot token "
        "and has not been revoked."
    )


class SlackPermissionError(IngestionError):
    """The token is real, but this workspace will not let it read this channel.

    Two quite different causes land here, and Slack names them separately:
    `missing_scope` means the token was created without `channels:history` or
    `groups:history`, and `not_in_channel` means the scope is present but the
    bot was never invited to the conversation. Both are fixed by an
    administrator rather than by a new token, which is why they share a status.
    """

    status_code = 403
    default_message = (
        "The supplied Slack token is not allowed to read this channel's "
        "history. Check that the app has the channels:history or "
        "groups:history scope and that the bot is a member of the channel."
    )


class SlackNotFoundError(IngestionError):
    """No such channel, or this token cannot see it.

    Slack answers `channel_not_found` both for a conversation that does not
    exist and for one the token has no visibility of, so the two are genuinely
    indistinguishable here - the same situation ConfluenceNotFoundError,
    JiraNotFoundError and RepositoryNotFoundError describe for their own
    vendors.
    """

    status_code = 404
    default_message = (
        "The requested Slack channel does not exist, or this token cannot see "
        "it."
    )


class SlackRateLimitError(IngestionError):
    """Slack is throttling this token.

    Returned to the caller immediately. Slack sends a `Retry-After` header with
    its 429 and the connector logs that value, but nothing sleeps on it - that
    would stall a synchronous request for the length of a Slack cooldown.
    """

    status_code = 429
    default_message = "Slack is rate limiting this token. Wait before retrying."


class SlackApiError(IngestionError):
    """Slack could not be reached, or returned something we cannot act on.

    Covers network failures, timeouts, unreadable bodies, a response with no
    usable `ok` field, and every Slack error string the classes above do not
    claim - including ones Slack has not invented yet. An unrecognised error
    becomes this rather than an authentication failure, because guessing wrong
    about a credential sends an operator to check the wrong thing.
    """

    status_code = 502
    default_message = "The Slack API could not be reached or returned an error."


class EmbeddingConfigurationError(IngestionError):
    """The embedding endpoint is not configured on this deployment.

    A 500 rather than a 4xx: the caller asked for something reasonable and the
    server cannot honour it. The message names the missing variable but never
    its value - an API key must not reach a response, a log line or a traceback.
    """

    status_code = 500
    default_message = (
        "The embedding service is not configured on this server. Set "
        "AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT."
    )


class EmbeddingError(IngestionError):
    """The embedding API failed, or answered with something we cannot trust.

    Two quite different causes share this class, and both are fatal to a run.
    The first is an ordinary API failure - unreachable, throttled past the
    retries, or a rejected request. The second is a *mismatch*: a batch of 30
    inputs coming back with 29 vectors, or a vector of the wrong width. That
    second case is the important one. Nothing downstream can tell which chunk
    lost its vector, so guessing an alignment would silently attach the wrong
    embedding to the wrong code - which is worse than failing the run, because
    it fails at retrieval time instead, months later and invisibly.
    """

    status_code = 502
    default_message = (
        "The embedding service could not be reached or returned an "
        "unusable response."
    )


class LLMConfigurationError(IngestionError):
    """The chat model is not configured on this deployment.

    A 500 for the same reason EmbeddingConfigurationError is one, and it names
    its own deployment variable rather than the embedding one: the two point at
    different models on the same resource, and an operator told to check the
    wrong variable finds it perfectly well set.
    """

    status_code = 500
    default_message = (
        "The chat model is not configured on this server. Set "
        "AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY and "
        "AZURE_OPENAI_CHAT_DEPLOYMENT."
    )


class LLMError(IngestionError):
    """The chat model failed, or answered with something we cannot use.

    Both halves matter. The first is an ordinary API failure - unreachable,
    throttled, or a rejected request. The second is a model that answered but
    not in the shape that was asked for, which is a real possibility whenever
    output is structured and is not something to paper over: a half-understood
    question sends the whole pipeline after the wrong thing.

    The provider's own words never reach this message. Only the exception's type
    name is logged, which is enough to tell a timeout from a rejection without
    putting a key, a prompt or a traceback into a response.
    """

    status_code = 502
    default_message = (
        "The chat model could not be reached or returned an unusable response."
    )


class EmptyQueryError(IngestionError):
    """There was no question to understand.

    The chat request models already reject a blank query, so this is the guard
    for every other caller. It is raised before the model is called rather than
    after, because an empty prompt costs a round trip to learn nothing.
    """

    status_code = 400
    default_message = "A query is required."


class DatabaseConfigurationError(IngestionError):
    """`DATABASE_URL` is not set on this deployment.

    A 500 for the same reason EmbeddingConfigurationError is one: the caller
    asked for something reasonable and the server cannot honour it. The message
    names the variable and never its value - a connection URL carries a
    password, so it must not reach a response, a log line or a traceback.
    """

    status_code = 500
    default_message = (
        "The database is not configured on this server. Set DATABASE_URL."
    )
