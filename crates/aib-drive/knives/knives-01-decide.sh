#!/usr/bin/env bash
# The "aib-drive decide knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-drive decide (DECISION-aib-drive-v1 item 7): each guard of the decision table, broken
# on purpose, must turn its own test red (2240e takeover 11-11; the original D1–D20 knife
# list was never committed, so these are rebuilt from the tests)
set -eo pipefail
set -eu
dk() { python3 -I scripts/knife.py crates/aib-drive/src/decide.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test decide "$3"; }
# D1 freeze unknown or on still merges
dk '    let freeze_off = match &s.freeze {' \
   '    let freeze_off = true || match &s.freeze { // knife' \
   frozen_or_unknown_freeze_merges_nothing_and_reruns_nothing
# D4 drafts are merged
dk '    if pr.draft {' \
   '    if pr.draft && false { // knife' \
   drafts_and_guarded_paths_never_merge
# D6 an empty required list counts as all green
dk '    if required.is_empty() {' \
   '    if required.is_empty() && false { // knife' \
   an_empty_required_list_merges_nothing
# D7 a draft-only run counts as a pass
dk '            CellState::Success if c.after_ready => {}' \
   '            CellState::Success if c.after_ready || true => {} // knife' \
   cells_that_are_not_a_real_pass_block
# D11 an author counts as a non-author
dk '    matches!(by, Known::Known(s) if !authors.contains(s))' \
   '    matches!(by, Known::Known(s) if !authors.contains(s) || !s.is_empty()) // knife' \
   an_author_reviewing_their_own_commits_does_not_count
# D11 an unverified writer counts
dk '    matches!(by, Known::Known(s) if !authors.contains(s))' \
   '    !matches!(by, Known::Known(s) if authors.contains(s)) // knife' \
   an_unverified_reviewer_does_not_count_and_the_merge_names_its_evidence
# D12 a pass that leaves M counts
dk '        self.pass && self.counts[0] == 0 && self.counts[1] == 0' \
   '        self.pass // knife' \
   a_pass_that_leaves_a_medium_is_not_a_merge
# D12 no review at this head needed (a commit list that does not end at the head)
dk '    if last.is_some() && covered == last && !at_head.is_empty() {' \
   '    if last.is_some() && covered == last { // knife' \
   a_commit_list_that_does_not_end_at_the_head_does_not_merge
# D13 an increment from an uncovered commit extends the chain
dk '                Some(f) => matches!((idx(f), to), (Some(i), Some(t)) if i <= t),' \
   '                Some(f) => idx(f).is_some(), // knife' \
   queue_an_unreviewed_middle_commit_blocks_and_asks_for_exactly_that_range
# H2 failed reviews make the chain
dk '        .filter(|r| r.passes() || closed_by_increment(r, &others, commits))' \
   '        .filter(|r| r.pass || !r.pass || closed_by_increment(r, &others, commits)) // knife' \
   a_failed_full_review_plus_a_passing_increment_is_not_a_merge
# D20 another reviewer closes the findings
dk '            && r.by.get() == Some(writer)' \
   '            && (r.by.get() == Some(writer) || true) // knife' \
   the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch
# D20 an increment off the branch closes
dk '            && idx(&r.head).is_some_and(|j| j > at)' \
   '            && (idx(&r.head).is_some_and(|j| j > at) || true) // knife' \
   a_closing_increment_off_the_branch_closes_nothing
# D20 a partial closing list closes all
dk '                        .is_some_and(|closed| raised.iter().all(|f| closed.contains(f)))' \
   '                        .is_some_and(|closed| raised.iter().any(|f| closed.contains(f))) // knife' \
   the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch
# D20e repeated ids pass
dk '    unique.len() == raised.len()' \
   '    (unique.len() == raised.len() || true) // knife' \
   the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch
# D20d H count not checked
dk '        && of('"'"'H'"'"') == u64::from(counts[0])' \
   '        && (of('"'"'H'"'"') == u64::from(counts[0]) || true) // knife' \
   the_same_reviewer_closing_their_findings_in_an_increment_covers_the_branch
