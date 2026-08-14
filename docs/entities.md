# Entities

[← Documentation](README.md)

The first database layer in the project, and the first thing here that is not
part of the ingestion pipelines.

Everything in `app/models/` is a pydantic DTO: a pipeline boundary type that is
handed to a chunker or serialised into a response, and then gone. Nothing in it
is a table. `app/entities/` is the other thing — rows that outlive a request.
The two live in separate packages so neither drifts into doing the other's job.

This page covers three groups: **organization** — departments, job titles, and
the people in them — **teams**, the working groups those people are actually in,
and **data_sources**, the external systems a team has connected and the record of
each attempt to ingest from them.

## What exists, and what does not

```
app/entities/
├── base.py                       Base, UUIDMixin, TimestampMixin
├── organization/
│   ├── application_role.py       ApplicationRole (an enum, not a table)
│   ├── department.py             departments
│   ├── job_title.py              job_titles
│   └── user.py                   users
├── teams/
│   ├── member_role.py            MemberRole (an enum, not a table)
│   ├── team.py                   teams
│   └── team_member.py            team_members
└── data_sources/
    ├── credential_type.py        CredentialType (an enum, not a table)
    ├── source_type.py            SourceType (an enum, not a table)
    ├── source_status.py          SourceStatus (an enum, not a table)
    ├── sync_run_status.py        SyncRunStatus (an enum, not a table)
    ├── source_credentials.py     source_credentials
    ├── external_data_source.py   external_data_sources
    └── sync_run.py               sync_runs
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

A team also owns the systems it has connected, and the credentials those
connections use:

```
Team 1 ───── * SourceCredentials 1 ───── * ExternalDataSource 1 ───── * SyncRun
  │                                             ↑
  └─────────────────────────────────────────────┘
```

Both arrows into `ExternalDataSource` are real and neither is redundant. A
source's team is what it belongs to; its credential is what it authenticates
with, and one credential serves several sources — a single GitHub token reaches
every repository a team has connected. Reading a source's team through its
credential would break the moment a source has no credential yet, which is the
normal state during setup.

| Relationship | Reverse |
| --- | --- |
| `Department.job_titles` | `JobTitle.department` |
| `Department.users` | `User.department` |
| `Department.teams` | `Team.department` |
| `JobTitle.users` | `User.job_title` |
| `Team.creator` | `User.created_teams` |
| `Team.team_members` | `TeamMember.team` |
| `User.team_membership` | `TeamMember.user` |
| `Team.source_credentials` | `SourceCredentials.team` |
| `Team.external_data_sources` | `ExternalDataSource.team` |
| `SourceCredentials.external_data_sources` | `ExternalDataSource.credential` |
| `ExternalDataSource.creator` | `User.created_external_data_sources` |
| `ExternalDataSource.sync_runs` | `SyncRun.external_data_source` |

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

The data source group follows the same rule, and it matters more there than
anywhere else. Deleting a team cannot take its credentials or its ingestion
history with it; deleting an `ExternalDataSource` fails while `sync_runs` still
point at it, so a source cannot be disconnected in a way that erases the record
of what it ingested. Deleting a `SyncRun` never touches its source — the foreign
key only runs one way. Deleting a `SourceCredentials` row that a source still
references is refused by the database, which is the point: revoking a credential
is `status = INACTIVE` on the sources that used it, not a delete that leaves them
pointing at nothing.

`external_data_sources.credential_id` is the one nullable link in the group, and
that is a workflow decision rather than a weaker rule — see
[`source_credentials`](#source_credentials) below.

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

## `source_credentials`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `team_id` | `UUID` | → `teams.id`, NOT NULL |
| `credential_type` | `credential_type` | NOT NULL |
| `secret_reference` | `VARCHAR(512)` | nullable |
| `encrypted_secret` | `TEXT` | nullable |
| `credential_metadata` | `JSONB` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one set of credentials a team holds for one provider.

```
Backend Team
 ├── GitHub credential
 ├── Atlassian credential
 └── Slack credential
