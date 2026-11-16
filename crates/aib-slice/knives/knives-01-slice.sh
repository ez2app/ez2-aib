#!/usr/bin/env bash
# The "aib-slice knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15; preview.7 A, 519d0 11-16 11:1x: aibd.yml was 533,152 B, over
# GitHub's 500 KB limit). aibd.yml runs every crates/aib-slice/knives/*.sh in name order in one
# step; aib-xtask knife-anchors reads crates/*/knives/*.sh, so every knife here is still checked.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
set -eu
sl() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --lib "$3"; }
ss() { python3 -I scripts/knife.py crates/aib-slice/src/scan.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test sweep "$3"; }
# SK1 a missing field passes the schema check
sl '                if !o.contains_key(k) {' '                if false && !o.contains_key(k) {' \
   a_missing_or_extra_field_is_red
# SK2 an extra field passes the schema check
sl '                None if closed => errs' '                None if closed && false => errs' \
   a_missing_or_extra_field_is_red
# SK3 a value outside the enum passes
sl '        if !e.contains(v) {' '        if false && !e.contains(v) {' a_missing_or_extra_field_is_red
# SK4 a schema keyword the checker does not read is accepted silently
sl '        if !KNOWN.contains(&k.as_str()) {' '        if false && !KNOWN.contains(&k.as_str()) {' \
   a_schema_keyword_the_checker_does_not_read_is_refused
# SK5 a review of an older commit counts for the head
sl '    let review_at = b.review.as_ref().filter(|r| r.at_head);' \
   '    let review_at = b.review.as_ref().filter(|_| true);' the_six_classes_in_order
# SK6 (IFACE-s79 r11 1) a branch whose commits all sit in an open PR is waved on as a to-do of its own
sl '    if b.unique_not_in_prs == 0 && !pr_open {' '    if b.unique_not_in_prs == 0 && !pr_open && false { // knife' \
   the_six_classes_in_order
# SK41 (IFACE-s79 r11 1) a branch with its own open PR is called superseded by the PR above it
sl '    if b.unique_not_in_prs == 0 && !pr_open {' '    if b.unique_not_in_prs == 0 { // knife' \
   the_six_classes_in_order
# SK7 a biting knife (rc 101) makes a receipt red
sl '        if line.trim_start().starts_with("KNIFE") {' \
   '        if false && line.trim_start().starts_with("KNIFE") {' \
   receipts_name_their_head_and_their_red_keys
# SK8 a merge-tree conflict is read as clean
ss '            conflict: true,' '            conflict: false,' an_open_pr_makes_merge_first_and_conflicts_show
# SK9 patches an open PR already holds are not taken off
ss '                        left.retain(|c| p.contains(c));' \
   '                        left.retain(|c| p.contains(c) || !p.is_empty());' \
   an_open_pr_makes_merge_first_and_conflicts_show
# SK10 --seat lists every seat
ss '            if seat.as_deref() != Some(want.as_str()) {' \
   '            if false && seat.as_deref() != Some(want.as_str()) {' one_seat_only
