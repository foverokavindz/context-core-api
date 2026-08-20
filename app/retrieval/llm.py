import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from app.core.exceptions import LLMConfigurationError
from app.models.chat.llm_config import LLMConfig

logger = logging.getLogger(__name__)

load_dotenv()


def build_chat_model(config: LLMConfig | None = None) -> ChatOpenAI:
    """Build the chat model"""

    base_url = os.getenv("AZURE_OPENAI_BASE_URL")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_BASE_URL", base_url),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_CHAT_DEPLOYMENT", deployment),
        )
        if not value
    ]
    if missing:
        logger.error("The chat model is not configured: missing %s", ", ".join(missing))
        raise LLMConfigurationError()

    config = config or LLMConfig()

    logger.info("Reasoning with chat deployment %s", deployment)
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=deployment,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