```

**There is no token column and there will never be one.** This is the same rule
`users` follows with `password_hash`, and for the same reason: the columns that
exist are named for what they hold, so no future code has a plausible-looking
place to put a secret in the clear. Nothing in this version encrypts or resolves
anything — `encrypted_secret` holds ciphertext when a credential service exists to
write it, and `secret_reference` holds a pointer such as a Vault path or an AWS
Secrets Manager ARN when that is the strategy instead.

**Both are nullable, and no `CHECK` requires one of the two.** The two are
alternatives, not a pair, and a row is legitimately written before either is
filled in — see the ordering below. Which one a deployment uses is a decision the
credential service makes; the table refuses to prejudge it.

`credential_metadata` is for the non-secret half of a credential — the part that
lets a human recognise a row without decrypting it:

```json
{ "auth_type": "API_TOKEN", "account_email": "ingest@company.com" }
```

Nothing secret goes in it. It is JSON for the same reason `config` is, one table
down.

**Credentials are a table, not columns on `Team`.** A team holds several at once,
one per provider, and each has its own lifecycle — rotated, revoked, replaced —
while the team carries on unchanged. On `Team` they would also sit in every query
that reads a team's name.

## `external_data_sources`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `team_id` | `UUID` | → `teams.id`, NOT NULL |
| `credential_id` | `UUID` | → `source_credentials.id`, **nullable** |
| `created_by_user_id` | `UUID` | → `users.id`, NOT NULL |
| `name` | `VARCHAR(255)` | NOT NULL, **not unique** |
| `source_type` | `source_type` | NOT NULL |
| `status` | `source_status` | NOT NULL, default `ACTIVE` |
| `config` | `JSONB` | nullable |
| `last_synced_at` | `TIMESTAMPTZ` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one connected thing: a repository, a Jira project, a Confluence space,
a Slack channel.

```
Backend Team
 ├── GitHub      TrackIt API        ACTIVE
 ├── JIRA        TRACK board        ACTIVE
 └── SLACK       #backend-team      ERROR
```

**`name` is a display name and carries no constraint.** Two teams may each have a
connection called "Main Repo", and even within one team the same repository
connected twice under different branches is a real configuration, not a mistake
for the schema to reject.

**`credential_id` is nullable on purpose.** Connecting a source runs in that
order: the `ExternalDataSource` row is written first, the credential is created
against it, and its id comes back onto the source. A source without a credential
is therefore a normal intermediate state, not a broken row — and refusing to sync
one is the service layer's job, recorded in [todo.md](todo.md) along with the rest
of that workflow.

**`config` is where connector-specific targeting lives**, one JSON column rather
than a union of every connector's columns:

```json
{ "repository": "Asteron-Labs/TrackIt", "branch": "main" }
{ "site_url": "https://company.atlassian.net", "project_key": "TRACK" }
{ "site_url": "https://company.atlassian.net", "space_key": "TR", "space_id": "6422530" }
{ "channel_id": "C0123456789" }
```

Four connectors with four different notions of "where" would otherwise mean a
dozen columns, all but three of them NULL on any given row, and a fifth connector
would mean a migration. The cost is that the database cannot check the shape of a
config, which is the connector's job anyway. **No secret goes in here** — that is
what the credential row is for.

**`last_synced_at` means the last ingestion that *completed*.** Nothing in this
entity writes it: no ORM default, no `onupdate`, no event listener. A future
ingestion service sets it when a `SyncRun` reaches `COMPLETED`, in the same
transaction, where the behaviour is visible and testable — the same reasoning that
keeps `Team.created_by_user_id` from implying a membership.

## `sync_runs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `external_data_source_id` | `UUID` | → `external_data_sources.id`, NOT NULL |
| `status` | `sync_run_status` | NOT NULL, default `PENDING` |
| `started_at` / `completed_at` | `TIMESTAMPTZ` | both nullable |
| `resources_processed` | `INTEGER` | NOT NULL, default `0` |
| `chunks_created` | `INTEGER` | NOT NULL, default `0` |
| `chunks_updated` | `INTEGER` | NOT NULL, default `0` |
| `chunks_deleted` | `INTEGER` | NOT NULL, default `0` |
| `error_message` | `TEXT` | nullable |
| `run_metadata` | `JSONB` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one execution of the ingestion pipeline against one source.

```
ExternalDataSource: GitHub TrackIt
 ├── SyncRun #1   COMPLETED
 ├── SyncRun #2   COMPLETED
 └── SyncRun #3   FAILED
```

**`started_at` and `completed_at` are both nullable and neither is `created_at`.**
A `PENDING` run has been recorded and has not begun, so it has neither; a `RUNNING`
one has only the first; a `FAILED` one may have both or only the first depending on
where it broke. `created_at` is when the row was written, which is when the run was
*queued* — keeping the three apart is what makes queue latency measurable later.

