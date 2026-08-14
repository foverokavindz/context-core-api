# Entities

[← Documentation](README.md)

The first database layer in the project, and the first thing here that is not
part of the ingestion pipelines.

Everything in `app/models/` is a pydantic DTO: a pipeline boundary type that is
handed to a chunker or serialised into a response, and then gone. Nothing in it
is a table. `app/entities/` is the other thing — rows that outlive a request.
The two live in separate packages so neither drifts into doing the other's job.

This page covers two groups: **organization** — departments, job titles, and the
people in them — and **teams**, the working groups those people are actually in.

## What exists, and what does not

```
app/entities/
├── base.py                       Base, UUIDMixin, TimestampMixin
├── organization/
│   ├── application_role.py       ApplicationRole (an enum, not a table)
│   ├── department.py             departments
│   ├── job_title.py              job_titles
│   └── user.py                   users
└── teams/
    ├── member_role.py            MemberRole (an enum, not a table)
    ├── team.py                   teams
    └── team_member.py            team_members
```

**There is no engine, no session factory, no `DATABASE_URL` and no Alembic.**
That is not an omission. Entity definitions do not need a connection —
`Base.metadata` is a *description* of a schema, and describing it is all this
version does. Nothing in `app/main.py` imports this package, so the running API
is unchanged. The engine arrives with the first code that actually reads or
writes a row, and the driver (`psycopg` or `asyncpg`) arrives with it.

`sqlalchemy>=2.0` is a runtime dependency; no database driver is listed yet, for
the same reason.

## The shape

```
Department 1 ─────── * JobTitle
     │                    │
     │                    │
     * User               * User
```

A department owns job titles and it owns users, and those two are independent:
a person sits in a department whether or not anyone has given them a title yet.
That is why `User.department` exists rather than being reached through
`JobTitle`.

A department also owns teams, and a team reaches its people through
`TeamMember`:

```
Department 1 ───── * Team 1 ───── * TeamMember 1 ───── 1 User
```

**A user is in at most one team.** A team holds many people; a person holds one
membership. `team_members.user_id` is `UNIQUE`, so the table is a one-to-one
between users and their membership, and many-to-one onto the team.

| Relationship | Reverse |
| --- | --- |
| `Department.job_titles` | `JobTitle.department` |
| `Department.users` | `User.department` |
| `Department.teams` | `Team.department` |
| `JobTitle.users` | `User.job_title` |
| `Team.creator` | `User.created_teams` |
| `Team.team_members` | `TeamMember.team` |
| `User.team_membership` | `TeamMember.user` |

`User.team_membership` is the only singular one: `TeamMember | None`, not a
list, because the unique constraint means a list could never hold more than one
element. `User.created_teams` beside it *is* a list — creating teams is not
limited, only belonging to them.

**No relationship carries a delete cascade** — not even `team_members`, which is
a join table and the one place a cascade would look routine. Deleting a
department must never take its users with it; people outlive reorganisations.
The foreign keys are left at the database default (`NO ACTION`), so rows still
pointing at a parent stop its deletion and a human decides where they go.
Reassignment will be an explicit operation in the service layer.

Deleting a `Team` therefore fails while it still has members, rather than
quietly emptying `team_members` — its `team_id` is `NOT NULL`, so SQLAlchemy's
default de-association has nothing to write. Removing people from a team is a
separate, deliberate act.

## `departments`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `name` | `VARCHAR(255)` | NOT NULL, unique across the organization |
| `description` | `TEXT` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

Names are unique globally, so "Engineering" is one department and not one per
office. Examples: Engineering, Human Resources, Finance.

## `job_titles`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `department_id` | `UUID` | → `departments.id`, NOT NULL |
| `name` | `VARCHAR(255)` | NOT NULL, unique *within* the department |
| `description` | `TEXT` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

A title belongs to exactly one department, and `UNIQUE(department_id, name)` is
the whole point: "Engineering Manager" under Engineering and again under Finance
is two different jobs and two valid rows, but the same title twice under
Engineering is a duplicate.

```
Engineering
 ├── Software Engineer
 ├── QA Engineer
 └── Engineering Manager
```

