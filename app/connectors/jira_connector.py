"""The Jira Cloud connector - the only module that talks to Jira.

    resolve cloud id  ->  validate credentials  ->  validate project  ->  search
    GET /_edge/tenant_info    GET /myself        GET /project/{k}   POST /search/jql

This is the only module that knows Jira's endpoints, and the only Jira module
that imports httpx. It hands back raw issue JSON exactly as it came off the
wire: no ADF parsing, no chunk building, no parent/child aggregation. Those live
downstream, where they can be tested without a fake HTTP server.

Requests go through Atlassian's gateway at api.atlassian.com rather than
straight at the site, because that is the only address a *scoped* API token can
authenticate against - pointing one at the site's own hostname returns 401 no
matter how correct it is. The gateway needs the site's cloud id in the path, so
a run begins by reading it from the site's public tenant_info endpoint. That
lookup is the one request here that carries no credential, and it uses a
separate unauthenticated client so it cannot accidentally acquire one.

The two validation calls before the search are deliberate. Without them a wrong
token and an invisible project both surface as a confusing 400 from the search
endpoint; with them each one has its own status and its own message.

The API token reaches exactly one expression - the httpx auth tuple. It is never
assigned to an attribute, never logged, and never folded into an error message.
Request headers are not logged at all, since that is where the credential rides.

Log volume tracks pages, not issues: one line per API call, with per-issue detail
at DEBUG. The GitHub connector logs a line per file because a file *is* an API
call - here a hundred issues arrive in one.
"""

import logging
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    IngestionError,
    JiraApiError,
    JiraAuthenticationError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitError,
)

logger = logging.getLogger(__name__)

# One raw issue exactly as the REST API returned it.
JiraIssueJson = dict[str, object]

# Public site metadata: returns {"cloudId": "..."} and needs no credential.
TENANT_INFO_PATH = "/_edge/tenant_info"

# Every authenticated call is made against the gateway, with the site's cloud id
# standing in for its hostname.
GATEWAY_BASE_URL = "https://api.atlassian.com/ex/jira/{cloud_id}"

MYSELF_PATH = "/rest/api/3/myself"
PROJECT_PATH = "/rest/api/3/project/{key}"
SEARCH_PATH = "/rest/api/3/search/jql"

# Exactly the fields the pipeline needs, and nothing else - an unqualified
# search returns every custom field a site has ever defined.
#
# `parent` earns its place twice over: it is what lets the ingestion service
# rebuild Epic -> Story links with a local pass instead of one extra API call
# per Epic.
ISSUE_FIELDS: tuple[str, ...] = (
    "summary",
    "description",
    "status",
    "issuetype",
    "parent",
    "project",
)

# Jira's own maximum for the enhanced search endpoint.
DEFAULT_PAGE_SIZE = 100

# A stop for a server that never reports a last page. At the default page size
# this is 10,000 issues - an order of magnitude past MAX_ISSUES_PER_INGESTION,
# so in practice only a misbehaving server can reach it.
DEFAULT_MAX_PAGES = 100

DEFAULT_TIMEOUT = 30

# Jira error bodies are usually small JSON, but a proxy or an SSO wall can
# return a multi-kilobyte HTML page. Only the head of it is worth a log line.
ERROR_BODY_LOG_CHARS = 500


@dataclass
class JiraSnapshot:
    """Everything one connector run retrieved.

    `truncated` means the run stopped before Jira ran out of results - the issue
    cap was reached, or pagination hit a guard. It has nothing to do with how
    much of this the HTTP response later serialises.
    """

    site_url: str
    project_key: str

    retrieved_issues: int
    truncated: bool = False

    issues: list[JiraIssueJson] = field(default_factory=list)

    # (issue or project key, reason) for anything skipped or degraded. Never
    # fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)


