#!/usr/bin/env bash
# RK2 (a9136 11-18, 0eed7 VERIFY 51ac92d9 M1): a held stop the next start turned `interrupted`
# is still decided by its recorded stop, never taken up as a plain interrupted one.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# RK2 the freed held stop is taken up like a queued one cut by the stop
python3 -I scripts/knife.py crates/aibd/src/revive.rs \
  '        "interrupted" if last.stop_status.is_some() => "stop_unconfirmed",' \
  '        "interrupted" if last.stop_status.is_some() && false => "stop_unconfirmed", // knife' \
  --expect-fail only_a_seat_running_when_aibd_stopped_is_picked_up \
  -- cargo test -p aibd --locked --lib only_a_seat_running_when_aibd_stopped_is_picked_up
# 5d33a 11-18 (N-ws-revive-held (ii)): unreadable processes older than the seat's provider.
tr() { python3 -I scripts/knife.py crates/aibd/src/platform/tree.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
rv() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
ss() { python3 -I scripts/knife.py crates/aibd/src/platform/session.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
# OL1 a process started the same second as the provider counts as older (macOS)
tr '.filter(|b| b.pid == pid && b.start_sec < r)' '.filter(|b| b.pid == pid && b.start_sec <= r) // knife' \
   a_candidate_is_older_only_when_it_started_strictly_before_a_recorded_root
# OL2 one unreadable candidate read as older is enough
tr '    older > 0 && older == unreadable' '    older > 0 && older <= unreadable // knife' \
   a_candidate_is_older_only_when_it_started_strictly_before_a_recorded_root
# OL3 older candidates keep the old reason
tr '            if self.candidates_unreadable_older {' '            if self.candidates_unreadable_older && false { // knife' \
   older_unreadable_candidates_have_their_own_reason_and_keep_the_slot
# OL4 an older stop is taken up without the provider's end recorded
rv '    s.stop_reason.as_deref() == Some(OLDER) && (s.exit_code.is_some() || s.signal.is_some())' \
   '    s.stop_reason.as_deref() == Some(OLDER) && (s.exit_code.is_some() || s.signal.is_some() || true) // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# OL5 the rejoin over older processes is not said
rv '        .filter(|s| s.stop_reason.as_deref() == Some(OLDER))' \
   '        .filter(|s| s.stop_reason.as_deref() == Some(OLDER) && false) // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# OL6 the stop does not carry an older process's start
ss '                            .flatten(),' '                            .flatten().filter(|_| false), // knife' \
   older_unreadable_candidates_have_their_own_reason_and_keep_the_slot
# OL7 the processes let go are read from the first stop, not the last
rv '        .rev()' '        .skip(0) // knife' \
   the_processes_let_go_come_from_the_last_stop_with_their_start
# OL8 a candidate the stop did not read as older is let go too
rv '                .filter(|c| c["started"].is_u64())' '                .filter(|c| c["pid"].is_u64()) // knife' \
   the_processes_let_go_come_from_the_last_stop_with_their_start
