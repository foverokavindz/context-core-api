# Entities

[← Documentation](README.md)

The first database layer in the project, and the first thing here that is not
part of the ingestion pipelines.

Everything in `app/models/` is a pydantic DTO: a pipeline boundary type that is
handed to a chunker or serialised into a response, and then gone. Nothing in it
is a table. `app/entities/` is the other thing — rows that outlive a request.
The two live in separate packages so neither drifts into doing the other's job.

This page covers the **organization** group: departments, job titles, and the
people in them. It is the first entity group of the Organizational Intelligence
application, and currently the only one.

## What exists, and what does not

```
app/entities/
├── base.py                       Base, UUIDMixin, TimestampMixin
└── organization/
    ├── application_role.py       ApplicationRole (an enum, not a table)
    ├── department.py             departments
    ├── job_title.py              job_titles
    └── user.py                   users
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

| Relationship | Reverse |
| --- | --- |
| `Department.job_titles` | `JobTitle.department` |
| `Department.users` | `User.department` |
| `JobTitle.users` | `User.job_title` |

**No relationship carries a delete cascade.** Deleting a department must never
take its users with it — people outlive reorganisations. The foreign keys are
left at the database default (`NO ACTION`), so a department that still has rows
pointing at it refuses to be deleted and a human decides where those rows go.
Reassignment will be an explicit operation in the service layer.

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

## Constraints and indexes

```sql
uq_job_titles_department_id_name   UNIQUE (department_id, name)
uq_users_username                  UNIQUE (username)
ix_departments_name                UNIQUE INDEX (name)
ix_users_email                     UNIQUE INDEX (email)
ix_users_department_id             INDEX
ix_users_job_title_id              INDEX
```

Two of these are worth explaining, since both look like something is missing.

**`job_titles.department_id` has no index of its own.** The composite unique
constraint already creates a btree index whose *leading* column is
`department_id`, which serves every lookup and join on it. A second single-column
index would be paid for on every write and read on none.

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

## Import graph

```
application_role.py  ->  (nothing)
base.py              ->  (nothing in the project)
department.py        ->  base
job_title.py         ->  base
user.py              ->  base, application_role
```

No entity module imports a sibling at runtime. `Department`, `JobTitle` and
`User` name each other only as strings in their relationships, with the concrete
types pulled in under `if TYPE_CHECKING:` for the annotations — so the package
cannot form an import cycle.

The consequence: **importing `app.entities.organization` is what makes the group
real to SQLAlchemy.** Its `__init__.py` imports all four modules, which is what
registers every mapper and fills `Base.metadata`. Importing `user.py` alone
would leave `Department` unmapped and the relationships unresolvable.

```python
from app.entities.organization import ApplicationRole, Department, JobTitle, User
```

## Not implemented yet

This is the first entity group of a larger model. Deliberately absent, and not
to be assumed: `Team`, `TeamMember`, `MemberRole`; `SourceCredentials`,
`ExternalDataSource`, `SyncRun`; `Document`; `Resource`, `ResourceAccessScope`;
`Chunk`, `ChunkType`; `ChatSession`, `ChatSessionMessage`, `Citation`. Also
absent: authentication endpoints, JWT, password hashing, login, CRUD APIs,
services, repositories and migrations.

The ingestion pipelines do not use any of this. `CodeChunk`, `JiraChunk`,
`ConfluenceChunk` and `SlackChunk` remain pydantic models that no table stores —
see [architecture.md](architecture.md).

## Checking the schema

There are no tests for these entities in `app/tests`. The schema they generate
can be inspected directly:

```python
from sqlalchemy import create_mock_engine
from app.entities.base import Base
import app.entities.organization  # registers the mappers

def dump(sql, *args, **kwargs):
    print(sql.compile(dialect=engine.dialect))

engine = create_mock_engine("postgresql://", dump)
Base.metadata.create_all(engine, checkfirst=False)
```

That prints the `CREATE TYPE`, the three `CREATE TABLE` statements and the four
indexes, without connecting to anything. Swapping in
`create_engine("sqlite://")` and a real `create_all` is a working smoke test —
on SQLite the native enum degrades to `VARCHAR` with a `CHECK`, which is
expected.
