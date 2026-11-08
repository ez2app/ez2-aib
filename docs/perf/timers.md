# aibd timers

Generated from `crates/aibd/src/timers.rs` (ADR 0005). Do not edit by hand:
run `AIBD_WRITE_TIMERS_DOC=1 cargo test -p aibd timers`.

| Name | Kind | Period | Max lifetime | Purpose |
|---|---|---|---|---|
| `tree.cancel_wait` | operation | 50 ms | 30 s | re-check a signalled process tree until it is gone (ADR 0004 cancel steps) |
