"""Tests for the vector query, the only place retrieval decides who may read what.

There is no database here and none is faked. The statement is a value before it
is a query, so it is built and then compiled against the PostgreSQL dialect with
its parameters inlined, and the assertions read the SQL that a server would have
been sent. That is the whole point: the two permission filters are the security
boundary of retrieval, and a test that stubbed them out would prove nothing about
the SQL actually emitted.

`literal_binds` is what makes "this team's id is in the WHERE clause and no other
team's id appears anywhere" an assertion rather than a hope - the ids are in the
text instead of hidden behind placeholders.

The mapping is tested separately and directly, over a hand-built row, because
`_to_result` is a pure function of one row and needs neither a session nor a
statement to exercise.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_access_scope import (
    ResourceAccessScope,
)
from app.entities.knowledge_sources.resource_type import ResourceType
from app.ingestion.embedding_service import EMBEDDING_DIMENSIONS
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.retrieval_result import RetrievalResult
from app.repository.chunk_repository import (
    _readable_by,
    _search_statement,
    _to_result,
)

ACCESS = AccessContext(user_id=uuid4(), team_id=uuid4(), department_id=uuid4())

# The team and department the caller is *not* in. Nothing they own may appear in
# a compiled statement, which is the strongest form the cross-team rule takes.
OTHER_TEAM = uuid4()
OTHER_DEPARTMENT = uuid4()

EMBEDDING = [0.5] * EMBEDDING_DIMENSIONS


# ----------------------------------------------------------------- helpers


def compiled(
    source: SourceType = SourceType.GITHUB,
    access: AccessContext = ACCESS,
    top_k: int = 10,
    embedding: list[float] | None = None,
) -> str:
    """The SQL a server would be sent, with every parameter inlined."""
    statement = _search_statement(
        embedding if embedding is not None else EMBEDDING, source, access, top_k
    )
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def readable_by_sql(access: AccessContext = ACCESS) -> str:
    """The access-scope disjunction alone, compiled."""
    return str(
        _readable_by(access).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def where_clause(sql: str) -> str:
    return sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]


def order_by_clause(sql: str) -> str:
    return sql.split("ORDER BY", 1)[1]


class FakeChunk:
    """The columns `_to_result` reads off a chunk row."""

    def __init__(self, **overrides) -> None:
        fields = {
            "id": uuid4(),
            "content": "export function verifyToken(token: string) {}",
            "external_data_source_id": uuid4(),
            "external_id": "src/auth/verifyToken.ts",
            "chunk_type": "FUNCTION",
            "chunk_metadata": {"symbol_name": "verifyToken", "start_line": 12},
        }
        self.__dict__.update({**fields, **overrides})


class FakeResource:
    """The columns `_to_result` reads off a resource row."""

    def __init__(self, **overrides) -> None:
        fields = {
            "id": uuid4(),
            "resource_type": ResourceType.GITHUB_FILE,
            "title": "verifyToken.ts",
            "resource_metadata": {"repository": "acme/api", "branch": "main"},
        }
        self.__dict__.update({**fields, **overrides})


def a_row(chunk=None, resource=None, source_name="Backend Repo", distance=0.25):
    """One row as the query returns it: entity, entity, name, distance."""
    return (chunk or FakeChunk(), resource or FakeResource(), source_name, distance)


# ------------------------------------------------------------- the joins


def test_the_chunk_reaches_its_resource_through_the_composite_key() -> None:
    """chunks has no resource_id - both columns are the whole link."""
    sql = compiled()

    assert (
        "chunks.external_data_source_id = resources.external_data_source_id" in sql
    )
    assert "chunks.external_id = resources.external_id" in sql


def test_the_resource_reaches_its_source() -> None:
    sql = compiled()

    assert "resources.external_data_source_id = external_data_sources.id" in sql


def test_it_is_one_query_and_not_three() -> None:
    """No Python round trip of id lists - PostgreSQL does the joining."""
    sql = compiled()

    assert sql.count("SELECT") == 1
    assert sql.count("JOIN") == 2


# --------------------------------------------------- source ownership


@pytest.mark.parametrize("source", list(SourceType))
def test_only_this_source_types_connectors_are_searched(source) -> None:
    sql = compiled(source=source)

    assert f"external_data_sources.source_type = '{source.value}'" in where_clause(sql)


def test_a_different_source_type_produces_a_different_query() -> None:
    assert compiled(source=SourceType.GITHUB) != compiled(source=SourceType.JIRA)


def test_only_connectors_owned_by_the_callers_team_are_searched() -> None:
    sql = compiled()

    assert f"external_data_sources.team_id = '{ACCESS.team_id}'" in where_clause(sql)


def test_another_teams_connectors_are_never_searched() -> None:
    """The MVP rule: a user searches only their own team's connected sources."""
    other = AccessContext(
        user_id=ACCESS.user_id, team_id=OTHER_TEAM, department_id=OTHER_DEPARTMENT
    )

    sql = compiled(access=ACCESS)

    assert str(other.team_id) not in sql
    assert str(other.department_id) not in sql


