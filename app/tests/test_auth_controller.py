from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth_dependencies import get_token_service
from app.core.db.dependencies import get_db
from app.core.exceptions import JWTConfigurationError
from app.core.security import hash_password
from app.entities import Department, JobTitle, Team, TeamMember, User
from app.entities.base import Base
from app.entities.organization.application_role import ApplicationRole
from app.main import app
from app.services.token_service import TokenService

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
PASSWORD = "Temporary123!"
TEST_SECRET = "test-secret-that-is-only-used-by-the-auth-test-suite"
OTHER_TEST_SECRET = "another-signing-secret-used-only-by-the-auth-test-suite"


@pytest.fixture
def session() -> Session:
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
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    database_session = factory()
    try:
        yield database_session
    finally:
        database_session.close()
        engine.dispose()


@pytest.fixture
def token_service() -> TokenService:
    return TokenService(TEST_SECRET, access_token_expire_minutes=60)


@pytest.fixture
def client(session: Session, token_service: TokenService) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_token_service] = lambda: token_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_token_service, None)


def add_user(
    session: Session,
    *,
    email: str = "kavinda@example.com",
    first_name: str = "Kavinda",
    last_name: str = "Perera",
    application_role: ApplicationRole = ApplicationRole.EMPLOYEE,
    is_active: bool = True,
    with_department: bool = True,
    with_team: bool = True,
    encoded_password: str | None = None,
) -> tuple[User, TeamMember | None, Department, Team]:
    suffix = uuid4().hex
    department = Department(id=uuid4(), name=f"Department-{suffix}")
    team = Team(
        id=uuid4(),
        department_id=department.id,
        name=f"Team-{suffix}",
        created_by_user_id=None,
    )
    user = User(
        id=uuid4(),
        email=email,
        username=None,
        password_hash=encoded_password or hash_password(PASSWORD),
        first_name=first_name,
        last_name=last_name,
        department_id=department.id if with_department else None,
        job_title_id=None,
        application_role=application_role,
        is_active=is_active,
    )
    membership = (
        TeamMember(id=uuid4(), team_id=team.id, user_id=user.id)
        if with_team
        else None
    )

    session.add_all(
        [department, team, user]
        + ([membership] if membership is not None else [])
    )
    session.commit()
    return user, membership, department, team


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def claims_for(
    user_id: UUID,
    team_id: UUID,
    department_id: UUID,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "sub": str(user_id),
        "application_role": ApplicationRole.EMPLOYEE.value,
        "team_id": str(team_id),
        "department_id": str(department_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }


def test_successful_login_returns_safe_user_and_required_claims(
    client: TestClient,
    session: Session,
) -> None:
    user, membership, department, _ = add_user(
        session,
        application_role=ApplicationRole.HR,
    )

    response = client.post(
        LOGIN,
        json={"email": user.email, "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {
        "id": str(user.id),
        "first_name": "Kavinda",
        "last_name": "Perera",
        "email": user.email,
        "application_role": "HR",
        "team_id": str(membership.team_id),
        "department_id": str(department.id),
    }
    assert "password" not in response.text
    assert user.password_hash not in response.text

    payload = jwt.decode(
        body["access_token"], TEST_SECRET, algorithms=["HS256"]
    )
    assert payload["sub"] == str(user.id)
    assert "user_id" not in payload
    assert payload["application_role"] == "HR"
    assert payload["team_id"] == str(membership.team_id)
    assert payload["department_id"] == str(department.id)
    assert type(payload["iat"]) is int
    assert type(payload["exp"]) is int
    assert payload["exp"] > payload["iat"]


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("unknown@example.com", PASSWORD),
        ("kavinda@example.com", "DefinitelyWrong123!"),
    ],
)
def test_unknown_email_and_wrong_password_have_the_same_response(
    client: TestClient,
    session: Session,
    email: str,
    password: str,
) -> None:
    add_user(session)

    response = client.post(LOGIN, json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("is_active", "with_department", "with_team", "encoded_password"),
    [
        (False, True, True, None),
        (True, False, True, None),
        (True, True, False, None),
        (True, True, True, "not-a-real-hash"),
    ],
)
def test_unusable_accounts_cannot_log_in(
    client: TestClient,
    session: Session,
    is_active: bool,
    with_department: bool,
    with_team: bool,
    encoded_password: str | None,
) -> None:
    user, _, _, _ = add_user(
        session,
        is_active=is_active,
        with_department=with_department,
        with_team=with_team,
        encoded_password=encoded_password,
    )

    response = client.post(
        LOGIN,
        json={"email": user.email, "password": PASSWORD},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}


