#!/usr/bin/env bash
# aib-knives: os=unix
# aib-slice git cache knives CK1–CK6 (1ced7 11-17, CK6 a9136, S79-slice-incr piece 1, DESIGN-slice-incremental r1):
# each rule of the cache, broken on purpose, must turn its own test red. Run by the shared crate-knives step
# (crates/aib-knife/run-knives.sh rust, main 454ccd67 #836); no step of its own. Run from the repo root.
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
kc '            if READS_REFS.contains(&name) || READS_STORE.contains(&name) {' '            if false && READS_REFS.contains(&name) || READS_STORE.contains(&name) { // knife' \
   only_commands_naming_objects_by_id_are_pure
# CK3 a merge-tree conflict (exit 1) comes back from the cache as clean
ks '            c.borrow_mut().put(args, &format!("{code}\n{out}"));' \
   '            c.borrow_mut().put(args, &format!("{}\n{out}", code * 0)); // knife' \
   the_git_cache_answers_like_a_sweep_without_it
# CK4 a repo with replaced objects or grafts still uses the cache
ks '        if replaced || grafts {' '        if (replaced || grafts) && false { // knife' \
   a_repo_with_replaced_objects_sweeps_without_the_cache
# CK5 a sweep cut off at its time limit leaves no cache (flush never writes)
kc '        self.save(path, false)?;' '        let _ = path; // knife' \
   a_sweep_cut_off_keeps_what_it_flushed
# CK6 (5d33a c8a97748 M1) --ignore-missing (the answer depends on which objects are here) is kept
kc '            if READS_REFS.contains(&name) || READS_STORE.contains(&name) {' '            if READS_REFS.contains(&name) || (READS_STORE.contains(&name) && false) { // knife' \
   only_commands_naming_objects_by_id_are_pure
echo "aib-slice cache knives: all bit"