class JiraConnector:
    """Fetches Epics and Stories from one Jira Cloud site."""

    def __init__(
        self,
        site_url: str,
        email: str,
        api_token: SecretStr,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.site_url = site_url.rstrip("/")
        self.page_size = page_size
        self.max_pages = max_pages

        # Authenticated Jira API calls, which are what a rate limit counts.
        # Reported at the end of a run so a later 429 is possible to reason
        # about. The public tenant lookup is not one of these and is logged on
        # its own line instead.
        self._api_calls = 0
        self._cloud_id: str | None = None

        # The tenant lookup needs no credential, so it gets a client that has
        # none - the token cannot leak into a request that never required it.
        self._public_client = httpx.Client(
            base_url=self.site_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )

        # The one place the token is unwrapped. Neither it nor the email is
        # stored on self, so nothing can print them by accident later.
        #
        # base_url is set once the cloud id is known; every authenticated path
        # is relative, so nothing can be requested before that happens.
        #
        # No retry policy on purpose: httpx does not retry by default, and
        # nothing here should sleep waiting for a rate limit to reset. A 429
        # comes back to the caller in well under a second.
        self._client = httpx.Client(
            auth=(email, api_token.get_secret_value()),
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )

    def close(self) -> None:
        """Drop both sessions.

        The ingestion service uses this connector as a context manager, so the
        client holding the caller's token does not outlive their request.
        """
        self._client.close()
        self._public_client.close()

    def __enter__(self) -> "JiraConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------------------------------------------------------- public

    def get_issues(
        self, project_key: str, *, max_issues: int | None = None
    ) -> JiraSnapshot:
        """Fetch every Epic and Story in one project.

        There is deliberately no `jql` parameter. The query is built here from
        the project key alone, so a caller cannot reach issues outside the
        project they asked for.
        """
        self._resolve_cloud_id()
        self._verify_credentials()
        self._verify_project(project_key)

        return self._search_all(project_key, max_issues=max_issues)

    # --------------------------------------------------------------- private

    def _resolve_cloud_id(self) -> None:
        """Look up the site's cloud id and point the API client at the gateway.

        Unauthenticated, because tenant_info is public - and deliberately so:
        a scoped token cannot authenticate anywhere until this has run, so
        sending one here would be both useless and needless exposure.
        """
        action = "resolving the Jira site"
        logger.info("Jira: %s", action)

        try:
            response = self._public_client.get(TENANT_INFO_PATH)
        except httpx.TimeoutException as exc:
            self._log_transport_error(action, exc)
            raise JiraApiError("Jira did not respond in time.") from exc
        except httpx.RequestError as exc:
            self._log_transport_error(action, exc)
            raise JiraApiError() from exc

        if response.status_code != httpx.codes.OK:
            self._log_jira_error(action, response)
            raise JiraNotFoundError("No Jira site was found at that URL.")

        cloud_id = self._read_json(response, action=action).get("cloudId")
        if not isinstance(cloud_id, str) or not cloud_id:
            self._log_jira_error(action, response)
            raise JiraApiError("Jira did not report a cloud id for this site.")

        self._cloud_id = cloud_id
        self._client.base_url = GATEWAY_BASE_URL.format(cloud_id=cloud_id)
        logger.info("Resolved Jira cloud id for %s", self.site_url)

    def _verify_credentials(self) -> None:
        """Check that Jira accepts this email and token at all.

        One cheap call that turns a bad credential into a 401 here rather than
        an unexplained failure two calls later.
        """
        action = "validating credentials"
        response = self._request("GET", MYSELF_PATH, action=action)

        if response.status_code == httpx.codes.NOT_FOUND:
            # At this endpoint a 404 is about the site, not a project.
            self._log_jira_error(action, response)
            raise JiraNotFoundError("No Jira site was found at that URL.")
        if response.status_code != httpx.codes.OK:
            raise self._translate(response, action=action)

        # A 200 is not proof on its own. An SSO wall or a misconfigured proxy
        # can answer this endpoint with an HTML login page and a 200, which
        # would otherwise sail through and fail confusingly at the search.
        payload = self._read_json(response, action=action)
        if "accountId" not in payload:
            logger.warning("Jira answered /myself without an account; treating as 401")
            raise JiraAuthenticationError()

        logger.info("Jira authentication successful")

    def _verify_project(self, project_key: str) -> None:
        """Check that the project exists and this account can see it."""
        action = f"reading project {project_key}"
        response = self._request(
            "GET", PROJECT_PATH.format(key=project_key), action=action
        )

        if response.status_code == httpx.codes.NOT_FOUND:
            self._log_jira_error(action, response)
            raise JiraNotFoundError(
                f"Jira project '{project_key}' was not found, or the account "
                "cannot see it."
            )
        if response.status_code != httpx.codes.OK:
            raise self._translate(response, action=action)

    def _search_all(
        self, project_key: str, *, max_issues: int | None
    ) -> JiraSnapshot:
        """Walk every page of search results, up to the issue cap."""
        jql = self._build_jql(project_key)
        logger.info("Searching Epics and Stories in %s", project_key)

        issues: list[JiraIssueJson] = []
        errors: list[tuple[str, str]] = []
        seen_tokens: set[str] = set()
        page_token: str | None = None
        truncated = False
        page = 0

        while True:
            page += 1
            if page > self.max_pages:
                logger.warning(
                    "Stopping after %d pages; Jira never reported a last page",
                    self.max_pages,
                )
                errors.append(
                    (project_key, "Ingestion stopped at the page ceiling.")
                )
                truncated = True
                break

            # Ask for only what is still wanted. The cap has to prevent the
            # fetch, not trim the result afterwards - otherwise a cap of 1 on a
            # large project still costs a full page.
            remaining = None if max_issues is None else max_issues - len(issues)
            page_size = (
                self.page_size if remaining is None else min(self.page_size, remaining)
            )

            payload = self._search_page(
                jql, page_token=page_token, max_results=page_size
            )

            batch = payload.get("issues")
            batch = batch if isinstance(batch, list) else []
            issues.extend(batch)
            logger.info("Jira page %d returned %d issues", page, len(batch))
            for raw in batch:
                if isinstance(raw, dict):
                    logger.debug("Retrieved issue %s", raw.get("key"))

            next_token = payload.get("nextPageToken")
            next_token = (
                next_token if isinstance(next_token, str) and next_token else None
            )

            # The clean ending. Reached before the cap check on purpose: a cap
            # that happens to land on the final issue is not truncation.
            if bool(payload.get("isLast")) or next_token is None:
                break

            if max_issues is not None and len(issues) >= max_issues:
                logger.warning(
                    "Limiting ingestion to %d issues; project %s has more",
                    max_issues,
                    project_key,
                )
                errors.append(
                    (
                        project_key,
                        f"Ingestion stopped at the {max_issues}-issue cap; the "
                        "project contains more.",
                    )
                )
                truncated = True
                break

            if not batch:
                # A page with nothing on it, promising another one, would spin
                # forever.
                logger.warning("Jira returned an empty page while promising more")
                truncated = True
                break

            if next_token in seen_tokens:
                logger.warning("Jira repeated a page token; stopping to avoid a loop")
                truncated = True
                break

            seen_tokens.add(next_token)
            page_token = next_token

        # Jira honours maxResults, but a proxy in front of it might not.
        if max_issues is not None and len(issues) > max_issues:
            del issues[max_issues:]

        logger.info(
            "Retrieved %d Jira issues from %d page(s) (%d Jira API calls)",
            len(issues),
            page,
            self._api_calls,
        )

        return JiraSnapshot(
            site_url=self.site_url,
            project_key=project_key,
            retrieved_issues=len(issues),
            truncated=truncated,
            issues=issues,
            errors=errors,
        )

    @staticmethod
    def _build_jql(project_key: str) -> str:
        """Build the one query this connector ever runs.

        Interpolation is safe here because PROJECT_KEY_PATTERN restricts the key
        to [A-Z][A-Z0-9]{1,9} before it reaches this point - there is no
        character available to close the quoted string. The quotes are belt and
        braces.

        The ordering is not cosmetic: it gives pagination a stable total order,
        so a capped run returns the same issues every time rather than an
        arbitrary slice.
        """
        return (
            f'project = "{project_key}" '
            'AND issuetype in ("Epic", "Story") '
            "ORDER BY created ASC"
        )

    def _search_page(
        self, jql: str, *, page_token: str | None, max_results: int
    ) -> dict[str, object]:
        """Fetch one page of search results."""
        action = "searching issues"
        body: dict[str, object] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": list(ISSUE_FIELDS),
        }
        if page_token is not None:
            body["nextPageToken"] = page_token

        response = self._request("POST", SEARCH_PATH, json_body=body, action=action)

        if response.status_code == httpx.codes.BAD_REQUEST:
            # A 400 here means the JQL this module built is wrong, which is our
            # bug rather than the caller's - so it is logged loudly and reported
            # as an upstream failure rather than a validation error.
            self._log_jira_error(action, response)
            raise JiraApiError("Jira rejected the issue search.")
        if response.status_code != httpx.codes.OK:
            raise self._translate(response, action=action)

        return self._read_json(response, action=action)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        action: str,
    ) -> httpx.Response:
        """Make one Jira round trip, turning transport failures into our errors."""
        self._log_request(action)
        try:
            return self._client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            # Must come first: TimeoutException is a subclass of RequestError,
            # so the other order would silently lose the timeout wording.
            self._log_transport_error(action, exc)
            raise JiraApiError("Jira did not respond in time.") from exc
        except httpx.RequestError as exc:
            self._log_transport_error(action, exc)
            raise JiraApiError() from exc

    def _read_json(
        self, response: httpx.Response, *, action: str
    ) -> dict[str, object]:
        """Decode a JSON object body, or fail cleanly."""
        try:
            payload = response.json()
        except ValueError:
            # json.JSONDecodeError is a ValueError; catching the base covers a
            # future client that raises a different subclass.
            self._log_jira_error(action, response)
            raise JiraApiError(
                "Jira returned a response that could not be read."
            ) from None

        if not isinstance(payload, dict):
            self._log_jira_error(action, response)
            raise JiraApiError("Jira returned a response that could not be read.")

        return payload

    def _log_request(self, what: str) -> None:
        """Announce a Jira round trip and count it."""
        self._api_calls += 1
        logger.info("Jira: %s", what)

    # ----------------------------------------------------------- error handling

    @staticmethod
    def _log_jira_error(action: str, response: httpx.Response) -> None:
        """Record Jira's own wording server-side only.

        Jira error bodies do not contain the token - it travels in a request
        header, which is never logged - but they do carry account and instance
        detail we do not want in a client response. So it is logged here and the
        client gets one of our fixed messages instead.
        """
        logger.warning(
            "Jira API error while %s: status=%s detail=%s",
            action,
            response.status_code,
            response.text[:ERROR_BODY_LOG_CHARS],
        )

    @staticmethod
    def _log_transport_error(action: str, exc: httpx.RequestError) -> None:
        """Record a network failure server-side only."""
        logger.warning(
            "Jira request failed while %s: %s", action, type(exc).__name__
        )

    def _translate(self, response: httpx.Response, *, action: str) -> IngestionError:
        """Map a Jira response status onto the error the client should see.

        No body sniffing, unlike the GitHub connector. Jira Cloud separates
        "these credentials are wrong" from "this account may not do that"
        cleanly, so the status alone is enough.
        """
        self._log_jira_error(action, response)
        status = response.status_code

        if status == httpx.codes.UNAUTHORIZED:
            return JiraAuthenticationError()
        if status == httpx.codes.FORBIDDEN:
            return JiraPermissionError()
        if status == httpx.codes.NOT_FOUND:
            return JiraNotFoundError()
        if status == httpx.codes.TOO_MANY_REQUESTS:
            return JiraRateLimitError()

        return JiraApiError()
