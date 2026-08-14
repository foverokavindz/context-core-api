from enum import Enum


class ResourceAccessScope(str, Enum):

    TEAM = "TEAM"
    DEPARTMENT = "DEPARTMENT"
    ORGANIZATION = "ORGANIZATION"
