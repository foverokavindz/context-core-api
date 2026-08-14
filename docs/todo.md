# TODO

[← Documentation](README.md)

Work the schema deliberately leaves undone.

Every item here is an invariant that `app/entities/` does not enforce, and each
one is a decision rather than an oversight — a rule the database could only
express badly, or one that needs a service and a transaction to be enforced at
all. [entities.md](entities.md) explains the reasoning where the tables are
described; this page is the list, so that adding the services later is a matter
of working through it rather than rediscovering it.

Nothing here is a bug. A row that trips one of these is a valid row today.

## Source connection

**Create the source, then the credential, then link them.** `ExternalDataSource`
is written first, the `SourceCredentials` row is created against it, and its id
is written back to `external_data_sources.credential_id`. That is why the column
is nullable — the intermediate state is real, and a `NOT NULL` column would mean
inventing a placeholder credential row to satisfy it.

A future `DataSourceService.connect_source()` owns the whole sequence in one
transaction, and owns what happens when the second half fails: either the
half-finished source is rolled back, or it is left for the user to finish, but
not left silently.

**Refusing to sync a source with no credential** belongs to the same service.
`credential_id IS NULL` means setup never finished, and the ingestion path has to
say so rather than calling a connector with no token.

**A credential and the source using it should belong to the same team.** Nothing
enforces this — the two foreign keys are independent, so a source in one team can
reference another team's credential. A composite foreign key could express it, at
the cost of carrying `team_id` in both keys; for now it is a check when a source
is created or its credential is changed.

## Sync runs

