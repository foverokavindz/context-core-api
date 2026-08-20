from app.models.chat.message import ChatMessage
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalPipeline:

    def __init__(self, prompt_processor: PromptProcessor) -> None:
        self.prompt_processor = prompt_processor

    def run(
        self, query: str, history: list[ChatMessage] | None = None
    ) -> PromptAnalysis:
        return self.prompt_processor.process(query=query, history=history)
