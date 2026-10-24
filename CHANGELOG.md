[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

## 0.1.54

- **`aib room heat release` (new)**: release leftovers nobody uses. Two kinds for now: orphaned test scripts under a temp folder, and orphaned automation Chrome with a temp profile, both older than 120 seconds. Candidates are listed by the snapshot (`candidates` / `held`); release re-checks the owner and start time before every signal, kills only the listed process, and keeps anything holding an ssh / scp / rsync child or a `*.lock` file under `held`. `--dry-run` shows what would happen; `--auto` is capped per 10 minutes and stops if it cannot keep its log.
- **`aib memguard` and a per-test memory cap**: every test run by `aib test` gets a memory cap for its whole process tree (`AIB_TEST_MEM_CAP_MB`, default 2048, 0 turns it off); over the cap the tree is stopped with exit code 137 and `memory_cap`. A machine-level guard (`aib memguard scan|watch|log`) watches only your own interpreter processes and never touches seats, Codex, Claude, browsers or apps. Pressing Ctrl-C, closing the terminal or stopping `aib test` now also stops the test that was running instead of leaving it behind.
- **Fewer disk writes while testing**: the `aib test` trace hook keeps one append-only file open per process instead of opening and closing the trace file for every record (on APFS this cut write amplification from about 76× to 1×).
- **Lighter right panel**: the captain list syncs only when seats change or every 30 seconds, and window switches are picked up through a tmux hook instead of asking tmux every tick (about 230 → 33 tmux calls in 20 seconds).

## 0.1.53

- **`aib approve` (new)**: approve a WordPress sign-in with Touch ID on your Mac. `aib approve key init` creates a Secure Enclave signing key on this computer; `site add` registers a site; `pending` / `yes <nonce>` / `no <nonce>` / `history` list and answer requests. Every approval asks for Touch ID and is signed by the Secure Enclave. The WordPress side is a separate plugin.
- **`aib room heat` (new, read-only)**: a snapshot of load, memory, swap and temperature, processes grouped by who owns them (seats, `aib test`, headless browsers, sandboxes, system, other apps), one hour of history (`history`), and the system crash reports grouped by cause (`crashes`). Anything that cannot be measured is reported as `unmeasured`, never as 0. Output never includes command lines, working folders or full file paths. Releasing leftover processes comes in a later version.
- **Wrap-up always writes back**: when the human's own words cannot be read the usual way, the handoff falls back to the conversation file (human-typed lines only) instead of skipping; `aib summary write` stores the summary as a second-brain note; `verify` no longer accepts a skipped summary or persona step.
- **`aib test --changed` works in the open-source tree**: the consumer-scope rules are now included (no more `ModuleNotFoundError`); a subproject inside the repo is selected from the subproject and from the repo root, and anything changed outside it is reported as unmeasured instead of silently passing.
- **`aib memory write` with several seats at once**: numbering, writing and indexing now happen under one lock, so two seats writing at the same moment can no longer get the same note number.
- **Empty prompt hint is not a draft**: when the cursor sits on the first letter of the input box's placeholder hint, the box is still treated as empty, so queued messages are delivered instead of waiting for someone to type.

## 0.1.52

- **`aib test` no longer crashes Python 3.15 on macOS**: the network audit hook no longer duplicates old socket file descriptors (a reused descriptor could be a protected one, and duplicating it made the system kill the test process). It now keeps weak references to the sockets it bound and asks them directly.
- **Status line context reading**: the aib-boards status line now refreshes the context figure when a turn ends, not only on its 60-second timer, so a newly opened seat no longer shows "context not measured" for its first minute.
- **`aib secret ask` names**: a secret name must match `[A-Z][A-Z0-9_]{0,127}` before anything else runs (otherwise `bad_name`). The mail-sending credentials can only be pushed straight to the server (`--remote-only`); no copy stays on this computer.
- **Power room hardening**: sign-in links are still returned when the wait runs out; the sign-in log is removed before it is created again (exclusive create) and always cleaned up afterwards; more edge-case tests.
- **Online chat after a persona switch**: each seat now sends and lists exactly one key (the one for the persona it is bound to now), so other computers no longer reject the roster as having duplicate seats. When a peer's seat snapshot is stale, the roster is used instead. A malformed seat display file is treated as "not bound" instead of stopping the chat service.

## 0.1.51

- **Right-click menu, more hardening**: clicking [X] on a seat row now checks the same things as "Close this seat" in the right-click menu (the cell exists and the window id is a tmux id); otherwise the click does nothing. If the menu's 3-second time limit cannot be cleared, the timer is made harmless so the menu still prints; every other path puts the previous handler back.
- **Online chat keys follow your state folder**: chat keys and the key map now live under the same root as aib's other secrets (`EZ2_AIB_STATE_ROOT`, then `EZ2_AIB_HOME`, then `~/.ez2-aib`); with none of these set nothing moves. The background chat bridge started at login now gets the same path settings. If the new root has no identity but `~/.ez2-aib` still has one, aib stops with `LegacyKeysElsewhere` instead of quietly making a new identity: either set `EZ2_AIB_SECRETS_DIR` and `AIB_NOSTR_MAP` back to the old location to keep it, or put an empty key map in the new root to start a new identity on purpose.

## 0.1.50

- **Captain's power room (`aib power`)**: a first stop for new captains. `aib power status` shows versions, health, bindings and a light for each AI (signed in / quota); `aib power login <ai>` opens that AI's own sign-in link; `aib power upgrade <ai>` updates its CLI. `aib ui power` opens a simple static page with the same data — its buttons only copy commands for you to paste, and no local server is started.
- **Launch deck data**: `aib power launch --list` and `aib power fav` list personas, favourites and the last 20 launches; `aib power launch <ai> --dry-run` prints the commands that would open a seat and bind a persona and second brain, without running them.
- **Starter task board**: R / SR / SSR / UR tasks that tick themselves from what you actually do on this machine (open a seat, bind a persona, send a seat message, hand off…); `aib power tier` turns a level on or off. SSR and UR open after your first launch.
- **Ask AI (`aib power ask`)**: asks an AI a question about aib with every tool and MCP server turned off, in a read-only sandbox — it only answers, it never acts.
- **Why AIB (`aib power why`)**: pick and rank up to 5 reasons you use aib; kept on this machine for now.
- **Support bundle (`aib support`)**: collects versions, `doctor` results and failure codes into one local file you can choose to send; it never includes log text, conversations or keys, and never sends anything by itself.

## 0.1.49

- **Seat titles in the right-click menu drop invisible characters**: right-to-left overrides and zero-width characters are removed before a title is drawn, so a name can no longer be shown reversed or made to look like another seat; emoji are kept.
- **"Close this seat" checks more before it offers itself**: the row's close cell has to be a real range and the window id has to look like a tmux window, otherwise the item is not shown; the menu's column value only accepts 1–4 digits.
- **The right-click menu waits at most 3 seconds for its readiness check**: if one provider's settings are slow to read, the menu still opens and that seat explains the problem itself when opened; saving a key is not affected by the limit.

## 0.1.48

- **Right-click "Close this seat"**: the seat list's right-click menu can now close the seat under the pointer; it checks that the window is still the one you clicked before closing, and does nothing if it has changed.
- **Every provider's "skip approvals" mode is now pinned by a test**: Claude, Codex, agy, Copilot and Grok each start with their own flag in dgrs mode (and none in safe mode) across new, resumed, forked and relaunched seats; API-key and bot seats are listed as having no such flag.
- **OpenRouter / TypeSafe seats start when a model is given and effort is turned off**: previously they stopped at once with an internal variable error.

## 0.1.47

- **Opening a folder in agy / Grok no longer stalls on the trust question**: the folder trust prompt is answered for the folder you opened (never for `/`, your home folder or its parent), and only the menu itself is sent to the small model that picks the answer.
- **The right-click "New AI" menu says what is missing before you open**: providers billed by API key show "missing key — click to add" and open `aib secret ask` (masked box + Touch ID) instead of a window that dies at once; bots that are not connected yet say which step is missing.
- **A new seat's onboarding message is marked read once the seat replies "ready"**, so the status bar no longer shows a stale unread DM.
- **Usage probes clean up after themselves**: temporary probe folders are removed from the agy / Grok trusted-folder lists.

## 0.1.46

- **Opening another AI from the right-click menu works again**: one-shot resume flags (`AIB_REVIVE_SESSION_UUID` and friends) no longer leak into the tmux server, so a new seat is not mistaken for a resume of an old conversation.
- **The (x) soft-close entry actually closes the seat**: confirming (x) now re-checks only whether the seat moved while the menu was open (and that its CPU is idle), instead of the 300-second rule meant for [X]. [X] is unchanged.
- **`aib plugins license` keeps add-ons you already have**: if the licence installed on this machine covers more add-ons than the one fetched, it is no longer overwritten.
- **Touch ID approval text filters more invisible characters**: C1 control characters, U+00AD and U+061C are removed before the text is shown.

## 0.1.45

- **New `aib plugins install <name>[@version]`, `aib plugins catalog`, `aib plugins license <addon>`**: members can get add-ons straight from the ez2.page catalog. aib checks the size and sha256 the catalog lists, unpacks with limits (archive bombs, links and odd paths are refused), and installs only if the package's own signature checks out; nothing unsigned is ever loaded. Paid add-ons need this machine to be bound to ez2.page and the account to own the add-on. A newer installed version is not downgraded unless you name the version. `plugins license` fetches the licence your account owns and installs it the same way as `aib license install`. Results use the contract codes in `contracts/page-api.json` (`plugins.*`); `--json` gives the same receipt.
- **Seat panel opens large transcripts much faster**: the seat panel and its pending check now read only the end of the transcript, going further back only when needed (a 243 MB transcript went from about 2 s to 0.2 s).

## 0.1.44

- **New `aib create submit / status / list` (submission service not open yet)**: send a finished creation package (a `.tar` made by an add-on or skill) for review, then check its status or list what you have sent. aib checks the package locally first (paths, file types, manifest hashes, size of each member), asks for one Touch ID before sending, and prints the server's review state. Until the submission service opens, `submit` answers `creator_unsupported` and nothing is stored. Results use the contract codes in `contracts/page-api.json` (`creator.*`); `--json` gives the same receipt.

## 0.1.43

- **`aib` no longer opens a browser when the Captain Room add-on is too old**: `aib` (room by default) always starts the room with `aib room open --no-browser`. If the installed add-on is too old to support that (0.1.33 and earlier), aib does not start the server or open any window; it answers `room-too-old`, tells you to update the add-on, and falls back to the terminal help.
- **Clearer EZP- serial message after a lost-device revoke**: when ez2.page reports `serial_rotated` even though every token was already revoked or expired, aib now says the serial has been replaced instead of asking you to reissue it from the dashboard.
- **`aib test` in the add-on repository keeps `AIB_CORE_DIR`**: when testing the add-on repository, `aib test` passes `AIB_CORE_DIR` through (or points it at the running core if unset), so add-on tests use the right core. The core repository and other `AIB_*` variables are unchanged.

## 0.1.42

- **A revoked machine says when and by whom**: when ez2.page refuses a machine because it was revoked, aib now prints the time and the name of the device that revoked it, plus where to manage devices, instead of a bare credential error.
- **Lost-device revoke can replace the EZP- serial**: if ez2.page reports `serial_rotated`, `aib device lost` says the account's EZP- serial has been replaced, the old one can no longer bind, other machines keep working, and new machines need the new serial from the web dashboard. If the server does not rotate it, aib reminds you that the serial may still be in that machine's terminal history and to reissue it. If rotation fails, nothing is revoked and aib says it is safe to run again. `aib page bind` with a replaced serial explains where to get the new one. The new serial is never returned by the API or printed by aib.

## 0.1.41

- **New `aib device list` / `aib device lost`**: when a computer or phone is lost, revoke it from a computer you still have. It lists the target's name and last-seen date first, then asks for one Touch ID (the dialog text names the device), then removes it from the owner-signed chat device list (owner passphrase still required) and revokes every ez2.page token of that machine at once, reporting each step and each token separately. It never revokes the machine you are on, and it cannot be started from the phone remote. Until ez2.page offers the endpoint, the page step reports `page_device_revoke_unsupported` and points to the web dashboard. With `--json`, the chat step answers `chat_needs_terminal` without changing anything.
- **Contract file covers device revoke**: `GET /api/v1/page/machines` and `POST /api/v1/page/machines/{fp}/revoke` are in `aib/contracts/page-api.json`, including error codes, the per-account rate limit, the Email + LINE notice, and the threat-model note.
- **`aib chat off` exits cleanly every time**: if the chat bridge was already finishing when `off` arrived, it could end with a kill signal instead of a normal exit. It now ignores late stop signals once it has decided to exit.

## 0.1.40

- **Screen text is loaded once**: translation files are re-read only when they change (checked by file identity and modification time), with the safety checks still run every time; `aib chat --help` goes from about 1.2 s to 0.1 s with identical output.
- **Contract file covers the ez2.page feature list**: `GET /api/v1/page/features` is now in `aib/contracts/page-api.json`, and tests compare the requests core actually sends against it.

## 0.1.39

- **Remove only this Mac's credential**: `aib page unbind --local-only` deletes the local copy without contacting the server or asking for Touch ID, and reminds you to revoke the server copy on the ez2.page dashboard. If this Mac was bound but its machine identity file is gone, `aib page bind` now refuses instead of quietly creating a new identity.
- **Is the remote page live?**: `aib seat remote status --json` now has `remote_ready` / `remote_why`, checking in order whether this Mac is bound, the local link is running, and the server has the feature turned on. It only decides what the page shows.
- **`aib doctor` lists ez2.page features**: it shares the same 60-second cache as above. No answer or no connection is a note, not a failure; an unbound Mac makes no network call.
- **Turning on BB Call phone forwarding needs you**: turning it on, or shortening the wait / raising the hourly limit, asks for Touch ID; turning it off or checking status does not.
- **No invisible characters in source**: a new check requires zero-width and direction-control characters to be written as escapes (for example `\u200b`).
- **Core ↔ ez2.page contract file**: `aib/contracts/page-api.json`; tests compare the requests actually sent against it.

## 0.1.38

- **Signed quotes of your own words**: `aib quote key init` creates a signing key in this Mac's Secure Enclave; `aib quote add "<words>"` asks for Touch ID every time, and anyone can verify the result with the public key alone (`aib quote verify`). A quote is limited to 200 characters so the fingerprint window can show all of it; longer text is refused, never cut. The trust root is a key certificate signed by the ez2.page server; until that side is live, results are marked as "root not verified".
- **Unbind this Mac**: `aib page unbind` asks for Touch ID, asks ez2.page to revoke this Mac's credential, and only then removes the local copy (atomic; safe to rerun if interrupted). If the server does not support it yet, it says so, links to the dashboard and leaves the local copy alone. `--json` is for the captain room card.
- **Forward long-waiting mail to your phone (off by default)**: after `aib chat phone-forward on`, mail waiting for you for too long sends one line, "N new messages in your captain room", to the phones you signed; no content, sender or seat code, and capped per hour.
- **Temporary host changes get an expiry date**: `aib temp add/list/check/done` records when a temporary change should be undone and how; overdue items show up in `aib doctor` and `aib temp check`.
- **Fix**: the dm latency check in `aib doctor` always measures the store doctor was given, instead of one picked up from an outside environment variable.

## 0.1.37

- **One-tap phone binding, core side**: `aib page bind --start / --poll / --confirm / --cancel --json` lets the captain room run the whole pairing. A two-digit confirmation number is shown only on screen (never written to a file, a log or the QR); polling never asks for Touch ID, only `--confirm` does. `--cancel` after the phone confirmed returns the page where the credential can be removed. `aib page remote status` now includes the page slug and days left on the binding.
- **Binding hints point to the right page**: the "remove the credential" hint uses the server's `confirm_url` (https only, otherwise ez2.page/manage/aib). Falling back to binding without machine id now happens only for servers that really do not know it.
- **Opening your captain room needs you**: `aib chat open`, `visit accept`, and changing `space` / `share` levels ask for Touch ID every time and refuse when called from an AI seat. Reading settings, sending a visit request and declining do not ask.
- **Captain room reloads itself after updates**: after `aib update` or installing the captain-room plugin, a running captain room is told to switch to the new code (only when the version actually changed; local http only).
- **Receipts say whether the person has seen it**: messages waiting for the person are kept 7 days, and the sender gets a daily "still waiting (day N)"; the sender sees "read by the person" separately from an AI having read it; held messages carry a short reason code.
- **New helper commands**: `aib seat caps` (which features this core has, for the local page only), `aib chrome orphans` (automation Chrome left running and which seat started it; lists only, never kills), `aib summary template` (a minimal report input that passes the format check).
- **dm queue is easier to use**: `read` / `done` work out the seat by themselves, suggest a complete command to copy when something is missing, and accept a PR link as evidence; scheduled sends find Homebrew's tmux.
- **Cloudflare grants**: `aib cf grant` can grant redirect rules (zone read/write) and read-only bulk redirect and rule lists.

## 0.1.36

- **Pairing-code binding knows this Mac**: `aib page bind` (pairing code) now sends this machine's id, like `--serial` does, so phone remote recognises pages bound by code. If the server says the credential belongs to a different machine, nothing is saved. Older servers that do not accept it still bind as before.
- **Phone remote replies stay within the relay limit**: replies are trimmed to 96 KB (oldest conversation lines dropped first); if still too large the phone is told `result_too_large`. A busy relay (429 / 5xx) gets one retry, and every command id is logged for later checking.
- **Docs**: rollout guide for `aib cf grant` (docs/CF-GRANT.*).

## 0.1.35

- **Cloudflare changes need Touch ID every time**: `aib cf grant <zone> <perm>[,…] --push HOST:/abs/path [--ttl 10m]` opens a short-lived token limited to one zone, whitelisted permissions and the destination's IPs, writes it to the destination over ssh stdin, and deletes it again if delivery fails. The minting key is sealed by this Mac's Secure Enclave (biometrics required on every use, enforced by the system); it is never stored in the secrets file. `aib cf list` / `aib cf revoke <id>`. The key cabinet gains a Cloudflare category; `aib secret ask` reports `no_gui` when it cannot show a window.
- **Phone answers cover the options too**: the question fingerprint in an answer ticket now includes every option (key and label), so the options cannot be swapped on the phone; a question too long to show in full can only be answered on the computer.
- **Phone commands only use phone-opened windows**: typing, stopping and reading a seat from the phone only work in a window opened by a server-signed ticket, not one you opened on the Mac with Touch ID.

## 0.1.34

- **Command seats from your phone (off by default, not live yet)**: `aib seat remote on` (Touch ID) turns on the switch and starts `aib seat remote link` in the background, which only connects out to ez2.page and long-polls for commands; `off` stops both. Unlock needs a server-signed ticket; typing, stopping and reading a seat only work while that window is open; every answer needs its own ticket bound to the question, the choice and your typed text, and the core checks the question against the real one on screen. The trusted key list is still empty, so every ticket is refused until the maintainer approves the production key.
- **Phone seat list and transcript are slimmed before leaving the machine**: seat code / role / provider / time left only; no paths, project names, tool input/output or thinking.
- **Signing-in with the management key cannot sign tickets** (refused by default on this machine too).

## 0.1.33

- **Unlock stays open while you keep working**: after Touch ID, the no-reprompt window for typing into a seat now counts down only while you are idle; every command you send restarts the countdown. Choose the minutes when unlocking (`aib seat input --unlock-min N`, 0–60); `aib seat unlock <seat> [--min N]` unlocks without sending, `aib seat lock <seat>` locks at once, `aib seat unlocked <seat>` shows what is left. The window is still a local file and only guards against mistakes; answer / stop still verify every time.
- **Phone unlock tickets (off by default)**: `aib seat unlock <seat> --grant <file|->` accepts an unlock ticket signed by the ez2.page server after it verified you on your phone (passkey, LINE + password, or password). Checks: pinned public key list (empty for now ⇒ every ticket is refused), signature, this machine and bound account, seat, ≤ 5-minute lifetime, unused nonce. `aib seat remote on` (Touch ID) / `off` / `status` is the master switch.
- **Chat bridge can start at login without opening a cabin**: `aib chat install-boot` installs a LaunchAgent (macOS, explicit opt-in), `aib chat uninstall-boot` removes it; it shares the existing single-instance lock with the cabin's bridge and refuses to install from a development worktree.
- **Public seats come back after reopening the cabin**: being public now follows the persona, so a reopened seat with the same persona is public again without redoing it.
- **Screenshot editor opens on the display you captured**: on an extended display it opens and centers on the screen under the cursor and uses that screen's scale.

## 0.1.32

- **Open your captain's room to a friend in one step**: `aib chat open <friend>` sets both share levels (presence + room interior) and tells the other side; `aib chat visit <friend>` sends a visit request, which the owner accepts or rejects with `aib chat visit accept|reject`; `aib chat visits` lists who is waiting. No more two CLI commands on two machines, no npub pasting.
- **Send mail by friend name**: `aib chat send` and the captain's room accept a friend's or one of your machines' nickname (case-insensitive) and resolve it to the global address; a non-friend or blocked name returns unknown_contact, a malformed address still returns bad_address. `aib chat address <friend>` shows the address.
- **Mail for you that lands in the review box raises an event**: readable with `aib chat events` (for BB Call / badges) and visible in the chat panel; the sender's receipt gains a hint that you will see it in the room inbox (codes unchanged).
- **Friend snapshots carry a last-updated time**: when the other side built it, when you received it, and how many seconds ago, so the page can show "updating / last updated".
- **Handoff adds a vault check line**: read-only, numbers only, 20-second cap; skipped when no vault, not a uid vault, or not licensed.

## 0.1.31

- **`aib` with no arguments opens the captain's room**: in an app window (Chrome / Edge / Brave app mode with its own profile, no address bar; without any of them it falls back to the default browser and says so); when the plugin is missing or will not start it prints the usage as before. `aib ui room` / `aib ui terminal` switch and are remembered. When the plugin supports `--no-browser`, starting the server no longer opens an extra browser tab.
- **Chat panel: seat busy state detected correctly**: a non-breaking space after the prompt, blank lines at the bottom of the screen, and a draft in the prompt (someone typing) no longer confuse it; nothing is typed in while a draft is present.
- **Transcript long polling**: `aib seat transcript --follow --wait N` (up to 60 s) waits from the last position and returns only when there is a new event; it checks the file size instead of re-reading; when the seat switched conversations or the file shrank it reads from the start and flags a reset.

## 0.1.30

- **Core commands for the captain's room chat panel (phase 1)**, all under `aib seat …`:
  - `aib seat transcript <seat>`: reads that seat's conversation log (Claude Code's transcript file) as a list of events (what you said, what the AI said, tool calls and results, option questions, system events); long relayed messages are expanded; only files under the state root are read; big files are read incrementally by offset.
  - `aib seat pending <seat>`: whether that seat is waiting for your answer (option questions from the log, permission prompts from the screen).
  - `aib seat input <seat> --text …`: types text into that seat's prompt. Needs Touch ID; sends nothing while the seat is waiting for an answer or is not at its prompt; control characters, empty text and more than 64 KB are refused. The 10-minute re-check window only guards against your own slips; it is not a security boundary.
  - `aib seat answer <seat> <option>` and `aib seat stop <seat>`: answer a question, or stop (Esc / Ctrl-C). Touch ID every time; an answer carries the question id and is dropped if the question changed.
  - `aib seat attach <seat>`: opens a new Terminal window attached to that seat.
  - All of these refuse calls made from inside a seat window (so an AI seat cannot drive another seat); every action is logged.

