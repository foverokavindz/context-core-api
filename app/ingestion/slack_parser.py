"""Raw Slack message JSON -> SlackMessage, or nothing at all.

    {"type": "message", "user": "U1",   ->   SlackMessage(message_ts="175...",
     "ts": "1754810101.100100",                           author_id="U1",
     "text": "Ship it &amp; tell ops"}                    text="Ship it & tell ops")

    {"type": "message", "subtype":      ->   None
     "channel_join", "text": "<@U1>
     has joined the channel"}

This is the only module that knows Slack's field names. Nothing downstream sees
`subtype`, `thread_ts`, `bot_id`, `blocks` or `reactions`, and nothing here
performs I/O: no user is resolved to a name, no channel id to a title, no emoji
to an image. A run's entire Slack conversation happens in the connector.

The filter, in the order it is applied:

    type != "message"                     -> skip  (an event, not a message)
    subtype not absent and not bot_message -> skip (system / channel event)
    thread_ts present and != ts           -> skip  (a thread reply)
    no usable ts                          -> skip  (nothing to identify it by)
    text empty once normalised            -> skip  (nothing to embed)
    otherwise                             -> keep

Two of those rules are worth their own paragraph.

**The subtype rule is an allow-list of two, not a deny-list of thirty.** Slack
uses `subtype` for everything that is technically a message and is really a
channel event - joins, leaves, topic and purpose changes, renames, archivals,
edits, deletions, tombstones, file shares, huddles, and more arriving with every
release. Naming them all would be a losing race, and the failure mode of losing
it is that a new event type silently becomes ingested content. Allowing only
"absent" and "bot_message" inverts that: something new is skipped until someone
decides otherwise. The cost is that `file_share` - a file uploaded with a real
comment - is skipped too, which is a known and accepted V1 limitation.

**Skipping is not an error.** ConfluenceParser.parse raises when it cannot use a
page, because a Confluence space full of unusable pages means something is
wrong. Here the opposite is true: a healthy channel is *mostly* joins and thread
replies, so a skip is the pipeline working. parse() returns None for those, and
nothing is written to `errors` or logged above DEBUG - otherwise a normal run
would answer with a response buried in notices about ordinary Slack behaviour.
"""

import logging
import re

from app.models.slack_message import SlackMessage

logger = logging.getLogger(__name__)

# One raw history item exactly as the Web API returned it. `object` rather than
# `Any` because every read below narrows with isinstance anyway, and `object` is
# what forces that to stay true.
SlackMessageJson = dict[str, object]

# The only `type` a history item may have to be worth reading.
MESSAGE_TYPE = "message"

# The only subtype that is still a person or an app saying something, rather
# than Slack narrating an event. Everything else - named or not, existing today
# or not - is skipped. See the module docstring.
KEPT_SUBTYPES = frozenset({"bot_message"})

# The three characters Slack escapes in message text, and the only three. Slack
# documents that it escapes &, < and > and nothing else, so html.unescape would
# be wrong here: it would also rewrite &nbsp;, &copy; and &#8212;, which in a
# Slack message are literally what somebody typed.
#
# The order matters and is the reason this is a tuple rather than a dict.
# &amp; must be replaced LAST: doing it first turns "&amp;lt;" - which is how
# Slack transmits a literal "&lt;" - into "&lt;" and then into "<", losing what
# the author actually wrote.
SLACK_ENTITIES: tuple[tuple[str, str], ...] = (
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&amp;", "&"),
)

# Three or more newlines collapse to a blank line. Slack lets a message contain
# an arbitrary run of them and they carry no meaning worth embedding.
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


