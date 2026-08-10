"""Tests for raw Slack history JSON -> SlackMessage.

Nothing here touches the network. The subject is one decision made over and
over: is this history item something a person said, or is it Slack narrating an
event?

Most of this file is therefore the filter, one test per rule and one per rule
that nearly applies - a thread *root* against a thread *reply*, a bot's message
against a channel-join notice that also has a subtype. Those pairs are where a
filter regresses.

The other half guards the two things that must NOT happen: no filtered message
may record an error, because a healthy channel is mostly filtered messages; and
no reaction, file, attachment or block may influence the result, because this
version ignores them rather than reading them.
"""

import pytest

from app.ingestion.slack_parser import SlackParser

CHANNEL = "C0123456789"
USER = "U0000000001"
BOT = "B0000000002"
TS = "1754810101.100100"


@pytest.fixture
def parser() -> SlackParser:
    return SlackParser()


def make_raw(
    ts: object = TS,
    *,
    message_type: object = "message",
    text: object = "We should update the authentication flow.",
    user: object = USER,
    subtype: object = None,
    thread_ts: object = None,
    bot_id: object = None,
) -> dict:
    """One raw history item, shaped as conversations.history returns it.

    Every field is overridable - including with None, which removes it - so a
    test can take away exactly the one thing it is about.
    """
    raw: dict = {}

    if message_type is not None:
        raw["type"] = message_type
    if ts is not None:
        raw["ts"] = ts
    if text is not None:
        raw["text"] = text
    if user is not None:
        raw["user"] = user
    if subtype is not None:
        raw["subtype"] = subtype
    if thread_ts is not None:
        raw["thread_ts"] = thread_ts
    if bot_id is not None:
        raw["bot_id"] = bot_id

    return raw


def parse(parser: SlackParser, raw: dict):
    """Parse one item with the channel every test shares."""
    return parser.parse(raw, channel_id=CHANNEL)


# --------------------------------------------------------------- happy path


def test_a_normal_user_message_is_kept(parser: SlackParser) -> None:
    message = parse(parser, make_raw())

    assert message is not None
    assert message.channel_id == CHANNEL
    assert message.message_ts == TS
    assert message.author_id == USER
    assert message.text == "We should update the authentication flow."


def test_a_textual_bot_message_is_kept(parser: SlackParser) -> None:
    message = parse(
        parser, make_raw(subtype="bot_message", user=None, bot_id=BOT)
    )

    assert message is not None
    assert message.author_id == BOT


def test_a_bot_message_that_also_names_a_user_is_attributed_to_the_user(
    parser: SlackParser,
) -> None:
    """An app posting on somebody's behalf is more usefully filed under them."""
    message = parse(parser, make_raw(subtype="bot_message", bot_id=BOT))

    assert message is not None
    assert message.author_id == USER


def test_a_message_with_no_author_at_all_is_still_kept(parser: SlackParser) -> None:
    message = parse(parser, make_raw(user=None))

    assert message is not None
    assert message.author_id is None


def test_the_channel_comes_from_the_run_not_from_the_payload(
    parser: SlackParser,
) -> None:
    """A message claiming another channel cannot widen a run past the one asked for."""
    raw = make_raw()
    raw["channel"] = "C9999999999"

    message = parse(parser, raw)

    assert message is not None
    assert message.channel_id == CHANNEL


# ------------------------------------------------------------------ threads


def test_a_thread_root_is_kept(parser: SlackParser) -> None:
    """It is still an ordinary channel message; it just happens to have replies."""
    message = parse(parser, make_raw(thread_ts=TS))

    assert message is not None
    assert message.message_ts == TS


def test_a_thread_reply_is_skipped(parser: SlackParser) -> None:
    assert parse(parser, make_raw("1754810200.200200", thread_ts=TS)) is None


