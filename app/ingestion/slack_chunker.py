"""SlackMessage -> SlackChunk.

    SlackMessage  ->  SlackChunker  ->  SlackChunk

One message produces exactly one chunk. Messages are neither grouped into
conversations nor split when long - the simplest baseline that can work, so that
what a chunk contains is obvious before any smarter strategy is layered on.
Grouping is the interesting question for a chat source, since a single "yes,
agreed" means nothing on its own, and it is deliberately left for later: a
baseline is what a windowing strategy would have to be measured against, and
measuring it is not part of this version. When that changes, the decision belongs
here and nowhere else.

This module is where all chunk formatting lives. The route does not build chunk
text and neither does the connector, so the shape of what gets embedded is
readable in one place.

**There is no rendered header, and that is where this chunker differs from
ConfluenceChunker.** A Confluence page gets "Space: TrackIt (TR)" and
"Page: Authentication" prefixed to it, because the space and the title are
context a page's body cannot recover on its own and cost a rounding error
against kilobytes of prose. A Slack message is frequently one sentence. Adding
three lines of channel id, author id and timestamp to "We should move
authentication validation into the service layer." would leave most of the
embedded text describing where the message came from rather than what it said.
Those identifiers ride on the chunk as metadata instead, which is where a
retrieval layer wants them anyway.

There is consequently no "(no content)" placeholder either, which Confluence
needs because empty pages are routine. The parser has already dropped every
message whose text came out empty, so a chunk with nothing in it is unreachable
from here.
"""

import logging

from app.models.slack.chunk import SlackChunk
from app.models.slack.message import SlackMessage

logger = logging.getLogger(__name__)


class SlackChunker:
    """Renders messages as the text we expect to embed."""

    def chunk(self, message: SlackMessage) -> SlackChunk:
        """Render one message.

        The message's own fields are carried onto the chunk as metadata, so a
        chunk is self-describing once it leaves the pipeline - but they are kept
        out of `content`, which holds what the person said and nothing else.
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

        len(chunks) == len(messages), always. Nothing here can skip a message or
        emit two for one, which is what makes `generated_chunks` and
        `parsed_messages` comparable in the response - and what makes a
        discrepancy between them a real bug rather than a filtering decision.
        """
        chunks = [self.chunk(message) for message in messages]
        logger.info("Generated %d Slack chunks", len(chunks))
        return chunks

    # --------------------------------------------------------------- internal

    @staticmethod
    def _render_content(message: SlackMessage) -> str:
        """Lay out one message as the text to embed.

        Which is to say: hand back what was said, unchanged. The method exists
        anyway rather than being inlined, because this is the one place a future
        version would add a header or fold a reply into its parent, and having
        somewhere obvious for that to go is worth one indirection.

        The text arrives already normalised from the parser and is never empty.
        """
        return message.text