**`last_synced_at` propagation.** When a `SyncRun` reaches `COMPLETED`, set
`ExternalDataSource.last_synced_at = completed_at` in the same transaction. The
entity deliberately has no `onupdate` and no event listener for this — see
[entities.md](entities.md#external_data_sources).

**Status transitions.** `PENDING → RUNNING → COMPLETED | FAILED` is the intended
path, and the schema enforces none of it. Nothing stops a run going straight from
`PENDING` to `COMPLETED`, or a `COMPLETED` run being reopened.

**Nothing enforces one run at a time per source.** Two concurrent `RUNNING` rows
against the same source are accepted by the database. Whether that is prevented
by the scheduler, by a partial unique index, or by an advisory lock is open.

**`started_at` / `completed_at` are not checked against each other**, and neither
is checked against `status`. A `RUNNING` run with a `completed_at` is a valid row.

## Credentials

**Encryption.** `encrypted_secret` holds ciphertext and nothing in this version
produces any. A credential service owns encrypting on write and decrypting on
use, and owns never letting either reach a log or a response — see
[security.md](security.md).

**Secret manager.** `secret_reference` is the alternative: a Vault path, an AWS
Secrets Manager ARN, whatever the deployment uses. Which of the two a deployment
takes is not decided yet, which is why both columns exist and neither is
required.

**Nothing requires one of the two to be set.** A credential row with neither is
accepted. Once the strategy is chosen, this becomes either a `CHECK` or a service
check.

**Rotation and revocation.** Replacing a credential in place versus writing a new
row and repointing its sources is an open question. Revocation is *not* a delete:
the foreign key refuses it while sources still reference the row, and the intended
answer is `status = INACTIVE` on those sources.

**One Atlassian token, two credential rows.** `CredentialType` has separate `JIRA`
and `CONFLUENCE` members, but a single Atlassian API token authenticates both — so
a team using both holds two rows carrying the same secret. Rotating that token has
to update both, and the service that owns rotation has to know the two are linked;
nothing in the schema says so. See
[entities.md](entities.md#credentialtype) for why the enum is shaped this way.

**A source's `credential_type` should match its `source_type`.** Nothing enforces
the pairing — a `JIRA` source can reference a `SLACK` credential, and the failure
would surface as a confusing authentication error at ingestion time rather than at
insert. A check when a credential is attached to a source is the cheap answer.

**`credential_metadata` shape.** Free JSON, unvalidated, and non-secret by
convention only. The convention needs to survive contact with the credential
service.

## Source configuration

**`config` is unvalidated JSON.** Nothing checks it against `source_type`, so a
`GITHUB` source may carry a Slack `channel_id`. Each connector knows the shape it
needs — a pydantic model per source type, validated at the service boundary
before the row is written, is the natural place for this.

**`SourceStatus` transitions.** Who moves a source to `ERROR`, and what clears
it. A single failed run should not do it — a network blip is not a broken
connection — so the rule is a threshold, or an authentication failure
specifically, and it has to be written down before it is written in code.

## Knowledge sources

**A resource's `access_scope` is not checked against `team_id` and
`department_id`.** `TEAM` with a `NULL` `team_id`, or `ORGANIZATION` with a team
set, are valid rows today. A `CHECK` constraint could express the pairing, and the
reason there is not one is that the rule belongs beside the authorisation code
that reads these columns — writing the constraint first would mean guessing what
that code needs. Whoever writes the first permission check writes this too.

**Chunk permission columns can drift from their resource's.**
`chunks.access_scope`, `chunks.team_id` and `chunks.department_id` are
denormalized copies and nothing keeps them in step — not an ORM event, not a
trigger. The ingestion service creates chunks from the resource's permission
context, in one place; a resource whose scope is later *changed* also has to
rewrite its chunks, and that is the case most likely to be forgotten. See
[entities.md](entities.md#why-the-permission-columns-are-here-twice).

**`document_id` points at nothing.** It is a bare `UUID` column with no
`REFERENCES`, because the `documents` table does not exist. Adding the `Document`
entity means adding the foreign key in the same migration — and until then a
resource may carry a `document_id` for a document that was never written.

**Nothing requires a resource to have exactly one origin.** A row with both
`external_data_source_id` and `document_id` set, or with neither, is accepted. The
intended rule is one or the other, which is a `CHECK` once `documents` exists and
a service check before then.

**`content_hash` and `embedding` are written by nobody.** The entity does no
hashing and calls no embedding API. The ingestion service hashes `content`,
compares against the stored `content_hash`, and only then spends an embedding
call — the whole reason the column is there. `embedding_model` has to be written
in the same statement as `embedding`, or the record of which model produced a
vector is lost.

**`chunk_index` is not checked for gaps.** The unique constraint stops a resource
holding index 0 twice; nothing requires its chunks to run 0, 1, 2 with nothing
missing. A partial re-ingestion that writes some chunks and fails can leave a
hole, and reassembling a resource in order has to tolerate that or the service
has to prevent it.

**Re-ingestion strategy is undecided.** `version_key` and `external_id` exist so a
second run can recognise an item it already stored, and nothing yet decides
whether an unchanged `version_key` skips the resource entirely, or whether a
changed one replaces its chunks, renumbers them, or writes a new resource
version. `SyncRun.chunks_updated` and `chunks_deleted` are counters waiting on
that decision.

**The `vector` extension and its index.** `chunks.embedding` compiles to
`vector(1536)` but a real PostgreSQL server needs `CREATE EXTENSION vector` before
that table can be created, and an `ivfflat` or `hnsw` index before a similarity
search is anything but a sequential scan. Both belong to the first migration and
the first real corpus respectively — the dimension is pinned, the index is not,
because it has to be tuned against data that does not exist yet.

## Organization

Carried over from the first two entity groups, and unchanged:

**A user's `job_title_id` should belong to their `department_id`.** No trigger,
no composite foreign key; a check when organizational information is assigned.

**Team leads are not constrained.** A team may have no `TEAM_LEAD` or several.
Whether "one lead per team" holds is a product question, and a unique index would
answer it badly.

**A team's creator is not its member.** `teams.created_by_user_id` records
authorship only. If creating a team should also insert a `TeamMember` with
`TEAM_LEAD`, `TeamService.create_team()` writes both rows explicitly.

## Not on this page

Entities that do not exist yet, and the endpoints, authentication and migrations
that go with them, are listed at the end of [entities.md](entities.md). This page
is only about rules the current tables leave to a service.
