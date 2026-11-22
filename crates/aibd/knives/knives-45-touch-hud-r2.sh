#!/usr/bin/env bash
# TH1–TH6 (3e53b 11-23; ORDER-touchhud-ebab8-3e53b-1123, Andy「1+2 都可以」): the countdown's script
# never runs its words; the system window is four lines and only the object is ever cut; an object
# cannot say the window's own code line; open-for comes only from the fixed table.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
H=touch_hud::tests::the_script_is_fixed_and_the_words_are_only_arguments
F=owner_ok::tests::the_window_has_four_lines_and_only_the_object_is_cut
O=owner_ok::tests::the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
# TH1 the countdown evaluates the action it shows
python3 -I scripts/knife.py crates/aibd/src/touch_hud.rs '  act.stringValue = words;' '  act.stringValue = eval(words); // knife' \
  --expect-fail "$H" -- cargo test -p aibd --locked --lib "$H"
# TH2 the object and the code share a line
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '"{}\n對象:{}\n對照碼 {} · {until},過了這張就作廢{open}\n單號 {}|參數 {}|{}",' '"{}\n對象:{} 對照碼 {} · {until},過了這張就作廢{open}\n單號 {}|參數 {}|{}", // knife' \
  --expect-fail "$F" -- cargo test -p aibd --locked --lib "$F"
# TH3 an object may write a code of its own
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    ("對照碼", "對·照碼"),' '    ("對照碼", "對照碼"), // knife' \
  --expect-fail "$O" -- cargo test -p aibd --locked --lib "$O"
# TH4 any open_for word is shown
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    OPEN_FOR.iter().find(|(k, _)| *k == w).map(|(_, s)| *s)' '    Some(Box::leak(w.to_owned().into_boxed_str())) // knife' \
  --expect-fail "$F" -- cargo test -p aibd --locked --lib "$F"
# TH5 the object line is never cut
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '    if c.len() <= RESOURCE_MAX {' '    if true { // knife' \
  --expect-fail "$F" -- cargo test -p aibd --locked --lib "$F"
# TH6 where it stands reads what another window shows (its title), not only owner and bounds
python3 -I scripts/knife.py crates/aibd/src/touch_hud.rs "      if (o.kCGWindowOwnerName === 'coreautha' && o.kCGWindowLayer >= 1000" "      if (String(o.kCGWindowName) !== 'x' && o.kCGWindowOwnerName === 'coreautha' && o.kCGWindowLayer >= 1000" \
  --expect-fail "$H" -- cargo test -p aibd --locked --lib "$H"
