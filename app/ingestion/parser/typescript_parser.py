"""Parses TypeScript and TSX into logical code chunks using Tree-sitter.

Why an AST and not regex: regex cannot tell a method from a function inside a
string literal, and cannot find where a body actually ends. Every structural
decision here comes from the syntax tree. The only string work is reading the
text of a node the grammar already identified.

Chunking strategy
-----------------
The unit of a chunk is a syntax node, never a character count. A function is
never split because it got long, and two unrelated declarations are never glued
together because they were short.

For a class we emit the class itself *and* each of its methods:

    export class AuthService {   ->  chunk: class  AuthService
        login() { ... }          ->  chunk: method AuthService.login
        logout() { ... }         ->  chunk: method AuthService.logout
    }

The class chunk contains the whole class, so each method body appears twice -
once inside the class chunk and once on its own. That duplication is deliberate:
it keeps full class context retrievable alongside the granular methods. Set
`ChunkingConfig.emit_full_class_body = False` to shrink the class chunk to just
its declaration header (through the opening brace), which removes the
duplication entirely while keeping the `extends`/`implements` context.

Nesting: we descend into classes and namespaces, but never into a function
body. A helper declared inside a function stays part of that function's chunk
rather than becoming a competing chunk of its own.

Fallback: a file that yields no recognised symbol - a file of constants, say -
still produces one whole-file chunk, so no source is silently dropped.
"""

import bisect
import logging
from dataclasses import dataclass

import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from app.ingestion.parser.base import BaseParser
from app.models.code_chunk import CodeChunk
from app.models.repository_file import RepositoryFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkingConfig:
    """Knobs for how aggressively code is broken up."""

    # Emit a chunk for the class itself, not only for its members.
    emit_class_chunks: bool = True

    # True:  the class chunk holds the entire class, method bodies included.
    # False: the class chunk stops at the opening brace, so it carries the
    #        declaration and heritage clause with no duplicated bodies.
    emit_full_class_body: bool = True


# --------------------------------------------------------------------- grammars

# `.tsx` needs its own grammar: in the plain TypeScript grammar `<T>` is a type
# assertion, while in TSX it opens a JSX element. Using the wrong one turns every
# React component into a syntax error.
_GRAMMAR_LOADERS = {
    ".ts": tstypescript.language_typescript,
    ".tsx": tstypescript.language_tsx,
}

# Grammars are compiled once and reused. Building a Language is not free, and
# they are immutable and safe to share.
_LANGUAGES: dict[str, Language] = {}


def _language_for(extension: str) -> Language:
    """Return (and cache) the grammar for a file extension."""
    key = extension.lower()
    if key not in _LANGUAGES:
        _LANGUAGES[key] = Language(_GRAMMAR_LOADERS[key]())
    return _LANGUAGES[key]


# ------------------------------------------------------------------- source


class SourceIndex:
    """The file's bytes, plus a line lookup over them.

    Line numbers are derived from byte offsets rather than read off
    `Node.start_point`. That is not a stylistic choice: on tree-sitter 0.26.0 /
    CPython 3.14, reading `Point.row` corrupts the heap and eventually
    segfaults the interpreter (indexing the point, `start_point[0]`, is
    unaffected - it is the attribute accessor that is broken). Deriving lines
    ourselves sidesteps the bug entirely and keeps one consistent definition of
    a line for both whole-node and partial spans.

    The mapping is verified against tree-sitter's own points in the test suite.
    """

    __slots__ = ("source", "_line_starts")

    def __init__(self, source: bytes) -> None:
        self.source = source
        self._line_starts = self._index_lines(source)

    @staticmethod
    def _index_lines(source: bytes) -> list[int]:
        """Byte offset at which each line begins."""
        starts = [0]
        offset = source.find(b"\n")
        while offset != -1:
            starts.append(offset + 1)
            offset = source.find(b"\n", offset + 1)
        return starts

    def line_at(self, byte_offset: int) -> int:
        """The 1-based line containing `byte_offset`."""
        return bisect.bisect_right(self._line_starts, byte_offset)

    def text(self, start_byte: int, end_byte: int) -> str:
        """An exact slice of the original source."""
        return self.source[start_byte:end_byte].decode("utf-8", errors="replace")

    def start_line(self, node: Node) -> int:
        return self.line_at(node.start_byte)

    def end_line(self, node: Node) -> int:
        return self.end_line_for(node.start_byte, node.end_byte)

    def end_line_for(self, start_byte: int, end_byte: int) -> int:
        """The last line the span actually puts text on.

        `end_byte` is exclusive, so a span finishing just after a newline ends
        on the line before it - not on the empty line that follows.
        """
        return self.line_at(max(start_byte, end_byte - 1))


# ------------------------------------------------------------------ node types

# Nodes we walk *through* without chunking them.
_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_FUNCTION_NODES = frozenset(
    {"function_declaration", "generator_function_declaration", "function_signature"}
)
_VARIABLE_NODES = frozenset({"lexical_declaration", "variable_declaration"})
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function"})

