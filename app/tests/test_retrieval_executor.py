"""Tests for the executor, the stage that runs a plan and decides nothing.

Two seams are faked and nothing else is: the retrievers, so no vector search or
database is involved, and the query enricher, so no chat model is. What is left
is the only thing this stage actually owns - which steps can run when, which
retriever each one reaches, what query it is finally searched with, and what is
recorded about it afterwards.

The concurrency test is the one that needs explaining. Two retrievers meet at a
`threading.Barrier(2)`, which only releases once both have arrived; if the
executor ran independent steps one after another the first would wait for a
second arrival that could never come, and the test would time out rather than
fail. That is deliberate - it asserts the steps genuinely overlap rather than
asserting something about how they were scheduled.

The retriever stubs in `app/retrieval/retrievers/` find nothing yet, so the
fakes here are what stand in for a source with content in it.
"""

import threading
from uuid import uuid4

import pytest

from app.core.exceptions import RetrievalExecutionError
from app.entities.data_sources.source_type import SourceType
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.query_enrichment import EnrichedQuery, QueryEnrichmentInput
from app.models.retrieval.retrieval_plan import RetrievalPlan, RetrievalStep
from app.models.retrieval.retrieval_result import RetrievalResult
from app.retrieval.execution.executor import MAX_DEPENDENCY_RESULTS, RetrievalExecutor

ENRICHED = "JWT expiration refresh token AuthMiddleware authentication"

ACCESS = AccessContext(user_id=uuid4(), team_id=uuid4(), department_id=uuid4())


# ------------------------------------------------------------------- fakes


