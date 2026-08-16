"""Talks to the GitHub REST API and hands back RepositoryFile objects.

This is the ONLY module in the application that imports PyGithub. Everything it
returns is our own model, so the parser, the chunker and anything built later
stay completely unaware of where the source came from.

How a repository is walked, and why:

    repository -> branch -> commit SHA -> commit's tree SHA -> recursive tree
        -> filter paths -> fetch only the accepted blobs

The recursive Git tree API returns every path in the repository in one call.
That is far cheaper than walking the contents API directory by directory, and -
more importantly - it lets the filter run on paths *before* any file content is
downloaded, so ignored files never cost an API call.
"""

import base64
import logging
from pathlib import PurePosixPath

from github import Auth, Github
from github.GithubException import (
    BadCredentialsException,
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)
from pydantic import SecretStr
from urllib3.util.retry import Retry

from app.connectors.base import BaseSourceConnector, PathFilter, SourceSnapshot
from app.core.exceptions import (
    AuthenticationError,
    BranchNotFoundError,
    IngestionError,
    RateLimitError,
    RepositoryNotFoundError,
    SourceUnavailableError,
)
from app.models.repository_file import RepositoryFile, language_for_extension

logger = logging.getLogger(__name__)

# Tree entries are either "blob" (a file) or "tree" (a directory). We only want
# files; the recursive listing already flattens the directories away.
_TREE_BLOB_TYPE = "blob"

# Byte-order mark. Editors on Windows write it at the head of UTF-8 files, where
# it decodes to a stray invisible character that would land inside the first
# chunk of every affected file.
BOM = "\ufeff"

# Retry transient server and connection failures - but never a rate limit.
#
# PyGithub's own default (GithubRetry) treats a rate limit as retryable and
# sleeps until X-RateLimit-Reset, up to ten times. Inside a synchronous HTTP
# request that means the caller hangs for as long as an hour instead of being
# told what happened. A plain urllib3 policy skips that behaviour, so a rate
# limit comes straight back as an exception and we answer 429 immediately.
DEFAULT_RETRY = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=None,
    raise_on_status=False,
)


