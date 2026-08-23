from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db.dependencies import get_db
from app.entities import (
    Department,
    JobTitle,
    Team,
    TeamMember,
    User,
)
from app.entities.base import Base
from app.entities.organization.application_role import ApplicationRole
from app.entities.teams.member_role import MemberRole
from app.main import app
from app.repository.department_repository import DepartmentRepository
from app.repository.employee_repository import EmployeeRepository
from app.repository.team_repository import TeamRepository
from app.services.employee_service import password_hash

DEPARTMENTS = "/api/v1/departments"
TEAMS = "/api/v1/teams"
EMPLOYEES = "/api/v1/employees"
PASSWORD = "Temporary123!"


class TrackingSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def commit(self) -> None:
        self.commits += 1
        super().commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        super().rollback()

    def flush(self, objects=None) -> None:
        self.flushes += 1
        super().flush(objects)


@pytest.fixture
def session() -> TrackingSession:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Department.__table__,
            JobTitle.__table__,
            User.__table__,
            Team.__table__,
            TeamMember.__table__,
        ],
    )
    factory = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        expire_on_commit=False,
    )
    database_session = factory()
    try:
        yield database_session
    finally:
        database_session.close()
        engine.dispose()


@pytest.fixture
def client(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def create_department(client: TestClient, name: str = "Engineering") -> dict:
    response = client.post(
        DEPARTMENTS,
        json={"name": name, "description": f"The {name} department."},
    )
    assert response.status_code == 201
    return response.json()


def create_team(
    client: TestClient,
    department_id: str,
    name: str = "Platform",
) -> dict:
    response = client.post(
        TEAMS,
        json={
            "department_id": department_id,
            "name": name,
            "description": f"The {name} team.",
        },
    )
    assert response.status_code == 201
    return response.json()


def employee_payload(team_id: str, department_id: str, **overrides) -> dict:
    payload = {
        "first_name": "Kavinda",
        "last_name": "Perera",
        "email": "kavinda@example.com",
        "password": PASSWORD,
        "department_id": department_id,
        "team_id": team_id,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("endpoint", [DEPARTMENTS, TEAMS, EMPLOYEES])
def test_empty_collections_return_an_empty_list(client, endpoint: str) -> None:
    response = client.get(endpoint)

    assert response.status_code == 200
    assert response.json() == []


def test_get_endpoints_do_not_commit(client, session) -> None:
    for endpoint in (DEPARTMENTS, TEAMS, EMPLOYEES):
        assert client.get(endpoint).status_code == 200

    assert session.commits == 0


def test_department_is_created_and_returned_by_the_list(client, session) -> None:
    response = client.post(
        DEPARTMENTS,
        json={
            "name": "  Engineering  ",
            "description": "  Software engineering.  ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"])
    assert body["name"] == "Engineering"
    assert body["description"] == "Software engineering."
    assert client.get(DEPARTMENTS).json() == [body]
    assert session.commits == 1


@pytest.mark.parametrize("name", [None, "", "   "])
def test_department_requires_a_non_blank_name(client, name) -> None:
    body = {} if name is None else {"name": name}

    assert client.post(DEPARTMENTS, json=body).status_code == 422


def test_departments_are_listed_deterministically(client) -> None:
    create_department(client, "Marketing")
    create_department(client, "Engineering")

    departments = client.get(DEPARTMENTS).json()

    assert [department["name"] for department in departments] == [
        "Engineering",
        "Marketing",
    ]


def test_duplicate_department_is_a_conflict(client) -> None:
    create_department(client)

    response = client.post(DEPARTMENTS, json={"name": "Engineering"})

    assert response.status_code == 409
    assert response.json() == {"detail": "Department already exists."}
    assert len(client.get(DEPARTMENTS).json()) == 1


def test_team_is_created_for_a_department(client, session) -> None:
    department = create_department(client)

    response = client.post(
        TEAMS,
        json={
            "department_id": department["id"],
            "name": "  Platform  ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"])
    assert body["department_id"] == department["id"]
    assert body["name"] == "Platform"
    assert body["description"] is None
    assert client.get(TEAMS).json() == [body]

    team = session.get(Team, UUID(body["id"]))
    assert team.department_id == UUID(department["id"])
    assert team.created_by_user_id is None


def test_team_requires_an_existing_department(client) -> None:
    response = client.post(
        TEAMS,
        json={"department_id": str(uuid4()), "name": "Platform"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Department not found."}


def test_team_name_is_unique_within_its_department(client) -> None:
    department = create_department(client)
    create_team(client, department["id"])

    response = client.post(
        TEAMS,
        json={"department_id": department["id"], "name": "Platform"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Team already exists in this department."
    }


def test_departments_may_have_teams_with_the_same_name(client) -> None:
    engineering = create_department(client, "Engineering")
    finance = create_department(client, "Finance")

    first = create_team(client, engineering["id"])
    second = create_team(client, finance["id"])

    assert first["name"] == second["name"] == "Platform"
    assert first["department_id"] != second["department_id"]


def test_employee_creation_persists_user_and_membership(client, session) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])
    commits_before = session.commits

    response = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], department["id"]),
    )

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"])
    assert body == {
        "id": body["id"],
        "first_name": "Kavinda",
        "last_name": "Perera",
        "email": "kavinda@example.com",
        "department_id": department["id"],
        "team_id": team["id"],
        "application_role": "EMPLOYEE",
        "member_role": "TEAM_MEMBER",
    }

    user = session.get(User, UUID(body["id"]))
    membership = session.scalars(
        select(TeamMember).where(TeamMember.user_id == user.id)
    ).one()
    assert user.department_id == UUID(department["id"])
    assert user.username is None
    assert user.job_title_id is None
    assert user.is_active is True
    assert membership.team_id == UUID(team["id"])
    assert password_hash.verify(PASSWORD, user.password_hash)
    assert user.password_hash != PASSWORD
    assert session.commits == commits_before + 1


