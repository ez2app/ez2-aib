# aibd timers

Generated from `crates/aibd/src/timers.rs` (ADR 0005). Do not edit by hand:
run `AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers`.

| Name | Kind | Period | Max lifetime | Purpose |
|---|---|---|---|---|
| `tree.cancel_wait` | operation | 50 ms | 30 s | re-check a signalled process tree until it is gone (ADR 0004 cancel steps) |
| `session.slot_rescan` | operation | 2 s | 30 s | re-check a stopped session whose stop could not show the tree gone, so its slot can come back (a63ab 11-09) |
| `selfcheck.child_wait` | operation | 20 ms | 3 s | wait for the selfcheck tree's child to report it started, before stopping the tree (bb7dc #549) |
