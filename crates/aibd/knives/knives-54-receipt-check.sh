#!/usr/bin/env bash
# N-webonly 第 4 件 knives RCK1–RCK12 (7ebca 11-20): aibd reads a receipt's .out key by key
# (seal, head, every step's rc, tests total, knives sent = expected = bit, no red line).
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
rc() { python3 -I scripts/knife.py crates/aibd/src/receipt_check.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "receipt_check::tests::$3"; }
# RCK1 a seal that names another digest passes
rc '        Some((body, named)) if digest(body) != named => no("seal_mismatch"),' \
   '        Some((body, named)) if digest(body) != named && body.is_empty() => no("seal_mismatch"), // knife' \
   the_seal_is_the_digest_of_what_is_before_its_line
# RCK2 a file with no seal line passes
rc '        None => no("seal_missing"),' \
   '        None => {} // knife' \
   the_seal_is_the_digest_of_what_is_before_its_line
# RCK3 a receipt for another head passes
rc '        hs if hs.iter().any(|h| *h != head) => no("head_other"),' \
   '        hs if hs.iter().any(|h| *h != head) && hs.is_empty() => no("head_other"), // knife' \
   a_receipt_counts_only_for_the_head_asked_for
# RCK4 a tree that moved while the receipt ran passes
rc '        es if es.iter().any(|h| *h != head) => no("head_moved"),' \
   '        es if es.iter().any(|h| *h != head) && es.is_empty() => no("head_moved"), // knife' \
   a_receipt_counts_only_for_the_head_asked_for
# RCK5 a step that is not zero passes (restored 101, 11-20)
rc '        } else if got.iter().any(|v| *v != "0") {' \
   '        } else if got.iter().all(|v| *v != "0") { // knife' \
   every_step_is_there_and_zero
# RCK6 a step that is not there passes
rc '        if got.is_empty() {' \
   '        if got.is_empty() && step.is_empty() { // knife' \
   every_step_is_there_and_zero
# RCK7 failed tests pass
rc '        (1, Some(t)) if t.failed != 0 => no("tests_failed"),' \
   '        (1, Some(t)) if t.failed != 0 && t.passed == 0 => no("tests_failed"), // knife' \
   the_tests_total_says_none_failed
# RCK8 a receipt that ran fewer knives than aibd sent passes
rc '            if expected != knives_sent {' \
   '            if expected > knives_sent { // knife' \
   every_knife_sent_bit
# RCK9 a knife that missed passes
rc '            if misses != 0 {' \
   '            if misses > 1 { // knife' \
   every_knife_sent_bit
# RCK10 a count that is there twice with two values passes
rc '    if v.iter().any(|x| x != first) {' \
   '    if v.iter().all(|x| x != first) { // knife' \
   every_knife_sent_bit
# RCK11 KNIVES-OK is not asked for
rc '        if !lines.contains(&"KNIVES-OK") {' \
   '        if !lines.contains(&"KNIVES-OK") && lines.is_empty() { // knife' \
   every_knife_sent_bit
# RCK12 a red line passes
rc '            no(&format!("red_line:{red}"));' \
   '            let _ = red; // knife' \
   a_red_or_narrow_line_is_never_green
