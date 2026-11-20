#!/usr/bin/env bash
# N-web-dmbridge mirror knives DM1-DM6 (7ebca 11-19, 能用 after Andy 定 04:2x; 5d33a
# BOUNDARY-webonly §5, CHECK-importdm M1 / N1 / N2): only a seat's own letter is that seat's post,
# a relay file is read only when it is a RELAY- file under the state folder, the text is cleaned
# before the page, a deleted letter is not posted, a person is hub only with a human_id (the
# answer coming back is LS6 in knives-33). Each rule, broken,
# turns its own test red. DM7: a live seat's code is never the hub. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
dl() { python3 -I scripts/knife.py crates/aibd/src/dm_import.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# DM1
dl '(text(letter, "sender_kind") == "seat" && seat_ok(s))' \
   '(seat_ok(s))' \
   dm_import::tests::only_a_seats_own_letter_is_that_seats_post
# DM2 (|| true keeps the name in use: a removed check left an unused variable, INVALID)
dl '(name.starts_with("RELAY-") && path.starts_with(state_root)' \
   '((name.starts_with("RELAY-") || true) && path.starts_with(state_root)' \
   dm_import::tests::a_long_letter_carries_its_relay_file_or_says_it_is_gone
# DM3
dl 'path.starts_with(state_root) && !p.contains("..")' \
   '(path.starts_with(state_root) || true) && !p.contains("..")' \
   dm_import::tests::a_long_letter_carries_its_relay_file_or_says_it_is_gone
# DM4
dl "if c == '\\n' || c == '\\t' || !aib_approve::text::is_bad_char(c) {" \
   'if true {' \
   dm_import::tests::a_letters_text_is_cleaned_before_the_page
# DM5
dl 'if !post_new || text(&letter, "status") == GONE {' \
   'if !post_new {' \
   dm_import::tests::a_pass_posts_a_letter_once_and_follows_its_status
# DM6
dl 'if text(letter, "sender_kind") == "human" && !text(letter, "human_id").is_empty() {' \
   'if text(letter, "sender_kind") == "human" {' \
   dm_import::tests::two_seats_share_the_thread_a9136s_import_made
# DM7 a live seat's code is taken as the hub (5d33a REVIEW-dmbridge 5e778fb6 L1)
python3 -I scripts/knife.py crates/aibd/src/bridge_cfg.rs \
   '    hub.filter(|h| !live.iter().any(|s| s == h))' \
   '    hub.filter(|h| live.iter().any(|s| s == h) || true)' \
   --expect-fail a_live_seats_code_is_not_the_hub \
   -- cargo test -p aibd --locked --lib bridge_cfg::tests::a_live_seats_code_is_not_the_hub
