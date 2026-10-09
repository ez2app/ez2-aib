---
name: ez2-aib-support
description: Collect a de-identified Ez2 AIB bug/suggestion pack. Use when the user wants to report a bug, send feedback, or file a support note. Do not send until the user confirms. Works the same for Claude, Codex, Grok, and Antigravity skill folders.
---

# Ez2 AIB support collector (four-vendor)

Copy this folder to the vendor skill dir (Claude `~/.claude/skills/`, Codex skills, Grok bundled skills, Antigravity skills). The command is the same.

## Do

1. Run `aib support` (installed aib) or `python3 tools/support/collect.py` from an aib checkout.
2. Show the JSON to the user. Redaction already ran; if collect exits non-zero, stop.
3. Ask: send this local pending file? Only if they say yes, run:
   `python3 tools/support/collect.py --confirm --sender human` when the user is sending,
   or `--sender ai` when the seat is sending for them.
4. Never LINE-push. Never paste API keys, vault notes, or full transcripts.
5. Nothing leaves the machine by itself. To reach the aib maintainers, the user opens an issue at
   https://github.com/ez2app/ez2-aib/issues/new/choose and attaches the pending file (path printed by `--confirm`).
   Do not open the issue for them unless they ask.

## Must-miss

If the JSON still contains `sk-`, `AKIA`, `api_key=`, or a private key block, collect refuses. Do not hand-edit secrets back in.
