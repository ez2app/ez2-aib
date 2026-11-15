# KNIVES-next(S79 n-1,a9136,IFACE r2)

每把刀拿掉一條規則，指定的測試要紅。測試指令:`cargo test -p aib-drive --locked --test next <測試名>`。

| 名 | 檔 | 舊字串 | 新字串 | 測試名 | 預期 |
|---|---|---|---|---|---|
| N1 | crates/aib-drive/src/next.rs | `"no" => needs = "seat_busy".into(),` | `"no" => needs = "none".into(), // knife` | a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched | 紅:忙的作者也被當成可派 |
| N2 | crates/aib-drive/src/next.rs | `.then(swap(b).cmp(&swap(a)))` | `.then(swap(a).cmp(&swap(b))) // knife` | a_swap_reviewer_first_and_unblocks_then_waited | 紅:交換審查的席沒被優先挑 |
| N3 | crates/aib-drive/src/next.rs | `.then(b.unblocks.cmp(&a.unblocks))` | `.then(a.unblocks.cmp(&b.unblocks)) // knife` | a_swap_reviewer_first_and_unblocks_then_waited | 紅:解開越多的反而排後面 |
| N4 | crates/aib-drive/src/next.rs | `if src.ok() {` | `if src.ok() || src.state == "partial" { // knife` | an_incomplete_due_scan_puts_its_rows_in_unknown | 紅:掃描不完整的來源照樣排進 rows |
| N5 | crates/aib-drive/src/next.rs | `&& Some(f.seat.as_str()) != author` | `&& author.is_some() // knife` | the_reviewer_is_a_free_non_author_or_nobody | 紅:作者審自己的 PR |
| N6 | crates/aib-drive/src/next.rs | `let complete = sources.iter().all(Source::ok) && unknown.is_empty();` | `let complete = sources.iter().all(Source::ok); // knife` | a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched | 紅:有不明項還說完整 |
| N7 | crates/aib-drive/src/next.rs | `} else if has(&RECEIPT_LACKS) {` | `} else if has(&RECEIPT_LACKS) && i.lacks.len() == 1 { // knife` | what_a_merge_item_lacks_picks_its_row | 紅:缺收據又在排 CI 的不派跑收據 |
| N8 | crates/aib-drive/src/next.rs | `"yes" if size_rank(&f.size) >= size_rank(ROW_SIZE) => {}` | `"yes" if !f.size.is_empty() => {} // knife` | a_seat_takes_a_row_no_bigger_than_its_gap | 紅:空檔不夠的席也派(IFACE r3) |
| N9 | crates/aib-drive/src/next.rs | `starving: d.layer < 4 && d.waited_min > STARVE_MIN,` | `starving: d.layer < 4 && d.waited_min > STARVE_MIN * 10, // knife` | a_row_waiting_over_four_hours_is_marked_starving | 紅:等超過 4 小時沒標(C13) |
| N10 | crates/aib-drive/src/next.rs | `.filter(\|b\| b.abandonable)` | `.filter(\|b\| b.abandonable && b.age_h == u64::MAX) // knife` | abandonable_is_never_a_row_and_merged_is_collected | 紅:可放棄清單空了(C4) |
| N11 | crates/aib-drive/src/next.rs | `f.dispatchable == "yes"` | `f.dispatchable != "no" // knife` | a_partial_seat_list_is_used_seat_by_seat | 紅:席位事實不完整時派給了狀態不明的席(b433b M1) |
| N12 | crates/aib-drive/src/next.rs | `*given.entry(r.seat.clone()).or_default() += 1;` | `given.entry(r.seat.clone()).or_default(); // knife` | reviews_are_spread_over_the_free_seats | 紅:審查列全派同一席(b433b L1) |
| N13 | crates/aib-drive/src/next.rs | `starving: d.layer < 4 && d.waited_min > STARVE_MIN,` | `starving: d.waited_min > STARVE_MIN, // knife` | abandonable_is_never_a_row_and_merged_is_collected | 紅:收樹列被標等太久(b433b L2) |
| N14 | crates/aib-drive/src/next.rs | `needs = "no_owner".into();` | `needs = "none".into(); // knife` | abandonable_is_never_a_row_and_merged_is_collected | 紅:沒有席碼的列沒標 no_owner |
| N15 | crates/aib-drive/src/next.rs | `if d.kind != "review" \|\| reviewed.contains(&d.head) {` | `if d.kind != "review" { // knife` | a_branch_not_pushed_is_reviewed_once_per_head | 紅:同一個頭出兩列審查 |
| N16 | crates/aib-drive/src/next.rs | `} else if has(&["no_review"])` | `} else if has(&["no_review_knife"])` | a_branch_not_pushed_is_reviewed_once_per_head | 紅:沒推的分支缺審查不出列(§18) |

