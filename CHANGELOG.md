[English](CHANGELOG.md) | [繁體中文](CHANGELOG.zh-TW.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

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
