# ADR 0005 — Performance budget inside aibd: event-first, registered timers, lean runtime

- Status: proposed (input: AIB 2.0 performance budget spec v0.2, 9320/a63ab, 2026-11-07,
  merging opinions from @3f312, @bb7dc and @a9136)
- Deciders: @a9136 (proposal), 9320/a63ab (approval)
- Scope: aibd only (spec P1–P3, P5–P6, internal counters, timer rules, Windows
  equivalents). Web / lobby rules (P4) and the measurement gates (B1–B10) are owned by
  other seats; this ADR only fixes what aibd must expose so they can be measured.

## Context

AIB 1.x spends most of its idle energy on polling: status lines that start a shell and an
interpreter on every refresh, periodic tmux captures, per-seat loops. AIB 2.0 runs up to
several AI CLIs at once on a laptop, so aibd itself must be close to free when nothing
happens, and must not multiply its cost by the number of seats when things do happen.

"Zero polling" is not achievable: cancel escalation needs a deadline, process-tree
tracking needs a backstop, and file-change notification can overflow and must be
followed by a rescan. The rule is therefore: **event-driven first; every periodic wakeup
is registered, budgeted and stops by itself; a lost event is detected and repaired.**

## Decision

### 1. Two kinds of timers, one entry point

| Kind | When it may run | Rule |
|---|---|---|
| `Resident` | Always, including when idle | Interval **≥ 30 s**; listed in `docs/perf/timers.md` with a reason |
| `Operation` | Only while an operation or a BUSY session exists (cancel escalation wait, waiting for a CLI's first event, process-tree backstop scan, SSE keep-alive if ever needed) | Must declare a maximum lifetime and stop by itself; **when aibd is idle (0 sessions, 0 operations) none may exist** |

- Every periodic or delayed wakeup goes through `aibd::timers` (`every(name, interval,
  kind)` / `after(name, delay, kind)`), which returns a handle that stops the timer on
  drop.
- `clippy.toml` `disallowed_methods` forbids `tokio::time::{interval, interval_at, sleep,
  sleep_until}` and `std::thread::sleep` outside that module, so a new timer cannot be
  added without passing through it. This is enforced by the compiler, not by grep.
- The module knows every timer name at build time; CI compares that list with
  `docs/perf/timers.md` (missing or extra → red).
- At run time each timer counts its wakeups by name. The counts feed the idle-wakeup gate
  (B2) and say *which* timer caused a regression.

### 2. Runtime

- Tokio multi-thread runtime with `worker_threads = 2` (aibd mostly waits on I/O) and a
  bounded blocking pool. CPU-heavy work, if it ever appears, gets its own dedicated
  thread instead of a larger pool.
- SQLite keeps one writer thread (ADR 0002).
- Background maintenance (catch-up, cleanup, migrations) runs at the OS low-energy
  scheduling class: macOS QoS `utility`, Windows EcoQoS. Lowering the QoS of a
  non-foreground seat's CLI is an **experiment**, off by default, until measured to not
  slow the CLI down.

### 3. What is written, and when (spec P3, P5)

- Only state changes and terminal / approval / error / cancel events are persisted.
  Streaming text fragments (`message.delta`) are pushed live and **never written to
  `events`** (ADR 0003 update).
- For an in-progress message, a snapshot of the partial text is stored **at most once per
  second**. With 8 streaming seats that is about 8 writes/s; it is counted in the
  write-amplification gate (B9).
- Coalescing per aggregate: trailing 50 ms, at most 250 ms (starting values, tunable after
  B4). Terminal, approval, error and cancel events are never coalesced and go out at once.
- One transaction may carry several events; there is one writer.

### 4. Internal counters and self-monitoring (spec Q5)

- Counters are atomics, incremented in place. No thread, no timer, no sampling loop.
- `GET /api/v1/metrics` (Bearer token, same guard as every other route) computes the
  snapshot on request. Nothing is pushed unless a threshold is crossed.
- Thresholds are checked at the moment the value changes (for example on enqueue), are
  edge-triggered (one event when crossing, one when clearing) and never polled.
- Machine state (CPU, temperature, memory, process table) is sampled **only when a visible
  client asks**, cached 5 s and shared by all clients; with no viewer it is not sampled
  at all.
- aibd counts GET requests per route template and flags a client that polls (high-rate
  GETs on a route that has an SSE equivalent) in the metrics output. Clients must use
  SSE; periodic GET is a bug.

### 5. AI processes (spec P6)

- A Seat without a Session costs nothing. What costs energy and memory is a live CLI.
- Concurrent active sessions are capped at `floor(physical_memory × 0.5 ÷ 500 MB)`, at
  least 2 and **at most 4** for now; extra requests wait in a queue and are never
  refused. 8 is the stress-test target, not the default, until the baseline proves it
  safe.
- "End an interactive CLI after 15 idle minutes and mark the session resumable" is
  implemented but **off by default** until resume reliability and the energy saving are
  measured. A session that is running a tool or waiting for an approval is never idle.
- Process-tree tracking is event-driven (ADR 0004 update); the periodic scan is an
  `Operation` backstop every 10 s while a session has a live tree.

### 6. Network

License and update checks: once at start and once a day. No other periodic network
traffic from aibd.

### 7. Windows equivalents

| Concern | macOS | Windows |
|---|---|---|
| New / exited descendant | kqueue `EVFILT_PROC` `NOTE_FORK` / `NOTE_EXIT` | Job Object + I/O completion port (`JOB_OBJECT_MSG_NEW_PROCESS`, `JOB_OBJECT_MSG_EXIT_PROCESS`, `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO`) |
| Count of our own descendants (B1) | count of `NOTE_FORK` events under aibd | `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION.TotalProcesses` |
| Low-energy scheduling | QoS `utility` | EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`) |
| File change | FSEvents | `ReadDirectoryChangesW`; a buffer overflow must trigger one full rescan |
| Starting a process | cheap | expensive (security scanning, process creation cost); "one resident process, no helper processes per refresh" matters more |

Verification levels: macOS rows are VERIFIED-mac only where a test exists; every Windows
row is CI-windows at best for the API call, UNVERIFIED-desktop for real CLI behaviour.
The descendant counts on both OSes are **proxies until measured** (see ADR 0004 update).

## Consequences

- Positive: idle cost is provable (B2 sees zero `Operation` timers and only the listed
  `Resident` ones); a regression names its timer.
- Positive: write volume drops by one to two orders of magnitude compared with persisting
  every fragment.
- Negative: catch-up after a disconnect returns the completed message plus the latest
  snapshot, not the token-by-token history.
- Negative: the timer module and the clippy rule are one more thing every contributor has
  to go through; the error message must say how.
- Negative: the Windows event path needs Win32 calls; either a vetted safe wrapper crate
  or a small `unsafe` module confined to the Platform Adapter (to be decided in Phase 1;
  `aib-core` stays `forbid(unsafe_code)` either way).
