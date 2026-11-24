#!/bin/bash
# aib-knives: os=unix
# FINDING-andy-realrun-519d0-1120 1 knives JN1-JN3 (7ebca 11-20): a seat the page opens in
# another work seat's folder is put on that seat's items as observer, said by with_seat, so it
# is in their threads.
set -eo pipefail
jn() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$4"; }
# JN1 the new seat is not put on the other's items (Andy's reviewer was not in the thread)
jn crates/aibd/src/seats.rs '                crate::assign::join_nodes(&st, other, &seat_id, by, req.join_role).await;' \
   '                let _ = (other, by, req.join_role); // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN2 it is put on them as more than a reader
jn crates/aibd/src/assign.rs '        "observer"' \
   '        "author"' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN3 the row does not say it came from with_seat
jn crates/aibd/src/seats.rs '                let by = format!("with_seat:{}", seat_code(other));' \
   '                let by = format!("page:{}", seat_code(other));' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN4 a seat the page opens as the reviewer joins only as observer (cannot read the author's tree)
jn crates/aibd/src/assign.rs '    if asked == Some("reviewer") {' \
   '    if asked == Some("reviewer") && false { // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN5 any role the page names comes in by with_seat
jn crates/aibd/src/assign.rs '    if asked == Some("reviewer") {' \
   '    if asked.is_some() { // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN6 the items the other seat only watches are passed on too (5d33a N2: with_seat chains spread)
jn crates/aibd/src/assign.rs '    let own = |r: &AssignRow| r.role != "observer" && !r.by_key.starts_with("with_seat:");' \
   '    let own = |r: &AssignRow| !r.by_key.is_empty(); // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN7 (a9136 11-20, 519d0 VERIFY-selftest H1: with_seat now resolves to the folder's owner, so
# the named seat's with_seat rows are never read; the filter in join_nodes stays as a guard)
# naming a sharer joins the sharer, not the folder's owner: the chained seat lands on nothing.
# 7ebca 11-20: the chained seat opens with own_tree since 99f6351b2b (the folder holds two
# writers), so the knife cuts the own_tree arm; the plain arm only ever names the owner there
jn crates/aibd/src/seats.rs '                join_of = Some(owner.clone());' \
   '                join_of = Some(other.to_owned()); // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
