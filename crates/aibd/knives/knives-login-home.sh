#!/usr/bin/env bash
# not_logged_in says whose HOME was looked in (519d0 N-pv-agree-drive, Andy at 47790)
set -u
lh() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "workspace::tests::$3"; }
# LH1 a hand-started aibd's AIBD_SEAT_HOME is not said to be ignored
lh '        "seat_home_ignored": !preview && seat_home.is_some(),' '        "seat_home_ignored": false, // knife' \
   a_missing_login_says_whose_home_and_an_ignored_seat_home
# LH2 a handed HOME outside a preview is taken as handed
lh '        "login_home": if preview && given { "handed" } else { "own" },' '        "login_home": if given { "handed" } else { "own" }, // knife' \
   a_missing_login_says_whose_home_and_an_ignored_seat_home
# LH3 rows that are open get the note too
lh '    for r in rows.iter_mut().filter(|r| r["reason"] == "not_logged_in") {' '    for r in rows.iter_mut() { // knife' \
   a_missing_login_says_whose_home_and_an_ignored_seat_home
