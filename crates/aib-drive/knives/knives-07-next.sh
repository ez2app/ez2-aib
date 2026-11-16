#!/usr/bin/env bash
# The "aib-drive next knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 n-1 next layers 1-4 (a9136, KNIVES-next.md): each ranking / dispatch rule, broken, turns its test red.
set -eo pipefail
set -eu
nx() { python3 -I scripts/knife.py crates/aib-drive/src/next.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test next "$3"; }
# N1
nx '                    "no" => needs = "seat_busy".into(),' \
   '                    "no" => needs = "none".into(), // knife' \
   a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched
# N2
nx '            .then(swap(b).cmp(&swap(a)))' \
   '            .then(swap(a).cmp(&swap(b))) // knife' \
   a_swap_reviewer_first_and_unblocks_then_waited
# N3
nx '            .then(b.unblocks.cmp(&a.unblocks))' \
   '            .then(a.unblocks.cmp(&b.unblocks)) // knife' \
   a_swap_reviewer_first_and_unblocks_then_waited
# N4
nx '        if src.ok() {' \
   '        if src.ok() || src.state == "partial" { // knife' \
   an_incomplete_due_scan_puts_its_rows_in_unknown
# N5
nx '                && Some(f.seat.as_str()) != author' \
   '                && author.is_some() // knife' \
   the_reviewer_is_a_free_non_author_or_nobody
# N6
nx '    let complete = sources.iter().all(Source::ok) && unknown.is_empty();' \
   '    let complete = sources.iter().all(Source::ok); // knife' \
   a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched
# N7
nx '        } else if has(&RECEIPT_LACKS) {' \
   '        } else if has(&RECEIPT_LACKS) && i.lacks.len() == 1 { // knife' \
   what_a_merge_item_lacks_picks_its_row
# N8
nx '                    "yes" if size_rank(&f.size) >= size_rank(ROW_SIZE) => {}' \
   '                    "yes" if !f.size.is_empty() => {} // knife' \
   a_seat_takes_a_row_no_bigger_than_its_gap
# N9
nx '                starving: d.layer < 4 && d.waited_min > STARVE_MIN,' \
   '                starving: d.layer < 4 && d.waited_min > STARVE_MIN * 10, // knife' \
   a_row_waiting_over_four_hours_is_marked_starving
# N10
nx '            .filter(|b| b.abandonable)' \
   '            .filter(|b| b.abandonable && b.age_h == u64::MAX) // knife' \
   abandonable_is_never_a_row_and_merged_is_collected
# N11
nx '            f.dispatchable == "yes"' \
   '            f.dispatchable != "no" // knife' \
   a_partial_seat_list_is_used_seat_by_seat
# N12
nx '                    *given.entry(r.seat.clone()).or_default() += 1;' \
   '                    given.entry(r.seat.clone()).or_default(); // knife' \
   reviews_are_spread_over_the_free_seats
# N13
nx '                starving: d.layer < 4 && d.waited_min > STARVE_MIN,' \
   '                starving: d.waited_min > STARVE_MIN, // knife' \
   abandonable_is_never_a_row_and_merged_is_collected
# N14
nx '            needs = "no_owner".into();' \
   '            needs = "none".into(); // knife' \
   abandonable_is_never_a_row_and_merged_is_collected
# N15
nx '        if d.kind != "review" || reviewed.contains(&d.head) {' \
   '        if d.kind != "review" { // knife' \
   a_branch_not_pushed_is_reviewed_once_per_head
# N16
nx '        } else if has(&["no_review"])' \
   '        } else if has(&["no_review_knife"])' \
   a_branch_not_pushed_is_reviewed_once_per_head
# I1 (integration)
nx '        Err(e) if e.starts_with("stale:") => ("stale", Some(e.clone())),' \
   '        Err(e) if e.starts_with("stale-knife:") => ("stale", Some(e.clone())), // knife' \
   a_stale_cache_is_a_stale_source