def test_a_thread_reply_broadcast_into_the_channel_is_skipped(
    parser: SlackParser,
) -> None:
    """The case the thread_ts rule exists for: it looks like channel history."""
    raw = make_raw("1754810200.200200", subtype="thread_broadcast", thread_ts=TS)

    assert parse(parser, raw) is None


def test_a_thread_root_with_reply_metadata_is_still_kept(
    parser: SlackParser,
) -> None:
    raw = make_raw(thread_ts=TS)
    raw["reply_count"] = 12
    raw["reply_users"] = ["U1", "U2"]
    raw["latest_reply"] = "1754899999.000100"

    assert parse(parser, raw) is not None


# ----------------------------------------------------------------- subtypes


@pytest.mark.parametrize(
    "subtype",
    [
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "message_changed",
        "message_deleted",
        "message_replied",
        "tombstone",
        "file_share",
        "reminder_add",
        "bot_add",
        "huddle_thread",
        "a_subtype_slack_has_not_invented_yet",
    ],
)
def test_a_system_or_event_subtype_is_skipped(
    parser: SlackParser, subtype: str
) -> None:
    """An allow-list of two, so something new is skipped until we decide otherwise."""
    assert parse(parser, make_raw(subtype=subtype)) is None


def test_a_channel_join_notice_is_skipped_despite_having_text(
    parser: SlackParser,
) -> None:
    raw = make_raw(subtype="channel_join", text="<@U0000000001> has joined the channel")

    assert parse(parser, raw) is None


@pytest.mark.parametrize(
    "message_type", ["reaction_added", "channel_created", "", None]
)
def test_anything_that_is_not_a_message_is_skipped(
    parser: SlackParser, message_type: object
) -> None:
    assert parse(parser, make_raw(message_type=message_type)) is None


# ------------------------------------------------------------------- text


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t", None, 42, [], {}, True])
def test_a_message_with_no_usable_text_is_skipped(
    parser: SlackParser, text: object
) -> None:
    assert parse(parser, make_raw(text=text)) is None


def test_surrounding_whitespace_is_stripped(parser: SlackParser) -> None:
    message = parse(parser, make_raw(text="   Ship it.   \n"))

    assert message is not None
    assert message.text == "Ship it."


def test_excessive_blank_lines_collapse(parser: SlackParser) -> None:
    message = parse(parser, make_raw(text="First\n\n\n\n\nSecond"))

    assert message is not None
    assert message.text == "First\n\nSecond"


def test_a_single_blank_line_survives(parser: SlackParser) -> None:
    message = parse(parser, make_raw(text="First\n\nSecond"))

    assert message is not None
    assert message.text == "First\n\nSecond"


def test_windows_line_endings_are_normalised(parser: SlackParser) -> None:
    message = parse(parser, make_raw(text="First\r\nSecond\rThird"))

    assert message is not None
    assert message.text == "First\nSecond\nThird"


def test_trailing_whitespace_on_a_line_is_removed(parser: SlackParser) -> None:
    message = parse(parser, make_raw(text="First   \nSecond\t"))

    assert message is not None
    assert message.text == "First\nSecond"


# ---------------------------------------------------------------- escaping


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("a &amp; b", "a & b"),
        ("a &lt; b", "a < b"),
        ("a &gt; b", "a > b"),
        ("if (a &lt; b &amp;&amp; c &gt; d)", "if (a < b && c > d)"),
        ("&amp;&amp;&amp;", "&&&"),
    ],
)
def test_slacks_three_escapes_are_undone(
    parser: SlackParser, wire: str, expected: str
) -> None:
    message = parse(parser, make_raw(text=wire))

    assert message is not None
    assert message.text == expected


def test_a_literally_typed_entity_survives(parser: SlackParser) -> None:
    """Slack sends a typed "&lt;" as "&amp;lt;". Unescaping &amp; first would
    turn it into "<" and lose what the author wrote."""
    message = parse(parser, make_raw(text="type &amp;lt; to get a bracket"))

    assert message is not None
    assert message.text == "type &lt; to get a bracket"


