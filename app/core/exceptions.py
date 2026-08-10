"""Errors that the ingestion pipeline raises, and the HTTP status each maps to.

Every message here is written to be safe to hand straight to an API client. The
upstream API's own error text - GitHub's or Jira's - is deliberately NOT folded
into these messages; it is logged server-side instead. That keeps two things out
of responses: internal detail, and any chance of echoing back credentials.

The GitHub errors come first, then the Jira ones. They are kept as separate
classes rather than shared because the wording a client sees should name the
system that actually failed, and because the two vendors do not agree on what a
given status means - GitHub's 403 is ambiguous, Jira's is not.
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
