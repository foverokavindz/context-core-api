# Entities

[← Documentation](README.md)

The first database layer in the project, and the first thing here that is not
part of the ingestion pipelines.

Everything in `app/models/` is a pydantic DTO: a pipeline boundary type that is
handed to a chunker or serialised into a response, and then gone. Nothing in it
is a table. `app/entities/` is the other thing — rows that outlive a request.
The two live in separate packages so neither drifts into doing the other's job.

This page covers seven groups: **organization** — departments, job titles, and
the people in them — **teams**, the working groups those people are actually in,
**data_sources**, the external systems a team has connected and the record of
each attempt to ingest from them, **documents**, the files uploaded straight into
the application rather than fetched from anywhere, **knowledge_sources**, the
searchable items both origins produce, **chunks**, the embeddable pieces those
items are cut into and the unit a retrieval step ranks, and **chat**, the
conversations held against all of it and the sources each answer rested on.

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
├── data_sources/
│   ├── credential_type.py        CredentialType (an enum, not a table)
│   ├── source_type.py            SourceType (an enum, not a table)
│   ├── source_status.py          SourceStatus (an enum, not a table)
│   ├── sync_run_status.py        SyncRunStatus (an enum, not a table)
│   ├── source_credentials.py     source_credentials
│   ├── external_data_source.py   external_data_sources
│   └── sync_run.py               sync_runs
├── documents/
│   ├── document_status.py        DocumentStatus (an enum, not a table)
│   └── document.py               documents
├── knowledge_sources/
│   ├── resource_type.py          ResourceType (an enum, not a table)
│   ├── resource_access_scope.py  ResourceAccessScope (an enum, not a table)
│   └── resource.py               resources
├── chunks/
│   ├── chunk_type.py             ChunkType (an enum, not a table)
│   └── chunk.py                  chunks
└── chat/
    ├── message_role.py           MessageRole (an enum, not a table)
    ├── chat_session.py           chat_sessions
    ├── chat_session_message.py   chat_session_messages
    └── citation.py               citations
```

`chunks` has its own package rather than sitting beside `resources`, which is
where it started. A chunk is the retrieval unit and the only table carrying a
vector, and it is read by `chat` as often as it is written by ingestion — so it
is a group of its own rather than a tail of the group that produces it.

**The engine, the session factory and the migrations now exist**, and they live
outside this package: `app/core/database.py` holds the engine and the one reader
of `DATABASE_URL`, and `alembic/versions/` holds the migrations that turn these
descriptions into tables on a server. See [migrations.md](migrations.md).

What has not changed is this package. `Base.metadata` is still a *description* of
a schema, nothing in `app/main.py` imports it, and no endpoint reads or writes a
row yet — so the running API is unchanged. Note in particular that nothing calls
`Base.metadata.create_all()`: the tables on a server are the ones the migrations
created, and [migrations.md](migrations.md#create_all-is-not-used) explains why
having both would be worse than having either.

`sqlalchemy>=2.0` and `psycopg[binary]` are runtime dependencies, the second
being the driver — psycopg 3, which is why every URL is spelled
`postgresql+psycopg`. `pgvector` is a runtime dependency too, and it is *not* a
driver: it is a pure-python package that contributes a SQLAlchemy type, which
`chunks.embedding` needs at import time even though nothing writes a vector yet.
See [`chunks`](#chunks) below.

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

What a source *produced* hangs off it separately, and that chain is the whole
point of the schema:

```
ExternalDataSource 1 ───── * Resource 1 ───── * Chunk
```

A `SyncRun` counts resources and chunks; it does not own them. A run is the
record of an *attempt*, and its counters are still true after the rows it wrote
have been replaced by a later run — which is why `resources.sync_run_id` does not
exist. A resource belongs to the source it came from, not to the execution that
happened to fetch it that day.

**A resource has a second origin, and it is a person rather than a system.** An
uploaded file is a `Document`, and a document becomes exactly one resource:

```
User 1 ───── * Document 1 ───── 1 Resource 1 ───── * Chunk
```

Both chains end in the same two tables, which is the point. Whether an answer
came from a Confluence page or a PDF somebody uploaded is a fact about the
resource, not a different retrieval path — `resources` and `chunks` do not care
which arrow reached them.

The second arrow is worth a footnote: it is a foreign key, but not a `resource_id`
on `chunks`. A chunk names its resource by `(external_data_source_id,
external_id)`, which is a real composite foreign key for the first chain and
matches nothing at all for the second, because a document-origin resource has both
columns `NULL`. `Resource.chunks` works normally over the first and is always
empty over the second. See [`chunks`](#chunks) and [todo.md](todo.md).

The other thing a user owns is their conversations, and those close the loop back
onto the corpus:

```
User 1 ───── * ChatSession 1 ───── * ChatSessionMessage 1 ───── * Citation
                                                                    │
                                                          ┌─────────┴─────────┐
                                                        Chunk              Resource