def test_employee_response_never_exposes_password_material(client, session) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])

    response = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], department["id"]),
    )
    user = session.scalars(select(User)).one()

    assert PASSWORD not in response.text
    assert user.password_hash not in response.text
    assert "password" not in response.json()
    assert "password_hash" not in response.json()


def test_employee_can_receive_explicit_existing_roles(client) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])

    response = client.post(
        EMPLOYEES,
        json=employee_payload(
            team["id"],
            department["id"],
            application_role="HR",
            member_role="TEAM_LEAD",
        ),
    )

    assert response.status_code == 201
    assert response.json()["application_role"] == "HR"
    assert response.json()["member_role"] == "TEAM_LEAD"


def test_employee_requires_an_existing_team(client, session) -> None:
    department = create_department(client)
    response = client.post(
        EMPLOYEES,
        json=employee_payload(str(uuid4()), department["id"]),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found."}
    assert session.scalars(select(User)).all() == []


def test_employee_requires_an_existing_department(client, session) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])

    response = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], str(uuid4())),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Department not found."}
    assert session.scalars(select(User)).all() == []


def test_employee_department_must_match_the_team(client, session) -> None:
    engineering = create_department(client, "Engineering")
    finance = create_department(client, "Finance")
    team = create_team(client, engineering["id"])

    response = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], finance["id"]),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "department_id does not match the selected team."
    }
    assert session.scalars(select(User)).all() == []


def test_duplicate_employee_email_is_a_conflict(client, session) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])
    client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], department["id"]),
    )

    response = client.post(
        EMPLOYEES,
        json=employee_payload(
            team["id"],
            department["id"],
            first_name="Someone",
            last_name="Else",
        ),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "An employee with this email already exists."
    }
    assert len(session.scalars(select(User)).all()) == 1
    assert len(session.scalars(select(TeamMember)).all()) == 1


def test_get_employees_returns_created_employees(client) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])
    created = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], department["id"]),
    ).json()

    assert client.get(EMPLOYEES).json() == [created]


def test_get_employees_excludes_users_without_membership(client, session) -> None:
    session.add(
        User(
            id=uuid4(),
            email="unassigned@example.com",
            password_hash=password_hash.hash(PASSWORD),
            first_name="Unassigned",
            last_name="Account",
        )
    )
    session.commit()

    assert client.get(EMPLOYEES).json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": ""},
        {"last_name": "   "},
        {"email": "not-an-email"},
        {"password": "short"},
        {"password": "x" * 129},
        {"team_id": "not-a-uuid"},
        {"application_role": "ENGINEER"},
        {"member_role": "MANAGER"},
        {"unexpected": "field"},
    ],
)
def test_invalid_employee_requests_are_rejected(client, overrides: dict) -> None:
    body = employee_payload(str(uuid4()), str(uuid4()))
    body.update(overrides)

    assert client.post(EMPLOYEES, json=body).status_code == 422


def test_employee_request_requires_department_id(client) -> None:
    body = employee_payload(str(uuid4()), str(uuid4()))
    del body["department_id"]

    assert client.post(EMPLOYEES, json=body).status_code == 422


def test_secret_password_is_masked_in_validation_response(client) -> None:
    body = employee_payload(
        str(uuid4()),
        str(uuid4()),
        password="too-short",
    )
    body["unexpected"] = "field"

    response = client.post(EMPLOYEES, json=body)

    assert response.status_code == 422
    assert "too-short" not in response.text


def test_repositories_flush_without_committing(session) -> None:
    department = Department(id=uuid4(), name="Engineering")
    DepartmentRepository(session).create(department)

    team = Team(
        id=uuid4(),
        department_id=department.id,
        name="Platform",
        created_by_user_id=None,
    )
    TeamRepository(session).create(team)

    user = User(
        id=uuid4(),
        email="employee@example.com",
        password_hash=password_hash.hash(PASSWORD),
        first_name="Test",
        last_name="Employee",
        department_id=department.id,
    )
    membership = TeamMember(
        id=uuid4(),
        team_id=team.id,
        user_id=user.id,
    )
    EmployeeRepository(session).create(user, membership)

    assert session.flushes == 3
    assert session.commits == 0


def test_failed_employee_creation_rolls_back(
    client, session, monkeypatch
) -> None:
    department = create_department(client)
    team = create_team(client, department["id"])
    rollbacks_before = session.rollbacks
    commits_before = session.commits

    def fail_to_create(self, user, membership):
        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(EmployeeRepository, "create", fail_to_create)

    response = client.post(
        EMPLOYEES,
        json=employee_payload(team["id"], department["id"]),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "The employee could not be created."}
    assert session.commits == commits_before
    assert session.rollbacks == rollbacks_before + 1
