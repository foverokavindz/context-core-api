"""SlackMessage - one channel message, normalised into our own shape.

This model is THE boundary of the Slack pipeline, the counterpart to
ConfluencePage on the Confluence side, JiraIssue on the Jira side and
RepositoryFile on the GitHub side. Everything upstream of it may know about
Slack's payload shape - `type`, `subtype`, `thread_ts`, `bot_id`, `blocks`,
`reactions`, `files` - and everything downstream of it must not.

By the time a message is a SlackMessage, four things are already true and the
chunker relies on all of them: it is a real message rather than a channel event,
it is not a thread reply, it has a timestamp, and `text` is non-empty. The
parser is where those decisions live.

The field list is deliberately tiny. Slack returns reactions, files,
attachments, block structure, edit history, pinned state, a client message id
and more, and none of them earn a place in a chunk yet.
"""

from pydantic import BaseModel


class SlackMessage(BaseModel):
    """A single Slack channel message, reduced to what a chunk needs."""

    # Comes from the channel the run resolved, not from the payload. Every
    # message in one run therefore reports the same channel, which is the
    # containment guarantee this pipeline is built around.
    channel_id: str

    # Slack's own message timestamp, e.g. "1754810101.100100". Kept as the
    # string Slack sent and never converted to a float or a datetime: within a
    # channel this value *is* the message's identity, it is half of the permalink
    # to it, and re-formatting it - dropping a trailing zero, rounding the
    # microseconds - would quietly break the ability to point back at the
    # message. Sorting parses it; storing it does not.
    message_ts: str

    # Who said it, as an opaque Slack id: `user` for a person, `bot_id` for an
    # app that posted. Not resolved to a name - that would cost a users.info
    # call per distinct author and put personal data in a chunk. Optional
    # because a message can carry neither.
    author_id: str | None = None

    # What was actually said, normalised but not rewritten. Mentions like
    # <@U123>, channel references and :emoji_name: are left exactly as the
    # author typed them. Never empty - a message whose text came out blank is
    # dropped by the parser rather than arriving here.
    text: str
