#!/usr/bin/env bash
# aib-plan change badges knives BD1–BD8 (S104; b433b d50c0606, redone on main by 7b81e 11-19 per f1fae
# TRIAGE-pr815): each rule broken on purpose must turn its own test red. Run by the "aib-plan knives (macOS)"
# step in aibd.yml (one line runs crates/aib-plan/knives/*.sh). Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
bk() { python3 -I scripts/knife.py crates/aib-plan/src/badges.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --test plan_badges "$3"; }
mk() { python3 -I scripts/knife.py crates/aib-plan/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --bin aib-plan "$3"; }
# BD1 turning into 實查不通過 is not 退回
bk '    if new == Some(FAIL) {' '    if new == Some("never") { // knife' \
   going_back_follows_the_progress_order_and_fail_and_pass
# BD2 the lowest-priority badge wins
bk '    got.into_iter().min()' '    got.into_iter().max() // knife' \
   one_badge_per_node_by_priority
# BD3 a collapsed group only adds up its direct children
bk '        stack.extend(ix.kids[i].iter().copied());' \
   '        stack.extend(ix.kids[i].iter().copied().filter(|_| false)); // knife' \
   a_collapsed_group_shows_the_badges_below_it_added_up
# BD4 exactly 4 hours already counts as 卡住
bk '.is_some_and(|t| now - t > STUCK_S)' '.is_some_and(|t| now - t >= STUCK_S)' \
   updated_reads_month_day_and_ten_minute_marks_in_taipei_time
# BD5 with no snapshot every node is [新]
bk '        None if have_snapshot => {' '        None if have_snapshot || true => { // knife' \
   without_a_snapshot_only_the_state_badges_show_and_the_summary_asks_for_v
# BD6 branches the cockpit grafts on are compared too
bk '.filter(|(_, n)| ids.contains(&n.id))' '.filter(|(_, n)| ids.contains(&n.id) || true)' \
   grafted_nodes_are_not_compared
# BD7 v writes the file but the screen keeps the old snapshot
mk '        self.seen = Some(snap);' '        self.seen = self.seen.take().or(Some(snap)); // knife' \
   badges_show_on_the_tree_and_v_clears_them
# BD8 badges are drawn with no snapshot path (tests, --check)
mk '        if self.seen_path.is_none() {' '        if self.seen_path.is_none() && false { // knife' \
   badges_show_on_the_tree_and_v_clears_them
