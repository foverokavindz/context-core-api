from enum import Enum


class EntityType(str, Enum):
    """small controlled vocabulary for identifying what important things as entities the user mentioned"""

    # Jira
    JIRA_TICKET = "JIRA_TICKET"
    JIRA_EPIC = "JIRA_EPIC"

    # GitHub
    REPOSITORY = "REPOSITORY"
    CODE_FILE = "CODE_FILE"
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    PULL_REQUEST = "PULL_REQUEST"
    COMMIT = "COMMIT"

    # Confluence
    CONFLUENCE_PAGE = "CONFLUENCE_PAGE"
    CONFLUENCE_SPACE = "CONFLUENCE_SPACE"

    # Slack
    SLACK_CHANNEL = "SLACK_CHANNEL"
    SLACK_THREAD = "SLACK_THREAD"

    # Domain concepts
    SERVICE = "SERVICE"
    COMPONENT = "COMPONENT"
    FEATURE = "FEATURE"
    TECHNOLOGY = "TECHNOLOGY"

    # Organization
    PERSON = "PERSON"
    TEAM = "TEAM"

    UNKNOWN = "UNKNOWN"