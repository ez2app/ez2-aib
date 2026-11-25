#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace-c2 knives HS1–HS2 (7b81e 11-18; a9136 hosts core 3: run id and start time): the lease's start and run id
# are read, and the page's answer carries them. Each, broken, turns its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
kl() { python3 -I scripts/knife.py crates/aib-lease/src/hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-lease --locked --lib "hosts::tests::$3"; }
ka() { python3 -I scripts/knife.py crates/aibd/src/hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "hosts::tests::$3"; }
# HS1 the lease's start is not read
kl '        v.start_epoch = l.get("acquired_epoch").and_then(Value::as_f64);' \
   '        v.start_epoch = None; // knife' \
   busy_carries_the_holder
# HS2 the page's answer leaves the start out
ka '        "start_epoch": v.start_epoch,' \
   '        "start_epoch": Value::Null, // knife' \
   a_machine_is_shown_by_its_nickname_with_who_holds_it_and_never_where_it_is
