"""Seeds the rows the ingestion endpoint needs to exist before it can run.

`external_data_sources.team_id` and `.created_by_user_id` are NOT NULL foreign
keys, and `resources` and `chunks` both reference `teams` and `departments`. The
endpoint takes those ids from the request body and trusts them - there is no
authentication yet - so without real rows the very first insert fails.

The ids are fixed rather than generated, so they can be pasted straight into a
request body. They are the same ones docs/ingestion-endpoint.md and
app/tests/test_ingestion_controller.py already use.

    python scripts/seed_dev.py

Safe to run twice: anything already present is left alone.

Development only. The seeded login password defaults to ``Temporary123!`` and
can be changed with ``DEV_SEED_PASSWORD``.
"""

import os
from uuid import UUID

from sqlalchemy import select

from app.core.db.session import SessionLocal
from app.core.security import hash_password
from app.entities.organization.department import Department
from app.entities.organization.job_title import JobTitle
from app.entities.organization.user import User
from app.entities.teams.member_role import MemberRole
from app.entities.teams.team import Team
from app.entities.teams.team_member import TeamMember

DEPARTMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
TEAM_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("33333333-3333-3333-3333-333333333333")
JOB_TITLE_ID = UUID("44444444-4444-4444-4444-444444444444")
DEV_SEED_PASSWORD = os.getenv("DEV_SEED_PASSWORD", "Temporary123!")
LEGACY_PLACEHOLDER_HASH = "not-a-real-hash"


def seed() -> None:
    session = SessionLocal()
    try:
        # In this order: a job title belongs to a department, a user belongs to
        # both, and a team needs a department and the user who created it.
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

        if session.get(JobTitle, JOB_TITLE_ID) is None:
            session.add(
                JobTitle(
                    id=JOB_TITLE_ID,
                    department_id=DEPARTMENT_ID,
                    name="Software Engineer",
                    description="Seeded for local development.",
                )
            )
            session.flush()
            print(f"created job title  {JOB_TITLE_ID}")

        user = session.get(User, USER_ID)
        if user is None:
            session.add(
                User(
                    id=USER_ID,
                    email="dev@contextcore.local",
                    username="dev",
                    password_hash=hash_password(DEV_SEED_PASSWORD),
                    first_name="Dev",
                    last_name="User",
                    department_id=DEPARTMENT_ID,
                    job_title_id=JOB_TITLE_ID,
                )
            )
            session.flush()
            print(f"created user       {USER_ID}")
        else:
            if user.password_hash == LEGACY_PLACEHOLDER_HASH:
                user.password_hash = hash_password(DEV_SEED_PASSWORD)
                session.flush()
                print(f"set login password on user {USER_ID}")
            if user.job_title_id is None:
                # A database seeded before job titles existed here: fill the
                # gap rather than leave the user title-less.
                user.job_title_id = JOB_TITLE_ID
                session.flush()
                print(f"set job title on user {USER_ID}")

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

        # team_members.user_id is unique, so the check is on the user rather than
        # on a fixed row id - a membership written by anything else still counts.
        membership = session.scalars(
            select(TeamMember).where(TeamMember.user_id == USER_ID)
        ).first()
        if membership is None:
            session.add(
                TeamMember(
                    team_id=TEAM_ID,
                    user_id=USER_ID,
                    member_role=MemberRole.TEAM_LEAD,
                )
            )
            session.flush()
            print(f"created membership {USER_ID} -> {TEAM_ID}")

        session.commit()
    finally:
        session.close()

    print()
    print("Development login:")
    print('  email: "dev@contextcore.local"')
    print("  password: DEV_SEED_PASSWORD (defaults to Temporary123!)")
    print()
    print("Use these in the ingestData body:")
    print(f'  "team_id":            "{TEAM_ID}"')
    print(f'  "department_id":      "{DEPARTMENT_ID}"')
    print(f'  "created_by_user_id": "{USER_ID}"')


if __name__ == "__main__":
    seed()
