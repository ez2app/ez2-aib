#!/usr/bin/env bash
# aib-idea settle knives (519d0 11-19 09:20): each rule loosened must turn its own test red.
# Run by crates/aib-knife/run-knives.sh (macOS); from the repo root; -eo pipefail like GitHub's `shell: bash`.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
st() { python3 -I scripts/knife.py crates/aib-idea/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-idea --locked --lib "tests::$3"; }
# ST1 a settled needs_human one is still asked of Andy in the footer
st '        .filter(|i| i.settle.is_none())' '        .filter(|_| true)' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST2 a settle is not kept on the idea
st '                    i.settle = Some(t.clone());' '                    let _ = &t;' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST3 an unknown outcome is taken
st '    if !OUTCOMES.iter().any(|(k, _)| *k == outcome) {' '    if false {' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST4 an idea Andy already reviewed is settled
st '        if i.review.is_some() {' '        if false {' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST5 another seat withdraws an idea that is not its own
st '        if t.outcome == "withdrawn" && t.seat != i.seat {' '        if false {' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST6 a superseded idea may name itself
st '            if ids.iter().any(|i| i == by) || l.get(by).is_none() {' '            if l.get(by).is_none() {' \
   a_settled_one_leaves_the_footer_but_stays_andys_to_review
# ST7 a commit nobody knows counts as evidence
st '        .find(|w| exists(w))' '        .next()' \
   done_evidence_is_a_known_commit_or_an_existing_path
