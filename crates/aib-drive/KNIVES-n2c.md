# KNIVES-n2c · S79 n-2c next_local.rs · ebb36 · 11-15(r3:043dd 審 M1/M2/L1/L2 + 519d0 IFACE r3、r4 C7 / C9 / C12)
整合席照這張加進 aibd.yml 的 aib-drive 刀步驟(RULES §21 §25)。預期:每把都讓所列測試紅(knife.py --expect-fail);每把都要編得過。
指令形:`python3 -I scripts/knife.py crates/aib-drive/src/next_local.rs <舊> <新> --expect-fail <測試> -- cargo test -p aib-drive --locked --test next_local <測試>`(舊 / 新照下面逐字,含行首空白)

## NL1 — approved 沒寫也派(錨點 1 處)
- 測試:a_node_without_approved_is_never_mainline_next
- 舊:
```
        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```
- 新:
```
        if !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```

## NL2 — 被認領的也派(錨點 1 處)
- 測試:a_node_already_moving_or_claimed_is_not_handed_out
- 舊:
```
        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```
- 新:
```
        if !n.approved || !OPEN_STATUSES.contains(&n.status) {
```

## NL3 — 前置沒合也派(錨點 1 處)
- 測試:an_after_item_not_merged_holds_the_node
- 舊:
```
                    Some(false) => open.push(dep.text()),
```
- 新:
```
                    Some(false) => open.clear(),
```

## NL4 — 讀不到當成合了(錨點 1 處)
- 測試:a_merge_state_that_cannot_be_read_is_unknown_not_a_row
- 舊:
```
                    None => cannot.push(format!("{} 合了沒讀不到", dep.text())),
```
- 新:
```
                    None => cannot.truncate(cannot.len()), // knife
```
- (S79 整合 a9136:IFACE r9 起 `after` 不認得改進 `not_known`,`cannot` 只剩這一處 push;舊的 `None => {}` 讓 `let mut cannot` 變成不必要的 mut、編譯錯不算咬(收據 3ed805dc7),改成仍動到 `cannot` 的寫法。)

## NL5 — 前置節點沒完成當完成(錨點 1 處)
- 測試:an_after_node_not_done_holds_the_node
- 舊:
```
        Dep::Node(id) => status_of.get(id.as_str()).map(|s| *s == DONE_STATUS),
```
- 新:
```
        Dep::Node(id) => status_of.get(id.as_str()).map(|_| true),
```

## NL6 — 只該審查的席也派主軸(錨點 1 處)
- 測試:a_review_sized_seat_gets_no_mainline
- 舊:
```
                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))
```
- 新:
```
                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && s.size != "none")
```

## NL7 — 第 5 層解開少的排前(錨點 1 處)
- 測試:more_unblocked_comes_first_in_layer_5
- 舊:
```
                    b.unblocks.cmp(&a.unblocks)
```
- 新:
```
                    a.unblocks.cmp(&b.unblocks)
```

## NL8 — 需人同意的建議進第 6 層(錨點 1 處)
- 測試:ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
- 舊:
```
                if s(i, "classification") != "auto"
```
- 新:
```
                if s(i, "classification") == "x"
```

## NL9 — 一席挑到低等級(錨點 1 處)
- 測試:ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
- 舊:
```
                    Some((pg, pi, _)) if *pg < g || (*pg == g && !older(i, pi)) => {}
```
- 新:
```
                    Some((pg, pi, _)) if *pg > g || (*pg == g && !older(i, pi)) => {}
```

## NL10 — 完成的節點算進卡點(錨點 1 處)
- 測試:the_fleet_blocker_is_the_biggest_group
- 舊:
```
        if n.status.is_empty() || n.status == DONE_STATUS {
```
- 新:
```
        if n.status.is_empty() {
```

## NL11 — 挑最小的一組(錨點 1 處)
- 測試:the_fleet_blocker_is_the_biggest_group
- 舊:
```
        .max_by(|a, b| a.1.len().cmp(&b.1.len()).then_with(|| b.0.cmp(&a.0)))?;
```
- 新:
```
        .max_by(|a, b| b.1.len().cmp(&a.1.len()).then_with(|| b.0.cmp(&a.0)))?;
```

