"""Who is asking. Carried through retrieval, acted on by nobody yet."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AccessContext(BaseModel):

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    team_id: UUID
    department_id: UUID
