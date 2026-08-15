from enum import Enum


class DocumentStatus(str, Enum):

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