## 0.1.29

- **`aib vault title <path…>`**: turns vault paths (folder and note ids) into readable title paths with an Obsidian open link; reads only the title index, never note bodies, and gives no title for paths outside the vault. Used by the captain's room chat panel; no add-on license needed.
- **Alarm on repeated receive failures**: when the receiving connection drops right away three times in a row, `aib chat status` prints a warning and records it in the status file; a session that lasts 60 seconds counts as normal. One of two machines once went 20 hours without receiving mail and nobody noticed.
- **`aib chat restart`, and the bridge restarts itself after an update**: only the process recorded in the status file whose command line really is the bridge gets the stop signal, then a 10-second wait and a respawn if its window died; a successful `aib update` restarts the bridge with the upgraded code instead of leaving the old code running.
- **Status reports power**: `aib chat status` adds a line with the adapter wattage and battery level; plugged in but not charging (a weak adapter) shows ⚠, and running on battery is stated too. `--json` adds a power object; a friend's online status also carries charging / level / wattage when the other side sends it (older versions are unaffected). The captain's room battery warning reads this.

## 0.1.28

- **`aib plugins install <folder | .tgz | .zip | https://…>`**: one command to install a plugin. It checks the trusted-key signature, every file hash and the required core version before anything lands in the plugins folder; archives with paths that climb up, links, or more than 64 MB are refused. Installing again replaces (same command to upgrade). It also tells you whether the plugin's license is active.
- **`aib license install <file>`**: puts a signed license file in place (mode 0600); the old one is backed up and only the two newest backups are kept. A bad signature or an expired file leaves the old one untouched.
- **Plugins find the core**: running a plugin now always passes the location of the aib that is running (AIB_CORE_DIR, EZ2_AIB_ROOT), so plugins started from a source checkout, over ssh or by a boot item no longer show an empty seat list because they could not find the cockpit modules.
- **`aib vault mobile-license`**: copies a signed, non-device-bound license into the vault's `.ez2/license.json` so the Obsidian title plugin on a phone can verify it; `--remove` takes it out.

