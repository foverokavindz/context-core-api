from app.models.chat.message import ChatMessage
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.retrieval.answering.answer_generator import AnswerGenerator
from app.retrieval.execution.executor import RetrievalExecutor
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.prompt_processing.processor import PromptProcessor


class RetrievalPipeline:

    def __init__(
        self,
        prompt_processor: PromptProcessor,
        planner: RetrievalPlanner,
        executor: RetrievalExecutor,
        answer_generator: AnswerGenerator | None = None,
    ) -> None:
        self.prompt_processor = prompt_processor
        self.planner = planner
        self.executor = executor
        self.answer_generator = answer_generator

    def run(
        self,
        query: str,
        access: AccessContext,
        history: list[ChatMessage] | None = None,
    ) -> RetrievalPipelineResult:

        # step 1: analyze the prompt to see if retrieval is needed
        analysis = self.prompt_processor.process(query=query, history=history)

        if not analysis.retrieval_required:
            # Nothing to search, but there is still something to say
            return RetrievalPipelineResult(
                analysis=analysis,
                answer=self._answer(analysis, None, history),
            )

        # step 2: plan what to retrieve
        plan = self.planner.plan(analysis)

        # step 3: execute the plan to get the sources
        execution = self.executor.execute(plan=plan, analysis=analysis, access=access)

        # step 4: generate the answer
        answer = self._answer(analysis, execution, history)

        # step 5: return the result of the pipeline
        return RetrievalPipelineResult(
            analysis=analysis,
            plan=plan,
            execution=execution,
            answer=answer,
        )

    def _answer(self, analysis, execution, history):
        """The answer, when there is a stage to write one.

        A pipeline built without a generator retrieves and stops: it still
        returns everything it found, with no answer over it. Returning nothing
        at all would leave the caller with a None where a result is promised.
        """
        if self.answer_generator is None:
            return None

        return self.answer_generator.generate(
            analysis=analysis, execution=execution, history=history
        )

