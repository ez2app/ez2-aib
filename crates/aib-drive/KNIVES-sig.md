# KNIVES-sig(S79 sig 席位事實,b433b)

整合席加進 aibd.yml(RULES §21 §25)。每把只動 `crates/aib-drive/src/seats.rs` 一處(錨點各 1 處),都能在 -D warnings 下編過;測試在 `crates/aib-drive/tests/seats.rs`(`cargo test -p aib-drive --locked --test seats <測試名>`),預期:該測試紅。

| 刀 | 拿掉的規則 | 舊字串 | 新字串 | 測試名 |
|---|---|---|---|---|
| SG1 | an unread pile of 10 counts as alive | `(_, Some(u), _) if u >= UNREAD_LOST => Fact::new("no", format!("unread {u}")),` | `(_, Some(u), _) if u >= UNREAD_LOST * 1000 => Fact::new("no", format!("unread {u}")), // knife` | ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle |
| SG2 | a dm still queued for the seat does not mean it is in a turn | `(Known::Known(Some(_)), Some(_)) if is_queued => {` | `(Known::Known(Some(_)), Some(_)) if is_queued && false => { // knife` | recent_output_or_a_queued_dm_means_the_seat_is_in_a_turn |
| SG3 | the coordinator counts as development capacity | `let dispatchable = if role == "coordinator" {` | `let dispatchable = if role == "coordinator" && false { // knife` | the_coordinator_is_never_development_capacity |
| SG4 | an unreadable fact is read as not free instead of unknown | `} else if alive.v == "unknown" || hands.v == "unknown" {` | `} else if alive.v == "unknown" && hands.v == "unknown" && false { // knife` | what_cannot_be_read_is_unknown_and_never_free |
| SG5 | a gone window counts as alive | `(Known::Known(None), _, _) => Fact::new("no", "window gone"),` | `(Known::Known(None), _, _) => Fact::new("yes", "window gone"), // knife` | ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle |
| SG6 | holding this machine's lease is not waiting on a receipt | `let waiting_on = if holds_lease {` | `let waiting_on = if holds_lease && false { // knife` | waiting_on_comes_from_the_lease_and_the_state_and_sets_the_size |
| SG7 | two quiet hours count as alive | `(_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 => Fact::new(` | `(_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 * 1000 => Fact::new( // knife` | ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle |
| SG8 | archived dms are counted | `if status == "archived" {` | `if status == "archived" && false { // knife` | files_tmux_and_lease_parse_as_written |

整行原文(含縮排,給 knife.py):

- SG1
  - 舊:
```
        (_, Some(u), _) if u >= UNREAD_LOST => Fact::new("no", format!("unread {u}")),
```
  - 新:
```
        (_, Some(u), _) if u >= UNREAD_LOST * 1000 => Fact::new("no", format!("unread {u}")), // knife
```
- SG2
  - 舊:
```
        (Known::Known(Some(_)), Some(_)) if is_queued => {
```
  - 新:
```
        (Known::Known(Some(_)), Some(_)) if is_queued && false => { // knife
```
- SG3
  - 舊:
```
    let dispatchable = if role == "coordinator" {
```
  - 新:
```
    let dispatchable = if role == "coordinator" && false { // knife
```
- SG4
  - 舊:
```
    } else if alive.v == "unknown" || hands.v == "unknown" {
```
  - 新:
```
    } else if alive.v == "unknown" && hands.v == "unknown" && false { // knife
```
- SG5
  - 舊:
```
        (Known::Known(None), _, _) => Fact::new("no", "window gone"),
```
  - 新:
```
        (Known::Known(None), _, _) => Fact::new("yes", "window gone"), // knife
```
- SG6
  - 舊:
```
    let waiting_on = if holds_lease {
```
  - 新:
```
    let waiting_on = if holds_lease && false { // knife
```
- SG7
  - 舊:
```
        (_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 => Fact::new(
```
  - 新:
```
        (_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 * 1000 => Fact::new( // knife
```
- SG8
  - 舊:
```
            if status == "archived" {
```
  - 新:
```
            if status == "archived" && false { // knife
```

## sig-b(B3 容量、B7 先做、A4 剛變空、7b81e L1–L3)

| 刀 | 拿掉的規則 | 舊字串 | 新字串 | 測試名 |
|---|---|---|---|---|
| SB1 | WIP_MAX reviewed-not-pushed slices still get a mainline | `("yes", _) if wip_full => "review",` | `("yes", _) if false && wip_full => "review",` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB2 | queued plan nodes are not counted as on deck | `ON_DECK_STATUS.contains(&s)` | `!ON_DECK_STATUS.contains(&s)` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB3 | a branch counts for a seat whose code is only a substring | `.filter(|b| branch_of_seat(b, seat))` | `.filter(|b| b.contains(seat))` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB4 | four quiet minutes count as free (A4 boundary, 7b81e L3) | `if q >= HANDS_FREE_MIN =>` | `if q >= HANDS_FREE_MIN - 1 =>` | four_quiet_minutes_is_still_a_turn_five_is_free |
| SB5 | an unreadable lease still gives waiting_on none (7b81e L2) | `_ if matches!(lease, Known::Unknown(_)) => "unknown",` | `_ if false => "unknown",` | a_lease_that_cannot_be_read_makes_waiting_on_unknown |
| SB6 | the window comes from the latest dm, not the seat file (7b81e L1) | `let where_ = match src.seat_window(seat) {` | `let where_ = match Known::<Option<(String, String)>>::Known(None) {` | the_seat_file_window_wins_over_the_latest_dm |
| SB7 | a seat already free last round is named newly free again (A4 dedupe) | `.filter(|s| prev.get(&s.seat).map(String::as_str) != Some("free"))` | `.filter(|s| prev.len() < usize::MAX || s.role.is_empty())` | newly_free_names_a_seat_once_per_free_stretch |
| SB8 | a tree with commits beyond main is offered for tidy | `filter(|t| t.ahead == 0 && t.dirty == 0)` | `filter(|t| t.dirty == 0)` | first_lists_dm_commit_push_tidy_next_in_that_order |
| SB9 | a seat file of another seat is taken | `if !uuid.ends_with(seat) || !is_seat_code(seat) {` | `if !is_seat_code(seat) {` | the_seat_file_window_wins_over_the_latest_dm |
| SB10 | an unreadable plan tree is not an error | `if let Known::Unknown(e) = &shared.nodes {` | `if let Known::Unknown(e) = &Known::<Vec<PlanNode>>::Known(vec![]) {` | capacity_that_cannot_be_read_is_null_and_an_error_not_a_block |
