# aibd timers

Generated from `crates/aibd/src/timers.rs` (ADR 0005). Do not edit by hand:
run `AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers`.

| Name | Kind | Period | Max lifetime | Purpose |
|---|---|---|---|---|
| `tree.cancel_wait` | operation | 50 ms | 30 s | re-check a signalled process tree until it is gone (ADR 0004 cancel steps) |
| `session.slot_rescan` | operation | 2 s | 30 s | re-check a stopped session whose stop could not show the tree gone, so its slot can come back (a63ab 11-09) |
| `selfcheck.child_wait` | operation | 20 ms | 3 s | wait for the selfcheck tree's child to report it started, before stopping the tree (bb7dc #549) |
| `session.reject_flush` | operation | 60 s | 61 s | when a window of folded spawn rejections closes, write how many were folded (a63ab 11-09 19:23, 1ced7 #593 M1) |
| `selfcheck.leaf_life` | operation | 30 s | 30 s | the self-exec check tree's child stays for the stop to meet, then ends by itself (one wake-up; a9136 11-09) |
| `startup.lock_wait` | operation | 100 ms | 7 s | at start, re-try aibd.lock while a program held before exec may still keep a copy of it (macOS, #614) |
| `thermal_sample` | operation | 5 s | 12 h | sample CPU die temperature and thermal pressure while aibd's own processes run; begun again at 12 h without a gap (ADR 0005 §1 exception, R1) |
| `alive.due_wait` | operation | 60 s | 24 h | only while a down seat waits for its alert threshold: wake at the earliest due time, report, stop (A11 T46, 2240e 11-13) |
| `drive.wake` | resident | 600 s | — | only with AIBD_DRIVE_WAKE=1: run aib-drive event tick --by aibd, the 10-minute floor under aib-drive's event wakes (S79 A2, 7b81e 11-15) |
| `drive.wake_child_wait` | operation | 200 ms | 61 s | only during a drive wake: re-check aib-drive event tick until it exits, kill it at 60 s so a hung child cannot stop the floor (S79 A2 L1, 7b81e 11-15) |
| `drive.refresh` | resident | 300 s | — | only with AIBD_DRIVE_WAKE=1: run aib-drive refresh --auto, which scans only when the cache and the last try are 20 minutes old and GitHub has 1500 calls left (S79 B3-1, a9136 11-15) |
| `drive.refresh_child_wait` | operation | 1 s | 1860 s | only during a drive refresh: re-check aib-drive refresh --auto until it exits, kill it at 31 min (its own scan limit is 30) so a hung scan cannot stop the refresh (S79 B3-1, a9136 11-15) |
| `drive.act_child_wait` | operation | 1 s | 181 s | only during a drive wake: re-check aib-drive act --auto (next, seat facts, one act round; every act switch off = a dry run) until it exits, kill it at 180 s so a hung round cannot stop the floor (519d0 11-15, 56cda) |
| `seat_input.write_wait` | operation | 5 s | 5 s | only while a typed text is written to a seat's stdin: wait for the write, answer 202 delivery pending at 5 s (S89-input L1, 5d33a / 0eed7; M1 on 29e68425) |
| `hosts.probe_wait` | operation | 200 ms | 16 s | only during GET /api/v1/hosts: re-check each machine ssh (connect timeout 5 s) until it exits, kill it at 15 s so a hung machine cannot hold the answer; one round a minute at most (N-workspace wave 3, a9136 11-18) |
| `gh.view_wait` | operation | 200 ms | 20 s | only during a trusted seat's gh_view act: re-check the one gh read until it exits, kill it at 20 s so a hung gh cannot hold the answer (DESIGN-trusted-g1g4 §1, 7b81e 11-20) |
| `repo.push_wait` | operation | 200 ms | 120 s | only during a page's repo_push act: re-check the one git call or leak scan until it exits, kill its group at 120 s so a hung remote cannot hold the answer (DESIGN-repo-push-g2, 7b81e 11-20) |
| `preview.port_wait` | operation | 100 ms | 20 s | only while the page turns the preview on: re-read the preview build's run/aibd.json until it names its port, give up (and stop it) at 20 s (SPEC §20, a9136 11-18) |
| `preview.stop_wait` | operation | 100 ms | 10 s | only while the preview is stopped: re-check the preview build after TERM until it has ended, kill it at 10 s (SPEC §20, a9136 11-18) |
| `preview.handoff_wait` | operation | 5 s | 300 s | only during aibd ctl preview swap while seats run in the preview: ask the preview every 5 s which seats wrote their handoff, swap anyway at 300 s and name the rest (PREREAD-previewswap, a9136 11-21) |
| `dm.mirror` | resident | 60 s | — | only with dm_mirror true in <data>/bridge.json: read the 1.x mail ledger files changed since the last pass into the message library (new live letters, status moves), read only (N-web-dmbridge, 7ebca 11-19) |
| `seat.tick` | resident | 60 s | — | only while <data>/seat_ticks.json lists a seat: type each listed seat its sentence when its period (5 min to 1 day) has passed, between turns only (ORDER-webonly-today 7, a9136 11-20) |
| `receipt.poll` | resident | 30 s | — | only while a receipt run waits for its owner or runs (<data>/receipt_runs.json): ask the runner for each one's .out and read it, stop when none is under way (N-webonly 4, 7ebca 11-20) |
| `receipt.owner_wait` | operation | 1 s | 130 s | only while one receipt's fingerprint card is out: re-read the ticket until the owner answers, give up when the ticket has ended (N-webonly 4, b4297 11-21) |
| `receipt.link_wait` | operation | 200 ms | 900 s | only while aibd says one line to a pinned test machine (a receipt's bundle, a read, a cancel): re-check its ssh until it ends, stop it at the line's own limit (N-webonly 4, b4297 11-21) |
