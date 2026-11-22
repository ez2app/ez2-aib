#!/usr/bin/env bash
# HL1–HL3 (3e53b 11-23; f163c REVIEW-touchhud M1, c2f0b 06:07): the system window's third line says how
# long only from grants::hold_in, in grants' own words, and nothing for a hold the operation refuses.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# HL1 the third line uses other words than grants' own
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '        .map(|h| format!(" · {}", h.words()))' '        .map(|h| format!(" · {}", h.word())) // knife' \
  --expect-fail owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved -- cargo test -p aibd --locked --lib owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved
# HL2 the hold is looked up for another operation than the one asked
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    let hold = crate::grants::hold_in(ask.operation, &ask.params)' '    let hold = crate::grants::hold_in(crate::grants::OP_FS_OPEN, &ask.params) // knife' \
  --expect-fail owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved -- cargo test -p aibd --locked --lib owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved
# HL3 the hold is not said at all
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '        "{}\n對象:{}\n對照碼 {} · {until},過了這張就作廢{hold}\n單號 {}|參數 {}|{}",' '        "{}\n對象:{}\n對照碼 {} · {until},過了這張就作廢{hold:.0}\n單號 {}|參數 {}|{}", // knife' \
  --expect-fail owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved -- cargo test -p aibd --locked --lib owner_ok::tests::the_hold_is_in_the_window_and_in_what_was_approved
