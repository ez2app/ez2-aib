#!/usr/bin/env bash
# `aibd ctl web` / `aib web` knives (ADR 0006 §7 W1, PLAN A5): each breaks one rule and must
# turn its own test red (scripts/knife.py checks the failed test's name). Run from the repo root.
set -eu
# FILE is written out on every line (not a variable): aib-xtask knife-anchors reads the
# anchors before the push and only understands plain text and $1..$9 (#590).
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }

# W1 the pairing is confirmed whatever the person answers
k crates/aibd/src/ctl.rs '    if answer.eq_ignore_ascii_case("y") || answer.eq_ignore_ascii_case("yes") {' \
       '    if true { // knife' \
  web_cancels_on_anything_but_yes
# W2 web does not wait for the page to redeem the code
k crates/aibd/src/ctl.rs '            Some("issued") if Instant::now() < deadline => std::thread::sleep(poll),' \
       '            Some("issued") if false => std::thread::sleep(poll), // knife' \
  web_waits_for_the_page_and_confirms_only_after_a_yes
# W3 an ended pairing (expired / revoked) is treated as waiting for confirmation
k crates/aibd/src/ctl.rs '            Some("awaiting_confirmation") => {' \
       '            Some("awaiting_confirmation") | Some("expired") | Some("revoked") | Some("confirmed") => { // knife' \
  web_never_confirms_an_ended_pairing
# W4 pairing off is reported as a bare HTTP error, without how to turn it on
k crates/aibd/src/ctl.rs '        if e.starts_with("HTTP 404") {' '        if false { // knife' \
  web_says_how_to_turn_pairing_on
# W5 health stops telling a restarted aibd apart
k crates/aibd/src/api.rs '        "instance_id": PROCESS.instance_id,' '        // knife: no instance_id' \
  health_is_public_and_minimal
echo "web knives: 5/5 bite"