# Declaration node type -> the symbol_type we record for it.
_SIMPLE_DECLARATIONS = {
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "type_alias_declaration": "type_alias",
}

# Class members worth a chunk of their own.
_METHOD_NODES = frozenset({"method_definition", "abstract_method_signature"})


class TypeScriptTreeSitterParser(BaseParser):
    """Extracts classes, methods, functions, interfaces, enums and type aliases."""

    extensions: tuple[str, ...] = (".ts", ".tsx")

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def supports(self, extension: str | None) -> bool:
        return extension is not None and extension.lower() in self.extensions

    def parse(
        self,
        file: RepositoryFile,
        warnings: list[str] | None = None,
    ) -> list[CodeChunk]:
        """Turn one file into chunks.

        `warnings` is an optional list to append diagnostics to - syntax errors
        and the like. Passing it lets the caller report problems per file
        without this method having to raise and abort a whole repository run.
        """
        extension = (file.extension or "").lower()
        if extension not in _GRAMMAR_LOADERS:
            raise ValueError(f"No TypeScript grammar for extension {extension!r}")

        # Parse from bytes, and keep those exact bytes: every chunk's content is
        # sliced out of them, so what we store is character-for-character what
        # was in the file rather than anything rebuilt from the tree.
        index = SourceIndex(file.content.encode("utf-8"))
        tree = Parser(_language_for(extension)).parse(index.source)
        root = tree.root_node

        if root.has_error and warnings is not None:
            # Tree-sitter recovers from syntax errors and still returns a usable
            # tree, so we note the problem and keep whatever parsed cleanly.
            warnings.append(
                "Contains syntax errors; chunks were extracted on a best-effort basis."
            )

        chunks: list[CodeChunk] = []
        for child in root.named_children:
            self._visit(child, file, index, parent_symbol=None, chunks=chunks)

        if not chunks:
            chunks.append(self._whole_file_chunk(file))

        for position, chunk in enumerate(chunks):
            chunk.chunk_index = position

        return chunks

    # traversal

    def _visit(
        self,
        node: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        parent_symbol: str | None,
        chunks: list[CodeChunk],
    ) -> None:
        """Handle one top-level-ish statement."""
        span = node
        declaration = node

        if node.type == "export_statement":
            # `export` is transparent: chunk the thing being exported, but keep
            # the span on the export statement so the `export` keyword - and any
            # decorators sitting in front of it - stay in the chunk's content.
            inner = node.child_by_field_name("declaration")
            if inner is None:
                # `export { a, b }` / `export * from "x"` declare nothing new.
                return
            declaration = inner

        node_type = declaration.type

        if node_type in _CLASS_NODES:
            self._visit_class(
                declaration, span, file, index, parent_symbol=parent_symbol, chunks=chunks
            )
            return

        if node_type in _FUNCTION_NODES:
            chunks.append(
                self._chunk(
                    span,
                    file,
                    index,
                    symbol_type="function",
                    symbol_name=self._name_of(declaration, index),
                    parent_symbol=parent_symbol,
                )
            )
            return

        if node_type in _SIMPLE_DECLARATIONS:
            chunks.append(
                self._chunk(
                    span,
                    file,
                    index,
                    symbol_type=_SIMPLE_DECLARATIONS[node_type],
                    symbol_name=self._name_of(declaration, index),
                    parent_symbol=parent_symbol,
                )
            )
            return

        if node_type in _VARIABLE_NODES:
            self._visit_variable(
                declaration, span, file, index, parent_symbol=parent_symbol, chunks=chunks
            )
            return

        if node_type == "internal_module":
            # A namespace. Worth descending into, but not worth a chunk of its
            # own - that would duplicate everything declared inside it.
            self._visit_namespace(
                declaration, file, index, parent_symbol=parent_symbol, chunks=chunks
            )
            return

        # Anything else (imports, bare expressions, plain constants) is not a
        # symbol we chunk. If a file contains nothing but these, the whole-file
        # fallback in parse() catches it.

    def _visit_class(
        self,
        declaration: Node,
        span: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        parent_symbol: str | None,
        chunks: list[CodeChunk],
    ) -> None:
        """Emit the class chunk, then a chunk per method."""
        class_name = self._name_of(declaration, index)
        body = declaration.child_by_field_name("body")

        if self.config.emit_class_chunks:
            if self.config.emit_full_class_body or body is None:
                chunks.append(
                    self._chunk(
                        span,
                        file,
                        index,
                        symbol_type="class",
                        symbol_name=class_name,
                        parent_symbol=parent_symbol,
                    )
                )
            else:
                # Header only: from the start of the span through the opening
                # brace. Still an exact slice of the file, just a shorter one.
                chunks.append(
                    self._chunk_span(
                        span.start_byte,
                        body.start_byte + 1,
                        file,
                        index,
                        symbol_type="class",
                        symbol_name=class_name,
                        parent_symbol=parent_symbol,
                    )
                )

        if body is None:
            return

        for member in body.named_children:
            self._visit_class_member(
                member, file, index, parent_symbol=class_name, chunks=chunks
            )

    def _visit_class_member(
        self,
        member: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        parent_symbol: str | None,
        chunks: list[CodeChunk],
    ) -> None:
        """Emit a chunk for a method, including arrow-function class fields."""
        if member.type in _METHOD_NODES:
            chunks.append(
                self._chunk(
                    member,
                    file,
                    index,
                    symbol_type="method",
                    symbol_name=self._name_of(member, index),
                    parent_symbol=parent_symbol,
                )
            )
            return

        # `handleClick = () => { ... }` is a field holding a function. It is a
        # method in every way that matters here.
        if member.type == "public_field_definition":
            value = member.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES:
                chunks.append(
                    self._chunk(
                        member,
                        file,
                        index,
                        symbol_type="method",
                        symbol_name=self._name_of(member, index),
                        parent_symbol=parent_symbol,
                    )
                )

    def _visit_variable(
        self,
        declaration: Node,
        span: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        parent_symbol: str | None,
        chunks: list[CodeChunk],
    ) -> None:
        """Chunk `const x = () => {}` - but not `const x = 5`."""
        declarators = [
            child
            for child in declaration.named_children
            if child.type == "variable_declarator"
        ]

        for declarator in declarators:
            value = declarator.child_by_field_name("value")
            if value is None or value.type not in _FUNCTION_VALUES:
                continue

            # One declarator: span the whole statement so `export const` is
            # included. Several: span just this declarator, since the others are
            # unrelated declarations that happen to share a keyword.
            target = span if len(declarators) == 1 else declarator
            chunks.append(
                self._chunk(
                    target,
                    file,
                    index,
                    symbol_type="function",
                    symbol_name=self._name_of(declarator, index),
                    parent_symbol=parent_symbol,
                )
            )

    def _visit_namespace(
        self,
        declaration: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        parent_symbol: str | None,
        chunks: list[CodeChunk],
    ) -> None:
        """Descend into a namespace, qualifying names declared inside it."""
        name = self._name_of(declaration, index)
        qualified = f"{parent_symbol}.{name}" if parent_symbol and name else name

        body = declaration.child_by_field_name("body")
        if body is None:
            return

        for child in body.named_children:
            self._visit(child, file, index, parent_symbol=qualified, chunks=chunks)

    # ----------------------------------------------------------- chunk making

    @staticmethod
    def _name_of(node: Node, index: SourceIndex) -> str | None:
        """Read a declaration's `name` field as text."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        return index.text(name_node.start_byte, name_node.end_byte)

    def _chunk(
        self,
        node: Node,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        symbol_type: str,
        symbol_name: str | None,
        parent_symbol: str | None,
    ) -> CodeChunk:
        """Build a chunk covering exactly one syntax node."""
        return self._chunk_span(
            node.start_byte,
            node.end_byte,
            file,
            index,
            symbol_type=symbol_type,
            symbol_name=symbol_name,
            parent_symbol=parent_symbol,
        )

    def _chunk_span(
        self,
        start_byte: int,
        end_byte: int,
        file: RepositoryFile,
        index: SourceIndex,
        *,
        symbol_type: str,
        symbol_name: str | None,
        parent_symbol: str | None,
    ) -> CodeChunk:
        """Build a chunk covering any byte range of the source.

        Both whole nodes and partial spans (a class header, say) come through
        here, so content slicing and line numbering are defined in one place.
        """
        return self._build(
            file,
            content=index.text(start_byte, end_byte),
            start_line=index.line_at(start_byte),
            end_line=index.end_line_for(start_byte, end_byte),
            symbol_type=symbol_type,
            symbol_name=symbol_name,
            parent_symbol=parent_symbol,
        )

    def _whole_file_chunk(self, file: RepositoryFile) -> CodeChunk:
        """The fallback: one chunk for a file with no recognised symbols.

        Without this, a file of exported constants or re-exports would be
        discarded entirely just because it has no class or function in it.
        """
        content = file.content
        # Count lines from the source rather than the tree, so the span covers
        # the whole file. A trailing newline does not open a new line.
        line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
        return self._build(
            file,
            content=content,
            start_line=1,
            end_line=max(1, line_count),
            symbol_type="file",
            symbol_name=None,
            parent_symbol=None,
        )

    @staticmethod
    def _build(
        file: RepositoryFile,
        *,
        content: str,
        start_line: int,
        end_line: int,
        symbol_type: str,
        symbol_name: str | None,
        parent_symbol: str | None,
    ) -> CodeChunk:
        """Attach repository coordinates to an extracted span."""
        return CodeChunk(
            repository=file.repository,
            branch=file.branch,
            commit_sha=file.commit_sha,
            file_path=file.file_path,
            file_name=file.file_name,
            extension=file.extension,
            file_sha=file.file_sha,
            external_id=file.file_path, # names the resource row this chunk belongs to; parse() fills chunk_index once it knows the order
            language=file.language or "unknown",
            symbol_type=symbol_type,
            symbol_name=symbol_name,
            parent_symbol=parent_symbol,
            start_line=start_line,
            end_line=end_line,
            content=content,
        )
