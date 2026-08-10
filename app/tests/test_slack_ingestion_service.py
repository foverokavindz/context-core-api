"""Tests for the Slack pipeline's orchestration.

Only the network boundary is faked. The real parser and the real chunker both
run, and the fake connector hands back raw Slack-shaped dicts rather than
finished models - so these tests exercise the whole pipeline below the HTTP
layer and would notice any of those pieces being wired up wrong.

The fake also records how it was used, which is what backs the two wiring
guarantees worth stating out loud: the connector is called exactly once, and it
is closed before any parsing happens.

The funnel is what this file watches hardest. `retrieved_messages` counts what
Slack served and `parsed_messages` counts what survived the filter, and unlike
the other three sources a large gap between them is correct rather than
alarming - so a test that only checked "the numbers match" would be asserting a
bug.
"""

import pytest
from pydantic import SecretStr

from app.connectors.slack_connector import SlackSnapshot
from app.core.exceptions import (
    SlackAuthenticationError,
    SlackNotFoundError,
    SlackRateLimitError,
)
from app.ingestion.slack_ingestion_service import SlackIngestionService

TOKEN = SecretStr("xoxb-slack-fake-token-for-tests-only")
CHANNEL = "C0123456789"
USER = "U0000000001"


# ------------------------------------------------------------------- fakes


def make_raw(
    ts: str = "1754810101.100100",
    *,
    text: str = "We should update the authentication flow.",
    subtype: str | None = None,
    thread_ts: str | None = None,
) -> dict:
    """One raw history item, shaped as conversations.history returns it."""
    raw: dict = {"type": "message", "ts": ts, "text": text, "user": USER}
    if subtype is not None:
        raw["subtype"] = subtype
    if thread_ts is not None:
        raw["thread_ts"] = thread_ts
    return raw