## 0.1.27

- **Friend visit snapshots can carry travel collections**: how many souvenirs, how many stamp sets, which ones are on display (up to 6), and which continents are complete; only souvenir IDs and numbers, no seats, dates, or places visited. Checked when sending and again when receiving; anything invalid drops just this field.
- The phone-pairing network test waits longer, so it no longer fails now and then on a busy machine (test only).

## 0.1.26

- **Online receiving no longer breaks because the dedup record grows too large**: the dedup record now stores short keys and keeps at most 1,500 entries; old files are converted the first time they are read. Before, the record could pass 64 KB within a week, fail to save, and keep the receiver reconnecting.
- **Held outside messages no longer show the sender's self-chosen name to AI**: for Telegram and unknown aib seats still waiting for owner review, the command line and AI now see the sender as "External Telegram #tag" / "External aib seat #tag"; the tag stays the same for the same sender.
- **Friend visit snapshots can carry collection titles**: only 7 fixed titles are allowed; anything else drops just the titles.

## 0.1.25

- **Replies to a message you sent yourself from the captain room stay in the captain room**: when an online reply points to a message the owner sent in person (Touch ID), it is held for the owner and is not handed to the AI box. The owner can release it to the AI.
- **Telegram, step one**: `aib chat telegram on --seat` (Touch ID) / `off` / `status` / `check` / `allow` / `block` / `send`. Private text messages to your bot go to the seat's queue and are shown only in the captain room (never the AI box); you reply from the captain room. The bot token is stored with `aib secret ask TELEGRAM_BOT_TOKEN` and is never printed. Plainly: the token is on this computer, so an AI running as the same user can read it, and Telegram bot chats are not end-to-end encrypted.
- **`aib chat recent --json [--minutes N]`**: recent online / Telegram private messages sent and received (time, direction, network, contact, result) for the captain room's mail view. No message content.
- **Friend visit snapshots carry a few more fields**: which layout item each public seat is using, which public seats are in a meeting, which area a seat is in (only areas without a shard number), and a short summary of collected decor. Each field is checked on the sending side and again on the receiving side; anything that does not fit is dropped.
- **`aib vault newpost --meta-json` / `--appendable` and `aib vault append`**: plugins can add allow-listed frontmatter fields (core fields such as uid / routeKey / created cannot be set; privacy_level can only be raised; control, direction and zero-width characters are removed; a wrong type rejects the whole call) and append to a note created with `--appendable` under the same write route (64 KB per append). Writes to the vault now take a lock so newpost / append / mkdir / doctor --fix run one at a time.

