"""Tests for the planning stage.

The model is faked at the same seam the prompt processor's tests use -
`with_structured_output` - so the real prompt, the real serialization of the
analysis and the real repair pass all run, with no network and no credentials.

The planner does not decide what a plan says; the model does. So the tests about
sources and dependencies check that a model's answer survives the round trip,
and the ones worth more are about what is sent, and about the repair pass - the
only part of this stage that is deterministic.
"""

import logging

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.exceptions import LLMError
from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.ontology.entity_type import EntityType
from app.models.retrieval.ontology.information_need import InformationNeed
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.prompt_analysis import ExtractedEntity, PromptAnalysis
from app.models.retrieval.retrieval_plan import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    MIN_TOP_K,
    RetrievalPlan,
    RetrievalStep,
)
from app.retrieval.planning.planner import RetrievalPlanner
from app.retrieval.planning.validator import MAX_STEPS, repair_plan

GOAL = "Understand previous authentication work, implementation and architecture."


# ------------------------------------------------------------------- fakes


class FakeStructuredModel:
    """What `with_structured_output` hands back: something with `invoke`."""

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list] = []

    def invoke(self, messages: list) -> object:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeChatModel:
    """A chat model that never leaves the process."""

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.structured = FakeStructuredModel(answer, error)
        self.schema: type | None = None

    def with_structured_output(self, schema: type) -> FakeStructuredModel:
        self.schema = schema
        return self.structured


def analysis(**overrides) -> PromptAnalysis:
    """A valid analysis, with any field replaced."""
    fields = {
        "resolved_query": "Understand the previous authentication work.",
        "intent": QueryIntent.BROAD_CONTEXT,
        "entities": [ExtractedEntity(type=EntityType.FEATURE, value="authentication")],
        "information_needs": [
            InformationNeed.PREVIOUS_WORK,
            InformationNeed.IMPLEMENTATION,
            InformationNeed.ARCHITECTURE,
        ],
        "improved_query": "authentication previous changes implementation architecture",
    }
    return PromptAnalysis(**{**fields, **overrides})


def a_step(step_id: str, source: SourceType, **overrides) -> RetrievalStep:
    """A valid step, with any field replaced."""
    fields = {
        "id": step_id,
        "source": source,
        "goal": f"Find something in {source.value}",
        "query": f"{step_id} query",
    }
    return RetrievalStep(**{**fields, **overrides})


def a_plan(*steps: RetrievalStep) -> RetrievalPlan:
    return RetrievalPlan(goal=GOAL, steps=list(steps))


def broad_plan() -> RetrievalPlan:
    """Jira, then the code it points at, and architecture alongside both."""
    return a_plan(
        a_step(
            "authentication_history",
            SourceType.JIRA,
            query="authentication previous changes requirements",
            top_k=8,
        ),
        a_step(
            "authentication_implementation",
            SourceType.GITHUB,
            query=None,
            depends_on=["authentication_history"],
        ),
        a_step(
            "authentication_architecture",
            SourceType.CONFLUENCE,
            query="authentication architecture design flow",
            top_k=8,
        ),
    )


def planner_for(answer: object = None, error: Exception | None = None):
    """A planner over a fake model, plus the fake, for asserting on both."""
    model = FakeChatModel(answer, error)
    return RetrievalPlanner(model), model


def sources_of(plan: RetrievalPlan) -> list[SourceType]:
    return [step.source for step in plan.steps]


def ids_of(plan: RetrievalPlan) -> list[str]:
    return [step.id for step in plan.steps]


def has_a_cycle(plan: RetrievalPlan) -> bool:
    """Whether any step can still reach itself through its dependencies."""
    dependencies = {step.id: step.depends_on for step in plan.steps}

    def reaches(start: str, step_id: str, seen: set[str]) -> bool:
        for dependency in dependencies.get(step_id, []):
            if dependency == start:
                return True
            if dependency not in seen:
                seen.add(dependency)
                if reaches(start, dependency, seen):
                    return True
        return False

    return any(reaches(step.id, step.id, set()) for step in plan.steps)


# ------------------------------------------------------- what comes back out


def test_the_plan_schema_is_what_the_model_is_asked_for() -> None:
    _, model = planner_for(broad_plan())
    assert model.schema is RetrievalPlan


