#!/usr/bin/env bash
# aib-knives: os=unix
# §18 search knives SR1–SR6 (a9136 11-18, IFACE-search-a9136-1118; Andy 「要有一個 搜尋框 搜尋出來 要分類」):
# grouped by kind with counts, threads by category, a post's text only to the built-in page.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
sr() { python3 -I scripts/knife.py crates/aibd/src/search.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "search::tests::$3"; }
se() { python3 -I scripts/knife.py "$1" "$2" "$3" \
        --expect-fail search_groups_by_kind_and_keeps_post_text_to_the_built_in_page \
        -- cargo test -p aibd --locked --test search search_groups_by_kind_and_keeps_post_text_to_the_built_in_page; }
# SR1 a user page / carried pairing gets post text (§18 point 4)
se crates/aibd/src/search.rs \
   '    let full = words && !page.carried;' \
   '    let full = true; // knife'
# SR2 post text is not searched
se crates/aib-store/src/threads.rs \
   '        if bodies {' \
   '        if bodies && false { // knife (bodies stays used: -D warnings)'
# SR3 a thread's kind is not counted
se crates/aibd/src/search.rs \
   '                *n = json!(n.as_u64().unwrap_or(0) + 1);' \
   '                *n = json!(0); // knife'
# SR4 a group with nothing found is shown
se crates/aibd/src/search.rs \
   '    if items.is_empty() {' \
   '    if items.is_empty() && false { // knife'
# SR5 a 待人決策 item's words go to a user page
sr '            } else if full && has(&text, q) {' \
   '            } else if has(&text, q) { // knife' \
   andy_open_words_are_searched_and_shown_only_in_full
# SR6 the snippet cuts the match
sr '    let keep = q.chars().count() + SNIPPET_AROUND;' \
   '    let keep = SNIPPET_AROUND / 10; // knife' \
   a_snippet_keeps_the_text_around_the_match
# SR7 a user page gets the documents group (names of files it cannot open; 5d33a M3)
se crates/aibd/src/search.rs \
   '            if words {' \
   '            if true { // knife'
# SR8 a built-in credential is not held while a user page serves / (5d33a M1: gives_words)
se crates/aibd/src/search.rs \
   '    let words = crate::workspace::gives_words(&c).await;' \
   '    let words = !page.user_page; // knife'
