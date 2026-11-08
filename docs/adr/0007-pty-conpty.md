# ADR 0007 — PTY / ConPTY for interactive sessions

- Status: accepted for direction and invariants (maintainers' summary DECISION-13,
  9320/a63ab, 2026-11-08). API names and the remaining details are decided in the
  implementation PR. The Windows combination of `PSEUDOCONSOLE` + `JOB_LIST` is
  UNVERIFIED until its knife runs on CI.
- Deciders: @043dd (author), @a9136 (aibd / lifeline shim), @bb7dc (review), @1ced7
  (review / measurement), 9320/a63ab (approval)
- Depends on: ADR 0003 (WebSocket reserved for the terminal view), ADR 0004 (process trees,
  cancel), ADR 0005 (performance budget), DECISION-8 item 2 / DECISION-11 (Job Object, crash
  semantics, lifeline shim), `aib-sys::job` (#492).

## Context

- Phase 1 runs Claude Code non-interactively over pipes (`-p --output-format stream-json`,
  ADR 0004 §4). That path needs no terminal and **does not change**.
- Interactive sessions need a terminal. Examples: subscription login, CLIs that refuse to
  run or behave differently without a TTY, and the terminal view that ADR 0003 reserves
  for Phase 3. ADR 0001 already lists "ConPTY interaction with a real CLI login" as
  UNVERIFIED-desktop.
- Constraints already in force:
  - A CLI never runs outside its Job on Windows (DECISION-8 item 2).
  - An aibd crash ends the seats it manages (DECISION-11 B).
  - `unsafe` OS calls live only in `aib-sys`, whose own dependencies are an exact
    allow-list (`SYS_OWN_DEPS`, #492).
  - Nothing polls while nothing changes (ADR 0005).

## Options considered

### A. `portable-pty` — rejected

Checked against portable-pty **0.9.0**, the latest stable release (2025-02-11, MIT, crate
sha256 `b4a596a2b3d2752d94f51fac2d4a96737b8705dddd311a32b9af47211f08671e`). The source
was read only: nothing was built, its build script was not run, and nothing was vendored.

- **The child is not born in our Job, and this cannot be changed.**
  - `src/win/psuedocon.rs` L127–128: the attribute list has capacity 1 and holds only
    the pseudo-console, so there is no room for `JOB_LIST`.
  - L145: the creation flags are fixed to
    `EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT`, with no
    `CREATE_SUSPENDED`.
  - L168: the main-thread handle is dropped at once.
  - `src/win/mod.rs` L21–23: the child type keeps only the process handle.
- **It loads `conpty.dll` by bare name when one is present** (`psuedocon.rs` L52–58).
  That is a DLL search-order dependency for a background daemon.
- **It panics instead of returning an error** when ConPTY is missing (L48).
- **Dependencies.** It uses `winapi` 0.3 (not `windows-sys`), plus `winreg`,
  `shared_library`, `nix`, `filedescriptor`, `serial2` and others. Its own lock file lists
  79 packages, dev-dependencies included.

Behaviour worth matching (not its code):

- On Unix, the child does `setsid` (L257), then `TIOCSCTTY` (L271), then closes stray fds
  (L276).
- On Windows, the std handles are set to `INVALID_HANDLE_VALUE` so the child does not
  inherit the parent's redirected stdio (L116–124).

### B. Our own implementation in `aib-sys` — chosen

The macOS side uses `openpty` (libc, already allowed). The Windows side uses ConPTY
through `windows-sys`. It needs 1–2 more features (for example pipes), each added to
`SYS_OWN_DEPS` in the same PR. There are no new crates.

### C. Mixed (portable-pty on macOS only) — rejected

The macOS side is the small half, and it has to integrate with the shim anyway.

## Decision

1. PTY support is written in `aib-sys` (option B). Phase 1 keeps pipes. PTY is implemented
   before the Phase 3 gate.
2. aibd owns the terminal:
   - on macOS, the PTY **master**;
   - on Windows, the `HPCON` and both of its pipe ends on aibd's side.

   No other process holds them.
3. The macOS lifeline shim (`aibd --shim`, DECISION-11 C) inherits exactly two fds: the PTY
   slave and the lifeline read end. It then does the following, in order:
   - `setsid`;
   - makes the slave its controlling terminal (`TIOCSCTTY`);
   - spawns the CLI in a new process group;
   - makes that group the terminal's foreground group (`tcsetpgrp`).

   The shim's own `(pid, start time)` is recorded as a session member, so start-up
   reconciliation can collect it too. In pipe mode the shim is the same program without a
   slave.
4. On Windows there is no shim. The CLI is created with `CreateProcessW` +
   `STARTUPINFOEX` carrying **both** `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` and
   `PROC_THREAD_ATTRIBUTE_JOB_LIST`. `KILL_ON_JOB_CLOSE` covers an aibd crash.
5. API names, resize, UTF-8 handling and similar details are left to the implementation
   PR. Before any code, that PR states the following contracts (from @bb7dc):
   - ownership of every fd / handle;
   - inheritance rules;
   - close order;
   - how errors and incomplete results are reported.

## Invariants (each has a knife that must turn a named test red)

1. **Windows: born in the Job.**
   - Rule: `PSEUDOCONSOLE` and `JOB_LIST` go in the same attribute list. The CLI never
     runs outside its Job. The pseudo-console host (`conhost` / `OpenConsole`, a child of
     aibd) goes into **a separate Job** with `KILL_ON_JOB_CLOSE`. This is not left to
     "it probably exits by itself"; the separate Job can be dropped later only if
     measurement shows it is redundant.
   - Knife: drop `JOB_LIST` and assign after the spawn → a barrier test sees the child
     running outside the Job.
   - Knife: drop the host's Job → kill aibd and the host is still alive after the bound.
2. **No fd / handle leaks into another session.**
   - macOS rules:
     - The master is `FD_CLOEXEC`. `posix_openpt` cannot set close-on-exec atomically
       on macOS.
     - aibd is multithreaded, so every spawn either uses `posix_spawn` with
       `POSIX_SPAWN_CLOEXEC_DEFAULT` (only the fds named in the file actions are
       inherited), or opening a PTY and spawning share one lock. Which one is chosen,
       and how it is reached from Rust, is decided in the implementation PR.
   - Windows rules:
     - `bInheritHandles = FALSE`, or a `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` allow-list.
     - The std handles in `STARTUPINFO` are `INVALID_HANDLE_VALUE`.
     - The two pipe ends handed to `CreatePseudoConsole` are closed in aibd right after
       the call.
   - Knife (macOS): open two sessions concurrently; A's master must not appear in B's
     CLI fd table. Otherwise A's SIGHUP path silently disappears.
   - Knife (Windows): two concurrent sessions; closing A's `HPCON` must give A's reader
     EOF within the bound.
3. **The shim holds no copy of the master, and its fds are an allow-list.**
   - Rule: the shim keeps the slave and the lifeline read end and closes everything else.
     This is an allow-list, not a list of known fds to close, so fds opened by
     third-party code cannot slip through.
   - Rule: the shim ignores `SIGHUP` for itself (`SIG_IGN` at start; restored to
     `SIG_DFL` in the child before `exec`). Otherwise a hang-up could kill the shim before
     it runs the cancel order on lifeline EOF.
   - Knife: the shim keeps a dup of the master → the "SIGHUP alone" test goes red.
   - Knife: remove `SIG_IGN` → the "lifeline EOF alone collects the tree" test goes red.
4. **SIGHUP is a second signal, not a cleanup guarantee.**
   - Who receives the hang-up on macOS is measured, not assumed: the foreground group
     and/or the session leader. A CLI that moves to another group, or ignores SIGHUP, is
     not reached.
   - Nothing reports `clean` because of SIGHUP. If the shim was killed, or a grandchild
     left the group or the session, the result is `tree_not_clean` / UNVERIFIED.
   - The final word is start-up reconciliation (DECISION-11) and CRASH-TEST-SPEC v0.3
     assertion 1. A result collected by the shim is never reported as `clean`.
5. **Graceful stop does not rely on `\x03` alone.**
   - In raw mode (`ISIG` off) `\x03` is just a byte, and ConPTY's translation depends on
     the console mode and the CLI. aibd writes `\x03`, waits the grace period, and then
     always continues with the cancel order of ADR 0004 §2 (Job terminate on Windows;
     group signals via the shim on macOS).
   - Knife: the fixture enables raw mode and ignores the byte; a stop that only writes
     `\x03` must fail to stop it, which proves that the later steps run.

Also required, from the review:

- Reads and writes do not poll. On macOS the master is in the event loop; on Windows each
  session has one blocking reader thread.
- `ClosePseudoConsole` is bounded: the output pipe is drained while the console is open
  and while it closes. A close that would hang is reported, not waited on forever.
- Knife: stop draining → the bounded close fails red instead of hanging the test.

## Tests

- Fixture: the Rust test double of CRASH-TEST-SPEC v0.3, with a `--tty` mode. That mode
  reports `isatty(0/1)`, the window size and the termios mode, and can switch to raw mode.
- On both platforms:
  - echo round-trip;
  - resize seen by the child;
  - a multi-byte UTF-8 character split across reads is reassembled;
  - stop with and without raw mode;
  - aibd SIGKILL / `TerminateProcess` → every recorded member is gone (CRASH-TEST-SPEC
    assertion 1).
- Timing-dependent tests: 5 consecutive green runs per platform (DECISION-7 item 3).

## Status of evidence

- Windows CI (`windows-latest`) verifies the mechanism with the fixture only.
- Real AI CLIs under ConPTY (login included) stay **UNVERIFIED-desktop**. Fixture
  results are not used to mark them green.
- Also UNVERIFIED: ConPTY on Windows builds older than the CI image (Server 2025,
  10.0.26100 as of #492). ConPTY needs Windows 10 1809+ and `JOB_LIST` needs 1607+; both
  are to be re-checked against Microsoft's documentation in the implementation PR.

## Consequences

- `aib-sys` grows a `pty` module on both platforms, and `SYS_OWN_DEPS` gains 1–2
  windows-sys features.
- For a while Windows has two spawn paths:
  - pipes via suspend → assign → resume (#492);
  - PTY via `JOB_LIST`.

  Once the `JOB_LIST` path has CI evidence, we decide whether to unify on it.
- Every PTY session adds a resident pseudo-console host on Windows. It is an aibd
  descendant, so it counts in the B1 / B10 measurements of ADR 0005.
- The shim's fd allow-list and its SIGHUP handling become tested rules for pipe mode too.
