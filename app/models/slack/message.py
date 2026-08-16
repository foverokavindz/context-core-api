from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.common.permission_scope import PermissionScope


class SlackMessage(PermissionScope):

    channel_id: str
    message_ts: str

    author_id: str | None = None

    text: str

    external_id: str # = "channel_id:message_ts". Neither half identifies a message on its own - a timestamp is only unique within its channel - so the pair is joined into the one column resources gives it
    title: str | None = None # a Slack message has no title, and one cut from the first few words of its text would read like a real one without being one
    version_key: str | None = None # Slack records that a message was edited, but not on the fields this connector reads
    resource_type: ResourceType = ResourceType.SLACK_MESSAGE