# SK13 a schema field the IFACE.md field table does not list
sj() { python3 -I scripts/knife.py crates/aib-slice/schema/sweep.v1.json "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test iface "$3"; }
sj '"stack_top": {' '"stack_top_x": {' the_document_and_the_schema_name_the_same_fields
# SK11 a receipt whose RECEIPT-SHA256 does not match still counts as green
sl '        self.nonzero.is_empty() && self.sealed != Some(false)' '        self.nonzero.is_empty()' \
   a_receipt_seal_is_checked_not_trusted
# SK12 an open PR with red checks is not class 3
sl '            .filter(|p| p.open && p.checks.as_deref() == Some("FAILURE"))' \
   '            .filter(|p| p.open && p.checks.as_deref() == Some("FAILURE") && false)' \
   the_six_classes_in_order
# SK14 a branch changing a CI workflow is not a new gate
sl '            f.starts_with(".github/workflows/")' '            f.starts_with(".github/workflows/x")' \
   a_branch_that_changes_a_check_is_a_new_gate
# SK21 a branch that went on after its PR was closed is still abandonable (s33)
sl '    (b.unique_not_in_prs > 0 && p.head.as_deref() == Some(b.head.as_str())).then_some(p.number)' \
   '    (b.unique_not_in_prs > 0).then_some(p.number) // knife' \
   a_branch_whose_pr_was_closed_at_its_head_is_abandonable
# SK22 the .py files a branch carries are not listed (s35)
sl '        .filter(|f| PY_EXT.iter().any(|e| f.to_ascii_lowercase().ends_with(*e)))' \
   '        .filter(|f| f.to_ascii_lowercase().ends_with(".rs")) // knife' \
   python_files_are_listed
# SK37 a deleted .py is listed as Python the branch carries (043dd s33 L1)
sl "        .filter(|(st, _)| !st.starts_with('D'))" \
   "        .filter(|(st, _)| !st.starts_with('X')) // knife" \
   python_files_are_listed
# SK23 an abandonable branch is classed by what else it has, a to-do again (s33)
sl '    if let Some(n) = abandonable(b) {' \
   '    if let Some(n) = abandonable(b).filter(|_| false) { // knife' \
   a_branch_whose_pr_was_closed_at_its_head_is_abandonable
# S73k gate by content (519d0 11-14): gatecontent.rs rules 1-6, each refusal bites
sg() { python3 -I scripts/knife.py crates/aib-slice/src/gatecontent.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --lib "$3"; }
# SK24 rule 2: a changed line besides the step is taken as an insertion
sg '    (p + s == base.len()).then_some(p)' \
   '    (p + s >= p).then_some(p) // knife' \
   anything_but_one_added_step_is_literal
# SK25 rule 3: two steps added at once pass as one
sg '        if j <= i {' \
   '        if j < i { // knife' \
   anything_but_one_added_step_is_literal
# SK26 rule 4: an item of another list (matrix include) passes as a step
sg '        if i < indent && t == "steps:" {' \
   '        if i < indent { // knife' \
   anything_but_one_added_step_is_literal
# SK27 rule 4: lines inside the previous step's run block after the insertion are not checked
sg '        if i >= indent && !next_item {' \
   '        if false && i >= indent && !next_item { // knife' \
   anything_but_one_added_step_is_literal
# SK28 rule 5: a step for another crate passes
sg '    if let Some(n) = names.iter().find(|n| !own_crates.contains(*n)) {' \
   '    if let Some(n) = names.iter().find(|n| n.is_empty() && !own_crates.contains(*n)) { // knife' \
   anything_but_one_added_step_is_literal
# SK29 rule 6: a GitHub expression in the step passes
sg '    if block.iter().any(|l| l.contains(EXPR)) {' \
   '    if block.iter().any(|l| l.contains(EXPR)) && false { // knife' \
   anything_but_one_added_step_is_literal
# SK30 rule 5 (a9136 M3): a key a knife step does not have passes (another shell, env, an action)
sg '        if !key_ok(t) {' \
   '        if !key_ok(t) && false { // knife' \
   anything_but_one_added_step_is_literal
# SK31 rule 5 (a9136 M3): any command passes as a run line
sg "        } else if !t.starts_with('#') && t != \"set -eu\" {" \
   '        } else if false { // knife' \
   anything_but_one_added_step_is_literal
# SK32 rule 5 (a9136 M2): anything may follow `cargo test -p <crate> --locked` (--workspace, && cargo)
sg '    let ok = w.next() == Some("--locked") && w.all(|x| word_ok(x, params));' \
   '    let ok = w.next() == Some("--locked") && (w.all(|x| word_ok(x, params)) || true); // knife' \
   anything_but_one_added_step_is_literal
# SK33 rule 1: aibd.yml plus another gate file is judged on aibd.yml alone
sg '    let [file] = gate_files else {' \
   '    let [file, ..] = gate_files else { // knife' \
   only_aibd_yml_present_on_both_sides_is_judged
# SK34 rule 1 (a9136 M1): a workflow whose jobs may write is judged by content too
sg "    if file.replace('\\\\', \"/\") != ONLY {" \
   "    if file.replace('\\\\', \"/\") != ONLY && false { // knife" \
   only_aibd_yml_present_on_both_sides_is_judged
# SK38 rule 4 (a9136 L1): `#` lines of the run block the step was put into are skipped as comments
sg "    l.trim_start().starts_with('#') && lead(l).is_some_and(|i| i <= indent)" \
   "    l.trim_start().starts_with('#') && lead(l).is_some_and(|i| i <= indent || true) // knife" \
   a_step_put_into_a_run_block_is_literal_even_before_hash_lines
# SK39 rule 5: a knife call whose double-quoted text the shell expands passes
sg '                    Some(x) if EXPANDS.contains(&x) => {' \
   '                    Some(x) if EXPANDS.contains(&x) && false => { // knife' \
   anything_but_one_added_step_is_literal
# SK40 rule 3 (a9136 L3): an indentation with NBSP in it passes as spaces
sg '    plain.then_some(i)' \
   '    plain.then_some(i).or(Some(i)) // knife' \
   anything_but_one_added_step_is_literal
# S73l aib-slice due (Andy 11-14 13:1x): 應合未合 / 應修未修 from the same sweep
sd() { python3 -I scripts/knife.py crates/aib-slice/src/due.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test due "$3"; }
# SD1 a branch that conflicts with main is listed as ready to merge
sd '        let clash = matches!(class, 1 | 2 | 8) && (main_conflict(b) || drops_steps(b));' \
   '        let clash = matches!(class, 1 | 2 | 8) && drops_steps(b); // knife' \
   what_must_be_fixed_says_where_it_is_red
# SD2 an open PR nobody touched for days is in neither list
sd '        if to_fix || (class == 4 && idle(b)) {' \
   '        if to_fix { // knife' \
   what_must_be_fixed_says_where_it_is_red
# SD3 a head under two names is listed by the copy without the PR
sd '                if open(b) && !open(seen[at]) {' \
   '                if false && open(b) && !open(seen[at]) { // knife' \
   a_head_listed_twice_is_one_item
# SD4 a sweep that is not complete prints its lists with no warning (reads as 0 件)
sd '    if let Some(first) = errors.first() {' \
   '    if let Some(first) = errors.first().filter(|_| false) { // knife' \
   a_sweep_that_is_not_complete_says_so_and_long_lists_are_cut
# SD5 a list over --max is cut with no 「還有 k 件」
sd '    if items.len() > max {' \
   '    if items.len() > max && false { // knife' \
   a_sweep_that_is_not_complete_says_so_and_long_lists_are_cut
# SD6 a PR without ready-merge is not told so
sd '        if !p.labels.iter().any(|l| l == READY) {' \
   '        if false && !p.labels.iter().any(|l| l == READY) { // knife' \
   what_can_merge_says_what_it_still_lacks
# SD7 due without --seat / --all prints the whole fleet
python3 -I scripts/knife.py crates/aib-slice/src/main.rs '    if is_due && f.all == o.seat.is_some() {' \
  '    if false && is_due && f.all == o.seat.is_some() { // knife' \
  --expect-fail the_command_needs_one_of_seat_and_all_and_never_prints_zero_for_an_unread_repo \
  -- cargo test -p aib-slice --locked --test due the_command_needs_one_of_seat_and_all_and_never_prints_zero_for_an_unread_repo
# SD8 check cells that wait are counted wrong
python3 -I scripts/knife.py crates/aib-slice/src/due.rs '        slot.0 += 1;' \
  '        slot.0 += 2; // knife' \
  --expect-fail check_cells_that_wait_are_counted_by_kind \
  -- cargo test -p aib-slice --locked --lib check_cells_that_wait_are_counted_by_kind
# SD9 a cancelled cell is red again (the merge queue's cancelled runs read as failures)
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs '            Some("cancelled") => pending = true,' \
  '            Some("cancelled") => red.push(name.to_owned()), // knife' \
  --expect-fail a_cancelled_cell_is_not_red_and_an_older_run_of_a_cell_does_not_count \
  -- cargo test -p aib-slice --locked --lib a_cancelled_cell_is_not_red_and_an_older_run_of_a_cell_does_not_count
# SD10 the oldest run of a cell counts instead of the newest
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs '        if by.get(name).is_none_or(|cur| key(r) >= key(cur)) {' \
  '        if by.get(name).is_none_or(|cur| key(r) <= key(cur)) { // knife' \
  --expect-fail a_cancelled_cell_is_not_red_and_an_older_run_of_a_cell_does_not_count \
  -- cargo test -p aib-slice --locked --lib a_cancelled_cell_is_not_red_and_an_older_run_of_a_cell_does_not_count
# SD11 cancelled cells are not told as something to wait for
sd '        if let Some(c) = p.cancelled.filter(|c| *c > 0) {' \
   '        if let Some(c) = p.cancelled.filter(|c| *c > 99) { // knife' \
   cancelled_cells_wait_and_a_pr_below_is_named_by_number
# SD12 a PR below that was not swept is named by its branch, not its number
sd '                    .find(|p| &p.head_ref == under)' \
   '                    .find(|p| &p.head_ref == under && false) // knife' \
   cancelled_cells_wait_and_a_pr_below_is_named_by_number
# S82 aib-slice gc (Andy 11-14 14:2x): DEV-S82 rules 1-6, each keeps what it must
gl() { python3 -I scripts/knife.py crates/aib-slice/src/gc.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --lib "$3"; }
gt() { python3 -I scripts/knife.py crates/aib-slice/src/gc.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test gc "$3"; }
# GC1 rule 1: another seat's merged tree is removed
gl '    if !owned(&t.name, seat) {' \
   '    if false && !owned(&t.name, seat) { // knife' \
   each_rule_keeps_the_tree
# GC2 rule 2: trees are removed while a receipt of this seat runs
gl '    if running {' \
   '    if running && false { // knife' \
   each_rule_keeps_the_tree
# GC3 rule 2: a tree touched a minute ago is removed
gl '    if t.idle_s < MIN_IDLE_S {' \
   '    if false && t.idle_s < MIN_IDLE_S { // knife' \
   each_rule_keeps_the_tree
# GC4 rule 2: a tree with uncommitted changes is removed
gl '    if dirty {' \
   '    if dirty && false { // knife' \
   each_rule_keeps_the_tree
# GC5 rule 5: a tree whose main could not be read counts as merged
gl '        (t.in_main, t.dirty, t.only_here, t.own_work)' \
   '        (t.in_main.or(Some(true)), t.dirty, t.only_here, t.own_work) // knife' \
   each_rule_keeps_the_tree
# GC12 (043dd H1) files git does not track yet do not count as changes: a new crate is removed with its tree
gt '        .map(|s| s.lines().any(|l| !l.trim().is_empty() && !cache_line(l)));' \
   '        .map(|s| s.lines().any(|l| !l.trim().is_empty() && !cache_line(l) && !l.starts_with("??"))); // knife' \
   apply_removes_merged_trees_and_tagged_caches_and_logs_each
# GC16 (043dd r2 M1) a merged full clone is removed while another work tree uses it as its repository
gt '    if t.clone && t.others > Some(0) {' \
   '    if t.clone && t.others > Some(99) { // knife' \
   apply_removes_merged_trees_and_tagged_caches_and_logs_each
# GC17 (043dd r2 L2) the stashes of a full clone are not counted
gt '            .map(|(c, s)| c + s)' \
   '            .map(|(c, _s)| c) // knife' \
   a_dry_run_lists_and_removes_nothing
# GC13 (043dd H1) a tree opened from main with no commit of its own counts as merged
gl '    if in_main && !own_work {' \
   '    if in_main && !own_work && false { // knife' \
   each_rule_keeps_the_tree
# GC14 (043dd M1) a full clone with a local-only branch or a stash is removed
gl '    if t.clone && only_here > 0 {' \
   '    if t.clone && only_here > 99 { // knife' \
   each_rule_keeps_the_tree
# GC15 (043dd L1) a seat code in the middle of a name makes the folder this seat's
gl '        _ => false,' \
   '        _ => parts.contains(&seat), // knife' \
   each_rule_keeps_the_tree
# GC6 rule 6: commits that are only here are not called a backup
gl '    if only_here > 0 {' \
   '    if only_here > 0 && false { // knife' \
   commits_only_here_are_a_backup_never_a_removal
# GC7 rule 3: a cache folder without CACHEDIR.TAG is cleared
gl '        .filter(|c| c.tagged)' \
   '        .filter(|c| c.tagged || true) // knife' \
   only_tagged_caches_are_cleared_and_only_when_asked
# GC8 rule 4: a tree is removed with no log line
gt '                log_line(root, seat, machine, r, now).and_then(|()| remove_tree(&r.tree))' \
   '                remove_tree(&r.tree) // knife' \
   apply_removes_merged_trees_and_tagged_caches_and_logs_each
# S82-a council autoclean §二 (DECISION-council-autoclean-519d0-1115), each condition keeps a merged tree
# GC18 rule 1: a failed fetch still removes a merged tree
gl '        if t.fetched == Some(false) {' \
   '        if t.fetched == Some(false) && false { // knife' \
   each_council_condition_keeps_a_merged_tree
# GC19 rule 3: open PR heads not given count as no open PR
gl '        let Some(open_pr) = t.open_pr else {' \
   '        let Some(open_pr) = t.open_pr.or(Some(false)) else { // knife' \
   each_council_condition_keeps_a_merged_tree
# GC20 rule 2: an ignored file does not keep the tree
gl '        if let Some(p) = ignored {' \
   '        if let Some(p) = ignored.as_ref().filter(|_| false) { // knife' \
   each_council_condition_keeps_a_merged_tree
# GC21 rule 3: a locked tree is removed
gl '        if locked {' \
   '        if locked && false { // knife' \
   each_council_condition_keeps_a_merged_tree
# GC22 rule 3: the head of an open PR is removed
gl '        if open_pr {' \
   '        if open_pr && false { // knife' \
   each_council_condition_keeps_a_merged_tree
# GC23 rule 5: a tree something uses is removed
gl '        Some(Some(w)) => return Verdict::Keep(w.clone()),' \
   '        Some(Some(_)) => {} // knife' \
   each_council_condition_keeps_a_merged_tree
# GC24 rule 5: processes or leases unread count as free
gl '        None => return keep("processes, leases or receipts could not be read"),' \
   '        None => {} // knife' \
   each_council_condition_keeps_a_merged_tree
# GC25 rule 5: a lease that names the tree is not seen
gl '        if mentions(leases, name) {' \
   '        if false && mentions(leases, name) { // knife' \
   a_folder_is_named_only_as_a_whole_word
# GC26 rule 5: a receipt not done that names the tree is not seen
gl '        if self.receipts.iter().any(|r| mentions(r, name)) {' \
   '        if false && self.receipts.iter().any(|r| mentions(r, name)) { // knife' \
   a_folder_is_named_only_as_a_whole_word
# GC27 rule 5: a longer folder name counts as naming this one
gl '            !before.is_some_and(part) && !after.is_some_and(part)' \
   '            !after.is_some_and(part) && before.is_none_or(|_| true) // knife' \
   a_folder_is_named_only_as_a_whole_word
# GC28 rule 3: git worktree lock is not read
gt '                t.locked = Some(gd.join("locked").exists());' \
   '                t.locked = Some(false); // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC29 rule 2: ignored files are not read
gt '        .map(|s| first_work_ignored(&s));' \
   '        .map(|_| None); // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC30 rule 5: a finished receipt is read as running (or a running one as finished)
gt '        .filter(|run| !root.join(format!("{run}.done")).exists())' \
   '        .filter(|run| root.join(format!("{run}.done")).exists()) // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC31 rule 3: the open heads are not compared with the head
gt '            (Some(open), Some(h)) => Some(open.iter().any(|o| o == h)),' \
   '            (Some(_open), Some(_h)) => Some(false), // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC32 rule 1: a failed fetch counts as fetched
gt '    let ok = git(dir, &["fetch", "-q", "origin", "main"]).is_ok();' \
   '    let ok = true; // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC33 rule 8: a held cleanup lock is taken anyway
gt '        .create_new(true)' \
   '        .create(true) // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC34 rule 8: a tree changed since the plan is removed without judging it again
gt '        if again != r.verdict {' \
   '        if false && again != r.verdict { // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC35 rule 4: the tree's own index does not count as touching it
gt '                        t.idle_s = t.idle_s.min(a);' \
   '                        let _ = a; // knife' \
   a_fresh_index_counts_as_touched_when_the_files_are_old
# GC36 every run writes the journal: the command skips it
python3 -I scripts/knife.py crates/aib-slice/src/main.rs \
  '    let journaled = gc::journal(&journal, &rows, &seat, &machine, dry, now);' \
  '    let journaled: Result<(), String> = { let _ = &journal; Ok(()) }; // knife' \
  --expect-fail the_command_removes_nothing_unless_told_which_seat_and_to_apply \
  -- cargo test -p aib-slice --locked --test gc the_command_removes_nothing_unless_told_which_seat_and_to_apply
# GC37 L1: a lease status that exits 4 (a machine held) is read as no leases
python3 -I scripts/knife.py crates/aib-ghread/src/lib.rs '    if any {' \
  '    if false && any { // knife' \
  --expect-fail run_fixed_any_returns_the_output_at_any_exit \
  -- cargo test -p aib-ghread --locked --lib run_fixed_any_returns_the_output_at_any_exit
# GC38 L1: a lease status with no output counts as read
gt '    (!text.trim().is_empty()).then_some(text)' \
   '    Some(text) // knife' \
   the_leases_are_read_whatever_the_exit_code
# GC39 L2: __pycache__ / .DS_Store are work
gl '        || JUNK_NAMES.contains(&name)' \
   '        || (JUNK_NAMES.contains(&name) && false) // knife' \
   caches_and_junk_are_not_ignored_work
# GC40 L2: target-rel is work
gl 'p == "target" || p.starts_with("target-")' \
   'p == "target" || (false && p.starts_with("target-"))' \
   caches_and_junk_are_not_ignored_work
# GC41 L2: the folder git lists above a junk folder is work
gl '        .find(|l| !junk_ignored(l) && !above(l))' \
   '        .find(|l| !junk_ignored(l) && (above(l) || true)) // knife' \
   caches_and_junk_are_not_ignored_work
# GC42 L2: a tree is measured by its first ignored line, junk or not
gt '        .map(|s| first_work_ignored(&s));' \
   '        .map(|s| first_work_ignored(&s).or(s.lines().next().map(str::to_owned))); // knife' \
   the_council_conditions_keep_trees_a_dry_run_would_remove
# GC9 rule 2: a receipt that has not finished is not seen
gt '        .any(|run| !root.join(format!("{run}.done")).exists())' \
   '        .any(|run| root.join(format!("{run}.done")).exists()) // knife' \
   a_running_receipt_or_an_unreadable_main_keeps_everything
# GC10 rule 1: other seats' folders are measured and listed
gt '        .filter(|n| owned(n, seat))' \
   '        .filter(|n| owned(n, seat) || true) // knife' \
   a_dry_run_lists_and_removes_nothing
# GC11 gc with neither --dry-run nor --apply runs
python3 -I scripts/knife.py crates/aib-slice/src/main.rs '    if dry == apply {' \
  '    if dry && apply { // knife' \
  --expect-fail the_command_removes_nothing_unless_told_which_seat_and_to_apply \
  -- cargo test -p aib-slice --locked --test gc the_command_removes_nothing_unless_told_which_seat_and_to_apply
# SK35 by_content does not lower a new gate
sl '    let by_content = matches!(b.gate_content, Some(Ok(_)));' \
   '    let by_content = b.gate_content.is_none() && false; // knife' \
   a_new_gate_is_a_non_gate_only_with_label_and_another_seats_reason
# SK36 the sweep never judges the content
ss '                if !b.gate_files.is_empty() {' \
   '                if b.gate_files.len() > 9 { // knife' \
   every_branch_lands_in_its_class
# SK15 the sweep never fills gate_files
ss '                b.gate_files = gate_files(&files);' '                b.gate_files = gate_files(&files[..0]);' \
   every_branch_lands_in_its_class
# SK16 a receipt cut off before HEAD-END still counts as finished (043dd M1)
sl '        self.complete && !self.steps.is_empty()' '        self.complete || !self.steps.is_empty()' \
   the_six_classes_in_order
# SK17 `(want N)` is ignored: an expected non-0 turns the receipt red (043dd L1)
sl '                        n => n.parse::<i64>().map_or(rc != 0, |n| rc != n),' \
   '                        _ => rc != 0,' receipts_name_their_head_and_their_red_keys
# SK18 an unreadable pending folder is silently empty (043dd L2)
ss '            errors.push(format!("pending {}: {e}", dir.display()));' \
   '            let _ = (e, dir);' an_unreadable_pending_folder_is_an_error
# SK19 the author's own 非閘理由 lowers a new gate
sl '        .filter(|r| r.at_head && r.reviewer.is_some() && r.reviewer != b.seat)' \
   '        .filter(|r| r.at_head && r.reviewer.is_some())' \
   a_new_gate_is_a_non_gate_only_with_label_and_another_seats_reason
# SK20 a branch changing the docs-only list is not a new gate (043dd M2)
sl '                || f == "aib/tools/release/docs_only.py"' '                || f == "aib/tools/release/docs_only.py.x"' \
   a_branch_that_changes_a_check_is_a_new_gate
# MG1 (S78 r-2a) a merge resolved by hand counts as a plain automatic merge
ss '            if auto == tree.trim() {' \
   '            if auto == tree.trim() || !auto.is_empty() { // knife' \
   merges_resolved_by_hand_are_listed_with_their_files
# MG2 (S78 r-2a) every listed merge is called a merge of main
ss '            from_main: ahead.trim() == "0",' \
   '            from_main: ahead.trim() == "0" || !ahead.is_empty(), // knife' \
   merges_resolved_by_hand_are_listed_with_their_files
# MG3 (S78 r-2a) a merge that conflicts cannot be redone: the branch is left unread
ss '        (Some(0) | Some(1), Some(t))' \
   '        (Some(0), Some(t)) // knife' \
   merges_resolved_by_hand_are_listed_with_their_files
# MG4 (S78 r-2a) what a merge of main brought in counts as unreviewed commits of the branch
ss '                &format!("^{main}"),' \
   '                "--no-merges", // knife' \
   merges_resolved_by_hand_are_listed_with_their_files
# MG5 (S78 r-2a, f1fae r-2b) a merge resolved by hand after the review is not listed after it
ss '                                .filter(|x| after.contains(x.commit.as_str()))' \
   '                                .filter(|x| !after.contains(x.commit.as_str())) // knife' \
   merges_resolved_by_hand_are_listed_with_their_files
# FA1 (S79 --fast) a branch nobody touched inside the window is read all the same
ss '        if o.fast && r.at < window && !prs.iter().any(|p| p.head_ref == *name || p.head == r.head) {' \
   '        if false && o.fast && r.at < window && !prs.iter().any(|p| p.head_ref == *name || p.head == r.head) { // knife' \
   fast_reads_recent_branches_and_asks_github_for_closed_prs_once
# FA2 (S79 --fast) a closed PR changed before the window still counts
ss '            if at.is_some_and(|t| t < cutoff) {' \
   '            if at.is_some_and(|t| t < cutoff) && false { // knife' \
   fast_reads_recent_branches_and_asks_github_for_closed_prs_once
# FA3 (S79 --fast) GitHub is asked about every branch one by one again
ss '        } else if let Some(m) = &recent_closed {' \
   '        } else if let Some(m) = recent_closed.as_ref().filter(|_| false) { // knife' \
   fast_reads_recent_branches_and_asks_github_for_closed_prs_once
# ST1 (S79 C1a, RULES 25) a branch whose merge drops a workflow step is still listed as ready to merge
sd '        let clash = matches!(class, 1 | 2 | 8) && (main_conflict(b) || drops_steps(b));' \
   '        let clash = matches!(class, 1 | 2 | 8) && (main_conflict(b) || (drops_steps(b) && false)); // knife' \
   a_branch_that_drops_a_workflow_step_is_to_fix_not_to_merge
# ST2 (S79 C1a) the sweep never compares the workflow steps
ss '        if !flows.is_empty() && b.conflicts_with_main.as_ref().is_some_and(|c| !c.conflict) {' \
   '        if false && !flows.is_empty() && b.conflicts_with_main.as_ref().is_some_and(|c| !c.conflict) { // knife' \
   a_merge_that_drops_a_workflow_step_names_it
# ST3 (S79 C1a) a step missing from the merged file is not seen
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '    let mut have = merged.map(step_names).unwrap_or_default();' \
   '    let mut have = merged.map(|_| step_names(main)).unwrap_or_default(); // knife' --expect-fail a_step_main_has_and_the_merge_does_not_is_lost \
   -- cargo test -p aib-slice --locked --test steps a_step_main_has_and_the_merge_does_not_is_lost
# ST4 (043dd L1) a workflow change whose steps were not compared is listed as ready to merge
sd '        None => steps_unchecked(b),' \
   '        None => steps_unchecked(b) && false, // knife' \
   a_branch_that_drops_a_workflow_step_is_to_fix_not_to_merge
# OU1 (S79 --due-out) the due lists of the same run are not written
python3 -I scripts/knife.py crates/aib-slice/src/main.rs '    if let Some(p) = &f.due_out {' \
   '    if let Some(p) = f.due_out.as_ref().filter(|_| false) { // knife' --expect-fail one_run_writes_the_sweep_and_the_due_lists_as_files \
   -- cargo test -p aib-slice --locked --test sweep one_run_writes_the_sweep_and_the_due_lists_as_files
# OU2 (S79, 519d0) the cache file is cut to the 8 items of a reply's tail
python3 -I scripts/knife.py crates/aib-slice/src/main.rs '        let max = if f.max_set { f.max } else { usize::MAX };' \
   '        let max = if f.max_set || f.max > 0 { f.max } else { usize::MAX }; // knife' --expect-fail the_due_cache_file_takes_every_item_unless_max_is_given \
   -- cargo test -p aib-slice --locked --test sweep the_due_cache_file_takes_every_item_unless_max_is_given
# SK42 (IFACE-s79 r11 2) a PR whose checks are still running is called ready to merge
sl '        if checks == Some("SUCCESS") {' \
   '        if checks == Some("SUCCESS") || checks.is_some() { // knife' \
   the_six_classes_in_order
# SD13 (IFACE-s79 r11 3) a clash with another open PR is not named on the PR that waits to merge
sd '        for (front, both) in &b.conflicts_with {' \
   '        for (front, both) in b.conflicts_with.iter().take(0) { // knife' \
   what_must_be_fixed_says_where_it_is_red
# RC1 (IFACE-s79 r11 5) only TEST-*.txt is a receipt: .out and odd names are not read
ss '        let is_receipt = crate::is_receipt_name(&name);' \
   '        let is_receipt = name.starts_with("TEST-") && name.ends_with(".txt"); // knife' \
   the_receipts_of_one_head_are_read_as_one
# RC2 (IFACE-s79 r11 5) only the newest receipt file of a head is read
ss '                .and_then(|v| crate::receipts_together(v))' \
   '                .and_then(|v| v.first().cloned()) // knife' \
   the_receipts_of_one_head_are_read_as_one
# RC3 (IFACE-s79 r11 5) an older red step still counts after a green rerun
sl '            if !out.steps.contains(k) {' \
   '            if !out.steps.contains(k) || r.nonzero.contains(k) { // knife' \
   a_green_rerun_clears_an_older_red_and_split_files_add_up
# IN1 (IFACE-s79 r11 4) any failed step counts as a set-up failure: a failed test is called infra
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs '    let setup = text(&steps[at], "name").starts_with("set up ");' \
   '    let setup = !text(&steps[at], "name").is_empty(); // knife' --expect-fail a_job_that_died_in_set_up_is_infra_and_a_failed_test_is_not \
   -- cargo test -p aib-slice --locked --lib a_job_that_died_in_set_up_is_infra_and_a_failed_test_is_not
# IN2 (IFACE-s79 r11 4) a set-up failure stays FAILURE: the author is sent to fix nothing
ss '                if !red.is_empty() && infra.len() == red.len() {' \
   '                if !red.is_empty() && infra.len() == red.len() && false { // knife' \
   a_job_that_died_in_set_up_is_infra_not_a_fix
# IN3 (IFACE-s79 r11 4) the PR that waits does not say its cell needs the merge queue
sd '        if p.checks.as_deref() == Some("INFRA") {' \
   '        if p.checks.as_deref() == Some("INFRA") && false { // knife' \
   an_infra_failure_is_named_on_the_pr_that_waits_and_is_not_a_fix
# PG1 (519d0 11-15) a full page of open PRs is taken for the last one
ss '        let full = rows.len() >= PR_PAGE;' \
   '        let full = rows.len() > PR_PAGE; // knife' \
   pr_lists_are_read_in_small_pages_and_a_failed_read_is_tried_again
# PG2 (519d0 11-15) a read that fails is not tried again
ss '    gh.get(path).or_else(|_| gh.get(path))' \
   '    gh.get(path) // knife' \
   pr_lists_are_read_in_small_pages_and_a_failed_read_is_tried_again
# LH1 (5d33a M1) a PR whose local branch is on another commit drops out of the list
ss '        if let Some(ph) = pr_head.filter(|ph| *ph != r.head) {' \
   '        if let Some(ph) = pr_head.filter(|ph| *ph != r.head && false) { // knife' \
   an_open_pr_is_judged_at_its_head_wherever_the_local_branch_is
# RP1 (IFACE-s79 r10 3) a range that does not end at the reviewed head is taken
sl '            if hex(a) && hex(b) && head.starts_with(b) {' \
   '            if hex(a) && hex(b) && (head.starts_with(b) || true) { // knife' \
   the_scope_of_a_review_is_read_from_its_range
# RP2 (IFACE-s79 r10 3) a review at a merge covers everything under it
sl '            _ if p.merge => BTreeSet::new(),' \
   '            _ if p.merge && false => BTreeSet::new(), // knife' \
   passed_reviews_under_a_head_are_added_up
# RP3 (IFACE-s79 r10 3) an increment with nothing reviewed under it covers the whole branch
sl '                if under.is_empty() {' \
   '                if false && under.is_empty() { // knife' \
   passed_reviews_under_a_head_are_added_up
# RP4 (IFACE-s79 r10 3) a merge resolved by hand that nobody reviewed is let through
sl '            .filter(|r| !parts.iter().any(|p| p.head == r.commit))' \
   '            .filter(|r| false && !parts.iter().any(|p| p.head == r.commit)) // knife' \
   passed_reviews_under_a_head_are_added_up
# RP5 (IFACE-s79 r10 3) a head with an uncovered commit counts as reviewed
sl '        !self.files.is_empty() && self.uncovered.is_empty() && self.unreviewed_merges.is_empty()' \
   '        !self.files.is_empty() && self.unreviewed_merges.is_empty() // knife' \
   a_head_covered_by_passed_reviews_counts_as_reviewed
# RP6 (IFACE-s79 r10 3) the passed reviews under a head are never added up
ss '            if !r.at_head && r.passed() {' \
   '            if !r.at_head && r.passed() && false { // knife' \
   a_head_stacked_from_reviewed_slices_counts_as_reviewed
# RP7 (IFACE-s79 r10 3) a head reviewed by parts is still said to lack its review
sd '        Some(_) if b.reviewed() => {}' \
   '        Some(_) if b.reviewed() && false => {} // knife' \
   a_head_reviewed_by_parts_does_not_lack_a_review
# MC1 (f1fae COMPARE r2 1) an open PR that conflicts with main is said to wait for its checks
sl '        .filter(|c| pr_open && c.conflict)' \
   '        .filter(|c| pr_open && c.conflict && false) // knife' \
   an_open_pr_that_conflicts_with_main_merges_main_first
# SR1 (f1fae COMPARE r2 2) a green receipt on a stack is not counted for the PRs in it
ss '                if s.head != b.head && s.reach.contains(&b.head) {' \
   '                if false && s.head != b.head && s.reach.contains(&b.head) { // knife' \
   a_green_receipt_on_a_stack_of_plain_merges_counts_for_the_prs_in_it
# SR2 (f1fae COMPARE r2 2) a stack with a change of its own inside a merge still counts
ss '        if auto != tree.trim() {' \
   '        if false && auto != tree.trim() { // knife' \
   a_green_receipt_on_a_stack_of_plain_merges_counts_for_the_prs_in_it
# RB1 (S79, b433b's receipts) a .out file from the shared receipt-run.sh is not read as a receipt
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '    if ![".txt", ".out", ".log"].iter().any(|e| name.ends_with(e)) {' \
   '    if ![".txt", ".log"].iter().any(|e| name.ends_with(e)) { // knife' --expect-fail the_shared_receipt_run_files_are_read_at_their_head \
   -- cargo test -p aib-slice --locked --test sweep the_shared_receipt_run_files_are_read_at_their_head
# RB2 (S79, b433b's receipts) any key=N on a line is an rc: TESTS-TOTAL suites=166 turns the receipt red
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '            } else if key.strip_suffix("_rc").is_some_and(|k| !k.is_empty()) {' \
   '            } else if !key.is_empty() { // knife' --expect-fail the_shared_receipt_run_files_are_read_at_their_head \
   -- cargo test -p aib-slice --locked --test sweep the_shared_receipt_run_files_are_read_at_their_head
# KB1 (S79, 519d0 11-15) a knife at rc 0 (did not bite) counts as one that bit: the receipt stays green
sl '                    let bit = val.parse::<i64>().is_ok_and(|rc| rc != 0) && !invalid;' \
   '                    let bit = val.parse::<i64>().is_ok() && !invalid; // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# KB2 (S79) a knife marked INVALID (did not run / did not build) counts as one that bit
sl '                    let invalid = toks.get(i + 1).is_some_and(|w| w.starts_with("INVALID"));' \
   '                    let invalid = toks.get(i + 1).is_some_and(|w| w.starts_with("INVALID-NEVER")); // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# KB3 (S79) a knife with no number (anchor-missing) is skipped, not red
sl '                    if !bit {' '                    if !bit && val.parse::<i64>().is_ok() { // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# KB4 (S79) a line saying ANCHOR-MISSING does not make the receipt red
sl '        if line.contains("ANCHOR-MISSING") {' '        if line.contains("ANCHOR-MISSING-NEVER") { // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# KB5 (S79, 1ced7) receipt-run.sh's KNIVES-RC 1 / MISSES-COUNT 1 lines are not read
sl '        for (mark, key) in [("KNIVES-RC ", "knives_rc"), ("MISSES-COUNT ", "misses")] {' \
   '        for (mark, key) in [("KNIVES-RC-NEVER ", "knives_rc"), ("MISSES-COUNT-NEVER ", "misses")] { // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# BL1 (5d33a L3) --gate lets a PR that waits for CI (class 8) through
sl '    matches!(class, 1..=3 | 8)' '    matches!(class, 1..=3) // knife' the_six_classes_in_order
# BL2 (5d33a L3) a PR that waits for CI leaves the queue: its clashes are not listed
ss '        .filter(|(_, b)| matches!(crate::classify(b, o.hours).0, 1 | 2 | 8))' \
   '        .filter(|(_, b)| matches!(crate::classify(b, o.hours).0, 1 | 2)) // knife' \
   a_job_that_died_in_set_up_is_infra_not_a_fix
# DU1 (5d33a L1) a PR read on two pages is listed twice
ss '            .is_none_or(|n| seen.insert(n))' \
   '            .is_none_or(|n| { seen.insert(n); true }) // knife' \
   an_open_pr_read_on_two_pages_is_listed_once
# TO1 (S79, 519d0 11-15) a branch another seat took over is still a to-do of its own
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '    if let Some((t, _)) = b.taken_over_by.as_ref().filter(|_| !pr_open) {' \
   '    if let Some((t, _)) = b.taken_over_by.as_ref().filter(|_| pr_open) { // knife' --expect-fail a_branch_taken_over_or_absorbed_is_not_a_to_do \
   -- cargo test -p aib-slice --locked --test sweep a_branch_taken_over_or_absorbed_is_not_a_to_do
# TO2 (S79) an older integ-src branch of another seat takes over a newer one
ss '                    && *at > b.last_commit_at' '                    && (*at > b.last_commit_at || *at > 0) // knife' \
   a_branch_taken_over_or_absorbed_is_not_a_to_do
# TO3 (S79) one shared name part (3a) is enough to take a branch over
sl "    !key.is_empty() && (key == core || (key.contains('-') && core.ends_with(&format!(\"-{key}\"))))" \
   "    !key.is_empty() && (key == core || core.ends_with(&format!(\"-{key}\"))) // knife" \
   a_taker_branch_names_the_core_of_the_branch_it_carries
# AB1 (S79) a red receipt carried by a green integration head still makes a fix
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '        .filter(|_| !pr_open && receipt_at.is_some_and(|r| !r.green()))' \
   '        .filter(|_| pr_open && receipt_at.is_some_and(|r| !r.green())) // knife' --expect-fail a_branch_taken_over_or_absorbed_is_not_a_to_do \
   -- cargo test -p aib-slice --locked --test sweep a_branch_taken_over_or_absorbed_is_not_a_to_do
# AB2 (S79) any green receipt not under the head counts as one above it
ss '        let mut args = vec!["rev-list", "--ignore-missing", "--ancestry-path", &not_here];' \
   '        let mut args = vec!["rev-list", "--ignore-missing", &not_here]; // knife' \
   a_branch_taken_over_or_absorbed_is_not_a_to_do
# AB3 (S79) a plain commit stacked on a red head absorbs it (only a merge does)
ss '        args.push("--merges");' '        args.push("--topo-order"); // knife' \
   the_shared_receipt_run_files_are_read_at_their_head
# AO1 (519d0 11-15) an absorbed branch with a big workflow file is still a fix
sl '        .filter(|_| !pr_open && receipt_at.is_some_and(|r| !r.green()))' \
   '        .filter(|_| b.workflow_too_big.is_empty() && !pr_open && receipt_at.is_some_and(|r| !r.green())) // knife' \
   an_absorbed_branch_is_in_progress_before_its_workflow_size
# AO2 (5d33a 254a579d L1) an absorbed branch with an unfixed H/M review is in progress
sl '    if let Some(r) = review_at.filter(|r| r.h + r.m > 0) {' \
   '    if let Some(r) = review_at.filter(|r| r.h + r.m > 99) { // knife' \
   an_absorbed_branch_is_in_progress_before_its_workflow_size
# #755 (519d0 11-15): required cells (mergewatch list) and GitHub's workflow size limit
# MR1 a PR green everywhere but missing a required cell stays SUCCESS
ss '    if !missing.is_empty() && p.checks.as_deref() == Some("SUCCESS") {' \
   '    if !missing.is_empty() && p.checks.as_deref() == Some("SUCCESS-NEVER") { // knife' \
   a_pr_without_its_required_cells_or_over_the_workflow_limit_is_a_fix
# MR2 a change under crates/ requires no rust cell
sl '        f.starts_with("crates/")' '        f.starts_with("crates-NEVER/") // knife' \
   required_cells_follow_the_merge_queue_and_big_workflows_are_found
# MR3 MISSING is not a fix
sl '            .filter(|p| p.open && p.checks.as_deref() == Some("MISSING"))' \
   '            .filter(|p| p.open && p.checks.as_deref() == Some("MISSING-NEVER")) // knife' \
   a_missing_required_cell_or_a_big_workflow_is_a_fix
# MR4 the cloud knife jobs the head names are not required
sl '            .filter(|c| names.contains(*c))' '            .filter(|c| names.contains(*c) && c.is_empty()) // knife' \
   required_cells_follow_the_merge_queue_and_big_workflows_are_found
# WB1 a workflow one byte over the limit is not found
sl '            (bytes > WORKFLOW_MAX_BYTES).then(|| (path.to_owned(), bytes, at.to_owned()))' \
   '            (bytes > WORKFLOW_MAX_BYTES + 1).then(|| (path.to_owned(), bytes, at.to_owned())) // knife' \
   required_cells_follow_the_merge_queue_and_big_workflows_are_found
# WB2 a workflow over the limit only after the merge into main is not found
ss '                            for big in crate::too_big(&s, "merged") {' \
   '                            for big in crate::too_big(&s.replace(char::from(9), " "), "merged") { // knife' \
   a_pr_without_its_required_cells_or_over_the_workflow_limit_is_a_fix
# WB3 a workflow over the limit is not a fix
sl '    if !b.workflow_too_big.is_empty() {' '    if b.workflow_too_big.len() > 9 { // knife' \
   a_missing_required_cell_or_a_big_workflow_is_a_fix
# TO4 (5d33a L2) a taker with the right name but without the branch's commits takes it over
ss "        .is_ok_and(|s| !s.lines().any(|l| l.starts_with('+')))" \
   "        .is_ok_and(|s| !s.lines().any(|l| l.starts_with(\"+NEVER\"))) // knife" \
   a_branch_taken_over_or_absorbed_is_not_a_to_do
# MR5 (5d33a L3) a check-runs page that does not hold every run still gives names
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs '    if total.is_some_and(|t| t > listed) {' \
   '    if total.is_some_and(|t| t > listed.saturating_mul(2)) { // knife' --expect-fail check_names_are_none_when_the_page_does_not_hold_every_run \
   -- cargo test -p aib-slice --locked --lib check_names_are_none_when_the_page_does_not_hold_every_run
# RR1 (5d33a) a receipt marked RECEIPT-RED (cut short before the knife summary) is green
sl '            ("RECEIPT-RED", "receipt_red"),' '            ("RECEIPT-RED-NEVER", "receipt_red"), // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# RR2 a receipt marked KNIVES-INCOMPLETE is green
sl '            ("KNIVES-INCOMPLETE", "knives_incomplete"),' '            ("KNIVES-INCOMPLETE-NEVER", "knives_incomplete"), // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# RS1 (519d0) a sealed receipt that ended without KNIVES-RC is green
sl '    if has("RECEIPT-SHA256 ") && has("END ") && !has("KNIVES-RC ") {' \
   '    if has("RECEIPT-SHA256 ") && has("END-NEVER ") && !has("KNIVES-RC ") { // knife' \
   a_knife_that_did_not_bite_makes_the_receipt_red
# RF1 (519d0) a receipt with <run-id>.red beside it is green
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs '                .is_some_and(|(stem, _)| dir.join(format!("{stem}.red")).exists());' \
   '                .is_some_and(|(stem, _)| dir.join(format!("{stem}.red-never")).exists()); // knife' \
   --expect-fail a_receipt_with_a_red_file_beside_it_is_red \
   -- cargo test -p aib-slice --locked --lib a_receipt_with_a_red_file_beside_it_is_red
# S79-anc 2-5 (519d0 11-16, 1ced7): automatic dispatch sent fixes nobody had to do
# AN1 5: a whole green run does not close the older red files (#809 read red)
sl '        .position(Receipt::whole_green)' '        .position(|_| false) // knife' \
   a_newer_whole_green_run_closes_the_older_red_ones
# AN2 5: KNIVES-RC 0 / MISSES-COUNT 0 are not steps, so an older red one is added back
sl '                    put(&mut out, key, red);' '                    if red { put(&mut out, key, true); } // knife' \
   a_newer_whole_green_run_closes_the_older_red_ones
# AN3 4: a PR is listed on every file of the clash, its own or main's
ss '                        Some(theirs) => crate::own_clash(&c.files, theirs),' \
   '                        Some(_) => c.files, // knife' \
   a_pr_is_listed_against_another_only_on_files_that_one_changes
# AN4 4: own_clash keeps files the other PR did not change
python3 -I scripts/knife.py crates/aib-slice/src/lib.rs '        .filter(|f| theirs.contains(*f))' \
   '        .filter(|_| !theirs.is_empty()) // knife' \
   --expect-fail a_pr_is_listed_against_another_only_on_files_that_one_changes \
   -- cargo test -p aib-slice --locked --test sweep a_pr_is_listed_against_another_only_on_files_that_one_changes
# AN5 2: a carried branch is listed again
sd '        if carried(b, r, hours) && !review_open {' '        if carried(b, r, hours) && !review_open && false { // knife' \
   a_carried_branch_is_no_to_do_and_a_local_one_is_not_sent_to_fix_its_clash
# AN6 2: a branch under a stack is not carried by it
sd '            && x.stacked_on.contains(&b.branch)' '            && x.stacked_on.contains(&b.branch) && false // knife' \
   a_carried_branch_is_no_to_do_and_a_local_one_is_not_sent_to_fix_its_clash
# AN7 2: work in progress or a branch given up above still carries the branch under it
sd '            && matches!(classify(x, hours).0, 1 | 2 | 3 | 8)' \
   '            && matches!(classify(x, hours).0, 1 | 2 | 3 | 4 | 6 | 8) // knife' \
   a_carried_branch_is_no_to_do_and_a_local_one_is_not_sent_to_fix_its_clash
# AN8 2: an absorbed branch whose review asks for a fix is dropped
sd '            .is_some_and(|x| x.at_head && x.h + x.m > 0);' '            .is_some_and(|x| x.at_head && x.h + x.m > 0 && false); // knife' \
   a_carried_branch_is_no_to_do_and_a_local_one_is_not_sent_to_fix_its_clash
# AN9 3: a branch only on this machine is sent to fix its clash with main
sd '        let to_fix = class == 3 || (clash && !local_only(b));' \
   '        let to_fix = class == 3 || (clash && (!local_only(b) || true)); // knife' \
   a_carried_branch_is_no_to_do_and_a_local_one_is_not_sent_to_fix_its_clash
# AN10 5 (5d33a 273bdce9 M1): a run given no knife file closes an older run's knife misses
sl '                if red || !not_run {' '                if red || !not_run || true { // knife' \
   a_newer_whole_green_run_closes_the_older_red_ones
# S79-c1fb (5d33a r6 C1, 519d0 11-16): a cancelled cell run green on our own Mac
# FB1 a FALLBACK receipt that left steps out (SKIP-REUSED) counts
sl '        "SKIP-REUSED",' '        "X-KNIFE", // knife' \
   a_fallback_receipt_counts_only_whole_and_green
# FB2 a red step in a FALLBACK receipt counts
sl '        && steps.iter().all(|l| l.starts_with("STEP rc=0 "))' \
   '        && steps.iter().all(|l| l.starts_with("STEP rc=")) // knife' \
   a_fallback_receipt_counts_only_whole_and_green
# FB3 a FALLBACK receipt stands in for a smoke cell
sl '    !cell.starts_with("smoke")' '    !cell.starts_with("smoke-knife")' \
   a_fallback_receipt_counts_only_whole_and_green
# FB4 one cell covered is taken for all of them
sd '            if left > 0 {' '            if covered.is_empty() { // knife' \
   a_cancelled_cell_run_green_on_our_own_mac_is_not_waited_for
# FB5 a FALLBACK receipt of another cell covers this one
sd '        .filter(|n| crate::fallback_may_cover(n) && b.fallback_cells.iter().any(|(c, _)| c == *n))' \
   '        .filter(|n| crate::fallback_may_cover(n) && !b.fallback_cells.is_empty()) // knife' \
   a_cancelled_cell_run_green_on_our_own_mac_is_not_waited_for
# FB6 a FALLBACK receipt with a .red file beside it counts
python3 -I scripts/knife.py crates/aib-slice/src/scan.rs \
  '            if let Some(f) = crate::fallback_cell(&text).filter(|_| !red_file) {' \
  '            if let Some(f) = crate::fallback_cell(&text) { // knife' \
  --expect-fail a_fallback_receipt_is_read_by_head_and_cell_unless_marked_red \
  -- cargo test -p aib-slice --locked --lib a_fallback_receipt_is_read_by_head_and_cell_unless_marked_red
# S79-c1fb2 (5d33a 82336470 L1): the receipt ran this head's job
# FB7 a FALLBACK receipt of other lines than the head's job counts
ss '                        .filter(|(f, _)| crate::fallback_at_head(f, &yml))' \
   '                        .filter(|(f, _)| crate::fallback_at_head(f, &yml) || true) // knife' \
   a_fallback_receipt_counts_only_when_its_job_matches_the_head_workflow
# FB8 the job's lines are not compared
sl '    sha == f.slice_sha && named' '    (sha == f.slice_sha || true) && named // knife' \
   a_fallback_receipt_counts_only_for_the_job_it_ran_at_that_head
# FB9 the cell is not checked against the job
sl '    sha == f.slice_sha && named' '    sha == f.slice_sha && (named || true) // knife' \
   a_fallback_receipt_counts_only_for_the_job_it_ran_at_that_head
# FB10 a cell is the job's when its name merely starts with the job's
sl '        .is_some_and(|l| f.cell == l || f.cell.starts_with(&format!("{l} (")));' \
   '        .is_some_and(|l| f.cell == l || f.cell.starts_with(l.as_str())); // knife' \
   a_fallback_receipt_counts_only_for_the_job_it_ran_at_that_head
# FB11 a step skipped on macOS under a condition GitHub would run it counts
sl '            .all(|l| skipped_on_macos_ok(l));' '            .all(|l| skipped_on_macos_ok(l) || true); // knife' \
   a_fallback_receipt_counts_only_whole_and_green
# FB13 a receipt whose run met an if: it did not know (UNKNOWN-IF) counts
sl '        "UNKNOWN-IF",' '        "X-KNIFE-IF", // knife' a_fallback_receipt_counts_only_whole_and_green
# FC1 a cell-steps receipt (STEP rc= / CELL-RED / END rc=) never runs to its end (519d0 11-16 08:33)
sl '    shared || cell' '    shared || (cell && false) // knife' a_cell_steps_receipt_is_read_as_a_receipt_of_its_head
# FC2 a red cell-steps STEP is read green
sl 'put(&mut out, &name, rc != Some(0));' 'put(&mut out, &name, rc.is_none()); // knife' a_cell_steps_receipt_is_read_as_a_receipt_of_its_head
# FC3 END rc=1 / CELL-RED 1 are read green
sl 'put(&mut out, key, n != "0");' 'put(&mut out, key, n.is_empty()); // knife' a_cell_steps_receipt_is_read_as_a_receipt_of_its_head
# FC4 a reused step is not red
sl '("SKIP-REUSED ", "skip_reused"),' '("X-KNIFE ", "skip_reused"),' a_cell_steps_receipt_is_read_as_a_receipt_of_its_head
# FC5 a cell-steps run with no STEP line ran to its end
sl 'l.starts_with("STEP rc="));' 'l.starts_with("STEP rc=") || true); // knife' a_cell_steps_receipt_is_read_as_a_receipt_of_its_head
# FC6 a clash with another PR is noted though main clashes on other files or not at all
sd 'main_files.contains(x)' 'main_files.contains(x) || true' a_clash_with_another_pr_is_noted_on_a_fix_only_beside_the_same_clash_with_main
# FC7 a PR carried by an integration PR still lists its clashes
sd 'if pr.is_some() && !in_other_prs(b) {' 'if pr.is_some() && (!in_other_prs(b) || true) {' a_clash_with_another_pr_is_noted_on_a_fix_only_beside_the_same_clash_with_main
# FC8 a workflow over the limit is not named on the fix
sd 'in &b.workflow_too_big {' 'in b.workflow_too_big.iter().take(0) {' a_clash_with_another_pr_is_noted_on_a_fix_only_beside_the_same_clash_with_main
# FB12 a job's lines run on into the next job
sl '        .find(|&i| is_job(lines[i]))' '        .find(|&i| is_job(lines[i]) && i == 0) // knife' \
   a_fallback_receipt_counts_only_for_the_job_it_ran_at_that_head