# D14 a failed acceptance passes
dk '        if lines.iter().all(|v| v.pass && v.not_checked.is_none()) {' \
   '        if lines.iter().all(|v| v.pass || !v.pass) { // knife' \
   g1_a_failed_acceptance_waits_for_the_captains_ruling_on_this_head
# D14 a ruling for another head counts
dk '                && r.head.as_deref() == Some(pr.head.as_str())' \
   '                && (r.head.as_deref() == Some(pr.head.as_str()) || true) // knife' \
   g1_a_failed_acceptance_waits_for_the_captains_ruling_on_this_head
# D14 acceptance of any head carries
dk '        let holds = |h: &Sha| *h == pr.head || pr.verify_carry.as_ref() == Some(h);' \
   '        let holds = |h: &Sha| *h == pr.head || pr.verify_carry.as_ref() == Some(h) || !h.is_empty(); // knife' \
   the_queue_acceptance_carries_from_cd33802b_to_766fa8f6_only_when_the_snapshot_says_so
# D16 integration without int/ merges
dk '        && !(pr.head_ref.starts_with("int/") || pr.head_ref.starts_with("release/"))' \
   '        && false // knife' \
   integration_prs_need_the_int_prefix_and_unmoved_originals
# D10b (F9d, 519d0 11-15) knives left to CI block on their own
dk '        Known::Known(KnifeScan::ToCi) => {' \
   '        Known::Known(KnifeScan::ToCi) => { g.block(); // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D10c (F9d, 5d33a N1) knives left to CI do not wait on the knife cells
dk '            gate_knife_cells(pr, &mut g);' \
   '            if pr.number == 0 { gate_knife_cells(pr, &mut g); } // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D10d (F9d) the cloud knife jobs are not knife cells
dk '    name.starts_with("rust (") || name.contains("knives")' \
   '    name.starts_with("rust (") // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D10e (F9d) a knife cell skipped by its paths blocks
dk '            (CellState::Success, true) | (CellState::Skipped, _) => {}' \
   '            (CellState::Success, true) => {} // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D10f (F9d, 5d33a 1c98e45d M1) knives left to CI pass with no rust (…) cell at all
dk '    if !cells.iter().any(|c| c.name.starts_with("rust (")) {' \
   '    if false { // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D10g (F9d) a knife cell green only on a draft run counts
dk '            (CellState::Success, true) | (CellState::Skipped, _) => {}' \
   '            (CellState::Success, _) | (CellState::Skipped, _) => {} // knife' \
   knives_left_to_ci_wait_on_the_cells_and_ask_nothing
# D16b (F9b) full-ci lets any branch name count, or integ/ counts without it
dk '        && !(pr.full_ci && pr.head_ref.starts_with("integ/"))' \
   '        && !(pr.full_ci || pr.head_ref.starts_with("integ/")) // knife' \
   integration_prs_need_the_int_prefix_and_unmoved_originals
# D15 moved originals do not block
dk '                for part in v.iter().filter(|x| x.collected != x.now) {' \
   '                for part in v.iter().filter(|x| x.collected != x.now && false) { // knife' \
   integration_prs_need_the_int_prefix_and_unmoved_originals
# D19 the journal is asked again
dk '            if s.done.contains(&k) || !seen.insert(k) {' \
   '            if !seen.insert(k) { // knife' \
   what_the_journal_holds_is_not_asked_again_and_a_busy_seat_is_nudged
# D19 two shared writes in one round
dk '            if shared {' \
   '            if shared && false { // knife' \
   two_clean_prs_give_one_merge_and_the_release_blocker_goes_first
# D3 an unknown merge slot merges
dk '    let slot_free = matches!(s.merge_slot_free, Known::Known(true))' \
   '    let slot_free = !matches!(s.merge_slot_free, Known::Known(false)) // knife' \
   every_unknown_blocks_the_merge
# D21 (n-1, 519d0 11-14) an unreadable note about the head is read as "no review"
dk '    if pr.unknown_notes.is_empty() {' \
   '    if true { // knife' \
   an_unreadable_note_about_the_head_holds_only_that_pr
