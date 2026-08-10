"""Tests for the path filter.

The filter runs before any file is downloaded, so a mistake here either wastes
API calls on junk or silently drops real source. Both directions are tested.
"""

from dataclasses import replace

import pytest

from app.ingestion.file_filter import FileFilter, FileFilterConfig


@pytest.fixture
def file_filter() -> FileFilter:
    return FileFilter()


@pytest.mark.parametrize(
    "path",
    [
        "src/auth/AuthService.ts",
        "src/components/Login.tsx",
        "index.ts",
        "src/deeply/nested/path/to/module.ts",
    ],
)
def test_accepts_typescript_sources(file_filter: FileFilter, path: str) -> None:
    assert file_filter.should_include(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/library/index.ts",
        "packages/web/node_modules/dep/index.ts",
        "dist/app.ts",
        "build/main.ts",
        "coverage/report.ts",
        ".next/server/page.ts",
        "out/bundle.ts",
        "vendor/lib.ts",
        "tmp/scratch.ts",
        "temp/scratch.ts",
    ],
)
def test_rejects_ignored_directories(file_filter: FileFilter, path: str) -> None:
    assert file_filter.should_include(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "package.json",
        "src/styles.css",
        "src/legacy.js",
        "image.png",
        "Makefile",
    ],
)
def test_rejects_unsupported_extensions(file_filter: FileFilter, path: str) -> None:
    assert file_filter.should_include(path) is False


@pytest.mark.parametrize(
    "path", ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "src/app.min.js"]
)
def test_rejects_lockfiles_and_minified(file_filter: FileFilter, path: str) -> None:
    assert file_filter.should_include(path) is False


def test_rejects_declaration_files_by_default(file_filter: FileFilter) -> None:
    assert file_filter.should_include("src/types/generated.d.ts") is False


def test_declaration_files_can_be_enabled() -> None:
    permissive = FileFilter(
        replace(FileFilterConfig(), exclude_declaration_files=False)
    )
    assert permissive.should_include("src/types/generated.d.ts") is True


@pytest.mark.parametrize(
    "path",
    [
        "src/auth/AuthService.test.ts",
        "src/components/Login.test.tsx",
        "src/auth/AuthService.spec.ts",
        "src/components/Login.spec.tsx",
    ],
)
def test_rejects_test_files_by_default(file_filter: FileFilter, path: str) -> None:
    assert file_filter.should_include(path) is False


def test_test_files_can_be_enabled() -> None:
    permissive = FileFilter(replace(FileFilterConfig(), exclude_test_files=False))
    assert permissive.should_include("src/auth/AuthService.test.ts") is True


def test_rejects_oversized_files(file_filter: FileFilter) -> None:
    assert file_filter.should_include("src/huge.ts", size=2 * 1024 * 1024) is False


def test_accepts_file_at_the_size_limit(file_filter: FileFilter) -> None:
    limit = FileFilterConfig().max_file_size_bytes
    assert file_filter.should_include("src/big.ts", size=limit) is True


def test_unknown_size_is_not_treated_as_oversized(file_filter: FileFilter) -> None:
    """An unreported size is not evidence that a file is too large."""
    assert file_filter.should_include("src/app.ts", size=None) is True


def test_directory_match_is_on_segments_not_substrings(
    file_filter: FileFilter,
) -> None:
    """A real 'src/distribution/' must not be mistaken for a build 'dist/'."""
    assert file_filter.should_include("src/distribution/index.ts") is True
    assert file_filter.should_include("src/outbound/mailer.ts") is True
    assert file_filter.should_include("src/building/blocks.ts") is True


def test_test_suffix_match_requires_the_dot(file_filter: FileFilter) -> None:
    """'latest.ts' ends in 'test.ts' but is not a test file."""
    assert file_filter.should_include("src/latest.ts") is True


def test_file_named_like_an_ignored_directory_is_still_checked_normally(
    file_filter: FileFilter,
) -> None:
    """Only directory segments are matched, so a file called dist.ts is fine."""
    assert file_filter.should_include("src/dist.ts") is True


def test_empty_path_is_rejected(file_filter: FileFilter) -> None:
    assert file_filter.should_include("") is False


def test_windows_style_separators_are_normalised(file_filter: FileFilter) -> None:
    assert file_filter.should_include("node_modules\\library\\index.ts") is False
    assert file_filter.should_include("src\\auth\\AuthService.ts") is True


def test_extensions_are_configurable() -> None:
    """Widening to another language is a config change, not a code change."""
    with_python = FileFilter(
        replace(FileFilterConfig(), allowed_extensions=frozenset({".py"}))
    )
    assert with_python.should_include("app/main.py") is True
    assert with_python.should_include("src/app.ts") is False
