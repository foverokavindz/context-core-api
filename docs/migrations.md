# Migrations

[← Documentation](README.md)

The first code in this project that opens a connection. Everything before it —
`app/entities/` and the DDL dumped into [schema.sql](schema.sql) — described a
schema without ever reaching a server. This is where the description meets one.

Three files carry the whole of it:

```
alembic.ini                    what Alembic is configured with
alembic/env.py                 what it compares against, and what it connects to
alembic/versions/*.py          the migrations themselves
app/core/database.py           the engine and session the application uses
```

`app/core/database.py` is not part of Alembic, but it is listed here because it
owns `DATABASE_URL` and `env.py` imports it to reach that one function. There is
exactly one reader of that variable in the project, which is the point: the
migrations and the running API cannot end up pointed at different servers.

## Configuration

One variable:

```
DATABASE_URL=postgresql+psycopg://user:password@host:5432/contextcore
```

It goes in `.env`, which is gitignored, and `load_dotenv()` in
`app/core/database.py` reads it — the same mechanism `embedding_service.py` uses
for its three Azure variables, called at import so a developer running `uvicorn`
from a shell with no exports still gets a configured server.

**`sqlalchemy.url` in `alembic.ini` is deliberately empty.** That file is
committed and a connection string carries a password. Leaving a value there
would either put the secret in git or leave a stale one that silently overrides
the environment — and "silently overrides" is the failure that ends with a
migration applied to the wrong database.

**The URL is normalized onto psycopg 3.** `normalize_database_url()` rewrites
`postgres://`, `postgresql://` and `postgresql+psycopg2://` — the three forms
hosting providers hand out — to `postgresql+psycopg`. A bare `postgresql://`
would work, because SQLAlchemy resolves it to whichever DBAPI it can import, but
relying on that means the driver in use depends on what happens to be installed,
which differs between a laptop and a container. A URL naming some other backend
is returned untouched rather than rewritten: rewriting it would hide the mistake
instead of reporting it.

**A missing `DATABASE_URL` raises** rather than defaulting to `localhost`. A
default is the kind of convenience that ends with a migration running against
the wrong server, and a service that starts and only fails on its first query is
harder to diagnose than one that refuses to start. The exception is
`DatabaseConfigurationError`, which names the variable and never its value — a
connection URL carries a password, and the rule in
[security.md](security.md) applies to it exactly as it applies to a token.

## Running them

Every command runs from the repository root, which is where `alembic.ini` sits
and what `prepend_sys_path = .` makes importable.

```bash
alembic upgrade head        # apply everything outstanding
alembic current             # what this database is at
alembic history --verbose   # the chain, newest first
alembic downgrade -1        # step back one
```

`alembic upgrade head --sql` prints the SQL instead of running it. That is worth
doing before any migration meets data.

## Writing one

```bash
alembic revision --autogenerate -m "add resource embedding index"
```

**What comes out is a draft, not a migration.** Autogenerate compares
`Base.metadata` against the live database and writes what it can see, and the
things it cannot see are precisely the ones this schema depends on:

- **`CREATE EXTENSION`.** Not part of any table's metadata. The `vector`
  extension is created by hand in the first migration, and any future extension
  is created by hand too.
