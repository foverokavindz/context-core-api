"""Seeds the three rows the ingestion endpoint needs to exist before it can run.

`external_data_sources.team_id` and `.created_by_user_id` are NOT NULL foreign
keys, and `resources` and `chunks` both reference `teams` and `departments`. The
endpoint takes those ids from the request body and trusts them - there is no
authentication yet - so without real rows the very first insert fails.

The ids are fixed rather than generated, so they can be pasted straight into a
request body. They are the same ones docs/ingestion-endpoint.md and
app/tests/test_ingestion_controller.py already use.

    python scripts/seed_dev.py

Safe to run twice: anything already present is left alone.

Development only. The password hash is not a real hash of anything - there is no
login path yet, and the column is NOT NULL.
"""

from uuid import UUID

from app.core.db.session import SessionLocal
from app.entities.organization.department import Department
from app.entities.organization.user import User
from app.entities.teams.team import Team

DEPARTMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
TEAM_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("33333333-3333-3333-3333-333333333333")


def seed() -> None:
    session = SessionLocal()
    try:
        # In this order: a user belongs to a department, and a team needs both a
        # department and the user who created it.
        if session.get(Department, DEPARTMENT_ID) is None:
            session.add(
                Department(
                    id=DEPARTMENT_ID,
                    name="Engineering",
                    description="Seeded for local development.",
                )
            )
            session.flush()
            print(f"created department {DEPARTMENT_ID}")

        if session.get(User, USER_ID) is None:
            session.add(
                User(
                    id=USER_ID,
                    email="dev@contextcore.local",
                    username="dev",
                    password_hash="not-a-real-hash",
                    first_name="Dev",
                    last_name="User",
                    department_id=DEPARTMENT_ID,
                )
            )
            session.flush()
            print(f"created user       {USER_ID}")

        if session.get(Team, TEAM_ID) is None:
            session.add(
                Team(
                    id=TEAM_ID,
                    department_id=DEPARTMENT_ID,
                    name="Backend Team",
                    description="Seeded for local development.",
                    created_by_user_id=USER_ID,
                )
            )
            session.flush()
            print(f"created team       {TEAM_ID}")

        session.commit()
    finally:
        session.close()

    print()
    print("Use these in the ingestData body:")
    print(f'  "team_id":            "{TEAM_ID}"')
    print(f'  "department_id":      "{DEPARTMENT_ID}"')
    print(f'  "created_by_user_id": "{USER_ID}"')


if __name__ == "__main__":
    seed()
