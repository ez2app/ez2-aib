---
name: ez2-local-perf
description: Keep an Ez2 AIB development Mac cool and fast. Use when the machine feels hot or slow, fans spin, load is high, or before/after a big test run — 主機效能、發熱、變慢、風扇、負載高、普查、孤兒行程、殘留、舊工作樹、清樹、room heat. Measures first with `aib room heat`, releases only whitelisted leftovers, notifies owners of orphan daemons instead of killing them, and archives merged worktrees by moving (never deleting). Works the same for Claude, Codex, Grok, and Antigravity skill folders.
---

# Ez2 local performance (four-vendor)

Copy this folder to the vendor skill dir (Claude `~/.claude/skills/`, Codex skills, Grok bundled skills,
Antigravity skills). Everything below is the same `aib` command on every vendor. macOS is the main target;
on Linux the commands answer `unmeasured` / `unsupported` where they cannot measure — say so, never guess.

## Rules (read first)

1. **Measure before you act.** Every suggestion carries a number from this machine, taken now.
   "Could not measure" is not zero — report it as unmeasured.
2. **Never broad-kill.** No `pkill` / `killall` / name matching. Only stop a pid you started yourself,
   or use `aib room heat release` (whitelist, re-checks pid + start time + uid before every signal).
3. **Other seats' processes are theirs.** Orphans from someone else's worktree: notify the owner
   (`tidy notify`), do not kill.
4. **Archive by moving, never delete.** Old worktrees go to `~/Ez2-backup.noindex/`. New worktrees go
   under `~/wt.noindex/` (Spotlight skips `.noindex`).
5. **Do not edit a tree while a wide test runs in it.** The tree guard will flag and restore it.
6. **Paid automation lives only in the captain room.** Manual commands here are free; scheduled auto-release,
   auto-archive and auto-notify are switches in the captain room plugin. Do not show prices or purchase links.

## Do

1. **Snapshot** — `aib room heat` (or `--json`). Read level, load, memory, temperature and the per-owner groups
   (seat / aib_test / reviewer / headless_browser / sandbox / aib_core / system / other_app).
   The snapshot also shows `cpu_total` and `unseen_cpu` (short-lived processes `ps` never catches);
   if unseen is large, run `aib room heat churn --json` to see who keeps spawning them.
2. **Leftovers** — the snapshot's `candidates` are whitelisted leftovers (headless browsers, test orphans).
   Show them, then `aib room heat release <id>…` or `--all-safe`. `held` items are never released
   (ssh / deploy locks / someone still reading the output).
3. **Orphan daemons and old worktrees** — `aib room heat tidy [--seat <your seat>]`.
   - `daemon_orphan`: your uid, parent gone (ppid 1), a script from a worktree, alive over an hour.
     Run `aib room heat tidy notify --dry-run`, show the list, then `aib room heat tidy notify`
     (one notice per owner seat, not repeated within 6 hours). Owner `unknown` ⇒ tell the human.
   - Worktrees: `archivable` = merged into origin/main, clean (a `node_modules` link is fine), nobody's cwd or
     running file inside, idle 2 h, not locked. Anything else shows its reason code
     (`not_merged` `dirty` `busy` `recent` `locked`) and is left alone.
     Run `aib room heat tidy archive --all-mine --seat <your seat> --dry-run`, show it, then run it without
     `--dry-run`. Only your own seat's trees move (`not_mine` otherwise).
4. **Report** — a short table: what you measured, what you released / archived / notified (with ids),
   what is left and why (reason codes as-is; add plain words beside them, never instead of them).
   Bigger changes (merging resident daemons, test queueing, polling intervals) are proposals for the human
   to pick, with the numbers.

## Don't

- Don't run the full test suite to "check" performance; it is usually the biggest heat source itself.
- Don't touch `~/.ez2-aib` state files, launchd jobs, or another seat's tmux panes to save CPU.
- Don't paste command lines from the snapshot into reports — they can carry tokens; program names only.
