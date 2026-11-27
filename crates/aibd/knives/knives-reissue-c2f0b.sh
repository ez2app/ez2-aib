#!/usr/bin/env bash
# aib-knives: os=unix
# R1–R7 the code page's 「重新取得配對碼」 (Andy 11-27 10:18; ebab8 dispatch; 3e53b PRE-READ-pair-reissue-3e53b-1127):
# our own Origin only (no Origin refused), a browser's Sec-Fetch-Site same-origin, only where the owner's window
# confirms, one page-asked code alive, at most REISSUE_MAX in REISSUE_WINDOW, and the window says the page asked.
# Each knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
k() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
it() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test pairing "$4" || fail=1; }
# R1 the reissue takes any Origin, or none
it guard.rs '    if path == PAIR_REDEEM || path == PAIR_NEXT || path == PAIR_PREVIEW || path == PAIR_REISSUE {' '    if path == PAIR_REDEEM || path == PAIR_NEXT || path == PAIR_PREVIEW {' a_new_code_for_the_page_needs_our_own_origin_and_the_owners_window
# R2 a cross-site Sec-Fetch-Site is taken
it pair_api.rs '        [v] => v.as_bytes() == b"same-origin",' '        [_v] => true,' a_new_code_for_the_page_needs_our_own_origin_and_the_owners_window
# R3 a code for the page where the terminal's y confirms
k pairing.rs '        if self.channel != ConfirmChannel::TouchId {' '        if self.channel == ConfirmChannel::Off {' pairing::tests::a_page_asked_code_is_the_owners_window_only_one_alive_and_limited
# R4 the page-asked code is not marked, so its window says the terminal asked
k pairing.rs '            p.by_page = true;' '            p.by_page = false;' pairing::tests::a_page_asked_code_is_the_owners_window_only_one_alive_and_limited
# R5 the page-asked code before the new one still redeems
k pairing.rs '            .filter(|(id, p)| p.by_page && p.stored == Stored::Issued && **id != n.pair_id)' '            .filter(|(id, p)| false && p.by_page && p.stored == Stored::Issued && **id != n.pair_id)' pairing::tests::a_page_asked_code_is_the_owners_window_only_one_alive_and_limited
# R6 no limit on page-asked codes
k pairing.rs '            if asks.len() >= REISSUE_MAX {' '            if asks.len() >= REISSUE_MAX * 9 {' pairing::tests::a_page_asked_code_is_the_owners_window_only_one_alive_and_limited
# R7 the window says a terminal asked for the page's code
k touch.rs '            "aibd 配對網頁:短碼 {short_code};給 {to};由瀏覽器頁要求(重新取得配對碼)。{OWNER_CHECK_WORD}"' '            "aibd 配對網頁:短碼 {short_code};給 {to};由終端機 / 程式要求。{OWNER_CHECK_WORD}"' pairing::tests::a_page_asked_code_is_the_owners_window_only_one_alive_and_limited
exit "$fail"
