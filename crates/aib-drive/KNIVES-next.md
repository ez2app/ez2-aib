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
| I5 | crates/aib-drive/src/next.rs | `n.sources.iter().all(\|s\| s.ok() \|\| s.state == "unsupported") && n.unknown.is_empty();` | `n.sources.iter().all(Source::ok) && n.unknown.is_empty(); // knife` | the_plan_adds_layer_five_after_the_slice_layers | 第 7 層「不支援」讓整份永遠不完整 |
| I6 | crates/aib-drive/src/next.rs | `if name == "seats" {` | `if name == "seats-knife" {` | the_plan_adds_layer_five_after_the_slice_layers | seats 來源列兩次 |
| I7 | crates/aib-drive/src/next.rs | `n.complete = false;` | `n.complete = n.sources.len() > 99 \|\| n.complete; // knife` | the_plan_adds_layer_five_after_the_slice_layers | plan 讀不到卻 rc 0 |
| I8 | crates/aib-drive/src/next.rs | `listed.or_else(|| self.more.and_then(|m| m.merged(dep)))` | `listed.or_else(|| self.more.and_then(|m| m.merged(&Dep::SkillsPr(0)))) // knife` | a_pr_sweep_does_not_list_is_asked_of_github_once | sweep 沒列的 PR 沒問 GitHub(--fast 快取下第 5 層全進 unknown) |
| I9 | crates/aib-drive/src/next.rs | `if let Some(v) = self.asked.borrow().get(n) {` | `if let Some(v) = self.asked.borrow().get(&u32::MAX) { // knife` | a_pr_sweep_does_not_list_is_asked_of_github_once | 同一張 PR 一輪問兩次 GitHub |
| I10 | crates/aib-drive/src/next.rs | `if d.head.is_empty() {` | `if d.head.is_empty() && d.layer == 0 { // knife` | rows_without_a_head_are_not_merged_into_one | 沒頭碼的兩列被當成同一列(b433b inc N2) |
| I11 | crates/aib-drive/src/next.rs | `dispatchable: if f.unread_unknown() {` | `dispatchable: if f.seat.is_empty() { // knife` | layer_five_takes_the_checked_seat_facts | 第 5–6 層把未讀不明的席當可派(Codex M1,IFACE r6) |
| I12 | crates/aib-drive/src/next.rs | `s.as_ref().ok().filter(\|s\| s.complete)` | `s.as_ref().ok().filter(\|s\| !s.schema.is_empty()) // knife` | an_incomplete_sweep_is_not_read_for_merged | 不完整的 sweep 被拿來判前置 PR 已合(Codex M3) |
| I13 | crates/aib-drive/src/next.rs | `Some(_) => layer(s, 3),` | `Some(_) => from_sweep(s), // knife` | layer_four_comes_from_the_full_scan_alone | 有完整掃描時,fast sweep 仍出第 4 層(IFACE r8) |
| I14 | crates/aib-drive/src/next.rs | `Some(f) => (f, sources.len() - 1),` | `Some(_) => (sweep, 1), // knife` | layer_four_comes_from_the_full_scan_alone | 可放棄清單取自 fast sweep,不是完整掃描(IFACE r8) |
| I15 | crates/aib-drive/src/next.rs | `let l4 = f.as_ref().map(\|s\| layer(s, 4)).unwrap_or_default();` | `let l4 = Vec::new(); // knife` | layer_four_comes_from_the_full_scan_alone | 完整掃描裡的已合工作樹不出收樹列(IFACE r8) |
| I16 | crates/aib-drive/src/next.rs | `let complete = sources.iter().all(Source::ok) && unknown.is_empty();` | `let complete = sources.iter().all(Source::ok) && unknown.is_empty() && skipped.is_empty(); // knife` | a_seat_whose_unread_is_not_known_is_never_sent_to | 照規則不排的列讓整份不完整、rc 3(IFACE r9) |
| I17 | crates/aib-drive/src/next.rs | `n.sources.iter().all(\|s\| s.ok() \|\| s.state == "unsupported") && n.unknown.is_empty();` | `n.sources.iter().all(\|s\| s.ok() \|\| s.state == "unsupported") && n.unknown.is_empty() && n.skipped.is_empty(); // knife` | layer_five_takes_the_checked_seat_facts | 第 5–6 層照規則不排的節點讓整份 rc 3(1ced7 L4,IFACE r9) |
| I18 | crates/aib-drive/src/next.rs | `if d.generated_at != 0 && s.generated_at != 0 && d.generated_at != s.generated_at {` | `if d.generated_at == u64::MAX { // knife` | due_and_sweep_of_two_rounds_are_partial | due / sweep 不同輪沒標 partial(1ced7 L5) |
| I19 | crates/aib-drive/src/next.rs | `s.seats.retain(\|f\| roster.contains(&f.seat));` | `s.seats.retain(\|f\| !f.seat.is_empty()); // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊外的席被派(519d0 T1) |
| I20 | crates/aib-drive/src/next.rs | `d.to = coordinator.clone();` | `d.to = coordinator.clone().filter(\|c\| c.is_empty()); // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 失聯作者的列沒交給統籌席(519d0 T2) |
| I21 | crates/aib-drive/src/next.rs | `dispatchable: next_local::LOST.into(),` | `dispatchable: "unknown".into(), // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊有、事實沒有的席當成讀不到(519d0 T2) |
| I22 | crates/aib-drive/src/next.rs | `(!codes.is_empty()).then_some(codes)` | `Some(codes) // knife` | no_roster_means_no_review_row | 沒有名冊照樣派審查(519d0 T1) |
| I23 | crates/aib-drive/src/next.rs | `} else if !n.skipped.is_empty() {` | `} else if n.skipped.is_empty() { // knife` | the_text_counts_what_was_held_back_unless_all | 文字版不印不排的條數(519d0 T3) |
| I24 | crates/aib-drive/src/next.rs | `Some(None) if off_roster => {` | `Some(None) if off_roster && seat.is_empty() => { // knife` | only_roster_seats_are_sent_and_a_lost_author_goes_to_the_coordinator | 名冊外作者的列進 unknown、整份永遠不完整(519d0 T2 延伸) |
