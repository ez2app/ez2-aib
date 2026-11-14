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

舊字串含前導空白;逐字以收據用的 TSV 為準(整合時照 aibd.yml 既有寫法加 step)。
