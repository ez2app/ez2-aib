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
sp crates/aibd/src/seats.rs '    let asked_now = req.mode != "work";' \
   '    let asked_now = true; // knife'
# BS1–BS3 (ORDER-webonly-today 12): a seat's copy may start on a named full commit (the all-head)
# BS1 the copy stays on the repo's HEAD
rp '    seat_worktree::git(&tree, hooks, &["checkout", "-q", "-B", &branch, b])' \
   '    seat_worktree::git(&tree, hooks, &["rev-parse", "HEAD"]) // knife'
# BS2 a short commit is taken
rp '    b.len() == 40' '    (7..=40).contains(&b.len())'
# BS3 a base without a repo is dropped silently
sp crates/aibd/src/seats.rs '    if req.base.is_some() && req.repo.is_none() {' \
   '    if false && req.base.is_some() && req.repo.is_none() {'
# RC1–RC4 (5d33a PREREAD-workrepo L1 L2): copies capped and kept off a nearly full disk; a
# repo's copy is never merged into the repo
rc() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" \
  --expect-fail repos::tests::a_copy_needs_room_and_is_never_merged_into_the_repo \
  -- cargo test -p aibd --locked --lib repos::tests::a_copy_needs_room_and_is_never_merged_into_the_repo; }
# RC1 no cap on copies
rc '    if copies >= REPO_COPIES_MAX {' '    if copies > usize::MAX - 1 {'
# RC2 a disk whose free space cannot be read takes a copy
rc '        Some(a) if a >= REPO_DISK_FREE_MIN => Ok(()),' '        Some(a) if a >= REPO_DISK_FREE_MIN => Ok(()), None => Ok(()),'
# RC3 a repo's copy is taken for a seat's (合回 into the registered repo's checkout)
rc '    author.starts_with(REPO_AUTHOR_PREFIX)' '    author.starts_with("never:")'
# RC4 copies are not counted (the cap never bites)
rp '                .and_then(|v| v["author"].as_str().map(is_repo_copy))' \
   '                .and_then(|v| v["author"].as_str().map(|_| false))'
# RL1–RL2 (5d33a PREREAD-workrepo r2 L1 N4): a released copy stops counting; a broken base file counts
# RL1 a released copy still counts (the tenth repo seat is refused for good)
rp '            if dir.join(RELEASED_FILE).exists() {' \
   '            if false && dir.join(RELEASED_FILE).exists() {'
# RL2 a copy whose base file is broken is not counted
rp '                .unwrap_or(true)' \
   '                .unwrap_or(false)'
# RL3–RL5 (5d33a PREREAD-workrepo r3 L: the archive's call and the lock had no test of their own)
ra() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" \
  --expect-fail repos::tests::an_archived_copy_stops_counting_only_when_its_head_was_taken \
  -- cargo test -p aibd --locked --lib repos::tests::an_archived_copy_stops_counting_only_when_its_head_was_taken; }
rk() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" \
  --expect-fail repos::tests::a_copy_opens_only_while_no_other_copy_is_opening \
  -- cargo test -p aibd --locked --lib repos::tests::a_copy_opens_only_while_no_other_copy_is_opening; }
# RL3 an archived copy whose head was taken still counts
ra '    if c.is_ok() && repo_copy {' '    if c.is_ok() && repo_copy && false {'
# RL4 a copy whose collect failed stops counting
ra '    if c.is_ok() && repo_copy {' '    if (c.is_ok() || true) && repo_copy {'
# RL5 two copies open at once
rk '    let _one_at_a_time = COPY_OPEN' '    let _one_at_a_time = Box::leak(Box::new(std::sync::Mutex::new(())))'