def _human_size(size: int | None) -> str:
    """Render a byte count for a log line. Cosmetic only."""
    if size is None:
        return "unknown size"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class GitHubConnector(BaseSourceConnector):
    """Reads one repository through the GitHub API.

    The token is taken as a SecretStr and unwrapped exactly once, on the line
    that builds the API client. It is never assigned to an attribute, so there
    is nothing holding it after construction beyond PyGithub's own auth object,
    and nothing for a repr or a traceback to print.
    """

    def __init__(
        self,
        token: SecretStr,
        repository: str,
        *,
        timeout: int = 30,
        per_page: int = 100,
        retry: Retry | None = DEFAULT_RETRY,
    ) -> None:
        self.repository = repository
        # Every GitHub round trip this connector makes. Reported at the end of a
        # run: the pipeline spends one call per file, so seeing the number is
        # what makes a later rate limit make sense.
        self._api_calls = 0
        self._github = Github(
            auth=Auth.Token(token.get_secret_value()),
            timeout=timeout,
            per_page=per_page,
            retry=retry,
        )

    def close(self) -> None:
        """Drop the authenticated session.

        The ingestion service uses this connector as a context manager, so the
        client holding the caller's token does not outlive their request.
        """
        self._github.close()

    # ---------------------------------------------------------------- public

    def get_files(
        self,
        *,
        branch: str | None = None,
        path_filter: PathFilter | None = None,
        max_files: int | None = None,
    ) -> SourceSnapshot:
        """Fetch the accepted source files for one branch."""
        # No "connecting to X" line here: the ingestion service already logs the
        # repository as its run header, and the request lines below say what is
        # actually being asked of GitHub.
        repo = self._resolve_repository()
        branch_name, commit_sha = self._resolve_commit(repo, branch)
        entries, tree_truncated = self._discover_blobs(repo, commit_sha)

        logger.info("Discovered %d files", len(entries))

        accepted, capped = self._apply_filter(entries, path_filter, max_files)

        logger.info("%d files passed filter", len(accepted))

        snapshot = SourceSnapshot(
            repository=self.repository,
            branch=branch_name,
            commit_sha=commit_sha,
            discovered_paths=len(entries),
            truncated=tree_truncated or capped,
        )
        if tree_truncated:
            snapshot.errors.append(
                (
                    self.repository,
                    "GitHub truncated the repository tree; some paths were not "
                    "listed.",
                )
            )

        # Only now, having decided what we want, do we spend an API call per file.
        total = len(accepted)
        logger.info("Downloading %d files from GitHub", total)

        for position, (path, blob_sha, size) in enumerate(accepted, start=1):
            file = self._fetch_file(
                repo,
                path=path,
                blob_sha=blob_sha,
                size=size,
                branch=branch_name,
                commit_sha=commit_sha,
                errors=snapshot.errors,
                position=position,
                total=total,
            )
            if file is not None:
                snapshot.files.append(file)

        logger.info(
            "Downloaded %d files, skipped %d (%d GitHub API calls)",
            len(snapshot.files),
            total - len(snapshot.files),
            self._api_calls,
        )
        return snapshot

    # --------------------------------------------------------------- private

    def _log_request(self, what: str) -> None:
        """Announce a GitHub round trip and count it.

        Used for the handful of setup calls. Blob downloads count themselves,
        because they already log a line of their own and logging both would
        double every file.
        """
        self._api_calls += 1
        logger.info("GitHub: %s", what)

    def _resolve_repository(self):
        """Look up the repository, translating GitHub's errors into ours."""
        self._log_request("reading repository metadata")
        try:
            return self._github.get_repo(self.repository)
        except UnknownObjectException as exc:
            self._log_github_error("resolving repository", exc)
            raise RepositoryNotFoundError() from exc
        except GithubException as exc:
            raise self._translate(exc, action="resolving repository") from exc

    def _resolve_commit(self, repo, branch: str | None) -> tuple[str, str]:
        """Resolve a branch name (or the default branch) to its HEAD commit."""
        try:
            branch_name = branch or repo.default_branch
        except GithubException as exc:
            raise self._translate(exc, action="reading default branch") from exc

        logger.info("Resolved branch: %s", branch_name)

        self._log_request("reading branch head")
        try:
            resolved = repo.get_branch(branch_name)
        except UnknownObjectException as exc:
            self._log_github_error("resolving branch", exc)
            raise BranchNotFoundError(
                f"Branch '{branch_name}' does not exist in this repository."
            ) from exc
        except GithubException as exc:
            # GitHub answers a missing branch with 404, and occasionally 422
            # when the name is unusable as a ref.
            if getattr(exc, "status", None) in (404, 422):
                self._log_github_error("resolving branch", exc)
                raise BranchNotFoundError(
                    f"Branch '{branch_name}' does not exist in this repository."
                ) from exc
            raise self._translate(exc, action="resolving branch") from exc

        commit_sha = resolved.commit.sha
        logger.info("Resolved commit: %s", commit_sha)
        return branch_name, commit_sha

    def _discover_blobs(
        self, repo, commit_sha: str
    ) -> tuple[list[tuple[str, str, int | None]], bool]:
        """List every file path in the commit, as (path, blob sha, size).

        The tree endpoint wants a *tree* SHA, so the commit is resolved to its
        tree first rather than relying on GitHub accepting a commit SHA there.
        """
        try:
            self._log_request("resolving commit tree")
            git_commit = repo.get_git_commit(commit_sha)
            tree_sha = git_commit.tree.sha

            self._log_request("reading recursive git tree")
            tree = repo.get_git_tree(tree_sha, recursive=True)
        except GithubException as exc:
            raise self._translate(exc, action="reading repository tree") from exc

        # `truncated` means GitHub gave us a partial listing. Reading raw_data on
        # an already-loaded object is cheap, but guard it anyway - a missing flag
        # must not break discovery.
        try:
            truncated = bool(tree.raw_data.get("truncated", False))
        except Exception:  # pragma: no cover - defensive
            truncated = False

        entries: list[tuple[str, str, int | None]] = []
        for element in tree.tree:
            if element.type != _TREE_BLOB_TYPE:
                continue
            entries.append((element.path, element.sha, element.size))

        return entries, truncated

    @staticmethod
    def _apply_filter(
        entries: list[tuple[str, str, int | None]],
        path_filter: PathFilter | None,
        max_files: int | None,
    ) -> tuple[list[tuple[str, str, int | None]], bool]:
        """Keep the paths worth fetching, and report whether the cap was hit."""
        accepted = [
            entry
            for entry in entries
            if path_filter is None or path_filter(entry[0], entry[2])
        ]

        if max_files is not None and len(accepted) > max_files:
            logger.warning(
                "Limiting ingestion to %d of %d accepted files", max_files, len(accepted)
            )
            return accepted[:max_files], True

        return accepted, False

    def _fetch_file(
        self,
        repo,
        *,
        path: str,
        blob_sha: str,
        size: int | None,
        branch: str,
        commit_sha: str,
        errors: list[tuple[str, str]],
        position: int,
        total: int,
    ) -> RepositoryFile | None:
        """Download and decode one file, or record why it was skipped.

        Returns None instead of raising for anything wrong with a single file.
        One binary blob or one unreadable file must not cost the whole run.
        """
        # Logged before the download, not after, so a slow or stalled fetch is
        # attributable to a named file rather than to silence.
        logger.info("[%d/%d] %s (%s)", position, total, path, _human_size(size))

        self._api_calls += 1
        try:
            blob = repo.get_git_blob(blob_sha)
        except GithubException as exc:
            self._log_github_error(f"fetching {path}", exc)
            errors.append((path, "Could not be downloaded from GitHub."))
            return None

        try:
            raw = self._decode_blob(blob)
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping %s: undecodable blob payload (%s)", path, exc)
            errors.append((path, "Blob payload could not be decoded."))
            return None

        if b"\x00" in raw:
            logger.info("Skipping %s: binary file", path)
            errors.append((path, "Skipped: file appears to be binary."))
            return None

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.info("Skipping %s: not valid UTF-8", path)
            errors.append((path, "Skipped: file is not valid UTF-8 text."))
            return None

        # A byte-order mark would otherwise show up as a stray character at the
        # very start of the first chunk.
        if content.startswith(BOM):
            content = content[1:]

        pure = PurePosixPath(path)
        extension = pure.suffix or None

        return RepositoryFile(
            repository=self.repository,
            branch=branch,
            commit_sha=commit_sha,
            file_path=path,
            file_name=pure.name,
            extension=extension,
            file_sha=blob_sha,
            size=size if size is not None else len(raw),
            language=language_for_extension(extension),
            content=content,
            external_id=path,
            title=pure.name,
            version_key=blob_sha,
        )

    @staticmethod
    def _decode_blob(blob) -> bytes:
        """Turn a GitBlob into raw bytes, whatever encoding GitHub used."""
        if blob.encoding == "base64":
            return base64.b64decode(blob.content)
        # GitHub occasionally returns utf-8 directly for very small blobs.
        return (blob.content or "").encode("utf-8")

    # ------------------------------------------------------- error handling

    @staticmethod
    def _log_github_error(action: str, exc: GithubException) -> None:
        """Record GitHub's own wording server-side only.

        GitHub error bodies never contain the token, but they do contain
        internal detail we do not want in a client response - so it is logged
        here and the client gets one of our fixed messages instead.
        """
        logger.warning(
            "GitHub API error while %s: status=%s detail=%s",
            action,
            getattr(exc, "status", "unknown"),
            getattr(exc, "data", ""),
        )

    def _translate(self, exc: GithubException, *, action: str) -> IngestionError:
        """Map a PyGithub exception onto the error the client should see."""
        self._log_github_error(action, exc)
        status = getattr(exc, "status", None)

        if isinstance(exc, RateLimitExceededException):
            return RateLimitError()
        if isinstance(exc, BadCredentialsException) or status == 401:
            return AuthenticationError()
        if status == 403:
            # 403 covers both "rate limited" and "token lacks this permission".
            # The body is the only thing that distinguishes them.
            if "rate limit" in str(getattr(exc, "data", "")).lower():
                return RateLimitError()
            return AuthenticationError(
                "The supplied token does not have permission to read this "
                "repository."
            )
        if status == 404:
            return RepositoryNotFoundError()

        return SourceUnavailableError()