## 0.1.24

- **Pair your phone as the owner's phone**: `aib chat pair start` (Touch ID) shows a QR code; after the phone sends back the one-time code (10 characters, 5 minutes), a second Touch ID window shows the phone's npub before it is added. Up to 3 phones; `aib chat pair revoke <npub prefix|all>` (Touch ID). Messages from the phone that start with `指令:`, `!` or `gm ` are instructions; everything else is discussion (reply only). Security settings (pairing, devices, owner key, wake, friends, sharing, Touch ID / PIN, secret store, plugins, license) still need Touch ID on the computer. Replayed or out-of-time messages are rejected.
- **Online messages reach the AI box without a Plus licence**: incoming messages from other people's seats are delivered on the basic plan too (other queue features still need Plus).
- **Messages with an unknown type are no longer put in the AI box** (they are logged and not receipted).
- **Who wrote it**: the message frame now shows what the sender says about the author (the owner / drafted by AI and approved / sent automatically by AI), and says plainly that the receiver cannot verify it.
- **World lists the live cabin first**: when one owner has several cabins, the fresh, most recently seen one comes first, so a captain room that reads only the first entry no longer shows an online friend as offline.
- **`aib newproj --approve` shows what changed**: approving records a hash of the goals and red lines; before you confirm, it lists added / removed goals and whether the red lines are the same as last time. The records are not signed, and the screen says so.

