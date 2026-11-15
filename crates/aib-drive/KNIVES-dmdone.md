---
status: 定稿
rev: r0
node: S79 A3(dm_done)
owner: b433b
---
# aib-drive dm_done knives (S79 A3)

Each row: one change in `src/dmdone.rs` / `src/observe.rs`; the named test in `tests/dmdone.rs` must fail.
DD8 and DD9 are in `crates/aib-drive/src/observe.rs`, the others in `crates/aib-drive/src/dmdone.rs`; every anchor occurs exactly once in its file. For the integration seat to put into aibd.yml (RULES §21 §25), same shape as KNIVES-wake.md:

```bash
dd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-drive --locked --test dmdone "$4"; }
```

| knife | what goes wrong | old | new | test |
|---|---|---|---|---|
| DD1 | a transition to any state but read counts as a close | `v.get("to").and_then(serde_json::Value::as_str) == Some("done")` | `v.get("to").and_then(serde_json::Value::as_str) != Some("read")` | only_a_successful_transition_to_done_with_a_dm_id_is_a_close |
| DD2 | a refused close (already done) counts | `.and_then(serde_json::Value::as_bool) == Some(true);` | `.is_some();` | only_a_successful_transition_to_done_with_a_dm_id_is_a_close |
| DD3 | a cut last line is taken as read and lost | `.map_or(0, \|i\| i + 1);` | `.map_or(bytes.len(), \|_\| bytes.len());` | a_cut_last_line_is_read_again_next_time_and_whole_lines_move_the_offset |
| DD4 | every look reads the whole file again: one close wakes every second | `Some(&o) if o <= len => o,` | `Some(&o) if o <= len => 0,` | closes_from_before_the_first_look_are_not_events_later_ones_are |
| DD5 | offsets are never kept: a restarted observe loses them | `if moved {` | `if moved && !moved {` | closes_from_before_the_first_look_are_not_events_later_ones_are |
| DD6 | by is aib-drive: act skips the event as its own | `pub const BY: &str = "aib-drive-dmq";` | `pub const BY: &str = "aib-drive";` | wake_writes_the_newest_close_as_a_dm_done_event_that_observe_answers |
| DD7 | folders that are not seat codes are read | `.filter(\|s\| seat_code(s))` | `.filter(\|s\| !s.is_empty())` | a_shorter_file_starts_again_at_its_end_without_events_and_other_names_are_skipped |
| DD8 | the real clock never looks at the dm queues | `let _ = w.wake(at);` | `let _ = (at, &w);` | the_real_clock_wakes_on_a_dm_close_within_its_wait |
| DD9 | closes during the first round are taken as history | `let _ = w.scan();` | `let _ = &mut w;` | the_real_clock_wakes_on_a_dm_close_within_its_wait |
| DD10 | the oldest close is reported, not the newest | `let last = self.scan().pop()?;` | `let last = self.scan().into_iter().next()?;` | wake_writes_the_newest_close_as_a_dm_done_event_that_observe_answers |
