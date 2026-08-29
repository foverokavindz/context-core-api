
import logging

from app.models.slack.chunk import SlackChunk
from app.models.slack.message import SlackMessage

logger = logging.getLogger(__name__)


class SlackChunker:
    """Renders messages as the text we expect to embed."""

    def chunk(self, message: SlackMessage) -> SlackChunk:
        """Render one message.
        """
        return SlackChunk(
            channel_id=message.channel_id,
            message_ts=message.message_ts,
            author_id=message.author_id,
            content=self._render_content(message),
            external_id=message.external_id,
        )

    def chunk_many(self, messages: list[SlackMessage]) -> list[SlackChunk]:
        """Render every message, in order.
        """
        chunks = [self.chunk(message) for message in messages]
        logger.info("Generated %d Slack chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(message: SlackMessage) -> str:
        """Lay out one message as the text to embed.
        """
        return message.text