def test_get_me_uses_token_identity_and_current_database_values(
    client: TestClient,
    session: Session,
) -> None:
    user, membership, _, _ = add_user(session)
    other, _, _, _ = add_user(session, email="other@example.com")
    login = client.post(
        LOGIN,
        json={"email": user.email, "password": PASSWORD},
    ).json()

    new_department = Department(id=uuid4(), name="Current Department")
    new_team = Team(
        id=uuid4(),
        department_id=new_department.id,
        name="Current Team",
        created_by_user_id=None,
    )
    session.add_all([new_department, new_team])
    session.flush()
    user.first_name = "Updated"
    user.department_id = new_department.id
    membership.team_id = new_team.id
    session.commit()

    response = client.get(
        f"{ME}?user_id={other.id}",
        headers=bearer(login["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["first_name"] == "Updated"
    assert response.json()["department_id"] == str(new_department.id)
    assert response.json()["team_id"] == str(new_team.id)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic credentials"},
        {"Authorization": "Bearer malformed"},
    ],
)
def test_missing_or_malformed_bearer_credentials_return_401(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.get(ME, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing access token."}
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_token_returns_401(client: TestClient) -> None:
    now = datetime.now(timezone.utc)
    claims = claims_for(uuid4(), uuid4(), uuid4())
    claims["iat"] = int((now - timedelta(minutes=2)).timestamp())
    claims["exp"] = int((now - timedelta(minutes=1)).timestamp())
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_token_with_another_signature_returns_401(client: TestClient) -> None:
    token = jwt.encode(
        claims_for(uuid4(), uuid4(), uuid4()),
        OTHER_TEST_SECRET,
        algorithm="HS256",
    )

    assert client.get(ME, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    "missing_claim",
    ["sub", "application_role", "team_id", "department_id", "iat", "exp"],
)
def test_token_missing_a_required_claim_returns_401(
    client: TestClient,
    missing_claim: str,
) -> None:
    claims = claims_for(uuid4(), uuid4(), uuid4())
    del claims[missing_claim]
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")

    assert client.get(ME, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("sub", 123),
        ("application_role", "NOT_A_ROLE"),
        ("team_id", "not-a-uuid"),
        ("department_id", "not-a-uuid"),
        ("iat", "not-a-timestamp"),
        ("exp", "not-a-timestamp"),
    ],
)
def test_token_with_invalid_claim_type_or_value_returns_401(
    client: TestClient,
    claim: str,
    value,
) -> None:
    claims = claims_for(uuid4(), uuid4(), uuid4())
    claims[claim] = value
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")

    response = client.get(ME, headers=bearer(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing access token."}


def test_get_me_rejects_a_token_for_a_missing_user(client: TestClient) -> None:
    token = jwt.encode(
        claims_for(uuid4(), uuid4(), uuid4()),
        TEST_SECRET,
        algorithm="HS256",
    )

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_get_me_rechecks_that_the_user_is_active(
    client: TestClient,
    session: Session,
) -> None:
    user, _, _, _ = add_user(session)
    token = client.post(
        LOGIN,
        json={"email": user.email, "password": PASSWORD},
    ).json()["access_token"]
    user.is_active = False
    session.commit()

    assert client.get(ME, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    ("algorithm", "expiry"),
    [("none", "60"), ("HS256", "0"), ("HS256", "not-a-number")],
)
def test_invalid_jwt_environment_configuration_is_rejected(
    monkeypatch,
    algorithm: str,
    expiry: str,
) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", algorithm)
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", expiry)

    with pytest.raises(JWTConfigurationError):
        TokenService.from_environment()


def test_missing_jwt_secret_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")

    with pytest.raises(JWTConfigurationError):
        TokenService.from_environment()


def test_missing_jwt_secret_returns_a_safe_500(
    session: Session,
    monkeypatch,
) -> None:
    user, _, _, _ = add_user(session)
    monkeypatch.setenv("JWT_SECRET", "")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides.pop(get_token_service, None)
    try:
        response = TestClient(app).post(
            LOGIN,
            json={"email": user.email, "password": PASSWORD},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert response.json() == {
        "detail": (
            "JWT authentication is not configured correctly on this server."
        )
    }
    assert "JWT_SECRET" not in response.text