@pytest.mark.parametrize(
    "text", ["50&nbsp;km", "&copy; 2026", "&#8212; a dash", "&quot;quoted&quot;"]
)
def test_other_html_entities_are_left_alone(
    parser: SlackParser, text: str
) -> None:
    """Slack escapes exactly three characters; everything else is literal text."""
    message = parse(parser, make_raw(text=text))

    assert message is not None
    assert message.text == text


# ------------------------------------------------------- left alone on purpose


@pytest.mark.parametrize(
    "text",
    [
        "cc <@U0000000001> please review",
        "see <#C0123456789|engineering>",
        "docs at <https://example.com|the wiki>",
        "shipped :tada: :rocket:",
        "shipped 🎉",
        "<!here> heads up",
        "`code` and *bold* and _italic_ and ~strike~",
        "```\nblock\n```",
    ],
)
def test_slack_markup_reaches_the_model_verbatim(
    parser: SlackParser, text: str
) -> None:
    """No API call resolves any of this, and rewriting it would change what
    somebody said into what we guessed they meant."""
    message = parse(parser, make_raw(text=text))

    assert message is not None
    assert message.text == text


# --------------------------------------------------------------- ignored data


def test_reactions_are_ignored(parser: SlackParser) -> None:
    raw = make_raw()
    raw["reactions"] = [
        {"name": "tada", "users": ["U1", "U2"], "count": 2},
        {"name": "+1", "users": ["U3"], "count": 1},
    ]

    message = parse(parser, raw)

    assert message is not None
    assert message.text == "We should update the authentication flow."
    assert "tada" not in message.text


def test_files_are_ignored(parser: SlackParser) -> None:
    raw = make_raw()
    raw["files"] = [{"id": "F1", "name": "design.pdf", "url_private": "https://x"}]

    message = parse(parser, raw)

    assert message is not None
    assert "design.pdf" not in message.text


def test_attachments_are_ignored(parser: SlackParser) -> None:
    raw = make_raw()
    raw["attachments"] = [{"title": "A link", "text": "attachment body text"}]

    message = parse(parser, raw)

    assert message is not None
    assert "attachment body text" not in message.text


def test_blocks_are_ignored(parser: SlackParser) -> None:
    raw = make_raw()
    raw["blocks"] = [
        {
            "type": "rich_text",
            "elements": [{"type": "text", "text": "block-only-content"}],
        }
    ]

    message = parse(parser, raw)

    assert message is not None
    assert "block-only-content" not in message.text


def test_a_message_that_is_only_a_file_with_no_text_is_skipped(
    parser: SlackParser,
) -> None:
    raw = make_raw(text="", subtype="file_share")
    raw["files"] = [{"id": "F1", "name": "design.pdf"}]

    assert parse(parser, raw) is None


def test_edit_pin_and_client_metadata_are_ignored(parser: SlackParser) -> None:
    raw = make_raw()
    raw["edited"] = {"user": "U1", "ts": "1754810999.000000"}
    raw["client_msg_id"] = "26a0b6ba-1d9f-4a2e-9d05-000000000000"
    raw["pinned_to"] = [CHANNEL]
    raw["team"] = "T0000000001"

    message = parse(parser, raw)

    assert message is not None
    assert message.model_dump().keys() == {
        "channel_id",
        "message_ts",
        "author_id",
        "text",
    }


# ------------------------------------------------------------------ the ts


@pytest.mark.parametrize("ts", [None, "", "   ", 1754810101.1, 42, [], {}, True])
def test_a_message_with_no_usable_timestamp_is_skipped(
    parser: SlackParser, ts: object
) -> None:
    assert parse(parser, make_raw(ts)) is None


def test_the_timestamp_is_kept_as_the_string_slack_sent(
    parser: SlackParser,
) -> None:
    """It is the message's identity; reformatting it would break the pointer back."""
    message = parse(parser, make_raw("1754810101.100100"))

    assert message is not None
    assert message.message_ts == "1754810101.100100"