def test_the_team_filter_is_not_optional() -> None:
    """There is no code path that omits it and searches every team."""
    for source in SourceType:
        assert "external_data_sources.team_id" in where_clause(compiled(source=source))


def test_only_active_sources_are_searched() -> None:
    sql = compiled()

    assert "external_data_sources.status = 'ACTIVE'" in where_clause(sql)


# ------------------------------------------------------- access scope


def test_organization_resources_are_readable() -> None:
    sql = compiled()

    assert "resources.access_scope = 'ORGANIZATION'" in where_clause(sql)


def test_team_resources_are_readable_only_for_a_matching_team() -> None:
    sql = where_clause(compiled())

    assert (
        f"resources.access_scope = 'TEAM' AND resources.team_id = '{ACCESS.team_id}'"
        in sql
    )


def test_department_resources_are_readable_only_for_a_matching_department() -> None:
    sql = where_clause(compiled())

    assert (
        "resources.access_scope = 'DEPARTMENT' AND resources.department_id = "
        f"'{ACCESS.department_id}'" in sql
    )


def test_the_three_scopes_are_alternatives_and_not_requirements() -> None:
    """ORGANIZATION or matching TEAM or matching DEPARTMENT - an OR, not an AND."""
    clause = readable_by_sql()

    assert clause.count(" OR ") == 2
    for scope in ResourceAccessScope:
        assert f"'{scope.value}'" in clause


def test_access_is_read_from_the_resource_and_not_the_chunks_stale_copy() -> None:
    """The resource is the source of truth; nothing keeps the chunk's copy in step."""
    clause = readable_by_sql()

    assert "resources.access_scope" in clause
    assert "chunks.access_scope" not in clause
    assert "chunks.team_id" not in clause
    assert "chunks.department_id" not in clause


def test_permission_filtering_happens_before_ranking() -> None:
    """Both filters are in the WHERE, so an unreadable chunk is never ranked."""
    sql = compiled()
    filters = where_clause(sql)

    assert "external_data_sources.team_id" in filters
    assert "resources.access_scope" in filters
    assert "resources.access_scope" not in order_by_clause(sql)


def test_source_ownership_and_access_scope_are_separate_filters() -> None:
    filters = where_clause(compiled())

    assert "external_data_sources.team_id" in filters
    assert "resources.team_id" in filters


# ------------------------------------------------------- vector search


def test_chunks_without_an_embedding_are_ignored() -> None:
    sql = compiled()

    assert "chunks.embedding IS NOT NULL" in where_clause(sql)


def test_results_are_ranked_by_cosine_distance() -> None:
    """`<=>` is pgvector's cosine distance operator."""
    sql = compiled()

    assert "chunks.embedding <=>" in sql
    assert "distance" in order_by_clause(sql)


def test_the_nearest_chunk_comes_first() -> None:
    """Ascending distance - SQLAlchemy's default, and no DESC anywhere."""
    assert "DESC" not in order_by_clause(compiled())


def test_ties_are_broken_so_two_runs_agree() -> None:
    assert "chunks.id" in order_by_clause(compiled())


def test_the_query_vector_is_the_one_it_was_given() -> None:
    embedding = [0.125] + [0.0] * (EMBEDDING_DIMENSIONS - 1)

    assert "0.125" in compiled(embedding=embedding)


