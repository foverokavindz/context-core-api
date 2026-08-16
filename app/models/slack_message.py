from app.models.permission_scope import PermissionScope


class SlackMessage(PermissionScope):

    channel_id: str
    message_ts: str

    author_id: str | None = None

    text: str
