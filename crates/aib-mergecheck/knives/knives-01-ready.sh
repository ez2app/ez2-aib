#!/usr/bin/env bash
# aib-mergecheck ready (S73-ready 片 1, IFACE-mergecheck-ready-7b81e-1117 r2 §9): each knife takes one rule away
# and must turn its own test red. Run by the one "crate knives" step (crates/aib-knife/run-knives.sh rust: macOS,
# job rust, 30 minutes by default); aib-xtask knife-anchors reads the same folder. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
mk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p "$5" --locked "$4"; }
# MC1 a PR on another base is mergeable
mk crates/aib-mergecheck/src/lib.rs 'if pr.base == cfg.main {' 'if true { // knife' a_pr_based_on_a_side_branch_is_not_mergeable_and_says_the_base aib-mergecheck
# MC2 a draft passes
mk crates/aib-mergecheck/src/lib.rs 'V::Fail("還是草稿".into())' 'V::Pass' a_green_draft_is_not_mergeable aib-mergecheck
# MC3 mergeable PRs are not checked against each other
mk crates/aib-mergecheck/src/lib.rs 'if let Merge::Conflict(_) | Merge::Err(_) = w.merge_after(&main, &ok[i].1, &ok[j].1) {' \
      'if let Merge::Err(_) = w.merge_after(&main, &ok[i].1, &ok[j].1) {' \
      two_clean_prs_that_clash_are_both_mergeable_and_marked_not_together aib-mergecheck
# MC4 an empty gh answer is not unread
mk crates/aib-mergecheck/src/lib.rs 'unread.push("gh PR 清單是空的回覆".into());' 'let _ = 0; // knife' \
      gh_returning_nothing_or_timing_out_is_unread_and_rc_3 aib-mergecheck
# MC5 two samples are enough for an estimate
mk crates/aib-core/src/median.rs 'pub const MIN_SAMPLES: usize = 3;' 'pub const MIN_SAMPLES: usize = 2;' \
      two_samples_print_unknown aib-mergecheck
# MC6 an unread machine line still gives 現在合
mk crates/aib-mergecheck/src/lib.rs 'if !unread.is_empty() {' 'if false { // knife' an_unread_receipt_host_never_yields_merge_now aib-mergecheck
# MC7 a cancelled cell reads as red (the verdict word, not the push: a never-pushed `mut` would be a warning)
mk crates/aib-mergecheck/src/lib.rs 'V::Cancelled(format!("被取消:{}", cancelled.join("、")))' 'V::Fail(format!("被取消:{}", cancelled.join("、")))' \
      a_cancelled_check_is_listed_as_cancelled_not_red_or_green aib-mergecheck
# MC8 the station judge's answer is ignored
mk crates/aib-mergecheck/src/lib.rs 'Some(p) => V::Fail(p.clone()),' 'Some(_) => V::Pass,' review_item_calls_the_station_judge_not_a_copy aib-mergecheck
# MC9 a lost step is not reported
mk crates/aib-mergecheck/src/steps.rs 'if have < *n {' 'if have < *n && false {' \
      a_step_lost_in_a_hand_merge_fails_the_step_item aib-mergecheck
# MC10 the time left is the median of every sample minus what already ran
mk crates/aib-core/src/median.rs 'let longer: Vec<(i64, u64)> = s.into_iter().filter(|x| x.1 > elapsed).collect();' \
      'let longer: Vec<(i64, u64)> = s.into_iter().filter(|_| true).collect();' \
      the_remaining_time_is_conditional_on_elapsed aib-core
# MC11 an integration PR is judged by one card
mk crates/aib-mergecheck/src/lib.rs 'if pr.branch.starts_with("integ/") {' 'if false { // knife' \
      an_integration_pr_is_unread_and_points_at_its_check_file aib-mergecheck
# MC12 the review is judged against the card's (last written) head, not the PR's head now
mk crates/aib-mergecheck/src/lib.rs 'let head_now: &str = &pr.head;' \
      'let head_now: &str = aib_plan::card::sha40s(node.evidence.as_deref().unwrap_or("")).last().copied().unwrap_or("");' \
      a_review_of_an_older_head_is_not_review_pass aib-mergecheck
# MC13 green mergeable PRs are not told to chain-merge (keeps clash / never_auto_n read: unused = warning)
mk crates/aib-mergecheck/src/lib.rs 'if k >= 2 && !clash && never_auto_n == 0 {' 'if k >= 2 && !clash && never_auto_n == 0 && false {' \
      green_mergeable_prs_say_chain_merge_not_stack aib-mergecheck