def test_no_approximate_index_is_relied_on() -> None:
    """Not in scope for this milestone - the scan is sequential by design."""
    sql = compiled().lower()

    assert "ivfflat" not in sql
    assert "hnsw" not in sql


# --------------------------------------------------------------- top k


@pytest.mark.parametrize("top_k", [1, 5, 10, 15])
def test_top_k_is_respected(top_k) -> None:
    assert f"LIMIT {top_k}" in compiled(top_k=top_k)


def test_the_limit_applies_after_ranking() -> None:
    sql = compiled(top_k=10)

    assert sql.index("ORDER BY") < sql.index("LIMIT")


# ------------------------------------------------------- result mapping


def test_the_chunk_is_mapped() -> None:
    chunk = FakeChunk()

    result = _to_result(a_row(chunk=chunk), SourceType.GITHUB)

    assert result.chunk_id == chunk.id
    assert result.content == chunk.content
    assert result.chunk_type == "FUNCTION"
    assert result.chunk_metadata == chunk.chunk_metadata


def test_the_source_is_mapped() -> None:
    chunk = FakeChunk()

    result = _to_result(a_row(chunk=chunk, source_name="Backend Repo"), SourceType.JIRA)

    assert result.source is SourceType.JIRA
    assert result.source_name == "Backend Repo"
    assert result.external_data_source_id == chunk.external_data_source_id
    assert result.external_id == "src/auth/verifyToken.ts"


def test_the_resource_is_mapped() -> None:
    """resource_id comes from the joined row, since chunks has no such column."""
    resource = FakeResource()

    result = _to_result(a_row(resource=resource), SourceType.GITHUB)

    assert result.resource_id == resource.id
    assert result.resource_type is ResourceType.GITHUB_FILE
    assert result.resource_title == "verifyToken.ts"
    assert result.resource_metadata == resource.resource_metadata


def test_the_score_is_one_minus_the_distance() -> None:
    result = _to_result(a_row(distance=0.25), SourceType.GITHUB)

    assert result.score == pytest.approx(0.75)


def test_a_nearer_chunk_scores_higher() -> None:
    near = _to_result(a_row(distance=0.1), SourceType.GITHUB)
    far = _to_result(a_row(distance=0.9), SourceType.GITHUB)

    assert near.score > far.score


def test_missing_metadata_becomes_an_empty_dict_rather_than_none() -> None:
    result = _to_result(
        a_row(
            chunk=FakeChunk(chunk_metadata=None),
            resource=FakeResource(resource_metadata=None),
        ),
        SourceType.GITHUB,
    )

    assert result.chunk_metadata == {}
    assert result.resource_metadata == {}


def test_a_source_without_a_name_maps_to_none() -> None:
    result = _to_result(a_row(source_name=None), SourceType.GITHUB)

    assert result.source_name is None


# --------------------------------------------------------- what never leaks


def test_the_embedding_is_not_a_field_of_the_result() -> None:
    assert "embedding" not in RetrievalResult.model_fields


def test_the_embedding_column_is_never_fetched_back() -> None:
    """It is what the ranking reads; nothing downstream needs the vector itself."""
    selected = compiled().split("FROM", 1)[0]

    assert "chunks.embedding," not in selected
    assert "chunks.embedding <=>" in selected


def test_no_credential_or_connector_configuration_is_selected() -> None:
    sql = compiled()

    assert "external_data_sources.token" not in sql
    assert "external_data_sources.config" not in sql


def test_only_the_source_name_is_taken_from_the_connector() -> None:
    selected = compiled().split("FROM", 1)[0]

    assert "external_data_sources.name" in selected
    assert selected.count("external_data_sources.") == 1


# ------------------------------------------------------- the access context


def test_a_context_without_a_team_cannot_be_built() -> None:
    """Fail safe: there is no way to reach the query without a team to filter on."""
    with pytest.raises(ValueError):
        AccessContext(user_id=uuid4(), department_id=uuid4())


def test_a_context_without_a_department_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        AccessContext(user_id=uuid4(), team_id=uuid4())


def test_a_context_cannot_smuggle_in_extra_fields() -> None:
    with pytest.raises(ValueError):
        AccessContext(
            user_id=uuid4(),
            team_id=uuid4(),
            department_id=uuid4(),
            organization_id=UUID(int=9),
        )
