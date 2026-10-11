[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

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
