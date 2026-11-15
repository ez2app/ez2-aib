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

## sig-b(B3 容量、B7 先做、A4 剛變空、7b81e sig L1–L3 與 sig-b r1 審查)

| 刀 | 拿掉的規則 | 舊字串 | 新字串 | 測試名 |
|---|---|---|---|---|
| SB1 | WIP_MAX reviewed-not-pushed slices still get a mainline | `("yes", _) if wip_full => "review",` | `("yes", _) if false && wip_full => "review",` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB2 | planned nodes are counted wrongly | `ON_DECK_STATUS.contains(&s)` | `!ON_DECK_STATUS.contains(&s)` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB3 | a branch counts for a seat whose code is only a substring | `.filter(|b| branch_of_seat(b, seat))` | `.filter(|b| b.contains(seat))` | capacity_comes_from_the_plan_tree_and_the_open_prs |
| SB4 | four quiet minutes count as free (A4 boundary, 7b81e L3) | `if q >= HANDS_FREE_MIN =>` | `if q >= HANDS_FREE_MIN - 1 =>` | four_quiet_minutes_is_still_a_turn_five_is_free |
| SB5 | an unreadable lease still gives waiting_on none (7b81e L2) | `_ if matches!(lease, Known::Unknown(_)) => "unknown",` | `_ if false => "unknown",` | a_lease_that_cannot_be_read_makes_waiting_on_unknown |
| SB6 | the window comes from the latest dm, not the seat file (7b81e L1) | `let where_ = match shared.wins.as_ref_known().map(|m| m.get(seat).cloned()) {` | `let where_ = match Known::<Option<(String, String)>>::Known(None) {` | the_seat_file_window_wins_over_the_latest_dm |
| SB7 | a seat already free last round is named newly free again (A4 dedupe) | `.filter(|s| prev.get(&s.seat).map(String::as_str) != Some("free"))` | `.filter(|s| prev.len() < usize::MAX || s.role.is_empty())` | newly_free_names_a_seat_once_per_free_stretch |
| SB8 | a tree with commits beyond main is offered for tidy | `    t.ahead == 0` | `    t.ahead < u64::MAX` | first_is_iface_codes_and_the_detail_lists_dm_commit_push_tidy |
| SB9 | a seat file of another seat is taken | `if !uuid.ends_with(seat) || !is_seat_code(seat) {` | `if uuid.is_empty() || !is_seat_code(seat) {` | the_seat_file_window_wins_over_the_latest_dm |
| SB10 | an unreadable plan tree is not reported as partial | `if let Known::Unknown(e) = &shared.nodes {` | `if let Known::Unknown(e) = &Known::<Vec<PlanNode>>::Known(vec![]) {` | capacity_that_cannot_be_read_is_null_and_partial_not_a_block |
| SB11 | a lost seat with unreadable hands makes the round incomplete (next f106a7f8 M1) | `.filter(|s| s.alive.v != "no")` | `.filter(|s| !s.alive.v.is_empty())` | a_lost_seat_does_not_make_the_round_incomplete |
| SB12 | idle.jsonl is written under state/drive instead of <root>/drive (7b81e sig-b M3) | `root.join("drive").join("idle.jsonl")` | `root.join("state").join("drive").join("idle.jsonl")` | idle_is_under_drive_and_only_the_json_round_writes_it |
| SB13 | a hand-run text view appends idle lines and eats newly free (7b81e sig-b L1) | `    if as_json {` | `    if as_json || !as_json {` | idle_is_under_drive_and_only_the_json_round_writes_it |
| SB14 | an unreadable capacity source makes the round incomplete (7b81e sig-b L2) | `.all(facts_read);` | `.all(facts_read) && partial.is_empty();` | capacity_that_cannot_be_read_is_null_and_partial_not_a_block |
| SB15 | a freshly opened tree is offered for collection | `.is_some_and(|c| now.saturating_sub(c) >= CLEAN_AFTER_H * 3600)` | `.is_some_and(|c| c > 0 || now > 0)` | a_fresh_tree_is_not_collected |

## sig-c(519d0 11-15 07:41:B7 plan_node / memory_tidy / handoff,B3 --repo 資料夾)

| 刀 | 拿掉的規則 | 舊字串 | 新字串 | 測試名 |
|---|---|---|---|---|
| SC1 | another seat's 未驗 node counts as this seat's plan_node | `Known::Known(n) => owned(n, seat, \|s\| s == PLAN_UNVERIFIED),` | `Known::Known(n) => owned(n, "a9136", \|s\| s != PLAN_UNVERIFIED),` | first_lists_all_four_codes_in_iface_order_each_only_when_due |
| SC2 | memory_tidy fires at exactly 90 lines (the tidy limit is over 90) | `if memory.is_some_and(\|n\| n > MEMORY_LINES_MAX) {` | `if memory.is_some_and(\|n\| n >= MEMORY_LINES_MAX) {` | first_codes_stay_off_at_the_limit_and_when_a_source_cannot_be_read |
| SC3 | an open handoff is not listed | `    if handoff.is_some() {` | `    if handoff.is_some() && handoff.is_none() {` | first_lists_all_four_codes_in_iface_order_each_only_when_due |
| SC4 | a receipt written in the same second as the collect leaves the handoff open | `Some(r) if r >= collected => None,` | `Some(r) if r > collected => None,` | a_handoff_is_open_only_while_no_receipt_is_as_new_as_the_collect |
| SC5 | the first receipt line is taken instead of the last | `        .rev()` | `        .skip(0)` | a_handoff_is_open_only_while_no_receipt_is_as_new_as_the_collect |
| SC6 | any github path is taken as OWNER/NAME | `is_owner_name(rest).then(\|\| rest.to_string())` | `Some(rest.to_string())` | repo_names_come_from_a_github_remote_and_nothing_else |
| SC7 | a repo folder is handed to gh as it is (B3: open_prs never read) | `    if !p.is_dir() {` | `    if !p.is_dir() \|\| p.is_dir() {` | a_repo_folder_is_turned_into_owner_name_before_gh_sees_it |
| SC8 | a seat with a live dm queue but no seat-now file is never listed (B1: c477a / 2240e lost but unseen) | `.is_some_and(\|t\| now.saturating_sub(t.as_secs()) <= LIST_DAYS * 86_400);` | `.is_some_and(\|t\| now.saturating_sub(t.as_secs()) > LIST_DAYS * 86_400);` | a_seat_with_no_seat_now_file_but_a_live_dm_queue_is_listed_and_judged_lost |
| SC9 | a queue-only seat with no seat-now file stays unknown, the round goes rc 3 (87167 M1) | `Known::Unknown(_) => src.seat_now_gone(seat),` | `Known::Unknown(_) => false,` | a_seat_with_no_seat_now_for_two_days_is_lost_even_when_window_and_unread_cannot_say |
| SC10 | no memory file is taken as unreadable (87167 L1) | `Err(e) if e.kind() == std::io::ErrorKind::NotFound => Known::Known(0),` | `Err(e) if e.kind() == std::io::ErrorKind::NotFound && false => Known::Known(0),` | the_file_source_reads_memory_lines_and_an_open_handoff |
| SC11 | a fresh beat is ignored and the seat called lost | `} && beat.is_none_or(stale);` | `};` | a_seat_with_no_seat_now_for_two_days_is_lost_even_when_window_and_unread_cannot_say |