def test_an_analysis_becomes_the_plan_the_model_answered_with() -> None:
    planner, _ = planner_for(broad_plan())

    result = planner.plan(analysis())

    assert isinstance(result, RetrievalPlan)
    assert result.goal == GOAL


def test_a_broad_request_can_be_planned_across_three_sources() -> None:
    planner, _ = planner_for(broad_plan())

    result = planner.plan(analysis())

    assert sources_of(result) == [
        SourceType.JIRA,
        SourceType.GITHUB,
        SourceType.CONFLUENCE,
    ]


def test_a_step_may_wait_for_another_and_carry_no_query_of_its_own() -> None:
    """The Executor writes that query once the step it waits on has run."""
    planner, _ = planner_for(broad_plan())

    github = planner.plan(analysis()).steps[1]

    assert github.depends_on == ["authentication_history"]
    assert github.query is None


def test_a_step_that_needs_nothing_first_is_planned_independently() -> None:
    planner, _ = planner_for(broad_plan())

    result = planner.plan(analysis())

    assert result.steps[0].depends_on == []
    assert result.steps[2].depends_on == []


def test_an_explicitly_named_source_can_be_planned_on_its_own() -> None:
    planner, _ = planner_for(
        RetrievalPlan(
            goal="Understand why Redis was selected.",
            steps=[
                a_step(
                    "redis_decision",
                    SourceType.SLACK,
                    query="Redis selection decision rationale",
                    top_k=8,
                )
            ],
        )
    )

    result = planner.plan(
        analysis(
            intent=QueryIntent.DECISION_HISTORY,
            entities=[ExtractedEntity(type=EntityType.TECHNOLOGY, value="Redis")],
            information_needs=[InformationNeed.DECISIONS, InformationNeed.RATIONALE],
            explicit_sources=[SourceType.SLACK],
        )
    )

    assert sources_of(result) == [SourceType.SLACK]


# ------------------------------------------------------------- what goes in


def test_the_system_prompt_comes_first_and_the_analysis_last() -> None:
    planner, model = planner_for(broad_plan())

    planner.plan(analysis())

    sent = model.structured.calls[0]
    assert [type(message) for message in sent] == [SystemMessage, HumanMessage]
    assert "retrieval planner" in sent[0].content


def test_the_whole_analysis_reaches_the_model_as_json() -> None:
    planner, model = planner_for(broad_plan())
    given = analysis(explicit_sources=[SourceType.JIRA])

    planner.plan(given)

    sent = model.structured.calls[0][-1].content
    assert sent == given.model_dump_json(indent=2)
    assert "BROAD_CONTEXT" in sent
    assert "PREVIOUS_WORK" in sent
    assert "JIRA" in sent


def test_an_analysis_containing_braces_is_sent_not_treated_as_a_template() -> None:
    """The prompt is a template; the analysis - JSON - must never be one."""
    planner, model = planner_for(broad_plan())
    given = analysis(resolved_query="What does {user_id} mean in the middleware?")

    planner.plan(given)

    assert "{user_id}" in model.structured.calls[0][-1].content


# --------------------------------------------------------- what goes wrong


def test_a_failing_model_does_not_leak_what_it_said() -> None:
    planner, _ = planner_for(error=RuntimeError("api key sk-secret is invalid"))

    with pytest.raises(LLMError) as caught:
        planner.plan(analysis())

    assert caught.value.status_code == 502
    assert "sk-secret" not in caught.value.message


def test_a_failing_model_does_not_leak_what_it_said_into_the_log(caplog) -> None:
    planner, _ = planner_for(error=RuntimeError("api key sk-secret is invalid"))

    with caplog.at_level(logging.ERROR), pytest.raises(LLMError):
        planner.plan(analysis())

    assert "RuntimeError" in caplog.text
    assert "sk-secret" not in caplog.text


@pytest.mark.parametrize("answer", [None, "JIRA", {"steps": []}])
def test_an_answer_that_is_not_a_plan_fails_rather_than_being_guessed_at(
    answer,
) -> None:
    planner, _ = planner_for(answer)

    with pytest.raises(LLMError):
        planner.plan(analysis())


