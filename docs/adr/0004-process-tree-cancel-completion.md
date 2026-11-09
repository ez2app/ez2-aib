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
- **macOS lineage by unique id** (decided: maintainers' summary DECISION-1, 2026-11-07;
  the call lives in the `aib-sys` crate, see ADR 0005 Consequences).
  - Observation, not yet a guarantee: on macOS 15.7.9, after a process was re-parented to
    launchd its `p_puniqueid` (from `proc_pidinfo` flavor `PROC_PIDUNIQIDENTIFIERINFO`)
    still held the original parent's `p_uniqueid` in 3 of 3 PoC runs. That unique ids are
    not reused is an XNU implementation property we rely on, **not** a documented
    contract. Both are hypotheses until the fixture and a real-machine run confirm them,
    and they are re-checked on every major macOS version (capability table; versions not
    tested are UNVERIFIED).
  - Flavor 17 is a **private SPI**: it is not in the public SDK headers (checked against
    the macOS 15.7.9 SDK), only in XNU sources. Its availability and the size of the
    structure are checked on every call; a size mismatch is a failure.
  - What it may be used for: to *find* more candidates. It never on its own permits a
    signal. Before any signal aibd re-checks identity with the public data (pid and start
    time), and with the unique id where available. If identity cannot be read or does not
    match, **no signal is sent** and the result is `tree_not_clean` (or UNVERIFIED when the
    process cannot be observed).
  - If the call fails or is unavailable, aibd falls back to the event watcher plus the
    10 s backstop described above, marks the result `lineage_unavailable`, and counts it.
  - Remaining blind spot: an intermediate process that forks and exits before its own
    unique id is recorded cannot be linked to its children. The 10 s backstop does not
    claim to find every descendant. Such misses are reported as `tree_not_clean`.
  - Measured before this change: the fixture's short-lived-parent mode missed about 1 run
    in 3 under a parallel test load with the fork watcher alone.
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

## Update 2026-11-09 (Windows Job Object in `aib-sys`, DECISION-8 item 2 / DECISION-11 / DECISION-21)

Windows section drafted by @043dd, merged here by @a9136 and checked against the code on
`main` (`crates/aib-sys/src/job.rs`, `crates/aib-sys/tests/windows.rs`,
`scripts/check-win-job-knives.sh`). What is on `main` came in with #492, #535 and #539
(references below are `file:line` at `main` d018516c). The aibd session adapter on
Windows is #543, still a draft: those points are marked **(#543)** and are not a decision
until it merges. Status: CI-windows (GitHub
`windows-latest`); desktop behaviour is UNVERIFIED.

- **Where.** `aib-sys::job` is the only place with `unsafe` for this (`windows-sys
  =0.61.2`, the feature list is checked by `aib-xtask deps`).
- **Start.** `CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP`, then `AssignProcessToJobObject`,
  then resume the single thread. If the assign or the resume fails, or the process does
  not have exactly one thread, the process is terminated and reaped and the spawn fails.
  A CLI never runs outside its job, not even for a moment.
- **Job limits.** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. `BREAKAWAY_OK` and
  `SILENT_BREAKAWAY_OK` are never set, so `CREATE_BREAKAWAY_FROM_JOB` from inside the tree
  is refused (tested). A breakaway allowed by an *outer* job is outside this guarantee
  (see "Update 2026-11-07 (performance budget)").
- **J0 (DECISION-21).** `aib-sys` can put the calling process into its own job with
  `KILL_ON_JOB_CLOSE`, no breakaway and a non-inheritable handle; session jobs are then
  nested in it. aibd adopts J0 first thing in `main`, before the runtime and before any
  child exists, and refuses to start if it cannot (`crates/aibd/src/main.rs:42`).
- **Identity.** `(pid, creation time)`, with the creation time from `GetProcessTimes`
  (FILETIME, 100 ns). The ppid is never read. Membership is re-checked with
  `IsProcessInJob`, so a reused pid outside the job is never reported as a member.
- **Member list.** A list that is still too short after the retries is
  `SysError::Incomplete`, never a short list.
- **Waiting.** The job is associated with an I/O completion port; aibd waits for
  `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` and re-checks the active count after every message
  (a queued "zero" from before a later spawn does not count). If the port cannot be
  attached it polls every 20 ms and reports `wait_mode = poll`.
