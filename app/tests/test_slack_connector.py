"""Tests for the Slack connector, with Slack replaced by a transport.

No test here needs a real Slack workspace, a real token or a network call. The
fake records every request it served, which is what lets us assert the two
properties this connector exists to guarantee: that every request names the
channel the caller asked for, and that conversations.history is the only Slack
method ever called - so a run can reach neither another channel nor a thread.

Using httpx.MockTransport rather than patching the client out means these tests
run through the real httpx.Client - real base URL joining, real query-string
encoding, a real Authorization header.

The section this file has and the other connectors' do not is `ok is false`.
Slack reports a missing channel, a revoked token and a missing scope all with
HTTP 200, so those cases cannot be driven by a status code and are easy to
regress into silent empty runs.
"""

import time

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.slack_connector import (
    CONVERSATIONS_HISTORY_PATH,
    SlackConnector,
)
from app.core.exceptions import (
    SlackApiError,
    SlackAuthenticationError,
    SlackNotFoundError,
    SlackPermissionError,
    SlackRateLimitError,
)

FAKE_TOKEN = SecretStr("xoxb-slack-fake-token-for-tests-only")
CHANNEL = "C0123456789"
OTHER_CHANNEL = "C9999999999"

HISTORY_PATH = "/api/conversations.history"


# ------------------------------------------------------------------- fakes


def make_message(
    ts: str, *, text: str = "Ship it.", user: str | None = "U0000000001"
) -> dict:
    """One raw history item, shaped as conversations.history returns it."""
    raw: dict = {"type": "message", "ts": ts, "text": text}
    if user is not None:
        raw["user"] = user
    return raw


class FakeSlack:
    """A Slack API that serves conversations.history and records every call.

    Pages are handed out by integer offset, and the cursor is that offset as a
    string - so a test can assert exactly which cursor was echoed back.

    Slack serves history newest first, so `messages` is served in reverse: a
    test that passes them in chronological order gets them back the way the real
    API would send them, which is what makes the ordering tests meaningful.
    """

    def __init__(
        self,
        *,
        messages: list[dict] | None = None,
        history_status: int = 200,
        history_body: object = None,
        history_text: str | None = None,
        headers: dict[str, str] | None = None,
        ok: bool = True,
        error: str = "channel_not_found",
        raises: Exception | None = None,
    ) -> None:
        self.messages = messages if messages is not None else []
        self.history_status = history_status
        self.history_body = history_body
        self.history_text = history_text
        self.headers = headers
        self.ok = ok
        self.error = error
        self.raises = raises

        # Call recorders - the point of the fake.
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises

        if request.url.path == HISTORY_PATH:
            if self.history_text is not None:
                return httpx.Response(
                    self.history_status,
                    text=self.history_text,
                    headers=self.headers,
                )
            if self.history_status != 200:
                return httpx.Response(
                    self.history_status,
                    json={"ok": False, "error": "internal_error"},
                    headers=self.headers,
                )
            if self.history_body is not None:
                return httpx.Response(200, json=self.history_body)
            if not self.ok:
                return httpx.Response(200, json={"ok": False, "error": self.error})
            return httpx.Response(200, json=self._page(request))

        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    def _page(self, request: httpx.Request) -> dict:
        """One page of history, newest first, cursored by offset."""
        newest_first = list(reversed(self.messages))

        limit = int(request.url.params.get("limit", 200))
        start = int(request.url.params.get("cursor", 0))
        following = start + limit

        payload: dict = {"ok": True, "messages": newest_first[start:following]}
        if following < len(newest_first):
            payload["has_more"] = True
            payload["response_metadata"] = {"next_cursor": str(following)}
        return payload

    @property
    def paths(self) -> list[str]:
        return [str(request.url.path) for request in self.requests]

    @property
    def history_requests(self) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if str(request.url.path) == HISTORY_PATH
        ]