## 0.1.23

- **Owner key protected by a passphrase**: `aib chat owner protect` encrypts your owner key at rest (passphrase of at least 12 characters, read only from the terminal; on macOS Touch ID first). Changing your device list then always needs you: the base is the signed list fetched from the relays (an older or missing one is shown in full with a warning), and the changes (+added / −removed, with fingerprints) are shown before you approve. Wrong passphrases lock with growing delays. Restoring an old backup no longer writes the plain key back.
- **Pair another machine**: `aib chat join` on the new machine shows a pairing code; `aib chat devices add <code>` on the owner machine checks the fingerprint, then you approve. `devices revoke` / `devices extend` sign a new list.
- **See whether the other computer is asleep**: contacts and `world` show awake / asleep / offline; sends to a sleeping machine say so; on mains power the relay keeps the Mac awake (`aib chat awake on|off`); messages without a receipt after 24 hours are marked unconfirmed.
- **Friends see each seat's outfit** on your island (the captain room's look code, checked again by the receiver).
- **Right column**: a click during a redraw now waits for the list to finish drawing instead of being lost; on macOS the bottom of the list reminds you where to turn mouse reporting back on (Terminal: View → Allow Mouse Reporting).
- **`aib test` no longer flags the repository under test** as touching the real home folder when worktrees live under `~/.ez2-aib` (regression in 0.1.22).

## 0.1.22

- **Paste API keys into a masked box**: `aib secret ask <NAME>` opens a masked input box, checks the format (and with `--check`, asks the provider whether the key works), then needs Touch ID before the key is stored encrypted. `--push <host>:<path>` also sends it to a server over ssh; the Touch ID prompt shows the real host and port that ssh will connect to, and that connection is pinned (no proxy). The key never appears in arguments, output or logs.
- **The chat relay restarts itself and reports its health**: if the relay stops while online chat is on, it starts again after 5 seconds. `aib chat status --json` adds `healthy` and `why`, and aib reminds you to back up your chat key.
- **One owner, several machines (part 1)**: aib can verify an owner-signed device list and merge each listed machine's roster when looking someone up; mail from a removed machine no longer verifies. Old rosters without a device list work as before.
- **Contacts show the other side's aib version** and which features it cannot receive yet; with several machines it uses the version of the machine that seat lives on.
- **Contacts follow roster changes**: seats removed from a friend's roster drop out of your contacts, so messages no longer go to a closed seat; `aib chat contacts --refresh <npub|all>` fetches again right away.
- **Approved project cards can get goals later**: `aib newproj --propose-goals <uid> --goal …` writes a proposal only; the goals take effect after the human runs `aib newproj --approve <uid>`. Red lines and human rulings stay as approved, and control or invisible characters are stripped from the goals.
- **`aib test` refuses to touch the real home folder**: a test that reads or writes `~/.ez2-aib`, `~/.claude`, `~/.ssh` and similar places is marked red with the paths it touched.
- The screenshot editor drops its old copy-to-clipboard icon; "Clipboard only" next to Save remains.

## 0.1.21

- **Send online messages as the human from the captain room**: `aib chat send <address> --as-human --cabin <cabin>` (text on stdin). On macOS every send needs Touch ID (or the login password); the prompt text is built by aib ("send to <who>: <first 40 chars>"), one prompt at a time, and there is no way to skip it — if Touch ID is off or unavailable the send is refused. Other platforms use the captain-room PIN (pbkdf2-hashed, at least 6 digits, lockout that doubles after each miss up to 1 hour); the PIN only guards against mistakes, not against other programs running as you.
- **Screenshots in a uid vault go to the second brain's gallery**: the screenshot helper finds the gallery by route (`storage.gallery`), creates a "Screenshots" subfolder when missing, and only falls back to the Desktop (with the reason in `status`) when there is no gallery. The screenshot editor can also save to the clipboard only.
- **Friends see your island's sky** (season, weather, time of day, festival, realm); only these keys travel, each a short lowercase name. Values that end with a newline are no longer accepted anywhere in island snapshots.
- **Handoff lists stuck messages**: `aib handoff collect` shows messages from the last 48 hours that never reached the other seat's input box (held because of a draft), both sent and received, so the next session knows what to re-send.

## 0.1.20

- **`aib vault doctor --fix` keeps persona card folder titles**: 0.1.19 dropped the titles of non-`fld_` folders (persona card folders) when rewriting indexes, so card references fell back to raw uids. Retired `fld_` folders are still removed. Do not run `doctor --fix` on 0.1.19.

## 0.1.19

- **Cleaner installs and upgrades**: a copy that is 5 or more minor versions behind is backed up and installed fresh; the installer ends with commands you can copy to open your first seat; PATH is no longer added twice to your shell profile.
- **Persona card list refreshes after an upgrade**: `aib update` / `install.sh` rebuild `cards.json` when the program version changes, and `aib doctor` rebuilds one made by an older aib (cards you edited by hand or a changed persona library are reported, never overwritten).
- **Clearer command hints**: `aib dm --help` prints its usage; the status line's read hint includes `--seat`; "folder not found" errors name the full path.
- **Chat**: a third default relay (offchain.pub).
- README: new projects are approved with `aib newproj --approve` (Touch ID on macOS when an AI seat runs it); `safe` mode is explained as each AI's own permission settings.

