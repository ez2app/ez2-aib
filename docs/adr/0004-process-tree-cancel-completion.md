# ADR 0004 — Process trees, cancellation and completion

- Status: proposed for Phase 1 (input: provider matrix by @3f312, 2026-11-07, relayed by 9320/a63ab)
- Deciders: @a9136 (proposal), 9320/a63ab (approval)

## Context (measured, macOS, one valid run each)

- A child started by the CLI's command tool with `start_new_session=True` gets its own
  process group. After `SIGTERM` to the CLI's group:
  - **Claude Code:** that child was gone.
  - **Codex:** that child **was still running**; it only stopped when the harness sent
    `SIGTERM` to its PID directly (it then showed as `Z <defunct>` until reaped).
  So "the process group is gone" does not prove the session's processes are gone.
- **Codex exited 0 after `SIGTERM`** without a `turn.completed` event (1 MB of
  `command_execution` output, no final answer). Exit code alone cannot tell "finished"
  from "cut off".
- Claude Code: `SIGINT` exit 0, `SIGTERM` exit 143; `result/success` event at the end of a
  normal run.

## Decision

### 1. A session owns a process tree, not a process group

Each session gets a `TreeHandle` from the Platform Adapter (`spawn_tree`). Cancelling
or cleaning up a session walks the whole tree it knows about.

**macOS / Unix** — no Job Object, no subreaper on macOS, and `NOTE_TRACK` is not
available, so we use three layers:

1. **Process group:** the CLI starts as the leader of a new group. Cancel signals the
   group first.
2. **Recorded descendants:** record every descendant as `(pid, start_time)`, never pid
   alone, so a reused PID is never signalled. Record them right after spawn and on a
   periodic scan by parent pid while the session runs (every 1 s while BUSY, 5 s while
   IDLE; tune after the Phase 1 baseline). *Superseded (2026-11-07 performance update
   below): event-driven watcher, scan only as a 10 s backstop.* A child that calls `setsid` keeps its parent
   until that parent exits, so the scan sees it as long as it lives at least one interval.
3. **Session marker:** start the CLI with `AIBD_SESSION=<session_id>` in its environment.
   Descendants inherit it through `setsid` and re-parenting. At cancel time and at aibd
   start, a sweep over same-user processes (`sysctl KERN_PROCARGS2`, Platform Adapter
   only) finds processes that carry the marker but were missed by the scan, for example
   a double fork faster than the scan interval. A process that clears its environment
   escapes this layer; we record that limit in the event instead of claiming a clean
   tree.

