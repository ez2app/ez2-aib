#!/usr/bin/env bash
# S68d gh escape sequences (043dd 11-17, 5d33a CHECK-s68d-round1-5d33a-1f9a749c.md): a newer gh refuses a
# job log with terminal escape sequences; GhCli reads it once more with --allow-escape-sequences and
# strips the escapes. Each rule, broken, turns its own test red. The "aib-cireuse knives (macOS)" step
# runs every crates/aib-cireuse/knives/*.sh in name order; aib-xtask knife-anchors reads them.
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gk() { python3 -I scripts/knife.py "crates/aib-cireuse/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-cireuse --locked --test gh_escapes "$4"; }
# GE1 the refusal is not read again
gk gh.rs '        Err(e) if e.contains(ALLOW_ESCAPES) => {' '        Err(e) if false && e.contains(ALLOW_ESCAPES) => { // knife' \
   a_refused_log_is_read_again_with_the_flag_and_comes_back_plain
# GE2 the second read does not carry the flag
gk gh.rs '            args.push(ALLOW_ESCAPES.to_string());' '            args.push(String::new()); // knife' \
   a_refused_log_is_read_again_with_the_flag_and_comes_back_plain
# GE3 the body keeps its escape sequences
gk gh.rs '    Ok(strip_escapes(&String::from_utf8_lossy(&out)))' '    Ok(String::from_utf8_lossy(&out).into_owned()) // knife' \
   a_refused_log_is_read_again_with_the_flag_and_comes_back_plain
# GE4 a colour sequence is not ended by its final byte
gk gh.rs "                    if ('@'..='~').contains(&d) {" "                    if d == '~' { // knife" \
   colour_title_and_two_byte_escapes_are_dropped_and_the_text_kept
# GE5 a title sequence is not ended by BEL
gk gh.rs "                    if d == '\\u{7}' {" "                    if d == '\\u{0}' { // knife" \
   colour_title_and_two_byte_escapes_are_dropped_and_the_text_kept
