# ADR 0008 — Four rules for stopping a process group (proc_group and aibd)

- Status: proposed (a63ab outline r2 P1-3, 2026-11-08). Rules 1–4 are already in force in
  Python (`aib/src/cockpit/proc_group.py`, #509 merged). This ADR writes them down once, adds
  the OS call that Rust needs for rule 3 (`aib-sys` M5), and records the interrupt behaviour
  that #520 adds. `aibd`'s `platform/tree.rs` adopts rule 3 in a follow-up PR, not here.
- Deciders: @a9136 (author), @043dd (review), 9320/a63ab (approval)
- Depends on: ADR 0004 (process trees, cancel), DECISION-20 (one shared stop tool),
  DECISION-14 #3 (`killpg` EPERM = check again), API list v0.3 (M5), J0 (Windows Job Object,
  #492).

## Context

- Two places stop a group of processes they started: `proc_group.run_group` (Python: prepr,
  `aib test`, host lease after #500) and `aibd::platform::tree` (Rust: a session's CLI tree).
  They were written apart, and reviews kept finding the same gaps in both: a signal to a pid
  that was already reused, a "stopped" report while a member was still alive, and a reap that
  freed the leader's pid while signals were still going to its group.
- Measured on macOS 15.7.9 (arm64), 2026-11-08, in `crates/aib-sys/tests/macos.rs`:
  - `waitid(P_PID, pid, WEXITED | WNOWAIT [| WNOHANG])` on our own child reports its exit
    status and **leaves it a zombie**: the same call answers again, and the one real `wait()`
    afterwards still gets the status.
  - For that zombie, `proc_pidinfo` M1 (unique ids) and M2 (`PROC_PIDTBSDINFO`) both return
    `ESRCH` ("gone"), although the zombie still holds the pid. So for **our own child**,
    "gone" from M1 / M2 does not mean the pid is free; only M5 tells.
  - After the real reap, M5 returns `ECHILD`.
  - Python before 3.13 has no `os.waitid` on macOS. `proc_group` calls libc `waitid` through
    ctypes there (siginfo_t is 104 bytes, `si_pid` at offset 12); that path is tested on the
    system Python 3.9.6.

## Decision

### Rule 1 — Only our own group

Every command we stop runs in a new session (`setsid`, group = its pid). We never send a group
signal to a group we did not create. The caller never gets the `Popen` / `Child` (so nobody
else can reap it behind our back).

### Rule 2 — Identity before every signal, and once lost it stays lost

The leader's (pid, start time) is recorded when it starts. Before **every** signal it is read
again; if the start time cannot be read or differs, nothing is sent and the report is
`unverified`. A lost identity is latched: later steps of the same stop send nothing more.
In Rust, `unique` (M1) is compared too when it is available; M1 only adds candidates and never
decides an action alone (aib-sys rules).

### Rule 3 — Look at the leader, do not reap it until every signal is sent

**Premise: `SIGCHLD` keeps zombies.** If it is `SIG_IGN`, or a handler has `SA_NOCLDWAIT`, the
kernel reaps every exited child at once: `WNOWAIT` has nothing to look at and the pid is free
immediately (measured on macOS by 043dd, #522 M1). Both are inherited across `exec`, so a
launcher that ignores `SIGCHLD` hands it to aibd without aibd doing anything. Therefore:

- M5 says so: an `ECHILD` while `SIGCHLD` does not keep zombies is
  `SysError::ChildrenAutoReaped` (tag `children_auto_reaped`), not a bare `NotChild`; both mean
  "send nothing", the first also names the cause;
- `aibd` sets `SIGCHLD` back to `SIG_DFL` when the server starts if it does not keep zombies, and
  logs `SIGCHLD reset to default` with what it was (`aib_sys::sigchld_keep_zombies`);
- `aibd selfcheck` shows a `sigchld` row: `ok` when zombies are kept, `unverified` (with
  `disposition=ignored|no_child_wait`) when it was inherited that way, `failed:sigchld:<tag>` when
  it cannot be read;
- `proc_group` (Python) cannot fix a caller's process; its module doc states the consequence:
  the identity check sees `leader_reaped_elsewhere` and nothing is sent (safe, always unverified).

Whether the leader has exited is read with `waitid(..., WEXITED | WNOWAIT | WNOHANG)`:

| place | call |
|---|---|
| Python ≥ 3.13, or Linux | `os.waitid` |
| Python < 3.13 on macOS | libc `waitid` through ctypes |
| Rust (`aib-sys` M5) | `ChildExit::peek` / `ChildExit::wait_exit` |
| neither available | `poll()` (reaps; the report says so) |

- The result counts only when the kernel's `si_pid` is the pid we asked about. Anything else
  is "unknown", never "exited".
- `ECHILD` means somebody else reaped it: its pid may already be reused, so **no group signal
  is sent** after that.
- A zombie leader is still ours (it pins the pid and the group number). TERM / KILL to the
  group are allowed while it is a zombie, and the single real `wait()` comes **after** the last
  signal.
- `aibd`'s root-wait thread today calls `child.wait()` as soon as the root exits. The
  follow-up PR changes it to `ChildExit::wait_exit` (no reap) and reaps after the cancel
  sequence, the same order as `proc_group`.

### Rule 4 — Verify, never assume

After TERM, grace, and KILL, the group is listed again. Only an empty group (or a group with
just our zombie leader) is `stopped`. Each of these is `unverified`, with the pids:

- `EPERM` from `killpg` with any member still alive (DECISION-14 #3: "check again", never
  "done");
- a listing that fails;
- a member still running after KILL.

A normal exit that leaves members behind gets the same stop. On Windows without a Job Object
(before J0) only the direct child is killed, and the report says
`windows_direct_child_only` / `windows_leftovers_not_checked`. It never says `stopped`.

### Interrupted while waiting (#520, proc_group)

- **Handlers only record.** While `run_group` runs on the main thread, its SIGINT / SIGTERM
  handlers append the signal to a list and never raise. `run_group` looks at the list at fixed
  points and acts as the caller's own handler would: Python's default Ctrl-C raises
  `KeyboardInterrupt`, `SIG_DFL` stops the group first and then runs the default action, a
  callable is called there (what it raises is the interrupt; if it returns, the run goes on),
  `SIG_IGN` does nothing. So nothing can be raised between `except` and the stop, and nothing is
  delivered twice. There is no timer thread.
- **Behaviour change:** a Ctrl-C no longer raises synchronously inside `run_group`; it acts at
  the next check, at most `POLL_S` (0.05 s) later while waiting. Every wait from the start to the
  reap is a bounded poll with a check, the final reap included (no bare `wait()`).
- **One hard deadline per stop**, `grace + 2 × KILL_WAIT_S` (5 + 2 + 2 = 9 s by default), counted
  from when the stop starts: the timeout stop, the leftovers listing and stop after a normal exit,
  and the stop after an interrupt. A Ctrl-C during a running stop keeps that stop's deadline (no
  new 9 s). Every wait in it, the external `ps` included, is cut there (`stop_cut_short_at_limit`,
  `leader_not_reaped`): one press always gets the user out within the limit.
- After the stop the same exception is raised again with the report attached. Signals recorded
  during the stop go to the caller's handlers afterwards, each once, in order; a held Ctrl-C is
  dropped only when the exception already leaving is itself a `KeyboardInterrupt`.
- **Nesting:** handlers are per process. The outermost `run_group` on the main thread installs;
  one started inside it (e.g. from a caller's handler) reuses that record and decides with the
  caller's real handlers, so one Ctrl-C stops the inner group, then the outer one. Off the main
  thread nothing is installed and the caller's handlers act as usual.

## Windows

`waitid` has no Windows counterpart, and none is needed. A process HANDLE stays valid after
exit, so the pid cannot be reused while we hold it: `WaitForSingleObject` already "looks
without freeing". The group counterpart is the Job Object (J0, #492):

- terminate the job;
- `QueryInformationJobObject` lists what is left.

This PR adds no Windows implementation of `ChildExit`; Windows callers go through the Job Object path. All Windows statements here are
**UNVERIFIED** (no real machine).

## Consequences

- `aib-sys` gets M5 (public POSIX, own children only), `SysError::NotChild`, and
  `sigchld` / `sigchld_keep_zombies` for the premise above.
- `tree.rs` adopts rule 3 in the next PR. Until then its group signal is guarded by
  `group_is_ours` (ADR 0004), which reads M2. That check has a known blind spot: our own zombie
  root reads as gone. **Its direction is conservative**: `group_is_ours` then checks the other
  live members (start time + unique + pgid == root) and still accepts the group if one matches;
  when only the zombie root is left it sends nothing and reports `unverified`. It never signals
  someone else's group, so it is not a fix-first item.
- `proc_group` and `aibd` can be reviewed against one list. A new stop path that breaks a rule
  needs a new ADR, not a code comment.

## Tests

- `aib-sys`:
  - `exit_state_counts_only_for_the_asked_pid` (pure mapping, including a foreign `si_pid`);
  - real `m5_*` tests: running, exit status without reaping and the reap still works, a
    killed child, a process that is not ours, `m5_wait_retries_when_a_signal_interrupts_it`
    (three SIGUSR2 without `SA_RESTART`; the handler ran and the wait did not return);
  - `tests/sigchld.rs` (own process): `SIG_IGN` and `SA_NOCLDWAIT` auto-reap ⇒ M5 `NotChild`;
    `sigchld_keep_zombies` puts `SIG_DFL` back and M5 reads `Exited(7)` again; a handler that
    keeps zombies is left alone;
  - the zombie-`Gone` assertion pins macOS 15.7.9 behaviour; its message says so, so a red cell
    after a runner upgrade points here, not at the code;
  - fake `looking_never_reaps_until_the_caller_does`.
- `aibd`: `tests/sigchld_reset.rs` starts the real server under a parent that ignores
  `SIGCHLD` and expects the reset warning; `selfcheck` unit and CLI tests cover the `sigchld` row.
- Python: `aib/tests/test-proc-group-a9136-1108.py` (rules 1–4, the libc path on 3.9.6, and
  the #520 interrupt tests), with the knife table in #509 / #520.