```

A citation hangs off the **message**, not the session. A session holds many
answers and each one rested on its own sources, so hanging them off the session
would lose which answer cited what — the one thing the table exists to record.

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
| `ExternalDataSource.resources` | `Resource.external_data_source` |
| `Team.resources` | `Resource.team` |
| `Department.resources` | `Resource.department` |
| `Resource.chunks` | `Chunk.resource` |
| `Team.chunks` | `Chunk.team` |
| `Department.chunks` | `Chunk.department` |
| `User.documents` | `Document.uploader` |
| `Document.resource` | `Resource.document` |
| `User.chat_sessions` | `ChatSession.user` |
| `ChatSession.messages` | `ChatSessionMessage.chat_session` |
| `ChatSessionMessage.citations` | `Citation.message` |
| `Chunk.citations` | `Citation.chunk` |
| `Resource.citations` | `Citation.resource` |

`User.team_membership` is the only singular one on the organization side:
`TeamMember | None`, not a list, because the unique constraint means a list could
never hold more than one element. `User.created_teams` beside it *is* a list —
creating teams is not limited, only belonging to them.

`Document.resource` is singular for exactly the same reason and it is the only
other one: `resources.document_id` is `UNIQUE`, so one uploaded file becomes one
searchable item. A hundred-page policy does not become a hundred resources — it
becomes one resource and a hundred chunks, which is what chunks are for.

`ChatSession.messages` is named for what it holds rather than after its class, so
it does not read `ChatSession.chat_session_messages`. `Citation.message` is the
same trim on the other side.

**One relationship carries a delete cascade, and it is `Resource.chunks`.**
Everywhere else the answer is no — not even `team_members`, which is a join table
and the place a cascade would look most routine. Deleting a department must never
take its users with it; people outlive reorganisations. The foreign keys are left
at the database default (`NO ACTION`), so rows still pointing at a parent stop its
deletion and a human decides where they go. Reassignment will be an explicit
operation in the service layer.

Chunks are the exception because they are not independent facts. A chunk is a
slice of its resource's text and exists only to be embedded; re-ingesting a
resource replaces every one of them, and a chunk whose resource is gone is not a
row anybody has to decide about — it is debris. `cascade="all, delete-orphan"` on
`Resource.chunks` says exactly that, and it is the only place in this schema that
says it. See [`chunks`](#chunks) for what the cascade does and does not cover.

The join runs over `(external_data_source_id, external_id)` rather than over
`resources.id`, and that changes nothing here. A composite foreign key is still a
foreign key: SQLAlchemy infers the condition from the constraint on `chunks`,
and appending to `resource.chunks` fills both columns on the chunk the same way
a single-column relationship would fill one.

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

**Citations are the case where the rule looks wrong and is not.** A citation is
derived, the way a chunk is, so a second cascade would be the obvious move — and
deleting a chat message that has citations is refused instead. The difference is
what the row is *for*: a chunk exists to be embedded and re-ingestion replaces it,
while a citation is the record that an answer was built on a particular piece of
text. Losing it silently to a delete somewhere else would leave an answer that
claims sources it can no longer name. `Resource.chunks` stays the only cascade in
the schema; deleting a message, a chunk or a resource that has been cited is a
decision, and it is in [todo.md](todo.md).

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
| `token` | `VARCHAR(2048)` | nullable, **plain text** — see below |
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
what `token` one row down is for, and what the credential row is for after that.

**`token` is a deliberate shortcut and the one place this schema knowingly holds
a secret badly.** It is the access token a source authenticates with, written by
`POST /api/v1/ingestData/{external_source}` so a later sync can re-run an
ingestion without asking the user for it again. It is stored as sent — not
hashed, which would make it useless, and not encrypted, which is the thing that
has not been built.

The right home for it is a `source_credentials` row and `encrypted_secret`, and
that is where it goes. Connecting a source is three writes and a service that
owns all of them; this phase does the first one, leaves `credential_id` NULL, and
puts the token where the ingestion path can reach it. Both halves of that debt
are in [todo.md](todo.md).

What the shortcut does *not* do is loosen the rule about where a secret may
appear. `token` is never logged, never returned by any endpoint, and never
written into a run file — the run file's source block is assembled field by
field rather than dumped, precisely so this column cannot ride along by
accident. See [security.md](security.md).

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

## `documents`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `uploaded_by_user_id` | `UUID` | → `users.id`, NOT NULL |
| `original_file_name` | `VARCHAR(512)` | NOT NULL |
| `display_name` | `VARCHAR(512)` | nullable |
| `mime_type` | `VARCHAR(255)` | nullable |
| `file_size` | `BIGINT` | NOT NULL |
| `storage_path` | `VARCHAR(1024)` | NOT NULL |
| `checksum` | `VARCHAR(64)` | nullable |
| `status` | `document_status` | NOT NULL, defaults to `UPLOADED` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one file somebody put into the application by hand, normally HR — a
leave policy, a handbook, a contract template. It is the other half of
[`resources`](#resources): everything else in the corpus was fetched from a system
a team connected, and this is the part that was not.

```
HR uploads leave-policy.pdf
   ↓
Document      status = UPLOADED
   ↓
Resource      resource_type = DOCUMENT
   ↓
