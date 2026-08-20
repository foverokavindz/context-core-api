"""Makes a plan executable. Corrects what it can rather than failing a request."""

import logging

from app.models.retrieval.retrieval_plan import (
    MAX_TOP_K,
    MIN_TOP_K,
    RetrievalPlan,
    RetrievalStep,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 4


def repair_plan(plan: RetrievalPlan) -> RetrievalPlan:
    """repair_plan is the deterministic cleanup between "what the model said" and "what the executor can actually run."""

    # 1. Duplicate ids — two steps both called history, keep the first. Ids are how steps refer to each other; with two of them, depends_on: ["history"] means nothing.
    steps = _without_duplicate_ids(plan.steps)

    # 2. Too many steps — more than 4, keep the first 4. Every step is a search; the prompt asks for at most 4, this enforces it.
    steps = _within_the_step_limit(steps)

    # 3. Bad dependencies — drop any entry pointing at itself, or at a step that isn't in the list. Note this runs after 1 and 2, because those two can create the problem: if step 5 got trimmed away, anything depending on step 5 is now pointing at nothing.
    steps = [_with_usable_dependencies(step, steps) for step in steps]

    # 4. Cycles — history waits for code, code waits for history. Neither can ever start. Drop the one edge that closes the loop and keep the rest, so the ordering that was sensible survives.
    steps = _without_cycles(steps)

    # 5. top_k — clamp to 5–15. A top_k of 200 is a slow, useless search.
    steps = [_with_a_sensible_top_k(step) for step in steps]

    if not steps:
        logger.warning("The plan has no steps, and nothing can be retrieved for it")
    else:
        logger.info(
            "Planned %d step(s): %s",
            len(steps),
            ", ".join(step.source.value for step in steps),
        )

    return plan.model_copy(update={"steps": steps})


def _without_duplicate_ids(steps: list[RetrievalStep]) -> list[RetrievalStep]:
    kept: list[RetrievalStep] = []
    seen: set[str] = set()

    for step in steps:
        if step.id in seen:
            logger.warning("Dropped a second step calling itself %s", step.id)
            continue
        seen.add(step.id)
        kept.append(step)

    return kept


def _within_the_step_limit(steps: list[RetrievalStep]) -> list[RetrievalStep]:
    if len(steps) <= MAX_STEPS:
        return steps

    logger.warning(
        "Dropped %d step(s) past the limit of %d", len(steps) - MAX_STEPS, MAX_STEPS
    )
    return steps[:MAX_STEPS]


def _with_usable_dependencies(
    step: RetrievalStep, steps: list[RetrievalStep]
) -> RetrievalStep:
    """Dependencies on a step that is gone, or on this one, are removed."""
    present = {other.id for other in steps}
    kept = [
        dependency
        for dependency in step.depends_on
        if dependency != step.id and dependency in present
    ]

    if kept == step.depends_on:
        return step

    logger.warning("Dropped an unusable dependency of step %s", step.id)
    return step.model_copy(update={"depends_on": kept})


def _without_cycles(steps: list[RetrievalStep]) -> list[RetrievalStep]:
    """Every dependency that closes a cycle is removed, the rest are kept."""
    dependencies = {step.id: list(step.depends_on) for step in steps}
    settled: set[str] = set()

    def walk(step_id: str, being_walked: list[str]) -> None:
        being_walked.append(step_id)
        for dependency in list(dependencies[step_id]):
            if dependency in being_walked:
                logger.warning(
                    "Dropped the dependency of %s on %s: it closes a cycle",
                    step_id,
                    dependency,
                )
                dependencies[step_id].remove(dependency)
            elif dependency not in settled:
                walk(dependency, being_walked)
        being_walked.pop()
        settled.add(step_id)

    for step in steps:
        if step.id not in settled:
            walk(step.id, [])

    return [
        step.model_copy(update={"depends_on": dependencies[step.id]}) for step in steps
    ]


def _with_a_sensible_top_k(step: RetrievalStep) -> RetrievalStep:
    top_k = min(max(step.top_k, MIN_TOP_K), MAX_TOP_K)
    if top_k == step.top_k:
        return step

    logger.warning("Moved the top_k of step %s to %d", step.id, top_k)
    return step.model_copy(update={"top_k": top_k})
