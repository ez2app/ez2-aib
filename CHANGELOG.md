[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

## 0.1.12

- **Bundled extensions install themselves**: the free signed extensions that ship with aib are installed and registered with Claude Code during install, `aib init` and `aib update`.
- **Global chat receipts no longer get dropped**: the sender now records a message as sent before sending it, so a receipt that arrives before the relay's confirmation is no longer mistaken for an impostor.
- **Find who owns an out-of-scope question**: `aib who-owns <keyword or sentence>` searches persona cards, project cards and the vault's full text, ranks the matches with evidence, and suggests reopening the seat that owns it, or creating a new persona or project when nothing matches. `aib scope set / show` records the sites, domains, keywords and aliases a card is responsible for.
- **Seats hand off out-of-scope questions**: the persona pack now tells each seat to run `aib who-owns` first when a question is outside its scope, and to suggest opening the seat that owns it instead of doing it itself.
- **Add and retire vault folders**: `folder-add` and `folder-retire` register new folders in a uid vault, or retire old ones, without editing the index by hand.

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
