from pydantic import BaseModel, ConfigDict, Field

# The one place a chat model is named. Nothing calls an LLM yet - this is
# request configuration the retrieval pipeline will read once it exists.
DEFAULT_CHAT_MODEL = "gpt-4o-mini"


class LLMConfig(BaseModel):

    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_CHAT_MODEL
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
