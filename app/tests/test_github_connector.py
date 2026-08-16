"""Tests for the GitHub connector, with PyGithub replaced by fakes.

No test here needs a real token or a network call. The fakes record which API
calls were made, which is what lets us assert the property that matters most:
that ignored files are never downloaded in the first place.
"""

import base64
from unittest.mock import patch

import pytest
from github.GithubException import (
    BadCredentialsException,
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)
from github.GithubRetry import GithubRetry
from pydantic import SecretStr

from app.connectors.github_connector import GitHubConnector
from app.core.exceptions import (
    AuthenticationError,
    BranchNotFoundError,
    RateLimitError,
    RepositoryNotFoundError,
    SourceUnavailableError,
)
from app.ingestion.file_filter import FileFilter

# A value shaped like a token, so tests can assert it never surfaces anywhere.
FAKE_TOKEN = SecretStr("ghp_fake_token_for_tests_only")


# ------------------------------------------------------------------- fakes

class FakeBlob:
    def __init__(self, raw: bytes, encoding: str = "base64") -> None:
        self.encoding = encoding
        self.content = (
            base64.b64encode(raw).decode("ascii")
            if encoding == "base64"
            else raw.decode("utf-8")
        )


class FakeTreeElement:
    def __init__(self, path: str, size: int, element_type: str = "blob") -> None:
        self.path = path
        self.sha = f"sha-{path}"
        self.size = size
        self.type = element_type


class FakeTree:
    def __init__(self, elements: list[FakeTreeElement], truncated: bool) -> None:
        self.tree = elements
        self.raw_data = {"truncated": truncated}


class FakeCommit:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class FakeBranch:
    def __init__(self, commit_sha: str) -> None:
        self.commit = FakeCommit(commit_sha)


class FakeGitCommit:
    def __init__(self, tree_sha: str) -> None:
        self.tree = FakeCommit(tree_sha)