舊字串含前導空白;逐字以收據用的 TSV 為準(整合時照 aibd.yml 既有寫法加 step)。

## 整合加的(a9136,S79 整合分支:next 接 next_local、IFACE r5 stale、r6 unread、r8 完整掃描;I11–I15 = Codex 整合審 M1/M3/M4;I16–I18 = 1ced7 整合審 L4/L5、IFACE r9;I19–I23 = 519d0 試跑 T1–T3)

| 刀 | 檔 | 舊 | 新 | 測試 | 紅了代表 |
|---|---|---|---|---|---|
| I1 | crates/aib-drive/src/next.rs | `Err(e) if e.starts_with("stale:") => ("stale", Some(e.clone())),` | `Err(e) if e.starts_with("stale-knife:") => ("stale", Some(e.clone())), // knife` | a_stale_cache_is_a_stale_source | 一個被說過期的快取當成讀不到(failed),不是 stale(IFACE r5) |
| I2 | crates/aib-drive/src/next.rs | `&& !f.unread_unknown()` | `&& f.role != "knife"` | a_seat_whose_unread_is_not_known_is_never_sent_to | 未讀不明的席被挑成審查席(IFACE r6) |
| I3 | crates/aib-drive/src/next.rs | `Some(Some(f)) if f.unread_unknown() => {` | `Some(Some(f)) if f.unread_unknown() && f.seat.is_empty() => { // knife` | a_seat_whose_unread_is_not_known_is_never_sent_to | 未讀不明的作者照派(IFACE r6) |
| I4 | crates/aib-drive/src/next.rs | `.map(\|p\| p.merged)` | `.map(\|_\| false) // knife` | the_plan_adds_layer_five_after_the_slice_layers | 前置 PR 已合卻讀成沒合,第 5 層不出列 |
| I5 | crates/aib-drive/src/next.rs | `n.sources.extend(optional);` | `drop(optional); // knife` | the_plan_adds_layer_five_after_the_slice_layers | tidy_scan 為了算 complete 先分出去、沒放回輸出(C7b 改錨點:舊錨點那行仍在,但「不支援讓整份不完整」已由 OPTIONAL_SOURCES 與 I66 管) |
| I6 | crates/aib-drive/src/next.rs | `if name == "seats" {` | `if name == "seats-knife" {` | the_plan_adds_layer_five_after_the_slice_layers | seats 來源列兩次 |
| I7 | crates/aib-drive/src/next.rs | `why: "plan 讀不到".into(),` | `why: "名冊讀不到".into(), // knife` | the_plan_adds_layer_five_after_the_slice_layers | plan 讀不到卻說成名冊讀不到(收據 ad83e932 改錨:T1 起 plan 讀不到時名冊也失敗、complete 已是 false,原刀 `n.complete = false;` 咬不到) |
| I8 | crates/aib-drive/src/next.rs | `listed.or_else(|| self.more.and_then(|m| m.merged(dep)))` | `listed.or_else(|| self.more.and_then(|m| m.merged(&Dep::SkillsPr(0)))) // knife` | a_pr_sweep_does_not_list_is_asked_of_github_once | sweep 沒列的 PR 沒問 GitHub(--fast 快取下第 5 層全進 unknown) |
| I9 | crates/aib-drive/src/next.rs | `if let Some(v) = self.asked.borrow().get(n) {` | `if let Some(v) = self.asked.borrow().get(&u32::MAX) { // knife` | a_pr_sweep_does_not_list_is_asked_of_github_once | 同一張 PR 一輪問兩次 GitHub |
| I10 | crates/aib-drive/src/next.rs | `if d.head.is_empty() {` | `if d.head.is_empty() && d.layer == 0 { // knife` | rows_without_a_head_are_not_merged_into_one | 沒頭碼的兩列被當成同一列(b433b inc N2) |
| I11 | crates/aib-drive/src/next.rs | `dispatchable: if f.unread_unknown() {` | `dispatchable: if f.seat.is_empty() { // knife` | layer_five_takes_the_checked_seat_facts | 第 5–6 層把未讀不明的席當可派(Codex M1,IFACE r6) |
| I12 | crates/aib-drive/src/next.rs | `s.as_ref().ok().filter(\|s\| s.complete)` | `s.as_ref().ok().filter(\|s\| !s.schema.is_empty()) // knife` | an_incomplete_sweep_is_not_read_for_merged | 不完整的 sweep 被拿來判前置 PR 已合(Codex M3) |
| I13 | crates/aib-drive/src/next.rs | `_ => layer(s, 3),` | `_ => from_sweep(s), // knife` | layer_four_comes_from_the_full_scan_alone | 有完整掃描時,fast sweep 仍出第 4 層(IFACE r8) |
| I14 | crates/aib-drive/src/next.rs | `Some(f) => (f, sources.len() - 1),` | `Some(_) => (sweep, 1), // knife` | layer_four_comes_from_the_full_scan_alone | 可放棄清單取自 fast sweep,不是完整掃描(IFACE r8) |
| I15 | crates/aib-drive/src/next.rs | `let l4_rows = f.as_ref().map(\|s\| layer(s, 4)).unwrap_or_default();` | `let l4_rows = Vec::new(); // knife` | layer_four_comes_from_the_full_scan_alone | 完整掃描裡的已合工作樹不出收樹列(IFACE r8) |
| I16 | crates/aib-drive/src/next.rs | `let complete = sources.iter().all(Source::ok) && unknown.is_empty();` | `let complete = sources.iter().all(Source::ok) && unknown.is_empty() && skipped.is_empty(); // knife` | a_seat_whose_unread_is_not_known_is_never_sent_to | 照規則不排的列讓整份不完整、rc 3(IFACE r9) |
| I17 | crates/aib-drive/src/next.rs | `n.sources.iter().all(\|s\| s.ok() \|\| s.state == "unsupported") && n.unknown.is_empty();` | `n.sources.iter().all(\|s\| s.ok() \|\| s.state == "unsupported") && n.unknown.is_empty() && n.skipped.is_empty(); // knife` | layer_five_takes_the_checked_seat_facts | 第 5–6 層照規則不排的節點讓整份 rc 3(1ced7 L4,IFACE r9) |
| I18 | crates/aib-drive/src/next.rs | `if d.generated_at != 0 && s.generated_at != 0 && d.generated_at != s.generated_at {` | `if d.generated_at == u64::MAX { // knife` | due_and_sweep_of_two_rounds_are_partial | due / sweep 不同輪沒標 partial(1ced7 L5) |
| I19 | crates/aib-drive/src/next.rs | `s.seats.retain(\|f\| roster.contains(&f.seat));` | `s.seats.retain(\|f\| !f.seat.is_empty()); // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊外的席被派(519d0 T1) |
| I20 | crates/aib-drive/src/next.rs | `d.to = coordinator.clone();` | `d.to = coordinator.clone().filter(\|c\| c.is_empty()); // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 失聯作者的列沒交給統籌席(519d0 T2) |
| I21 | crates/aib-drive/src/next.rs | `dispatchable: next_local::LOST.into(),` | `dispatchable: "unknown".into(), // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊有、事實沒有的席當成讀不到(519d0 T2) |
| I22 | crates/aib-drive/src/next.rs | `(!codes.is_empty()).then_some(codes)` | `Some(codes) // knife` | no_roster_means_no_review_row | 沒有名冊照樣派審查(519d0 T1) |
| I23 | crates/aib-drive/src/next.rs | `} else if !n.skipped.is_empty() {` | `} else if n.skipped.is_empty() { // knife` | the_text_counts_what_was_held_back_unless_all | 文字版不印不排的條數(519d0 T3) |
| I24 | crates/aib-drive/src/next.rs | `Some(None) if off_roster => {` | `Some(None) if off_roster && seat.is_empty() => { // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊外作者的列進 unknown、整份永遠不完整(519d0 T2 延伸) |
| I25 | crates/aib-drive/src/next.rs | `if g.len() == 1 {` | `if !g.is_empty() { // knife` | one_seats_merged_trees_are_one_row | 同席已合工作樹不併列、第 4 層又一次 28 列(519d0 T8) |
| I26 | crates/aib-drive/src/main.rs | `last.is_none_or(\|t\| now < t \|\| now - t >= WATCH_REFRESH_SECS)` | `last.is_none_or(\|t\| now < t \|\| WATCH_REFRESH_SECS == 0) // knife` | a_watch_refreshes_at_most_every_twenty_minutes | watch 只在第一輪 refresh、之後快取又變舊(519d0 T7) |
| I27 | crates/aib-drive/src/main.rs | `a.push(DUE_MAX.to_owned());` | `a.push(DUE_MAX.replace('5', "8")); // knife` | refresh_asks_one_fast_scan_for_both_files | due 每段又只留 8 筆、#748 看不到(519d0 T7 通知 1) |
| I28 | crates/aib-drive/src/main.rs | `if clone.is_some() && !watch {` | `if clone.is_none() && !watch { // knife` | a_clone_goes_with_watch | --once 收了 --clone 卻不 refresh,呼叫的人以為有刷新 |
| I29 | crates/aib-drive/src/next.rs | `None if l4 => from_sweep(s),` | `None if l4 \|\| !l4 => from_sweep(s), // knife` | layer_four_is_given_only_with_the_flag | 沒加 --with-l4 也出第 4 層、叫人收還要合的樹(IFACE r11) |
| I30 | crates/aib-drive/src/next.rs | `(Ok(s), true) if l4 => s` | `(Ok(s), true) => s // knife` | layer_four_is_given_only_with_the_flag | 可放棄清單在沒加 --with-l4 時照樣列(IFACE r11) |
| I31 | crates/aib-drive/src/next.rs | `let Some(c) = x.capacity.as_ref().filter(\|c\| c.open_prs == Some(0)) else {` | `… c.open_prs != Some(99)) else { // knife` | a_reviewed_node_not_pushed_is_a_push_row | 名下有開著 PR 或 PR 數不明的席也被叫推(519d0 F8) |
| I32 | crates/aib-drive/src/next.rs | `for id in &c.wip_nodes {` | `for id in c.wip_nodes.iter().take(0) { // knife` | a_reviewed_node_not_pushed_is_a_push_row | 進度樹審過未推的節點不出第 1 層推送列(519d0 F8) |
| I33 | crates/aib-drive/src/next.rs | `if let (Some(who), None) = (&lost, &coordinator) {` | `if let (Some(who), Some(_)) = (&lost, &coordinator.as_ref().filter(\|c\| c.is_empty())) { // knife` | a_lost_author_with_no_coordinator_is_unknown | 統籌席讀不到時失聯作者的列派給沒有人(ebb36 f80a69ea L1) |
| I34 | crates/aib-drive/src/next.rs | `queue_unblock: front.as_deref(),` | `queue_unblock: front.as_deref().filter(\|_\| false), // knife` | the_queue_front_and_the_intents_ledger_reach_layer_five | 隊首沒接到 unblock_by(519d0 D1) |
| I35 | crates/aib-drive/src/next.rs | `.map(\|p\| (p.number, p.state == "open" && !p.merged, b.seat.as_deref()))` | `.map(\|p\| (p.number, !p.merged, b.seat.as_deref())) // knife` | the_queue_front_and_the_intents_ledger_reach_layer_five | 已關沒合的 PR 被當隊首(519d0 D1) |
| I36 | crates/aib-drive/src/next.rs | `if bad > 0 {` | `if bad > 9 { // knife` | the_queue_front_and_the_intents_ledger_reach_layer_five | 意圖帳有壞行仍算完整(C5) |
| I44 | crates/aib-drive/src/next.rs | `} else if b.class == 5 && b.worktree.is_some() && collect_hold(b).is_none() {` | `} else if b.class == 5 && b.worktree.is_some() { // knife` | a_merged_branch_with_an_open_pr_is_not_collected | aib-slice 判已合但 PR 還開著 / 樹不乾淨也出收工作樹列,收了會丟工作(C4 安全版) |
| I45 | crates/aib-drive/src/next.rs | `if let Some(p) = b.pr.as_ref().filter(\|p\| p.state == "open") {` | `if let Some(p) = b.pr.as_ref().filter(\|p\| p.state == "knife") { // knife` | a_merged_branch_with_an_open_pr_is_not_collected | PR 還開著的已合分支照收(C4 安全版) |
| I46 | crates/aib-drive/src/next.rs | `Ok(s) if l4 => collect_held(s),` | `Ok(s) => collect_held(s), // knife` | a_merged_branch_with_an_open_pr_is_not_collected | 第 4 層預設不出時又多說一次不收的樹(C4 安全版) |
| I47 | crates/aib-drive/src/next.rs | `Some(true) => return Some(format!("{} 有沒 commit 的改動", b.branch)),` | `Some(true) => {} // knife` | a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected | 樹裡有沒 commit 的改動照收(519d0 06:5x) |
| I48 | crates/aib-drive/src/next.rs | `None => return Some(format!("{} 樹乾不乾淨沒讀到", b.branch)),` | `None => {} // knife` | a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected | 樹乾不乾淨沒讀到也照收(519d0 06:5x) |
| I49 | crates/aib-drive/src/next.rs | `.map(\|h\| format!("{} 本機在 {}、有沒推的 commit", b.branch, short(h)))` | `.filter(\|_\| false).map(\|h\| format!("{} 本機在 {}、有沒推的 commit", b.branch, short(h))) // knife` | a_merged_tree_with_work_not_committed_or_not_pushed_is_not_collected | 本機分支有沒推的 commit 照收(519d0 06:5x) |
| I38 | crates/aib-drive/src/main.rs | `if !(ran_ok && staged.iter().all(\|p\| whole(p))) {` | `if !((ran_ok \|\| !ran_ok) && …) { // knife` | an_incomplete_refresh_keeps_the_old_caches | aib-slice rc 2 照樣換掉好快取(519d0 T9) |
| I39 | crates/aib-drive/src/main.rs | `== Some(true)` | `!= None // knife` | an_incomplete_refresh_keeps_the_old_caches | 檔案自己寫 complete false 仍換上去(519d0 T9) |
| I40 | crates/aib-drive/src/next.rs | `if let Some(f) = full.filter(\|_\| l4) {` | `if let Some(f) = full { // knife` | layer_four_is_given_only_with_the_flag | 第 4 層不出時，舊的全掃檔仍讓答案不完整(ebb36 211ec156 L2) |
| I41 | crates/aib-drive/src/next.rs | `Ok(v.unwrap_or_default())` | `v.ok_or_else(\|\| serde::de::Error::custom("null")) // knife` | a_reviewed_node_not_pushed_is_a_push_row | seats 的 wip_nodes 是 null 時整份席位事實讀不進來(ebb36 211ec156 M1) |
| I42 | crates/aib-drive/src/next.rs | `starving: r.waited_min.is_some_and(\|w\| w > STARVE_MIN),` | `… w >= STARVE_MIN), // knife` | a_mainline_row_waiting_over_four_hours_is_starving | 第 5–6 層剛好 4 小時就算等太久(C13) |
| I55 | crates/aib-drive/src/next.rs | `.unwrap_or_else(\|\| b.review.as_ref().is_some_and(\|r\| r.at_head));` | `.filter(\|_\| false).unwrap_or_else(…); // knife` | aib_slices_reviewed_is_read_before_the_review_at_head | 只看 at_head、不讀 aib-slice 的 reviewed:接 main carried 的 #773 又被列「頭上沒有非作者審查」(519d0 07:33) |
| I56 | crates/aib-drive/src/next.rs | `.unwrap_or_else(\|\| b.review.as_ref().is_some_and(\|r\| r.at_head));` | `.unwrap_or(false); // knife` | aib_slices_reviewed_is_read_before_the_review_at_head | 舊 aib-slice 沒寫 reviewed 時，頭上有審查也被當沒審 |
| I63 | crates/aib-drive/src/main.rs | `if !matches!(code, Some(0 \| 1)) {` | `if !matches!(code, Some(0 \| 1 \| 2) \| None) { // knife` | tidy_scan_is_put_in_place_only_from_a_report | tidy-scan exit 2(什麼都沒掃)也蓋掉舊的 tidy-scan.json(519d0 08:00) |
| I64 | crates/aib-drive/src/main.rs | `== Some(aib_drive::next_local::TIDY_SCHEMA)` | `!= Some("knife") // knife` | tidy_scan_is_put_in_place_only_from_a_report | 印出來的不是 tidy-scan 報告(別的 schema)也寫進檔 |
| NL57 | crates/aib-drive/src/next_local.rs | `Some(t) if tidy_stale(inp, t).is_some() => {` | `… && false => { // knife` | layer_seven_skips_a_stale_report_and_gives_one_row_a_node | 舊的 tidy-scan.json(沒產生時間、超過 60 分、head 不是同一輪 refresh 寫的 main)照樣派(519d0 09:17、09:3x) |
| NL58 | crates/aib-drive/src/next_local.rs | `if taken.contains(node) {` | `if taken.contains(node) && false { // knife` | layer_seven_skips_a_stale_report_and_gives_one_row_a_node | 同一節點的重複項目同一輪全部派(5d33a N2) |
| NL59 | crates/aib-drive/src/next_local.rs | `let mut taken: BTreeSet<String> = rows.iter()…collect();` | `let mut taken: BTreeSet<String> = BTreeSet::new(); // knife` | layer_seven_skips_a_stale_report_and_gives_one_row_a_node | 第 5 層已出列的節點，第 7 層又出一列(5d33a N3) |
| NL60 | crates/aib-drive/src/next_local.rs | `_ if head != main => Some(format!(` | `… && false => Some(format!( // knife` | layer_seven_skips_a_stale_report_and_gives_one_row_a_node | head 不是同檔 main 的報告當成新的(519d0 09:3x,C7b M1) |
| NL61 | crates/aib-drive/src/next_local.rs | `tidy_skip(tidy_stale(inp, t).unwrap_or_default(), &mut skipped);` | `unknown.push(Unknown { … }); // knife` | layer_seven_skips_a_stale_report_and_gives_one_row_a_node | 舊的 tidy-scan 寫進 unknown(選用來源讓 next 不完整、act 停送;519d0 09:33 IFACE r12) |
| I65 | crates/aib-drive/src/main.rs | `let b = std::fs::read(drive.join(format!("{STAGE}{}", refresh_files(full)[0]))).ok()?;` | `… refresh_files(!full)[0] … // knife` | tidy_scan_is_put_in_place_only_from_a_report | main 取 full sweep(常比 refresh 舊)而不是這輪暫存的 sweep(519d0 09:3x,C7b M1) |
| I66 | crates/aib-drive/src/next.rs | `.partition(\|s\| OPTIONAL_SOURCES.contains(&s.name.as_str()));` | `.partition(\|_\| false); // knife` | a_broken_tidy_scan_leaves_the_answer_complete | tidy-scan 不是 ok 就讓 complete=false(519d0 09:33 IFACE r12) |
| I67 | crates/aib-drive/src/main.rs | `git(&["-C", &c, "fetch", "--quiet", "--prune", "origin"], 180)?;` | `// knife: no fetch` | tidy_scan_reads_its_own_clone_at_origin_main | drive 專用 clone 沒 fetch、停在舊的 origin/main(519d0 09:5x,C7b r3 L1) |
| NL62 | crates/aib-drive/src/next_local.rs | `.map(cannot_take)` | `.filter(\|_\| false).map(cannot_take) // knife` | a_review_sized_seat_gets_no_mainline | 第 5 層 owner 都接不了(size 不夠 / 不是 dev / 不能派)時那列無聲消失(87167 重跑抓到,519d0 11-15) |
| NL63 | crates/aib-drive/src/next_local.rs | `reason: cannot_take(f),` | `reason: String::new(), // knife` | rows_are_slice_and_go_only_to_a_seat_that_fits | 第 6 層建議的席接不了時沒寫原因 |
| NL64 | crates/aib-drive/src/next_local.rs | `if f.role == "dev" && f.dispatchable == "yes" && !fits(&f.size, ROW_SIZE) {` | `if false { // knife` | a_review_sized_seat_gets_no_mainline | size 不夠沒寫成 size_too_small(列要的 size、席的 size) |