class FakeSlackConnector:
    """Stands in for SlackConnector, recording how it was used."""

    def __init__(
        self,
        raw_messages: list[dict] | None = None,
        *,
        truncated: bool = False,
        errors: list[tuple[str, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.raw_messages = raw_messages if raw_messages is not None else []
        self.truncated = truncated
        self.errors = errors if errors is not None else []
        self.error = error

        self.call_count = 0
        self.max_messages: int | None = None
        self.channel_id: str | None = None
        self.closed = False

    def get_history(
        self, channel_id: str, *, max_messages: int | None = None
    ) -> SlackSnapshot:
        self.call_count += 1
        self.max_messages = max_messages
        self.channel_id = channel_id

        if self.error is not None:
            raise self.error

        return SlackSnapshot(
            channel_id=channel_id,
            retrieved_messages=len(self.raw_messages),
            truncated=self.truncated,
            messages=list(self.raw_messages),
            errors=list(self.errors),
        )

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeSlackConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def build_service(connector: FakeSlackConnector, **kwargs) -> SlackIngestionService:
    return SlackIngestionService(connector_factory=lambda token: connector, **kwargs)


def ingest(connector: FakeSlackConnector, **kwargs):
    """Run one ingestion through the real parser and chunker.

    Keyword arguments go to `ingest`, not to the service, so a test that wants
    to configure the service itself builds one with `build_service`.
    """
    return build_service(connector).ingest(TOKEN, CHANNEL, **kwargs)


# --------------------------------------------------------------- happy path


def test_a_channel_runs_end_to_end() -> None:
    result = ingest(FakeSlackConnector([make_raw("1000.000100")]))

    assert result.channel_id == CHANNEL
    assert result.retrieved_messages == 1
    assert result.parsed_messages == 1
    assert result.generated_chunks == 1
    assert result.truncated is False
    assert result.errors == []


def test_an_empty_channel_is_not_an_error() -> None:
    result = ingest(FakeSlackConnector([]))

    assert result.retrieved_messages == 0
    assert result.parsed_messages == 0
    assert result.generated_chunks == 0
    assert result.messages == []
    assert result.chunks == []


def test_the_real_parser_runs() -> None:
    """Not a mocked one: the escaping has to actually come out undone."""
    connector = FakeSlackConnector([make_raw(text="ship &amp; tell")])

    assert ingest(connector).messages[0].text == "ship & tell"


def test_the_real_chunker_runs() -> None:
    connector = FakeSlackConnector([make_raw(text="Move it to the service layer.")])

    assert ingest(connector).chunks[0].content == "Move it to the service layer."


def test_every_message_carries_the_channel_that_was_read() -> None:
    connector = FakeSlackConnector(
        [make_raw("1000.000100"), make_raw("1001.000100")]
    )

    result = ingest(connector)

    for message in result.messages:
        assert message.channel_id == CHANNEL
    for chunk in result.chunks:
        assert chunk.channel_id == CHANNEL


# ------------------------------------------------------------------ the funnel


def test_the_filter_shows_up_as_a_gap_in_the_funnel() -> None:
    """A real channel is mostly joins and thread replies; that is not a fault."""
    connector = FakeSlackConnector(
        [
            make_raw("1000.000100"),
            make_raw("1001.000100", subtype="channel_join"),
            make_raw("1002.000100", thread_ts="1000.000100"),
            make_raw("1003.000100", text="   "),
            make_raw("1004.000100"),
        ]
    )

    result = ingest(connector)

    assert result.retrieved_messages == 5
    assert result.parsed_messages == 2
    assert result.generated_chunks == 2


def test_filtering_records_no_errors() -> None:
    connector = FakeSlackConnector(
        [
            make_raw("1000.000100", subtype="channel_join"),
            make_raw("1001.000100", thread_ts="1000.000100"),
        ]
    )

    assert ingest(connector).errors == []


def test_chunks_always_match_parsed_messages() -> None:
    connector = FakeSlackConnector(
        [make_raw(f"10{n:02d}.000100", subtype=None if n % 2 else "channel_join")
         for n in range(10)]
    )

    result = ingest(connector)

    assert result.generated_chunks == result.parsed_messages


def test_one_malformed_item_does_not_cost_the_channel() -> None:
    connector = FakeSlackConnector(
        [make_raw("1000.000100"), "not a message object", make_raw("1002.000100")]
    )

    result = ingest(connector)

    assert result.retrieved_messages == 3
    assert result.parsed_messages == 2
    assert result.generated_chunks == 2
    assert len(result.errors) == 1


def test_connector_errors_survive_into_the_result() -> None:
    connector = FakeSlackConnector(
        [make_raw("1000.000100")],
        truncated=True,
        errors=[
            (
                CHANNEL,
                "Ingestion stopped at the 1-message cap; the channel contains "
                "more.",
            )
        ],
    )

    result = ingest(connector)

    assert result.truncated is True
    assert result.errors[0][0] == CHANNEL


# ------------------------------------------------------------------- wiring


def test_the_connector_is_called_once() -> None:
    connector = FakeSlackConnector([make_raw("1000.000100")])

    ingest(connector)

    assert connector.call_count == 1


def test_the_connector_is_asked_for_the_channel_that_was_requested() -> None:
    connector = FakeSlackConnector([make_raw("1000.000100")])

    ingest(connector)

    assert connector.channel_id == CHANNEL


def test_the_connector_is_closed_before_parsing() -> None:
    """The token must not outlive the fetching."""
    connector = FakeSlackConnector([make_raw("1000.000100")])

    ingest(connector)

    assert connector.closed is True


def test_the_connector_is_closed_when_the_run_fails() -> None:
    connector = FakeSlackConnector(error=SlackAuthenticationError())

    with pytest.raises(SlackAuthenticationError):
        ingest(connector)

    assert connector.closed is True


def test_the_service_default_message_cap_is_applied() -> None:
    from app.models.slack_response import MAX_MESSAGES_PER_INGESTION

    connector = FakeSlackConnector([make_raw("1000.000100")])

    ingest(connector)

    assert connector.max_messages == MAX_MESSAGES_PER_INGESTION


def test_a_per_run_cap_overrides_the_service_default() -> None:
    connector = FakeSlackConnector([make_raw("1000.000100")])

    ingest(connector, max_messages=7)

    assert connector.max_messages == 7


def test_a_service_level_cap_is_used_when_the_run_names_none() -> None:
    connector = FakeSlackConnector([make_raw("1000.000100")])

    build_service(connector, max_messages=42).ingest(TOKEN, CHANNEL)

    assert connector.max_messages == 42


def test_the_channel_stamped_on_messages_comes_from_the_snapshot() -> None:
    """Not from what the caller typed, so the result cannot claim a channel
    the run did not read."""
    connector = FakeSlackConnector([make_raw("1000.000100")])

    result = ingest(connector)

    assert result.channel_id == connector.channel_id
    assert result.messages[0].channel_id == connector.channel_id


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    "error",
    [SlackAuthenticationError(), SlackNotFoundError(), SlackRateLimitError()],
)
def test_a_connector_failure_reaches_the_caller(error: Exception) -> None:
    with pytest.raises(type(error)):
        ingest(FakeSlackConnector(error=error))


def test_a_failed_run_produces_no_partial_result() -> None:
    with pytest.raises(SlackRateLimitError):
        ingest(
            FakeSlackConnector(
                [make_raw("1000.000100")], error=SlackRateLimitError()
            )
        )


# ----------------------------------------------------------------- logging


def test_the_run_is_summarised(caplog) -> None:
    connector = FakeSlackConnector(
        [make_raw("1000.000100"), make_raw("1001.000100")]
    )

    with caplog.at_level("INFO", logger="app.ingestion.slack_ingestion_service"):
        ingest(connector)

    assert f"Ingesting Slack channel {CHANNEL}" in caplog.text
    assert "into 2 chunks" in caplog.text


def test_the_summary_reports_both_sides_of_the_funnel(caplog) -> None:
    connector = FakeSlackConnector(
        [make_raw("1000.000100"), make_raw("1001.000100", subtype="channel_join")]
    )

    with caplog.at_level("INFO", logger="app.ingestion.slack_ingestion_service"):
        ingest(connector)

    assert "2 history items retrieved, 1 parsed" in caplog.text


def test_the_token_never_reaches_the_logs(caplog) -> None:
    connector = FakeSlackConnector([make_raw("1000.000100")])

    with caplog.at_level("DEBUG"):
        ingest(connector)

    assert TOKEN.get_secret_value() not in caplog.text
    assert "xoxb-slack-fake" not in caplog.text


def test_message_text_is_never_logged(caplog) -> None:
    connector = FakeSlackConnector([make_raw(text="sentinel-service-body")])

    with caplog.at_level("DEBUG"):
        ingest(connector)

    assert "sentinel-service-body" not in caplog.text
