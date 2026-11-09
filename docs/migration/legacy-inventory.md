# Legacy inventory — platform coupling in AIB 1.x

Status: Phase 0 draft, 2026-11-07. Scope: open-source core `ez2app/ez2-aib` at `474441f4`.
Private plugins (`aib-plugins`) are counted here by category only; their file-level
inventory lives in the private repository (`aib-plugins/docs/`), not in this repository.

How this was produced (read-only, reproducible):

```sh
python3 -I scripts/audit-legacy/scan.py --repo . --rev HEAD --out /tmp/scan-oss
python3 -I scripts/audit-legacy/scan.py --repo <aib-plugins> --rev origin/main --out /tmp/scan-priv --names-only
```

The script only runs `git grep` against a commit; it never writes inside the scanned
repository. `--names-only` drops matched source text so private code is not copied.
Counts are keyword hits, not call sites: a comment that says "tmux" counts too.
They are good for ranking, not for estimating effort line by line.

## 1. Totals

| Category | Keywords | OSS files / hits | Private files / hits |
|---|---|---:|---:|
| terminal-mux | `tmux` | 268 / 2399 | 15 / 87 |
| file-lock | `fcntl`, `flock` | 95 / 508 | 21 / 117 |
| paths | `/tmp/`, `Library/`, `expanduser("~` | 180 / 392 | 28 / 63 |
| process | `os.fork`, `setsid`, `killpg`, `SIGKILL`, `os.kill`, `start_new_session`, `preexec_fn` | 103 / 318 | 21 / 113 |
| process-discovery | `pgrep`, `pkill`, `lsof`, `ps -` | 66 / 237 | 12 / 37 |
| terminal-app | `osascript`, `Terminal.app`, `open -a` | 49 / 167 | 5 / 10 |
| autostart | `launchctl`, `LaunchAgent` | 23 / 151 | 1 / 8 |
| os-probe | `sysctl`, `pmset`, `sw_vers` | 22 / 88 | 0 / 0 |
| native-helper | `swiftc`, `LocalAuthentication` | 23 / 71 | 0 / 0 |
| shell-string | `bash -c`, `sh -c`, `shell=True` | 24 / 69 | 7 / 10 |
| loader-injection | `LD_PRELOAD`, `DYLD_` | 15 / 33 | 2 / 4 |
| clipboard | `pbcopy`, `pbpaste` | 6 / 28 | 1 / 3 |
| secrets | `security *-generic-password`, `Keychain` | 0 / 0 | 1 / 1 |

Notable zeros: `shell=True` 0, `security find-generic-password` 0. AIB 1.x does not
keep secrets in the macOS login Keychain; see section 3.8.

Where the coupling sits (OSS): `aib/src/cockpit` 2553 hits (57 %), `aib/tests` 838,
`aib/tools` 456, `aib/i18n` 152 (user-facing strings that name tmux / launchd),
everything else under 70 each. **The coupling is concentrated in the cockpit layer.
Identity, memory, ledger, rotation and persona code only touch file locks and paths.**
That is the main reason a strangler migration is realistic: the domain logic that
AIB 2.0 wants to keep is mostly not tangled with tmux.

## 2. Reading the tables

Each group lists: **files** (top hitters, non-test), **function**, **coupling**,
**tests** (how many files under `aib/tests` reference the module by name; 0 means
none found there, it may still be exercised from `aib/tools` harnesses), **risk**
for AIB 2.0, and the **Windows replacement** that the Platform Adapter must offer.

Risk scale: **H** = blocks the Phase 1 gate or loses data if ported naively;
**M** = needed for parity, has a known cross-platform answer; **L** = dev tooling
or cosmetic, can stay macOS-only or be dropped.

## 3. Groups

### 3.1 Terminal multiplexer cockpit (tmux) — H

| Files | Function |
|---|---|
| `src/cockpit/aib` (83), `cockpit-full.sh` (154), `pane-hub-frame.sh` (79) | cabin = outer tmux session + inner `-L aihub-<inst>` server; seat = tmux window; launch path `aib <provider> <mode> <role> <cabin>` |
| `mouse/mouse-ctl.sh` (217), `nav/captains-ctl.sh` (46), `title/title-beat.sh` (30) | mouse bindings, navigation, title bar |
| `dm-ctl.sh` (41), `shot_helper.py` (49), `idle_reaper.py` (40) | DM delivery by typing into panes, screenshots, idle cabin reaping |
| `pane-right-tabs.sh`, `pane-right-ai.sh` | right column, polling (PROBE-B: ~100 spawns/s for 2 cabins × 6 seats) |

