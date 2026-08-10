"""Tests for the Tree-sitter TypeScript/TSX parser.

Sources are inline so each test states exactly what it expects to find. The
recurring assertions are that a chunk's `content` is a verbatim slice of the
file and that its line range points at that same text - if either drifts, chunks
stop being usable as source references.
"""

import pytest
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser

from app.ingestion.parser.typescript_parser import (
    ChunkingConfig,
    SourceIndex,
    TypeScriptTreeSitterParser,
)
from app.models.repository_file import RepositoryFile


def make_file(content: str, path: str = "src/example.ts") -> RepositoryFile:
    extension = ".tsx" if path.endswith(".tsx") else ".ts"
    return RepositoryFile(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        path=path,
        file_name=path.rsplit("/", 1)[-1],
        extension=extension,
        file_sha="xyz456",
        size=len(content.encode("utf-8")),
        language="tsx" if extension == ".tsx" else "typescript",
        content=content,
    )


@pytest.fixture
def parser() -> TypeScriptTreeSitterParser:
    return TypeScriptTreeSitterParser()


def chunk_named(chunks, name: str):
    matches = [c for c in chunks if c.symbol_name == name]
    assert matches, f"no chunk named {name!r}; got {[c.symbol_name for c in chunks]}"
    return matches[0]


def assert_span_is_verbatim(chunk, source: str) -> None:
    """The chunk's text must appear in the file, at the lines it claims."""
    assert chunk.content in source, "chunk content is not a slice of the source"
    lines = source.split("\n")
    at_reported_lines = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
    assert at_reported_lines.strip() == chunk.content.strip()


# ------------------------------------------------------------------ classes

CLASS_SOURCE = """export class AuthService {
    async login(email: string, password: string) {
        return true;
    }

    logout() {
        return;
    }
}
"""


def test_class_and_methods_are_extracted(parser: TypeScriptTreeSitterParser) -> None:
    chunks = parser.parse(make_file(CLASS_SOURCE))
    by_name = {c.symbol_name: c for c in chunks}

    assert set(by_name) == {"AuthService", "login", "logout"}
    assert by_name["AuthService"].symbol_type == "class"
    assert by_name["login"].symbol_type == "method"
    assert by_name["logout"].symbol_type == "method"


def test_methods_record_their_parent_class(parser: TypeScriptTreeSitterParser) -> None:
    chunks = parser.parse(make_file(CLASS_SOURCE))
    assert chunk_named(chunks, "login").parent_symbol == "AuthService"
    assert chunk_named(chunks, "logout").parent_symbol == "AuthService"
    assert chunk_named(chunks, "AuthService").parent_symbol is None


def test_method_content_is_the_exact_source(
    parser: TypeScriptTreeSitterParser,
) -> None:
    login = chunk_named(parser.parse(make_file(CLASS_SOURCE)), "login")
    assert login.content == (
        "async login(email: string, password: string) {\n"
        "        return true;\n"
        "    }"
    )
    assert_span_is_verbatim(login, CLASS_SOURCE)


def test_line_ranges_are_one_based_and_inclusive(
    parser: TypeScriptTreeSitterParser,
) -> None:
    chunks = parser.parse(make_file(CLASS_SOURCE))
    assert (chunk_named(chunks, "AuthService").start_line) == 1
    assert (chunk_named(chunks, "AuthService").end_line) == 9
    assert (chunk_named(chunks, "login").start_line) == 2
    assert (chunk_named(chunks, "login").end_line) == 4
    assert (chunk_named(chunks, "logout").start_line) == 6
    assert (chunk_named(chunks, "logout").end_line) == 8