def build_connector(fake: FakeSlack, **kwargs) -> SlackConnector:
    return SlackConnector(
        FAKE_TOKEN, transport=httpx.MockTransport(fake.handler), **kwargs
    )


def get_history(fake: FakeSlack, **kwargs):
    """Run one ingestion against the fake.

    Connector-construction knobs are split out of the call arguments, because
    page_size and max_pages configure the connector while max_messages belongs
    to the call.
    """
    connector_kwargs = {
        key: kwargs.pop(key) for key in ("page_size", "max_pages") if key in kwargs
    }
    return build_connector(fake, **connector_kwargs).get_history(CHANNEL, **kwargs)


# --------------------------------------------------------------- happy path


def test_a_channel_with_messages_comes_back() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    snapshot = get_history(fake)

    assert snapshot.channel_id == CHANNEL
    assert snapshot.retrieved_messages == 1
    assert snapshot.truncated is False
    assert snapshot.errors == []


def test_an_empty_channel_is_not_an_error() -> None:
    snapshot = get_history(FakeSlack())

    assert snapshot.retrieved_messages == 0
    assert snapshot.messages == []
    assert snapshot.truncated is False


def test_raw_payloads_are_handed_back_untouched() -> None:
    """The connector filters nothing; that is the parser's job."""
    raw = {
        "type": "message",
        "subtype": "channel_join",
        "ts": "1000.000100",
        "text": "<@U1> has joined the channel",
        "reactions": [{"name": "tada", "count": 3}],
    }
    fake = FakeSlack(messages=[raw])

    snapshot = get_history(fake)

    assert snapshot.messages == [raw]


# ------------------------------------------------------- one channel, one method


def test_every_request_names_the_selected_channel() -> None:
    """The test this connector exists for: a run cannot leave its channel."""
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    assert len(fake.history_requests) == 3
    for request in fake.history_requests:
        assert request.url.params["channel"] == CHANNEL


def test_no_request_is_ever_made_for_another_channel() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    for request in fake.requests:
        assert OTHER_CHANNEL not in str(request.url)


def test_conversations_history_is_the_only_method_called() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    assert set(fake.paths) == {HISTORY_PATH}


def test_conversations_replies_is_never_called() -> None:
    """A thread reply in history must not trigger a follow-up request.

    The parser drops it later; the point here is that the connector does not go
    looking for the rest of the thread in the meantime.
    """
    reply = make_message("1000.000200")
    reply["thread_ts"] = "1000.000100"
    fake = FakeSlack(messages=[make_message("1000.000100"), reply])

    get_history(fake)

    for path in fake.paths:
        assert "replies" not in path


def test_the_request_goes_to_slacks_api_host() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    get_history(fake)

    assert str(fake.requests[0].url).startswith(
        "https://slack.com/api/conversations.history"
    )


def test_the_endpoint_constant_matches_the_path_requested() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    get_history(fake)

    assert fake.paths == ["/api" + CONVERSATIONS_HISTORY_PATH]


# ---------------------------------------------------------------- the token


def test_the_token_is_sent_as_a_bearer_credential() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    get_history(fake)

    assert fake.requests[0].headers["authorization"] == (
        f"Bearer {FAKE_TOKEN.get_secret_value()}"
    )


def test_the_token_is_sent_on_every_page() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    for request in fake.history_requests:
        assert request.headers["authorization"].startswith("Bearer ")


def test_the_token_is_never_sent_as_a_query_parameter() -> None:
    """Slack once accepted this. A URL reaches logs and proxies; a header does not."""
    fake = FakeSlack(messages=[make_message("1000.000100")])

    get_history(fake)

    assert "token" not in fake.requests[0].url.params
    assert FAKE_TOKEN.get_secret_value() not in str(fake.requests[0].url)


# --------------------------------------------------------------- pagination


def test_every_page_is_walked() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    snapshot = get_history(fake, page_size=2)

    assert snapshot.retrieved_messages == 5
    assert len(fake.history_requests) == 3


