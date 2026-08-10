"""End-to-end tests for the orchestration, with GitHub replaced by a fake.

Everything downstream of the connector is real here - the real FileFilter, the
real parser registry, the real Tree-sitter grammars. Only the network is faked,
so these tests exercise the actual pipeline wiring rather than a mock of it.
"""

import pytest
from pydantic import SecretStr

from app.connectors.base import BaseSourceConnector, SourceSnapshot
from app.core.exceptions import AuthenticationError
from app.ingestion.ingestion_service import GitHubIngestionService
from app.models.repository_file import RepositoryFile

TOKEN = SecretStr("ghp_fake_token_for_tests_only")

AUTH_SERVICE_TS = """export class AuthService {
    async login(email: string, password: string) {
        return true;
    }

    logout() {
        return;
    }
}
"""

BUTTON_TSX = """export const Button = ({ label }: { label: string }) => {
    return <button>{label}</button>;
};
"""

CONSTANTS_TS = 'export const API_URL = "https://example.com";\n'


def make_file(path: str, content: str) -> RepositoryFile:
    extension = ".tsx" if path.endswith(".tsx") else ".ts"
    return RepositoryFile(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        path=path,
        file_name=path.rsplit("/", 1)[-1],
        extension=extension,
        file_sha=f"sha-{path}",
        size=len(content.encode("utf-8")),
        language="tsx" if extension == ".tsx" else "typescript",
        content=content,
    )