# I2 (integration)
nx '                && !f.unread_unknown()' \
   '                && f.role != "knife"' \
   a_seat_whose_unread_is_not_known_is_never_sent_to
# I3 (integration)
nx '                Some(Some(f)) if f.unread_unknown() => {' \
   '                Some(Some(f)) if f.unread_unknown() && f.seat.is_empty() => { // knife' \
   a_seat_whose_unread_is_not_known_is_never_sent_to
# I4 (integration)
nx '            .map(|p| p.merged)' \
   '            .map(|_| false) // knife' \
   the_plan_adds_layer_five_after_the_slice_layers
# I5 (integration; C7b re-anchor: tidy_scan is set apart for complete, IFACE r12, and must come back)
nx '    n.sources.extend(optional);' \
   '    drop(optional); // knife' \
   the_plan_adds_layer_five_after_the_slice_layers
# I6 (integration)
nx '        if name == "seats" {' \
   '        if name == "seats-knife" {' \
   the_plan_adds_layer_five_after_the_slice_layers
# I7 (integration; re-anchored at receipt ad83e932: since T1 the roster already makes it not complete)
nx '                    why: "plan 讀不到".into(),' \
   '                    why: "名冊讀不到".into(), // knife' \
   the_plan_adds_layer_five_after_the_slice_layers
# I8 (integration)
nx '        listed.or_else(|| self.more.and_then(|m| m.merged(dep)))' \
   '        listed.or_else(|| self.more.and_then(|m| m.merged(&Dep::SkillsPr(0)))) // knife' \
   a_pr_sweep_does_not_list_is_asked_of_github_once
# I9 (integration)
nx '        if let Some(v) = self.asked.borrow().get(n) {' \
   '        if let Some(v) = self.asked.borrow().get(&u32::MAX) { // knife' \
   a_pr_sweep_does_not_list_is_asked_of_github_once
# I10 (integration)
nx '        if d.head.is_empty() {' \
   '        if d.head.is_empty() && d.layer == 0 { // knife' \
   rows_without_a_head_are_not_merged_into_one
# I11 (integration, Codex M)
nx '            dispatchable: if f.unread_unknown() {' \
   '            dispatchable: if f.seat.is_empty() { // knife' \
   layer_five_takes_the_checked_seat_facts
# I12 (integration, Codex M)
nx '                    s.as_ref().ok().filter(|s| s.complete)' \
   '                    s.as_ref().ok().filter(|s| !s.schema.is_empty()) // knife' \
   an_incomplete_sweep_is_not_read_for_merged
# I13 (integration, Codex M; re-anchored at rank_l4)
nx '                    _ => layer(s, 3),' \
   '                    _ => from_sweep(s), // knife' \
   layer_four_comes_from_the_full_scan_alone
# I14 (integration, Codex M)
nx '        Some(f) => (f, sources.len() - 1),' \
   '        Some(_) => (sweep, 1), // knife' \
   layer_four_comes_from_the_full_scan_alone
# I15 (integration, Codex M; re-anchored at rank_l4)
nx '        let l4_rows = f.as_ref().map(|s| layer(s, 4)).unwrap_or_default();' \
   '        let l4_rows = Vec::new(); // knife' \
   layer_four_comes_from_the_full_scan_alone
# I16 (integration, 1ced7 / IFACE r9)
nx '    let complete = sources.iter().all(Source::ok) && unknown.is_empty();' \
   '    let complete = sources.iter().all(Source::ok) && unknown.is_empty() && skipped.is_empty(); // knife' \
   a_seat_whose_unread_is_not_known_is_never_sent_to
# I17 (integration, 1ced7 / IFACE r9)
nx '        n.sources.iter().all(|s| s.ok() || s.state == "unsupported") && n.unknown.is_empty();' \
   '        n.sources.iter().all(|s| s.ok() || s.state == "unsupported") && n.unknown.is_empty() && n.skipped.is_empty(); // knife' \
   layer_five_takes_the_checked_seat_facts