- **Enum value changes.** Adding a member to `ResourceType` produces nothing.
  PostgreSQL needs `ALTER TYPE resource_type ADD VALUE 'X'`, written out — and
  that statement cannot run inside a transaction block on older servers, so it may
  need its own migration. This is the friction that made `chunks.chunk_type` a
  plain string; see [entities.md](entities.md#chunks).
- **Vector indexes.** An `ivfflat` or `hnsw` index on `chunks.embedding` is not
  in the entities, and deliberately so — see [todo.md](todo.md).
- **Anything involving data.** A column that is being split, backfilled or
  renamed. Autogenerate sees a drop and an add, which is how a rename silently
  becomes data loss.
- **Table renames**, for the same reason.

Read the generated file before committing it. Read the `downgrade()` especially:
autogenerate writes one, it is rarely checked, and a wrong one is only
discovered at the worst possible moment.

### What `env.py` gets right

**`import app.entities` is the load-bearing line.** The seven entity groups point
at each other's mappers, so importing one leaves the others out of
`Base.metadata` — and autogenerate would then cheerfully write a migration
dropping thirteen tables it could not see. The import looks unused. It is not.

**`include_object` skips `alembic_version`.** Alembic's own bookkeeping table is
not in `Base.metadata`, so every autogenerate run would otherwise propose
dropping it.

**`compare_type` and `compare_server_default` are both on.** Off by default
because they are imperfect, and a false positive in a *proposed* migration costs
a moment's reading, while a missed `VARCHAR(255)` → `VARCHAR(512)` costs a
truncated title in production. Every timestamp in the schema carries
`server_default=func.now()`, and a change to one should show up.

**`render_as_batch` is absent.** It exists for SQLite's inability to `ALTER` a
column, and this project migrates PostgreSQL. The entities are dialect-agnostic
so they can be *created* on SQLite in a test — see the end of
[entities.md](entities.md) — but nothing migrates one.

**Migrations run inside a transaction.** PostgreSQL has transactional DDL, so a
migration that fails halfway leaves the database exactly as it was, including
`alembic_version` — that row is updated in the same transaction as the schema
change it records, which is what makes "applied" and "recorded" impossible to
disagree.

## The naming convention is what makes all this work

`app/entities/base.py` sets a `MetaData(naming_convention=...)`, and it is worth
being explicit about why that matters here rather than there.

A constraint created without an explicit name gets one from the database.
PostgreSQL's choice and SQLAlchemy's differ, and neither is guaranteed stable
across versions. A migration that needs to *drop* such a constraint has to name
it — and it can only name what it can predict. With the convention in place,
`fk_chunks_team_id_teams` is derivable from the model without asking any
particular server what it happened to call the thing. Without it, an autogenerated
`op.drop_constraint()` is a guess, and one that fails on a database created by a
different version than the one the migration was written against.

## `create_all` is not used

Nothing in the application calls `Base.metadata.create_all()`. The tables on a
server are the ones `alembic/versions` created, full stop.

Having both would mean two sources of truth that agree exactly until the first
column is added, and then quietly stop. The failure mode is specific and nasty: a
developer whose database was built by `create_all` has a schema that no migration
ever produced, so their `alembic upgrade` starts from a state the migration chain
does not describe, and the first `alembic revision --autogenerate` they run
compares against it and proposes a diff that is wrong everywhere else.

[schema.sql](schema.sql) is the one exception, and it is not an exception at all:
it is a *dump* for reading, regenerated from the entities, and nothing applies it
to a server.

## The first migration

`CREATE EXTENSION IF NOT EXISTS vector` before anything else, because
`chunks.embedding` compiles to `vector(1536)` and the type has to exist before
the table that uses it. Then the eleven enum types, the fourteen tables in
dependency order, and the thirty-one indexes.

Its `downgrade()` drops all of that and leaves the extension alone. Dropping an
extension is not the inverse of creating one: `CREATE EXTENSION IF NOT EXISTS`
succeeds against a database where somebody else already installed `vector`, and
a `DROP EXTENSION` in the downgrade would then remove something this migration
did not add — from under whatever else in that database is using it.

The `vector` extension needs a superuser, or `rds_superuser` on RDS. That is
usually the only statement in the migration that does, so a database where the
application role cannot create extensions needs the extension installed once by
an administrator; `IF NOT EXISTS` then makes the migration's own attempt a no-op
rather than an error.

**What the first migration does not create is the vector index.** A similarity
search against `chunks.embedding` is a sequential scan until an `ivfflat` or
`hnsw` index exists, and choosing between those — and their parameters — has to
be done against a real corpus, which does not exist yet. The dimension is pinned;
the index is not. It gets its own migration when there is data to tune it
against.

## `a1c4e7f92b60` — `chunk_type` as a string

The second migration, and the first one that changes something the first one made.
`chunks.chunk_type` stops being the native `chunk_type` enum and becomes
`VARCHAR(255)`; the now-orphaned type is dropped; `chunks.content_hash` goes with
it. The reasoning for both is in [entities.md](entities.md#chunks) — the short
version is that the symbol kinds a parser finds and the issue types a Jira project
defines are open sets, and `content_hash` was a column nothing ever wrote.

That leaves the first migration creating a type the second one destroys, which is
deliberate: `d0371f6b5f55` has been applied, and rewriting an applied migration is
how two databases end up with schemas no revision describes.

Written by hand, for the reason listed above — a type change with data behind it is
one of the things autogenerate turns into a drop and an add. The conversion is
`postgresql_using='chunk_type::text'`, which no existing row can fail, and
`ix_chunks_chunk_type` survives untouched because PostgreSQL rebuilds an index
across `ALTER COLUMN ... TYPE` on its own.

**The `downgrade()` can fail, and should.** It casts back to the ten-member enum,
so any row holding an `INTERFACE`, `TYPE_ALIAS` or `BUG` — exactly the values the
upgrade makes possible — aborts it. Refusing is better than discarding, and
whoever wants to go back has to decide what those rows become first.

## Not done yet

**No test runs a migration.** The suite creates the entities on SQLite, which
proves the mappers agree with each other and nothing about the SQL in
`alembic/versions`. A test that runs `upgrade head` then `downgrade base`
against a throwaway PostgreSQL database is the one that would catch a broken
`downgrade()`, and it needs a server in CI.

**Nothing checks that the entities and the head revision agree.** The check is
mechanical — run `alembic check`, which fails when `Base.metadata` has drifted
from what the migrations produce — and it belongs in CI, where it catches the
commit that edits an entity and forgets the migration.

**`docs/schema.sql` is regenerated by hand.** It is derived from the entities and
so is the migration, which means the two can disagree without anything
complaining. Whoever automates the dump gets to delete this paragraph.
