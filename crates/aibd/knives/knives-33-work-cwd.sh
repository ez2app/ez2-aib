#!/usr/bin/env bash
# a work seat's folder (a9136 11-19, 5d33a M1 on ecf54f91d, CHECK-preview-work-seat r2 §4):
# under <data>/seat-work by its real path, never a link. Each rule, broken, turns its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
wc() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seats::tests::$3"; }
# WC1 a link in a work seat's folder's place is followed
wc '    if std::fs::symlink_metadata(p).map_or(true, |m| m.file_type().is_symlink()) {' \
   '    if std::fs::symlink_metadata(p).is_err() { // knife' \
   a_work_seat_folder_is_under_seat_work_and_not_a_link
# WC2 a work seat's path may be anywhere
wc '    if real == base || !real.starts_with(&base) {' '    if real == base { // knife' \
   a_work_seat_folder_is_under_seat_work_and_not_a_link
# WC3 no seat.work_opened is written (5d33a CHECK-preview-work-seat r2 b)
wd() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$3"; }
wd '    let page_work = opened_by.clone().filter(|_| req.mode == "work").map(|by| {' \
   '    let page_work = opened_by.clone().filter(|_| false).map(|by| { // knife' \
   a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