def test_the_first_page_carries_no_cursor() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    assert "cursor" not in fake.history_requests[0].url.params


def test_the_next_cursor_is_sent_on_the_following_page() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    get_history(fake, page_size=2)

    assert fake.history_requests[1].url.params["cursor"] == "2"
    assert fake.history_requests[2].url.params["cursor"] == "4"


def test_the_page_size_is_sent_as_the_limit() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    get_history(fake, page_size=25)

    assert fake.history_requests[0].url.params["limit"] == "25"


def test_a_single_page_makes_one_request() -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(3)])

    get_history(fake, page_size=100)

    assert len(fake.history_requests) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"next_cursor": ""},
        {"next_cursor": None},
        {"next_cursor": 42},
        "not a dict",
    ],
)
def test_a_missing_or_unusable_cursor_ends_the_walk(metadata: object) -> None:
    body: dict = {"ok": True, "messages": [make_message("1000.000100")]}
    if metadata is not None:
        body["response_metadata"] = metadata
    body["has_more"] = True

    fake = FakeSlack(history_body=body)

    snapshot = get_history(fake)

    assert len(fake.history_requests) == 1
    assert snapshot.retrieved_messages == 1


def test_has_more_without_a_cursor_does_not_loop() -> None:
    """The cursor is the authority; has_more alone cannot be acted on."""
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [make_message("1000.000100")],
            "has_more": True,
        }
    )

    get_history(fake)

    assert len(fake.history_requests) == 1


def test_a_repeated_cursor_stops_the_walk() -> None:
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [make_message("1000.000100")],
            "response_metadata": {"next_cursor": "always-the-same"},
        }
    )

    snapshot = get_history(fake)

    assert len(fake.history_requests) == 2
    assert snapshot.truncated is True


def test_an_empty_page_promising_more_stops_the_walk() -> None:
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [],
            "response_metadata": {"next_cursor": "onwards"},
        }
    )

    snapshot = get_history(fake)

    assert len(fake.history_requests) == 1
    assert snapshot.truncated is True


def test_the_page_ceiling_stops_a_server_that_never_finishes() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [make_message(f"100{counter['n']}.000100")],
                "response_metadata": {"next_cursor": str(counter["n"])},
            },
        )

    connector = SlackConnector(
        FAKE_TOKEN, max_pages=4, transport=httpx.MockTransport(handler)
    )

    snapshot = connector.get_history(CHANNEL)

    assert counter["n"] == 4
    assert snapshot.truncated is True
    assert snapshot.errors == [(CHANNEL, "Ingestion stopped at the page ceiling.")]


# -------------------------------------------------------------- the message cap


def test_the_cap_stops_the_run_and_marks_it_truncated() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(10)])

    snapshot = get_history(fake, page_size=2, max_messages=4)

    assert snapshot.retrieved_messages == 4
    assert snapshot.truncated is True


def test_the_cap_shrinks_the_request_rather_than_trimming_the_answer() -> None:
    """A cap of 3 must not cost a full page of messages we then throw away."""
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(50)])

    get_history(fake, page_size=20, max_messages=3)

    assert fake.history_requests[0].url.params["limit"] == "3"


def test_the_last_page_of_a_page_size_run_asks_only_for_what_is_left() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(50)])

    get_history(fake, page_size=4, max_messages=6)

    assert fake.history_requests[0].url.params["limit"] == "4"
    assert fake.history_requests[1].url.params["limit"] == "2"


def test_a_cap_that_lands_on_the_last_page_is_not_truncation() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(4)])

    snapshot = get_history(fake, page_size=2, max_messages=4)

    assert snapshot.retrieved_messages == 4
    assert snapshot.truncated is False


def test_a_cap_above_the_channel_size_is_not_truncation() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(3)])

    snapshot = get_history(fake, max_messages=100)

    assert snapshot.truncated is False