## `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `email` | `VARCHAR(320)` | NOT NULL, unique. 320 is the longest address the RFC permits |
| `username` | `VARCHAR(150)` | nullable, unique when set |
| `password_hash` | `VARCHAR(255)` | NOT NULL |
| `first_name` | `VARCHAR(255)` | NOT NULL |
| `last_name` | `VARCHAR(255)` | NOT NULL |
| `department_id` | `UUID` | → `departments.id`, **nullable** |
| `job_title_id` | `UUID` | → `job_titles.id`, **nullable** |
| `application_role` | `application_role` | NOT NULL, default `EMPLOYEE` |
| `is_active` | `BOOLEAN` | NOT NULL, default `true` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

Three things about this table are worth knowing before changing it.

**There is no `password` column and there will never be one.** Only
`password_hash`. Nothing in this version hashes anything — that belongs to the
authentication work, which has not started — but the column it will eventually
write to is named for what it holds, so no future code has a plausible-looking
place to put a plain-text password.

**Organizational position is optional.** Both foreign keys are nullable, because
an account exists from the moment it is created and HR may fill in where that
person actually sits some time later. A user with `department_id = NULL` and
`job_title_id = NULL` is a normal, valid row, not a broken one.

**Deactivation, not deletion.** A departed employee's rows still need to
resolve, so accounts are switched off through `is_active` rather than removed.

## `teams`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `department_id` | `UUID` | → `departments.id`, NOT NULL |
| `name` | `VARCHAR(255)` | NOT NULL, unique *within* the department |
| `description` | `TEXT` | nullable |
| `created_by_user_id` | `UUID` | → `users.id`, NOT NULL |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

Every team belongs to a department, and `UNIQUE(department_id, name)` works the
same way it does for job titles: Engineering and Finance may each have a
"Platform Team", Engineering may not have two.

```
Engineering
 ├── Backend Team
 ├── Frontend Team
 └── Platform Team
```

**`created_by_user_id` is authorship, not membership.** It records which user
made the row, and nothing more — the creator is not implicitly in the team, and
the entity does not insert a `TeamMember` for them. Making the creator a
`TEAM_LEAD` is a plausible product decision, but it is a *service* decision;
a future `TeamService.create_team()` will write both rows explicitly, where the
behaviour is visible and can be tested. Hiding it in a default or an ORM event
would mean no reader of this table could tell why a membership exists.

## `team_members`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `team_id` | `UUID` | → `teams.id`, NOT NULL |
| `user_id` | `UUID` | → `users.id`, NOT NULL, **unique** |
| `member_role` | `member_role` | NOT NULL, default `TEAM_MEMBER` |
| `joined_at` | `TIMESTAMPTZ` | NOT NULL, defaults to `now()` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row means: *this user is in this team, in this role.*

```
Backend Team
 ├── Kavinda   TEAM_LEAD
 └── Dilan     TEAM_MEMBER
```

**`UNIQUE(user_id)` is the rule that shapes this table.** A user gets at most
one row, so a person is in one team at a time — being added to a second team is
rejected by the database, not by a service check that someone could forget to
write. The same constraint incidentally covers the other case, a user added to
the team they are already in.

Moving someone between teams is therefore a delete plus an insert, not an
update to a second row. That is a deliberate consequence: it gives the move a
fresh `joined_at`, and it means no code path can leave a person half-way into
two teams.

Note what the schema does *not* enforce, deliberately: nothing requires a
member's `department_id` to match the team's. Someone from Design can sit on a
platform team, and cross-functional teams are the normal case, not an anomaly.

**Why this is still a table, and not a `team_id` column on `users`.** With one
team per user the two are equivalent in raw storage, so the join table has to
earn its place, and it does on two counts. `member_role` and `joined_at` are
facts about the *membership*, not about the person — on `users` they would sit
beside `email` and `application_role` implying a person has a team role the way
they have a name. And the one-team rule is a product decision expressed as a
single unique constraint: relaxing it later is dropping that constraint, not
migrating a column into a new table and rewriting every query that read it.

