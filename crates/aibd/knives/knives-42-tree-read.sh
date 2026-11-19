#!/usr/bin/env bash
# N-web-worktree r3 knives TR1–TR8 (b4297 11-20; DESIGN-web-worktree-b4297-1119 r3 補): the
# reviewer reads the author seat's tree and only reads it — inside the tree, never .git, no
# program the repo names, only the commits since the tree was opened, a seat token on the three
# reads only, never from a user page, never a folder aibd did not open from a repo. Each rule,
# broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tl() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "tree_read::tests::$4"; }
th() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$5" \
        -- cargo test -p aibd --locked --test "$4" "$5"; }
# TR1 a link inside the tree leads to a file outside it
tl crates/aibd/src/tree_read.rs '    let under = real.strip_prefix(&root).ok()?;' \
   '    let under = real.strip_prefix(&root).unwrap_or(&real); // knife' \
   a_path_is_read_only_inside_the_tree_with_either_separator
# TR2 a link leads into the shared .git
tl crates/aibd/src/tree_read.rs '    (!git).then_some(real)' \
   '    Some(real) // knife' \
   a_path_is_read_only_inside_the_tree_with_either_separator
# TR3 a folder lists .git
tl crates/aibd/src/tree_read.rs '                (!name.eq_ignore_ascii_case(".git")).then_some((name, md))' \
   '                Some((name, md)) // knife' \
   a_path_is_read_only_inside_the_tree_with_either_separator
# TR4 the diff runs the external diff program the repo names
tl crates/aibd/src/tree_read.rs 'const DIFF_SAFE: &[&str] = &["diff", "--no-ext-diff", "--no-textconv", "--no-color"];' \
   'const DIFF_SAFE: &[&str] = &["diff", "--no-textconv", "--no-color"]; // knife' \
   the_log_and_diff_are_the_commits_since_the_tree_was_opened
# TR5 the log is every commit, not those since the tree was opened
tl crates/aibd/src/tree_read.rs '    let range = format!("{base}..{head}");' \
   '    let range = head.to_owned(); // knife' \
   the_log_and_diff_are_the_commits_since_the_tree_was_opened
# TR6 a seat token reaches a seat route beside the three tree reads
tl crates/aibd/src/guard.rs '    !id.is_empty() && matches!(tail, "tree" | "tree/log" | "tree/diff")' \
   '    !id.is_empty() // knife' \
   a_seat_token_reaches_the_three_tree_reads_and_nothing_next_to_them
# TR7 a user folder page reads a seat's tree
th crates/aibd/src/tree_read.rs '            if crate::seat_input::held_as_user_page(p).await {' \
   '            if false && crate::seat_input::held_as_user_page(p).await { // knife' \
   seats_tree_userpage a_user_folder_page_reads_no_seats_tree
# TR8 a seat aibd opened no repo tree for is read as if it had one
th crates/aibd/src/tree_read.rs '            .as_str()?' \
   '            .as_str().or(Some("0000000000000000000000000000000000000000"))? // knife' \
   seats_page_open_repo a_seat_without_a_repo_tree_has_nothing_to_read
# TR9 (a9136 11-20 03:04) a seat's own copy is read in place: git runs in the copy, whose config the seat writes
tl crates/aibd/src/tree_read.rs '    if crate::seat_worktree::base_of(tree).is_some() {' \
   '    if false && crate::seat_worktree::base_of(tree).is_some() { // knife' \
   a_seats_own_copy_is_read_from_the_authors_repo_and_runs_none_of_its_programs
# TR10 collecting a seat's own copy counts it with git run in it and may take it away
tl crates/aibd/src/seat_worktree.rs '    if base_of(tree).is_some() {' \
   '    if false && base_of(tree).is_some() { // knife' \
   a_seats_own_copy_is_read_from_the_authors_repo_and_runs_none_of_its_programs
