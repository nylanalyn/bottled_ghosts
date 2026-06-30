# ADR-005: SQLite connection ownership

## Decision

Each running Bottle owns one SQLite connection. The command connection is used
to load configuration, while `run-all` opens and closes a separate connection
for every Bottle task.

Every connection enables foreign keys, WAL journaling, and a five-second busy
timeout. Identity resolution runs its read/create/update sequence inside
`BEGIN IMMEDIATE`, so concurrent Bottles cannot create separate UUIDs for the
same observed IRC identity.

## Alternatives considered

- Share one connection between all Bottle coroutines. This permits transaction
  statements from unrelated coroutines to interleave on the same connection.
- Protect a shared connection with a global asyncio lock. This introduces hidden
  process-global state and couples unrelated storage operations.
- Add unique account and hostmask constraints. The identity table intentionally
  records multiple observations across nick and hostmask changes, so those
  constraints would discard useful identity history.
- Open a new connection for every operation. This makes transaction boundaries
  explicit but adds unnecessary connection churn.

## Reason chosen

Connection ownership matches the existing Bottle task boundary and lets SQLite
coordinate concurrent writers through visible transactions. It avoids hidden
locks while preserving complete identity evidence.

## Tradeoffs

WAL creates normal `-wal` and `-shm` sidecar files while the database is active.
Concurrent writes may wait up to five seconds before raising a lock error. Schema
migrations must complete on the command connection before Bottle tasks start.
