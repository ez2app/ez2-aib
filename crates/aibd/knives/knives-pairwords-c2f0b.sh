#!/usr/bin/env bash
# aib-knives: os=unix
# W1–W4 pairing window words (3e53b TRIAGE-nohello-confirmed-3e53b-1126 r5, 11-26: the customer typed the PIN
# into two Hello windows a remote script raised): the window first asks whether you started it and says to
# cancel when not, asks to compare the code with your page's, and names the true source. Every knife keeps
# the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
tw() { python3 -I scripts/knife.py crates/aibd/src/touch.rs "$1" "$2" --expect-fail "touch::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch::tests::$3" || fail=1; }
# W1 the first line no longer says to cancel when you did not start it
tw '        Who::Terminal | Who::Page => "你剛在 AIB 頁面按了配對嗎?沒有就按取消",' '        Who::Terminal | Who::Page => "你剛在 AIB 頁面按了配對嗎?",' a_pairing_window_asks_first_whether_you_started_it
# W2 the code line no longer says to cancel when the page shows another code
tw '        "{}\n你的頁面也要顯示 {code},不一樣就按取消 · {until},過了這張就作廢\n{}(對象:{})\n{}",' '        "{}\n對照碼 {code} · {until},過了這張就作廢\n{}(對象:{})\n{}",' a_pairing_window_asks_first_whether_you_started_it
# W3 the window says a page asked when a terminal or a program did
tw '            "aibd 配對網頁:短碼 {short_code};給 {to};由終端機 / 程式要求。{OWNER_CHECK_WORD}"' '            "aibd 配對網頁:短碼 {short_code};給 {to};由瀏覽器頁要求。{OWNER_CHECK_WORD}"' the_window_says_the_code_where_it_goes_and_who_asked
# W4 the question is not the first line
tw '        ask_first(who),' '        { let _ = ask_first(who); plain(who, user_page) },' a_pairing_window_asks_first_whether_you_started_it
exit "$fail"
