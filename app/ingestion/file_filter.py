
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FileFilterConfig:
    """The rules. Construct a modified copy with `dataclasses.replace`."""

    allowed_extensions: frozenset[str] = frozenset({".ts", ".tsx"})

    ignored_directories: frozenset[str] = frozenset(
        {
            "node_modules",
            "dist",
            "build",
            "coverage",
            ".git",
            ".next",
            "out",
            "vendor",
            "tmp",
            "temp",
        }
    )

    ignored_filenames: frozenset[str] = frozenset(
        {
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        }
    )

    ignored_suffixes: tuple[str, ...] = (".min.js", ".min.ts", ".map")

    exclude_declaration_files: bool = True
    declaration_suffix: str = ".d.ts"

    exclude_test_files: bool = True
    test_file_suffixes: tuple[str, ...] = (
        ".test.ts",
        ".test.tsx",
        ".spec.ts",
        ".spec.tsx",
    )

    max_file_size_bytes: int = 1_048_576


class FileFilter:
    """Answers one question: should this path be ingested?
    """

    def __init__(self, config: FileFilterConfig | None = None) -> None:
        self.config = config or FileFilterConfig()

    def should_include(self, path: str, size: int | None = None) -> bool:
        """True if `path` should be fetched and parsed.
        """
        config = self.config

        normalised = path.replace("\\", "/").strip("/")
        if not normalised:
            return False

        pure = PurePosixPath(normalised)
        file_name = pure.name
        lowered_name = file_name.lower()

        for segment in pure.parts[:-1]:
            if segment in config.ignored_directories:
                return False

        if file_name in config.ignored_filenames:
            return False

        if lowered_name.endswith(config.ignored_suffixes):
            return False

        if config.exclude_declaration_files and lowered_name.endswith(
            config.declaration_suffix
        ):
            return False

        if config.exclude_test_files and lowered_name.endswith(
            config.test_file_suffixes
        ):
            return False

        if pure.suffix.lower() not in config.allowed_extensions:
            return False

        if size is not None and size > config.max_file_size_bytes:
            return False

        return True
