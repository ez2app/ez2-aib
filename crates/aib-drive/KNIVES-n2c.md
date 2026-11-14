# KNIVES-n2c · S79 n-2c next_local.rs · ebb36 · 11-15
整合席照這張加進 aibd.yml 的 aib-drive 刀步驟(RULES §21 §25)。預期:每把都讓所列測試紅(knife.py --expect-fail);每把都要編得過。
指令形:`python3 -I scripts/knife.py crates/aib-drive/src/next_local.rs <舊> <新> --expect-fail <測試> -- cargo test -p aib-drive --locked --test next_local <測試>`(舊 / 新照下面逐字,含行首空白)

## NL1 — approved 沒寫也派(錨點 1 處)
- 測試:a_node_without_approved_is_never_mainline_next
- 舊:
```
        if !n.approved || MOVING_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```
- 新:
```
        if MOVING_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```

## NL2 — 被認領的也派(錨點 1 處)
- 測試:a_node_already_moving_or_claimed_is_not_handed_out
- 舊:
```
        if !n.approved || MOVING_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {
```
- 新:
```
        if !n.approved || MOVING_STATUSES.contains(&n.status) {
```

## NL3 — 前置沒合也派(錨點 1 處)
- 測試:an_after_item_not_merged_holds_the_node
- 舊:
```
                    Some(false) => open.push(dep.text()),
```
- 新:
```
                    Some(false) => {}
```

## NL4 — 讀不到當成合了(錨點 1 處)
- 測試:a_merge_state_that_cannot_be_read_is_unknown_not_a_row
- 舊:
```
                    None => cannot.push(format!("{} 合了沒讀不到", dep.text())),
```
- 新:
```
                    None => {}
```

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
                    && (s.size == "mainline" || s.size == "slice")
```
- 新:
```
                    && s.size != "none"
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

## NL8 — 需人同意的建議進排行(錨點 1 處)
- 測試:ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
- 舊:
```
                if s(i, "classification") != "auto" || s(i, "status") == "needs_human" {
```
- 新:
```
                if s(i, "status") == "x" {
```

## NL9 — 一席挑到低等級(錨點 1 處)
- 測試:ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
- 舊:
```
                    Some((pg, pi)) if *pg < g || (*pg == g && !older(i, pi)) => {}
```
- 新:
```
                    Some((pg, pi)) if *pg > g || (*pg == g && !older(i, pi)) => {}
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
                s.role == "dev"
```
- 新:
```
                !s.role.is_empty()
```

