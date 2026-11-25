#!/usr/bin/env bash
# aib-knives: os=unix
# aib-plan fields PF1–PF10 (a9136 11-17, S107-assist 片 1, SPEC-plan-fields-519d0-1117 r4 §2–§6): touches are
# repo paths with `/`, the author cannot verify, only the verifier writes 實查通過, a cycle in `after` is
# refused, a station change asks for its field. Each rule, broken, turns its own test red. Run on macOS (the
# header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust). Run from the repo
# root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gp() { python3 -I scripts/knife.py crates/aib-plan/src/card.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "$3"; }
# PF1 a Windows path is stored with backslashes
gp "    let s = p.trim().replace('\\\\', \"/\");" \
   "    let s = p.trim().to_string(); // knife" \
   touches_are_repo_paths_with_slashes
# PF2 a path that climbs out of the repo is taken
gp "        || s.split('/').any(|seg| seg == \"..\");" \
   "        || false; // knife" \
   touches_are_repo_paths_with_slashes
# PF3 the author may verify
gp '                if authors(&probe).0.contains(v) {' \
   '                if authors(&probe).0.contains(v) && false { // knife' \
   the_author_cannot_verify
# PF4 a reviewer segment counts as an author
gp '            if ["審", "驗", "疊"].iter().any(|w| seg.contains(w)) {' \
   '            if seg.is_empty() { // knife' \
   the_author_cannot_verify
# PF5 the seat in the branch name is not an author
gp "        for t in b.split(['-', '/', '_', '.']) {" \
   "        for t in b.split(['/']) { // knife" \
   the_author_cannot_verify
# PF6 anyone writes 實查通過 on a card someone else (or a human) verifies (0eed7 M1)
gp '                            Some(v) if v != seat => {' \
   '                            Some(v) if v != seat && false => { // knife' \
   only_the_verifier_writes_pass
# PF9 with no verifier set, the author writes 實查通過 on its own card (0eed7 M2)
gp '                            None if authors(&probe).0.contains(seat) => {' \
   '                            None if authors(&probe).0.contains(seat) && false => { // knife' \
   only_the_verifier_writes_pass
# PF7 a cycle in after is written
gp '                    if let Some(c) = after_cycle(&plan, id, to) {' \
   '                    if let Some(c) = after_cycle(&plan, id, to).filter(|_| false) { // knife' \
   a_cycle_in_after_is_refused
# PF8 a card goes to 開發中 without saying what it touches
gp '                    if forward && pos == 2 && fields.touches.is_none() && !has_key(raw, "touches") {' \
   '                    if forward && pos == 2 && fields.touches.is_none() && !has_key(raw, "touches") && false { // knife' \
   a_station_change_asks_for_its_field
# PF10 a card sent back (未驗 → 開發中) must fill touches again (0eed7 L1)
gp '                        .is_none_or(|c| pos > c);' \
   '                        .is_none_or(|_| true); // knife' \
   a_station_change_asks_for_its_field
