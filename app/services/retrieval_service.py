from app.models.chat.message import ChatMessage
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.retrieval.llm import build_chat_model
from app.retrieval.pipeline import RetrievalPipeline
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalService:

    def __init__(self, pipeline: RetrievalPipeline) -> None:
        self.pipeline = pipeline

    def start(self, query: str, history: list[ChatMessage] | None = None) -> PromptAnalysis:
        return self.pipeline.run(query=query, history=history)


def build_retrieval_service() -> RetrievalService:
    """Wire the default stack together.

    A function, not a module-level instance: `build_chat_model` reads the
    environment, so building this at import time would make importing the module
    require credentials.
    """
    return RetrievalService(RetrievalPipeline(PromptProcessor(build_chat_model())))
