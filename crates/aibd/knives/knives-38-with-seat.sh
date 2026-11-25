#!/bin/bash
# aib-knives: os=unix
# 頁面開席 with_seat knives SW1-SW5 (7ebca 11-19, 0eed7 STUCK-live-in 15:23, webdev-8h demo;
# 5d33a CHECK-preview-work-seat r2): a new work seat opens in a running work seat's own folder,
# only the folder aibd made for it and not a link; a safe seat cannot name one.
set -eo pipefail
sd() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$3"; }
ss() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open "$3"; }
# SW1 a folder swapped for a link is followed
sd '    let real_dir = std::fs::symlink_metadata(&own).is_ok_and(|md| md.file_type().is_dir());' \
   '    let real_dir = own.is_dir();' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW2 with_seat is read but the seat still gets its own folder
sd '        cwd,' \
   '        cwd: cwd.filter(|_| false),' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW3 a safe seat may name with_seat
sd '    if mode != "work" {' \
   '    if mode != "work" && false {' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW4 a dgrs seat's folder is shared
sd '            .find(|m| m.seat_id == id && m.mode.as_deref() == Some("work"))' \
   '            .find(|m| m.seat_id == id)' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW5 a seat sharing a stopped seat's folder lets a new seat into it (the folder's owner must run)
sd '    if owner != other && (!live.contains(&owner) || !id_ok(&owner)) {' \
   '    if owner != other && !id_ok(&owner) { // knife' \
  naming_a_seat_that_shares_a_folder_opens_in_its_owners_folder
# SW6 the new seat shares the named seat, not the folder's owner (joins the reviewer's items)
sd '                join_of = Some(owner.clone());' \
   '                join_of = Some(other.to_owned());' \
  naming_a_seat_that_shares_a_folder_opens_in_its_owners_folder
