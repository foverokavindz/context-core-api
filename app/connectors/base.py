"""The shape every source connector follows.

A connector talks to exactly one outside system and hands back RepositoryFile
objects. That is the whole contract. GitHub is the only implementation today;
Jira and Confluence would each be another file in this folder, and nothing
downstream of RepositoryFile would need to change to accommodate them.

This is intentionally a small abstraction, not a connector framework.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.repository_file import RepositoryFile

# Decides whether a discovered path is worth fetching, given its path and its
# size in bytes (None when the source does not report one).
#
# The connector takes this as a callback rather than importing FileFilter
# directly. That keeps the filter free of any GitHub knowledge and the connector
# free of any filtering policy, while still letting the connector apply the
# decision at the only point where it saves work: before downloading content.
PathFilter = Callable[[str, int | None], bool]


@dataclass
class SourceSnapshot:
    """Everything one connector run produced.

    Carries the counts the pipeline needs to report a funnel, not just the
    files: `discovered_paths` is how many candidates existed before filtering,
    which is what makes "240 discovered, 87 accepted" possible to report.
    """

    repository: str
    branch: str
    commit_sha: str

    # Candidate file paths seen in the tree, before filtering.
    discovered_paths: int

    # True when this is a partial view: the file cap was reached, or the source
    # itself truncated its listing.
    truncated: bool = False

    files: list[RepositoryFile] = field(default_factory=list)

    # (path, reason) for files that were skipped. Skipping is never fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)


class BaseSourceConnector(ABC):
    """Fetches source material from one outside system."""

    def close(self) -> None:
        """Release any session held against the outside system.

        A no-op by default; connectors holding an authenticated client override
        it so credentials do not outlive the request that supplied them.
        """

    def __enter__(self) -> "BaseSourceConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @abstractmethod
    def get_files(
        self,
        *,
        branch: str | None = None,
        path_filter: PathFilter | None = None,
        max_files: int | None = None,
    ) -> SourceSnapshot:
        """Return the files for a branch, filtered before any content is read.

        Implementations must apply `path_filter` to a path *before* fetching
        that file's contents, and must record unreadable files in
        `SourceSnapshot.errors` rather than raising.
        """