class FakeRepo:
    """A repository whose contents are a {path: bytes} mapping."""

    def __init__(
        self,
        files: dict[str, bytes],
        *,
        default_branch: str = "main",
        directories: tuple[str, ...] = (),
        truncated: bool = False,
        branch_error: Exception | None = None,
        blob_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.files = files
        self.default_branch = default_branch
        self._directories = directories
        self._truncated = truncated
        self._branch_error = branch_error
        self._blob_errors = blob_errors or {}

        # Call recorders - the point of the fake.
        self.requested_branches: list[str] = []
        self.requested_blobs: list[str] = []
        self.requested_trees: list[str] = []

    def get_branch(self, name: str) -> FakeBranch:
        self.requested_branches.append(name)
        if self._branch_error is not None:
            raise self._branch_error
        return FakeBranch("commit-sha-123")

    def get_git_commit(self, sha: str) -> FakeGitCommit:
        assert sha == "commit-sha-123"
        return FakeGitCommit("tree-sha-456")

    def get_git_tree(self, sha: str, recursive: bool = False) -> FakeTree:
        self.requested_trees.append(sha)
        assert recursive is True, "the tree must be fetched recursively"
        elements = [
            FakeTreeElement(path, len(raw)) for path, raw in self.files.items()
        ]
        elements += [
            FakeTreeElement(path, 0, element_type="tree") for path in self._directories
        ]
        return FakeTree(elements, self._truncated)

    def get_git_blob(self, sha: str) -> FakeBlob:
        self.requested_blobs.append(sha)
        path = sha.removeprefix("sha-")
        if path in self._blob_errors:
            raise self._blob_errors[path]
        return FakeBlob(self.files[path])


class FakeGithub:
    def __init__(self, repo: FakeRepo | None = None, error: Exception | None = None):
        self._repo = repo
        self._error = error
        self.closed = False

    def get_repo(self, full_name: str):
        if self._error is not None:
            raise self._error
        return self._repo

    def close(self) -> None:
        self.closed = True


def build_connector(
    repo: FakeRepo | None = None, error: Exception | None = None
) -> tuple[GitHubConnector, FakeGithub]:
    """Construct a connector whose PyGithub client is a fake."""
    fake = FakeGithub(repo, error)
    with patch(
        "app.connectors.github_connector.Github", return_value=fake
    ) as github_cls:
        connector = GitHubConnector(FAKE_TOKEN, "my-org/backend")

    # The token must reach PyGithub, and go nowhere else.
    assert github_cls.call_count == 1
    return connector, fake


# --------------------------------------------------------------- happy path

SOURCES = {
    "src/auth/AuthService.ts": b"export class AuthService { login() { return 1; } }\n",
    "src/components/Login.tsx": b"export const Login = () => <div />;\n",
    "README.md": b"# docs\n",
    "node_modules/dep/index.ts": b"export const dep = 1;\n",
    "dist/bundle.ts": b"export const bundled = 1;\n",
    "src/types/generated.d.ts": b"export declare const x: number;\n",
    "src/auth/AuthService.test.ts": b"describe('x', () => {});\n",
    "package-lock.json": b"{}\n",
}


def test_returns_only_files_that_pass_the_filter() -> None:
    repo = FakeRepo(SOURCES)
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert [file.file_path for file in snapshot.files] == [
        "src/auth/AuthService.ts",
        "src/components/Login.tsx",
    ]


def test_ignored_files_are_never_downloaded() -> None:
    """The whole point of filtering on the tree: no wasted API calls."""
    repo = FakeRepo(SOURCES)
    connector, _ = build_connector(repo)

    connector.get_files(path_filter=FileFilter().should_include)

    assert repo.requested_blobs == [
        "sha-src/auth/AuthService.ts",
        "sha-src/components/Login.tsx",
    ]
    for rejected in ("README.md", "node_modules/dep/index.ts", "dist/bundle.ts"):
        assert f"sha-{rejected}" not in repo.requested_blobs


def test_discovered_count_covers_everything_before_filtering() -> None:
    repo = FakeRepo(SOURCES)
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert snapshot.discovered_paths == len(SOURCES)
    assert len(snapshot.files) == 2


def test_directories_are_not_counted_as_files() -> None:
    repo = FakeRepo(
        {"src/app.ts": b"export const a = 1;\n"}, directories=("src", "src/deep")
    )
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert snapshot.discovered_paths == 1


def test_repository_file_is_fully_populated() -> None:
    repo = FakeRepo({"src/auth/AuthService.ts": SOURCES["src/auth/AuthService.ts"]})
    connector, _ = build_connector(repo)

    file = connector.get_files(path_filter=FileFilter().should_include).files[0]

    assert file.repository == "my-org/backend"
    assert file.branch == "main"
    assert file.commit_sha == "commit-sha-123"
    assert file.file_path == "src/auth/AuthService.ts"
    assert file.file_name == "AuthService.ts"
    assert file.extension == ".ts"
    assert file.file_sha == "sha-src/auth/AuthService.ts"
    assert file.language == "typescript"
    assert file.content.startswith("export class AuthService")


def test_tsx_files_are_tagged_as_tsx() -> None:
    repo = FakeRepo({"src/Login.tsx": SOURCES["src/components/Login.tsx"]})
    connector, _ = build_connector(repo)

    file = connector.get_files(path_filter=FileFilter().should_include).files[0]
    assert file.language == "tsx"


# ------------------------------------------------------------------ branches

def test_uses_the_default_branch_when_none_is_given() -> None:
    repo = FakeRepo({"a.ts": b"export const a = 1;\n"}, default_branch="develop")
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert repo.requested_branches == ["develop"]
    assert snapshot.branch == "develop"


def test_uses_the_requested_branch_when_given() -> None:
    repo = FakeRepo({"a.ts": b"export const a = 1;\n"}, default_branch="main")
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(
        branch="feature/x", path_filter=FileFilter().should_include
    )

    assert repo.requested_branches == ["feature/x"]
    assert snapshot.branch == "feature/x"


def test_the_tree_is_read_from_the_commits_tree_sha() -> None:
    """The tree endpoint takes a tree SHA, not a commit SHA."""
    repo = FakeRepo({"a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert repo.requested_trees == ["tree-sha-456"]
    # ...while the chunks are still stamped with the commit.
    assert snapshot.commit_sha == "commit-sha-123"


@pytest.mark.parametrize(
    "error",
    [
        UnknownObjectException(404, {"message": "Branch not found"}, {}),
        GithubException(422, {"message": "Invalid ref"}, {}),
    ],
)
def test_missing_branch_reports_a_branch_error(error: Exception) -> None:
    repo = FakeRepo({"a.ts": b"x"}, branch_error=error)
    connector, _ = build_connector(repo)

    with pytest.raises(BranchNotFoundError) as caught:
        connector.get_files(branch="nope", path_filter=FileFilter().should_include)

    assert "nope" in caught.value.message


# ------------------------------------------------------- per-file resilience

def test_binary_files_are_skipped_not_fatal() -> None:
    repo = FakeRepo(
        {
            "src/good.ts": b"export const good = 1;\n",
            "src/binary.ts": b"\x00\x01\x02binary",
        }
    )
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert [f.file_path for f in snapshot.files] == ["src/good.ts"]
    assert ("src/binary.ts", "Skipped: file appears to be binary.") in snapshot.errors


def test_invalid_utf8_is_skipped_not_fatal() -> None:
    repo = FakeRepo(
        {
            "src/good.ts": b"export const good = 1;\n",
            "src/latin1.ts": b"export const caf\xe9 = 1;\n",
        }
    )
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert [f.file_path for f in snapshot.files] == ["src/good.ts"]
    assert any("UTF-8" in reason for _, reason in snapshot.errors)


def test_a_failing_download_does_not_stop_the_run() -> None:
    repo = FakeRepo(
        {"src/a.ts": b"export const a = 1;\n", "src/b.ts": b"export const b = 2;\n"},
        blob_errors={"src/a.ts": GithubException(500, {"message": "boom"}, {})},
    )
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert [f.file_path for f in snapshot.files] == ["src/b.ts"]
    assert snapshot.errors[0][0] == "src/a.ts"


def test_byte_order_mark_is_stripped() -> None:
    """A UTF-8 BOM would otherwise ride along inside the first chunk."""
    raw = bytes([0xEF, 0xBB, 0xBF]) + b"export const a = 1;\n"
    repo = FakeRepo({"src/a.ts": raw})
    connector, _ = build_connector(repo)

    file = connector.get_files(path_filter=FileFilter().should_include).files[0]

    assert "﻿" not in file.content
    assert file.content == "export const a = 1;\n"


def test_utf8_encoded_blobs_are_handled() -> None:
    """GitHub occasionally returns plain utf-8 instead of base64."""
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)
    repo.get_git_blob = lambda sha: FakeBlob(  # type: ignore[method-assign]
        b"export const a = 1;\n", encoding="utf-8"
    )

    file = connector.get_files(path_filter=FileFilter().should_include).files[0]
    assert file.content == "export const a = 1;\n"


# ------------------------------------------------------------------- limits

def test_file_cap_truncates_and_is_reported() -> None:
    repo = FakeRepo({f"src/f{i}.ts": b"export const a = 1;\n" for i in range(10)})
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(
        path_filter=FileFilter().should_include, max_files=3
    )

    assert len(snapshot.files) == 3
    assert snapshot.truncated is True
    assert snapshot.discovered_paths == 10
    # The cap must prevent the downloads, not just trim the results.
    assert len(repo.requested_blobs) == 3


def test_a_truncated_tree_is_surfaced() -> None:
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"}, truncated=True)
    connector, _ = build_connector(repo)

    snapshot = connector.get_files(path_filter=FileFilter().should_include)

    assert snapshot.truncated is True
    assert any("truncated" in reason for _, reason in snapshot.errors)


def test_no_filter_accepts_everything() -> None:
    repo = FakeRepo({"README.md": b"# hi\n", "src/a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)

    snapshot = connector.get_files()

    assert len(snapshot.files) == 2


# ----------------------------------------------------------- error mapping

@pytest.mark.parametrize(
    ("github_error", "expected"),
    [
        (BadCredentialsException(401, {"message": "Bad credentials"}, {}), AuthenticationError),
        (UnknownObjectException(404, {"message": "Not Found"}, {}), RepositoryNotFoundError),
        (
            RateLimitExceededException(403, {"message": "API rate limit exceeded"}, {}),
            RateLimitError,
        ),
        (GithubException(500, {"message": "Server Error"}, {}), SourceUnavailableError),
        (GithubException(401, {"message": "Requires authentication"}, {}), AuthenticationError),
    ],
)
def test_github_errors_map_to_our_errors(
    github_error: Exception, expected: type[Exception]
) -> None:
    connector, _ = build_connector(error=github_error)

    with pytest.raises(expected):
        connector.get_files(path_filter=FileFilter().should_include)


def test_forbidden_is_reported_as_a_permission_problem() -> None:
    connector, _ = build_connector(
        error=GithubException(403, {"message": "Resource not accessible"}, {})
    )

    with pytest.raises(AuthenticationError) as caught:
        connector.get_files(path_filter=FileFilter().should_include)

    assert "permission" in caught.value.message


def test_rate_limits_are_not_waited_out() -> None:
    """A rate limit must come back as a 429, not hang the request.

    PyGithub's default retry sleeps until X-RateLimit-Reset - up to an hour -
    which would stall a synchronous HTTP request rather than answering it.
    """
    fake = FakeGithub(FakeRepo({}))
    with patch(
        "app.connectors.github_connector.Github", return_value=fake
    ) as github_cls:
        GitHubConnector(FAKE_TOKEN, "my-org/backend")

    retry = github_cls.call_args.kwargs["retry"]
    assert 403 not in retry.status_forcelist
    assert 429 not in retry.status_forcelist
    assert set(retry.status_forcelist) == {500, 502, 503, 504}
    assert not isinstance(retry, GithubRetry), "must not use the sleeping policy"


def test_secondary_rate_limit_is_reported_as_a_rate_limit() -> None:
    connector, _ = build_connector(
        error=GithubException(403, {"message": "You have exceeded a rate limit"}, {})
    )

    with pytest.raises(RateLimitError):
        connector.get_files(path_filter=FileFilter().should_include)


# ----------------------------------------------------------------- logging

# A run should be narratable from the log alone: which GitHub calls were made,
# which files were downloaded, and how many API calls it cost. Deliberately
# nothing about chunks or symbols - that is the parser's business, not progress.


def test_each_downloaded_file_is_logged_with_its_position(caplog) -> None:
    repo = FakeRepo(
        {
            "src/a.ts": b"export const a = 1;\n",
            "src/b.tsx": b"export const B = () => 1;\n",
        }
    )
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "[1/2] src/a.ts" in caplog.text
    assert "[2/2] src/b.tsx" in caplog.text


def test_filtered_out_files_are_not_logged(caplog) -> None:
    """Only files we actually fetch should produce a progress line."""
    repo = FakeRepo(SOURCES)
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "node_modules/dep/index.ts" not in caplog.text
    assert "package-lock.json" not in caplog.text
    assert caplog.text.count("[") >= 2  # the two accepted files


def test_file_sizes_are_logged_readably(caplog) -> None:
    repo = FakeRepo({"src/a.ts": b"x" * 2048})
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "2.0 KB" in caplog.text


def test_github_requests_are_announced(caplog) -> None:
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    for stage in (
        "reading repository metadata",
        "reading branch head",
        "resolving commit tree",
        "reading recursive git tree",
    ):
        assert f"GitHub: {stage}" in caplog.text


def test_the_summary_counts_api_calls(caplog) -> None:
    """Four setup calls plus one per downloaded file."""
    repo = FakeRepo({f"src/f{i}.ts": b"export const a = 1;\n" for i in range(3)})
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "Downloading 3 files from GitHub" in caplog.text
    assert "Downloaded 3 files, skipped 0 (7 GitHub API calls)" in caplog.text


def test_the_summary_reports_skipped_files(caplog) -> None:
    repo = FakeRepo(
        {"src/good.ts": b"export const a = 1;\n", "src/bin.ts": b"\x00\x01binary"}
    )
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "Downloaded 1 files, skipped 1" in caplog.text


def test_progress_volume_is_one_line_per_downloaded_file(caplog) -> None:
    """Log volume must track files, not the source inside them.

    A file holding twenty symbols still gets exactly one progress line - the
    logging is about progress through the repository, not about parsing.
    """
    dense = (
        b"export class A {"
        + b"".join(b" m%d() { return 1; }" % i for i in range(20))
        + b"}\n"
    )
    repo = FakeRepo({f"src/f{i}.ts": dense for i in range(4)})
    connector, _ = build_connector(repo)

    with caplog.at_level("INFO", logger="app.connectors.github_connector"):
        connector.get_files(path_filter=FileFilter().should_include)

    progress_lines = [
        line for line in caplog.messages if line.startswith("[")
    ]
    assert len(progress_lines) == 4


# ---------------------------------------------------------------- security

def test_error_messages_never_contain_the_token() -> None:
    connector, _ = build_connector(
        error=BadCredentialsException(401, {"message": "Bad credentials"}, {})
    )

    with pytest.raises(AuthenticationError) as caught:
        connector.get_files(path_filter=FileFilter().should_include)

    assert FAKE_TOKEN.get_secret_value() not in caught.value.message


def test_the_token_never_reaches_the_progress_logs(caplog) -> None:
    """The chattier the logging, the more this needs guarding."""
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)

    with caplog.at_level("DEBUG"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert FAKE_TOKEN.get_secret_value() not in caplog.text
    assert "ghp_" not in caplog.text


def test_file_contents_are_not_logged(caplog) -> None:
    """Progress lines carry a path and a size - never the source itself."""
    repo = FakeRepo({"src/a.ts": b"export const SECRET_MARKER = 1;\n"})
    connector, _ = build_connector(repo)

    with caplog.at_level("DEBUG"):
        connector.get_files(path_filter=FileFilter().should_include)

    assert "SECRET_MARKER" not in caplog.text


def test_the_connector_does_not_keep_the_token_on_an_attribute() -> None:
    """Nothing a repr or a traceback could print should hold the raw token."""
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"})
    connector, _ = build_connector(repo)

    secret = FAKE_TOKEN.get_secret_value()
    assert secret not in repr(vars(connector))


def test_closing_releases_the_authenticated_session() -> None:
    repo = FakeRepo({"src/a.ts": b"export const a = 1;\n"})
    connector, fake = build_connector(repo)

    with connector:
        connector.get_files(path_filter=FileFilter().should_include)

    assert fake.closed is True
