from enum import Enum


class SourceStatus(str, Enum):

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ERROR = "ERROR"
