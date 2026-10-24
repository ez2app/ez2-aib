---
name: ez2-aib-support
description: Collect a de-identified Ez2 AIB bug/suggestion pack. Use when the user wants to report a bug, send feedback, or file a support note. Do not send until the user confirms. Works the same for Claude, Codex, Grok, and Antigravity skill folders.
---

# Ez2 AIB support collector (four-vendor)

Copy this folder to the vendor skill dir (Claude `~/.claude/skills/`, Codex skills, Grok bundled skills, Antigravity skills). The command is the same.

## Do

1. Run `aib support`. It only lists what the pack would hold (six sections: versions, plugins, doctor, logins,
   cabins, failures) and writes nothing. It never collects messages, Vault notes, project names, personas, keys,
   log text, or cabin / seat ids.
2. Show that list to the user.
3. Only if the user says to save it, run `aib support --write`. The file is saved on this machine only
   (private permissions); the reply shows where.
4. Never send it, upload it, or attach it anywhere yourself. The user decides whether to send it and does it
   themselves (for example, attaching it to https://github.com/ez2app/ez2-aib/issues/new/choose).
5. Never LINE-push. Never paste API keys, vault notes, or full transcripts.