def test_class_chunk_keeps_the_export_keyword(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """The span starts at `export`, not at `class`, so modifiers survive."""
    cls = chunk_named(parser.parse(make_file(CLASS_SOURCE)), "AuthService")
    assert cls.content.startswith("export class AuthService {")


def test_chunks_carry_repository_coordinates(
    parser: TypeScriptTreeSitterParser,
) -> None:
    chunk = parser.parse(make_file(CLASS_SOURCE))[0]
    assert chunk.repository == "my-org/backend"
    assert chunk.branch == "main"
    assert chunk.commit_sha == "abc123"
    assert chunk.file_path == "src/example.ts"
    assert chunk.file_sha == "xyz456"
    assert chunk.language == "typescript"


def test_constructor_and_accessors_are_methods(
    parser: TypeScriptTreeSitterParser,
) -> None:
    source = """class Repo {
    constructor(private db: Db) {}
    get size() { return 1; }
    static create() { return new Repo(); }
}
"""
    chunks = parser.parse(make_file(source))
    names = {c.symbol_name: c.symbol_type for c in chunks}
    assert names["constructor"] == "method"
    assert names["size"] == "method"
    assert names["create"] == "method"


def test_arrow_function_class_field_is_a_method(
    parser: TypeScriptTreeSitterParser,
) -> None:
    source = """class Widget {
    handleClick = async (event: Event) => {
        return event;
    };
    label = "not a method";
}
"""
    chunks = parser.parse(make_file(source))
    handler = chunk_named(chunks, "handleClick")
    assert handler.symbol_type == "method"
    assert handler.parent_symbol == "Widget"
    # A plain data field is not a chunk.
    assert "label" not in {c.symbol_name for c in chunks}


def test_abstract_class_and_signatures(parser: TypeScriptTreeSitterParser) -> None:
    source = """export abstract class Base {
    abstract run(): void;
    protected helper() { return 1; }
}
"""
    chunks = parser.parse(make_file(source))
    by_name = {c.symbol_name: c for c in chunks}
    assert by_name["Base"].symbol_type == "class"
    assert by_name["run"].symbol_type == "method"
    assert by_name["run"].parent_symbol == "Base"
    assert by_name["helper"].parent_symbol == "Base"


# ---------------------------------------------------------------- functions

def test_standalone_function(parser: TypeScriptTreeSitterParser) -> None:
    source = """export function generateToken(userId: string) {
    return userId;
}
"""
    chunk = chunk_named(parser.parse(make_file(source)), "generateToken")
    assert chunk.symbol_type == "function"
    assert chunk.parent_symbol is None
    assert chunk.start_line == 1
    assert chunk.end_line == 3
    assert chunk.content.startswith("export function generateToken")
    assert_span_is_verbatim(chunk, source)


def test_arrow_function_assigned_to_const(
    parser: TypeScriptTreeSitterParser,
) -> None:
    source = """export const validateToken = async (token: string) => {
    return true;
};
"""
    chunk = chunk_named(parser.parse(make_file(source)), "validateToken")
    assert chunk.symbol_type == "function"
    assert chunk.parent_symbol is None
    assert "async (token: string) =>" in chunk.content


def test_plain_constants_are_not_function_chunks(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """`const x = 5` is data, not a symbol worth its own chunk."""
    chunks = parser.parse(make_file('export const API_URL = "https://x";\n'))
    assert [c.symbol_type for c in chunks] == ["file"]


def test_generator_function(parser: TypeScriptTreeSitterParser) -> None:
    chunks = parser.parse(make_file("function* ids() { yield 1; }\n"))
    assert chunk_named(chunks, "ids").symbol_type == "function"


def test_nested_functions_do_not_become_separate_chunks(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """A helper inside a function stays part of that function's chunk."""
    source = """export function outer() {
    function inner() { return 1; }
    return inner();
}
"""
    chunks = parser.parse(make_file(source))
    assert [c.symbol_name for c in chunks] == ["outer"]
    assert "function inner()" in chunks[0].content


# --------------------------------------------------------------- type shapes

def test_interface(parser: TypeScriptTreeSitterParser) -> None:
    source = """export interface User {
    id: string;
    email: string;
}
"""
    chunk = chunk_named(parser.parse(make_file(source)), "User")
    assert chunk.symbol_type == "interface"
    assert chunk.start_line == 1
    assert chunk.end_line == 4
    assert "email: string;" in chunk.content


def test_enum(parser: TypeScriptTreeSitterParser) -> None:
    source = "export enum Role { Admin = 'admin', User = 'user' }\n"
    chunk = chunk_named(parser.parse(make_file(source)), "Role")
    assert chunk.symbol_type == "enum"


def test_type_alias(parser: TypeScriptTreeSitterParser) -> None:
    source = "export type UserRole = 'admin' | 'user';\n"
    chunk = chunk_named(parser.parse(make_file(source)), "UserRole")
    assert chunk.symbol_type == "type_alias"


def test_namespace_members_are_qualified(
    parser: TypeScriptTreeSitterParser,
) -> None:
    source = """export namespace Utils {
    export function slugify(value: string) { return value; }
}
"""
    chunk = chunk_named(parser.parse(make_file(source)), "slugify")
    assert chunk.parent_symbol == "Utils"


# ---------------------------------------------------------------------- TSX

TSX_SOURCE = """import React from 'react';

export const Button = ({ label }: { label: string }) => {
    return <button className="btn">{label}</button>;
};

export default function App() {
    return <div><Button label="hi" /></div>;
}
"""


def test_tsx_grammar_parses_jsx(parser: TypeScriptTreeSitterParser) -> None:
    """Proves the TSX grammar is loaded - the TS grammar would error on JSX."""
    warnings: list[str] = []
    chunks = parser.parse(make_file(TSX_SOURCE, "src/Button.tsx"), warnings)

    assert warnings == [], "TSX source should parse without syntax errors"
    assert {c.symbol_name for c in chunks} == {"Button", "App"}
    assert "<button className=" in chunk_named(chunks, "Button").content


def test_tsx_chunks_record_the_tsx_language(
    parser: TypeScriptTreeSitterParser,
) -> None:
    chunks = parser.parse(make_file(TSX_SOURCE, "src/Button.tsx"))
    assert all(c.language == "tsx" for c in chunks)


def test_angle_bracket_generics_still_parse_in_ts(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """The .ts grammar must handle generics that would look like JSX."""
    warnings: list[str] = []
    source = "export function first<T>(items: T[]): T { return items[0]; }\n"
    chunks = parser.parse(make_file(source), warnings)
    assert warnings == []
    assert chunk_named(chunks, "first").symbol_type == "function"


# ----------------------------------------------------------------- fallbacks

def test_file_without_symbols_falls_back_to_a_whole_file_chunk(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """A file of constants must not be silently discarded."""
    source = 'export const API_URL = "https://x";\nexport const RETRIES = 3;\n'
    chunks = parser.parse(make_file(source))

    assert len(chunks) == 1
    assert chunks[0].symbol_type == "file"
    assert chunks[0].symbol_name is None
    assert chunks[0].parent_symbol is None
    assert chunks[0].content == source
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2


def test_reexport_only_file_falls_back(parser: TypeScriptTreeSitterParser) -> None:
    chunks = parser.parse(make_file('export { Button } from "./Button";\n'))
    assert [c.symbol_type for c in chunks] == ["file"]


def test_empty_file_produces_one_chunk(parser: TypeScriptTreeSitterParser) -> None:
    chunks = parser.parse(make_file(""))
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1


def test_syntax_errors_are_reported_but_do_not_raise(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """One broken file must not be able to abort a repository run."""
    warnings: list[str] = []
    chunks = parser.parse(make_file("export class Broken { m( {\n"), warnings)

    assert len(warnings) == 1
    assert "syntax errors" in warnings[0]
    assert chunks, "a broken file should still yield something"


def test_partially_broken_file_still_yields_the_good_symbols(
    parser: TypeScriptTreeSitterParser,
) -> None:
    source = """export function good() {
    return 1;
}

export function bad( {
"""
    warnings: list[str] = []
    chunks = parser.parse(make_file(source), warnings)
    assert warnings, "the syntax error should be reported"
    assert "good" in {c.symbol_name for c in chunks}


def test_unsupported_extension_is_rejected(
    parser: TypeScriptTreeSitterParser,
) -> None:
    file = make_file("print('hi')\n")
    file = file.model_copy(update={"extension": ".py"})
    with pytest.raises(ValueError):
        parser.parse(file)


def test_supports_reports_the_handled_extensions(
    parser: TypeScriptTreeSitterParser,
) -> None:
    assert parser.supports(".ts") is True
    assert parser.supports(".tsx") is True
    assert parser.supports(".TS") is True
    assert parser.supports(".py") is False
    assert parser.supports(None) is False


# ----------------------------------------------------------- chunking config

def test_header_only_class_mode_removes_body_duplication() -> None:
    """The alternative strategy: class context without repeating the methods."""
    parser = TypeScriptTreeSitterParser(ChunkingConfig(emit_full_class_body=False))
    chunks = parser.parse(make_file(CLASS_SOURCE))

    cls = chunk_named(chunks, "AuthService")
    assert cls.content == "export class AuthService {"
    assert "return true;" not in cls.content
    # Methods are still extracted in full.
    assert "return true;" in chunk_named(chunks, "login").content


def test_class_chunks_can_be_disabled() -> None:
    parser = TypeScriptTreeSitterParser(ChunkingConfig(emit_class_chunks=False))
    chunks = parser.parse(make_file(CLASS_SOURCE))
    assert {c.symbol_name for c in chunks} == {"login", "logout"}


def test_full_class_body_is_the_default() -> None:
    """Documented tradeoff: the class chunk repeats its method bodies."""
    parser = TypeScriptTreeSitterParser()
    cls = chunk_named(parser.parse(make_file(CLASS_SOURCE)), "AuthService")
    assert "return true;" in cls.content


# ------------------------------------------------------------- line indexing

# Line numbers are derived from byte offsets rather than `Node.start_point`,
# because reading `Point.row` corrupts the heap on tree-sitter 0.26.0 /
# CPython 3.14 and eventually segfaults the interpreter. These tests pin both
# halves of that decision: that our mapping is correct, and that parsing stays
# stable when repeated (the shape of run in which the crash surfaced).

LARGE_CLASS = (
    "// leading comment\n\nexport class Big {\n"
    + "".join(
        f"    method{i}(value: number) {{\n        return value + {i};\n    }}\n\n"
        for i in range(60)
    )
    + "}\n"
)


@pytest.mark.parametrize(
    ("source", "offset", "expected_line"),
    [
        ("a\nb\nc", 0, 1),
        ("a\nb\nc", 1, 1),  # the newline belongs to the line it ends
        ("a\nb\nc", 2, 2),
        ("a\nb\nc", 4, 3),
        ("", 0, 1),
    ],
)
def test_source_index_maps_offsets_to_lines(
    source: str, offset: int, expected_line: int
) -> None:
    assert SourceIndex(source.encode("utf-8")).line_at(offset) == expected_line


def test_source_index_end_line_excludes_a_trailing_newline() -> None:
    """A span ending just after a newline ends on the line that held the text."""
    index = SourceIndex(b"first\nsecond\n")
    assert index.end_line_for(0, 13) == 2
    assert index.end_line_for(0, 6) == 1


def test_source_index_agrees_with_tree_sitter_points() -> None:
    """Our line mapping must match the grammar's own, node for node.

    `start_point[0]` is used rather than `.row` - indexing the point is the
    accessor that does not corrupt memory.
    """
    source = LARGE_CLASS.encode("utf-8")
    index = SourceIndex(source)
    root = Parser(Language(tstypescript.language_typescript())).parse(source).root_node

    checked = 0
    stack = [root]
    while stack:
        node = stack.pop()
        assert index.start_line(node) == node.start_point[0] + 1

        # tree-sitter puts a span that ends on a newline at the start of the
        # next (empty) line; we report the last line holding text.
        ends_on_newline = source[node.end_byte - 1 : node.end_byte] == b"\n"
        expected_end = node.end_point[0] + 1 - (1 if ends_on_newline else 0)
        assert index.end_line(node) == expected_end

        checked += 1
        stack.extend(node.named_children)

    assert checked > 100, "the sample should exercise a decent number of nodes"


def test_repeated_parsing_stays_stable(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """Regression: repeated parsing used to corrupt the heap and segfault.

    A failure here is likely to kill the whole test process rather than fail an
    assertion - which is exactly the signal we want.
    """
    file = make_file(LARGE_CLASS, "src/Big.ts")
    first = parser.parse(file)

    for _ in range(12):
        again = parser.parse(file)
        assert len(again) == len(first)
        assert [(c.symbol_name, c.start_line, c.end_line) for c in again] == [
            (c.symbol_name, c.start_line, c.end_line) for c in first
        ]


def test_line_numbers_are_correct_in_a_large_file(
    parser: TypeScriptTreeSitterParser,
) -> None:
    """Spot-check against lines counted directly from the text."""
    chunks = parser.parse(make_file(LARGE_CLASS, "src/Big.ts"))
    lines = LARGE_CLASS.split("\n")

    for chunk in chunks:
        first_line = lines[chunk.start_line - 1]
        assert chunk.content.split("\n")[0] == first_line.strip() or (
            first_line.strip() in chunk.content.split("\n")[0]
        )
        assert "\n".join(
            lines[chunk.start_line - 1 : chunk.end_line]
        ).strip() == chunk.content.strip()

    big = chunk_named(chunks, "Big")
    assert big.start_line == 3
    assert big.end_line == len(lines) - 1  # the file's final "}"
