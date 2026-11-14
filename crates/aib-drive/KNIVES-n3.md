status: 定稿 · rev: 1 · supersedes: 無 · node: S79-n3 · owner: 043dd · 11-15(台北)

# KNIVES-n3:act.rs 的刀(S79 n-3,IFACE-s79 §0 / §3)

整合席(a9136)把下面的 bash 區塊原樣搬進 aibd.yml 一個新 step `aib-drive act knives (macOS)`(`if: env.RUN == 'true' && runner.os == 'macOS'`、`shell: bash`)。每把刀拿掉一條規則,它自己的測試要變紅(`--expect-fail`)。錨點在 rustfmt 之後逐把數過,各 1 處。

| 刀 | 拿掉什麼 | 咬哪支測試 |
|---|---|---|
| A1 | 第 1 層(能合)也被當成開審查 | a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch |
| A2 | 需人同意的列照派 | a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent |
| A3 | 失聯的席照派 | 同上 |
| A4 | 統籌席被派開發工作 | 同上 |
| A5 | 未讀 > 10 照派 | 同上 |
| A6 | 合併隊下一張的作者照派 | 同上 |
| A7 | 大小不合照派 | 同上 |
| A8 | 做過的去重鍵再做一次 | done_keys_and_the_thirty_minute_gap_hold_across_rounds |
| A9 | 每席 30 分一封的節流拿掉 | 同上 |
| A10 | 有收尾沒做照派新工作 | a_seat_with_wrap_up_left_gets_only_the_wrap_up |
| A11 | 同一席同一輪分成多封 | rows_for_one_seat_in_one_round_go_in_one_dm |
| A12 | 同一封信一直提醒 | a_queued_dm_is_nudged_once_then_the_sender_is_told_once |
| A13 | 排隊不到 5 分就提醒 | 同上 |
| A14 | 鎖還沒到期就被搶走 | the_lock_is_one_holder_until_it_expires |
| A15 | 舊持鎖者結束時刪掉新持鎖者的鎖 | 同上 |
| A16 | 不帶 --dry-run 也跑(這一版只准 dry-run) | only_the_dry_run_exists_and_a_missing_source_is_rc_3 |
| A17 | 開關檔多一個不認得的開關照讀 | switches_default_off_and_a_broken_file_is_an_error |
| A18 | 切開關不檢查 --by 是不是席碼 | a_switch_flip_is_recorded_with_who_and_why |
| A19 | 暫停連提醒、收尾也擋(應只擋新派工) | a_pause_holds_back_only_new_work |
| A20 | 暫停什麼都不擋 | 同上 |
| A21 | act 裡出現呼叫外部程式(合併、推送、刪除…的入口) | act_runs_no_outside_program |

```bash
set -eu
ak() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# A1 a layer-1 row (merge) is taken as a review
ak '        3 => Some("open_review"),' '        1 | 3 => Some("open_review"), // knife' \
   a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch
# A2 a needs_human row is dispatched
ak '        if row.needs != "none" {' '        if false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A3 a lost seat is dispatched
ak '        if seat.alive.v != "yes" {' '        if seat.alive.v != "yes" && false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A4 the coordinator gets development work
ak '        if seat.role != "dev" || to == COORDINATOR {' '        if false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A5 more than 10 unread is dispatched
ak '            Some(u) if u <= UNREAD_MAX => {}' '            Some(u) if u <= UNREAD_MAX || true => {} // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A6 the seat on deck in the merge queue is dispatched
ak '        if seat.capacity.on_deck {' '        if seat.capacity.on_deck && false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A7 a row bigger than the seat's size is dispatched
ak '            (Some(r), Some(s)) if r <= s => {}' '            (Some(_), Some(_)) => {} // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A8 a done key is done again
ak '        if hist.keys.contains(&k) {' '        if hist.keys.contains(&k) && false { // knife' \
   done_keys_and_the_thirty_minute_gap_hold_across_rounds
# A9 no 30-minute gap per seat
ak '            if now_ms - last < SEAT_GAP_MIN * 60_000 {' '            if now_ms - last < SEAT_GAP_MIN * 60_000 && false { // knife' \
   done_keys_and_the_thirty_minute_gap_hold_across_rounds
# A10 new work before the wrap-up
ak '        if !seat.first.is_empty() {' '        if !seat.first.is_empty() && false { // knife' \
   a_seat_with_wrap_up_left_gets_only_the_wrap_up
# A11 one seat gets several dms in one round
ak '        if let Some(&i) = this_round.get(to) {' '        if let Some(&i) = this_round.get(to).filter(|_| false) { // knife' \
   rows_for_one_seat_in_one_round_go_in_one_dm
# A12 the same dm is nudged again and again
ak '        if !hist.keys.contains(&nk) {' '        if true { // knife' \
   a_queued_dm_is_nudged_once_then_the_sender_is_told_once
# A13 a dm queued under 5 minutes is nudged
ak '        if w.state != "sort-queued" || w.queued_min < NUDGE_AFTER_MIN {' '        if w.state != "sort-queued" { // knife' \
   a_queued_dm_is_nudged_once_then_the_sender_is_told_once
# A14 a lock that has not expired is taken
ak '        if exp > now_ms {' '        if exp > now_ms && false { // knife' \
   the_lock_is_one_holder_until_it_expires
# A15 the old holder removes the new holder's lock
ak '        if fs::read_to_string(&self.path).ok().as_deref() == Some(self.mine.as_str()) {' '        if true { // knife' \
   the_lock_is_one_holder_until_it_expires
# A16 act runs without --dry-run
ak '    if !f.has("once") || !f.has("dry-run") {' '    if !f.has("once") { // knife' \
   only_the_dry_run_exists_and_a_missing_source_is_rc_3
# A17 an unknown switch in act.json is read
ak '                _ => return Err(format!("act.json: unknown switch {k}")),' '                _ => {} // knife' \
   switches_default_off_and_a_broken_file_is_an_error
# A18 switch takes any --by
ak '    if !seat_ok(by) || why.trim().is_empty() {' '    if why.trim().is_empty() { // knife' \
   a_switch_flip_is_recorded_with_who_and_why
# A19 a pause also holds back nudges and wrap-ups
ak '    sw.get(action) && !(sw.paused && new_work)' '    sw.get(action) && !sw.paused && (new_work || !new_work) // knife' \
   a_pause_holds_back_only_new_work
# A20 a pause holds back nothing
ak '    sw.get(action) && !(sw.paused && new_work)' '    sw.get(action) && !(sw.paused && new_work && false) // knife' \
   a_pause_holds_back_only_new_work
# A21 act calls an outside program
ak 'pub fn would_send(sw: Switches, action: &str) -> bool {' 'pub fn would_send(sw: Switches, action: &str) -> bool { let _ = std::process::Command::new("git"); // knife' \
   act_runs_no_outside_program
```
