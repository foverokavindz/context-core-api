from enum import Enum


class ChunkType(str, Enum):

    FILE = "FILE"
    CLASS = "CLASS"
    METHOD = "METHOD"
    FUNCTION = "FUNCTION"

    ISSUE = "ISSUE"
    PAGE = "PAGE"
    MESSAGE = "MESSAGE"
    EPIC = "EPIC"

    DOCUMENT = "DOCUMENT"
    DOCUMENT_SECTION = "DOCUMENT_SECTION"