def test_the_question_itself_is_never_logged(caplog) -> None:
    """The rule the rest of the pipeline follows: nothing a person typed."""
    planner, _ = planner_for(broad_plan())
    given = analysis(resolved_query="Why does the payroll export drop nulls?")

    with caplog.at_level(logging.DEBUG):
        planner.plan(given)

    assert given.resolved_query not in caplog.text


# ----------------------------------------------------------- the repair pass


def test_a_usable_plan_is_left_alone() -> None:
    plan = broad_plan()

    assert repair_plan(plan) == plan


def test_a_repaired_plan_is_what_the_planner_hands_back() -> None:
    planner, _ = planner_for(
        a_plan(a_step("history", SourceType.JIRA), a_step("history", SourceType.SLACK))
    )

    assert sources_of(planner.plan(analysis())) == [SourceType.JIRA]


def test_a_second_step_calling_itself_the_same_thing_is_dropped() -> None:
    plan = a_plan(
        a_step("history", SourceType.JIRA),
        a_step("history", SourceType.SLACK),
        a_step("code", SourceType.GITHUB),
    )

    result = repair_plan(plan)

    assert ids_of(result) == ["history", "code"]
    assert result.steps[0].source is SourceType.JIRA


def test_a_step_may_not_depend_on_itself() -> None:
    plan = a_plan(a_step("history", SourceType.JIRA, depends_on=["history"]))

    assert repair_plan(plan).steps[0].depends_on == []


def test_a_dependency_on_a_step_that_does_not_exist_is_dropped() -> None:
    plan = a_plan(
        a_step("history", SourceType.JIRA),
        a_step("code", SourceType.GITHUB, depends_on=["history", "nothing_like_it"]),
    )

    assert repair_plan(plan).steps[1].depends_on == ["history"]


def test_two_steps_waiting_on_each_other_stop_waiting() -> None:
    plan = a_plan(
        a_step("history", SourceType.JIRA, depends_on=["code"]),
        a_step("code", SourceType.GITHUB, depends_on=["history"]),
    )

    result = repair_plan(plan)

    assert not has_a_cycle(result)
    assert [] in [step.depends_on for step in result.steps]


def test_a_longer_ring_of_steps_is_broken_too() -> None:
    plan = a_plan(
        a_step("history", SourceType.JIRA, depends_on=["docs"]),
        a_step("code", SourceType.GITHUB, depends_on=["history"]),
        a_step("docs", SourceType.CONFLUENCE, depends_on=["code"]),
    )

    assert not has_a_cycle(repair_plan(plan))


def test_more_steps_than_the_limit_are_cut_back_to_it() -> None:
    plan = a_plan(
        *(a_step(f"step_{index}", SourceType.JIRA) for index in range(MAX_STEPS + 3))
    )

    result = repair_plan(plan)

    assert ids_of(result) == [f"step_{index}" for index in range(MAX_STEPS)]


def test_a_dependency_on_a_step_that_was_cut_back_goes_with_it() -> None:
    last = f"step_{MAX_STEPS}"
    plan = a_plan(
        a_step("step_0", SourceType.JIRA, depends_on=[last]),
        *(
            a_step(f"step_{index}", SourceType.GITHUB)
            for index in range(1, MAX_STEPS + 1)
        ),
    )

    result = repair_plan(plan)

    assert last not in ids_of(result)
    assert result.steps[0].depends_on == []


@pytest.mark.parametrize(
    ("given", "expected"), [(0, MIN_TOP_K), (1, MIN_TOP_K), (99, MAX_TOP_K)]
)
def test_a_top_k_outside_the_range_is_moved_into_it(given: int, expected: int) -> None:
    plan = a_plan(a_step("history", SourceType.JIRA, top_k=given))

    assert repair_plan(plan).steps[0].top_k == expected


def test_a_step_that_says_nothing_about_top_k_gets_the_default() -> None:
    assert a_step("history", SourceType.JIRA).top_k == DEFAULT_TOP_K


def test_an_empty_plan_is_returned_and_said_to_be_empty(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = repair_plan(RetrievalPlan(goal=GOAL))

    assert result.steps == []
    assert "no steps" in caplog.text
