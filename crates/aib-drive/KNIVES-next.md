# KNIVES-next(S79 n-1,a9136,IFACE r2)

每把刀拿掉一條規則，指定的測試要紅。測試指令:`cargo test -p aib-drive --locked --test next <測試名>`。

| 名 | 檔 | 舊字串 | 新字串 | 測試名 | 預期 |
|---|---|---|---|---|---|
| N1 | crates/aib-drive/src/next.rs | `"no" => needs = "seat_busy".into(),` | `"no" => needs = "none".into(), // knife` | a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched | 紅:忙的作者也被當成可派 |
| N2 | crates/aib-drive/src/next.rs | `c.sort_by(|a, b| swap(b).cmp(&swap(a)).then(a.seat.cmp(&b.seat)));` | `c.sort_by(|a, b| swap(a).cmp(&swap(b)).then(a.seat.cmp(&b.seat))); // knife` | a_swap_reviewer_first_and_unblocks_then_waited | 紅:交換審查的席反而最後挑 |
| N3 | crates/aib-drive/src/next.rs | `.then(b.unblocks.cmp(&a.unblocks))` | `.then(a.unblocks.cmp(&b.unblocks)) // knife` | a_swap_reviewer_first_and_unblocks_then_waited | 紅:解開越多的反而排後面 |
| N4 | crates/aib-drive/src/next.rs | `if src.ok() {` | `if src.ok() || src.state == "partial" { // knife` | an_incomplete_due_scan_puts_its_rows_in_unknown | 紅:掃描不完整的來源照樣排進 rows |
| N5 | crates/aib-drive/src/next.rs | `&& Some(f.seat.as_str()) != author` | `&& author.is_some() // knife` | the_reviewer_is_a_free_non_author_or_nobody | 紅:作者審自己的 PR |
| N6 | crates/aib-drive/src/next.rs | `let complete = sources.iter().all(Source::ok) && unknown.is_empty();` | `let complete = sources.iter().all(Source::ok); // knife` | a_busy_author_is_marked_and_an_unknown_one_is_not_dispatched | 紅:有不明項還說完整 |
| N7 | crates/aib-drive/src/next.rs | `} else if has(&RECEIPT_LACKS) {` | `} else if has(&RECEIPT_LACKS) && i.lacks.len() == 1 { // knife` | what_a_merge_item_lacks_picks_its_row | 紅:缺收據又在排 CI 的不派跑收據 |
| N8 | crates/aib-drive/src/next.rs | `"yes" if size_rank(&f.size) >= size_rank(ROW_SIZE) => {}` | `"yes" if !f.size.is_empty() => {} // knife` | a_seat_takes_a_row_no_bigger_than_its_gap | 紅:空檔不夠的席也派(IFACE r3) |

舊字串含前導空白;逐字以收據用的 TSV 為準(整合時照 aibd.yml 既有寫法加 step)。