**`joined_at` and `created_at` are not the same fact**, even though they will
hold the same value on almost every row. `created_at` is when the row was
written; `joined_at` is when the person joined, which a backfill or an import
from an existing HR system is entitled to set to a date in the past. Keeping
both means a migration never has to lie about one to preserve the other.
`updated_at` then covers the case this table is really built for: a member's
`member_role` changing while the membership itself continues.

## `ApplicationRole`

```python
class ApplicationRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    HR = "HR"
    EMPLOYEE = "EMPLOYEE"
```

A Python enum stored on `users`, and deliberately **not** an `application_roles`
table. A lookup table earns its place when rows are data — when an administrator
can add one, rename one, or attach settings to one at runtime. These three are
none of that; they are branches in the code. `SUPER_ADMIN` means something only
because some future authorisation check says so, and no row inserted into a
table could give a fourth role that meaning.

So the set is closed, it changes only when the code changes, and it lives in
`application_role.py`. If roles ever become configurable — per-tenant roles,
custom permission sets — that is the point at which this becomes a table, and it
will be a migration rather than a rewrite.

In PostgreSQL it is a native enum type:

```sql
CREATE TYPE application_role AS ENUM ('SUPER_ADMIN', 'HR', 'EMPLOYEE');
```

so the database itself rejects a role the application does not have. The cost is
that adding a fourth role needs an `ALTER TYPE` in a migration.

## `MemberRole`

```python
class MemberRole(str, Enum):
    TEAM_LEAD = "TEAM_LEAD"
    TEAM_MEMBER = "TEAM_MEMBER"
```

Stored on `team_members`, a native `member_role` enum in PostgreSQL, and not a
table — for the same reason `ApplicationRole` is not one.

**It is not a second copy of `ApplicationRole`, and the two must not be
collapsed.** `ApplicationRole` is what a person may do in the *application* —
the thing a future authorisation check reads. `MemberRole` is where a person
stands inside their team. Both are one value per user today, which is exactly
why the distinction is worth stating: they are not interchangeable.

```
Kavinda   application_role = EMPLOYEE     (on the user)
          member_role      = TEAM_LEAD    (on the membership, in Backend Team)
```

Leading a team does not widen what someone can do to the application, and being
`HR` does not make anyone a lead. Neither value is derivable from the other, and
they change for different reasons — one when HR changes someone's access, the
other when a team reorganises. That is why they are two enums on two tables.

## Constraints and indexes

```sql
uq_job_titles_department_id_name   UNIQUE (department_id, name)
uq_teams_department_id_name        UNIQUE (department_id, name)
uq_team_members_user_id            UNIQUE (user_id)
uq_users_username                  UNIQUE (username)
ix_departments_name                UNIQUE INDEX (name)
ix_users_email                     UNIQUE INDEX (email)
ix_users_department_id             INDEX
ix_users_job_title_id              INDEX
ix_teams_created_by_user_id        INDEX
ix_team_members_team_id            INDEX
```

Two of these are worth explaining, since both look like something is missing.

**`job_titles.department_id` has no index of its own**, and neither does
`teams.department_id`. In both cases the composite unique constraint already
creates a btree index whose *leading* column is that foreign key, which serves
every lookup and join on it. A second single-column index would be paid for on
every write and read on none.

The same reasoning is why `team_members` indexes `team_id` and *not* `user_id`,
which is the opposite of what the table's shape first suggests.
`UNIQUE(user_id)` already builds the index that answers "which team is this
person in" — and it answers it with a single row. `team_id` has no constraint
over it and carries the other question, "who is on this team", so it gets the
explicit index.

The columns that get one for the same reason: `teams.created_by_user_id`, for
"every team this person created", which no constraint covers.

**`users.username` uses a plain unique constraint, not a partial index.** SQL
treats NULLs as distinct from one another, so `UNIQUE` already allows any number
of users without a username while still rejecting a duplicate of one that is set.

The names above are not written by hand. `Base.metadata` carries a naming
convention (`ix_` / `uq_` / `ck_` / `fk_` / `pk_`), so constraints declared as
`unique=True` still get stable, predictable names — which is what lets a future
Alembic migration drop a constraint it did not create.

## The mixins

Every entity is `class X(UUIDMixin, TimestampMixin, Base)`.