**The four counters are `NOT NULL` with a server-side `0`**, so a run that dies
before reporting anything reads as zero work done rather than as unknown. `NULL`
there would force every consumer to decide what a missing count means.

**`run_metadata`, not `metadata`.** A declarative class already owns `metadata` —
it is `Base.metadata`, the schema itself — so the attribute has to be named around
it. The name also lines up with `credential_metadata` one table up.

**There is no `team_id` here.** A run's team is its source's team, and copying the
column would create a second place for that fact to be wrong. If a query for "every
run in this team" ever needs the join removed, that is a measured decision with a
migration behind it, not a column added on speculation.

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

## `CredentialType`

```python
class CredentialType(str, Enum):
    GITHUB = "GITHUB"
    JIRA = "JIRA"
    CONFLUENCE = "CONFLUENCE"
    SLACK = "SLACK"
```

Stored on `source_credentials`, a native `credential_type` enum in PostgreSQL,
and a closed set for the reason every enum here is one: it is a branch in the
code. Whatever eventually uses a credential has to know how — HTTP Basic with an
account email for Jira and Confluence, a bearer token for GitHub, an `xoxb`
header for Slack — and a value with no branch behind it would be a row nothing
can authenticate with.

**It has the same four members as `SourceType`, and it is still not the same
column.** One says what a source *is*; the other says what a credential *opens*.
Keeping them apart is what lets the pairing be checked — a `JIRA` source holding
a `SLACK` credential is a mistake something can catch, and it is a mistake that
cannot even be expressed if the type is only read through the relationship.

**Jira and Confluence get one member each, though one Atlassian API token
authenticates both.** A team connecting Jira and Confluence therefore holds two
credential rows carrying the same secret, and rotating that token means updating
both. That is a deliberate trade: a single `ATLASSIAN` member would model the
token more accurately, but it would mean a credential type that no longer lines
up with the source it serves, and scoped Atlassian tokens can genuinely be
issued for one product and not the other. The cost lands on rotation, which is
service-layer work that does not exist yet, and is recorded in
[todo.md](todo.md).

## `SourceType`

```python
class SourceType(str, Enum):
    GITHUB = "GITHUB"
    JIRA = "JIRA"
    CONFLUENCE = "CONFLUENCE"
    SLACK = "SLACK"
```

Exactly the four connectors that exist in `app/connectors/`, and that is the
constraint on this enum: a fifth member would name a connector that cannot run.
It is a closed set for the same reason `ApplicationRole` is — each value is a
branch in the code, and no row inserted into a lookup table could give a fifth
value an implementation.

**There is no `DOCUMENT` member.** An uploaded file is not a connected source: it
has no credential, no config pointing at a remote system, and nothing to
re-sync. Putting it here would mean an `external_data_sources` row that every
ingestion path has to special-case. Documents get their own entity later.

## `SourceStatus`

```python
class SourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ERROR = "ERROR"
```

The state of the *connection*: `ACTIVE` is syncing normally, `INACTIVE` is switched
off deliberately, `ERROR` is switched off by failure. Deactivating rather than
deleting, the same choice `users.is_active` makes — a disconnected source's sync
history still has to resolve.

## `SyncRunStatus`

```python
class SyncRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
```

**`SourceStatus` and `SyncRunStatus` are not the same fact and must not be
collapsed**, in the way `ApplicationRole` and `MemberRole` are not. One is the
standing state of a connection, the other is the outcome of one execution against
it. A source stays `ACTIVE` through a run that fails — a network blip is not a
broken connection — and only a service decision, after enough failures or an
authentication error, moves it to `ERROR`.

```
GitHub TrackIt   status = ACTIVE       (on the source)
  └── SyncRun #3 status = FAILED       (on that one run)
```

Which failures move a source to `ERROR`, and what clears it, is not decided here;
it is in [todo.md](todo.md).

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

ix_source_credentials_team_id              INDEX
ix_external_data_sources_team_id           INDEX
ix_external_data_sources_credential_id     INDEX
ix_external_data_sources_created_by_user_id INDEX
ix_external_data_sources_source_type       INDEX
ix_external_data_sources_status            INDEX
ix_sync_runs_external_data_source_id       INDEX
ix_sync_runs_status                        INDEX
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