Chunk 0..n    chunk_type = DOCUMENT_SECTION
```

**The bytes are not in this table.** `storage_path` is where they are — a
filesystem path, an S3 key, whatever the deployment ends up using — and which of
those it is has not been decided. A `BYTEA` column would make every query that
touches a document's name pay for its content, and would put a fifty-megabyte PDF
inside a transaction log.

**`uploaded_by_user_id` records who put the file there and nothing else.** It is
not a permission: who may *read* the document is decided by the resource it
produces, through the same `access_scope` every other resource uses. An HR user
uploading a policy for the whole company and one uploading a document for a single
team write the same row here and different ones there.

**`original_file_name` is kept unchanged**, so an upload can always be traced back
to what the user actually sent, and `display_name` is the friendlier title beside
it. `display_name` is nullable rather than defaulted to the file name, which keeps
"nobody named this" distinguishable from "somebody named it after the file" — the
same reasoning `resources.title` uses.

**`mime_type` is nullable and `file_size` is not.** The size comes from the stored
object and is always known once there is a `storage_path` to point at. The type
comes from the client's `Content-Type`, which may be absent and may be wrong, and
a parser that trusts it finds out either way. It is the same call `chunk_type`
makes: a wrong value is worse than none.

`file_size` is `BIGINT` rather than `INTEGER` because the ceiling on an uploaded
file is a storage policy, not an integer width, and a two-gigabyte video in an HR
folder should fail as the former.

**`checksum` has room for a sha256 hex digest** and is indexed, so a re-upload of
a file already held can be recognised rather than silently duplicated. Nothing
here computes it — the upload path does, the same way the ingestion service owns
`chunks.content_hash`. It is the same width for the same reason.

## `resources`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `external_data_source_id` | `UUID` | → `external_data_sources.id`, **nullable** |
| `document_id` | `UUID` | → `documents.id`, nullable, **UNIQUE** |
| `resource_type` | `resource_type` | NOT NULL |
| `external_id` | `VARCHAR(512)` | nullable |
| `title` | `VARCHAR(1024)` | nullable |
| `version_key` | `VARCHAR(255)` | nullable |
| `access_scope` | `resource_access_scope` | NOT NULL |
| `team_id` | `UUID` | → `teams.id`, nullable |
| `department_id` | `UUID` | → `departments.id`, nullable |
| `resource_metadata` | `JSONB` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one original piece of knowledge, *before* it was cut up: a file, an
issue, a page, a message, and later an uploaded document.

```
GitHub file       → Resource(GITHUB_FILE)
Jira issue        → Resource(JIRA_ISSUE)
Confluence page   → Resource(CONFLUENCE_PAGE)
Slack message     → Resource(SLACK_MESSAGE)
Uploaded PDF      → Resource(DOCUMENT)
```

**A resource has two possible origins and exactly one of them is ever set.**
`external_data_source_id` points at the connected system it was fetched from and
is `NULL` for anything uploaded; `document_id` points at the uploaded file and is
`NULL` for anything fetched. Both are nullable, and a `CHECK` says one of them is
not:

```sql
CHECK ((external_data_source_id IS NULL) <> (document_id IS NULL))
```

Written as a comparison of two `IS NULL` tests rather than as `num_nonnulls()`,
which is PostgreSQL's alone — this form compiles on SQLite too, so the constraint
is real in the tests as well as on the server. It rejects both directions: a
resource claiming two origins, and one claiming none.

This is the one place the schema *did* get a `CHECK` where it usually defers to a
service, and the reason is that it is a structural rule rather than a policy one.
"A resource came from somewhere" needs no code to have an opinion first, unlike
the `access_scope` pairing two paragraphs down, which is a rule an authorisation
layer has to state before a constraint can enforce it.

`document_id` is also **`UNIQUE`**, so one uploaded file becomes one resource. A
document that needs splitting produces chunks, not a second resource. `NULL`s stay
distinct in SQL, so every source-origin row is unaffected — the same property
`UNIQUE(external_data_source_id, external_id)` relies on just below.

**`external_id` is how the *source* names the item**, and it only means anything
inside that source:

```
GitHub      src/auth/auth.service.ts
Jira        TRACK-25
Confluence  7110680
Slack       C0123456789:1786134970.186879
```

Slack has no single id for a message, so the stable pair — channel and timestamp —
is joined into one. Re-ingestion is what this column is for: it is how a second
run recognises the item it already wrote a row for, rather than inserting a
duplicate.

**`version_key` is optional provenance**, one column rather than one per source:
a commit SHA for GitHub, a page version number for Confluence, a checksum for a
document. Jira and Slack have nothing to put in it, so it is nullable and stays
nullable — a `github_commit_sha` column would be `NULL` on three quarters of this
table and would need a sibling for every source added later.

**`resource_metadata` is the source-specific remainder**, the same trade
`external_data_sources.config` makes one table up:

```json
{ "repository": "Asteron-Labs/TrackIt", "branch": "main", "file_path": "src/auth/auth.service.ts" }
{ "issue_key": "TRACK-25", "parent_key": "TRACK-10" }
{ "space_key": "TR", "page_id": "7110680" }
{ "channel_id": "C0123456789", "message_ts": "1786134970.186879" }
```

**No permission field goes in here.** Access is decided by three real columns, and
a rule that lives inside a JSON document is a rule no index can serve and no
constraint can ever check.

**The resource is the source of truth for who may read it.** `access_scope` says
which rule applies and the two nullable foreign keys say who it applies to:

| `access_scope` | `team_id` | `department_id` | Who reads it |
| --- | --- | --- | --- |
| `TEAM` | the owning team | `NULL` | members of that team |
| `DEPARTMENT` | `NULL` | the owning department | everyone in that department |
| `ORGANIZATION` | `NULL` | `NULL` | every authenticated user |

**Nothing in the schema enforces that pairing.** A `TEAM` resource with a `NULL`
`team_id` is a valid row today, as is an `ORGANIZATION` one with a `team_id` set.
A `CHECK` could express it, and the reason there is not one yet is that the rule
belongs with the authorisation code that reads these columns, which does not
exist. It is in [todo.md](todo.md) with the rest.

## `chunks`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `external_data_source_id` | `UUID` | nullable, half of the key naming this chunk's resource |
| `external_id` | `VARCHAR(512)` | nullable, the other half |
| `chunk_index` | `INTEGER` | NOT NULL, unique *within* the resource |
| `chunk_type` | `chunk_type` | nullable |
| `content` | `TEXT` | NOT NULL |
| `embedding` | `VECTOR(1536)` | nullable |
| `embedding_model` | `VARCHAR(255)` | nullable |
| `content_hash` | `VARCHAR(64)` | nullable |
| `chunk_metadata` | `JSONB` | nullable |
| `access_scope` | `resource_access_scope` | NOT NULL, a **copy** of the resource's |
| `team_id` | `UUID` | → `teams.id`, nullable, a **copy** |
| `department_id` | `UUID` | → `departments.id`, nullable, a **copy** |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one embeddable piece of a resource, and this is the table retrieval
will actually read.

```
Resource: src/auth/auth.service.ts
 ├── Chunk 0   CLASS     AuthService
 ├── Chunk 1   METHOD    login
 └── Chunk 2   METHOD    refresh
