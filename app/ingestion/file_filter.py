"""Decides which repository paths are worth ingesting.

This runs on the repository *tree* - that is, on paths and sizes alone, before
any file content is downloaded. Downloading 2,000 files and then throwing away
1,900 of them would waste an API call each; deciding from the path first means
we only pay for the files we actually want.

Every rule reads from FileFilterConfig. Nothing is hard-coded at a call site, so
widening the filter to Python or Java later is a config change, not a code hunt.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FileFilterConfig:
    """The rules. Construct a modified copy with `dataclasses.replace`."""

    # V1 targets TypeScript only. Add ".py", ".java" etc. here to widen it -
    # but a matching parser has to be registered too, or the file is fetched and
    # then reported as unsupported.
    allowed_extensions: frozenset[str] = frozenset({".ts", ".tsx"})

    # Matched against whole path segments, never as substrings, so a legitimate
    # "src/distribution/" is not mistaken for a build "dist/".
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

    # Exact file names that are never source code.
    ignored_filenames: frozenset[str] = frozenset(
        {
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        }
    )

    # Generated or minified output. These carry no useful structure and would
    # produce enormous, meaningless chunks.
    ignored_suffixes: tuple[str, ...] = (".min.js", ".min.ts", ".map")

    # Declaration files are type surface with no implementation. They tend to
    # restate what the real source already says, so they are noise for V1 -
    # but this is a switch, not an assumption baked into the pipeline.
    exclude_declaration_files: bool = True
    declaration_suffix: str = ".d.ts"

    # Test files describe the code rather than being the code. Excluded by
    # default for the same reason; flip this to index them.
    exclude_test_files: bool = True
    test_file_suffixes: tuple[str, ...] = (
        ".test.ts",
        ".test.tsx",
        ".spec.ts",
        ".spec.tsx",
    )

    # 1 MB. Anything larger is almost certainly generated, bundled or vendored,
    # and parsing it in a prototype is not worth the time or memory.
    max_file_size_bytes: int = 1_048_576


class FileFilter:
    """Answers one question: should this path be ingested?

    Deliberately free of any GitHub or network dependency so it can be unit
    tested with nothing but strings.
    """

    def __init__(self, config: FileFilterConfig | None = None) -> None:
        self.config = config or FileFilterConfig()

    def should_include(self, path: str, size: int | None = None) -> bool:
        """True if `path` should be fetched and parsed.

        `size` is the blob size in bytes when the source knows it. None means
        "unknown", which passes the size check rather than failing it - an
        unknown size is not evidence that a file is too big.
        """
        config = self.config

        # Repository trees always use forward slashes; normalise anyway so the
        # filter behaves the same if handed a Windows-style path in a test.
        normalised = path.replace("\\", "/").strip("/")
        if not normalised:
            return False

        pure = PurePosixPath(normalised)
        file_name = pure.name
        lowered_name = file_name.lower()

        # Segment match, not substring match. `pure.parts[:-1]` is the
        # directories only, so a *file* named "dist" cannot exclude itself.
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

        # `.suffix` on "Login.spec.tsx" is ".tsx", which is what we want here -
        # the test/declaration checks above already ran on the full name.
        if pure.suffix.lower() not in config.allowed_extensions:
            return False

        if size is not None and size > config.max_file_size_bytes:
            return False

        return True
