#!/usr/bin/env bash
# aib-knives: os=unix
# 0eed7 VERIFY-t4-4cfc63d5: a reviewer of an author on no node gets a node of the pair
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# RP1 the reviewer is put on nothing
k crates/aibd/src/assign.rs '    if nodes.is_empty() && role == "reviewer" {' \
     '    if nodes.is_empty() && role == "never" { // knife' \
     a_reviewer_of_a_seat_on_no_node_gets_a_node_of_the_pair
# RP2 the author is left off the pair's node (reviews() needs both)
k crates/aibd/src/assign.rs '            (node.clone(), from.to_owned(), "author"),' \
     '            (node.clone(), to.to_owned(), "author"), // knife' \
     a_reviewer_of_a_seat_on_no_node_gets_a_node_of_the_pair
# RP3 an observer gets a node too
k crates/aibd/src/assign.rs '    if nodes.is_empty() && role == "reviewer" {' \
     '    if nodes.is_empty() { // knife' \
     a_reviewer_of_a_seat_on_no_node_gets_a_node_of_the_pair
