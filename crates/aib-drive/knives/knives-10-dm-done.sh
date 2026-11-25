#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-drive dm_done knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 A3 dm_done (b433b, KNIVES-dmdone.md): each dm-close reading rule, broken, turns its test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
dmk() { python3 -I scripts/knife.py "crates/aib-drive/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-drive --locked --test dmdone "$4"; }
# DD1: a transition to any state but read counts as a close
dmk dmdone.rs 'v.get("to").and_then(serde_json::Value::as_str) == Some("done")' \
   'v.get("to").and_then(serde_json::Value::as_str) != Some("read")' \
   only_a_successful_transition_to_done_with_a_dm_id_is_a_close
# DD2: a refused close (already done) counts
dmk dmdone.rs '.and_then(serde_json::Value::as_bool) == Some(true);' \
   '.is_some();' \
   only_a_successful_transition_to_done_with_a_dm_id_is_a_close
# DD3: a cut last line is taken as read and lost
dmk dmdone.rs '.map_or(0, |i| i + 1);' \
   '.map_or(bytes.len(), |_| bytes.len());' \
   a_cut_last_line_is_read_again_next_time_and_whole_lines_move_the_offset
# DD4: every look reads the whole file again: one close wakes every second
dmk dmdone.rs 'Some(&o) if o <= len => o,' \
   'Some(&o) if o <= len => 0,' \
   closes_from_before_the_first_look_are_not_events_later_ones_are
# DD5: offsets are never kept: a restarted observe loses them
dmk dmdone.rs 'if moved {' \
   'if moved && !moved {' \
   closes_from_before_the_first_look_are_not_events_later_ones_are
# DD6: by is aib-drive: act skips the event as its own
dmk dmdone.rs 'pub const BY: &str = "aib-drive-dmq";' \
   'pub const BY: &str = "aib-drive";' \
   wake_writes_the_newest_close_as_a_dm_done_event_that_observe_answers
# DD7: folders that are not seat codes are read
dmk dmdone.rs '.filter(|s| seat_code(s))' \
   '.filter(|s| seat_code(s) || !s.is_empty())' \
   a_shorter_file_starts_again_at_its_end_without_events_and_other_names_are_skipped
# DD8: the real clock never looks at the dm queues
dmk observe.rs 'let _ = w.wake(at);' \
   'let _ = (at, &w);' \
   the_real_clock_wakes_on_a_dm_close_within_its_wait
# DD9: closes during the first round are taken as history
dmk observe.rs 'let _ = w.scan();' \
   'let _ = &mut w;' \
   the_real_clock_wakes_on_a_dm_close_within_its_wait
# DD10: the oldest close is reported, not the newest
dmk dmdone.rs 'let last = self.scan().pop()?;' \
   'let last = self.scan().into_iter().next()?;' \
   wake_writes_the_newest_close_as_a_dm_done_event_that_observe_answers