**Windows:** create the CLI suspended, assign it to a Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and **without** `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, then
resume it. Children cannot leave the job, so `TerminateJobObject` stops the whole tree.
Status: CI-windows for the API calls; real CLI behaviour is UNVERIFIED-desktop.
*Superseded in part (2026-11-07 performance update below): the guarantee covers only
processes confirmed to be in the job and created without breakaway.*

### 2. Cancel sequence (both OSes, same events)

1. Graceful: macOS `SIGINT` to the group (both CLIs exit 0 on it); Windows
   `CTRL_BREAK_EVENT` to the console process group (UNVERIFIED-desktop). Wait 3 s.
2. `SIGTERM` to the group **and** to every recorded or marked descendant that is still
   alive. Wait 3 s. (Windows: go straight to step 3.)
3. `SIGKILL` to every survivor, or `TerminateJobObject` on Windows.
4. **Verify:** each recorded `(pid, start_time)` is gone or is a zombie. aibd reaps its
   own direct child (`waitpid`); orphans are re-parented to launchd, which reaps them.
   Zombies count as stopped.
5. Emit `session.cancelled` with `{signals_sent, survivors: [...], escaped_marker_check}`.
   If any process is still running after step 3, the session ends as `INTERRUPTED` with
   reason `tree_not_clean` and the seat goes to `ERROR` → `RECOVERING`. It never shows
   as cleanly cancelled.

Cancel is idempotent: a second cancel on a session that is already terminal returns the
earlier result.

### 3. Completion comes from events, not from the exit code

The Provider Adapter maps each CLI's terminal event to `session.completed`:

| Provider | Completed | Treated as not completed |
|---|---|---|
| Claude Code (`--output-format stream-json`) | `result` with `subtype=success` | `result` error subtypes, exit without `result` |
| Codex (`exec --json`) | `turn.completed` | exit 0 without `turn.completed` |

The final session state is decided like this:

- terminal success event seen → `COMPLETED` (even if the exit code is not 0; the code is
  recorded);
- no terminal event, cancel requested → `CANCELLED`;
- no terminal event, timeout fired → `TIMED_OUT`;
- no terminal event, exited by itself (any code, **including 0**) → `INTERRUPTED` with
  reason `missing_completion_event`;
- error event → `FAILED`.

### 4. First adapter

Claude Code on macOS is the Phase 1 prototype: streaming events, hooks, MCP success,
subscription login and resume all have evidence; its detached child was cleaned up in
the one test. Codex comes second. The tree logic above is built for the Codex case from
day one, so adding Codex does not change the cancel path.

## Tests that must exist before the Phase 1 gate

- A fixture CLI (our own small program, clearly named as a fixture, not presented as a
  provider) that forks a `setsid` grandchild and a double-forked great-grandchild,
  prints events and ignores `SIGTERM`. Cancel must leave nothing running. This tests our
  code; it does not replace real-CLI evidence.
- A real-CLI smoke per provider (VERIFIED-mac): start → a tool call that spawns a detached
  child → cancel → check by `(pid, start_time)` that nothing is left.
- Exit 0 without a completion event → `INTERRUPTED/missing_completion_event`.
- PID reuse: a recorded pid whose start time has changed is never signalled.

## Consequences

- Positive: cancel results match what happened; the Codex leak case is covered;
  "completed" means the provider said so.
- Negative: periodic scans cost CPU. The scan reads the process table once per tick for
  all sessions, not once per session, to stay well under the 1.x polling cost (PROBE-B).
  *Superseded (2026-11-07 performance update): the scan is a 10 s backstop only.*
- Negative: on macOS a process that both double-forks within one scan interval and
  clears its environment can escape. This is reported as `escaped_marker_check`, not
  hidden.

## Update 2026-11-07 (first real Claude Code run, VERIFIED-mac)

Measured with Claude Code 2.1.319 (`-p --output-format stream-json`), Bash tool running
`python3 -c "subprocess.Popen(['sleep','47'], start_new_session=True)"`:

- Natural finish: `result/success`, exit 0 → `COMPLETED`. Matches section 3.
- **The detached `sleep` survived both cancel and cleanup.** Two reasons, both measured:
  1. **Layer 3 is blind to Apple platform binaries.** `KERN_PROCARGS2` returns no
     environment for `/bin/sleep`, `/bin/zsh` (Apple-signed, `Platform identifier`),
     although the marker was set (the Python in between saw it in `os.environ`). It does
     return it for our own binaries and for the Command Line Tools Python. So the marker
     only covers non-platform programs.
  2. **Layer 2's periodic scan is too slow** for "parent spawns and exits at once": the
     intermediate Python exits within milliseconds, the `sleep` is re-parented to
     launchd before a 1 s scan sees it.
- Negative PoC: macOS "responsible pid" (`responsibility_get_pid_responsible_for_pid`
  with a disclaimed spawn) does **not** follow descendants; `sleep` was responsible for
  itself with or without `setsid`. Not usable.
- Positive PoC: **kqueue `EVFILT_PROC` + `NOTE_FORK`** on every recorded pid, and on each
  fork event listing that pid's children (`proc_listpids` by parent) and watching them
  too, caught the re-parented `sleep` **5 of 5 runs** for this shape. One shape, five
  runs: evidence, not proof that the race is closed.

Change to the decision: on macOS, layer 2 becomes **event-driven** (kqueue fork
watcher) with the periodic scan kept as a backstop; layer 3 stays for non-platform
programs. The fixture gains a mode where a short-lived parent starts `/bin/sleep` in a
new group and exits at once, so the watcher has its own test and knife. The Phase 1 gate
still needs the real-Claude cancel smoke to come back clean.

## Update 2026-11-07 (performance budget, ADR 0005)

This replaces the scan intervals in section 1 ("every 1 s while BUSY, 5 s while IDLE")
and the "periodic scans cost CPU" consequence.

- **macOS:** layer 2 is the kqueue fork watcher (`EVFILT_PROC` with `NOTE_FORK` and
  `NOTE_EXIT` on every recorded pid; on each fork, list that pid's children by parent and
  watch them too). The ppid scan stays only as a **10 s backstop**, as an `Operation`
  timer that exists only while a session has a live tree.
- **Windows:** the Job Object is associated with an I/O completion port; aibd learns about
  new and exited processes from `JOB_OBJECT_MSG_NEW_PROCESS` / `EXIT_PROCESS` /
  `ACTIVE_PROCESS_ZERO` without polling. Status: UNVERIFIED (not built yet).
- **Replaces** the sentence "Children cannot leave the job, so `TerminateJobObject` stops
  the whole tree" in section 1. New wording: `TerminateJobObject` stops the processes that
  are **confirmed** to be in the job and were created without breakaway. A process that is
  not in the job (for example started with breakaway allowed by an outer job, or by
  something outside the tree), or whose membership cannot be observed, is outside that
  guarantee: the cancel result is `tree_not_clean` when it is seen still running, and
  UNVERIFIED when it cannot be observed. It is never reported as a clean cancel.
- **macOS lineage by unique id** (ruling 9320/a63ab 2026-11-07, implemented in the
  Platform Adapter's FFI crate). Measured: after a process is re-parented to launchd its
  `p_puniqueid` (from `proc_pidinfo` `PROC_PIDUNIQIDENTIFIERINFO`) still holds the original
  parent's `p_uniqueid` (3 of 3 runs); unique ids are 64-bit and never reused. So a
  descendant whose parent exited at once can still be claimed exactly, **on one
  condition: the parent's unique id was recorded while the parent was alive**. If an
  intermediate process forks and exits before it is recorded, its children cannot be
  linked; that gap remains and is reported as `tree_not_clean`. The fixture's
  short-lived-parent mode missed about 1 run in 3 under a parallel test load with the
  fork watcher alone; that is the case this closes.
- **Pending measurement — until measured these are proxies, not guarantees** (raised by
  @bb7dc):
  - whether `NOTE_FORK` sees every short-lived descendant: a child that forks and exits
    before we register a watch on it can still let a grandchild slip to launchd;
  - whether processes started with `posix_spawn` (rather than `fork`) raise `NOTE_FORK`
    on the parent;
  - what `TotalProcesses` actually covers on Windows: processes ever assigned to the job,
    nested jobs created by the CLI itself, and anything started with breakaway.
  Each gets a fixture mode and a knife in Phase 1; the real-Claude cancel smoke remains
  the gate. A miss is reported as `tree_not_clean`, never as a clean cancel.
- The same watchers provide the descendant count for the B1 gate ("count only aibd's own
  descendants"); the count carries the same proxy caveat.
