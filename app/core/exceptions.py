class WorkspaceAlreadyExistsError(Exception):
    default_message = "Workspace has already been created."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class OrganizationError(Exception):
    status_code: int = 500
    default_message: str = "Organization request failed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class DepartmentNotFoundError(OrganizationError):
    status_code = 404
    default_message = "Department not found."


class TeamNotFoundError(OrganizationError):
    status_code = 404
    default_message = "Team not found."


class TeamDepartmentMismatchError(OrganizationError):
    status_code = 400
    default_message = "department_id does not match the selected team."


class DepartmentAlreadyExistsError(OrganizationError):
    status_code = 409
    default_message = "Department already exists."


class TeamAlreadyExistsError(OrganizationError):
    status_code = 409
    default_message = "Team already exists in this department."


class EmployeeAlreadyExistsError(OrganizationError):
    status_code = 409
    default_message = "An employee with this email already exists."


class ApplicationAuthError(Exception):
    status_code: int = 500
    default_message: str = "Authentication failed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class InvalidCredentialsError(ApplicationAuthError):
    status_code = 401
    default_message = "Invalid email or password."


class InvalidAccessTokenError(ApplicationAuthError):
    status_code = 401
    default_message = "Invalid or missing access token."


class JWTConfigurationError(ApplicationAuthError):
    status_code = 500
    default_message = (
        "JWT authentication is not configured correctly on this server."
    )


class IngestionError(Exception):
    """Base class for every failure the ingestion pipeline reports to a client.
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
    """

    status_code = 401
    default_message = (
        "Jira rejected the supplied credentials. Check the email address and "
        "that the API token is valid and has not been revoked."
    )


class JiraPermissionError(IngestionError):
    """The account authenticated, but is not allowed to do this.
    """

    status_code = 403
    default_message = (
        "The supplied Jira account does not have permission to read this project."
    )


class JiraNotFoundError(IngestionError):
    """No such Jira site or project, or the account cannot see it.
    """

    status_code = 404
    default_message = (
        "The requested Jira project does not exist, or the account cannot see it."
    )


class JiraRateLimitError(IngestionError):
    """Jira is throttling this account.
    """

    status_code = 429
    default_message = "Jira is rate limiting this account. Wait before retrying."


class JiraApiError(IngestionError):
    """Jira could not be reached, or returned something we cannot act on.
    """

    status_code = 502
    default_message = "The Jira API could not be reached or returned an error."


class ConfluenceAuthenticationError(IngestionError):
    """Confluence rejected the email/API-token pair itself.
    """

    status_code = 401
    default_message = (
        "Confluence rejected the supplied credentials. Check the email address "
        "and that the API token is valid and has not been revoked."
    )


class ConfluencePermissionError(IngestionError):
    """The account authenticated, but is not allowed to do this.
    """

    status_code = 403
    default_message = (
        "The supplied Atlassian account does not have permission to read this "
        "Confluence space."
    )


class ConfluenceNotFoundError(IngestionError):
    """No such Confluence site or space, or the account cannot see it.
    """

    status_code = 404
    default_message = (
        "The requested Confluence space does not exist, or the account cannot "
        "see it."
    )


class ConfluenceRateLimitError(IngestionError):
    """Confluence is throttling this account.
    """

    status_code = 429
    default_message = (
        "Confluence is rate limiting this account. Wait before retrying."
    )


class ConfluenceApiError(IngestionError):
    """Confluence could not be reached, or returned something we cannot act on.
    """

    status_code = 502
    default_message = (
        "The Confluence API could not be reached or returned an error."
    )


class SlackAuthenticationError(IngestionError):
    """Slack rejected the bot token itself.
    """

    status_code = 401
    default_message = (
        "Slack rejected the supplied token. Check that it is a valid bot token "
        "and has not been revoked."
    )


class SlackPermissionError(IngestionError):
    """The token is real, but this workspace will not let it read this channel.
    """

    status_code = 403
    default_message = (
        "The supplied Slack token is not allowed to read this channel's "
        "history. Check that the app has the channels:history or "
        "groups:history scope and that the bot is a member of the channel."
    )


class SlackNotFoundError(IngestionError):
    """No such channel, or this token cannot see it.
    """

    status_code = 404
    default_message = (
        "The requested Slack channel does not exist, or this token cannot see "
        "it."
    )


class SlackRateLimitError(IngestionError):
    """Slack is throttling this token.
    """

    status_code = 429
    default_message = "Slack is rate limiting this token. Wait before retrying."


class SlackApiError(IngestionError):
    """Slack could not be reached, or returned something we cannot act on.
    """

    status_code = 502
    default_message = "The Slack API could not be reached or returned an error."


class EmbeddingConfigurationError(IngestionError):
    """The embedding endpoint is not configured on this deployment.
    """

    status_code = 500
    default_message = (
        "The embedding service is not configured on this server. Set "
        "AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT."
    )


class EmbeddingError(IngestionError):
    """The embedding API failed, or answered with something we cannot trust.
    """

    status_code = 502
    default_message = (
        "The embedding service could not be reached or returned an "
        "unusable response."
    )


class LLMConfigurationError(IngestionError):
    """The chat model is not configured on this deployment.
    """

    status_code = 500
    default_message = (
        "The chat model is not configured on this server. Set "
        "AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY and "
        "AZURE_OPENAI_CHAT_DEPLOYMENT."
    )


class LLMError(IngestionError):
    """The chat model failed, or answered with something we cannot use.
    """

    status_code = 502
    default_message = (
        "The chat model could not be reached or returned an unusable response."
    )



class RetrievalExecutionError(IngestionError):
    """A retrieval plan could not be run.
    """

    status_code = 500
    default_message = "The retrieval plan could not be executed."


class EmptyQueryError(IngestionError):
    """There was no question to understand.
    """

    status_code = 400
    default_message = "A query is required."


class DatabaseConfigurationError(IngestionError):
    """`DATABASE_URL` is not set on this deployment.
    """

    status_code = 500
    default_message = (
        "The database is not configured on this server. Set DATABASE_URL."
    )
