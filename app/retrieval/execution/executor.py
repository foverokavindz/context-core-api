import logging
from concurrent.futures import ThreadPoolExecutor

from app.core.exceptions import RetrievalExecutionError
from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.execution_result import RetrievalExecutionResult, StepExecutionResult
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.query_enrichment import QueryEnrichmentInput
from app.models.retrieval.retrieval_plan import RetrievalPlan, RetrievalStep
from app.retrieval.execution.query_enricher import QueryEnricher
from app.retrieval.retrievers.base import SourceRetriever

logger = logging.getLogger(__name__)

MAX_DEPENDENCY_RESULTS = 5


class RetrievalExecutor:
    """Runs the steps of one plan, in an order the plan itself implies."""

    def __init__(
        self,
        retrievers: dict[SourceType, SourceRetriever],
        query_enricher: QueryEnricher,
    ) -> None:
        self.retrievers = retrievers
        self.query_enricher = query_enricher

    def execute(
        self,
        plan: RetrievalPlan,
        analysis: PromptAnalysis,
        access: AccessContext,
    ) -> RetrievalExecutionResult:
        # step id, result
        completed: dict[str, StepExecutionResult] = {}
        remaining = list(plan.steps)

        while remaining:
            ready = _ready_steps(remaining, completed)

            if not ready:
                logger.error(
                    "%d step(s) of the plan can never run: %s",
                    len(remaining),
                    ", ".join(step.id for step in remaining),
                )
                raise RetrievalExecutionError(
                    "No executable retrieval steps remain."
                )

            for result in self._run(ready, analysis, completed, access):
                completed[result.step_id] = result

            remaining = [step for step in remaining if step.id not in completed]

        # Plan order, not completion order, so two runs of one plan read alike.
        return RetrievalExecutionResult(
            steps=[completed[step.id] for step in plan.steps]
        )

    def _run(
        self,
        ready: list[RetrievalStep],
        analysis: PromptAnalysis,
        completed: dict[str, StepExecutionResult],
        access: AccessContext,
    ) -> list[StepExecutionResult]:

        if len(ready) == 1:
            return [self._execute_step(ready[0], analysis, completed, access)]

        logger.info(
            "Running %d independent step(s) together: %s",
            len(ready),
            ", ".join(step.id for step in ready),
        )
        with ThreadPoolExecutor(max_workers=len(ready)) as pool:
            return list(
                pool.map(
                    lambda step: self._execute_step(
                        step, analysis, completed, access
                    ),
                    ready,
                )
            )

    def _execute_step(
        self,
        step: RetrievalStep,
        analysis: PromptAnalysis,
        completed: dict[str, StepExecutionResult],
        access: AccessContext,
    ) -> StepExecutionResult:
        
        query = self._query_for(step, analysis, completed)

        retriever = self.retrievers.get(step.source)
        if retriever is None:
            logger.error("No retriever is registered for %s", step.source.value)
            raise RetrievalExecutionError()

        try:
            results = retriever.retrieve(query=query, top_k=step.top_k, access=access)

        except Exception as exc:
            logger.error(
                "Step %s failed against %s: %s",
                step.id,
                step.source.value,
                type(exc).__name__,
            )
            raise RetrievalExecutionError() from exc

        logger.info(
            "Step %s returned %d result(s) from %s",
            step.id,
            len(results),
            step.source.value,
        )
        return StepExecutionResult(
            step_id=step.id,
            source=step.source,
            goal=step.goal,
            executed_query=query,
            results=results,
        )

    def _query_for(
        self,
        step: RetrievalStep,
        analysis: PromptAnalysis,
        completed: dict[str, StepExecutionResult],
    ) -> str:
        """The query to run for this step, enriched with dependency results if any."""

        # If the step has no dependencies, we can just use its query or the improved query from the analysis.
        if not step.depends_on:
            return step.query or analysis.improved_query

        # If the step has dependencies, we need to enrich its query with the results of those dependencies. 
        dependency_results = [
            result
            for dependency in step.depends_on
            for result in completed[dependency].results[:MAX_DEPENDENCY_RESULTS] # pass only the top few results to the enricher
        ]

        enriched = self.query_enricher.enrich(
            QueryEnrichmentInput(
                original_query=analysis.resolved_query,
                base_query=step.query,
                next_step_goal=step.goal,
                dependency_results=dependency_results,
            )
        )
        return enriched.query


def _ready_steps(
    steps: list[RetrievalStep], completed: dict[str, StepExecutionResult]
) -> list[RetrievalStep]:
    """The steps that can run now, in plan order.
    """
    
    return [
        step
        for step in steps
        if all(dependency in completed for dependency in step.depends_on)
    ]
