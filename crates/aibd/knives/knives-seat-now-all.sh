#!/usr/bin/env bash
# NA1-NA8 a seat reads the other seats' status lines (DESIGN-seat-now-all-a9136-1121; 5d33a
# PREREAD-seatnowall 0/0/1/1): trusted now and named in now_all_seats, the item alone as `said`,
# cleaned, one ask every 10 s, journaled without words.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
na() { python3 -I scripts/knife.py crates/aibd/src/seat_now_all.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked $4 "$3"; }
# NA1 a seat not trusted now reads
na '    if !crate::trusted_seats::holds(k.trusted_until, now) {' \
   '    if !crate::trusted_seats::holds(k.trusted_until, now) && false {' \
   seat_now_all::tests::only_a_trusted_listed_safe_or_work_seat_reads_the_other_seats_lines --lib
# NA2 a trusted seat not on the list reads (the fingerprint alone opens it)
na '    if !k.listed {' \
   '    if !k.listed && false {' \
   seat_now_all::tests::only_a_trusted_listed_safe_or_work_seat_reads_the_other_seats_lines --lib
# NA3 the switch off still reads
na '    if !k.switch_on {' \
   '    if !k.switch_on && false {' \
   seat_now_all::tests::only_a_trusted_listed_safe_or_work_seat_reads_the_other_seats_lines --lib
# NA4 a dgrs seat reads
na '    if !matches!(k.mode, Some("safe" | "work")) {' \
   '    if !matches!(k.mode, Some("safe" | "work" | "dgrs")) {' \
   seat_now_all::tests::only_a_trusted_listed_safe_or_work_seat_reads_the_other_seats_lines --lib
# NA5 a line's why comes back
na '                "said": said,' \
   '                "said": said, "why": n.why,' \
   seat_now_all::tests::a_line_comes_back_as_said_cleaned_and_without_why_or_idea --lib
# NA6 said is not cleaned
na '            let said: String = crate::dm_import::clean(&n.item)' \
   '            let said: String = n.item.clone()' \
   seat_now_all::tests::a_line_comes_back_as_said_cleaned_and_without_why_or_idea --lib
# NA7 a seat asks again inside the gap
na '        if self.0.contains_key(seat) {' \
   '        if self.0.contains_key(seat) && false {' \
   seat_now_all::tests::one_ask_per_seat_inside_the_gap --lib
# NA8 a prefix of a listed id is listed
na '        .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(seat_id)))' \
   '        .is_some_and(|a| a.iter().any(|x| x.as_str().is_some_and(|s| seat_id.starts_with(s))))' \
   seat_now_all::tests::the_list_names_whole_seat_ids_and_is_empty_by_default --lib