```

**`content` and `embedding` live in the same row**, which is the point of the
table: a vector search ranks rows and the text it ranked comes back with them,
with no second lookup and no chance of the two drifting apart.

**`embedding` is `vector(1536)` on PostgreSQL and `JSON` everywhere else.** It is
declared as `JSON().with_variant(Vector(1536), "postgresql")` — the same shape
`config`, `credential_metadata` and `run_metadata` use with `JSONB`, and the same
trade `UUIDMixin` makes with `Uuid`. PostgreSQL gets a real pgvector column with
everything that implies, and SQLite gets a JSON array, so `create_all` in a test
still builds a table this project can insert into.

Two things that column does *not* do. It does not create the extension — a real
server needs `CREATE EXTENSION vector`, and that is the first statement of the
first migration rather than anything this column can express. And it has no
`ivfflat` or `hnsw` index; an approximate-nearest-neighbour index is tuned
against a real corpus and a real dimension, and adding one against an empty table
would be guesswork, so it waits for a migration of its own. See
[migrations.md](migrations.md).

**1536 is pinned deliberately.** It is the width of `text-embedding-3-small` and
`text-embedding-ada-002`. A different model means a different number, and a
different number means a migration whichever way the column had been declared, so
there is nothing to be gained by leaving it open.

`embedding_model` records which model produced the vector, so a model change can
be found rather than inferred. `content_hash` has room for a sha256 hex digest and
exists so re-ingestion can skip re-embedding text that did not change. **Neither
is computed here** — no default, no event, no `hashlib` import. The ingestion
service hashes and embeds, and the entity holds what it wrote.

**`chunk_type` is nullable**, unlike `resource_type`. A chunker that only splits
prose has nothing honest to put there, and a wrong value is worse than none:

```
GitHub      FILE / CLASS / METHOD / FUNCTION
Jira        ISSUE
Confluence  PAGE
Slack       MESSAGE
Document    DOCUMENT / DOCUMENT_SECTION
```

`chunk_metadata` holds what is true of *this piece* and not of the whole
resource — `{"symbol_name": "login", "parent_symbol": "AuthService", "start_line":
25, "end_line": 62}`. The repository and branch are already on the resource, and
copying them onto every chunk would be a second place for them to be wrong.

### How a chunk names its resource

`(external_data_source_id, external_id)`, tied to `resources` by a composite
`FOREIGN KEY` — the pair that table already declares `UNIQUE`. There is no
`resource_id` column.

The reason is what a run knows and when. `resources.id` is generated at insert,
so linking by it means writing every resource, reading its id back, and only then
writing chunks. A file's path is known while it is still being parsed, so keying
on it lets a run build every resource row and every chunk row in memory and insert
both without a round trip in between.

`Resource.chunks` and `Chunk.resource` are ordinary relationships over that pair.
SQLAlchemy infers the join from the constraint, appending to `resource.chunks`
fills both columns on the chunk, and the cascade behaves exactly as a
single-column one would — the composite key costs nothing at the ORM level.

The pipeline fills both columns anyway, without waiting for the ORM to do it.
`external_data_source_id` is stamped onto every chunk by `_apply_source_context`,
and each chunker sets `external_id` from the item it rendered — the file path, the
issue key, the page id, the `channel:timestamp` pair. That is what lets a whole
run be inserted in one pass: the chunks already know which resource they belong to
before either row exists.

**`UNIQUE(external_data_source_id, external_id, chunk_index)`** means a resource
cannot hold the same position twice, so a re-run that writes chunk 3 again
collides instead of quietly duplicating it. It takes three columns rather than
two for the same reason `resources` scopes its own uniqueness to the source:
`external_id` means nothing on its own, and two repositories each holding a
`README.md` would otherwise collide at every index.

**Neither constraint reaches a document-origin chunk.** An uploaded document's
resource has both columns `NULL`, and a composite foreign key is satisfied under
`MATCH SIMPLE` the moment either column is — so such a chunk passes the constraint
while pointing at nothing, and because NULLs are distinct, its `chunk_index` is
not unique either. Nothing uploads a document yet, so nothing writes such a row;
it is in [todo.md](todo.md) as work owed rather than left to be discovered.

### Why the permission columns are here twice

`access_scope`, `team_id` and `department_id` are on `chunks` as well as
`resources`, and they are **denormalized copies**. The resource remains
authoritative.

They are copied because of the query this table exists to serve. Retrieval
filters on permission and *then* ranks by distance, over chunk rows:

```sql
WHERE access_scope = 'ORGANIZATION'
   OR (access_scope = 'TEAM'       AND team_id IN (...))
   OR (access_scope = 'DEPARTMENT' AND department_id = ...)
```

Reaching those values through `resources` would mean a join inside the hot path of
every search, and would defeat an index the vector search wants to use directly.
None of that query is implemented yet; the columns it needs are.

**Nothing keeps the copy in step, on purpose.** No ORM event, no `default`, no
validator, no trigger. A chunk whose `team_id` disagrees with its resource's is a
valid row that this schema will accept, and the fix is not entity-level magic —
the ingestion service creates chunks from the resource's permission context, in
one place, where it can be read and tested:

```
Resource  access_scope = TEAM, team_id = Backend Team
    ↓ chunk creation
