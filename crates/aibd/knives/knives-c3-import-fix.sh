#!/usr/bin/env bash
# N-webonly-c3 (a9136 11-20): the new import's sender, threads and undo, after 0eed7
# REVIEW-integ2-ui daaafb770 H1 M1-M5 L2-L7. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
rg() { python3 -I scripts/knife.py crates/aibd/src/import_regroup.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import_regroup::tests::$3"; }
mo() { python3 -I scripts/knife.py crates/aibd/src/import_more.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import_more::tests::$3"; }
# IH1 an idea is sent as the seat it says recorded it
rg '            from: "seat:import".into(),' '            from: format!("seat:{seat}"), // knife' \
   each_idea_is_one_post_on_its_card_or_in_its_seats_bucket
# IM1 a bucket thread carries its card's node
rg '                        let bucket_node: Option<String> = None;' \
   '                        let bucket_node: Option<String> = card.map(str::to_owned); // knife' \
   a_buckets_thread_is_not_its_cards_and_undo_finds_every_post
# IM2 undo finds nothing without routing
rg '        .threads_with_keys(crate::import::OWNER, KEY_PREFIX)' \
   '        .threads_with_keys(crate::import::OWNER, "rg:none:") // knife' \
   a_buckets_thread_is_not_its_cards_and_undo_finds_every_post
# IM3 a mail only quoting a relay file is replaced by it
mo '    if !msg.trim_start().starts_with(RELAY_OPENING) {' '    if false { // knife' \
   only_a_relay_mail_is_replaced_by_its_file
# IL4 a Windows path is not cut at its backslashes
mo "        .filter_map(|w| w.rsplit(['/', '\\\\']).next())" "        .filter_map(|w| w.rsplit('/').next()) // knife" \
   only_a_relay_mail_is_replaced_by_its_file
# IM4 a label with two seats names the last one
python3 -I scripts/knife.py crates/aibd/src/import_regroup.rs \
   "    if label.matches('@').count() != 1 {" '    if false { // knife' \
   --expect-fail a_seat_named_only_in_its_label_sends_and_pairs_as_that_seat \
   -- cargo test -p aibd --locked --lib import_more::tests::a_seat_named_only_in_its_label_sends_and_pairs_as_that_seat
# IM5 the pair is named from the sender seat field only
mo '                    .or_else(|| seat_code(field("sender_seat")))' \
   '                    .and(seat_code(field("sender_seat"))) // knife' \
   a_seat_named_only_in_its_label_sends_and_pairs_as_that_seat
# IL6 a link or a huge ledger file is read
mo '    if !m.file_type().is_file() || m.len() > READ_MAX {' '    if false { // knife' \
   a_ledger_link_or_a_huge_file_is_not_read
# IL2 a status line's words go on the page as written
rg '            let item = im::clean(&e.item);' '            let item = e.item.clone(); // knife' \
   status_line_records_are_cleaned_keyed_by_content_and_whole_days_only
# IL3a a judgement's key is its place in the history
rg '                key: format!("judgement:{lid}"),' '                key: format!("judgement:{seat}:{}:{i}", e.seen), // knife' \
   status_line_records_are_cleaned_keyed_by_content_and_whole_days_only
# IL3b a day still going gets its row
rg '            if day.as_str() >= today {' '            if day.as_str() > today { // knife' \
   status_line_records_are_cleaned_keyed_by_content_and_whole_days_only
