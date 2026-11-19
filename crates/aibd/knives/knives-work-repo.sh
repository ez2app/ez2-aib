#!/usr/bin/env bash
# WR1–WR3 (7b81e 11-20; 519d0 06:35「做事席也能帶登記的倉,一律開自己的一份」, 5d33a N6;
# DESIGN-work-repo-7b81e-1120): a registered repo's seat works in its own clone, never a
# worktree of the registered repo; a work seat may take a repo and is not asked again.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
rp() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" \
  --expect-fail repos::tests::a_tree_opens_only_while_the_settings_are_what_was_registered \
  -- cargo test -p aibd --locked --lib repos::tests::a_tree_opens_only_while_the_settings_are_what_was_registered; }
sp() { python3 -I scripts/knife.py "$1" "$2" "$3" \
  --expect-fail a_repo_reaches_a_seat_only_through_the_owners_fingerprint \
  -- cargo test -p aibd --locked --test seats_page_open_repo a_repo_reaches_a_seat_only_through_the_owners_fingerprint; }
# WR1 the copy is a worktree writing the registered repo's .git
rp '    let head = seat_worktree::open_clone(src, &tree, &branch, &author, hooks)' \
   '    let head = { let _ = &author; seat_worktree::open_tree(src, &tree, &branch, "HEAD", hooks) } // knife'
# WR2 a work seat cannot take a repo
sp crates/aibd/src/repos.rs 'pub const REPO_MODES: [&str; 2] = ["dgrs", "work"];' \
   'pub const REPO_MODES: [&str; 2] = ["dgrs", "dgrs"]; // knife'
# WR3 a work seat's open is asked again (the page would wait for a finger it was told it needs no more)
sp crates/aibd/src/seats.rs '    let asked_now = req.mode == "dgrs";' \
   '    let asked_now = true; // knife'