Chunk     access_scope = TEAM, team_id = Backend Team
```

That is the same reasoning that keeps `last_synced_at` out of an `onupdate` and
`Team.created_by_user_id` from implying a membership. The drift it allows is in
[todo.md](todo.md).

### What the cascade covers

`Resource.chunks` is `cascade="all, delete-orphan"`, and it is **a SQLAlchemy
session behaviour, not `ON DELETE CASCADE`**. The foreign key itself is left at
`NO ACTION` like every other one here.

The difference matters. Deleting a resource *through a session* issues the
`DELETE` for its chunks first and both go. A raw `DELETE FROM resources` in psql
is still refused by the database while chunks point at it — which is the safe way
round: the convenience exists for the code that owns these rows, and a hand-written
statement gets no help it did not ask for.

Deleting a chunk never touches its resource; the relationship only runs one way.
And nothing above resource is affected — deleting a `Team`, a `Department` or an
`ExternalDataSource` is still refused while resources reference them, so no
disconnect or reorganisation can quietly erase a corpus.

One thing the cascade does **not** reach is citations. A chunk that has been cited
cannot be deleted, and that includes being deleted as part of its resource's
cascade — the `DELETE` for the chunks is issued and the database refuses it. A
re-ingestion that replaces the chunks of a resource somebody has already asked
about will hit this, and it is a real decision rather than an oversight; see
[todo.md](todo.md).

**A document-origin resource has an empty `chunks` collection, always.** The join
compares `external_data_source_id` and `external_id`, both of which are `NULL` on
such a row, and `NULL = NULL` is never true in SQL. Its chunks are therefore
unreachable through the relationship and untouched by the cascade — which is the
same hole the constraints have, wearing different clothes. See
[todo.md](todo.md).

## `chat_sessions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `user_id` | `UUID` | → `users.id`, NOT NULL |
| `title` | `VARCHAR(255)` | nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one conversation, and it belongs to one person. There is no
`team_id` and no sharing: a session is private to its user, and what that user is
*allowed to see inside it* is decided per resource by `access_scope`, not per
session.

**`title` is nullable** because a session exists from the moment it is opened and
nothing has named it yet. Whether the name is typed by the user or derived from
the first question is a product decision this table does not need to make.

## `chat_session_messages`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `chat_session_id` | `UUID` | → `chat_sessions.id`, NOT NULL |
| `role` | `message_role` | NOT NULL |
| `content` | `TEXT` | NOT NULL |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

**One row is one turn**, never a question and its answer together:

```
ChatSession "Leave questions"
 ├── USER       "How much leave do I accrue?"
 ├── ASSISTANT  "1.75 days per month."
 ├── USER       "Does it carry over?"
 └── ASSISTANT  "Up to 10 days, per section 4.2."
```

A single row holding both halves would be smaller and would break the thing this
table exists for: a citation points at *the assistant's answer*, and there would
be nothing to point at. It also makes the history a plain ordered read rather than
a shape that has to be unpacked, and it leaves room for a turn that is not a pair —
a system note, a regenerated answer, an answer that never came.

**There is no sequence column.** Order is `created_at`, which the mixin already
provides. A message's position in its session is not a fact the writer should have
to compute, and two rows written in the same millisecond are a display question
rather than a correctness one.

**`updated_at` is here because every entity in this package has it**, not because
messages are expected to be edited. `TimestampMixin` is applied whole rather than
split into a created-only variant, and one column that stays equal to `created_at`
is cheaper than a second mixin that has to be kept in step with the first.

## `citations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` | primary key |
| `chat_message_id` | `UUID` | → `chat_session_messages.id`, NOT NULL |
| `chunk_id` | `UUID` | → `chunks.id`, NOT NULL |
| `resource_id` | `UUID` | → `resources.id`, NOT NULL |
| `citation_order` | `INTEGER` | NOT NULL, unique *within* the message |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

One row is one source behind one answer — the record of what retrieval actually
found and the answer actually used.

```
ASSISTANT "1.75 days per month."
    ├── Citation 1 → Chunk (Accrual section)   → Resource (Leave Policy)
    └── Citation 2 → Chunk (Carry-over)        → Resource (Leave Policy)
```

**It points at the message, not the session.** A session holds many answers and
each rested on its own sources; hanging citations off the session would lose which
answer cited what, which is the only thing this table records. A `USER` row simply
has none, and so does an `ASSISTANT` row that answered without retrieving anything —
both are real states rather than missing data.

**Both `chunk_id` and `resource_id` are stored, and the second is reachable
through the first.** That is deliberate. The two answer different questions:

```
chunk_id     which exact retrieved text supported this answer
resource_id  which file, issue, page, message or document it came from
```

Rendering a source list is the common read and it needs the second — a file path,
an issue key, a document title. Making it join through `chunks` to find that out
would put a join in the path of every answer, to recover something already known
at the moment the citation was written. The chunk stays there because "the policy
says so" and "*this paragraph* says so" are not the same claim, and only the chunk
can make the stronger one.

It is worth more than the hop it saves. `Chunk.resource` resolves to `None` for a
document-origin chunk, because the columns it joins on are `NULL` there — so for
that one origin the citation's own `resource_id` is the only way back to the
resource at all.

**`UNIQUE(chat_message_id, citation_order)`** means one answer cannot hold two
sources at the same position, so `[1]` and `[2]` survive a re-read rather than
being whatever order the rows happened to come back in. It is the same rule
`chunks` applies to `chunk_index` within a resource, and it has the same gap:
nothing requires the orders to run 1, 2, 3 without a hole.

