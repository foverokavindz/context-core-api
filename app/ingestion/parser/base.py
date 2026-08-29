
from abc import ABC, abstractmethod

from app.models.github.chunk import CodeChunk
from app.models.github.file import RepositoryFile


class BaseParser(ABC):
    """Turns one source file into logical code chunks."""

    @abstractmethod
    def supports(self, extension: str | None) -> bool:
        """True if this parser can handle files with the given extension."""

    @abstractmethod
    def parse(
        self,
        file: RepositoryFile,
        warnings: list[str] | None = None,
    ) -> list[CodeChunk]:
        """Extract chunks from `file`.
        """


class ParserRegistry:
    """Routes a file to the parser that handles its extension.
    """

    def __init__(self, parsers: list[BaseParser] | None = None) -> None:
        self._parsers: list[BaseParser] = list(parsers or [])

    def register(self, parser: BaseParser) -> None:
        self._parsers.append(parser)

    def parser_for(self, extension: str | None) -> BaseParser | None:
        """Return the first parser that handles `extension`, or None."""
        for parser in self._parsers:
            if parser.supports(extension):
                return parser
        return None
