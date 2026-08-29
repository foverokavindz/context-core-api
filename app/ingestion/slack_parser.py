
import logging
import re

from app.models.slack.message import SlackMessage

logger = logging.getLogger(__name__)

SlackMessageJson = dict[str, object]

MESSAGE_TYPE = "message"

KEPT_SUBTYPES = frozenset({"bot_message"})

SLACK_ENTITIES: tuple[tuple[str, str], ...] = (
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&amp;", "&"),
)

_EXCESS_NEWLINES = re.compile(r"\n{3,}")


class SlackParser:
    """Turns Slack's history payloads into our own model, or into nothing."""

    def parse(
        self, raw: SlackMessageJson, *, channel_id: str
    ) -> SlackMessage | None:
        """Normalise one history item, or return None if it is not one.
        """
        if raw.get("type") != MESSAGE_TYPE:
            return None

        subtype = raw.get("subtype")
        if subtype is not None and subtype not in KEPT_SUBTYPES:
            return None

        message_ts = _identifier(raw.get("ts"))
        if message_ts is None:
            return None

        thread_ts = _identifier(raw.get("thread_ts"))
        if thread_ts is not None and thread_ts != message_ts:
            return None

        text = _message_text(raw.get("text"))
        if not text:
            return None

        return SlackMessage(

            channel_id=channel_id,
            message_ts=message_ts,
            author_id=_author_id(raw),
            text=text,

            external_id=f"{channel_id}:{message_ts}",
        )

    def parse_many(
        self,
        raw_messages: list[SlackMessageJson],
        errors: list[tuple[str, str]],
        *,
        channel_id: str,
    ) -> list[SlackMessage]:
        """Normalise every history item, recording only the unreadable ones.
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

                logger.debug("Filtered Slack history item %s", raw.get("ts"))
                continue

            messages.append(message)

        logger.info("Parsed %d Slack messages", len(messages))
        return messages


# --------------------------------------------------------------- extraction


def _author_id(raw: SlackMessageJson) -> str | None:
    """Name whoever posted, as an opaque Slack id.
    """
    return _identifier(raw.get("user")) or _identifier(raw.get("bot_id"))


def _identifier(value: object) -> str | None:
    """Read an opaque Slack id or timestamp, or None if there is not one.
    """
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    return stripped or None


# ------------------------------------------------------------ normalisation


def _message_text(value: object) -> str:
    """Reduce one message's text to what a person actually wrote.
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