# I18 (integration, 1ced7 / IFACE r9)
nx '        if d.generated_at != 0 && s.generated_at != 0 && d.generated_at != s.generated_at {' \
   '        if d.generated_at == u64::MAX { // knife' \
   due_and_sweep_of_two_rounds_are_partial
# I19 (integration, 519d0 trial)
nx '    s.seats.retain(|f| roster.contains(&f.seat));' \
   '    s.seats.retain(|f| !f.seat.is_empty()); // knife' \
   only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator
# I20 (integration, 519d0 trial)
nx '                        d.to = coordinator.clone();' \
   '                        d.to = coordinator.clone().filter(|c| c.is_empty()); // knife' \
   only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator
# I21 (integration, 519d0 trial)
nx '                dispatchable: next_local::LOST.into(),' \
   '                dispatchable: "unknown".into(), // knife' \
   only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator
# I22 (integration, 519d0 trial)
nx '    (!codes.is_empty()).then_some(codes)' \
   '    Some(codes) // knife' \
   no_roster_means_no_review_row
# I23 (integration, 519d0 trial)
nx '    } else if !n.skipped.is_empty() {' \
   '    } else if n.skipped.is_empty() { // knife' \
   the_text_counts_what_was_held_back_unless_all
# I24 (integration, 519d0 trial)
nx '                Some(None) if off_roster => {' \
   '                Some(None) if off_roster && seat.is_empty() => { // knife' \
   only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator
# I25 (519d0 T8): one seat's merged trees stop folding into one row
nx '        if g.len() == 1 {' \
   '        if !g.is_empty() { // knife' \
   one_seats_merged_trees_are_one_row
mb() { python3 -I scripts/knife.py crates/aib-drive/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --bin aib-drive "$3"; }
# I26 (519d0 T7): a watch refreshes once and never again
mb '    last.is_none_or(|t| now < t || now - t >= WATCH_REFRESH_SECS)' \
   '    last.is_none_or(|t| now < t || WATCH_REFRESH_SECS == 0) // knife' \
   a_watch_refreshes_at_most_every_twenty_minutes
# I27 (519d0 T7 note 1): due keeps aib-slice's 8 a part again
mb '    a.push(DUE_MAX.to_owned());' \
   "    a.push(DUE_MAX.replace('5', \"8\")); // knife" \
   refresh_asks_one_fast_scan_for_both_files
# I28 (519d0 T7): --clone is taken without --watch
mb '    if clone.is_some() && !watch {' \
   '    if clone.is_none() && !watch { // knife' \
   a_clone_goes_with_watch
# I38 (519d0 T9): an aib-slice exit 2 still replaces the caches
mb '    if !(ran_ok && staged.iter().all(|p| whole(p))) {' \
   '    if !((ran_ok || !ran_ok) && staged.iter().all(|p| whole(p))) { // knife' \
   an_incomplete_refresh_keeps_the_old_caches
# I39 (519d0 T9): a file that says complete false still replaces the cache
mb '            == Some(true)' \
   '            != None // knife' \
   an_incomplete_refresh_keeps_the_old_caches
# I50 (S79 B3-1, 519d0 07:26): refresh --auto scans a cache that is still fresh
mb '    if cache_age.is_some_and(|a| a < WATCH_REFRESH_SECS) {' \
   '    if cache_age.is_some_and(|a| a < WATCH_REFRESH_SECS) && false { // knife' \
   auto_refresh_runs_only_on_a_stale_cache_not_tried_lately
# I51 (S79 B3-1): a scan the network cut short is retried every few minutes
mb '    if !refresh_due(tried, now) {' \
   '    if !refresh_due(tried, now) && false { // knife' \
   auto_refresh_runs_only_on_a_stale_cache_not_tried_lately
