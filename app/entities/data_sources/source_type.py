from enum import Enum


class SourceType(str, Enum):

    GITHUB = "GITHUB"
    JIRA = "JIRA"
    CONFLUENCE = "CONFLUENCE"
    SLACK = "SLACK"
