#!/bin/bash
# FINDING-andy-realrun-519d0-1120 1 knives JN1-JN3 (7ebca 11-20): a seat the page opens in
# another work seat's folder is put on that seat's items as observer, said by with_seat, so it
# is in their threads.
set -eo pipefail
jn() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$4"; }
# JN1 the new seat is not put on the other's items (Andy's reviewer was not in the thread)
jn crates/aibd/src/seats.rs '                crate::assign::join_nodes(&st, other, &seat_id, by).await;' \
   '                let _ = (other, by); // knife' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN2 it is put on them as more than a reader
jn crates/aibd/src/assign.rs '                role: "observer".into(),' \
   '                role: "author".into(),' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
# JN3 the row does not say it came from with_seat
jn crates/aibd/src/seats.rs '                let by = format!("with_seat:{}", seat_code(other));' \
   '                let by = format!("page:{}", seat_code(other));' a_seat_opened_in_another_seats_folder_is_on_that_seats_items
