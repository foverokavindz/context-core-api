"""The Slack connector - the only module that talks to Slack.

    read one channel's history  ->  follow the cursor  ->  raw message JSON[]
    GET /api/conversations.history?channel=C0123456789

This is the only module that knows Slack's endpoints, and the only Slack module
that imports httpx. It hands back raw message JSON exactly as it came off the
wire: no filtering of thread replies or channel events, no text normalisation,
no chunk building. Those live downstream, where they can be tested without a
fake HTTP server.

`conversations.history` is the only endpoint this connector ever calls, and one
channel id is the only conversation it ever names. There is no channel discovery,
no `conversations.list`, no `conversations.info`, and - the one worth stating
outright - no `conversations.replies`. A thread reply that Slack chose to echo
into channel history is dropped downstream by the parser rather than followed up
with a second request, so no run can fan out from one channel into its threads.

There is deliberately no credential pre-check, which is where this differs from
the Jira connector and its `/myself` call. Slack reports `invalid_auth` on the
history call itself, just as clearly and one round trip earlier, so adding a
pre-flight `auth.test` would buy nothing and would make it untrue that this
pipeline touches exactly one Slack endpoint.

Two things about Slack's error reporting shape this module, and neither has a
counterpart in the other three connectors:

  Failure arrives with a 200. `{"ok": false, "error": "channel_not_found"}` is
  what a missing channel looks like, so a status check alone would read every
  such response as success and hand a parser an empty message list. Every
  response therefore goes through _read_payload, which checks the status *and*
  the `ok` field before anything else may look at the body.

  A rate limit does arrive as a real 429, with a Retry-After header. That header
  is logged and never obeyed: sleeping until a Slack cooldown expires would hold
  a synchronous HTTP request open for the length of it. The caller gets a 429 in
  well under a second and decides for itself.

The token reaches exactly one expression - the Authorization header built in the
constructor. It is never assigned to an attribute, never logged, and never folded
into an error message. Request headers are not logged at all, since that is where
the credential rides.

Log volume tracks pages, not messages: one line per API call, with per-message
detail at DEBUG and message text never logged at all, at any level. A channel's
conversation is the most sensitive thing any of these four pipelines reads.
"""

import logging
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    IngestionError,
    SlackApiError,
    SlackAuthenticationError,
    SlackNotFoundError,
    SlackPermissionError,
    SlackRateLimitError,
)

logger = logging.getLogger(__name__)

# One raw history item exactly as the Web API returned it.
SlackMessageJson = dict[str, object]

# Slack's Web API has one host and one prefix for every method, so there is no
# site to resolve and no gateway to route through - the whole cloud-id dance the
# two Atlassian connectors have to do simply does not arise here.
SLACK_API_BASE_URL = "https://slack.com/api"

CONVERSATIONS_HISTORY_PATH = "/conversations.history"

# Slack accepts up to 1000 per call but documents 200 as the recommended
# maximum, and a larger page is more to lose when a request fails partway.
DEFAULT_PAGE_SIZE = 200

# A stop for a server that never stops offering a next cursor. At the default
# page size this is 20,000 messages - two orders of magnitude past
# MAX_MESSAGES_PER_INGESTION, so in practice only a misbehaving server reaches
# it.
DEFAULT_MAX_PAGES = 100

DEFAULT_TIMEOUT = 30

# Slack error bodies are small JSON, but a proxy or a captive portal in front of
# it can return a multi-kilobyte HTML page. Only the head of it is worth a log
# line.
ERROR_BODY_LOG_CHARS = 500

# Slack's error strings, mapped to what the client should be told.
#
# A table rather than an if-chain because the interesting content here *is* the
# table: which vendor string means "your token is wrong" and which means "your
# token is fine but you were never invited" is the whole of the decision, and it
# reads better as data than as control flow.
#
# `channel_not_found` covers both a channel that does not exist and one this
# token cannot see - Slack does not distinguish them, and neither do we.
#
# Anything absent from this table becomes SlackApiError. That includes error
# strings Slack has not invented yet, and it is the right default: an unknown
# failure reported as an upstream problem sends an operator to look at Slack,
# whereas one reported as an authentication failure would send them to reissue a
# token that was never the problem.
SLACK_ERROR_MAP: dict[str, type[IngestionError]] = {
    "invalid_auth": SlackAuthenticationError,
    "not_authed": SlackAuthenticationError,
    "no_auth": SlackAuthenticationError,
    "token_revoked": SlackAuthenticationError,
    "token_expired": SlackAuthenticationError,
    "invalid_token": SlackAuthenticationError,
    "account_inactive": SlackAuthenticationError,
    "missing_scope": SlackPermissionError,
    "not_allowed_token_type": SlackPermissionError,
    "no_permission": SlackPermissionError,
    "not_in_channel": SlackPermissionError,
    "access_denied": SlackPermissionError,
    "channel_not_found": SlackNotFoundError,
    "ratelimited": SlackRateLimitError,
    "rate_limited": SlackRateLimitError,
}