def test_no_cap_retrieves_everything() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(7)])

    snapshot = get_history(fake, page_size=2)

    assert snapshot.retrieved_messages == 7
    assert snapshot.truncated is False


def test_a_server_ignoring_the_limit_is_trimmed_anyway() -> None:
    """Slack honours limit; a proxy in front of it might not."""
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [make_message(f"10{n:02d}.000100") for n in range(20)],
        }
    )

    snapshot = get_history(fake, max_messages=5)

    assert snapshot.retrieved_messages == 5


def test_the_cap_keeps_the_most_recent_messages() -> None:
    """Slack serves newest first, so a capped run is the recent end of history."""
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(10)])

    snapshot = get_history(fake, page_size=20, max_messages=3)

    assert [raw["ts"] for raw in snapshot.messages] == [
        "1007.000100",
        "1008.000100",
        "1009.000100",
    ]


# ----------------------------------------------------------------- ordering


def test_messages_come_back_oldest_first() -> None:
    """Slack serves newest first; everything downstream wants the other order."""
    fake = FakeSlack(
        messages=[make_message(f"10{n:02d}.000100") for n in range(5)]
    )

    snapshot = get_history(fake)

    assert [raw["ts"] for raw in snapshot.messages] == [
        "1000.000100",
        "1001.000100",
        "1002.000100",
        "1003.000100",
        "1004.000100",
    ]


def test_ordering_is_numeric_not_lexicographic() -> None:
    """"999.000100" sorts after "1000.000100" as text and before it as a number."""
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [
                make_message("1000.000100"),
                make_message("999.000100"),
            ],
        }
    )

    snapshot = get_history(fake)

    assert [raw["ts"] for raw in snapshot.messages] == [
        "999.000100",
        "1000.000100",
    ]


def test_ordering_spans_pages() -> None:
    fake = FakeSlack(messages=[make_message(f"10{n:02d}.000100") for n in range(6)])

    snapshot = get_history(fake, page_size=2)

    timestamps = [raw["ts"] for raw in snapshot.messages]
    assert timestamps == sorted(timestamps, key=float)


@pytest.mark.parametrize("ts", ["not-a-timestamp", "", 12345, None])
def test_an_unusable_timestamp_does_not_break_the_sort(ts: object) -> None:
    fake = FakeSlack(
        history_body={
            "ok": True,
            "messages": [
                make_message("1000.000100"),
                {"type": "message", "ts": ts, "text": "odd"},
            ],
        }
    )

    snapshot = get_history(fake)

    assert snapshot.retrieved_messages == 2


# ------------------------------------------------------------- ok is false


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("invalid_auth", SlackAuthenticationError),
        ("not_authed", SlackAuthenticationError),
        ("token_revoked", SlackAuthenticationError),
        ("token_expired", SlackAuthenticationError),
        ("account_inactive", SlackAuthenticationError),
        ("invalid_token", SlackAuthenticationError),
        ("missing_scope", SlackPermissionError),
        ("no_permission", SlackPermissionError),
        ("not_in_channel", SlackPermissionError),
        ("access_denied", SlackPermissionError),
        ("not_allowed_token_type", SlackPermissionError),
        ("channel_not_found", SlackNotFoundError),
        ("ratelimited", SlackRateLimitError),
        ("rate_limited", SlackRateLimitError),
        ("internal_error", SlackApiError),
        ("service_unavailable", SlackApiError),
        ("fatal_error", SlackApiError),
    ],
)
def test_slack_error_strings_map_to_our_errors(
    error: str, expected: type[Exception]
) -> None:
    """Slack reports every one of these with HTTP 200."""
    with pytest.raises(expected):
        get_history(FakeSlack(ok=False, error=error))


def test_an_unrecognised_error_string_is_an_upstream_problem() -> None:
    """Not an authentication failure: guessing sends an operator the wrong way."""
    with pytest.raises(SlackApiError):
        get_history(FakeSlack(ok=False, error="something_slack_added_last_week"))