`UUIDMixin` gives a `UUID` primary key generated in Python with `uuid4()`, so an
object has its id the moment it is constructed — before the flush, and without a
round trip to find out what the database chose. The column type is SQLAlchemy's
dialect-agnostic `Uuid` rather than `postgresql.UUID`: it still emits a native
`uuid` column on PostgreSQL, and degrades to `CHAR(32)` elsewhere, which is what
lets these entities be created against SQLite in a test without a Postgres server
anywhere near them.

`TimestampMixin` gives timezone-aware `created_at` and `updated_at`, both with a
`server_default` of `now()` so a row is stamped however it was written — through
the ORM, through a raw `INSERT`, or by a seed script that never imports the
module. `updated_at` also carries an ORM-side `onupdate`, so a statement that
bypasses the ORM entirely is responsible for its own value.

## Consistency left to the service layer

A user's `job_title_id` should belong to the same department as their
`department_id`. **Nothing in the database enforces that** — no trigger, no
composite foreign key. That is a decision, not a gap: for V1 it is the service
layer's job to check when assigning organizational information, and until that
layer exists the invariant is documented rather than pretended.

Two more of the same kind now sit on the teams group. Nothing stops a team from
having no `TEAM_LEAD`, or from having several — the schema has no notion of "one
lead per team", because whether that rule holds is a product question, and one
that a table with a unique index could only answer badly. And nothing links a
team's creator to its membership; see `teams` above. Both belong to
`TeamService`, which does not exist yet.

## Import graph

```
application_role.py  ->  (nothing)
member_role.py       ->  (nothing)
base.py              ->  (nothing in the project)
department.py        ->  base
job_title.py         ->  base
user.py              ->  base, application_role
team.py              ->  base
team_member.py       ->  base, member_role
```

No entity module imports another entity module at runtime — not even across the
two groups. They name each other only as strings in their relationships, with
the concrete types pulled in under `if TYPE_CHECKING:` for the annotations, so
no cycle can form. `Team` referring back to `Department` and `Department`
referring forward to `Team` costs nothing at import time.

The consequence: **importing a package is what makes its mappers real to
SQLAlchemy**, and the two groups now depend on each other's mappers. Import the
top-level package:

```python
from app.entities import Department, JobTitle, MemberRole, Team, TeamMember, User
```

`app/entities/__init__.py` imports both groups, which registers every mapper and
fills `Base.metadata`. Importing `app.entities.organization` on its own is no
longer enough — `Department.teams` would have no `Team` to resolve to, and
`configure_mappers()` would raise on the first use.

## Not implemented yet

These are the first two entity groups of a larger model. Deliberately absent,
and not to be assumed: `SourceCredentials`, `ExternalDataSource`, `SyncRun`;
`Document`; `Resource`, `ResourceAccessScope`; `Chunk`, `ChunkType`;
`ChatSession`, `ChatSessionMessage`, `Citation`. Also absent: authentication
endpoints, JWT, password hashing, login, CRUD APIs, team services,
authorisation policies, repositories and migrations.

The ingestion pipelines do not use any of this. `CodeChunk`, `JiraChunk`,
`ConfluenceChunk` and `SlackChunk` remain pydantic models that no table stores —
see [architecture.md](architecture.md).

## Checking the schema

There are no tests for these entities in `app/tests`. The schema they generate
can be inspected directly:

```python
from sqlalchemy import create_mock_engine
from sqlalchemy.orm import configure_mappers
from app.entities.base import Base
import app.entities  # registers the mappers

configure_mappers()  # raises if any back_populates pair does not line up

def dump(sql, *args, **kwargs):
    print(sql.compile(dialect=engine.dialect))

engine = create_mock_engine("postgresql://", dump)
Base.metadata.create_all(engine, checkfirst=False)
```

That prints the two `CREATE TYPE`s, the five `CREATE TABLE` statements and the
six indexes, without connecting to anything. Swapping in
`create_engine("sqlite://")` and a real `create_all` is a working smoke test —
on SQLite the native enum degrades to `VARCHAR` with a `CHECK`, which is
expected, and the unique constraints still reject a second team for a user who
already has one, or a duplicate team name within a department.
