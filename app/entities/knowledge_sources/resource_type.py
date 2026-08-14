from enum import Enum


class ResourceType(str, Enum):

    GITHUB_FILE = "GITHUB_FILE"
    JIRA_ISSUE = "JIRA_ISSUE"
    CONFLUENCE_PAGE = "CONFLUENCE_PAGE"
    SLACK_MESSAGE = "SLACK_MESSAGE"
    DOCUMENT = "DOCUMENT"