def test_a_200_with_ok_false_is_never_read_as_an_empty_run() -> None:
    """The regression this connector's _read_payload exists to prevent."""
    fake = FakeSlack(
        history_body={"ok": False, "error": "channel_not_found", "messages": []}
    )

    with pytest.raises(SlackNotFoundError):
        get_history(fake)


@pytest.mark.parametrize("ok", [None, "true", 0, 1, [], {}])
def test_a_response_without_a_true_ok_is_a_failure(ok: object) -> None:
    fake = FakeSlack(history_body={"ok": ok, "messages": []})

    with pytest.raises(SlackApiError):
        get_history(fake)


def test_ok_false_with_no_error_string_is_an_upstream_problem() -> None:
    with pytest.raises(SlackApiError):
        get_history(FakeSlack(history_body={"ok": False}))


def test_a_channel_the_bot_is_not_in_says_so_without_naming_slacks_error() -> None:
    with pytest.raises(SlackPermissionError) as caught:
        get_history(FakeSlack(ok=False, error="not_in_channel"))

    assert "not_in_channel" not in caught.value.message
    assert "scope" in caught.value.message.lower()


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 503])
def test_a_non_200_status_is_an_upstream_problem(status: int) -> None:
    """Slack reports its own failures in the body, so a status here is transport."""
    with pytest.raises(SlackApiError):
        get_history(FakeSlack(history_status=status))


def test_a_429_is_a_rate_limit() -> None:
    with pytest.raises(SlackRateLimitError):
        get_history(FakeSlack(history_status=429, headers={"Retry-After": "30"}))


def test_a_rate_limit_returns_immediately_instead_of_waiting() -> None:
    """Retry-After is logged, never obeyed - sleeping would stall the request."""
    fake = FakeSlack(history_status=429, headers={"Retry-After": "120"})

    started = time.monotonic()
    with pytest.raises(SlackRateLimitError):
        get_history(fake)
    elapsed = time.monotonic() - started

    assert elapsed < 1


def test_a_rate_limit_without_a_retry_after_still_maps() -> None:
    with pytest.raises(SlackRateLimitError):
        get_history(FakeSlack(history_status=429))


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("no route"), httpx.ReadError("reset")]
)
def test_a_network_failure_is_an_api_error(error: Exception) -> None:
    with pytest.raises(SlackApiError):
        get_history(FakeSlack(raises=error))


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("slow"), httpx.ReadTimeout("slow")]
)
def test_a_timeout_says_so(error: Exception) -> None:
    with pytest.raises(SlackApiError) as caught:
        get_history(FakeSlack(raises=error))

    assert "in time" in caught.value.message


@pytest.mark.parametrize(
    "body", ["<html>Login</html>", "", "null", "[1, 2, 3]", '"a string"']
)
def test_a_body_that_is_not_a_json_object_is_an_api_error(body: str) -> None:
    with pytest.raises(SlackApiError) as caught:
        get_history(FakeSlack(history_text=body))

    assert "could not be read" in caught.value.message


def test_a_timeout_on_a_later_page_says_so() -> None:
    """The earlier cases fail on the first call; this one gets further in."""

    def handler(request: httpx.Request) -> httpx.Response:
        if len(fake.requests) >= 1:
            raise httpx.ReadTimeout("slow")
        return fake.handler(request)

    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])
    connector = SlackConnector(
        FAKE_TOKEN, page_size=2, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(SlackApiError) as caught:
        connector.get_history(CHANNEL)

    assert "in time" in caught.value.message


# ----------------------------------------------------------------- logging


def test_a_page_is_logged_with_its_count(caplog) -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(3)])

    with caplog.at_level("INFO", logger="app.connectors.slack_connector"):
        get_history(fake, page_size=2)

    assert "Slack history page 1 returned 2 messages" in caplog.text
    assert "Slack history page 2 returned 1 messages" in caplog.text


