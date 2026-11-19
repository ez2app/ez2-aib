#!/usr/bin/env bash
# N-web-worktree wiring knives RP1–RP4 (b4297 11-19; DESIGN-web-worktree-b4297-1119 §1.1 §1.2
# r3): a repo is registered by its real top outside aibd's folder with the settings a seat would
# load in the fingerprint window, and a seat's tree opens only while those settings are what was
# registered. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
rp() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "repos::tests::$3"; }
# RP1 a repo inside aibd's own folder (a seat's tree) is registered
rp '    if real.starts_with(&data_real) {' \
   '    if false && real.starts_with(&data_real) { // knife' \
   a_repo_is_registered_by_its_real_top_outside_aibd_with_its_settings_listed
# RP2 a folder below a repo's top is registered as the repo
rp '    if top_real != real {' \
   '    if false && top_real != real { // knife' \
   a_repo_is_registered_by_its_real_top_outside_aibd_with_its_settings_listed
# RP3 the fingerprint window leaves out the settings a seat would load
rp '        "manifest": c.manifest,' \
   '        "manifest": Value::Null, // knife' \
   registering_or_opening_with_a_repo_asks_a_fingerprint_and_nothing_is_written_unasked
# RP4 a tree opens after the repo's settings changed since registration
rp '    seat_worktree::check_unchanged(&repo.manifest, src, hooks)?;' \
   '    let _ = seat_worktree::check_unchanged(&repo.manifest, src, hooks); // knife' \
   a_tree_opens_only_while_the_settings_are_what_was_registered
