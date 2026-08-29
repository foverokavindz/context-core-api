from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.common.permission_scope import PermissionScope


class SlackMessage(PermissionScope):

    channel_id: str
    message_ts: str

    author_id: str | None = None

    text: str

    external_id: str 
    title: str | None = None 
    version_key: str | None = None 
    resource_type: ResourceType = ResourceType.SLACK_MESSAGE