## NL12 — 給 Andy 的解開少的排前(錨點 1 處)
- 測試:for_andy_puts_what_unblocks_most_first
- 舊:
```
    out.sort_by(|a, b| b.unblocks.cmp(&a.unblocks).then_with(|| a.id.cmp(&b.id)));
```
- 新:
```
    out.sort_by(|a, b| a.unblocks.cmp(&b.unblocks).then_with(|| a.id.cmp(&b.id)));
```

## NL13 — owner 席碼重複(錨點 1 處)
- 測試:owner_seats_takes_whole_five_hex_words_once
- 舊:
```
        if is_seat(w) && !out.iter().any(|o| o == w) {
```
- 新:
```
        if is_seat(w) {
```

## NL14 — 統籌席也派開發(錨點 1 處)
- 測試:a_busy_or_coordinator_owner_gets_nothing_and_the_next_owner_does
- 舊:
```
                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))
```
- 新:
```
                .filter(|s| !s.role.is_empty() && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))
```

## NL15 — Andy 否決(status no)的建議照派(M1)(錨點 1 處)
- 測試:an_auto_idea_andy_said_no_to_is_never_dispatched
- 舊:
```
                    || !matches!(s(i, "status"), "auto" | "ok")
```
- 新:
```
                    || s(i, "status") == "needs_human"
```

## NL16 — human_review no 的建議照派(M1)(錨點 1 處)
- 測試:an_auto_idea_andy_said_no_to_is_never_dispatched
- 舊:
```
                    || s(i, "human_review") == "no"
```
- 新:
```
                    || s(i, "human_review") == "x"
```

## NL17 — 沒寫 complete 的帳當完整(M2)(錨點 1 處)
- 測試:a_ledger_that_is_not_whole_gives_no_layer_6_and_no_for_andy
- 舊:
```
        Some((v, _)) if v.get("complete").and_then(Value::as_bool) != Some(true) => Ledger::Partial,
```
- 新:
```
        Some((v, _)) if v.get("complete").and_then(Value::as_bool) == Some(false) => Ledger::Partial,
```

## NL18 — 沒掛節點的建議不說原因(錨點 1 處)
- 測試:an_idea_is_dispatched_only_under_a_node_still_to_do
- 舊:
```
                if node.is_empty() {
```
- 新:
```
                if node == "-" {
```

## NL19 — 節點已完成 / 進行中的建議照派(錨點 1 處)
- 測試:an_idea_is_dispatched_only_under_a_node_still_to_do
- 舊:
```
                    Some(st) if !IDEA_NODE_STATUSES.contains(st) => continue,
```
- 新:
```
                    Some(st) if st.is_empty() => continue,
```

## NL20 — 第 5 層已排的節點第 6 層再排一次(錨點 1 處)
- 測試:an_idea_is_dispatched_only_under_a_node_still_to_do
- 舊:
```
                if inp.claimed.contains(node) || in_five.contains(node) {
```
- 新:
```
                if inp.claimed.contains(node) || in_five.contains("-") {
```

## NL21 — 小席接大列(L1)(錨點 1 處)
- 測試:rows_are_slice_and_go_only_to_a_seat_that_fits
- 舊:
```
    matches!((at(seat_size), at(row_size)), (Some(a), Some(b)) if a >= b)
```
- 新:
```
    matches!((at(seat_size), at(row_size)), (Some(_), Some(_)))
```

## NL22 — 審查席接第 6 層(L1)(錨點 1 處)
- 測試:rows_are_slice_and_go_only_to_a_seat_that_fits
- 舊:
```
                                && fits(&f.size, ROW_SIZE) => {}
```
- 新:
```
                                && f.size != "none" => {}
```

## NL23 — for_andy 收已否決的(L2)(錨點 1 處)
- 測試:for_andy_puts_what_unblocks_most_first
- 舊:
```
        .filter(|i| s(i, "classification") == "needs_human" && s(i, "human_review") == "pending")
```
- 新:
```
        .filter(|i| s(i, "classification") == "needs_human" && s(i, "human_review") != "ok")
```

