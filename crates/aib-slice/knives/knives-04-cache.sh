#!/usr/bin/env bash
# aib-slice git cache knives CK1–CK3 (1ced7 11-17, S79-slice-incr piece 1, DESIGN-slice-incremental r1):
# each rule of the cache, broken on purpose, must turn its own test red. Run by one line in aibd.yml's
# "aib-slice knives (macOS)" step; new knives live here, not inline (RULES §21). Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
kc() { python3 -I scripts/knife.py crates/aib-slice/src/cache.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --lib "$3"; }
ks() { python3 -I scripts/knife.py crates/aib-slice/src/scan.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test sweep "$3"; }
# CK1 a command with no object id (it reads the work tree or a ref) is kept
kc '    revs > 0' '    revs > 0 || true // knife' \
   only_commands_naming_objects_by_id_are_pure
# CK2 an option that reads refs (--all) is kept
kc '            if READS_REFS.contains(&name) {' '            if false && READS_REFS.contains(&name) { // knife' \
   only_commands_naming_objects_by_id_are_pure
# CK3 a merge-tree conflict (exit 1) comes back from the cache as clean
ks '            c.borrow_mut().put(args, &format!("{code}\n{out}"));' \
   '            c.borrow_mut().put(args, &format!("{}\n{out}", code * 0)); // knife' \
   the_git_cache_answers_like_a_sweep_without_it
echo "aib-slice cache knives: all bit"