# I52 (S79 B3-1): a second refresh runs while the first holds the lock
mb '                let held = at.is_some_and(|t| now >= t && now - t < AUTO_LOCK_STALE_SECS);' \
   '                let held = at.is_some_and(|t| now >= t && now - t < AUTO_LOCK_STALE_SECS) && false; // knife' \
   auto_refresh_lock_is_one_holder_and_a_stale_lock_is_taken_over
# I53 (S79 B3-1): a lock left by a killed run is never taken over
mb '                if held || !first {' \
   '                if held || !first || first { // knife' \
   auto_refresh_lock_is_one_holder_and_a_stale_lock_is_taken_over
# I54 (S79 B3-1): the old holder removes the new holder's lock
mb '    if std::fs::read_to_string(path).ok().as_deref() == Some(mine) {' \
   '    if !mine.is_empty() { // knife' \
   auto_refresh_lock_is_one_holder_and_a_stale_lock_is_taken_over
# I60 (S79 B3-1, 7b81e L3 / 5d33a L3): a manual refresh scans while another holds the lock
mb 'fn under_lock(drive: &std::path::Path, now: u64, scan: impl FnOnce() -> i32) -> i32 {' \
   'fn under_lock(drive: &std::path::Path, now: u64, scan: impl FnOnce() -> i32) -> i32 { if now > 0 { return scan(); } // knife' \
   a_manual_refresh_does_not_scan_while_another_holds_the_lock
# I61 (S79 B3-1, 7b81e N2 / 5d33a L3): a cache time in the future counts as fresh
mb '        .and_then(|t| now.duration_since(t).ok())' \
   '        .map(|t| now.duration_since(t).unwrap_or_default()) // knife' \
   a_cache_time_in_the_future_is_due_not_fresh
# I62 (S79 B3-1, 5d33a L1): a lock made but not yet written is taken for a stale one
mb '.and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())' \
   '.and_then(|_| None::<std::time::Duration>) // knife' \
   auto_refresh_lock_is_one_holder_and_a_stale_lock_is_taken_over
# I63 (S79 C7, 519d0 08:00): a tidy-scan exit 2 replaces the report layer 7 reads
mb '    if !matches!(code, Some(0 | 1)) {' \
   '    if !matches!(code, Some(0 | 1 | 2) | None) { // knife' \
   tidy_scan_is_put_in_place_only_from_a_report
# I64 (S79 C7, 519d0 08:00): stdout that is not a tidy-scan report is written
mb '                == Some(aib_drive::next_local::TIDY_SCHEMA)' \
   '                != Some("knife") // knife' \
   tidy_scan_is_put_in_place_only_from_a_report
# I65 (S79 C7b, 519d0 09:3x on M1): main is taken from the full sweep, not the one this refresh staged
mb '    let b = std::fs::read(drive.join(format!("{STAGE}{}", refresh_files(full)[0]))).ok()?;' \
   '    let b = std::fs::read(drive.join(format!("{STAGE}{}", refresh_files(!full)[0]))).ok()?; // knife' \
   tidy_scan_is_put_in_place_only_from_a_report
# I66 (S79 C7b, 519d0 09:33 IFACE r12): a tidy-scan not ok makes the answer incomplete
nx '        .partition(|s| OPTIONAL_SOURCES.contains(&s.name.as_str()));' \
   '        .partition(|_| false); // knife' \
   a_broken_tidy_scan_leaves_the_answer_complete
# I67 (S79 C7b r3 L1, 519d0 09:5x): drive's own clone is not fetched, so it stays behind origin/main
mb '    let fetch = || git(&["-C", &c, "fetch", "--quiet", "--prune", "origin"], 180);' \
   '    let fetch = || Ok::<String, String>(String::new()); // knife: no fetch' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I69 (S79, 5d33a N1 on C7b r4): the clone goes to origin/main, not the main the sweep saw