@dataclass
class SlackSnapshot:
    """Everything one connector run retrieved.

    `truncated` means the run stopped before the channel ran out of history -
    the message cap was reached, or pagination hit a guard. It has nothing to do
    with how much of this the HTTP response later serialises, and nothing to do
    with the parser dropping items afterwards.

    `messages` holds raw history items, not messages in any useful sense: thread
    replies, channel-join notices and everything else Slack keeps in a channel's
    history are all still in here. `retrieved_messages` counts them as they
    arrived, which is what makes it comparable with the parser's own count.

    Deliberately not connectors.base.SourceSnapshot: a channel has no branch and
    no commit, and filling those with placeholder values to reuse a dataclass
    would make the model lie about what it holds.
    """

    channel_id: str

    retrieved_messages: int = 0
    truncated: bool = False

    messages: list[SlackMessageJson] = field(default_factory=list)

    # (message timestamp or channel id, reason) for anything skipped or
    # degraded. Never fatal.
    errors: list[tuple[str, str]] = field(default_factory=list)


class SlackConnector:
    """Fetches the message history of one Slack channel."""

    def __init__(
        self,
        token: SecretStr,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.page_size = page_size
        self.max_pages = max_pages

        # Slack API calls, which are what a rate limit counts. Reported at the
        # end of a run so a later 429 is possible to reason about. Every call
        # this connector makes is authenticated, so unlike the Atlassian
        # connectors there is no second, uncounted client to explain.
        self._api_calls = 0

        # The one place the token is unwrapped. It is not stored on self, so
        # nothing can print it by accident later.
        #
        # Slack takes the token as a bearer credential rather than as HTTP Basic,
        # so it is built into a header here instead of being handed to httpx's
        # `auth=`. That is the only structural difference from the Jira and
        # Confluence clients, and it is why the containment tests check for the
        # word "Bearer" as well as for the secret itself.
        #
        # No retry policy on purpose: httpx does not retry by default, and
        # nothing here should sleep waiting for a rate limit to reset. A 429
        # comes back to the caller in well under a second.
        self._client = httpx.Client(
            base_url=SLACK_API_BASE_URL,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token.get_secret_value()}",
            },
            transport=transport,
        )

    def close(self) -> None:
        """Drop the session.

        The ingestion service uses this connector as a context manager, so the
        client holding the caller's token does not outlive their request.
        """
        self._client.close()

    def __enter__(self) -> "SlackConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------------------------------------------------------- public

    def get_history(
        self, channel_id: str, *, max_messages: int | None = None
    ) -> SlackSnapshot:
        """Fetch one channel's message history, oldest first.

        The channel id is the only conversation this connector will ever name,
        and it is sent on every request in the walk - there is no parameter that
        could widen a run to a second channel, and no lookup that could resolve
        one. Nothing here calls conversations.replies, so a thread cannot pull
        the run outside the channel either.

        Slack serves history newest first. The result is reversed into
        chronological order before it is returned, which means a capped run
        holds the channel's most recent messages presented oldest to newest.
        """
        return self._read_all_history(channel_id, max_messages=max_messages)

    # --------------------------------------------------------------- private

    def _read_all_history(
        self, channel_id: str, *, max_messages: int | None
    ) -> SlackSnapshot:
        """Walk every page of the channel's history, up to the message cap."""
        logger.info("Slack: reading channel history")

        messages: list[SlackMessageJson] = []
        errors: list[tuple[str, str]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        truncated = False
        page = 0

        while True:
            page += 1
            if page > self.max_pages:
                logger.warning(
                    "Stopping after %d pages; Slack never ran out of history",
                    self.max_pages,
                )
                errors.append(
                    (channel_id, "Ingestion stopped at the page ceiling.")
                )
                truncated = True
                break

            # Ask for only what is still wanted. The cap has to prevent the
            # fetch, not trim the result afterwards - otherwise a cap of 1 on a
            # busy channel still costs a full page.
            remaining = None if max_messages is None else max_messages - len(messages)
            limit = (
                self.page_size if remaining is None else min(self.page_size, remaining)
            )

            payload = self._read_history_page(channel_id, cursor=cursor, limit=limit)

            batch = payload.get("messages")
            batch = batch if isinstance(batch, list) else []
            messages.extend(batch)
            logger.info("Slack history page %d returned %d messages", page, len(batch))
            for raw in batch:
                if isinstance(raw, dict):
                    logger.debug("Retrieved message %s", raw.get("ts"))

            next_cursor = self._next_cursor(payload)

            # The clean ending. Reached before the cap check on purpose: a cap
            # that happens to land on the final page is not truncation.
            if next_cursor is None:
                break

            if max_messages is not None and len(messages) >= max_messages:
                logger.warning(
                    "Limiting ingestion to %d messages; channel %s has more",
                    max_messages,
                    channel_id,
                )
                errors.append(
                    (
                        channel_id,
                        f"Ingestion stopped at the {max_messages}-message cap; "
                        "the channel contains more.",
                    )
                )
                truncated = True
                break

            if not batch:
                # A page with nothing on it, promising another one, would spin
                # forever.
                logger.warning("Slack returned an empty page while promising more")
                truncated = True
                break

            if next_cursor in seen_cursors:
                logger.warning("Slack repeated a cursor; stopping to avoid a loop")
                truncated = True
                break

            seen_cursors.add(next_cursor)
            cursor = next_cursor

        # Slack honours limit, but a proxy in front of it might not. Trimming
        # from the end keeps the newest messages, since this list is still in
        # Slack's newest-first order at this point.
        if max_messages is not None and len(messages) > max_messages:
            del messages[max_messages:]

        # Slack serves newest first; everything downstream reads better oldest
        # first, and a stable order is what makes a capped run reproducible.
        messages.sort(key=_ts_sort_key)

        logger.info(
            "Retrieved %d Slack history items from %d page(s) (%d Slack API calls)",
            len(messages),
            page,
            self._api_calls,
        )

        return SlackSnapshot(
            channel_id=channel_id,
            retrieved_messages=len(messages),
            truncated=truncated,
            messages=messages,
            errors=errors,
        )

    def _read_history_page(
        self, channel_id: str, *, cursor: str | None, limit: int
    ) -> dict[str, object]:
        """Fetch one page of the channel's history.

        `channel` is what confines a run to one conversation, and it is set on
        every request rather than only on the first. Nothing in this class can
        call this endpoint without it.
        """
        action = "reading channel history"
        params: dict[str, object] = {
            "channel": channel_id,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor

        response = self._request(
            "GET", CONVERSATIONS_HISTORY_PATH, params=params, action=action
        )

        return self._read_payload(response, action=action)

    @staticmethod
    def _next_cursor(payload: dict[str, object]) -> str | None:
        """Read the cursor for the next page, or None when the walk is over.

        Slack signals "more to come" two ways - a `has_more` boolean and a
        cursor in `response_metadata` - and only the cursor can actually be
        acted on. So the cursor is the authority: an empty or absent one ends
        the walk whatever `has_more` claims, which also means a server that sets
        `has_more` and forgets the cursor stops cleanly instead of looping on a
        page it cannot advance past.
        """
        metadata = payload.get("response_metadata")
        if not isinstance(metadata, dict):
            return None

        cursor = metadata.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            return None

        return cursor

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        action: str,
    ) -> httpx.Response:
        """Make one Slack round trip, turning transport failures into ours."""
        self._log_request(action)
        try:
            return self._client.request(method, path, params=params)
        except httpx.TimeoutException as exc:
            # Must come first: TimeoutException is a subclass of RequestError,
            # so the other order would silently lose the timeout wording.
            self._log_transport_error(action, exc)
            raise SlackApiError("Slack did not respond in time.") from exc
        except httpx.RequestError as exc:
            self._log_transport_error(action, exc)
            raise SlackApiError() from exc

    def _read_payload(
        self, response: httpx.Response, *, action: str
    ) -> dict[str, object]:
        """Decode one Slack response, or raise the error it is reporting.

        Three checks, and the order is load-bearing. The rate limit comes first
        because it is the one failure Slack reports as a real status and the one
        a caller most needs to recognise. The status check comes next, because a
        502 from a proxy has no JSON body to read an `ok` field out of. Only then
        is the body trusted enough to be asked whether it succeeded.
        """
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            self._log_rate_limit(action, response)
            raise SlackRateLimitError()

        if response.status_code != httpx.codes.OK:
            self._log_slack_error(action, response)
            raise SlackApiError()

        payload = self._read_json(response, action=action)

        # The check that makes this connector different from the other three.
        # Slack answers a missing channel, a revoked token and a missing scope
        # all with 200 OK, so a status-only reading of this response would treat
        # every one of them as an empty but successful run.
        if payload.get("ok") is not True:
            raise self._translate(payload, response, action=action)

        return payload

    def _read_json(
        self, response: httpx.Response, *, action: str
    ) -> dict[str, object]:
        """Decode a JSON object body, or fail cleanly."""
        try:
            payload = response.json()
        except ValueError:
            # json.JSONDecodeError is a ValueError; catching the base covers a
            # future client that raises a different subclass.
            self._log_slack_error(action, response)
            raise SlackApiError(
                "Slack returned a response that could not be read."
            ) from None

        if not isinstance(payload, dict):
            self._log_slack_error(action, response)
            raise SlackApiError("Slack returned a response that could not be read.")

        return payload

    def _log_request(self, what: str) -> None:
        """Announce a Slack round trip and count it."""
        self._api_calls += 1
        logger.info("Slack: %s", what)

    # ----------------------------------------------------------- error handling

    @staticmethod
    def _log_slack_error(action: str, response: httpx.Response) -> None:
        """Record Slack's own wording server-side only.

        Slack error bodies do not contain the token - it travels in a request
        header, which is never logged - but they can carry workspace and app
        detail we do not want in a client response. So it is logged here and the
        client gets one of our fixed messages instead.
        """
        logger.warning(
            "Slack API error while %s: status=%s detail=%s",
            action,
            response.status_code,
            response.text[:ERROR_BODY_LOG_CHARS],
        )

    @staticmethod
    def _log_rate_limit(action: str, response: httpx.Response) -> None:
        """Record a throttle, including how long Slack asked us to wait.

        The Retry-After value is worth knowing and is not worth obeying: this
        runs inside a synchronous HTTP request, and Slack's cooldowns are
        measured in tens of seconds. It is logged so an operator can see what
        was asked, and then the 429 goes straight back to the caller.
        """
        logger.warning(
            "Slack rate limited the request while %s; Retry-After: %s "
            "(not waited on)",
            action,
            response.headers.get("retry-after", "not reported"),
        )

    @staticmethod
    def _log_transport_error(action: str, exc: httpx.RequestError) -> None:
        """Record a network failure server-side only."""
        logger.warning("Slack request failed while %s: %s", action, type(exc).__name__)

    def _translate(
        self, payload: dict[str, object], response: httpx.Response, *, action: str
    ) -> IngestionError:
        """Map a Slack `ok: false` body onto the error the client should see.

        Keyed on Slack's error string rather than on a status code, because at
        this point the status is 200 and carries no information at all. That is
        the inverse of what the Jira and Confluence connectors do, and the
        reason they can get away with a pure status mapping.

        The string itself is never returned to the client - only logged - since
        `not_in_channel` and `missing_scope` describe the workspace's
        configuration rather than the caller's request.
        """
        self._log_slack_error(action, response)

        error = payload.get("error")
        if not isinstance(error, str):
            return SlackApiError()

        return SLACK_ERROR_MAP.get(error, SlackApiError)()


def _ts_sort_key(raw: SlackMessageJson) -> float:
    """Order one history item chronologically.

    Parsed as a float rather than compared as a string, because Slack
    timestamps are unpadded seconds: "999.000100" sorts after "1000.000100"
    lexicographically and before it numerically, and the numeric answer is the
    correct one.

    An unparseable timestamp sorts to the front rather than raising. One
    malformed item should not cost a run its ordering, and the parser will drop
    it a moment later anyway - a message with no usable timestamp cannot be
    identified.
    """
    if not isinstance(raw, dict):
        return 0.0

    ts = raw.get("ts")
    if not isinstance(ts, str):
        return 0.0

    try:
        return float(ts)
    except ValueError:
        return 0.0