class RecordingRetriever:
    """Records how it was asked and answers with a fixed list."""

    def __init__(self, results: list[RetrievalResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple] = []

    def retrieve(self, query, top_k, access) -> list[RetrievalResult]:
        self.calls.append((query, top_k, access))
        return self.results


class FailingRetriever:
    """The source that is down."""

    def retrieve(self, query, top_k, access) -> list[RetrievalResult]:
        raise RuntimeError("connection to secret-host.internal refused")


class FakeQueryEnricher:
    """Records what it was given and answers with one fixed query."""

    def __init__(self, query: str = ENRICHED) -> None:
        self.query = query
        self.calls: list[QueryEnrichmentInput] = []

    def enrich(self, request: QueryEnrichmentInput) -> EnrichedQuery:
        self.calls.append(request)
        return EnrichedQuery(query=self.query)


# ------------------------------------------------------------- the fixtures


def analysis(**overrides) -> PromptAnalysis:
    fields = {
        "resolved_query": "Understand the previous authentication work.",
        "intent": QueryIntent.BROAD_CONTEXT,
        "improved_query": "authentication previous changes implementation",
    }
    return PromptAnalysis(**{**fields, **overrides})


def a_step(step_id: str, source: SourceType, **overrides) -> RetrievalStep:
    fields = {
        "id": step_id,
        "source": source,
        "goal": f"Find something in {source.value}",
        "query": f"{step_id} query",
        "top_k": 8,
    }
    return RetrievalStep(**{**fields, **overrides})


def a_plan(*steps: RetrievalStep) -> RetrievalPlan:
    return RetrievalPlan(goal="Understand the authentication work.", steps=list(steps))


def a_result(content: str) -> RetrievalResult:
    return RetrievalResult(chunk_id=uuid4(), content=content, source=SourceType.JIRA)


def an_executor(retrievers: dict, enricher: FakeQueryEnricher | None = None):
    """An executor over fakes, plus the enricher, for asserting on both."""
    enricher = enricher or FakeQueryEnricher()
    return RetrievalExecutor(retrievers, enricher), enricher


def executed(executor, plan: RetrievalPlan, given_analysis=None):
    return executor.execute(
        plan=plan, analysis=given_analysis or analysis(), access=ACCESS
    )


# --------------------------------------------------------- independent steps


def test_two_steps_that_wait_for_nothing_are_both_run() -> None:
    jira, confluence = RecordingRetriever(), RecordingRetriever()
    executor, _ = an_executor(
        {SourceType.JIRA: jira, SourceType.CONFLUENCE: confluence}
    )

    result = executed(
        executor,
        a_plan(
            a_step("history", SourceType.JIRA),
            a_step("architecture", SourceType.CONFLUENCE),
        ),
    )

    assert [step.step_id for step in result.steps] == ["history", "architecture"]
    assert len(jira.calls) == 1
    assert len(confluence.calls) == 1


def test_independent_steps_are_run_at_the_same_time_rather_than_in_turn() -> None:
    """Both retrievers meet at a barrier, which one at a time can never reach."""
    gate = threading.Barrier(2, timeout=5)

    class BarrierRetriever:
        def retrieve(self, query, top_k, access):
            gate.wait()
            return []

    executor, _ = an_executor(
        {
            SourceType.JIRA: BarrierRetriever(),
            SourceType.CONFLUENCE: BarrierRetriever(),
        }
    )

    result = executed(
        executor,
        a_plan(
            a_step("history", SourceType.JIRA),
            a_step("architecture", SourceType.CONFLUENCE),
        ),
    )

    assert len(result.steps) == 2


def test_an_independent_step_is_searched_with_the_query_the_planner_wrote() -> None:
    jira = RecordingRetriever()
    executor, enricher = an_executor({SourceType.JIRA: jira})

    result = executed(executor, a_plan(a_step("history", SourceType.JIRA)))

    assert jira.calls[0][0] == "history query"
    assert result.steps[0].executed_query == "history query"
    assert enricher.calls == []


def test_an_independent_step_with_no_query_falls_back_to_the_analysis() -> None:
    """The planner should not do this. Nothing enforces it, so there is an answer."""
    jira = RecordingRetriever()
    executor, enricher = an_executor({SourceType.JIRA: jira})

    executed(executor, a_plan(a_step("history", SourceType.JIRA, query=None)))

    assert jira.calls[0][0] == "authentication previous changes implementation"
    assert enricher.calls == []


def test_a_step_is_searched_with_the_top_k_and_access_it_was_given() -> None:
    jira = RecordingRetriever()
    executor, _ = an_executor({SourceType.JIRA: jira})

    executed(executor, a_plan(a_step("history", SourceType.JIRA, top_k=12)))

    _, top_k, access = jira.calls[0]
    assert top_k == 12
    assert access is ACCESS


# ----------------------------------------------------------- dependent steps


def dependent_plan() -> RetrievalPlan:
    return a_plan(
        a_step("history", SourceType.JIRA),
        a_step(
            "implementation",
            SourceType.GITHUB,
            query=None,
            depends_on=["history"],
        ),
    )


def test_a_step_waits_for_the_one_it_depends_on() -> None:
    order: list[str] = []

    class OrderingRetriever:
        def __init__(self, name: str) -> None:
            self.name = name

        def retrieve(self, query, top_k, access):
            order.append(self.name)
            return []

    executor, _ = an_executor(
        {
            SourceType.JIRA: OrderingRetriever("jira"),
            SourceType.GITHUB: OrderingRetriever("github"),
        }
    )

    executed(executor, dependent_plan())

    assert order == ["jira", "github"]


def test_what_a_dependency_found_is_what_the_enricher_is_given() -> None:
    found = [a_result("JWT expiry"), a_result("refresh token rotation")]
    executor, enricher = an_executor(
        {
            SourceType.JIRA: RecordingRetriever(found),
            SourceType.GITHUB: RecordingRetriever(),
        }
    )

    executed(executor, dependent_plan())

    assert len(enricher.calls) == 1
    request = enricher.calls[0]
    assert request.dependency_results == found
    assert request.next_step_goal == "Find something in GITHUB"
    assert request.original_query == "Understand the previous authentication work."


def test_the_enriched_query_is_what_reaches_the_dependent_retriever() -> None:
    github = RecordingRetriever()
    executor, _ = an_executor(
        {SourceType.JIRA: RecordingRetriever(), SourceType.GITHUB: github}
    )

    result = executed(executor, dependent_plan())

    assert github.calls[0][0] == ENRICHED
    assert result.steps[1].executed_query == ENRICHED


def test_a_query_the_planner_already_wrote_is_offered_for_improvement() -> None:
    """A dependent step's query is a starting point, not a final answer."""
    github = RecordingRetriever()
    executor, enricher = an_executor(
        {SourceType.JIRA: RecordingRetriever(), SourceType.GITHUB: github}
    )
    plan = a_plan(
        a_step("history", SourceType.JIRA),
        a_step(
            "implementation",
            SourceType.GITHUB,
            query="authentication implementation",
            depends_on=["history"],
        ),
    )

    executed(executor, plan)

    assert enricher.calls[0].base_query == "authentication implementation"
    assert github.calls[0][0] == ENRICHED


def test_only_the_first_few_results_of_a_dependency_are_enriched_from() -> None:
    found = [a_result(f"result {index}") for index in range(MAX_DEPENDENCY_RESULTS + 4)]
    executor, enricher = an_executor(
        {
            SourceType.JIRA: RecordingRetriever(found),
            SourceType.GITHUB: RecordingRetriever(),
        }
    )

    executed(executor, dependent_plan())

    assert enricher.calls[0].dependency_results == found[:MAX_DEPENDENCY_RESULTS]


def test_a_step_depending_on_two_others_is_enriched_from_both() -> None:
    executor, enricher = an_executor(
        {
            SourceType.JIRA: RecordingRetriever([a_result("from jira")]),
            SourceType.SLACK: RecordingRetriever([a_result("from slack")]),
            SourceType.GITHUB: RecordingRetriever(),
        }
    )
    plan = a_plan(
        a_step("history", SourceType.JIRA),
        a_step("decisions", SourceType.SLACK),
        a_step(
            "implementation",
            SourceType.GITHUB,
            query=None,
            depends_on=["history", "decisions"],
        ),
    )

    executed(executor, plan)

    contents = [result.content for result in enricher.calls[0].dependency_results]
    assert contents == ["from jira", "from slack"]


# ------------------------------------------------------- choosing a retriever


@pytest.mark.parametrize("source", list(SourceType))
def test_a_step_reaches_the_retriever_for_its_own_source_and_no_other(
    source: SourceType,
) -> None:
    retrievers = {member: RecordingRetriever() for member in SourceType}
    executor, _ = an_executor(retrievers)

    executed(executor, a_plan(a_step("step", source)))

    assert len(retrievers[source].calls) == 1
    assert all(
        retrievers[other].calls == [] for other in SourceType if other is not source
    )


def test_a_source_with_no_retriever_fails_rather_than_being_skipped() -> None:
    executor, _ = an_executor({SourceType.JIRA: RecordingRetriever()})

    with pytest.raises(RetrievalExecutionError) as caught:
        executed(executor, a_plan(a_step("implementation", SourceType.GITHUB)))

    assert caught.value.status_code == 500


# ---------------------------------------------------------- what is recorded


def test_the_plan_is_left_exactly_as_the_planner_wrote_it() -> None:
    """The runtime query lives on the result, never back on the step."""
    plan = dependent_plan()
    executor, _ = an_executor(
        {
            SourceType.JIRA: RecordingRetriever(),
            SourceType.GITHUB: RecordingRetriever(),
        }
    )

    executed(executor, plan)

    assert plan.steps[1].query is None
    assert plan.steps[0].query == "history query"


def test_a_step_result_records_what_the_step_was_for() -> None:
    found = [a_result("JWT expiry")]
    executor, _ = an_executor({SourceType.JIRA: RecordingRetriever(found)})

    result = executed(executor, a_plan(a_step("history", SourceType.JIRA)))

    step = result.steps[0]
    assert step.step_id == "history"
    assert step.source is SourceType.JIRA
    assert step.goal == "Find something in JIRA"
    assert step.results == found


def test_results_come_back_in_the_order_the_plan_named_them() -> None:
    """Not the order they finished in, so one plan reads alike twice."""
    executor, _ = an_executor({member: RecordingRetriever() for member in SourceType})
    plan = a_plan(
        a_step("architecture", SourceType.CONFLUENCE),
        a_step("history", SourceType.JIRA),
        a_step("decisions", SourceType.SLACK),
    )

    result = executed(executor, plan)

    assert [step.step_id for step in result.steps] == [
        "architecture",
        "history",
        "decisions",
    ]


def test_a_plan_with_no_steps_executes_to_nothing() -> None:
    executor, _ = an_executor({})

    result = executed(executor, a_plan())

    assert result.steps == []


# --------------------------------------------------------- what goes wrong


def test_a_dependency_that_can_never_complete_fails_instead_of_looping() -> None:
    """`repair_plan` should make this impossible. If it ever does not, this stops."""
    executor, _ = an_executor({SourceType.GITHUB: RecordingRetriever()})
    plan = a_plan(
        a_step("implementation", SourceType.GITHUB, depends_on=["never_planned"])
    )

    with pytest.raises(RetrievalExecutionError) as caught:
        executed(executor, plan)

    assert "No executable retrieval steps remain." in caught.value.message


def test_a_step_that_can_run_does_not_rescue_one_that_cannot() -> None:
    executor, _ = an_executor(
        {
            SourceType.JIRA: RecordingRetriever(),
            SourceType.GITHUB: RecordingRetriever(),
        }
    )
    plan = a_plan(
        a_step("history", SourceType.JIRA),
        a_step("implementation", SourceType.GITHUB, depends_on=["never_planned"]),
    )

    with pytest.raises(RetrievalExecutionError):
        executed(executor, plan)


def test_a_retriever_that_fails_fails_the_whole_execution() -> None:
    executor, _ = an_executor({SourceType.JIRA: FailingRetriever()})

    with pytest.raises(RetrievalExecutionError) as caught:
        executed(executor, a_plan(a_step("history", SourceType.JIRA)))

    assert caught.value.status_code == 500


def test_a_failing_retriever_leaks_nothing_it_said_into_the_message_or_the_log(
    caplog,
) -> None:
    executor, _ = an_executor({SourceType.JIRA: FailingRetriever()})

    with caplog.at_level("ERROR"):
        with pytest.raises(RetrievalExecutionError) as caught:
            executed(executor, a_plan(a_step("history", SourceType.JIRA)))

    assert "secret-host.internal" not in caught.value.message
    assert "secret-host.internal" not in caplog.text
    assert "RuntimeError" in caplog.text
