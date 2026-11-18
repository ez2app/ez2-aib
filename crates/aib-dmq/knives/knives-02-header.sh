#!/usr/bin/env bash
# B1dm-header knives DH1–DH5 (b4297 11-18, 519d0 07:34): the dm header translated from
# dm_header.py builds and reads the same lines. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
dh() { python3 -I scripts/knife.py crates/aib-dmq/src/header.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-dmq --locked --test header "$3"; }
# DH1 a header glued to the next word is read as a header
dh "        if rest.is_empty() || rest.starts_with(' ') || rest.starts_with('\n') {" \
   "        if rest.is_empty() || rest.starts_with(' ') || rest.starts_with('\n') || true { // knife" \
   the_same_answers_as_the_python_header
# DH2 系統@aib is not the system
dh '    if code == SYSTEM_CODE && sender.name == SYSTEM_NAME {' \
   '    if code == SYSTEM_CODE && sender.name == SYSTEM_NAME && false { // knife' \
   the_sender_kind_comes_from_the_third_part
# DH3 across machines the main hub's cabin is blank
dh '            _ if sock == "aihub" => "main".to_owned(),' \
   '            _ if sock == "aihub" => String::new(), // knife' \
   an_unknown_sender_cabin_is_blank_not_the_receivers
# DH4 an old header's 台北 stays without 時間
dh '    let tz = if tz.is_empty() || tz.ends_with("時間") {' \
   '    let tz = if true || tz.ends_with("時間") { // knife' \
   the_same_answers_as_the_python_header
# DH5 a person's [a > b] without a time is read as a dm
dh '    if when.is_empty() && (sender.code.is_empty() || receiver.code.is_empty()) {' \
   '    if when.is_empty() && (sender.code.is_empty() || receiver.code.is_empty()) && false { // knife' \
   the_same_answers_as_the_python_header
