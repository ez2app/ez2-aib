#!/usr/bin/env bash
# aib-knives: os=unix
# TP1–TP4: pairing's fingerprint windows say it plainly first, until when and the code beside them, then what they always said (Andy 11-21 01:42, 01:44; 519d0 05:49)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
tt() { python3 -I scripts/knife.py crates/aibd/src/touch.rs "$1" "$2" --expect-fail "touch::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch::tests::$3"; }
# TP1 the code on top is not the one given (the countdown beside would show another)
tt '        "{}\n對象:{}\n對照碼 {code} · {until},過了這張就作廢\n{}",' \
   '        "{}\n對象:{}\n對照碼 {code:.0}{short_code} · {until},過了這張就作廢\n{}",' \
   a_pairing_window_says_it_plainly_first_and_then_what_it_always_said
# TP2 the top line says the old words instead of plain ones
tt '        plain(who, user_page),' \
   '        reason(who, short_code, user_page),' \
   a_pairing_window_says_it_plainly_first_and_then_what_it_always_said
# TP3 the pairing sentence does not say which page it is for
tt '        Who::Terminal => format!("讓這個瀏覽器分頁配對成 AIB 的{to}"),' \
   '        Who::Terminal => format!("讓這個瀏覽器分頁配對成 AIB 的頁{}", &to[..0]),' \
   a_pairing_window_says_it_plainly_first_and_then_what_it_always_said
# TP4 (ebab8 11-23 05:47, four lines as an approval's) the second line names another page than the one paired
tt '        Who::Reconfirm { .. } => format!("這一頁 → {to}"),' \
   '        Who::Reconfirm { .. } => format!("這一頁 → 內建頁{}", &to[..0]),' \
   a_pairing_window_says_it_plainly_first_and_then_what_it_always_said
