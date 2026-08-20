from app.models.chat.message import ChatMessage
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalPipeline:

    def __init__(
        self, prompt_processor: PromptProcessor, planner: RetrievalPlanner
    ) -> None:
        self.prompt_processor = prompt_processor
        self.planner = planner

    def run(
        self, query: str, history: list[ChatMessage] | None = None
    ) -> RetrievalPipelineResult:
        analysis = self.prompt_processor.process(query=query, history=history)

        # Thanks, an aside, or a request to repeat something: nothing to plan.
        if not analysis.retrieval_required:
            return RetrievalPipelineResult(analysis=analysis)

        plan = self.planner.plan(analysis)

        return RetrievalPipelineResult(
            analysis=analysis, plan=plan
        )
