#!/usr/bin/env bash
# The "aib-drive snapshot knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-drive snapshot (PR-B, a9136 on 2240e's base): a source that cannot be read, a page that may
# have more, a deadline, a writer's claim — each broken on purpose turns its own test red (§15.17)
set -eo pipefail
set -eu
sk() { python3 -I scripts/knife.py "crates/aib-drive/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-drive --locked --test snapshot "$4"; }
# run_fixed lives in aib-ghread since the drive x expect wiring (a9136 DECISION-drive-expect-cycle); drive's tests still cover it
rk() { python3 -I scripts/knife.py crates/aib-ghread/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test snapshot "$3"; }
# S1 an unreadable notes folder reads as "no reviews"
sk snapshot.rs '    let files = read_notes(inp.pending)?;' \
   '    let files = read_notes(inp.pending).unwrap_or_default(); // knife' \
   an_unreadable_notes_folder_is_no_snapshot
# S2 an unreadable dm queue reads as "not busy"
sk source.rs '        let v = q.waiting(seat).map_err(|e| format!("dm {seat}: {e}"))?;' \
   '        let Ok(v) = q.waiting(seat) else { continue }; // knife' \
   an_unreadable_dm_queue_is_no_snapshot
# S3 no deadline: a call that hangs is waited for
rk '    let stdout = match out_rx.recv_timeout(left()) {' \
   '    let stdout = match Ok::<_, ()>(out_rx.recv().unwrap_or(Ok(Vec::new()))) { // knife' \
   a_call_past_its_deadline_is_killed_and_an_error
# S4 a non-zero exit with output is read as an answer
rk '    if !status.success() {' \
   '    if false && !status.success() { // knife' \
   a_non_zero_exit_is_an_error_even_with_output
# S5 a full page is read as the whole list
sk snapshot.rs '    if a.len() >= PER_PAGE {' \
   '    if a.len() > PER_PAGE { // knife' \
   a_full_page_of_open_pulls_is_no_snapshot
# S6 (F9) the writer is the first seat code in the file name, not the last
sk snapshot.rs '.filter(|p| is_seat(p)).last()' \
   '.find(|p| is_seat(p)) /* knife */' \
   a_writer_is_the_last_seat_code_in_the_file_name
# S7 commits of a PR that moved between reads are trusted
sk snapshot.rs '        Known::Known(c) if c.last() != Some(&head_sha) => {' \
   '        Known::Known(c) if c.last() != Some(&head_sha) && false => { // knife' \
   a_commit_list_that_does_not_end_at_the_head_is_unknown
# S8 with no journal, a cancelled / runner-red cell is re-run
sk snapshot.rs '        rerun_done: true,' \
   '        rerun_done: false, // knife' \
   a_cancelled_or_runner_red_cell_is_not_re_run_without_a_journal
# S9 (1ced7 #685 L3) the wait for the exit has no deadline
rk '    let status = match exit_rx.recv_timeout(left()) {' \
   '    let status = match Ok::<_, ()>(exit_rx.recv().unwrap_or_else(|_| Err(std::io::ErrorKind::Other.into()))) { // knife' \
   a_child_that_closes_its_output_and_keeps_running_is_killed_at_the_deadline
# S10 (1ced7 #685 L2) a file moved out of a guarded path is not seen
sk snapshot.rs '            if let Some(old) = st(f, "previous_filename") {' \
   '            if let Some(old) = st(f, "previous_filename").filter(|_| false) { // knife' \
   a_file_moved_out_of_a_guarded_path_touches_it
# S11 (1ced7 #685 L1) a full page of rules is read as the whole list
sk snapshot.rs '    let rules = match whole(&v, "rules") {' \
   '    let rules = match arr(&v, "rules") { // knife' \
   a_full_page_of_rules_is_unknown
# S12 (1ced7 #685 r2 L1) an already reaped child is killed by a pid that may be reused
rk '        if let Err(TryRecvError::Empty) = exit_rx.try_recv() {' \
   '        if let Err(TryRecvError::Empty) | Ok(_) = exit_rx.try_recv() { // knife' \
   a_child_already_reaped_is_not_killed_by_pid_at_the_deadline
# S13 (Codex #685 M2) a dm item without a string priority reads as not urgent
sk source.rs '                .ok_or_else(|| bad("priority not a string"))?;' \
   '                .unwrap_or_default(); // knife' \
   a_dm_item_that_does_not_read_is_no_snapshot
# S14 (Codex #685 M2) a dm item whose read_at does not read counts as read
sk source.rs '                _ => return Err(bad("read_at missing or not a time")),' \
   '                _ => false, // knife' \
   a_dm_item_that_does_not_read_is_no_snapshot
# S15 (Codex #685 M3) a broken note about the PR's head is skipped as "not reviewed"
sk snapshot.rs '                unknown.push(crate::observe::clean(&format!("{}: {e}", f.name), MAX_NOTE));' \
   '                let _ = (e, MAX_NOTE, &mut unknown); // knife' \
   a_broken_note_about_the_head_makes_only_that_pr_unknown
# S16 (n-1, 519d0 11-14) one broken note about a head makes the whole round unknown again
sk snapshot.rs '        unknown_notes: notes.unknown,' \
   '        unknown_notes: if notes.unknown.is_empty() { notes.unknown } else { return Err(notes.unknown.join("; ")) }, // knife' \
   a_broken_note_about_the_head_makes_only_that_pr_unknown
# S17 (n-1) a broken note about an older commit, or an old note in pending, makes a PR unknown
sk snapshot.rs '            Err(e) if names_a_commit(&f.line, head) => {' \
   '            Err(e) if names_a_commit(&f.line, commits) || head.is_empty() => { // knife' \
   a_broken_note_off_the_head_changes_nothing
# S18 (n-1) the round does not list the PRs it could not judge
sk observe.rs '        .filter(|p| !p.unknown_notes.is_empty())' \
   '        .filter(|_| false) // knife' \
   a_broken_note_about_the_head_makes_only_that_pr_unknown
# S19 (F9, 519d0 11-15 S79-dec) the seats a merge names are not authors
sk snapshot.rs '        if seat != "unknown" {' \
   '        if seat != "unknown" && false { // knife' \
   authors_are_the_branch_seat_and_the_seats_of_merged_branches
# S20 (F9) merging main makes the authors unknown
sk snapshot.rs '        if w == "main" || w.ends_with("/main") {' \
   '        if false { // knife' \
   authors_are_the_branch_seat_and_the_seats_of_merged_branches
# S36 (F9) merge commits are not read: a collected slice's seat is no author
sk snapshot.rs '        if *p < 2 {' \
   '        if *p < 3 { // knife' \
   authors_are_the_branch_seat_and_the_seats_of_merged_branches
# S37 (F9) the seat right after Merge (Merge a9136 9d1ad69d …) is not read
sk snapshot.rs '        .filter(|w| is_seat(w))' \
   '        .filter(|_| false) // knife' \
   authors_are_the_branch_seat_and_the_seats_of_merged_branches
# S21 (F9) the oldest run of a cell is kept instead of the newest
sk snapshot.rs '            Some(old) if key(old) >= key(r) => {}' \
   '            Some(old) if key(old) <= key(r) => {} // knife' \
   the_newest_run_of_a_cell_replaces_the_older_one
# S22 (F9) every run of a cell is a result again (smoke N results)
sk snapshot.rs '    for c in latest_runs(runs) {' \
   '    for c in runs { // knife' \
   the_newest_run_of_a_cell_replaces_the_older_one
# S23 (F9) a knife that did not bite is not seen
sk snapshot.rs '    } else if misses != Some(0) {' \
   '    } else if misses != Some(0) && false { // knife' \
   receipts_read_the_way_receipt_run_prints_them
# S24 (F9) a knife that broke the build reads as a knife that survived
sk snapshot.rs '    } else if knife("INVALID") || has("INVALID") {' \
   '    } else if false { // knife' \
   receipts_read_the_way_receipt_run_prints_them
# S25 (F9) a head that moved during the run is not seen
sk snapshot.rs '    let moved = ends.any(|h| h.trim() != head);' \
   '    let moved = ends.any(|h| h.trim() != head) && false; // knife' \
   receipts_read_the_way_receipt_run_prints_them
# S38 (F9) a receipt whose knife section did not finish (receipt-run v2) reads as green
sk snapshot.rs '    let unfinished = has("KNIVES-INCOMPLETE") || has("RECEIPT-RED");' \
   '    let unfinished = false; // knife' \
   receipts_read_the_way_receipt_run_prints_them
# S39 (F9c, 519d0 11-15 09:03) a merge that names no seat makes the authors unknown again
sk snapshot.rs '        if let Some(seats) = merge_seats(s) {' \
   '        if let Some(seats) = merge_seats(s).or_else(|| panic!("knife")) { // knife' \
   authors_are_the_branch_seat_and_the_seats_of_merged_branches
# S40 (F9c) TEST-* receipts are not read
sk snapshot.rs '            if !(name.starts_with("RECEIPT-") || name.starts_with("TEST-")) {' \
   '            if !name.starts_with("RECEIPT-") { // knife' \
   the_newest_receipt_about_the_head_is_its_knife_scan
# S41 (F9c, 5d33a N2) a receipt that ended before its knife summary (RECEIPT-RED only) is skipped
sk snapshot.rs '    if !unfinished && !(has("KNIVES-RC ") || has("KNIVES-SKIPPED")) {' \
   '    if true && !(has("KNIVES-RC ") || has("KNIVES-SKIPPED")) { // knife' \
   receipts_read_the_way_receipt_run_prints_them
# S42 (F9c, 519d0 11-15 09:12) a LEASE=4 shell counts as a receipt
sk snapshot.rs '        .any(|n| n.trim() != "0")' \
   '        .any(|_| false) // knife' \
   self_written_receipts_and_lease_shells_read_as_043dd_wrote_them
# S43 (F9c) a self-written receipt (RC knives=) is not read
sk snapshot.rs '            .filter_map(|l| l.strip_prefix("RC knives="))' \
   '            .filter_map(|l| l.strip_prefix("RC knives=never")) // knife' \
   self_written_receipts_and_lease_shells_read_as_043dd_wrote_them
# S45 (F9c, 5d33a M1) a receipt given no knife file reads as a knife scan
sk snapshot.rs '    if !unfinished && has("KNIVES-NOT-RUN") {' \
   '    if false { // knife' \
   self_written_receipts_and_lease_shells_read_as_043dd_wrote_them
# S46 (F9d, 519d0 11-15) a receipt given no knife file is not left to CI
sk snapshot.rs '        return to_ci(moved);' \
   '        return None; // knife' \
   receipts_given_no_knives_leave_them_to_ci_and_replace_no_knife_scan
# S47 (F9d) a slice without knives (no knife line) is not left to CI
sk snapshot.rs '                l.starts_with("KNIVES-") || l.starts_with("RC knife") || l.starts_with("KNIFE")' \
   '                !l.is_empty() // knife' \
   receipts_given_no_knives_leave_them_to_ci_and_replace_no_knife_scan
# S48 (F9d, 5d33a L1) a no-knife receipt whose head moved is left to CI
sk snapshot.rs '    let to_ci = |moved: bool| (!moved).then(|| (head.to_ascii_lowercase(), KnifeScan::ToCi));' \
   '    let to_ci = |moved: bool| (!moved || head.len() == 40).then(|| (head.to_ascii_lowercase(), KnifeScan::ToCi)); // knife' \
   receipts_given_no_knives_leave_them_to_ci_and_replace_no_knife_scan
# S49 (F9d) a receipt left to CI replaces a knife scan
sk snapshot.rs '        found.sort_by_key(|x| x.3 != KnifeScan::ToCi);' \
   '        found.sort_by_key(|x| x.3 == KnifeScan::ToCi); // knife' \
   receipts_given_no_knives_leave_them_to_ci_and_replace_no_knife_scan
# S44 (F9c) RC knives=1 reads as green
sk snapshot.rs '                let scan = if rc.trim() == "0" && !moved {' \
   '                let scan = if !rc.is_empty() && !moved { // knife' \
   self_written_receipts_and_lease_shells_read_as_043dd_wrote_them
# S26 (F9) the oldest receipt about a head wins
sk snapshot.rs '        found.sort_by(|x, y| x.0.cmp(&y.0).then_with(|| x.1.cmp(&y.1)));' \
   '        found.sort_by(|x, y| y.0.cmp(&x.0).then_with(|| y.1.cmp(&x.1))); // knife' \
   the_newest_receipt_about_the_head_is_its_knife_scan
# S27 (F9) the freeze file is never found
sk snapshot.rs '    match std::fs::read(pending.join(FREEZE_FILE)) {' \
   '    match std::fs::read(pending.join("PUSH-FREEZE-not")) { // knife' \
   push_freeze_in_the_notes_folder_is_the_freeze
# S28 (F9) build does not read the freeze
sk snapshot.rs '        freeze: freeze(inp.pending),' \
   '        freeze: Known::Known(None), // knife' \
   push_freeze_in_the_notes_folder_is_the_freeze
# S29 (F9) build does not take the authors from the branch name
sk snapshot.rs '            authors_of(&head_ref, &lines)' \
   '            authors_of("x", &lines) // knife' \
   commit_authors_come_from_the_branch_and_its_merges
# S30 (F9) build does not read the receipts
sk snapshot.rs '    let receipts = knife_receipts(inp.pending);' \
   '    let receipts = knife_receipts(&inp.pending.join("none")); // knife' \
   the_newest_receipt_about_the_head_is_its_knife_scan
# S31 (F9b) an integ-src/ slice is taken for an integration
sk snapshot.rs '    head_ref.starts_with("int/") || head_ref.starts_with("integ/")' \
   '    head_ref.starts_with("int/") || head_ref.starts_with("integ") // knife' \
   an_integration_holds_the_open_prs_it_has_commits_of
# S32 (F9b) the oldest collected commit of an original is taken, not the newest
sk snapshot.rs '        if let Some(c) = theirs.iter().rev().find(|c| mine.contains(c)) {' \
   '        if let Some(c) = theirs.iter().find(|c| mine.contains(c)) { // knife' \
   an_integration_holds_the_open_prs_it_has_commits_of
# S33 (F9b) a PR whose commits cannot be read is skipped as "not an original"
sk snapshot.rs '            return Known::Unknown(format!("#{} commits unknown", other.number));' \
   '            continue; // knife' \
   an_integration_holds_the_open_prs_it_has_commits_of
# S34 (F9b) build never fills in the parts
sk snapshot.rs '        if parts.is_some() {' \
   '        if parts.is_some() && false { // knife' \
   an_integration_holds_the_open_prs_it_has_commits_of
# S35 (F9b) the full-ci label is not read
sk snapshot.rs '        full_ci: labels.iter().any(|l| l == "full-ci"),' \
   '        full_ci: false, // knife' \
   an_integration_holds_the_open_prs_it_has_commits_of