- **What "clean" means.** Unchanged: `Report::clean_status()` gives `clean` only with
  proven containment and complete lineage (DECISION-23); everything else is
  `tree_not_clean` or `unverified` with a reason. On Windows the settle rule is on `main`
  (`KillReport::all_gone`, `crates/aib-sys/src/settle.rs:41`); the session stop that uses
  it is **(#543)**: `Clean` only when the job emptied **and** every member handle opened
  before `TerminateJobObject` has signalled; survivors ⇒
  `TreeNotClean`; an error ⇒ `Unverified("job_error:<tag>")`.
- **Cancel on Windows.** Replaces "Windows: go straight to step 3" in section 2.
  - `aib-sys` can send `CTRL_BREAK_EVENT` to the CLI's group, only while the root is a
    current job member; pid 0 (everything on the console) is refused. Without a console
    shared with aibd, or under ConPTY, the result is `Unsupported`. A graceful stop under
    ConPTY belongs to the PTY layer (`\x03` on input), not to the job.
  - The session stop **(#543)** has no TERM step at all: a hidden aibd or a service has
    no console to share. `grace` only widens the wait. Then `TerminateJobObject`, one
    bounded wait for the job to empty, and the check above. No `taskkill /T`; no pid
    outside the job is touched.
- **aibd crash = the sessions it runs end (DECISION-11 B).** When aibd dies the kernel
  closes its handles, the job's last handle goes with them, and `KILL_ON_JOB_CLOSE` ends
  every process still in the job. This is deliberate: aibd holds the CLIs' stdio / PTY, and
  a CLI that outlives aibd cannot be reattached; it would only keep spending or editing
  files unattended. After a restart only what is in the event log comes back; a process is
  never "reattached". Tested in CI by ending an owner with `TerminateProcess` (no destructor
  runs) and requiring its grandchild to be gone within 10 s. macOS has no such kernel
  guarantee (next update, last point).
- **Not the same as closing the UI.** Closing the web UI or a terminal does not close the
  job; only aibd ending, or a cancel, does.
- **Knives in CI** (`scripts/check-win-job-knives.sh`; each must turn its named test red):
  `no-suspended`, `no-terminate`, `breakaway-ok`, `no-kill-on-close`,
  `assign-fail-no-kill`, `resume-fail-no-kill`, `trust-the-kill`, `break-an-exited-root`,
  `break-a-non-root`, `no-clear-last-error`, `record-after-resume`, `j0-no-adopt`,
  `j0-inheritable`, `j0-breakaway-ok`.
- **UNVERIFIED:** real AI CLIs started inside a job; `CTRL_BREAK` under ConPTY and without a
  console; jobs the CLI nests itself (`TotalProcesses` coverage, raised by @bb7dc); the
  whole of #543 on a real machine.

## Update 2026-11-09 (sessions preview.2 on macOS: #544, #546, #549)

The rules the sessions API follows on macOS. The numbers in brackets are the preview.2
decision list (a63ab, 2026-11-09).

- **Never `clean` on macOS** (DECISION-23). A stop is `tree_not_clean` (a recorded process
  still runs) or `unverified` with a reason code. The API reports it as
  `stop_unconfirmed`, with the reason and the survivors.
- **H1: the root is kept unreaped.** The root is looked at with `waitid(WNOWAIT)`
  (`aib-sys` M5, #522) and reaped only after the last signal, so its pid cannot be reused
  while aibd still signals the group. A group signal goes out only after
  `root_is_our_unreaped_child()` confirms the group is still ours; when the root's
  identity cannot be proven (someone else reaped it), no group signal is sent and
  `root_identity_lost` is recorded as a gap. The stop is then `tree_not_clean` if a
  recorded process is seen still running (survivors are checked first,
  `Report::clean_status()`), otherwise `unverified` with that reason.
- **The slot.** A stop frees the session's slot only when it saw no survivor in what it
  could observe and its result is on the allow list below. This is not a proof that the
  whole tree is gone, nor a resource-isolation guarantee: `observed_clean` still has its
  blind spots (`Report::blind_spots()`, `BLIND_SPOTS_UNCONTAINED` in
  `crates/aibd/src/platform/tree.rs`: `exec_after_creator_exit`, `env_cleared`,
  `fork_watch_miss`, `uid_changed`).
  `Stop::releases_slot()` = `clean`, or `unverified` with a reason in
  `SLOT_RELEASING_REASONS` (today only `observed_clean`), and no survivor. Anything else
  keeps the slot; a second DELETE runs the stop again (H2) [6, 7, 8].
- **Background re-check** [9]. A stop ending `candidates_unreadable` with no survivor is
  re-checked in the background for at most 30 s (`session.slot_rescan` timer, every 2 s):
  - The window is one absolute end. Each pass runs on its own thread with the grace that
    still fits, and is waited for only until that end.
  - A pass that has not answered by then ends the re-check with
    `session.slot_held {reason: rescan_pass_timeout}`, slot kept. Its late result is
    recorded and never frees the slot.
  - `observed_clean` frees the slot (`session.slot_released`); a survivor, a DELETE or the
    shutdown ends the re-check. Other reasons are not re-checked: another pass would not
    change them.
- **A start that fails after spawn** [11, 12]. The tree handle always comes back and the
  tree is stopped (TERM, then 2 s, then KILL). If that stop does not free the slot, the
  slot stays held (`left_running`) and the tree is kept to be stopped again at shutdown.
- **Shutdown** [10, 13]. One cap of 10 s for everything:
  - Set `shutting` (new POSTs get 503).
  - Stop every live session, each on its own thread.
  - Wait for in-flight starts to finish.
  - Stop the kept trees from failed starts, all in parallel under the same deadline.
    Trees not confirmed by then are logged with `(pid, start time)`, never as stopped.
- **`aibd selfcheck`** (contract §6). `process_management` really starts and stops one small
  tree, but only after the tree's child has reported it started; otherwise the row is
  `unverified` / `child_not_started`. The row is never better than the stop's own answer.
- **`aibd` killed with `kill -9`** [15]. On macOS its sessions' processes are left running.
  This is a known limit, stated as such; Windows covers it with `KILL_ON_JOB_CLOSE`
  (previous update, UNVERIFIED on a real machine).

## Update 2026-11-10 (G1: a program that ends at once gives its slot back, macOS — partly)

preview.3 known issue: on macOS a session whose program ends at once (`claude`, `printenv`,
`sleep 0`) kept its slot (1ced7 gate, 2026-11-09). Two causes, two codes:

- **`fork_watch_list_failed` was a false failure.** The fork watcher listed children with
  libproc 0.15 `pids_by_type`, which treats "0 pids and errno ≠ 0" as an error without
  clearing errno first. A childless process returns 0, so an ESRCH left by an earlier call
  read as "cannot list". The watcher now lists and reads through `aib-sys` (M2 / M3), which
  clears errno before each call and tells `Gone` apart from every other error.
- **`fork_watch_child_unreadable` from a real race.** A child is listed, then exits before
  its start time is read (ESRCH, `SysError::Gone`).
  - With lineage on, that is no longer a gap. The watcher notes it (`unread_gone`, latched
    for the whole session), and the stop's candidate check is widened: every same-uid
    `ppid` 1 process newer than the root whose `parent_unique` is also newer than the root
    is a candidate. The child's own children carry a `parent_unique` nobody recorded, so the
    narrow `parent_unique == 1` rule would miss them. The unread child was born after the
    root, so its orphans' creator id is larger than the root's; apps the user opened earlier
    are left out (043dd L1).
  - How many candidates the wide rule adds is counted (`Report::orphan_candidates_extra`,
    and `orphan_candidates_extra` in `/api/v1/metrics`). It is measured before anyone
    decides to narrow it.
  - Any other read error (permission, unknown) stays a gap. Without lineage, "gone" stays
    a gap too.

The best a stop can then say is still `unverified` / `observed_clean`, with the same blind
spots (`exec_after_creator_exit` and the rest), never `clean`. Windows is unchanged (no fork
watcher there). Tests: `crates/aibd/src/platform/fork_watch.rs` (fake probe + one real-OS
stale-errno case), `crates/aibd/src/lineage.rs`
(`an_unread_gone_child_widens_the_orphan_candidates`), and `crates/aibd/tests/sessions.rs`
(`g1_*`, macOS). Knives G1a–G1e are in `.github/workflows/aibd.yml`.

**A setuid child (1ced7 desktop check, 043dd THREAT-G1-setuid, 2026-11-09).** A real `claude`
ended `fork_watch_child_unreadable` 10 times out of 10. It runs `/bin/ps`, which is setuid root, and
M2 (`PROC_PIDTBSDINFO`) is EPERM for such a process.
- Its start time is now read through `sysctl(KERN_PROC_PID)` (`kp_proc.p_starttime`, the same
  `p_start`, `aib-sys` `start_sec_sysctl`, length checked). The process is then followed like any
  other child: watched, and its children listed. No blind spot is added and the wide-rule flag is
  not set.
- The fallback is used only for a process running as another uid (`PROC_PIDT_SHORTBSDINFO`). A
  same-uid process that refuses M2, or a fallback that fails, stays `fork_watch_child_unreadable`.
- The fallback is self-checked once per aibd process: sysctl must equal M2 on aibd itself.
  Otherwise the fallback is off.
- Descendants running as root stay in `uid_changed`.
- sysinfo reads such a process with start time 0. The tree handle's identity check then
  asks the same fallback. A process it still cannot check is never signalled, but it
  counts as alive (a survivor), so the stop is never `observed_clean` because of it
  (043dd efbe548d M1).

**What this does not fix (043dd review, measured on a desktop Mac, 2026-11-09).**
- macOS does not let another process read the environment of Apple platform binaries.
  On a desktop with a login session and Spotlight, launchd starts such services all the
  time: 28 new same-uid `ppid` 1 processes in 10 minutes, 23 of them with
  `parent_unique == 1` and an empty environment (`mdworker_shared`, `followupd`, ...).
- The narrow rule has always taken them as candidates. Their empty environment makes the
  stop `candidates_unreadable`, which keeps the slot.
- So programs that end within milliseconds (`printenv`, `sleep 0`) now give their slot
  back. A session that runs seconds to minutes (`claude`) may still end
  `candidates_unreadable` on a desktop, and keep its slot. Quiet machines (CI runners,
  Mac Pro 13) do not show this.
- The 10-starts `claude` check (1ced7) runs on a desktop Mac and reports the distribution
  of `stop.reason`, not only whether each start worked.
- Telling launchd's own services apart from "re-parented, then exec'd" session processes
  is a separate item. Directions to measure first: responsible pid / coalition, platform
  binary paths.
- Every stop record (`stop.candidates` in the session, `session.killed` /
  `session.stop_unconfirmed` / `session.slot_held` events, the `session stop` log line)
  carries the candidate check: `count`, `orphan_extra` (added only by the widened orphan
  check) and the unreadable candidates as pid + process name (never arguments). A
  `candidates_unreadable` stop then shows which processes held the slot and which rule
  took them (1ced7 2026-11-09: the record had neither).
- At most 16 unreadable candidates are listed; the rest is `unreadable_more` (left out
  when 0), and `count` is always the real total (043dd #607 L1).
- A `slot_held` event after a re-check that timed out carries the candidates of the last
  pass that answered (043dd #607 N1).

**Coalition filter (043dd MEASURE-G1-coalition, 1ced7 c25144e1 DockHelper).**
- A process starts in its parent's resource coalition and keeps it when it is re-parented
  to launchd. launchd starts each service it launches in a coalition of its own. On the
  measured desktop, 77 of 88 new same-uid `ppid` 1 processes in an hour were in another
  coalition than the shell aibd ran under; the session's own re-parented orphan was not.
- Right after spawn aibd reads the root's coalition (`PROC_PIDCOALITIONINFO`, a private
  `proc_pidinfo` flavor) and its own. This is the self-check of the private interface:
  - equal ⇒ the filter is on for that session;
  - the root read as another coalition ⇒ no filter, and none again for the rest of that
    aibd process (the read is not trusted);
  - the root already gone (an instant program; Mac Pro 13 2026-11-09: `printenv` held its
    slot on `AddressBookSourceSync` because the check needed the root) ⇒ aibd's own
    coalition is used;
  - any other error reading the root, or aibd's own not readable (or 0) ⇒ no filter for
    that session.
- A candidate whose coalition reads as 0 is not "another coalition": 0 is not an id.
- A candidate whose environment cannot be read, and whose coalition is read as different
  from the session's, is let go (`stop.candidates.other_coalition`, pid + name, same
  16-entry limit with `other_coalition_more`). It is not
  signalled and does not make the stop `candidates_unreadable`.
- Kept as before: a candidate with the session marker (whatever its coalition), a
  candidate whose coalition cannot be read, and every candidate when the self-check failed.
- Blind spot: a process the session asks launchd / LaunchServices to start (`open -a`, an
  XPC service) gets a coalition of its own. It is not in the session's process tree either
  way; if its environment cannot be read it is now let go instead of holding the slot.
- The interface is private: an OS update may change the struct. `pidinfo` refuses any
  answer that is not exactly 40 bytes, and an unread coalition keeps the rule before.

**Known limit (043dd L2).** The stop reads `unread_gone` when it merges the watcher's
records. A child that the watcher reads after that point, and that has already exited, sets
the flag too late for this stop: the narrow rule is used once. This needs a fork and an
exit right at the moment of the stop, so it is rare. It is stated, not handled.
