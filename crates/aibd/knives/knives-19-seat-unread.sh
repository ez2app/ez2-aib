#!/usr/bin/env bash
# M9b knives SU1–SU2 (a9136 11-18; 0eed7 VERIFY-autotest 3a2d6b09, 519d0 r16 裁定 5): the page's
# seat list carries each seat's messages to the person not read yet. Each rule, broken, turns
# its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
su() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# SU1 the count is not put on the seat
su '            o.insert("unread".into(), serde_json::json!(m.unread));' \
   '            o.insert("unread".into(), serde_json::json!(m.unread.min(0))); // knife (m stays used: -D warnings)' \
   the_seat_list_shows_what_each_seat_left_unread_for_the_person
# SU2 the count is of every message of the seat, not those to this person
su '        let m = crate::contacts::message_counts(msgs, &format!("seat:{id}"), Some(page));' \
   '        let m = crate::contacts::message_counts(msgs, &format!("seat:{id}"), Some(page).filter(|_| false)); // knife (page stays used)' \
   the_seat_list_shows_what_each_seat_left_unread_for_the_person
