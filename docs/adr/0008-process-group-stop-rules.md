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

### Interrupted while waiting (#520)

Any `BaseException` from the start to the reap (Ctrl-C, a SIGTERM handler that raises) stops
the group first and reaps the leader. After that the same exception is raised again, with the
report attached. SIGINT / SIGTERM that arrive during that stop are held, for at most
`grace + 2 × KILL_WAIT_S` (5 + 2 + 2 = 9 s by default). At the limit a held signal is
delivered by itself, without a second press. A held Ctrl-C is dropped only when the exception
already leaving is itself a `KeyboardInterrupt`.

## Windows

`waitid` has no Windows counterpart, and none is needed. A process HANDLE stays valid after
exit, so the pid cannot be reused while we hold it: `WaitForSingleObject` already "looks
without freeing". The group counterpart is the Job Object (J0, #492):

- terminate the job;
- `QueryInformationJobObject` lists what is left.

This PR adds no Windows implementation of `ChildExit`; Windows callers go through the Job Object path. All Windows statements here are
**UNVERIFIED** (no real machine).

## Consequences

- `aib-sys` gets M5 (public POSIX, own children only) and `SysError::NotChild`.
- `tree.rs` adopts rule 3 in the next PR. Until then its group signal is guarded by
  `group_is_ours` (ADR 0004), which reads M2. That check now has a known blind spot: our own
  zombie reads as gone.
- `proc_group` and `aibd` can be reviewed against one list. A new stop path that breaks a rule
  needs a new ADR, not a code comment.

## Tests

- `aib-sys`:
  - `exit_state_counts_only_for_the_asked_pid` (pure mapping, including a foreign `si_pid`);
  - real `m5_*` tests: running, exit status without reaping and the reap still works, a
    killed child, a process that is not ours;
  - fake `looking_never_reaps_until_the_caller_does`.
- Python: `aib/tests/test-proc-group-a9136-1108.py` (rules 1–4, the libc path on 3.9.6, and
  the #520 interrupt tests), with the knife table in #509 / #520.
