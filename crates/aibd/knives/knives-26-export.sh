#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace-c1 knives EX1–EX2 (7b81e 11-18; CHECKLIST-workspace-wave3 H5): an aibd node write reaches the old page's
# data.json through aib-plan's own writer, and a refusal gives back only aib-plan's code, never its message (paths).
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
kn() { python3 -I scripts/knife.py crates/aibd/src/nodes.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "nodes::tests::$3"; }
# EX1 the export says ok and writes nothing
kn '    match card::write_with(data, op, by, now, f, w) {' \
   '    match (data, op, by, now, f, w, Ok::<(), String>(())).6 { // knife (the names stay used: -D warnings)' \
   a_write_reaches_the_old_pages_file_one_card_at_a_time_the_aib_plan_way
# EX2 aib-plan's whole message (it can name the file) comes back
kn "                .split([':', '：'])" \
   "                .split(['\\u{0}']) // knife" \
   a_write_reaches_the_old_pages_file_one_card_at_a_time_the_aib_plan_way