# MC14 a rerun cell is judged by its first run
mk crates/aib-mergecheck/src/lib.rs '(Some(a), Some(b)) => b >= a,' '(Some(_), Some(_)) => false,' a_rerun_cell_is_judged_by_its_latest_run aib-mergecheck
# MC15 a PR that changes .github/ loses its 新閘 word (the category comes from the default list)
mk crates/aib-mergecheck/src/lib.rs 'vec![(".github/".into(), "新閘".into())]' 'vec![(".github/".into(), "工具鏈".into())]' \
      a_new_gate_pr_says_people_can_merge_it_early aib-mergecheck
# ---- review 9fae2967 (5d33a 0/1/4/3) ----
# MC16 (M1) main is read without refreshing first
mk crates/aib-mergecheck/src/lib.rs 'let refreshed = w.refresh();' 'let refreshed: Result<(), String> = Ok(());' \
      refresh_runs_before_main_is_read aib-mergecheck
# MC17 (L4) the whole card record goes to the station judge
mk crates/aib-mergecheck/src/lib.rs '.filter(|seg| !key.is_empty() && seg.contains(key))' '.filter(|seg| !key.is_empty() || seg.contains(key))' \
      a_release_word_elsewhere_on_the_card_does_not_release_an_m aib-mergecheck
# MC18 (L2) a self-hosted `macOS` label is not macOS
mk crates/aib-mergecheck/src/fsworld.rs 't.to_ascii_lowercase().starts_with("macos")' 't.starts_with("macos")' \
      a_self_hosted_macos_label_counts_as_macos aib-mergecheck
# MC19 (L1) a deleted file is not a never-auto category
mk crates/aib-mergecheck/src/lib.rs "if st.starts_with('D') {" "if st.starts_with('D') && false {" \
      never_auto_lists_each_category_with_its_own_word aib-mergecheck
# MC20 (L3) two PRs compared head against head, not with the first merged into main
mk crates/aib-mergecheck/src/lib.rs 'w.merge_after(&main, &ok[i].1, &ok[j].1)' 'w.merge_tree(&ok[i].1, &ok[j].1)' \
      two_prs_are_compared_with_the_first_merged_into_main aib-mergecheck
# MC21 (L1) a new Cargo.lock package is not found (take(0) keeps both sides read)
mk crates/aib-mergecheck/src/lib.rs 'after.difference(&before).cloned().collect()' 'after.difference(&before).take(0).cloned().collect()' \
      never_auto_lists_each_category_with_its_own_word aib-mergecheck
# MC22 (N3) a CI estimate keeps the run id as its time (.or(None): a plain self-assignment is a rustc error)
mk crates/aib-mergecheck/src/lib.rs 'e.last_at_ms = None;' 'e.last_at_ms = e.last_at_ms.or(None);' \
      two_samples_print_unknown aib-mergecheck
# MC25 (0eed7 3d92c6b6 M1) the PR list asks GitHub for commits again (1,000,000 nodes, refused)
mk crates/aib-mergecheck/src/fsworld.rs 'statusCheckRollup,isDraft,body' 'statusCheckRollup,commits,isDraft,body' \
      the_pr_list_query_does_not_ask_github_for_commits aib-mergecheck
# MC26 (M1) a PR whose commits can't be listed keeps a stale list
mk crates/aib-mergecheck/src/fsworld.rs 'o.remove("commits");' 'let _ = 0; // knife' \
      local_commits_fill_the_list_and_an_unlisted_pr_stays_without aib-mergecheck
# MC27 (N1) nothing read still gives a macOS wait as the verdict
mk crates/aib-mergecheck/src/lib.rs 'if !whole.is_empty() {' 'if false { // knife' \
      gh_returning_nothing_or_timing_out_is_unread_and_rc_3 aib-mergecheck
# MC28 (0eed7 r2 M2) the head no longer counts: a summary card listing #N ties the PR's own card
mk crates/aib-mergecheck/src/lib.rs 'score += 4;' 'score += 0;' \
      a_summary_card_listing_the_pr_later_does_not_take_its_card aib-mergecheck
# MC29 (M2) a tie at the top picks one card instead of unread
mk crates/aib-mergecheck/src/lib.rs 'many => Err(many.iter().map(|(_, n)| n.id.clone()).collect()),' 'many => Ok(Some(many[0].1)),' \
      two_cards_naming_the_same_head_are_unread_not_a_guess aib-mergecheck