## 0.1.18

- **Touch ID approval on macOS**: when an AI seat runs `aib newproj --approve`, the Mac shows a Touch ID (or login password) prompt and the project's goals and red lines are approved only after the owner touches the sensor. The prompt text is built by aib, each prompt carries a one-time nonce, only one prompt can be open per machine, and `AIB_TOUCHID=0` turns the path off. Other platforms keep the terminal-only approval.
- **Status line updates itself**: `aib update`, `install.sh` and `aib init` refresh the status line script at the end.
- **`aib vault doctor` syncs titles, aliases and the index** and fixes moved notes; new `aib vault mkdir`; `aib memory write` registers new notes in the index.
- **Handoff, tidy and review notes follow the seat's route** instead of always landing in the default handoff folder (falls back to the default and records why if the seat route cannot be written).
- **`aib newrole --new-seat --model`** picks the model for the new seat.
- **Chat**: seats without a bound persona still get a seat key and appear in the roster; island records gain extension fields with size limits and compatibility checks.
- Traditional Chinese: the brain row button in the right pane now reads 「換第二大腦(專案)」/「綁第二大腦(專案)」; when the pane is too narrow the title gives way and the button stays.

## 0.1.17

- **aib-boards 0.2.1**: new-mail cards pop up only for messages a human sent and for friend requests (friend requests only on the captain seat); the pixel avatar is gone and the icon is back in the title bar.
- **Release checks retry upgrades from PyPI**: right after an upload PyPI's CDN edges can disagree for a few minutes, so the from-PyPI upgrade check retries (6 × 20 s) instead of failing on the first stale answer.
- **Card dates use ISO**: new persona cards and skeleton cards write `updated` as UTC ISO, and the card registry reads ISO dates.
- **`aib vault newpost` no longer writes `orig_name`** for notes it creates (the field stays for notes adopted by `aib vault doctor`).
- Traditional Chinese: 「啟動時間 / 啟動」 is now 「初始」.

## 0.1.16