class FakeConnector(BaseSourceConnector):
    """Returns a canned snapshot and records how it was used."""

    def __init__(
        self,
        files: list[RepositoryFile],
        *,
        discovered: int | None = None,
        errors: list[tuple[str, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.files = files
        self.discovered = discovered if discovered is not None else len(files)
        self.errors = errors or []
        self.error = error
        self.closed = False
        self.get_files_kwargs: dict = {}

    def get_files(self, *, branch=None, path_filter=None, max_files=None):
        if self.error is not None:
            raise self.error
        self.get_files_kwargs = {
            "branch": branch,
            "path_filter": path_filter,
            "max_files": max_files,
        }
        return SourceSnapshot(
            repository="my-org/backend",
            branch=branch or "main",
            commit_sha="abc123",
            discovered_paths=self.discovered,
            files=list(self.files),
            errors=list(self.errors),
        )

    def close(self) -> None:
        self.closed = True


def build_service(connector: FakeConnector, **kwargs) -> GitHubIngestionService:
    return GitHubIngestionService(
        connector_factory=lambda token, repository: connector, **kwargs
    )


# --------------------------------------------------------------- happy path

def test_full_pipeline_produces_chunks() -> None:
    connector = FakeConnector(
        [
            make_file("src/auth/AuthService.ts", AUTH_SERVICE_TS),
            make_file("src/components/Button.tsx", BUTTON_TSX),
        ],
        discovered=240,
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert result.discovered_files == 240
    assert result.accepted_files == 2
    assert result.parsed_files == 2
    assert result.generated_chunks == 4  # class + 2 methods + 1 component

    symbols = {(c.symbol_type, c.symbol_name, c.parent_symbol) for c in result.chunks}
    assert ("class", "AuthService", None) in symbols
    assert ("method", "login", "AuthService") in symbols
    assert ("method", "logout", "AuthService") in symbols
    assert ("function", "Button", None) in symbols


def test_chunks_keep_repository_coordinates() -> None:
    connector = FakeConnector([make_file("src/auth/AuthService.ts", AUTH_SERVICE_TS)])
    result = build_service(connector).ingest(TOKEN, "my-org/backend", "main")

    for chunk in result.chunks:
        assert chunk.repository == "my-org/backend"
        assert chunk.branch == "main"
        assert chunk.commit_sha == "abc123"
        assert chunk.file_path == "src/auth/AuthService.ts"
        assert chunk.language == "typescript"


def test_result_keeps_every_file_and_chunk() -> None:
    """The internal result is complete; only the HTTP layer samples it."""
    files = [make_file(f"src/f{i}.ts", AUTH_SERVICE_TS) for i in range(30)]
    result = build_service(FakeConnector(files)).ingest(TOKEN, "my-org/backend")

    assert len(result.files) == 30
    assert len(result.chunks) == 90


def test_tsx_and_ts_are_both_parsed() -> None:
    connector = FakeConnector(
        [
            make_file("src/a.ts", "export function a() { return 1; }\n"),
            make_file("src/B.tsx", BUTTON_TSX),
        ]
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    languages = {c.language for c in result.chunks}
    assert languages == {"typescript", "tsx"}


def test_file_with_no_symbols_still_yields_a_chunk() -> None:
    connector = FakeConnector([make_file("src/config.ts", CONSTANTS_TS)])
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert result.parsed_files == 1
    assert [c.symbol_type for c in result.chunks] == ["file"]


# ------------------------------------------------------------------ wiring

def test_the_filter_is_handed_to_the_connector() -> None:
    """The connector must be able to filter before it downloads anything."""
    connector = FakeConnector([])
    service = build_service(connector)
    service.ingest(TOKEN, "my-org/backend")

    path_filter = connector.get_files_kwargs["path_filter"]
    # A bound method is a fresh object on every attribute access, so compare the
    # instance it is bound to rather than the method object itself.
    assert path_filter.__self__ is service.file_filter
    assert path_filter("src/app.ts", 100) is True
    assert path_filter("node_modules/dep/index.ts", 100) is False


def test_the_file_cap_is_passed_through() -> None:
    connector = FakeConnector([])
    build_service(connector, max_files=7).ingest(TOKEN, "my-org/backend")

    assert connector.get_files_kwargs["max_files"] == 7


def test_the_file_cap_can_be_overridden_per_run() -> None:
    connector = FakeConnector([])
    build_service(connector, max_files=7).ingest(
        TOKEN, "my-org/backend", max_files=250
    )

    assert connector.get_files_kwargs["max_files"] == 250


def test_the_branch_is_passed_through() -> None:
    connector = FakeConnector([])
    build_service(connector).ingest(TOKEN, "my-org/backend", "release/2.0")

    assert connector.get_files_kwargs["branch"] == "release/2.0"


def test_the_connector_is_closed_after_fetching() -> None:
    """Parsing happens after the authenticated session is released."""
    connector = FakeConnector([make_file("src/a.ts", AUTH_SERVICE_TS)])
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert connector.closed is True
    assert result.chunks, "parsing still completed after the session closed"


def test_connector_errors_propagate() -> None:
    connector = FakeConnector([], error=AuthenticationError())
    with pytest.raises(AuthenticationError):
        build_service(connector).ingest(TOKEN, "my-org/backend")


# -------------------------------------------------------------- resilience

def test_connector_errors_are_carried_into_the_result() -> None:
    connector = FakeConnector(
        [make_file("src/a.ts", AUTH_SERVICE_TS)],
        errors=[("src/logo.ts", "Skipped: file appears to be binary.")],
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert ("src/logo.ts", "Skipped: file appears to be binary.") in result.errors
    assert result.chunks, "the good file was still processed"


def test_a_file_with_no_parser_is_reported_not_fatal() -> None:
    connector = FakeConnector(
        [
            make_file("src/a.ts", AUTH_SERVICE_TS),
            make_file("src/notes.md", "# hi\n").model_copy(
                update={"extension": ".md", "language": None}
            ),
        ]
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert result.parsed_files == 1
    assert any("No parser" in reason for _, reason in result.errors)


def test_a_broken_file_does_not_stop_the_run() -> None:
    connector = FakeConnector(
        [
            make_file("src/broken.ts", "export class Broken { m( {\n"),
            make_file("src/good.ts", AUTH_SERVICE_TS),
        ]
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert any(path == "src/broken.ts" for path, _ in result.errors)
    assert any(c.symbol_name == "login" for c in result.chunks)


def test_a_parser_crash_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected parser bug must cost one file, not the whole repository."""
    from app.ingestion.parser.typescript_parser import TypeScriptTreeSitterParser

    original = TypeScriptTreeSitterParser.parse

    def exploding(self, file, warnings=None):
        if file.path == "src/bomb.ts":
            raise RuntimeError("unexpected parser failure")
        return original(self, file, warnings)

    monkeypatch.setattr(TypeScriptTreeSitterParser, "parse", exploding)

    connector = FakeConnector(
        [
            make_file("src/bomb.ts", AUTH_SERVICE_TS),
            make_file("src/good.ts", AUTH_SERVICE_TS),
        ]
    )
    result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert result.parsed_files == 1
    assert ("src/bomb.ts", "The file could not be parsed.") in result.errors


# ----------------------------------------------------------------- logging

def test_the_run_header_names_the_repository_and_branch(caplog) -> None:
    connector = FakeConnector([make_file("src/a.ts", AUTH_SERVICE_TS)])

    with caplog.at_level("INFO", logger="app.ingestion.ingestion_service"):
        build_service(connector).ingest(TOKEN, "my-org/backend", "release/2.0")

    assert "Ingesting my-org/backend (branch: release/2.0)" in caplog.text


def test_the_run_header_says_when_the_default_branch_is_used(caplog) -> None:
    connector = FakeConnector([make_file("src/a.ts", AUTH_SERVICE_TS)])

    with caplog.at_level("INFO", logger="app.ingestion.ingestion_service"):
        build_service(connector).ingest(TOKEN, "my-org/backend")

    assert "branch: repository default" in caplog.text


def test_the_summary_reports_chunks_files_and_duration(caplog) -> None:
    connector = FakeConnector([make_file("src/a.ts", AUTH_SERVICE_TS)])

    with caplog.at_level("INFO", logger="app.ingestion.ingestion_service"):
        result = build_service(connector).ingest(TOKEN, "my-org/backend")

    assert (
        f"Generated {result.generated_chunks} code chunks from "
        f"{result.parsed_files} files in " in caplog.text
    )


def test_parsing_is_not_logged_at_info(caplog) -> None:
    """Per-file parse lines stay at DEBUG - chunking is not progress logging."""
    connector = FakeConnector(
        [make_file(f"src/f{i}.ts", AUTH_SERVICE_TS) for i in range(5)]
    )

    with caplog.at_level("INFO", logger="app.ingestion.ingestion_service"):
        build_service(connector).ingest(TOKEN, "my-org/backend")

    assert "Parsing" not in caplog.text


def test_the_token_is_not_logged_by_the_service(caplog) -> None:
    connector = FakeConnector([make_file("src/a.ts", AUTH_SERVICE_TS)])

    with caplog.at_level("DEBUG"):
        build_service(connector).ingest(TOKEN, "my-org/backend")

    assert TOKEN.get_secret_value() not in caplog.text


def test_no_files_is_not_an_error() -> None:
    result = build_service(FakeConnector([], discovered=12)).ingest(
        TOKEN, "my-org/backend"
    )

    assert result.discovered_files == 12
    assert result.accepted_files == 0
    assert result.generated_chunks == 0
    assert result.errors == []