- **Coupling:** tmux is not just the UI; it is the process supervisor (`respawn-pane`),
  the identity source (`pane_pid` → seat), the message transport (`send-keys` into a
  seat's input) and the state store (`tmux set-option` user options per window).
- **Tests:** `cockpit-full` 5, `mouse-ctl` 6, `pane-right` 9; 61 test files mention tmux,
  14 of them start their own tmux server (`new-session` / `-L`).
- **Risk:** H. Four responsibilities share one mechanism. Porting tmux to Windows is not
  an option (no native tmux; WSL would break "native Windows").
- **AIB 2.0 replacement:** split the four jobs. Supervisor → `aibd` + Platform Adapter
  (process group / Job Object). Identity → `seat_id`/`session_id` assigned by `aibd`, not
  derived from a pane. Transport → Provider Adapter input channel (structured mode
  first, PTY/ConPTY fallback). State → SQLite. tmux remains an optional *client* during
  migration (spec 13.3 rule 5); `aibd` must not depend on it.

### 3.2 File locks — M

| Files | Function |
|---|---|
| `src/cockpit/dm_queue.py` (23), `chat_owner.py` (16), `codexbot_threads.py` (12) | DM queue rev/turn locks, single owner of the chat bridge |
| `pane-right-tabs.sh` (21), `cockpit-full.sh` (11) | `flock(1)` in shell around pane redraw and ensure |
| `src/ledger/ledger.py` (9), `src/fleet_identity/bind_attention.py` (9), `src/rotation`, `src/memory`, `src/collectors` | append-only ledgers and indexes |

- **Coupling:** `fcntl.flock` (advisory, whole file) in Python; `flock(1)` utility in shell.
  Neither exists on Windows.
- **Tests:** `dm_queue` 40, `ledger` 17, `chat_owner` 6. macOS CI runners have no
  `flock(1)` (known; tests skip by name).
- **Risk:** M. Correctness depends on them (DM rev races, ledger appends).
- **AIB 2.0 replacement:** most of these locks protect JSON files that become SQLite
  rows, where the transaction is the lock. The remaining cross-process need is the
  **single-instance lock for `aibd`** and the data-directory lock: use a cross-platform
  crate (`fs4`/`fd-lock`: `flock` on Unix, `LockFileEx` on Windows).

### 3.3 Process lifecycle — H

| Files | Function |
|---|---|
| `src/cockpit/memguard.py` (14) | kills a runaway interpreter (RSS) of the user's own process tree |
| `src/cockpit/seat_screen.py` (9), `usage_probe.py` (7), `usage_accounts.py` (6), `aib_dm_tool.py` (5) | spawn child with `start_new_session=True`, kill the group on timeout |
| `src/cockpit/auto_update.py` (7) | detached updater: `os.fork` + `setsid` |
| `tools/verify/runner.py` (14), `pushgate.py` (8), `tools/release/ship.py` (6) | test / push harness timeouts |

- **Coupling:** POSIX sessions and process groups (`setsid`, `killpg`, `SIGKILL`),
  `os.fork` (5 files). Windows has no `fork`, no signals beyond a few, no process groups
  in the POSIX sense.
- **Tests:** `memguard` 3, `auto_update` 6; harness code is self-tested through CI.
- **Risk:** H. Spec Phase 0 gate requires "cancel and clean up the process tree" on both
  OSes. A naive port leaks grandchildren on Windows.
- **AIB 2.0 replacement:** Platform Adapter `spawn_tree(cmd) -> TreeHandle` with
  `cancel(grace)` / `cleanup(grace)`. **On macOS a process group is only the first layer,
  not a full replacement:** a CLI's tool can start a child in its own group (measured
  with Codex: it survived `SIGTERM` to the CLI's group). The full strategy — group,
  recorded `(pid, start_time)` descendants, session marker, cancel sequence and its
  known limits — is in [ADR 0004](../adr/0004-process-tree-cancel-completion.md).
  Windows: assign to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and no
  breakaway before resuming, terminate the job. Detached updater becomes an `aibd` task
  or OS scheduler entry, never `fork`.

### 3.4 Process discovery — M

| Files | Function |
|---|---|
| `src/cockpit/seat-detect.py` (25) | maps a tmux pane to the AI CLI session id: `pgrep -P` walk, `ps eww` env scan, `lsof` on rollout files |
| `room_heat_release.py` (14), `codex_rotate_live.py` (11), `idle_reaper.py` (9), `room_heat_tidy.py` (5) | find leftovers, heat control |
| `proc_find.py` (9) | `pgrep`/`pkill` that excludes itself and its ancestors |

- **Coupling:** shelling out to `pgrep`, `ps`, `lsof`; reading other processes' environment
  (`ps eww`) is macOS/Linux-specific and is denied on Windows without debug rights.
- **Tests:** none found by module name in `aib/tests` for `seat-detect`, `idle_reaper`,
  `proc_find`.
- **Risk:** M. Session detection is the root of seat identity today.
- **AIB 2.0 replacement:** not needed for seats `aibd` launches: it holds the child handle
  and receives the provider session id from the Provider Adapter's structured output.
  For diagnostics only, use the `sysinfo` crate (process list, parent pid) behind the
  Platform Adapter. Do not port `ps eww` env scraping for seat identity; the only
  environment read AIB 2.0 keeps is the session-marker sweep used for cleanup (ADR 0004).

### 3.5 Terminal.app and AppleScript — M

| Files | Function |
|---|---|
| `mouse/mouse-ctl.sh` (14), `seat_attach.py` (10), `cabin_guard.py` (4) | open Terminal windows attached to a seat |
| `shot_helper.py` (7) | screenshots of the terminal |
| `touchid_approve.py` (6), `secret_ask.py` (3) | JXA calling `LocalAuthentication` for human approval; secret entry dialog |
| `bootstrap/nerd_font.py` (7), `identity/bindme.py` (6), `messaging/notify_adapter.py` (3) | font install prompt, bind prompt, desktop notification |

- **Coupling:** `osascript`/JXA and Terminal.app exist only on macOS.
- **Tests:** `touchid_approve` 11, `seat_attach` 1, `notify_adapter` 0 (it injects runners).
- **Risk:** M; the approval path is security-relevant (see 3.6).
- **AIB 2.0 replacement:** "open a terminal" disappears — the web workspace is the UI.
  Notifications → Platform Adapter `notify()` (macOS `UNUserNotificationCenter` via
  Tauri plugin; Windows toast). Dialogs → web UI or Tauri, never a script-built window.

### 3.6 Native helpers: Secure Enclave and Touch ID — H

| Files | Function |
|---|---|
| `src/cockpit/sehelper/aib-sehelper.swift`, `se_vault.py` (6), `tools/sehelper/install_anchored_helper.py` (3) | compile a Swift helper with `swiftc`; seal secrets with a Secure Enclave P-256 key, unseal behind `biometryCurrentSet` |
| `approve_grants.py`, `approve_enroll.py`, `anchored_helper.py` | time-window grants signed by an SE key; root-owned anchored helper path checked up to `/` |
| `shot_helper.py` (15 `swiftc`) | screenshot helper |

- **Coupling:** `swiftc` at runtime, CryptoKit, LocalAuthentication, root-owned paths.
- **Tests:** `se_vault` 4, `approve_grants` 4, `anchored_helper` 2.
- **Risk:** H. This is the only part of AIB 1.x that resists a same-user AI. Under the
  maintainers' same-user AI threat model, a file, hash or env var is not a defence; a
  biometric prompt, passphrase or signature is. AIB 2.0 Risk 3 approvals must keep this
  property.
- **AIB 2.0 replacement:** Platform Adapter `human_presence(prompt) -> Proof` and
  `hardware_key_{create,sign}`. macOS: keep the Swift helper, prebuilt and code-signed in
  the release (no `swiftc` on user machines). Windows: Windows Hello
  (`UserConsentVerifier`) + TPM-backed key (CNG `Microsoft Platform Crypto Provider`) —
  **UNVERIFIED-desktop**, needs a real machine.

### 3.7 Autostart and background jobs (launchd) — M

| Files | Function |
|---|---|
| `src/cockpit/auto_update.py` (19), `chat_bridge.py` (14), `cabin_restart.py` (11), `approve_grants.py` (9), `consent.py` (5), `room_heat_advice.py` (5) | write / load LaunchAgent plists for updater, chat bridge, restart snapshots, consent inbox |
| `tools/support/uninstall.py` (3) | remove plists |

- **Coupling:** `launchctl` and `~/Library/LaunchAgents`.
- **Tests:** `chat_bridge` 47, `cabin_restart` 4, `consent` 3.
- **Risk:** M. Silent stop of schedules has happened (backup schedule incidents).
- **AIB 2.0 replacement:** one autostart entry only — `aibd` itself (LaunchAgent on macOS,
  Task Scheduler "at logon" or `HKCU\...\Run` on Windows). Everything else becomes a
  scheduled task inside `aibd`, visible in its health endpoint.

### 3.8 Secrets — M

- AIB 1.x keeps secrets in its own state-root vault: `bootstrap/aib_secrets.py` +
  `secret_crypto.py` (XChaCha20-Poly1305, `wrap.key` 0600 next to the store), and for
  high-value tokens `se_vault.py` (Secure Enclave, Touch ID to unseal). No Keychain calls;
  `aib-sehelper.swift` explains why it avoids the data-protection keychain (it would
  need a `keychain-access-groups` entitlement).
- **Risk:** M. `wrap.key` next to the ciphertext does not stop a same-user process.
- **AIB 2.0 replacement:** Platform Adapter `secret_store` (macOS Keychain, Windows
  Credential Manager / DPAPI), plus the hardware-key path of 3.6 for Risk 3 secrets.
  Migration reads the old vault once, behind a human approval, and never writes back.

### 3.9 Paths — M

- 180 files reference `/tmp/`, `~/Library/` or expand `~` by hand. Many are tests and docs.
- State root is `~/.ez2-aib` (override `EZ2_AIB_STATE_ROOT`); Vault is `EZ2_VAULT_DIR`.
- **Risk:** M for runtime code (`activity_probe.py`, `usage_*`, `render/engine_badge_mode.py`
  read Claude/Codex home directories under `~/Library` or `~/.claude`).
- **AIB 2.0 replacement:** `aibd` data dir from the OS (`directories` crate:
  `~/Library/Application Support/<app>` / `%APPDATA%\<app>`), temp from `std::env::temp_dir`,
  provider home locations owned by each Provider Adapter. The old state root is read-only
  input to `aib migrate --dry-run`.

### 3.10 Shell strings — L

- `bash -c` / `sh -c` in 24 files, mostly test tracers (`tools/verify/shadow_tracer.py`,
  `bash-shim`), `install.sh`, `help-overlay.sh`. No `shell=True` in Python.
- **AIB 2.0 rule:** parameterised spawn only (`Command::new(prog).args(..)`); no shell in
  `aibd`. Windows quoting rules differ — never build one command string.

### 3.11 Loader injection and OS probes — L

- `LD_PRELOAD` / `DYLD_*`: test tracer (`tools/verify/ldp-shim`) and an environment scrub
  in `anchored_helper.py` / `se_vault.py`. Dev tooling; keep the scrub idea (clear
  `DYLD_*` before running privileged helpers).
- `pmset`, `sysctl`, `sw_vers` (22 files): power and thermal status, machine info for the
  right column and chat presence. Replacement: `sysinfo` crate behind the Platform
  Adapter; power/sleep state is UNVERIFIED on Windows.
- `pbcopy`/`pbpaste` (6 files): Platform Adapter `clipboard` or drop (web UI has the
  Clipboard API).

## 4. Private plugins (category level only)

`aib-plugins@origin/main` has 453 hits in 11 categories. The largest areas are the
captain-room server (file locks, process, paths, tmux client calls), the pending-review
pane (tmux), hostguard engine (process), pixel-studio engine (file locks, process) and
the test-guard plugin (process discovery, loader injection). One `Keychain` mention
exists. File-level rows are in the private repository.

Interfaces the plugins call in the core today (what the AIB 2.0 Plugin API must
replace): `aib` subcommands invoked as child processes, the state root (`~/.ez2-aib`)
read directly, tmux sockets for live cabins (`cabin_restart.collect`), the DM queue
(`dm_queue.py`), and the license check at the `aib` entry point. Spec 9.3 says plugins
must not read SQLite or secrets directly — every one of these becomes an authenticated
local API call.

## 5. What this means for Phase 1

1. `aibd` does not need tmux, `pgrep`, `lsof`, `osascript` or `launchctl` (except its own
   autostart) to pass the Phase 1 gate. Seat identity comes from `aibd`, not from panes.
2. The Platform Adapter surface for Phase 1 is small: `spawn_tree`/`terminate`,
   `data_dir`, `instance_lock`. Secrets, human presence, notifications and autostart
   come later and keep their trait signatures from day one.
3. The hard ports are 3.3 (process trees on Windows) and 3.6 (human presence on
   Windows). Both are UNVERIFIED-desktop until someone runs them on a Windows machine.
4. Most file locks disappear into SQLite transactions.

## 6. 待取代 / To be replaced — frozen 1.x features

From 2026-11-09, 1.x (Python) gets no new features: `aibd` replaces 1.x one function at a
time and the 1.x function is retired only after the replacement passes the gate on both
OSes (ADR 0001). A 1.x feature PR that was open on that day is turned into a draft, and its
requirements and tests are kept here so the replacement can be checked against them.
Each row is one frozen PR; the PR itself stays as a draft for reference.

| 1.x 功能 / 1.x function | 原 PR / Original PR | 需求 / Requirements | 現有測試 / Existing tests | 2.0 去處 / 2.0 home | 規格階段 / Spec phase |
|---|---|---|---|---|---|
| `aib prepr --md`:pre-PR checklist as a Markdown table for the PR description | #558 `ba24e519` (the PR template half is merged separately) | One row per item with its status and real exit code; the first line counts failed, not run and warnings, and never says only "no failures" when something did not run; says less than the plain output, never more (no output tail, version or command); no line breaks or bare pipes inside a cell; `--md` and `--json` cannot be asked together | `tests/test_aib_prepr.py` in #558: `test_every_item_is_a_row_with_its_status_and_real_rc`, `test_md_and_json_cannot_be_asked_together` | `aib-mergeq pr-body` (team tool crate) | not a product phase (team tool) |
| cooldown 收行程判準 / cooldown reap criteria | #473 `9a3812eb` | 收行程改看熱壓力或持續高溫(2 小時取樣平均 88°C);讀不到熱壓力退回瞬間值;每筆紀錄帶原因;多面板同時點只算一筆 + 鎖;壞歷史資料(非兩欄 / NaN)判壞;stderr 壞照收;dry-run 不清未完成紀錄;收一半可查 | `aib/tests/test-cooldown-reap-authority-34d15-1103.py` in #473: 30 cases + 12 knives | §16.4 執行中自我監控 + Platform Adapter 熱訊號 / §16.4 runtime self-monitoring + Platform Adapter thermal signal | Phase 4 |
| DM mid-turn hint | #548 (draft) | When a seat has ≥ 2 open DMs (waiting: no 3-minute grace; in-progress: read, no wait, not read by the system), tell the seat during its turn, not only at turn end. One hint per batch (key = sha256 of sorted `id@rev`), and ≥ 120 s between hints. Off by default, per seat. Never fires for the seat's own sub-agents. Any error ⇒ silent. | `aib/tests/test-dm-midturn-hint-043dd-1109.py` (7 cases, 7 knives) | `aibd` Message / Event; event-driven, so it also reaches non-Claude seats and quiet turns | Phase 1 |
| DM merge key (workflow 7c) | not opened (never pushed; this row is the whole record) | Two DMs that both name a PR and a head are grouped only when PR number and the first 7 hex chars of the head are equal; different heads never merge, even in the same thread or with near-identical text. A group carries its key, so A(head X)–B(no key)–C(head Y) cannot merge transitively. A head is 7–40 lowercase hex with at least one digit and one a–f, not adjacent to `[0-9A-Za-z_]` (DM ids, dates and words are not heads). A long DM is keyed from its stored body. | `aib/tests/test-dm-merge-key-043dd-1109.py` M1–M9, knives K1–K4 | `aibd` Message grouping; check against the #570 DM fixture | Phase 1 |
| DM `kind=notice` auto-close | not started | A notice (no reply or action asked) is closed once delivered and read, with the delivery as evidence. | none yet | `aibd` Message / outbox | Phase 1 |
| Gameworld switch result "unknown" | #491 (draft) | A cabin switch whose POST may have happened but was not confirmed (plugin says `post_unconfirmed`, core timeout, broken or empty plugin output) is recorded as *unknown*, not failure: no window, no success message, and the same request id is never retried automatically (`unknown_kept`). Markers are not clock-based and are never garbage-collected until an approved tombstone / ack interface exists (DECISION-16, DECISION-22). Request ids carry a time; > 12 h old or > 5 min in the future ⇒ `stale`. Only a spawn failure or an explicit `sent: false` clears the marker. User-facing text needs the maintainer's final wording (維護者定稿字). | `aib/tests/test-gameworld-043dd-1107.py` U11, U14, U17–U19, knives KG11–KG25 | Captain-room Scene (spec principle 5) | after Phase 3 |
| Machine lease short lane | #500 (draft) `70729a18` | Two lanes: `long` (today's lease, unchanged) and `short` (one holder at a time) for runs of 300 s or less that declare no browser and no fixed port; a run missing either declaration, estimating more than 300 s, or whose state root is the real `~/.ez2-aib` is refused. No short lease when the same tree is shipping (`same_tree_shipping`), long holds the same tree (`same_tree_long`), long opened Chrome and the Chrome limit is 1 (`long_chrome`), long's lease cannot be read (`long_unreadable`) or long is exclusive (`long_exclusive`); exclusive long is refused while short runs or short's lease cannot be read; both checks under one lock. Heat guard from the shared thresholds: throttle ≥ 40 %, CPU die ≥ 90 C or load1 > 12 stops short, and so does a throttle or load1 that cannot be measured (`throttle_unmeasured` / `load1_unmeasured`); short resumes only after 300 s of consecutive cool samples, a sample gap over 120 s restarts the count; a heat-state file that exists but cannot be read or written means no short (`heat_state_unreadable` / `heat_state_unwritable`). A run_id is "mine" only if pid and pid start time match too; another process with the same run_id is busy and cannot release my lease. The child runs in its own session at nice 10 with `AIB_LEASE_SHORT_RUN_ID`; past the declared time + 60 s: log `short_overrun`, TERM then KILL the whole group, rc 6. `aib test` under run-short skips the exclusive check only for a small batch whose run_id matches an ancestor holder; more than 30 files is refused (`aib.test.short_big_refused`, RC_EXCLUSIVE). `lease status --json` has a `short` field and every grant records the throttle %. Known gap for the Rust version: no test proves that release checks the pid. | `aib/tests/test-lease-two-lanes-1ced7-1108.py` on #500 (24 cases; 9 knives bit). The full changed-file selection last ran on `24d91c66`, not on the final head. | Team dev-tool crate `aib-lease` | not a product phase |
| Cooldown throttle thresholds | #506 (draft, stacked on #500) `cc1c18a7` | kernel_task throttle (%CPU, not temperature): < 20 ok; 20–39 *warm* (yellow cell, at most one `throttle_warm` log line per cabin per 10 min, blocks nothing); ≥ 40 *hot* (nogo flag, seat-open block, captain call). Reaping automation browsers stays on CPU die ≥ 90 C only; throttle never takes part in reap. One source for every heat threshold (cooldown, machine status, lease short lane), with a test that compares every reader. Readers of "hot": cooldown level → nogo flag and captain call; seat open (`seat_bootstrap` HEAT_BLOCK_LEVELS); status line and tab colours; short lane; reap. The ruler `kernel_task_nogo = 20` in `rulers.py` was left unchanged and needs its own decision. Kept separate, not frozen: cooldown on a team test Mac stops test Chrome when a single CPU-die sample is ≥ 90; a 1.x fix for that is its own bug-fix PR. | `aib/tests/test-cooldown-throttle-warm-1ced7-1108.py` on #506. Last run on `cc1c18a7` (a team test Mac, 245 files): 239 green, 2 red (one also red on main, one only under load), 4 not verified; another 145 green cells were skipped, and skipped is not verified. | AIB 2.0 spec §16.4 self-monitoring + Platform Adapter thermal signal | Phase 4 |
| Test temp-path normalisation to `<TMP>` | not opened (never pushed; this row is the whole record) | Before comparing output, replace both the abspath and the realpath form of every temp root (the given root, `tempfile`, `AIB_TEST_TMPDIR`, `TMPDIR`, `TEMP`, `TMP`, `/tmp`) with `<TMP>`, longest first. A root followed by a letter, digit, `.`, `_` or `-` is not replaced; only one followed by `/`, `\` or the end is. Case-insensitive on Windows. No global state. | `aib/tests/test-tmp-paths-043dd-1109.py` T1–T9; knives K1 (no realpath: a symlinked root, bites on Linux too), K2 (shorter root first), K3 (no boundary check) | `aib-fixture-tree` or a shared Rust test helper | not a product phase (with the Rust tests that need it) |
