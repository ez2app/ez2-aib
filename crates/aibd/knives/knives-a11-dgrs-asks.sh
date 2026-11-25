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
  'if let Some(r) = crate::repos::dgrs_restart_gate(&st, seat_id, ticket, asker).await {' \
  'if let Some(r) = crate::repos::dgrs_restart_gate(&st, "none", ticket, asker).await {' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-model
python3 -I scripts/knife.py crates/aibd/src/seat_model.rs \
  'dgrs_restart_gate(&st, &seat_id, ticket, asker)' \
  'dgrs_restart_gate(&st, "none", ticket, asker)' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-clear
python3 -I scripts/knife.py crates/aibd/src/seat_clear.rs \
  'if let Some(r) = crate::repos::dgrs_restart_gate(&st, seat_id, ticket, asker).await {' \
  'if let Some(r) = crate::repos::dgrs_restart_gate(&st, "none", ticket, asker).await {' \
  --expect-fail 'a_dgrs_seat_starts_from_the_page_only_through_the_owner' \
  -- cargo test -p aibd --locked --test seats_page_dgrs_asks 'a_dgrs_seat_starts_from_the_page_only_through_the_owner'
# a11-safe
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '    if meta.mode.as_deref() != Some("dgrs") {' \
  '    if false {' \
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
