# ADR 0002 — SQLite through rusqlite (bundled), one writer thread

- Status: accepted for Phase 0–1 (a63ab, 2026-11-07); revisit if the Phase 1 measurements below fail
- Deciders: @a9136 (proposal), 9320/a63ab (approval)

## Context

Spec section 2 leaves the driver open: SQLx or rusqlite, choose after a PoC. Needs:
WAL, versioned migrations, a transaction that writes business state and events
together (outbox), works the same on macOS and Windows, builds in CI without extra
setup.

## Options

| | rusqlite (`bundled`) | SQLx (sqlite) |
|---|---|---|
| SQLite version | compiled in, same on both OSes | compiled in (`libsqlite3-sys` too) |
| Async | none; we run it on a dedicated thread | async API, internally a worker thread per connection |
| Compile-time SQL checks | no | yes, but needs `DATABASE_URL` or committed `.sqlx` offline data in CI on both OSes |
| Migrations | `rusqlite_migration` or our own runner over `PRAGMA user_version` | built in (`sqlx::migrate!`) |
| Transaction control | explicit, synchronous, easy to reason about with one writer | async transactions; easy to hold one across an `.await` by mistake |
| Dependencies | small | larger (pulls its own runtime glue) |

## Decision

Use **rusqlite with `bundled`**, and our own small migration runner:

- Migrations are plain `.sql` files in `/migrations`, embedded at compile time, applied
  in order inside one transaction each, tracked in `schema_migrations(version, name,
  applied_at, checksum)`. A checksum mismatch on an applied migration stops startup.
- **One writer thread** owns the only write connection; requests send it closures over a
  bounded channel and await the reply. This serialises writes the way SQLite already
  does, without `SQLITE_BUSY` retries in handlers.
- Reads use a small pool of read-only connections (WAL lets them run beside the writer).
- Pragmas on open: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`,
  `busy_timeout=5000`.

Why not SQLx now: its main extra value (compile-time checked queries) costs a DATABASE_URL
or offline-data step in both CI jobs, and its SQLite backend is a worker thread anyway.
We cover query correctness with migration tests and repository tests that run against a
real temporary database.

## Phase 1 check (kept as a test, not a promise)

`aib-store` has an ignored-by-default benchmark test: insert 10 000 events in batches of
100 through the writer thread, then read them back by cursor. Record the numbers on the
Mac and on the Windows CI runner in the Phase 1 report.

Phase 0 first reading (VERIFIED-mac, release build, 2026-11-07, Apple Silicon dev Mac
under normal seat load): 10 000 events in 100 transactions 98 ms; cursor read of all
10 000 in pages of 500: 7 ms. One sample, not a baseline. If writes are the bottleneck for
8 concurrent seats, revisit this ADR.

## Consequences

- Positive: same SQLite build everywhere; no CI database setup; transaction scope is
  visible in code.
- Negative: no compile-time SQL checking; queries are strings. Mitigated by keeping SQL
  inside `aib-store` only and testing every repository function.
