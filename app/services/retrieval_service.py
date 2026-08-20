from app.models.chat.message import ChatMessage
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.retrieval.llm import build_chat_model
from app.retrieval.pipeline import RetrievalPipeline
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalService:

    def __init__(self, pipeline: RetrievalPipeline) -> None:
        self.pipeline = pipeline

    def start(
        self, query: str, history: list[ChatMessage] | None = None
    ) -> RetrievalPipelineResult:
        return self.pipeline.run(query=query, history=history)


def build_retrieval_service() -> RetrievalService:
    """Wire the default stack together.

    A function, not a module-level instance: `build_chat_model` reads the
    environment, so building this at import time would make importing the module
    require credentials. Both stages share the one model it returns.
    """

    llm = build_chat_model()
    return RetrievalService(
        RetrievalPipeline(PromptProcessor(llm), RetrievalPlanner(llm))
    )
