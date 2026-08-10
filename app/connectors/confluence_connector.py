"""The Confluence Cloud connector - the only module that talks to Confluence.

    resolve cloud id  ->  resolve space  ->  read pages
    GET /_edge/tenant_info   GET /spaces?keys=X   GET /pages?space-id=...

This is the only module that knows Confluence's endpoints, and the only
Confluence module that imports httpx. It hands back raw page JSON exactly as it
came off the wire: no storage-format parsing, no chunk building. Those live
downstream, where they can be tested without a fake HTTP server.

Requests go through Atlassian's gateway at api.atlassian.com rather than straight
at the site, because that is the only address a *scoped* API token can
authenticate against - pointing one at the site's own hostname returns 401 no
matter how correct it is. The gateway needs the site's cloud id in the path, so
a run begins by reading it from the site's public tenant_info endpoint. That
lookup is the one request here that carries no credential, and it uses a separate
unauthenticated client so it cannot accidentally acquire one.

The space lookup is the load-bearing call. Confluence's page endpoint will
happily return every page on a site, so the key the caller gave is resolved to a
space id first and every page request is filtered by it. That is the difference
between "ask Confluence for TrackIt's pages" and "ask for everything and hope
the filter downstream is right", and only the first one is safe. There is a test
whose entire job is to prove the page request carries the resolved id.

Unlike the Jira connector there is no separate credential check before it, and
the reason is worth writing down because it was measured rather than assumed.

Jira needs one because a bad token and an invisible project both surface as the
same confusing 400 from its search endpoint, and `GET /myself` answers a bad
credential with a clean 401. The Confluence gateway does not behave that way. It
answers 404 for the *whole* /ex/confluence/{cloud id} prefix whenever it will
not accept the credential - a wrong token, an absent one and a token with no
access to the site all produce the same 404, and 401 never appears at all.
Confluence's own v1 user endpoint answers 403 rather than 401 for the same
input, so adding a call there would buy an extra round trip and still not a
clean signal.

So the discrimination is made here instead, and it is a sharper one than a
status check: a space that simply does not exist comes back as 200 with an empty
result set, because /spaces is a filtered collection. That makes a 404 at the
space lookup mean "the gateway would not talk to us" and an empty result mean
"no such space, or not yours" - which is exactly the split the caller needs, at
no extra cost.

The API token reaches exactly one expression - the httpx auth tuple. It is never
assigned to an attribute, never logged, and never folded into an error message.
Request headers are not logged at all, since that is where the credential rides.

Log volume tracks batches, not pages: one line per API call, with per-page detail
at DEBUG and page bodies never logged at all.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    ConfluenceApiError,
    ConfluenceAuthenticationError,
    ConfluenceNotFoundError,
    ConfluencePermissionError,
    ConfluenceRateLimitError,
    IngestionError,
)

logger = logging.getLogger(__name__)

# One raw page exactly as the REST API returned it.
ConfluencePageJson = dict[str, object]

# Public site metadata: returns {"cloudId": "..."} and needs no credential.
TENANT_INFO_PATH = "/_edge/tenant_info"

# Every authenticated call is made against the gateway, with the site's cloud id
# standing in for its hostname. Confluence lives under /wiki on the site, and
# that stays true through the gateway, so the v2 paths below keep the prefix.
GATEWAY_BASE_URL = "https://api.atlassian.com/ex/confluence/{cloud_id}"

SPACES_PATH = "/wiki/api/v2/spaces"
PAGES_PATH = "/wiki/api/v2/pages"

# Confluence returns current, draft, archived and trashed pages. This pipeline
# ingests what the space says *now*, so it asks for current only - it is a
# knowledge base, not an archive.
CURRENT_STATUS = "current"

# The body representation asked for. Storage is Confluence's own XHTML-like
# markup; confluence_storage.py is what turns it back into prose.
STORAGE_BODY_FORMAT = "storage"

# Confluence's own maximum for the v2 collection endpoints.
DEFAULT_PAGE_SIZE = 100

# Enough room for a keyed space lookup to be unambiguous without inviting a
# server that ignores the filter to return a site's entire space list.
SPACE_LOOKUP_LIMIT = 25

# A stop for a server that never stops offering a next cursor. At the default
# page size this is 10,000 pages - an order of magnitude past
# MAX_PAGES_PER_INGESTION, so in practice only a misbehaving server reaches it.
DEFAULT_MAX_BATCHES = 100

DEFAULT_TIMEOUT = 30

# Confluence error bodies are usually small JSON, but a proxy or an SSO wall can
# return a multi-kilobyte HTML page. Only the head of it is worth a log line.
ERROR_BODY_LOG_CHARS = 500


@dataclass
class ConfluenceSpace:
    """The space a run resolved, and everything downstream needs from it."""

    space_id: str
    space_key: str
    space_name: str | None = None


@dataclass
class ConfluenceSnapshot:
    """Everything one connector run retrieved.

    `truncated` means the run stopped before the space ran out of pages - the
    page cap was reached, or pagination hit a guard. It has nothing to do with
    how much of this the HTTP response later serialises.

    Deliberately not connectors.base.SourceSnapshot: a space has no branch and
    no commit, and filling those with placeholder values to reuse a dataclass
    would make the model lie about what it holds.
    """

    site_url: str

    space_id: str
    space_key: str
    space_name: str | None = None

    retrieved_pages: int = 0
    truncated: bool = False

    pages: list[ConfluencePageJson] = field(default_factory=list)

    # (page id or space key, reason) for anything skipped or degraded. Never
    # fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)


class ConfluenceConnector:
    """Fetches the pages of one space from a Confluence Cloud site."""

    def __init__(
        self,
        site_url: str,
        email: str,
        api_token: SecretStr,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_batches: int = DEFAULT_MAX_BATCHES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.site_url = site_url.rstrip("/")
        self.page_size = page_size
        self.max_batches = max_batches

        # Authenticated Confluence API calls, which are what a rate limit
        # counts. Reported at the end of a run so a later 429 is possible to
        # reason about. The public tenant lookup is not one of these and is
        # logged on its own line instead.
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

    def __enter__(self) -> "ConfluenceConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------------------------------------------------------- public

    def get_pages(
        self, space_key: str, *, max_pages: int | None = None
    ) -> ConfluenceSnapshot:
        """Fetch every current page in one space.

        There is deliberately no `cql` parameter and no way to ask for another
        space's pages mid-run. The space is resolved once, from the key alone,
        and its id is what every page request is filtered by.
        """
        self._resolve_cloud_id()
        space = self._resolve_space(space_key)

        return self._read_all_pages(space, max_pages=max_pages)

    # --------------------------------------------------------------- private

    def _resolve_cloud_id(self) -> None:
        """Look up the site's cloud id and point the API client at the gateway.

        Unauthenticated, because tenant_info is public - and deliberately so:
        a scoped token cannot authenticate anywhere until this has run, so
        sending one here would be both useless and needless exposure.
        """
        action = "resolving the Confluence site"
        logger.info("Confluence: %s", action)

        try:
            response = self._public_client.get(TENANT_INFO_PATH)
        except httpx.TimeoutException as exc:
            self._log_transport_error(action, exc)
            raise ConfluenceApiError("Confluence did not respond in time.") from exc
        except httpx.RequestError as exc:
            self._log_transport_error(action, exc)
            raise ConfluenceApiError() from exc

        if response.status_code != httpx.codes.OK:
            self._log_confluence_error(action, response)
            raise ConfluenceNotFoundError(
                "No Atlassian site was found at that URL."
            )

        cloud_id = self._read_json(response, action=action).get("cloudId")
        if not isinstance(cloud_id, str) or not cloud_id:
            self._log_confluence_error(action, response)
            raise ConfluenceApiError(
                "Atlassian did not report a cloud id for this site."
            )

        self._cloud_id = cloud_id
        self._client.base_url = GATEWAY_BASE_URL.format(cloud_id=cloud_id)
        logger.info("Resolved Atlassian cloud id for %s", self.site_url)

    def _resolve_space(self, space_key: str) -> ConfluenceSpace:
        """Turn the caller's space key into the space id pages are filtered by.

        The key filter is applied by Confluence, not here. Listing every space
        and picking one locally would work on a small site and quietly become a
        multi-page walk on a large one, and it would mean this connector had
        held every space a token can see in memory for no reason.
        """
        action = f"resolving space {space_key}"
        logger.info("Confluence: %s", action)

        response = self._request(
            "GET",
            SPACES_PATH,
            params={"keys": space_key, "limit": SPACE_LOOKUP_LIMIT},
            action=action,
        )

        if response.status_code == httpx.codes.NOT_FOUND:
            # Not "no such space" - /spaces is a filtered collection and answers
            # that with 200 and an empty list. A 404 here is the gateway
            # refusing the credential for this site, which it does instead of
            # ever returning 401. See the module docstring.
            self._log_confluence_error(action, response)
            raise ConfluenceAuthenticationError(
                "Confluence did not accept these credentials for that site. "
                "Check the email address, that the API token is valid, and "
                "that the account can reach this site's Confluence."
            )
        if response.status_code != httpx.codes.OK:
            raise self._translate(response, action=action)

        payload = self._read_json(response, action=action)
        results = payload.get("results")
        results = results if isinstance(results, list) else []

        space = self._select_space(results, space_key)
        if space is None:
            # Confluence omits spaces an account may not read rather than
            # returning 403, so "no such space" and "not yours" are one case
            # here - and the message says so without naming which.
            logger.info("Confluence returned no space matching %s", space_key)
            raise ConfluenceNotFoundError(
                f"Confluence space '{space_key}' does not exist or is not "
                "accessible."
            )

        logger.info(
            "Resolved Confluence space %s (%s)", space.space_key, space.space_id
        )
        return space

    @staticmethod
    def _select_space(
        results: list[object], space_key: str
    ) -> ConfluenceSpace | None:
        """Pick the one space whose key is exactly the one that was asked for.

        A keyed lookup should return one space, but Confluence also matches a
        space's previous keys through its alias mechanism, so more than one
        result is possible. Taking results[0] would make which space a run
        ingests depend on Atlassian's ordering; requiring an exact match makes
        it depend on the key the caller typed.

        The comparison is case-insensitive because Confluence normalises keys to
        upper case, and "tr" is a reasonable thing for a person to type.
        """
        wanted = space_key.casefold()

        for entry in results:
            if not isinstance(entry, dict):
                continue

            key = entry.get("key")
            if not isinstance(key, str) or key.casefold() != wanted:
                continue

            space_id = entry.get("id")
            if isinstance(space_id, int) and not isinstance(space_id, bool):
                space_id = str(space_id)
            if not isinstance(space_id, str) or not space_id:
                # A space we cannot address by id is no use: it is the id that
                # every page request is filtered by.
                continue

            name = entry.get("name")
            return ConfluenceSpace(
                space_id=space_id,
                space_key=key,
                space_name=name if isinstance(name, str) and name else None,
            )

        return None

    def _read_all_pages(
        self, space: ConfluenceSpace, *, max_pages: int | None
    ) -> ConfluenceSnapshot:
        """Walk every batch of pages in the space, up to the page cap."""
        logger.info("Confluence: reading pages in %s", space.space_key)

        pages: list[ConfluencePageJson] = []
        errors: list[tuple[str, str]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        truncated = False
        batch = 0

        while True:
            batch += 1
            if batch > self.max_batches:
                logger.warning(
                    "Stopping after %d batches; Confluence never ran out of "
                    "pages",
                    self.max_batches,
                )
                errors.append(
                    (space.space_key, "Ingestion stopped at the batch ceiling.")
                )
                truncated = True
                break

            # Ask for only what is still wanted. The cap has to prevent the
            # fetch, not trim the result afterwards - otherwise a cap of 1 on a
            # large space still costs a full batch.
            remaining = None if max_pages is None else max_pages - len(pages)
            limit = (
                self.page_size if remaining is None else min(self.page_size, remaining)
            )

            payload = self._read_page_batch(space, cursor=cursor, limit=limit)

            results = payload.get("results")
            results = results if isinstance(results, list) else []
            pages.extend(results)
            logger.info(
                "Confluence page batch %d returned %d pages", batch, len(results)
            )
            for raw in results:
                if isinstance(raw, dict):
                    logger.debug("Retrieved page %s", raw.get("id"))

            next_cursor = self._next_cursor(payload)

            # The clean ending. Reached before the cap check on purpose: a cap
            # that happens to land on the final page is not truncation.
            if next_cursor is None:
                break

            if max_pages is not None and len(pages) >= max_pages:
                logger.warning(
                    "Limiting ingestion to %d pages; space %s has more",
                    max_pages,
                    space.space_key,
                )
                errors.append(
                    (
                        space.space_key,
                        f"Ingestion stopped at the {max_pages}-page cap; the "
                        "space contains more.",
                    )
                )
                truncated = True
                break

            if not results:
                # A batch with nothing in it, promising another one, would spin
                # forever.
                logger.warning(
                    "Confluence returned an empty batch while promising more"
                )
                truncated = True
                break

            if next_cursor in seen_cursors:
                logger.warning(
                    "Confluence repeated a cursor; stopping to avoid a loop"
                )
                truncated = True
                break

            seen_cursors.add(next_cursor)
            cursor = next_cursor

        # Confluence honours limit, but a proxy in front of it might not.
        if max_pages is not None and len(pages) > max_pages:
            del pages[max_pages:]

        logger.info(
            "Retrieved %d Confluence pages from %d batch(es) (%d Confluence API "
            "calls)",
            len(pages),
            batch,
            self._api_calls,
        )

        return ConfluenceSnapshot(
            site_url=self.site_url,
            space_id=space.space_id,
            space_key=space.space_key,
            space_name=space.space_name,
            retrieved_pages=len(pages),
            truncated=truncated,
            pages=pages,
            errors=errors,
        )

    def _read_page_batch(
        self, space: ConfluenceSpace, *, cursor: str | None, limit: int
    ) -> dict[str, object]:
        """Fetch one batch of pages from the resolved space.

        `space-id` is what confines a run to one space, and it is set from the
        resolved space rather than from anything the caller supplied. Nothing in
        this class can call this endpoint without it.
        """
        action = "reading pages"
        params: dict[str, object] = {
            "space-id": space.space_id,
            "status": CURRENT_STATUS,
            "body-format": STORAGE_BODY_FORMAT,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor

        response = self._request("GET", PAGES_PATH, params=params, action=action)

        if response.status_code == httpx.codes.BAD_REQUEST:
            # A 400 here means the request this module built is wrong, which is
            # our bug rather than the caller's - so it is logged loudly and
            # reported as an upstream failure rather than a validation error.
            self._log_confluence_error(action, response)
            raise ConfluenceApiError("Confluence rejected the page request.")
        if response.status_code != httpx.codes.OK:
            raise self._translate(response, action=action)

        return self._read_json(response, action=action)

    @staticmethod
    def _next_cursor(payload: dict[str, object]) -> str | None:
        """Read the cursor for the next batch out of _links.next.

        Confluence answers with a relative URL rather than a bare cursor, so the
        cursor is extracted and re-sent against our own path and our own
        parameters. Following that URL directly would be shorter and would also
        mean an upstream response could redirect a run at another endpoint,
        drop the space-id filter, or raise the limit past the cap - so it is
        parsed for the one value it is trusted to carry and nothing else.

        Anything unparseable simply ends the walk. A malformed next link is not
        worth failing a run that has already retrieved its pages.
        """
        links = payload.get("_links")
        if not isinstance(links, dict):
            return None

        nxt = links.get("next")
        if not isinstance(nxt, str) or not nxt:
            return None

        cursors = parse_qs(urlsplit(nxt).query).get("cursor")
        if not cursors:
            return None

        cursor = cursors[0]
        return cursor or None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        action: str,
    ) -> httpx.Response:
        """Make one Confluence round trip, turning transport failures into ours."""
        self._log_request(action)
        try:
            return self._client.request(method, path, params=params)
        except httpx.TimeoutException as exc:
            # Must come first: TimeoutException is a subclass of RequestError,
            # so the other order would silently lose the timeout wording.
            self._log_transport_error(action, exc)
            raise ConfluenceApiError("Confluence did not respond in time.") from exc
        except httpx.RequestError as exc:
            self._log_transport_error(action, exc)
            raise ConfluenceApiError() from exc

    def _read_json(
        self, response: httpx.Response, *, action: str
    ) -> dict[str, object]:
        """Decode a JSON object body, or fail cleanly."""
        try:
            payload = response.json()
        except ValueError:
            # json.JSONDecodeError is a ValueError; catching the base covers a
            # future client that raises a different subclass.
            self._log_confluence_error(action, response)
            raise ConfluenceApiError(
                "Confluence returned a response that could not be read."
            ) from None

        if not isinstance(payload, dict):
            self._log_confluence_error(action, response)
            raise ConfluenceApiError(
                "Confluence returned a response that could not be read."
            )

        return payload

    def _log_request(self, what: str) -> None:
        """Announce a Confluence round trip and count it."""
        self._api_calls += 1
        logger.info("Confluence: %s", what)

    # ----------------------------------------------------------- error handling

    @staticmethod
    def _log_confluence_error(action: str, response: httpx.Response) -> None:
        """Record Confluence's own wording server-side only.

        Confluence error bodies do not contain the token - it travels in a
        request header, which is never logged - but they do carry account and
        instance detail we do not want in a client response. So it is logged
        here and the client gets one of our fixed messages instead.
        """
        logger.warning(
            "Confluence API error while %s: status=%s detail=%s",
            action,
            response.status_code,
            response.text[:ERROR_BODY_LOG_CHARS],
        )

    @staticmethod
    def _log_transport_error(action: str, exc: httpx.RequestError) -> None:
        """Record a network failure server-side only."""
        logger.warning(
            "Confluence request failed while %s: %s", action, type(exc).__name__
        )

    def _translate(self, response: httpx.Response, *, action: str) -> IngestionError:
        """Map a Confluence response status onto the error the client should see.

        No body sniffing, unlike the GitHub connector. Confluence Cloud
        separates "these credentials are wrong" from "this account may not do
        that" cleanly, so the status alone is enough.
        """
        self._log_confluence_error(action, response)
        status = response.status_code

        if status == httpx.codes.UNAUTHORIZED:
            return ConfluenceAuthenticationError()
        if status == httpx.codes.FORBIDDEN:
            return ConfluencePermissionError()
        if status == httpx.codes.NOT_FOUND:
            return ConfluenceNotFoundError()
        if status == httpx.codes.TOO_MANY_REQUESTS:
            return ConfluenceRateLimitError()

        return ConfluenceApiError()
