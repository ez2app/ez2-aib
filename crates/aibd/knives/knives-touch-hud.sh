#!/usr/bin/env bash
# aib-knives: os=unix
# HU1–HU4: the countdown beside the fingerprint window only shows; its script is fixed, its words are arguments, it ends with the window, and it carries the ticket's own code (DESIGN-touch-countdown-7b81e-1121; Andy 11-21 01:42; 5d33a PREREAD-touchcountdown 3, L1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
hu() { python3 -I scripts/knife.py crates/aibd/src/touch_hud.rs "$1" "$2" --expect-fail "touch_hud::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch_hud::tests::$3"; }
ho() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
# HU1 the words are put into the script
hu '        .arg(SCRIPT)' \
   '        .arg(format!("{SCRIPT}//{words}"))' \
   the_script_is_fixed_and_the_words_are_only_arguments
# HU2 the window inherits the environment
hu '        .env_clear()' \
   '        .env("AIBD_KNIFE", "1")' \
   the_script_is_fixed_and_the_words_are_only_arguments
# HU3 the window is not ended with the fingerprint window
hu '            let _ = child.kill();' \
   '            let _ = child.id();' \
   the_window_ends_with_its_owner_and_a_failed_start_is_nothing
# HU4 one code for every ticket
ho '    params_sha256(ticket.as_bytes())[..4].to_ascii_uppercase()' \
   '    params_sha256(&ticket.as_bytes()[..0])[..4].to_ascii_uppercase()' \
   the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead
