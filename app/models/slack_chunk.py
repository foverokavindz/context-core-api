"""SlackChunk - one Slack message rendered as embeddable text.

This is the output of the Slack pipeline and its handover point, exactly as
ConfluenceChunk is for Confluence, JiraChunk is for Jira and CodeChunk is for
GitHub. The chunker produces one of these per message; the embedding service
then fills in `embedding` and `embedding_model` on the very same objects, in
place. It still carries no database id and no Slack-specific field.

For this first version the mapping is one chunk per message - messages are
neither grouped into conversations nor split when long. Grouping is the more
interesting question for a chat source and is deliberately left for later: a
baseline is what a windowing strategy would have to be measured against, and
measuring it is not part of this version.

The ids-stay-out-of-the-text rule that Confluence and Jira follow matters more
here than in either of them. A wiki page is kilobytes of prose, so a "Space:"
header costs it nothing; a Slack message is often a single sentence, and
prefixing it with a channel id, a user id and a float timestamp would leave the
identifiers outweighing what the person actually said.
"""

from pydantic import BaseModel


class SlackChunk(BaseModel):
    """The text of one message, plus the message fields as metadata.

    The message fields are repeated here rather than referenced so a chunk is
    self-describing once it leaves the pipeline - the same reason
    ConfluenceChunk copies its page fields. They will likely move into a shared
    metadata object once all four sources can be compared side by side.
    """

    channel_id: str
    message_ts: str
    author_id: str | None = None

    # What we expect to embed: the message text and nothing else. The three
    # fields above stay out of it deliberately - they are provenance for a
    # retrieval layer to filter and deduplicate on, not meaning for a model to
    # embed.
    content: str

    # Filled by the embedding service after chunking, on this same object, and
    # only once every batch of a run has come back intact - so these are either
    # both set or both None, never one without the other. A one-sentence message
    # costs the same 1536 floats as a whole wiki page does.
    embedding: list[float] | None = None
    embedding_model: str | None = None
