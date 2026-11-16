#!/usr/bin/env bash
# aib-seathook pre-push knives (56cda 11-17, PLAN-gates-56cda-1117 片 1; RULES §21: new knives live in
# crates/<crate>/knives/*.sh and aibd.yml only calls them). aibd.yml runs every crates/aib-seathook/knives/*.sh
# in name order in one step; aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root.
# Each knife must turn its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
sk() { python3 -I scripts/knife.py "crates/aib-seathook/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-seathook --locked $5; }
# SK1 warn mode stops a push (519d0 11-17 04:00: warn first, block only after f1fae agrees)
sk lib.rs '    i32::from(mode == Mode::Block && failed)' '    i32::from((mode == Mode::Block || true) && failed) // knife' \
   warn_never_stops_and_errors_never_count --lib
# SK2 a check that could not run (error) stops a push
sk lib.rs '    let failed = verdicts.iter().any(|(_, v)| matches!(v, Verdict::Fail(_)));' \
   '    let failed = verdicts.iter().any(|(_, v)| !matches!(v, Verdict::Pass)); // knife' \
   warn_never_stops_and_errors_never_count --lib
# SK3 a deleted ref (all-zero local sha) is checked as if it pushed content
sk lib.rs "            if f.len() != 4 || f[1].bytes().all(|b| b == b'0') {" \
   '            if f.len() != 4 { // knife' \
   pushed_refs_skip_deletions_and_bad_lines --lib
# SK4 a commented-out `# pre_push = "block"` turns block on
sk lib.rs "        let line = line.split('#').next().unwrap_or(\"\").trim();" \
   "        let line = line.trim().trim_start_matches('#').trim(); // knife" \
   block_only_from_an_explicit_key --lib
# SK5 install-pre-push overwrites a pre-push hook that is not ours
sk main.rs '        if !old.contains(SHIM_MARK) {' '        if false && !old.contains(SHIM_MARK) { // knife' \
   install_leaves_a_foreign_hook_alone_and_rewrites_its_own '--test prepush'