def test_the_run_reports_its_api_call_count(caplog) -> None:
    fake = FakeSlack(messages=[make_message(f"100{n}.000100") for n in range(5)])

    with caplog.at_level("INFO", logger="app.connectors.slack_connector"):
        get_history(fake, page_size=2)

    assert "Retrieved 5 Slack history items from 3 page(s) (3 Slack API calls)" in (
        caplog.text
    )


def test_message_text_is_never_logged(caplog) -> None:
    """A channel's conversation is the most sensitive thing this app reads."""
    fake = FakeSlack(
        messages=[make_message("1000.000100", text="sentinel-message-body")]
    )

    with caplog.at_level("DEBUG"):
        get_history(fake)

    assert "sentinel-message-body" not in caplog.text


def test_the_retry_after_value_is_logged(caplog) -> None:
    fake = FakeSlack(history_status=429, headers={"Retry-After": "97"})

    with caplog.at_level("WARNING", logger="app.connectors.slack_connector"):
        with pytest.raises(SlackRateLimitError):
            get_history(fake)

    assert "97" in caplog.text
    assert "not waited on" in caplog.text


def test_an_upstream_error_body_is_logged_but_not_returned(caplog) -> None:
    """Slack's own wording helps an operator and hurts a client."""
    fake = FakeSlack(history_status=500, history_text="workspace-internal-sentinel")

    with caplog.at_level("WARNING", logger="app.connectors.slack_connector"):
        with pytest.raises(SlackApiError) as caught:
            get_history(fake)

    assert "workspace-internal-sentinel" in caplog.text
    assert "workspace-internal-sentinel" not in caught.value.message


def test_a_slack_error_string_is_logged_but_not_returned(caplog) -> None:
    fake = FakeSlack(ok=False, error="missing_scope")

    with caplog.at_level("WARNING", logger="app.connectors.slack_connector"):
        with pytest.raises(SlackPermissionError) as caught:
            get_history(fake)

    assert "missing_scope" in caplog.text
    assert "missing_scope" not in caught.value.message


# ---------------------------------------------------------------- security


def test_the_token_never_reaches_the_logs(caplog) -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])
    secret = FAKE_TOKEN.get_secret_value()

    with caplog.at_level("DEBUG"):
        get_history(fake)

    assert secret not in caplog.text
    assert "xoxb-slack-fake" not in caplog.text


def test_no_authorization_header_is_logged(caplog) -> None:
    fake = FakeSlack(history_status=500)

    with caplog.at_level("DEBUG"):
        with pytest.raises(SlackApiError):
            get_history(fake)

    assert "authorization" not in caplog.text.lower()
    assert "Bearer " not in caplog.text


def test_the_token_never_appears_in_an_error_message() -> None:
    with pytest.raises(SlackApiError) as caught:
        get_history(FakeSlack(history_status=500))

    assert FAKE_TOKEN.get_secret_value() not in caught.value.message


def test_the_token_never_appears_in_an_ok_false_error_message() -> None:
    with pytest.raises(SlackAuthenticationError) as caught:
        get_history(FakeSlack(ok=False, error="invalid_auth"))

    assert FAKE_TOKEN.get_secret_value() not in caught.value.message


def test_the_connector_does_not_keep_the_token_on_an_attribute() -> None:
    """The token reaches one expression - the Authorization header - and stops."""
    connector = build_connector(FakeSlack())

    state = repr(vars(connector))

    assert FAKE_TOKEN.get_secret_value() not in state
    assert "xoxb-slack-fake" not in state


def test_closing_releases_the_http_client() -> None:
    fake = FakeSlack(messages=[make_message("1000.000100")])

    with build_connector(fake) as connector:
        connector.get_history(CHANNEL)
        assert connector._client.is_closed is False

    assert connector._client.is_closed is True
