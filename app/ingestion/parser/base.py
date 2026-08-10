"""The shape every parser follows, plus the registry that picks one.

A parser takes a RepositoryFile - our own model - and returns CodeChunks. It
never sees a PyGithub object, a URL or a token. That is what makes the parsing
layer reusable for any future source: once something has been normalised into a
RepositoryFile, the parser does not care where it came from.
"""

from abc import ABC, abstractmethod

from app.models.code_chunk import CodeChunk
from app.models.repository_file import RepositoryFile


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

        Implementations should degrade rather than raise: a file whose syntax
        cannot be understood should still yield something useful (at minimum a
        whole-file chunk) so no source is silently lost. Anything noteworthy
        that did not justify failing - a syntax error the parser recovered
        from, say - is appended to `warnings` when one is supplied.
        """


class ParserRegistry:
    """Routes a file to the parser that handles its extension.

    Deliberately trivial - a list and a loop. It exists so adding a Python or
    Java parser later is a registration call rather than an `if` in the
    ingestion service.
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