class SlackParser:
    """Turns Slack's history payloads into our own model, or into nothing."""

    def parse(
        self, raw: SlackMessageJson, *, channel_id: str
    ) -> SlackMessage | None:
        """Normalise one history item, or return None if it is not one.

        Returns None - never raises - for every item the filter rejects. A
        thread reply and a channel-join notice are both entirely normal things
        to find in channel history, so refusing them is an ordinary outcome
        rather than a failure worth reporting.
        """
        if raw.get("type") != MESSAGE_TYPE:
            return None

        subtype = raw.get("subtype")
        if subtype is not None and subtype not in KEPT_SUBTYPES:
            return None

        message_ts = _identifier(raw.get("ts"))
        if message_ts is None:
            return None

        # A reply that lives inside a thread, including one broadcast back into
        # the channel. The thread's root has thread_ts == ts and is kept: it is
        # an ordinary channel message that happens to have been replied to.
        # This is also the only defence needed against threads, because nothing
        # in this pipeline ever calls conversations.replies to find more.
        thread_ts = _identifier(raw.get("thread_ts"))
        if thread_ts is not None and thread_ts != message_ts:
            return None

        text = _message_text(raw.get("text"))
        if not text:
            return None

        return SlackMessage(
            # The channel comes from the run, not from the payload, so a message
            # claiming another channel cannot quietly widen the run past the one
            # that was asked for.
            channel_id=channel_id,
            message_ts=message_ts,
            author_id=_author_id(raw),
            text=text,
        )

    def parse_many(
        self,
        raw_messages: list[SlackMessageJson],
        errors: list[tuple[str, str]],
        *,
        channel_id: str,
    ) -> list[SlackMessage]:
        """Normalise every history item, recording only the unreadable ones.

        `errors` collects payloads that are not even shaped like a message -
        a string, a list, a null where an object belongs. Items the filter
        rejected are not in there: they are the expected majority of a channel's
        history, and listing them would make the response useless.

        One malformed payload costs one message, never the run - the same
        contract the Confluence pipeline gives a page with no id.
        """
        messages: list[SlackMessage] = []

        for position, raw in enumerate(raw_messages, start=1):
            if not isinstance(raw, dict):
                logger.warning(
                    "Skipping Slack history item %d: payload is not an object",
                    position,
                )
                errors.append(
                    (
                        f"item #{position}",
                        "Slack returned a history item that is not a message "
                        "object.",
                    )
                )
                continue

            message = self.parse(raw, channel_id=channel_id)
            if message is None:
                # Ordinary. Logged at DEBUG by timestamp only - never by text,
                # and never with the reason spelled out per item, which would
                # put a line in the log for every join notice in the channel.
                logger.debug("Filtered Slack history item %s", raw.get("ts"))
                continue

            messages.append(message)

        logger.info("Parsed %d Slack messages", len(messages))
        return messages


# --------------------------------------------------------------- extraction


def _author_id(raw: SlackMessageJson) -> str | None:
    """Name whoever posted, as an opaque Slack id.

    A person's message carries `user`; an app's carries `bot_id` and often no
    `user` at all. Preferring `user` means a bot posting on somebody's behalf is
    attributed to the person, which is the more useful answer of the two.

    Never resolved to a display name. That would cost a users.info call per
    distinct author, need a scope this app does not ask for, and put personal
    data into a chunk.
    """
    return _identifier(raw.get("user")) or _identifier(raw.get("bot_id"))


def _identifier(value: object) -> str | None:
    """Read an opaque Slack id or timestamp, or None if there is not one.

    Only strings count. Slack sends every id and every `ts` as a string, so a
    number here means the payload is not what it claims to be, and coercing it
    would invent an identity rather than read one.
    """
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    return stripped or None


# ------------------------------------------------------------ normalisation


def _message_text(value: object) -> str:
    """Reduce one message's text to what a person actually wrote.

    Light on purpose. Whitespace is tidied and Slack's three escapes are undone;
    nothing else is touched. In particular <@U123> mentions, <#C123|general>
    channel references, <http://example.com|links> and :emoji_name: shortcodes
    all survive verbatim - resolving any of them would need an API call this
    pipeline does not make, and rewriting them would change what somebody said
    into what we guessed they meant.

    Returns "" for anything that is not usable text, which the caller reads as
    "skip this message".
    """
    if not isinstance(value, str):
        return ""

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _EXCESS_NEWLINES.sub("\n\n", text)
    text = text.strip()

    for entity, character in SLACK_ENTITIES:
        text = text.replace(entity, character)

    return text
