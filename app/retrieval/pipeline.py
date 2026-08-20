from app.models.chat.message import ChatMessage
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.retrieval.execution.executor import RetrievalExecutor
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalPipeline:

    def __init__(
        self,
        prompt_processor: PromptProcessor,
        planner: RetrievalPlanner,
        executor: RetrievalExecutor,
    ) -> None:
        self.prompt_processor = prompt_processor
        self.planner = planner
        self.executor = executor

    def run(
        self,
        query: str,
        access: AccessContext,
        history: list[ChatMessage] | None = None,
    ) -> RetrievalPipelineResult:
        analysis = self.prompt_processor.process(query=query, history=history)

        if not analysis.retrieval_required:
            return RetrievalPipelineResult(analysis=analysis)

        plan = self.planner.plan(analysis)

        execution = self.executor.execute(
            plan=plan, analysis=analysis, access=access
        )

        return RetrievalPipelineResult(
            analysis=analysis, plan=plan, execution=execution
        )