mb '    let to = main.unwrap_or("origin/main");' \
   '    let to = main.map(|_| "origin/main").unwrap_or("origin/main"); // knife' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I70 (S79, 5d33a N3 on C7b r4): a clone git cannot read is not remade
mb '    if clone.exists() && !readable() {' \
   '    if clone.exists() && !readable() && false { // knife' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I71 (S79, 519d0 10:22): a path that is not <drive>/clone itself is removed
mb '        || real.file_name() != Some(std::ffi::OsStr::new(TIDY_CLONE))' \
   '        && false // knife' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I72 (S79, 519d0 10:22): a clone that is a link is gone into (fetch + checkout where it points)
mb '        return Err(format!("{c} 是連結,不在裡面跑 git"));' \
   '        let _ = &c; // knife' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I68 (S79, 519d0 11-15): a fix row with no due problem says 「修:」 and nothing
nx '        Some(w) => why_zh(w),' \
   '        Some(w) => why_zh(w)[..0].to_owned(), // knife' \
   a_fix_row_never_has_a_blank_reason
# I73 (S79, 5d33a N1 on tidyn r3): a folder under <drive> not named clone is removed
mb '        || real.file_name() != Some(std::ffi::OsStr::new(TIDY_CLONE))' \
   '        || real.file_name().is_none() // knife' \
   tidy_scan_reads_its_own_clone_at_origin_main
# I29 (IFACE r11): layer 4 is given without --with-l4
nx '                    None if l4 => from_sweep(s),' \
   '                    None if l4 || !l4 => from_sweep(s), // knife' \
   layer_four_is_given_only_with_the_flag
# I30 (IFACE r11): the abandonable list stays without --with-l4
nx '        (Ok(s), true) if l4 => s' \
   '        (Ok(s), true) => s // knife' \
   layer_four_is_given_only_with_the_flag
# I40 (ebb36 211ec156 L2): a stale full scan is a source while layer 4 is held back
nx '    if let Some(f) = full.filter(|_| l4) {' \
   '    if let Some(f) = full { // knife' \
   layer_four_is_given_only_with_the_flag
# I41 (ebb36 211ec156 M1): seats with "wip_nodes": null are not read
nx '    Ok(v.unwrap_or_default())' \
   '    v.ok_or_else(|| serde::de::Error::custom("null")) // knife' \
   a_reviewed_node_not_pushed_is_a_push_row
# I44 (ebb36 519d0 C4 safe version): an open PR, dirty or unpushed merged tree is not collected
nx '        } else if b.class == 5 && b.worktree.is_some() && collect_hold(b).is_none() {' \
   '        } else if b.class == 5 && b.worktree.is_some() { // knife' \
   a_merged_branch_with_an_open_pr_is_not_collected
# I45 (ebb36 519d0 C4 safe version): an open PR holds the tree back
nx '    if let Some(p) = b.pr.as_ref().filter(|p| p.state == "open") {' \
   '    if let Some(p) = b.pr.as_ref().filter(|p| p.state == "knife") { // knife' \
   a_merged_branch_with_an_open_pr_is_not_collected
# I46 (ebb36 519d0 C4 safe version): with layer 4 held back by default it is not said twice
nx '        Ok(s) if l4 => collect_held(s),' \
   '        Ok(s) => collect_held(s), // knife' \
   a_merged_branch_with_an_open_pr_is_not_collected
# I47 (ebb36 519d0 C4 safe version): files not committed hold the tree back
nx '        Some(true) => return Some(format!("{} 有沒 commit 的改動", b.branch)),' \
   '        Some(true) => {} // knife' \
   a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected
# I48 (ebb36 519d0 C4 safe version): a tree not known to be clean is held back
nx '        None => return Some(format!("{} 樹乾不乾淨沒讀到", b.branch)),' \
   '        None => {} // knife' \
   a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected
# I49 (ebb36 519d0 C4 safe version): a local branch on another commit holds the tree back
nx '        .map(|h| format!("{} 本機在 {}、有沒推的 commit", b.branch, short(h)))' \
   '        .filter(|_| false).map(|h| format!("{} 本機在 {}、有沒推的 commit", b.branch, short(h))) // knife' \
   a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected
# I31 (519d0 F8): a seat with open PRs also gets the push row
nx '        let Some(c) = x.capacity.as_ref().filter(|c| c.open_prs == Some(0)) else {' \
   '        let Some(c) = x.capacity.as_ref().filter(|c| c.open_prs != Some(99)) else { // knife' \
   a_reviewed_node_not_pushed_is_a_push_row
# I32 (519d0 F8): no push row for a reviewed node
nx '        for id in &c.wip_nodes {' \
   '        for id in c.wip_nodes.iter().take(0) { // knife' \
   a_reviewed_node_not_pushed_is_a_push_row
# I33 (ebb36 f80a69ea L1): a lost author's row goes to no one when there is no coordinator
nx '        if let (Some(who), None) = (&lost, &coordinator) {' \
   '        if let (Some(who), Some(_)) = (&lost, &coordinator.as_ref().filter(|c| c.is_empty())) { // knife' \
   a_lost_author_with_no_coordinator_is_unknown
# I34 (ebb36 519d0 D1: the queue front reaches unblock_by)
nx '                        queue_unblock: front.as_deref(),' \
   '                        queue_unblock: front.as_deref().filter(|_| false), // knife' \
   the_queue_front_and_the_intents_ledger_reach_layer_five
# I35 (ebb36 519d0 D1: a PR closed unmerged is not the front)
nx '                            .map(|p| (p.number, p.state == "open" && !p.merged, b.seat.as_deref()))' \
   '                            .map(|p| (p.number, !p.merged, b.seat.as_deref())) // knife' \
   the_queue_front_and_the_intents_ledger_reach_layer_five
# I36 (ebb36 519d0 C5: a broken intents line makes the source partial)
nx '                if bad > 0 {' \
   '                if bad > 9 { // knife' \
   the_queue_front_and_the_intents_ledger_reach_layer_five
# I42 (ebb36 C13: exactly four hours is not yet starving)
nx '            starving: r.waited_min.is_some_and(|w| w > STARVE_MIN),' \
   '            starving: r.waited_min.is_some_and(|w| w >= STARVE_MIN), // knife' \
   a_mainline_row_waiting_over_four_hours_is_starving
# I55 (ebb36 519d0 07:33, #773): aib-slice's reviewed is read, not only review.at_head
nx '            .unwrap_or_else(|| b.review.as_ref().is_some_and(|r| r.at_head));' \
   '            .filter(|_| false).unwrap_or_else(|| b.review.as_ref().is_some_and(|r| r.at_head)); // knife' \
   aib_slices_reviewed_is_read_before_the_review_at_head
# I56 (ebb36 519d0 07:33): without reviewed a review at the head still counts
nx '            .unwrap_or_else(|| b.review.as_ref().is_some_and(|r| r.at_head));' \
   '            .unwrap_or(false); // knife' \
   aib_slices_reviewed_is_read_before_the_review_at_head
# ED3 (ebb36 S79-n2c-ed1a): aib-drive scope lists the ok nodes and leaves out what to fill in
nx '    for l in lines.iter().filter(|l| l.state != "ok") {' \
   '    for l in lines.iter().filter(|l| l.state != "missing") { // knife' \
   scope_lists_what_to_fill_in_and_what_changed
# ED4 (ebb36 S79-n2c-ed1a): aib-drive scope takes an argument it does not know
nx '            other => return (format!("unknown argument {other:?}\n{SCOPE_USAGE}\n"), 2),' \
   '            _ => {} // knife' \
   scope_lists_what_to_fill_in_and_what_changed
