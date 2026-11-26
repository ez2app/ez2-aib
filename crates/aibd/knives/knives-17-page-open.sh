#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace wave 2 knives PO1–PO7 (b4297 11-18; 5d33a CHECKLIST-workspace-wave2 B): the
# built-in page's "add a seat" (act seat_open). Built-in page only, a mode other than safe needs
# this machine's switch, the page gives no folder / cabin / argv, brand and mode refused by
# aibd before a seat is written, one seat per key, the event names the page. Run from the
# repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
pi() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open "$3"; }
pu() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open_userpage "$3"; }
pt() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
pc() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
pd() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$3"; }
po() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open "$3"; }
# PO1 a user folder page opens a seat
pu '            let user_page = held_as_user_page(&page).await;' \
   '            let user_page = false && held_as_user_page(&page).await; // knife' \
   a_user_folder_page_opens_no_seat
# PO2 the page opens a dgrs seat with the switch off
pi '            let opens_safe = mode == Some("safe")' \
   '            let opens_safe = mode != Some("dgrs") // knife' \
   a_page_cannot_open_what_it_may_not
# PO3 the page gives the seat's folder, cabin or argv (they are dropped, not refused)
po '    let req: PageOpen = match serde_json::from_value(body) {' \
   '    let req: PageOpen = match serde_json::from_value({ let mut b = body; for k in ["cwd", "cabin", "argv"] { if let Some(o) = b.as_object_mut() { o.remove(k); } } b }) { // knife' \
   a_page_cannot_open_what_it_may_not
# PO4 a brand aibd cannot start is not refused before a seat is written
po '    if let Err(e) = check_open(&req) {' \
   '    if let (false, Err(e)) = (req.provider == "grok", check_open(&req)) { // knife' \
   a_page_cannot_open_what_it_may_not
# PO5 the page opens with a key outside the key rule
po '    if !key_ok {' \
   '    if !key_ok && false { // knife' \
   a_page_cannot_open_what_it_may_not
# PO6 the same key opens a second seat
po '        Some(format!("page:{}", req.idem_key)),' \
   '        Some(format!("page:{}:{}", req.idem_key, crate::guard::request_id())), // knife' \
   the_built_in_page_opens_a_safe_seat_once_per_key
# PO7 the event does not say which page opened the seat
po '    if let Some(by) = opened_by {' \
   '    if let Some(by) = opened_by.filter(|_| false) { // knife' \
   the_built_in_page_opens_a_safe_seat_once_per_key
# PO8 a pairing carried over an update opens a dgrs seat (043dd S89-pair-carry; 5d33a wave3 E1)
pc '            let carried_opens_none = page.carried;' \
   '            let carried_opens_none = false; // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
# PO9 a pairing carried over an update calls a dgrs seat in a conversation (5d33a REVIEW-pair-carry N1)
pt '        safe: user || page.carried,' \
   '        safe: user, // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
# PO10 a page opens a dgrs seat and no seat.dgrs_opened is written (5d33a wave3 E4)
pd '    let page_dgrs = opened_by.clone().filter(|_| req.mode == "dgrs").map(|by| {' \
   '    let page_dgrs = opened_by.clone().filter(|_| false).map(|by| { // knife' \
   with_the_switch_on_the_page_opens_a_dgrs_seat
# PO11 a pairing carried over an update sets an assignment (519d0 r16 裁定 3)
pc '            let carried_assigns_none = page.carried;' \
   '            let carried_assigns_none = false; // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