- **New notes and a health check for uid vaults** (licensed add-on `obsidian-labels`): `aib vault newpost` creates a note in uid layout in the chosen folder; `aib vault doctor [--fix --backup-dir]` checks the vault layout. Without the licence they exit 3 and write nothing.
- **Visit a friend's island and captain room**: `aib chat space [<owner> none|island|room]` sets what each contact may see (friends and your own machines: island by default; room is opened per person; strangers and blocked people: nothing). `aib chat island <owner>` / `aib chat room <owner>` read what was shared. Usage, unread counts, IP, host names and message text are always filtered out.
- **The "is this the human" check works on macOS**: in an ordinary terminal it no longer mistakes `/dev/tty` (owned by root) for the real terminal, so commands that only the human may run (such as approving a project's goals) work again; cockpit panes are now also recognised by their terminal, not only by an environment variable.
- **Release checks wait for PyPI**: the release verification waits until PyPI serves the new version before testing upgrades.
- End-to-end sandbox tests for online chat.
- **Seat level on the status line**: when the captain-room extension has written a snapshot within the last day, the CTX line shows the seat's level and resources after `@seat` (for example `@abb89 5⭐ 30 wood/12 stone`); the 📌 line keeps only the main item, steps and update time.
- `aib vault doctor` also reports `updated` values that are not ISO times and fixes the ones it can convert for certain.

## 0.1.15

- **Online chat, friends and contacts**: friend requests, blocking and a contact list (shown in the captain room's address book and inbox); replies from people you wrote to are accepted; your own other machines can be linked.
- **Presence and world map**: `aib chat share` sets what each contact can see (default: friends see online, your own machines see status, strangers see nothing).
- **Identity proofs**: NIP-05 goes into the public roster; social-media proofs are sent only to friends and your own machines.
- **Key backup**: `aib chat backup <file>` / `aib chat restore <file>` encrypt the chat keys with a passphrase you type (the file never holds the private key in clear).
- **Checks and relays**: `aib chat selftest`, custom relays, and `aib chat relays test <relay>`. Without a name and country set, chat does not connect.
- **Fewer lost or noisy receipts**: refusal receipts are rate-limited and listed; a receipt that arrives before its send record is held and matched later.
- **Memory index titles match the note**: the index keeps the cleaned title that is written in the note file.
- **Extensions install after being run**: stray `__pycache__` / `.pyc` files are listed and removed before install or enable instead of blocking it, and seat-info runs without writing bytecode.

## 0.1.14

- **Memory notes can be written on a fresh install**: the bundled note-format files carried a licence header added by the open-source export, so their fingerprint never matched and `aib memory write` always failed on machines without the private source. The check now ignores only that one header line and still enforces the fingerprint.
- **Notes go into vault folders with their own route**: a route added with `folder-add --route-key` uses its parent route's category, so `aib memory write --route <key>` writes into the new folder. `folder-add` reports `route_added=false` when the route was already there.

## 0.1.13

- **Online chat (renamed from global chat) asks once before going online**: the first time it is turned on, aib asks whether to connect to public relays and remembers the answer; saying no is remembered like `aib chat off`. When nobody can answer (non-interactive runs, the installer), it stays offline.
- **Friends only**: messages from strangers are refused with a reason (exit code 2). `aib chat friend add / remove` and `aib chat friends` manage the list.
- **Older versions keep working**: the message header still uses the old tag when sending, so 0.1.12 and earlier can read it; both old and new tags are accepted when reading.
- **New vault folders can carry their own route**: `folder-add --route-key <key>` registers a route for a new folder (permissions inherited from the nearest parent route, never widened), so `aib newproj`, binding and `aib memory write` can use it; `aib newproj --folder` also accepts a folder title.
- **`aib test --changed` no longer crashes** when a helper module is missing; it says the selection could not be measured (exit code 3).

## 0.1.12

- **Bundled extensions install themselves**: the free signed extensions that ship with aib are installed and registered with Claude Code during install, `aib init` and `aib update`.
- **Global chat receipts no longer get dropped**: the sender now records a message as sent before sending it, so a receipt that arrives before the relay's confirmation is no longer mistaken for an impostor.
- **Find who owns an out-of-scope question**: `aib who-owns <keyword or sentence>` searches persona cards, project cards and the vault's full text, ranks the matches with evidence, and suggests reopening the seat that owns it, or creating a new persona or project when nothing matches. `aib scope set / show` records the sites, domains, keywords and aliases a card is responsible for.
- **Seats hand off out-of-scope questions**: the persona pack now tells each seat to run `aib who-owns` first when a question is outside its scope, and to suggest opening the seat that owns it instead of doing it itself.
- **Add and retire vault folders**: `folder-add` and `folder-retire` register new folders in a uid vault, or retire old ones, without editing the index by hand.
- **`aib who-owns` matches more wording**: it splits camelCase and case (RichMenu = richmenu = rich menu), breaks a sentence into words before searching the vault, and reads each card's `scope_aliases`.
- **Licence trust keys**: licences signed by the maintainer's main key or the MacBook Pro backup key are accepted; a test pins the list so any change to it fails until a maintainer approves it.
- **Missing pieces are reported, not hidden**: when the module that recognises the human's own words is missing, memory tidy says so in its receipt and in the check-up instead of silently counting zero.
- **Main persona for the human**: `aib persona main show / set / clear` records which persona card is the human's own; when a seat's memory tidy finds words the human typed, it adds what it learned to that card quietly, with a snapshot and receipt so it can be undone.

## 0.1.11

- **Memory survives moving the vault**: the memory index stores each note's id and a path relative to the vault instead of an absolute path, and looks the note up again when reading; old indexes still work. If a note cannot be found it now says so instead of silently bringing back an empty note. Persona cards are found the same way after the vault moves.

## 0.1.10

- **Global chat (second part)**: receiving (catch-up subscription, verification, import into the queue, delivery status and receipts), read receipts sent in one batch without going through the AI, and observability (audit lines, worker status, clock skew, offline). Still being developed.
- **Prepared at install**: `aib chat prepare` installs the chat library and creates keys on this computer only (nothing goes online); the installer runs it after `aib init`, and a failure does not stop the install.
- **Tests**: the process-table warning tests also pass on Linux machines without a per-user process limit.

## 0.1.9

- **Global chat (first part)**: `aib chat` skeleton, keys, the roster, and sending to a global address (still being developed; the bridge that carries messages is not public yet).
- **Process-table warning**: when the processes of your user reach 80% of the system limit, the right pane shows a red warning with the top 3 commands, and fleet patrol prints it first (measured without starting new processes).
- **Persona sources**: `aib persona sources` entry point.
- **CI**: ordinary pull requests run macOS only on Python 3.12; full runs stay on `main`, integration and release branches.

## 0.1.8

- **Every AI is free**: the free edition no longer limits which AI tools you can use; every provider's sources can be turned on and no seat is refused or marked as paid.
- **Messages between AIs**: cross-host `aib dm` shows the real sender, works with older receivers and reuses one SSH connection (about 45 ms instead of 240–540 ms). Retries resend only to seats that did not get the message; options are no longer dropped; messages no longer vanish; a seat cannot send as another seat; a tool timeout no longer sends twice. When the receiving seat is busy, the message waits in its queue and is delivered when the seat is idle; messages held for a person are announced to the receiving seat when it is idle. A runaway loop is stopped after 8 nested calls.
- **Every AI brand is covered**: Claude, Codex, Grok, Copilot and Antigravity get the context-usage hook, and it is installed by the one-line install, `aib init` and `aib update` (not only when a seat opens; Codex asks you to trust it once with `/hooks`). API-key seats and bridged bots run the same table after each turn (seats whose usage cannot be measured only consolidate memory). A seat that ran `/clear` is still recognized.
- **Context usage is handled by the program**, from one table: memory is consolidated quietly at 30, 50, 70 and 90%; at 80% you are reminded to hand off; at 85% the closing steps (tidy → summary → `aib handoff` + verify) are required before a Claude seat can end its turn (write「先做」in your message to skip that one turn); 95% is the red line. The hook is now installed when a seat opens (it was never installed in the open-source layout).
- **Personas, projects, binding**: new persona cards are kept under their uid and shown by title; `aib newproj --approve` lets you approve a project's goals and red lines yourself; there is one route table, so re-binding another seat no longer drops yours; `bindme repair` reads back what it fixed; auto-binding no longer names things after status lines; `newrole` explains where the card is when binding fails.
- **Second-brain uid layout converter**: plan → apply → verify → undo; lists, links, attachments and `.obsidian` settings are converted; templates keep their names.
- **Opening seats**: a seat opened inside a cabin opens in that cabin; only defined roles are accepted; a missing AI tool is named with how to install and sign in, and no empty window is opened. The right pane marks bridged bots 🤖 and API-key engines 🔑.
- **English UI is English throughout** (`doctor`, `page`, `persona`, the bottom pane, every `--help`), with real Japanese and Korean; internal codes are gone.
- **Tests**: earlier green results are reused only when they still apply (12-hour limit on resuming, red results from other machines still count, a time-zone change reruns, damaged records repair themselves); a few tests that never reused results now do.
- **Install / doctor**: git installs get their hooks; uninstall keeps unsaved work; `aib doctor` marks what it cannot measure as a note instead of a failure.
- **Extensions**: `aib plugins licensed` for paid plugins; the five free extensions are signed, so `--allow-unsigned` is no longer needed.
- **Right pane and screenshots**: the load / CPU temperature / heavy-work readings at the top of the right pane work in the open-source version (the heat rulers ship now), and the Control+Shift+S screenshot helper ships and builds on Command Line Tools 26 (works around the duplicate SwiftBridging module).

## 0.1.7

- aib installed with pip in a virtual environment now updates itself when you open a seat, like pipx and one-line installs already did. **If you installed 0.1.6 or older with pip, run `pip install --upgrade ez2-aib` once.**
- `aib update` on a pip / pipx install now names the right package (`pipx upgrade ez2-aib` or `pip install --upgrade ez2-aib`).
- The captain bar label and the error messages of the captain tools follow your language (Japanese and Korean too), and the label is no longer cut off in English.
- Every release is now checked before it reaches PyPI: the built package is installed in a clean environment and upgraded to from the previous and the oldest available release; after the upload, the same checks run again against PyPI (pipx, pip and the one-line install) on Linux and macOS.

## 0.1.6

- The README now opens with where aib comes from and its slogan, **AI Basic to AI Best**: the easiest-to-use, most human foundation for AI tools. It also previews virtual buddies (a Claude Code mod first), which are still in development.
- Maintainer-only workflow tools are no longer shipped in the open-source version. Commands that rely on them say they are still in development instead of failing.
- The translation files only carry strings for features that ship with aib (about 600 unused strings removed).
- Every release is now checked for parts that should become plugins before it is published.

## 0.1.5

- Opening a seat now also checks aib itself: if a newer aib is out, it is updated in the same 30-second countdown as the AI tools (press s to skip); the new version is used from the next seat. `aib cli` lists aib too. `AIB_CLI_UPDATE=0` turns the check off.
- A brand-new computer no longer gets stuck: aib ships a default route contract and keeps its own vault in `~/.ez2-aib/vault` (created on first use), so `aib newrole` and the second-brain pane work without any extra setup.
- Your own vault (`EZ2_VAULT_DIR`) works too: `aib newrole` uses the default route contract when that vault is new or empty, has its own index, or already has `10-Roles`; otherwise it tells you the two ways to set it up instead of just failing.
- The one-line install runs `aib init` for you.
- Commands that are still in development now say so instead of showing a Python error.
- `aib doctor` is now reachable: it lists what this computer still lacks and how to fix it, checks only the AI tools you installed, and covers the second brain (vault, route contract, note-format modules).
- A mistyped command says "there is no … command" in your language and lists the common ones, instead of printing the full usage in four languages.
- The public repository's CI now installs aib into an empty home on Linux and macOS and runs a new user's first commands, so these dead ends cannot come back unnoticed.
- The ledger's event schemas ship with aib (earlier versions left them out, so anything that writes the ledger refused to run).
- `aib init` no longer crashes on its last step; Python cache files go to `~/.cache/ez2-aib` instead of the install folder.
- `aib room` (the Captain's Room, including `aib room contacts` and `aib room say`) is no longer in the open-source version; it is still in development and not open to the public yet.

## 0.1.4

- The cockpit color theme (`themes/aib-default.json`) is now included; earlier versions left it out, so the cockpit could not find its theme.
- `aib_dm` MCP gains three read-only tools for AI seats: `aib_inbox`, `aib_team`, `aib_tasks` (writing still goes through `aib_dm`).
- ClaudeBot read receipts: a message is marked read once it reaches Claude, instead of staying "delivered" forever.
- Bot bindings come back when a cabin restarts; bot title lines filter control characters; a GrokBot that fails while closing no longer stays stuck.
- Two small verify tools: `sync_receipt` (a receipt per host when syncing a fleet, failures loud) and `classify_env_blocked` (separates reds caused by a missing environment from real ones).
- The old ACB board backend is removed from `outline-to-tasks`.

## 0.1.3

- `aib killswitch on|off|status`: an emergency stop. While it is on, no AI can open a new seat, send `aib dm` or ship; if the switch cannot be read, it counts as on.
- The pre-push check runs the whole suite and then lists everything at once: new reds, tests that were already red, and tests that could not be verified here. `--fail-fast` brings back stopping at the first red.
- Captain's contacts: one list from four sources (who is online, search, your ten most frequent contacts), friends, groups, block and mute, delivery / read / handled / replying states for messages, sending to a group, and `aib room contacts`. `aib room say` speaks to the seats in the same room.
- Translation now uses the AI bound to the seat.
- `aib bindme`: see every seat's persona binding at once and fill in what is missing.
- Each seat's status line shows what it is working on (`aib now`): step, progress and a colored state.
- The screen stops refreshing while you are composing text with an input method.
- An old "main" cabin is no longer brought back by itself; it opens only when you ask for main.
- Connections to other hosts use safe ssh settings: your keys are not forwarded and all forwarding is off.
- Keys are masked before code is sent to an external AI for review.
- A cabin reconnects by itself when Terminal crashes or its window disappears, and a window that is stuck spewing errors is replaced with a new one. While an AI is writing, the input area says so, and you are warned when Terminal recently crashed during input-method typing.
- `aib top`: what is using this machine (top CPU users, which programs started each second and who started them). Log files are capped at 8 MB, compressed, three kept.
- A seat that switches Codex accounts three times in 30 minutes stops and waits; stopping programs by name never stops aib itself; the idle cleanup records what it stops.
- `aib handoff` checks the persona and project first and proposes a binding if one is missing; `aib bindme` reuses a fitting persona card before creating a new one.
- `aib test --guard` / `--scan`: the checks every push must pass are worked out from the rules instead of a hand-kept list.
- NVIDIA seats can rank models hourly and switch to the next model when one stops answering.
- Fixed: Grok seats come back with their own model; `lsof` not found on some Macs; leftover-process cleanup on macOS 27.
- `aib room` (the Captain's Room web page) is not in this version yet; `aib room contacts` and `aib room say` work in the terminal.
- Fixed: handoff receipts across midnight, `seat-info` crashing, a pinned cabin reopening after you closed it, and a flaky screen test.
- `aib uninstall` on a pip or pipx install now names the command that removes it (`pipx uninstall ez2-aib` / `python3 -m pip uninstall ez2-aib`) instead of calling it a developer copy.

## 0.1.2

- New `aib uninstall`: lists what it would remove first; `--yes` removes this aib, `--all` also removes its records (backed up first). Another aib on the same computer is never touched.
- One-line install on a computer that already has another aib: it no longer reports a failure or tells you to replace it. Your existing aib keeps working; the open-source copy sits next to it.
- The installer's hints now point to the GitHub address that works, and no longer show developer-only lines.
- Re-running the one-line install on Linux now says "updating" instead of "installing".
- The one-line install now installs the latest release (the newest `v*` tag), the same version as PyPI, instead of whatever was last merged; re-running it moves to a newer release once one is published, and never goes back.
- Releases: one workflow run creates the tag and the GitHub release (its notes are the CHANGELOG section) and, after a maintainer approves, uploads to PyPI; only one release runs at a time. CI runs on pull requests and on main only.

## 0.1.1

- Docs in four languages: README, CHANGELOG, CONTRIBUTING, SECURITY and both guides now come in English, 繁體中文, 日本語 and 한국어.
- The language links at the top of each document point to GitHub, so they also work on PyPI (they returned 404 there in 0.1.0).
- `aib --help` now opens with what aib is for: many AIs, one workspace.
- One-line install now works on Linux too: it installs the test safety net (`aib test`, `aib ship`); the cockpit still needs macOS.
- README shows how to get pipx first if you do not have it.
- CI also tests Python 3.14 and 3.15.

## 0.1.0 — first public release

- `aib test`: runs only the tests whose inputs changed and reuses earlier green results for the rest
  (Python, bash and Node tests; pytest and unittest). Every test gets its own temporary folder and home folder;
  leftovers and background programs are cleaned up and named in the summary.
- `aib ship`: pre-push check, packaging and pushing; tests that blocked an earlier push run first.
- Cockpit (tmux three panes, mouse menu, pop-up menu), fleet management, seat skills (handoff, new project,
  new role, new AI, new bot) and the bug-report skill are built in.
- `aib tmp` lists or removes what aib and its tests left in the temp folder, and nothing else.
- One-line install downloads `install.sh` from this repository; `aib update` tries the website copy first and falls back
  to this repository's copy if the website does not serve the open-source installer.
- **Revive**: bring a seat or a whole cockpit back in place after a crash or disconnect, and keep going.
  - It brings back the AI model the seat was using (conversation record first, then the old seat file, then the default).
  - Its first answer after reviving starts with a plain title, a summary of about 150 words and suggested next steps.
  - `aib restart` boot menu (macOS): after login, a terminal opens with the cockpits that can be restored — only if one did not come back.
- Panels pause redrawing while you are typing with an input method and have not sent the text yet, which avoids a macOS
  Terminal crash (needs the input method to report composing; without it panels redraw as usual).
- Right pane: once a seat is confirmed able to take input, an older "unconfirmed" no longer overrides it.
- `/aib view bot`: see bot seats' status from another seat (running or not, bound conversation, waiting mail, last send/receive, errors).
- The default model for Claude seats is now Opus 5.5.