## NL24 — 不在許可清單的狀態也派(N1)(錨點 1 處)
- 測試:only_the_open_statuses_are_mainline_next
- 舊:
```
        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```
- 新:
```
        if !n.approved || n.status == DONE_STATUS || inp.claimed.contains(n.id) {
```

## NL25 — 核准後範圍被改照派(C12)(錨點 1 處)
- 測試:a_node_whose_scope_changed_after_the_approval_is_unknown
- 舊:
```
        (Some(want), Some(h)) if h(&scope_text(n.title, &n.after_raw)) == want => ScopeCheck::Same,
```
- 新:
```
        (Some(want), Some(h)) if h(&scope_text(n.title, &n.after_raw)) == want || true => ScopeCheck::Same,
```

## NL26 — 雜湊讀不到當成範圍沒改(C12)(錨點 1 處)
- 測試:a_scope_that_cannot_be_hashed_is_unknown_not_a_row
- 舊:
```
        (Some(_), None) => ScopeCheck::Unreadable,
```
- 新:
```
        (Some(_), None) => if n.id.is_empty() { ScopeCheck::Unreadable } else { ScopeCheck::Same }, // knife
```

## NL27 — 沒 scope 不標 scope_unchecked(C12)(錨點 1 處)
- 測試:the_output_serializes_with_the_iface_field_names
- 舊:
```
        ScopeCheck::Unchecked => Ok(true),
```
- 新:
```
        ScopeCheck::Unchecked => Ok(false),
```

## NL28 — 第 6 層不查範圍(C12)(錨點 1 處)
- 測試:an_idea_under_a_node_whose_scope_changed_is_unknown
- 舊:
```
                    Some(n) if n.approved => match scope_gate(n, inp.scope_hash) {
```
- 新:
```
                    Some(n) if n.approved && false => match scope_gate(n, inp.scope_hash) {
```

## NL29 — 範圍字串漏 after(C12)(錨點 1 處)
- 測試:scope_text_is_the_title_then_each_after_item
- 舊:
```
        .chain(after.iter().copied())
```
- 新:
```
        .chain(after.iter().copied().take(0))
```

## NL30 — 第 7 層沒做卻報 ok(C7)(錨點 1 處)
- 測試:the_output_serializes_with_the_iface_field_names
- 舊:
```
    sources.insert("tidy_scan".to_owned(), "unsupported".to_owned());
```
- 新:
```
    sources.insert("tidy_scan".to_owned(), "ok".to_owned());
```

## NL31 — scope_unchecked 不輸出(C12)(錨點 1 處)
- 測試:the_output_serializes_with_the_iface_field_names
- 舊:
```
    #[serde(skip_serializing_if = "is_false")]
```
- 新:
```
    #[serde(skip_serializing_if = "is_false", rename = "scope_unchecked_knife")]
```

整合(a9136,收據 1a2cd1188):NL26、NL31 進了函式庫後原寫法讓東西變成沒用到(dead_code 編譯錯，刀不算咬),改寫成拿掉同一條規則、東西仍被用到。

## 整合加的(a9136,519d0 試跑 T2 / T5)

| 刀 | 舊 | 新 | 測試 | 紅了代表 |
|---|---|---|---|---|
| NL32 | `if devs > 1 {` | `if devs > 99 { // knife` | a_busy_or_coordinator_owner_gets_nothing_and_the_next_owner_does | 多人 owner 的節點照派第一個可派的(519d0 T5) |
| NL33 | `.any(\|o\| seat_of(seats, o).is_some_and(\|s\| s.dispatchable == LOST))` | `.any(\|o\| seat_of(seats, o).is_some_and(\|s\| s.dispatchable == UNREAD_UNKNOWN)) // knife` | a_node_whose_owner_is_lost_is_skipped_not_unknown | 失聯 owner 的節點沒進 skipped(519d0 T2) |
