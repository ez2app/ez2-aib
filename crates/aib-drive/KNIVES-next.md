# KNIVES-next(S79 n-1,a9136)

每把刀拿掉一條規則，指定的測試要紅。測試指令:`cargo test -p aib-drive --locked --test next <測試名>`。

| 名 | 檔 | 舊字串 | 新字串 | 測試名 | 預期 |
|---|---|---|---|---|---|
| N1 | crates/aib-drive/src/next.rs | `"no" => needs = "seat_busy".into(),` | `"no" => needs = "none".into(), // knife` | a_busy_seat_s_review_is_marked_and_goes_after_a_free_one | 紅:一個忙的席也被當成可以派 |
| N2 | crates/aib-drive/src/next.rs | `.then(sb.cmp(sa))` | `.then(sa.cmp(sb)) // knife` | a_review_swap_goes_first_among_equals | 紅:交換審查排到最後 |
| N3 | crates/aib-drive/src/next.rs | `.then(b.unblocks.cmp(&a.unblocks))` | `.then(a.unblocks.cmp(&b.unblocks)) // knife` | in_a_layer_unblocks_then_waited_then_number | 紅:解開越多的反而排後面 |
| N4 | crates/aib-drive/src/next.rs | `if let Some(n) = pr.unknown_notes.first() {` | `if let Some(n) = pr.unknown_notes.first().filter(|_| false) { // knife` | a_pr_with_an_unreadable_note_is_unknown_and_never_a_row | 紅:審查稿讀不懂的 PR 沒進 unknown |
| N5 | crates/aib-drive/src/next.rs | `state: if seats.is_some() { "ok" } else { "unsupported" }.into(),` | `state: "ok".into(), // knife` | without_seat_facts_a_review_is_unknown_not_dispatched | 紅:席位事實沒讀也標成 ok |
| N6 | crates/aib-drive/src/next.rs | `let complete = sources.iter().all(Source::ok) && unknown.is_empty();` | `let complete = sources.iter().all(Source::ok); // knife` | a_pr_with_an_unreadable_note_is_unknown_and_never_a_row | 紅:有不明項還說完整 |

整合時照 aibd.yml 既有寫法加一個 step(`sk next.rs '<舊>' '<新>' <測試名>`),舊字串含前導空白,以 TSV 收據檔為準。