**Nothing here renders anything.** There is no bracket, no footnote, no snippet
and no formatting — a citation is a foreign key with a position, and turning it
into text belongs wherever answers are rendered.

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

## `ResourceType`

```python
class ResourceType(str, Enum):
    GITHUB_FILE = "GITHUB_FILE"
    JIRA_ISSUE = "JIRA_ISSUE"
    CONFLUENCE_PAGE = "CONFLUENCE_PAGE"
    SLACK_MESSAGE = "SLACK_MESSAGE"
    DOCUMENT = "DOCUMENT"
```

What the original item *is*. Stored on `resources`, a native `resource_type` enum
in PostgreSQL, a closed set for the reason every enum here is one.

**It is not `SourceType` with different spelling, and this is the group where the
two most look alike.** `SourceType` says which system a connection talks to;
`ResourceType` says what came out of it. They are one-to-one today only because
each connector currently produces one kind of thing — a GitHub source that later
ingests pull requests as well as files would produce two `ResourceType`s from one
`SourceType`, and nothing about the schema would have to change.

**`DOCUMENT` is here and deliberately absent from `SourceType`.** An upload has no
credential, no config and nothing to re-sync, so it is not a connected source —
but it is very much a piece of knowledge, and it ends up in this table alongside
the rest. That asymmetry is the whole reason `resources` has two possible origins.

## `ResourceAccessScope`

```python
class ResourceAccessScope(str, Enum):
    TEAM = "TEAM"
    DEPARTMENT = "DEPARTMENT"
    ORGANIZATION = "ORGANIZATION"
```

Who may read a resource. Stored on **both** `resources` and `chunks` — one native
`resource_access_scope` type in PostgreSQL, used by two tables, which is why the
name is not `resource_scope` or `chunk_scope`.

Not a lookup table, and here that is a stronger claim than usual: these three are
not data an administrator adds to, they are three *branches* in an authorisation
check that does not exist yet. A fourth row inserted into a table could not give
itself a rule.

The scope names an audience, and the audience it names is looked up elsewhere —
`TEAM` means whatever `team_members` says, `DEPARTMENT` means whatever
`users.department_id` says. The resource stores the rule, not the list of people.

## `ChunkType`

```python
class ChunkType(str, Enum):
    FILE = "FILE"
    CLASS = "CLASS"
    METHOD = "METHOD"
    FUNCTION = "FUNCTION"

    ISSUE = "ISSUE"
    PAGE = "PAGE"
    MESSAGE = "MESSAGE"

    DOCUMENT = "DOCUMENT"
    DOCUMENT_SECTION = "DOCUMENT_SECTION"
```

The semantic shape of one chunk, grouped by where it comes from: the first four
from the TypeScript parser, then one each from Jira, Confluence and Slack, then
the two an uploaded document will produce.

Every member matches something a chunker in `app/ingestion/` produces today — that
is the constraint on this enum, and it is why there is no `PARAGRAPH`, no
`INTERFACE`, no `COMMENT`. Those are all plausible and none of them has code
behind it, and a value nothing writes is a value every reader has to guess at.
Adding one when its chunker exists is an `ALTER TYPE` in a migration.

