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
- At run time each timer counts its wakeups by name. That count says *which* registered
  timer caused a regression; it is one of two separate inputs to the idle-wakeup gate (B2),
  see below.
- **Exception: thermal protection sampling** (R1, 2026-11-10; DECISION-r1-thermal-c477a-1110,
  approved by 9320/a63ab). aibd must stop its own work before the machine overheats (1.x
  `cooldown.py`, after the 11-03 mac-c incident), and the reap rule needs an average
  held ≥ 60 s over ≥ 3 samples, which a "sample only when a viewer asks" rule (§4) cannot
  provide. So one named timer, `thermal_sample`, samples CPU die temperature and thermal
  pressure without a viewer, under these conditions:
  1. It is an `Operation` timer registered through `aibd::timers` like any other, listed in
     `docs/perf/timers.md`, and its wakeups appear in the §4 counters by name. This is the
     exception: its maximum lifetime is not a fixed duration but the life of aibd's own
     process set (condition 2), which is the stop condition the timer declares.
  2. It exists **only while aibd has at least one process of its own running**; the round in
     which the last one ends drops it. With 0 own processes it does not exist, so the idle
     gate B2 still expects 0 registered wakeups.
  3. The period is **fixed at 5 s**, the same as its lower and upper bound; it is not
     configurable. A thermal-pressure event may trigger one early decision, never a shorter
     period.
  4. On Windows there is no thermal protection (the sampling calls are `Unsupported`); the
     status line and the background-run view say so, and the 1.x retirement table records it.

  The sampling calls themselves live in `aib-sys` (T1 CPU die temperature, T2 thermal
  pressure); the decision rules are pure functions in `aib-thermal`. This exception is the
  only machine-state sampling without a viewer; §4 stays as written for everything else.

### 1a. What the idle-wakeup gate (B2) can and cannot see

B2 reports two columns that are never merged into one number:

| Column | Source | Covers | Does not cover |
|---|---|---|---|
| Registered timers | `aibd::timers` counts, by name | every wakeup of a registered timer | anything else: kernel, runtime internals, I/O readiness, other threads |
| OS counters | macOS `proc_pid_rusage`: `ri_interrupt_wkups`, `ri_pkg_idle_wkups`; Windows: context-switch count | the wakeup classes those fields count | they are **not** all OS wakeups; each field counts specific classes only. The Windows count is a **proxy, pending measurement** |

- If the OS fields cannot be read (tool missing, permission, unsupported OS version), the B2
  result is **invalid**, never a silent pass (spec v0.2 judging order: invalid before red).
- The OS column is the cross-check that catches wakeups outside the registry; it is a
  partial view, not a total.

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
- Streaming fragments follow ADR 0003 (update 2026-11-07): merged into one push every 50 ms.
  The 250 ms aggregate cap above does not apply to them and does not replace that rule.
- One transaction may carry several events; there is one writer.

### 4. Internal counters and self-monitoring (spec Q5)

- Counters are atomics, incremented in place. No thread, no timer, no sampling loop.
- `GET /api/v1/metrics` (Bearer token, same guard as every other route) computes the
  snapshot on request. Nothing is pushed unless a threshold is crossed.
- Thresholds are checked at the moment the value changes (for example on enqueue), are
  edge-triggered (one event when crossing, one when clearing) and never polled.
- Machine state (CPU, temperature, memory, process table) is sampled **only when a visible
  client asks**, cached 5 s and shared by all clients; with no viewer it is not sampled
  at all. The one exception is thermal protection sampling while aibd has its own
  processes running (§1, `thermal_sample`).
- aibd counts GET requests per route template and flags a client that polls (high-rate
  GETs on a route that has an SSE equivalent) in the metrics output. Clients must use
  SSE; periodic GET is a bug.

### 5. AI processes (spec P6)

- A Seat without a Session costs nothing. What costs energy and memory is a live CLI.
- Concurrent active sessions are capped at `floor(physical_memory × 0.5 ÷ 500 MB)`, at
  least 2 and **at most 4** for now; extra requests wait in a queue and are never
  refused. 8 is the stress-test target, not the default, until the baseline proves it
  safe.
- The queue is bounded (11-09, a9136 / a63ab): at most 64 sessions wait (trial value);
  the next request is `429 session_queue_full`, the only refusal for load. Waiting
  sessions are `pending` (no process), start in arrival order (FIFO) as slots come back,
  and can be cancelled. A pending session is never started from an old queue: at
  shutdown it ends `interrupted`, after a crash the next start marks it `interrupted`
  (`previous_state: pending`). `AIBD_MAX_SESSIONS` (1–64) is for stress tests only.
- A session leaving the queue goes through the M1 gate again on the `argv[0]` it was asked
  with (ADR 0003: every spawn resolves the real path again); refused ⇒ `failed`
  (`ended_by: policy_refused`), audited like a POST refusal (043dd 11-10 M1).
- Known limit (043dd 11-10 M2 / L3): a slot kept by a stop that could not confirm the tree
  is gone (ADR 0004: on a desktop Mac a longer session may end `candidates_unreadable`) is
  not given back by time. While every slot is held that way the queue does not move: there
  is no wait limit yet. A pending session shows why: `GET` returns `queue` with its
  `position`, the limit, and every session holding a slot with its state and stop reason.
  DELETE the holder again to re-check it, or restart aibd.
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

- Positive: the state of the **registered** timers is provable when idle (zero `Operation`
  timers active, only the listed `Resident` ones ticking), and a regression in them names its
  timer. This does not prove the total idle cost: wakeups outside the registry are only
  visible through the OS column of B2, which is itself partial (section 1a).
- Positive: write volume drops by one to two orders of magnitude compared with persisting
  every fragment.
- Negative: catch-up after a disconnect returns the completed message plus the latest
  snapshot, not the token-by-token history.
- Negative: the timer module and the clippy rule are one more thing every contributor has
  to go through; the error message must say how.
- Negative: the event paths need OS calls that safe wrappers do not cover (Windows Job
  Object messages; macOS `proc_pidinfo` flavor 17). Decided (DECISION-1, 2026-11-07): all
  such FFI lives in one crate, `aib-sys`. Only aibd's platform module may depend on it
  (amended by DECISION-10 B, 2026-11-08: also `aib-procwatch` for its child listing, and
  `aib-forkcount` only through aib-procwatch, which keeps aib-sys types private);
  `aib-core` and `aib-store` must not (checked on the `cargo metadata` dependency graph,
  with knives). Every other crate stays `forbid(unsafe_code)`; `aib-sys` uses
  `deny(unsafe_op_in_unsafe_fn)`, a SAFETY note on every unsafe block, Rust-only types at
  its boundary, a fake implementation for other crates' tests, and positive plus
  error-path tests with knives for every call. Its PRs carry nothing else and get a fixed
  second reviewer. Whether each platform API is available and behaves as expected is
  still open until measured on real machines.
