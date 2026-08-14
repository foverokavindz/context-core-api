from enum import Enum


class CredentialType(str, Enum):

    GITHUB = "GITHUB"
    JIRA = "JIRA"
    CONFLUENCE = "CONFLUENCE"
    SLACK = "SLACK"
