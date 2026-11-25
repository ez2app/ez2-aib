#!/usr/bin/env bash
# TH1–TH6 (3e53b 11-23; ORDER-touchhud-ebab8-3e53b-1123, Andy「1+2 都可以」): the countdown's script
# never runs its words; the system window is four lines and only the object is ever cut; an object
# cannot say the window's own code line; no line says how long from the request.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# TH1 the countdown evaluates the action it shows
python3 -I scripts/knife.py crates/aibd/src/touch_hud.rs '  act.stringValue = words;' '  act.stringValue = eval(words); // knife' \
  --expect-fail touch_hud::tests::the_script_is_fixed_and_the_words_are_only_arguments -- cargo test -p aibd --locked --lib touch_hud::tests::the_script_is_fixed_and_the_words_are_only_arguments
# TH2 the object and the code share a line
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '"{}\n對象:{}\n{}\n單號 {}|參數 {}|{}",' '"{}\n對象:{} {}\n單號 {}|參數 {}|{}", // knife' \
  --expect-fail owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut -- cargo test -p aibd --locked --lib owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut
# TH3 an object may write a code of its own
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    ("對照碼", "對·照碼"),' '    ("對照碼", "對照碼"), // knife' \
  --expect-fail owner_ok::tests::the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead -- cargo test -p aibd --locked --lib owner_ok::tests::the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
# TH4 (f163c REVIEW-touchhud M1) the window says how long from the request's own words
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    let p = params_sha256(&ask.params);' '    let p = params_sha256(&ask.params); let until = &format!("{until}{}", if ask.params.windows(8).any(|w| w == b"open_for") { " · 開放 15 分鐘" } else { "" }); // knife' \
  --expect-fail owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut -- cargo test -p aibd --locked --lib owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut
# TH5 the object line is never cut
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    if c.len() <= RESOURCE_MAX {' '    if true { // knife' \
  --expect-fail owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut -- cargo test -p aibd --locked --lib owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut
# TH6 where it stands reads what another window shows (its title), not only owner and bounds
# (f163c N1: this bites any change of the script's text, not the behaviour; not evidence for it)
python3 -I scripts/knife.py crates/aibd/src/touch_hud.rs "      if (o.kCGWindowOwnerName === 'coreautha' && o.kCGWindowLayer >= 1000" "      if (String(o.kCGWindowName) !== 'x' && o.kCGWindowOwnerName === 'coreautha' && o.kCGWindowLayer >= 1000" \
  --expect-fail touch_hud::tests::the_script_is_fixed_and_the_words_are_only_arguments -- cargo test -p aibd --locked --lib touch_hud::tests::the_script_is_fixed_and_the_words_are_only_arguments
