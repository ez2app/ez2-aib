#!/usr/bin/env bash
# TW1–TW5: the fingerprint window says what is done, to what and until when; no object can say it instead (DESIGN-touch-words-7b81e-1121; Andy 11-21 01:23; 5d33a PREREAD-touchwords L1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
tw() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
# TW1 an object keeps the window's own quote marks
tw '        .map(|c| if OBJECT_MARKS.contains(&c) { '"'"' '"'"' } else { c })' \
   '        .map(|c| if OBJECT_MARKS.contains(&c) && c == '"'"'|'"'"' { '"'"' '"'"' } else { c })' \
   the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
# TW2 an object shows a clock
tw '            kept[i] = '"'"'·'"'"';' \
   '            kept[i] = '"'"':'"'"';' \
   the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
# TW3 an object says the window's own 請在
tw '    let text = kept.iter().collect::<String>().replace("請在", "請·在");' \
   '    let text = kept.iter().collect::<String>().replace("請在", "請在");' \
   the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
# TW4 an operation the table does not have is shown as nothing
tw '        .map_or(operation, |(_, words)| *words)' \
   '        .map_or("", |(_, words)| *words)' \
   the_window_shows_the_cleaned_end_of_the_resource_and_the_params_hash
# TW5 a long object loses its start (the name)
tw '    let head: String = c[..OBJECT_HEAD].iter().collect();' \
   '    let head: String = c[..OBJECT_HEAD - OBJECT_HEAD].iter().collect();' \
   the_window_shows_the_cleaned_end_of_the_resource_and_the_params_hash