Unlike the other enums here it is stored **nullable** — see [`chunks`](#chunks).

## `DocumentStatus`

```python
class DocumentStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
```

How far ingestion has got with an uploaded file. `UPLOADED` is the default and
the only value anything writes today, because the row is written the moment the
file is accepted and nothing yet parses one.

```
UPLOADED → PROCESSING → READY
                     ↘  FAILED
```

That is the intended path and **the schema enforces none of it** — the same as
`SourceStatus` and `SyncRunStatus` one group up. A `READY` document with no
resource is a valid row. It is in [todo.md](todo.md).

The four are about the *file*, not about the knowledge it produced. A `READY`
document has been parsed and chunked; whether anybody may read the result is
`access_scope` on its resource, and whether its chunks have vectors yet is
`chunks.embedding`. Three separate questions, three separate columns, and folding
them into one status would make each one unanswerable.

## `MessageRole`

```python
class MessageRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
```

Who said one turn. Two members and not three: there is no `SYSTEM`, because a
system prompt is configuration belonging to whatever calls the model, not a row in
a user's conversation history — storing one per session would mean re-reading it
on every replay and versioning it forever.

`ASSISTANT` is the only role a [`citation`](#citations) points at, and nothing in
the schema says so. A citation on a `USER` row is a valid row today, and it is in
[todo.md](todo.md) with the rest.

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

uq_resources_external_data_source_id_external_id  UNIQUE (external_data_source_id, external_id)
uq_chunks_external_data_source_id_external_id_chunk_index  UNIQUE (external_data_source_id, external_id, chunk_index)
fk_chunks_resource                         FOREIGN KEY (external_data_source_id, external_id)
                                             REFERENCES resources (external_data_source_id, external_id)
ck_resources_single_origin                 CHECK ((external_data_source_id IS NULL) <> (document_id IS NULL))
ix_resources_document_id                   UNIQUE INDEX
ix_resources_resource_type                 INDEX
ix_resources_external_id                   INDEX
ix_resources_access_scope                  INDEX
ix_resources_team_id                       INDEX
ix_resources_department_id                 INDEX
ix_chunks_chunk_type                       INDEX
ix_chunks_access_scope                     INDEX
ix_chunks_team_id                          INDEX
ix_chunks_department_id                    INDEX

ix_documents_uploaded_by_user_id           INDEX
ix_documents_status                        INDEX
ix_documents_checksum                      INDEX

uq_citations_chat_message_id_citation_order       UNIQUE (chat_message_id, citation_order)
ix_chat_sessions_user_id                   INDEX
ix_chat_session_messages_chat_session_id   INDEX
ix_citations_chunk_id                      INDEX
ix_citations_resource_id                   INDEX
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

**The knowledge group brings the rule back**, and the omissions it produces are
the ones most likely to look like mistakes. Neither
`resources.external_data_source_id` nor `chunks.external_data_source_id` has an
index of its own — each leads a composite unique constraint above, which already
builds that btree and answers "every resource from this source" and "every chunk
from this source" with it.

`chunks.external_id` has none either, and that is the subtler case. "Every chunk
of this resource" is a lookup on `(external_data_source_id, external_id)`, which
is a *prefix* of the three-column constraint and served by the same btree. Only a
search for chunks by `external_id` while not knowing the source would need an
index of its own, and nothing asks that — a run is always inside one source.

`resources.external_id` *does* get one, because there it is the constraint's
**last** column with nothing usable in front of it. That index is what a
re-ingestion uses to find the row it wrote last time.

**`UNIQUE(external_data_source_id, external_id)` scopes uniqueness to the source,
and it has to.** `TRACK-25` is a Jira key and means nothing outside its project;
two sources may legitimately each hold a file called `README.md`. A global unique
index on `external_id` would reject the second one. The constraint also handles
uploaded documents for free — both columns are `NULL` on those rows, SQL treats
NULLs as distinct, and any number of them coexist. It is the same property
`users.username` relies on, two groups up.

`resource_type`, `access_scope` and `chunk_type` are indexed for the reason
`source_type` and `status` are: each carries a listing or filtering query rather
than a lookup, and `access_scope` in particular sits in the `WHERE` clause of
every retrieval this table was built for. `team_id` and `department_id` are
indexed on both tables for the same reason — including on `chunks`, where they are
copies, because filtering chunk rows directly is the point of copying them.

The same caveat about low-cardinality btrees applies here more than anywhere:
`access_scope` has three values and `chunks` is the table expected to grow into
the millions. A partial index on the interesting scope, or a composite with
`team_id`, is where that goes when there is a real corpus to measure. The plain
index is the right starting point and the wrong finishing point.

**`ix_resources_document_id` is the one index that changed shape.** It was a plain
index while `document_id` referenced nothing; it is a unique index now, which is
what makes `Document.resource` a single object rather than a list. The uniqueness
and the lookup are one structure, so nothing was added to get it.

**The last three groups add seven indexes and one unique constraint between them**,
and the pattern is the one already established. `citations.chat_message_id` gets no
index of its own — it leads `UNIQUE(chat_message_id, citation_order)`, which
already answers "every source behind this answer". `chunk_id` and `resource_id` do
get one each, because the questions they carry run the other way: "every answer
that cited this chunk", and "how often has this document been used". Those are the
reads that make a citation worth storing rather than recomputing.

`documents.checksum` is indexed to recognise a re-upload of a file already held,
and `status` for the same reason `external_data_sources.status` is — "what is
still `PROCESSING`" is a listing query, and the same low-cardinality caveat applies
to it.

**No `created_at` is indexed, on any table.** Ordering a session's messages or a
user's sessions by time is served by the foreign-key index plus a sort, over a
handful of rows in the first case and a modest list in the second. The index worth
having when either grows is composite — `(chat_session_id, created_at)`,
`(user_id, created_at)` — and a bare index on `created_at` would not be a step
towards it. That is in [todo.md](todo.md) rather than in the schema, because
choosing between them needs a query plan against real volume.

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

The knowledge group adds two more, and they are the largest of the set because
they concern who can read what. A resource's `access_scope` is not checked against
its `team_id` and `department_id`, so `TEAM` with no team is a valid row. And a
chunk's three permission columns are not checked against its resource's, so they
can drift. Both are in [todo.md](todo.md), and neither is a constraint the
database should be given before the service that maintains it exists — a `CHECK`
written now would be enforcing a rule no code has yet had to state out loud.

A third one from that group is now gone: `resources.document_id` referenced
nothing while `documents` did not exist, and the rule that a resource has exactly
one origin was a service check waiting for a table. Both arrived together — the
foreign key and `ck_resources_single_origin` — which is the shape these items are
meant to take when they are closed.

The document and chat groups add the smaller kind. `DocumentStatus` transitions
are unenforced, the same as every other status enum here. Nothing checks that a
citation hangs off an `ASSISTANT` message rather than a `USER` one. Nothing
requires `citation_order` to run without gaps. And `storage_path` and `checksum`
are written by an upload path that does not exist. All of them are in
[todo.md](todo.md).

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
resource_type.py        ->  (nothing)
resource_access_scope.py -> (nothing)
chunk_type.py           ->  (nothing)
document_status.py      ->  (nothing)
message_role.py         ->  (nothing)
resource.py             ->  base, resource_type, resource_access_scope
chunk.py                ->  base, chunk_type, resource_access_scope, pgvector
document.py             ->  base, document_status
chat_session.py         ->  base
chat_session_message.py ->  base, message_role
citation.py             ->  base
```

No entity module imports another entity module at runtime — not across any of
the seven groups. They name each other only as strings in their relationships,
with the concrete types pulled in under `if TYPE_CHECKING:` for the annotations,
so no cycle can form. `Team` referring back to `Department` and `Department`
referring forward to `Team` costs nothing at import time, and neither does
`SourceCredentials` and `ExternalDataSource` naming each other from inside the
same group.

`resource.py` and `chunk.py` are the pair where that rule is doing real work.
`Resource.chunks` and `Chunk.resource` name each other, and both reach out to
`Team` and `Department` in a third group which reach back — a straight import
would be a cycle in two directions at once. `citation.py` is the same shape again
and worse: it names `ChatSessionMessage`, `Chunk` and `Resource`, three classes in
three groups, all of which name it back.

**Enum modules are the one exception, and they are why this works.** They import
nothing at all, so importing one across a group boundary cannot cycle — which is
what `chunk.py` does with `resource_access_scope`, the only cross-group runtime
import in the package. It is also why the enums live in their own modules rather
than beside the entity that first used them: `ResourceAccessScope` declared inside
`resource.py` would have forced `chunk.py` to import `resource.py` itself, and the
cycle would be real.

`chunk.py` is the one entity module with a third-party import,
`pgvector.sqlalchemy.Vector`. It is a type, not a client — nothing about it opens
a connection — but it does mean `pgvector` has to be installed for
`app.entities` to import at all.

The consequence: **importing a module is what makes its mapper real to
SQLAlchemy**, and the seven groups now depend on each other's mappers.
`Department.teams` needs `Team` to exist before `configure_mappers()` can resolve
it, `Team.external_data_sources` needs `ExternalDataSource`, `Resource.chunks`
needs `Chunk`, `Citation.chunk` needs it again from a different direction, and so
on across all seven. Import the top-level package:

```python
from app.entities import ChatSession, Chunk, Citation, Document, Resource, User
```

`app/entities/__init__.py` imports all seven groups, which registers every mapper
and fills `Base.metadata`.

Importing a single group happens to work too — `import app.entities.chat` runs
`app/entities/__init__.py` first, because Python imports a parent package before
its child, and that is the file which pulls in the other six. It is worth knowing
that this is *why* it works, and not to build on it: it is a side effect of where
the imports sit, and it would stop being true the moment `__init__.py` imported
groups lazily. Import the package that promises every mapper, not the one that
happens to reach them.

## Not implemented yet

**The entity layer is complete; everything that uses it is not.** These seven
groups are the whole V1 data model — an uploaded file or a connected system
becomes a resource, a resource becomes chunks, a chunk gets a vector, and a chat
answer cites the chunks it rested on. No further table is planned for V1.

What is absent is every line of code that would write one. Authentication
endpoints, JWT, password hashing, login; CRUD APIs; team services; source
connection endpoints; the ingestion orchestrator; credential encryption;
authorisation policies; and repositories. The service-layer work these
entities specifically wait on is in [todo.md](todo.md).

Migrations are the one item that has since been struck off that list — the tables
exist on a server now, and nothing writes to them. See
[migrations.md](migrations.md).

The knowledge and chunk groups in particular are columns and nothing else. There
is no embedding generation and no client for any embedding API; no vector index
and no similarity search; no authorisation function that reads `access_scope`; no
row-level security; no resource or chunk CRUD; and nothing that turns a
`CodeChunk` or a `JiraChunk` into a `Chunk` row. `resources` and `chunks` are the
shape those things will need, written down before they are built.

The document and chat groups are newer and emptier still. Nothing uploads a file,
stores its bytes, or moves a `DocumentStatus` past `UPLOADED`; there is no PDF or
DOCX parser and no document chunker. Nothing opens a chat session, calls a model,
retrieves a chunk, or writes a citation — retrieval, the RAG agent and the chat
API are all absent, and `citations` is the table the last of those will fill.

The ingestion pipelines do not use any of this. `CodeChunk`, `JiraChunk`,
`ConfluenceChunk` and `SlackChunk` remain pydantic models that no table stores —
see [architecture.md](architecture.md). `chunks` is the table they are eventually
headed for, and the two are not yet connected by a single line of code: a DTO
crosses a request boundary and is discarded, a `Chunk` is a row. Mapping one to
the other is the ingestion orchestrator's job, and it does not exist.

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

That prints the eleven `CREATE TYPE`s, the fourteen `CREATE TABLE` statements and
the thirty-one indexes, without connecting to anything. Note that
`resource_access_scope` is emitted once even though two tables use it — one enum
name is one PostgreSQL type. Swapping in `create_engine("sqlite://")` and a real
`create_all` is a working smoke test — on SQLite the native enum degrades to
`VARCHAR` with a `CHECK`, which is expected, and the constraints still reject a
second team for a user who already has one, a duplicate team name within a
department, a second chunk at index 0 of the same resource, a second resource for
one document, two citations at the same position in one answer, and a resource
with two origins or none.

That last one is worth checking on both dialects, since it is the only
`CheckConstraint` in the schema. `ck_resources_single_origin` is written as
`(external_data_source_id IS NULL) <> (document_id IS NULL)` rather than with
PostgreSQL's `num_nonnulls()`, so the same DDL compiles for SQLite and the rule is
enforced in a test as well as on a server.

That SQLite pass is also what the JSON columns are shaped for. `config`,
`credential_metadata`, `run_metadata`, `resource_metadata` and `chunk_metadata`
are declared as `JSON().with_variant(JSONB, "postgresql")` rather than as `JSONB`
outright: the PostgreSQL DDL above still reads `JSONB`, with everything `JSONB`
gives — the binary representation, the containment operators, the ability to index
inside a document — while the same metadata still builds on SQLite for a test. It
is the same trade `UUIDMixin` makes with `Uuid` over `postgresql.UUID`, one type
down.

`chunks.embedding` is the same mechanism pointed at a type from outside
SQLAlchemy: `JSON().with_variant(Vector(1536), "postgresql")` prints
`VECTOR(1536)` in the dump above and a JSON column on SQLite, so a test can insert
`[0.1, 0.2, 0.3]` and read it back without a PostgreSQL server or the `vector`
extension anywhere near it.