def test_a_trailing_zero_in_the_timestamp_is_not_dropped(
    parser: SlackParser,
) -> None:
    message = parse(parser, make_raw("1754810101.000000"))

    assert message is not None
    assert message.message_ts == "1754810101.000000"


# ---------------------------------------------------------------- parse_many


def test_parse_many_keeps_order(parser: SlackParser) -> None:
    errors: list[tuple[str, str]] = []

    messages = parser.parse_many(
        [make_raw("1000.000100"), make_raw("1001.000100"), make_raw("1002.000100")],
        errors,
        channel_id=CHANNEL,
    )

    assert [message.message_ts for message in messages] == [
        "1000.000100",
        "1001.000100",
        "1002.000100",
    ]


def test_parse_many_filters_a_mixed_batch(parser: SlackParser) -> None:
    errors: list[tuple[str, str]] = []

    messages = parser.parse_many(
        [
            make_raw("1000.000100"),
            make_raw("1001.000100", subtype="channel_join"),
            make_raw("1002.000100", thread_ts="1000.000100"),
            make_raw("1003.000100", text="   "),
            make_raw("1004.000100"),
        ],
        errors,
        channel_id=CHANNEL,
    )

    assert [message.message_ts for message in messages] == [
        "1000.000100",
        "1004.000100",
    ]


def test_a_filtered_message_records_no_error(parser: SlackParser) -> None:
    """The rule this file exists for: a healthy channel is mostly filtered items."""
    errors: list[tuple[str, str]] = []

    parser.parse_many(
        [
            make_raw("1000.000100", subtype="channel_join"),
            make_raw("1001.000100", thread_ts="1000.000100"),
            make_raw("1002.000100", text=""),
        ],
        errors,
        channel_id=CHANNEL,
    )

    assert errors == []


@pytest.mark.parametrize("item", ["not a dict", 42, None, [], True])
def test_an_item_that_is_not_an_object_is_recorded(
    parser: SlackParser, item: object
) -> None:
    errors: list[tuple[str, str]] = []

    messages = parser.parse_many(
        [make_raw("1000.000100"), item], errors, channel_id=CHANNEL
    )

    assert len(messages) == 1
    assert errors == [
        (
            "item #2",
            "Slack returned a history item that is not a message object.",
        )
    ]


def test_one_unreadable_item_does_not_cost_the_channel(parser: SlackParser) -> None:
    errors: list[tuple[str, str]] = []

    messages = parser.parse_many(
        [make_raw("1000.000100"), "junk", make_raw("1002.000100")],
        errors,
        channel_id=CHANNEL,
    )

    assert len(messages) == 2
    assert len(errors) == 1


def test_an_empty_batch_parses_to_nothing(parser: SlackParser) -> None:
    errors: list[tuple[str, str]] = []

    assert parser.parse_many([], errors, channel_id=CHANNEL) == []
    assert errors == []


def test_the_parsed_count_is_logged(parser: SlackParser, caplog) -> None:
    errors: list[tuple[str, str]] = []

    with caplog.at_level("INFO", logger="app.ingestion.slack_parser"):
        parser.parse_many(
            [make_raw("1000.000100"), make_raw("1001.000100")],
            errors,
            channel_id=CHANNEL,
        )

    assert "Parsed 2 Slack messages" in caplog.text


def test_message_text_is_never_logged(parser: SlackParser, caplog) -> None:
    errors: list[tuple[str, str]] = []

    with caplog.at_level("DEBUG"):
        parser.parse_many(
            [
                make_raw("1000.000100", text="sentinel-kept-body"),
                make_raw(
                    "1001.000100",
                    subtype="channel_join",
                    text="sentinel-skipped-body",
                ),
            ],
            errors,
            channel_id=CHANNEL,
        )

    assert "sentinel-kept-body" not in caplog.text
    assert "sentinel-skipped-body" not in caplog.text
