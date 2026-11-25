#!/usr/bin/env bash
# aib-knives: os=unix
# A11 (DESIGN-a11-dgrs-open-asks-08de5-1123 r1, f163c REVIEW-a11 r1): a dgrs seat starts from the page only through the owner
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a11-open
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        && aib_seatopen::pipe_modes(&req.provider).contains(&"dgrs")' \
  '        && false' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-restart
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  'crate::repos::dgrs_restart_gate(&st, seat_id, ticket, asker, crate::repos::GateAct::Restart)' \
  'crate::repos::dgrs_restart_gate(&st, "none", ticket, asker, crate::repos::GateAct::Restart)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-model
python3 -I scripts/knife.py crates/aibd/src/seat_model.rs \
  'dgrs_restart_gate(&st, &seat_id, ticket, asker, crate::repos::GateAct::Model)' \
  'dgrs_restart_gate(&st, "none", ticket, asker, crate::repos::GateAct::Model)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-clear
python3 -I scripts/knife.py crates/aibd/src/seat_clear.rs \
  'crate::repos::dgrs_restart_gate(&st, seat_id, ticket, asker, crate::repos::GateAct::Clear)' \
  'crate::repos::dgrs_restart_gate(&st, "none", ticket, asker, crate::repos::GateAct::Clear)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-safe
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '    if mode != Some("dgrs") {' \
  '    if mode != Some("dgrs") && mode.is_none() {' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-kind
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '"kind": "restart", "seat_id": seat_id,' \
  '"kind": "open", "seat_id": seat_id,' \
  --expect-fail 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other'
# a11-seat
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '"kind": "restart", "seat_id": seat_id,' \
  '"kind": "restart",' \
  --expect-fail 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other'
# a11-unread (f163c REVIEW-a11 L1: a seat record not read stops the gate)
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '        Ok(Err(_)) | Err(_) => Err(()),' \
  '        Ok(Err(_)) | Err(_) => Ok(None),' \
  --expect-fail 'repos::tests::a_seat_record_not_read_stops_the_dgrs_gate' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_seat_record_not_read_stops_the_dgrs_gate'
# a11-n1 (f163c REVIEW-a11 N1: an archived or running seat's restart spends no fingerprint)
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '    } else if archived {' \
  '    } else if archived && runs {' \
  --expect-fail 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint'
# a11-n2 (f163c REVIEW-a11 N2: the window's first line names the persona)
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '                    .filter(|n| !n.is_empty())' \
  '                    .filter(|n| n.is_empty())' \
  --expect-fail 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other'
# a11-n1run (f163c pre-8947d325 M1: a running dgrs seat is refused by the gate, never let through)
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '    } else if runs && act == GateAct::Restart {' \
  '    } else if runs && archived && act == GateAct::Restart {' \
  --expect-fail 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint'
# a11-runact (08de5 1125, the 8cf426cd82 receipt red: only 重開 is refused on a running seat; 換模型 / 清對話 ask)
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '    } else if runs && act == GateAct::Restart {' \
  '    } else if runs && act != GateAct::Restart {' \
  --expect-fail 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_restart_refused_anyway_asks_no_fingerprint'
# a11-actrestart (重開 passes its own act: a running seat's restart is 409 with no window)
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  'asker, crate::repos::GateAct::Restart)' \
  'asker, crate::repos::GateAct::Model)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-actmodel (換模型 passes its own act: a running seat is asked, not refused)
python3 -I scripts/knife.py crates/aibd/src/seat_model.rs \
  'asker, crate::repos::GateAct::Model)' \
  'asker, crate::repos::GateAct::Restart)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-actclear (清對話 passes its own act: a running seat is asked, not refused)
python3 -I scripts/knife.py crates/aibd/src/seat_clear.rs \
  'asker, crate::repos::GateAct::Clear)' \
  'asker, crate::repos::GateAct::Restart)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
