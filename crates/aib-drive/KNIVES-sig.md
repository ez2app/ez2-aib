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
