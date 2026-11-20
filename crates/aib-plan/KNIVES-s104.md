---
status: 定稿
rev: r0
node: S104
owner: b433b
---
# S104 knives (aib-plan change badges)

`cargo test -p aib-plan <filter>`. BD1–BD6 in `crates/aib-plan/src/badges.rs`, BD7–BD8 in `crates/aib-plan/src/main.rs`. Every anchor occurs once in its file; these are the exact strings the receipt ran. Runs from `crates/aib-plan/knives/knives-08-badges.sh` (11-16 ruling: knives live under crates/<crate>/knives/, IFACE-1117 §5; 7b81e redo on main 11-19).

| knife | what goes wrong | old | new | filter |
|---|---|---|---|---|
| BD1 | turning into 實查不通過 is not 退回 | `if new == Some(FAIL) {` | `if new == Some("never") {` | `--test plan_badges going_back_follows_the_progress_order_and_fail_and_pass` |
| BD2 | the lowest-priority badge wins | `got.into_iter().min()` | `got.into_iter().max()` | `--test plan_badges one_badge_per_node_by_priority` |
| BD3 | a collapsed group only adds up its direct children | `stack.extend(ix.kids[i].iter().copied());` | `stack.extend(ix.kids[i].iter().copied().filter(\|_\| false));` | `--test plan_badges a_collapsed_group_shows_the_badges_below_it_added_up` |
| BD4 | exactly 4 hours already counts as 卡住 | `.is_some_and(\|t\| now - t > STUCK_S)` | `.is_some_and(\|t\| now - t >= STUCK_S)` | `--test plan_badges updated_reads_month_day_and_ten_minute_marks_in_taipei_time` |
| BD5 | with no snapshot every node is [新] | `None if have_snapshot => {` | `None if have_snapshot \|\| true => {` | `--test plan_badges without_a_snapshot_only_the_state_badges_show_and_the_summary_asks_for_v` |
| BD6 | branches the cockpit grafts on are compared too | `.filter(\|(_, n)\| ids.contains(&n.id))` | `.filter(\|(_, n)\| ids.contains(&n.id) \|\| true)` | `--test plan_badges grafted_nodes_are_not_compared` |
| BD7 | v writes the file but the screen keeps the old snapshot | `self.seen = Some(snap);` | `self.seen = self.seen.take().or(Some(snap));` | `--bin aib-plan badges_show_on_the_tree_and_v_clears_them` |
| BD8 | badges are drawn with no snapshot path (tests, --check) | `if self.seen_path.is_none() {` | `if self.seen_path.is_none() && false {` | `--bin aib-plan badges_show_on_the_tree_and_v_clears_them` |
