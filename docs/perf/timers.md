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
| `host.load_sample` | operation | 200 ms | 250 ms | one wait between the two CPU reads of a host load sample (sysinfo needs 200 ms); a sample is shared for 1 s, so at most one wake-up a second (S57) |
| `drive.wake` | resident | 600 s | — | only with AIBD_DRIVE_WAKE=1: run aib-drive event tick --by aibd, the 10-minute floor under aib-drive's event wakes (S79 A2, 7b81e 11-15) |
| `drive.wake_child_wait` | operation | 200 ms | 61 s | only during a drive wake: re-check aib-drive event tick until it exits, kill it at 60 s so a hung child cannot stop the floor (S79 A2 L1, 7b81e 11-15) |