**The data source group has no unique constraint at all**, so every foreign key
there carries its own index — there is no composite constraint to serve as one.
`source_type` and `status` are indexed beside them because both carry a listing
query rather than a lookup: "every GitHub source" and "every source in `ERROR`"
are how this table gets read outside a single connection's page, and the same
holds for `sync_runs.status` — "what is still `RUNNING`" is the question a
scheduler asks.

Low-cardinality columns like these are a poor fit for a btree index once a table is
large, and if these ever grow to where that matters the answer is a partial index
on the interesting value rather than the full one. At the current size the plain
index is the right trade, and it is worth knowing which way it would change.

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

The data source group adds more of them, and they are the ones most worth knowing
about before writing against these tables: a source's `credential_id` may be
`NULL`, a `config` may hold any JSON at all including the wrong shape for its
`source_type`, a credential may hold neither a `secret_reference` nor an
`encrypted_secret`, and a credential belonging to one team can be referenced by a
source belonging to another. Every one of those is a valid row today. They are
collected in [todo.md](todo.md) rather than repeated here, because each one is
work with an owner, not a gap in the schema.

## Import graph

```
application_role.py     ->  (nothing)
member_role.py          ->  (nothing)
credential_type.py      ->  (nothing)
source_type.py          ->  (nothing)
source_status.py        ->  (nothing)
sync_run_status.py      ->  (nothing)
base.py                 ->  (nothing in the project)
department.py           ->  base
job_title.py            ->  base
user.py                 ->  base, application_role
team.py                 ->  base
team_member.py          ->  base, member_role
source_credentials.py   ->  base, credential_type
external_data_source.py ->  base, source_type, source_status
sync_run.py             ->  base, sync_run_status
```

No entity module imports another entity module at runtime — not across any of
the three groups. They name each other only as strings in their relationships,
with the concrete types pulled in under `if TYPE_CHECKING:` for the annotations,
so no cycle can form. `Team` referring back to `Department` and `Department`
referring forward to `Team` costs nothing at import time, and neither does
`SourceCredentials` and `ExternalDataSource` naming each other from inside the
same group.

The consequence: **importing a module is what makes its mapper real to
SQLAlchemy**, and the three groups now depend on each other's mappers.
`Department.teams` needs `Team` to exist before `configure_mappers()` can resolve
it, `Team.external_data_sources` needs `ExternalDataSource`, and so on across all
three groups. Import the top-level package:

```python
from app.entities import ExternalDataSource, SourceCredentials, SyncRun, Team, User
```

`app/entities/__init__.py` imports all three groups, which registers every mapper
and fills `Base.metadata`.

Importing a single group happens to work too — `import app.entities.teams` runs
`app/entities/__init__.py` first, because Python imports a parent package before
its child, and that is the file which pulls in the other two. It is worth knowing
that this is *why* it works, and not to build on it: it is a side effect of where
the imports sit, and it would stop being true the moment `__init__.py` imported
groups lazily. Import the package that promises every mapper, not the one that
happens to reach them.

## Not implemented yet

These are the first three entity groups of a larger model. Deliberately absent,
and not to be assumed: `Document`; `Resource`, `ResourceAccessScope`; `Chunk`,
`ChunkType`, `Embedding`; `ChatSession`, `ChatSessionMessage`, `Citation`. Also
absent: authentication endpoints, JWT, password hashing, login, CRUD APIs, team
services, source connection endpoints, the ingestion orchestrator, credential
encryption, authorisation policies, repositories and migrations. The service-layer
work these entities specifically wait on is in [todo.md](todo.md).

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

That prints the six `CREATE TYPE`s, the eight `CREATE TABLE` statements and the
fourteen indexes, without connecting to anything. Swapping in
`create_engine("sqlite://")` and a real `create_all` is a working smoke test —
on SQLite the native enum degrades to `VARCHAR` with a `CHECK`, which is
expected, and the unique constraints still reject a second team for a user who
already has one, or a duplicate team name within a department.

That SQLite pass is also what the JSON columns are shaped for. `config`,
`credential_metadata` and `run_metadata` are declared as
`JSON().with_variant(JSONB, "postgresql")` rather than as `JSONB` outright: the
PostgreSQL DDL above still reads `JSONB`, with everything `JSONB` gives — the
binary representation, the containment operators, the ability to index inside a
document — while the same metadata still builds on SQLite for a test. It is the
same trade `UUIDMixin` makes with `Uuid` over `postgresql.UUID`, one type down.
