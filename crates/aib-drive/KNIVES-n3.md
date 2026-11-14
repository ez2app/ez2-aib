status: 定稿 · rev: 9 · supersedes: 無 · node: S79-n3 · owner: 043dd · 11-15(台北)

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
| A16 | --dry-run / --send 都沒帶也跑(只准二選一) | only_the_dry_run_exists_and_a_missing_source_is_rc_3 |
| A17 | 開關檔多一個不認得的開關照讀 | switches_default_off_and_a_broken_file_is_an_error |
| A18 | 切開關不檢查 --by 是不是席碼 | a_switch_flip_is_recorded_with_who_and_why |
| A19 | 暫停連提醒、收尾也擋(應只擋新派工) | a_pause_holds_back_only_new_work |
| A20 | 暫停什麼都不擋 | 同上 |
| A21 | act 裡出現呼叫外部程式(合併、推送、刪除…的入口) | act_runs_no_outside_program |
| A22 | on_deck 讀不到(null)時信裡不加註 | nulls_from_sig_and_next_read_as_not_known |
| A23 | 欄位是 null 就整份解析失敗(只認「欄位不存在」) | 同上 |
| A24 | 不認 EZ2_AIB_STATE_ROOT(狀態檔跟 observe / wake 分家) | the_state_root_comes_first_and_sig_nulls_still_make_a_round |
| A25 | 派工信不帶 next 列的頭碼(E7:證據照抄 next 的列) | a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch |
| A26 | (A5)act 自己寫的事件照樣開一輪 | an_event_act_wrote_itself_does_not_start_a_round |
| A27 | (a9136 F26)已經寫「審」的審查列又加一個「審」 | a_review_row_that_already_says_review_gets_one_review_word |
| A28 | (IFACE r10 1)第 2 層的修正列不出動作 | a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on |
| A29 | (IFACE r11 4)基礎設施失敗發給作者、不發合併隊 | an_infra_red_goes_to_the_merge_queue_not_the_author |
| A30 | 修正信第一行少了分支與原因之間的冒號 | a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on |
| A31 | 暫停時修正信照發 | a_pause_holds_back_only_new_work |
| A32 | (7b81e A5 N2)只給 --by、沒給 --event 不報錯 | by_without_an_event_is_a_usage_error |
| IN1 | (E4 intents.rs)送到一半被殺,下一輪又送 | killed_while_sending_is_not_sent_again |
| IN2 | 已 done 的又送一次 | a_message_is_sent_once_and_a_second_run_does_nothing |
| IN3 | 重查前提說不送,照送 | a_recheck_that_says_no_sends_nothing_and_stays_closed |
| IN4 | 送失敗寫成 done | a_failed_send_is_abandoned_not_done |
| SN1 | (E5 act.rs)修正通知真的發出(E6/E7 開審查、派工 11-15 起可發,notify_fix 仍不發) | review_dispatch_and_dispatch_are_sent_and_fix_requests_never |
| SN2 | 開關關著照發 | a_switch_that_is_off_sends_nothing |
| SN3 | 重查不看那封信還在不在排隊 | a_nudge_whose_dm_was_taken_meanwhile_is_dropped |
| SN4 | 沒有艙位碼的席照發(位址只剩席碼) | 同上 |
| SN5 | 多個收件人共用一把鍵,只有第一個收到 | telling_the_sender_goes_once_per_recipient |
| SN6 | (E10)done 行沒記送達階段 | the_done_line_keeps_the_delivery_stage_apart_from_read_and_taken |
| SN7 | (source.rs)aib dm 印什麼階段都算送出 | the_result_line_gives_the_stage_and_the_id |
| SN8 | 開頭是 - 的內文交給 aib dm(會被當旗標) | aib_dm_takes_only_an_address_and_one_text |
| IN5 | (7b81e E4 M1)非終局 abandoned 之後的 intent 當帳本錯誤 | a_recheck_that_said_no_is_tried_again_and_sent_once |
| IN6 | 所有 abandoned 都當終局(一次忙就永遠不發) | 同上 |
| IN7 | (7b81e E4 L1)must_be 放過第二個寫的人 | a_second_writer_in_between_stops_this_run_before_it_sends |
| SN9 | (ebb36 D1+C5 第 1 題)next 不完整也照發 | send_with_an_incomplete_next_sends_nothing |
| SN10 | retry 行被忽略，沒送出的鍵下一輪不再排 | a_retry_line_lets_the_next_round_plan_the_key_again |
| IN8 | (7b81e d3ad28dc M1)結果不明的送出寫成可重試(會重送) | a_send_whose_result_is_unknown_is_never_sent_again |
| SN11 | 啟動不了的程式當成可能已送(永不重試) | a_dm_that_could_not_start_is_not_sent_and_a_failed_call_is_unknown |
| SN12 | (5d33a r2 1)結束碼不是 0 就當確定沒送(exit 3 / 4 會重送) | the_result_line_gives_the_stage_and_the_id |
| SN13 | (M2)next 不完整那輪沒把鍵還回去,nudge 被吃掉 | an_incomplete_next_does_not_eat_the_nudge |
| SN14 | (L1)沒有退避,retry 立刻重排 | a_retry_line_lets_the_next_round_plan_the_key_again |
| SN15 | 永不放棄 | a_key_is_given_up_after_three_tries_that_did_not_go_out |
| SN16 | 整輪沒送的 retry 也計次、也退避 | 同上 |
| SN17 | (M1 實證)送達的階段只在結束碼 0 才算(rc 5 + delivered 會變不明) | a_dm_that_could_not_start_is_not_sent_and_a_failed_call_is_unknown |
| SN18 | (5d33a H1)aib dm 實際印的 sort-queued 不算排隊 | the_result_line_gives_the_stage_and_the_id |
| SN19 | (5d33a r2 2)提醒退避期間改排告知寄件人 | a_retry_line_lets_the_next_round_plan_the_key_again |
| TR1 | (E10)掛等待不算接單 | the_four_steps_are_kept_apart |
| TR2 | (E10)已讀當成接單 | the_four_steps_are_kept_apart |
| TR3 | (E10)讀不到的一段當成「否」 | a_step_that_cannot_be_read_is_not_a_no |
| TR4 | (E10)送出前就開始的項目也算開工 | the_four_steps_are_kept_apart |
| TR5 | (E10)不到 2 小時就標記 | work_not_taken_up_after_two_hours_is_marked_once |
| TR6 | (E10)每輪都再標記一次 | work_not_taken_up_after_two_hours_is_marked_once |
| TR7 | (E10)讀不到接單也標記 | work_not_taken_up_after_two_hours_is_marked_once |
| TR8 | (E10)提醒也當派的工作跟進 | only_work_sent_in_the_last_day_is_followed |
| TR9 | (E10)超過一天的還在跟進 | only_work_sent_in_the_last_day_is_followed |
| FW1 | (E10)派給一席的列改派給別席 | a_row_sent_to_one_seat_is_not_handed_to_another |
| FW2 | (E10)列永遠綁在原席 | a_row_sent_to_one_seat_is_not_handed_to_another |
| FW3 | (E10)沒送出的工作信仍綁住那一列 | a_retry_frees_the_row_and_a_mark_is_only_a_mark |
| FW4 | (E10)不讀 mark 行(每輪重標) | a_retry_frees_the_row_and_a_mark_is_only_a_mark |
| FW5 | (E10)四段沒印出來 | work_sent_before_is_followed_step_by_step |
| SD1 | (E7)派工沒真的發 | review_dispatch_and_dispatch_are_sent_and_fix_requests_never |
| SD2 | (E6)開審查沒真的發 | review_dispatch_and_dispatch_are_sent_and_fix_requests_never |
| SD3 | (E7)派工信沒有證據行 | a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch |
| SD4 | (E6/E7)開關關著也發，跟試跑算的不一樣 | review_dispatch_and_dispatch_go_out_only_as_the_dry_run_says |

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
ak '        if !infra && (seat.role != "dev" || to == COORDINATOR) {' '        if false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A5 more than 10 unread is dispatched
ak '            Some(u) if u <= UNREAD_MAX => {}' '            Some(u) if u <= UNREAD_MAX || true => {} // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A6 the seat on deck in the merge queue is dispatched
ak '        let deck_note = match seat.capacity.on_deck {' '        let deck_note = match seat.capacity.on_deck.map(|_| false) { // knife' \
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
ak '        if w.state != "queued" || w.queued_min < NUDGE_AFTER_MIN {' '        if w.state != "queued" { // knife' \
   a_queued_dm_is_nudged_once_then_the_sender_is_told_once
# A14 a lock that has not expired is taken
ak '        if exp > now_ms {' '        if exp > now_ms && false { // knife' \
   the_lock_is_one_holder_until_it_expires
# A15 the old holder removes the new holder's lock
ak '        if fs::read_to_string(&self.path).ok().as_deref() == Some(self.mine.as_str()) {' '        if !self.mine.is_empty() { // knife' \
   the_lock_is_one_holder_until_it_expires
# A16 act runs with neither --dry-run nor --send
ak '    if !f.has("once") || f.has("dry-run") == f.has("send") {' '    if !f.has("once") { // knife' \
   only_the_dry_run_exists_and_a_missing_source_is_rc_3
# A17 an unknown switch in act.json is read
ak '                _ => return Err(format!("act.json: unknown switch {k}")),' '                _ => {} // knife' \
   switches_default_off_and_a_broken_file_is_an_error
# A18 switch takes any --by
ak '    if !seat_ok(by) || why.trim().is_empty() {' '    if seat_ok("") || why.trim().is_empty() { // knife' \
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
# A22 an unknown on_deck carries no note
ak '        if let Some(n) = &deck_note {' '        if let Some(n) = deck_note.as_ref().filter(|_| false) { // knife' \
   nulls_from_sig_and_next_read_as_not_known
# A23 a null field fails the whole parse
ak '    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())' '    T::deserialize(d) // knife' \
   nulls_from_sig_and_next_read_as_not_known
# A24 EZ2_AIB_STATE_ROOT is not read
ak '    let root = match nonempty("EZ2_AIB_STATE_ROOT").or_else(|| nonempty("EZ2_AIB_HOME")) {' '    let root = match nonempty("EZ2_AIB_HOME") { // knife' \
   the_state_root_comes_first_and_sig_nulls_still_make_a_round
# A25 the dispatch dm leaves out the head from the next row
ak '        b.push(format!("頭:{h}"));' '        let _ = h; // knife' \
   a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch
# A26 (A5) an event act wrote itself starts a round anyway
ak '    crate::wake::is_own(&ev, me).then_some(ev.id)' '    (crate::wake::is_own(&ev, me) && false).then_some(ev.id) // knife' \
   an_event_act_wrote_itself_does_not_start_a_round
# A27 (a9136 F26) a review row that already says 審 gets a second one
ak '"open_review" if !row.what' '"open_review" if true || !row.what' \
   a_review_row_that_already_says_review_gets_one_review_word
# A28 (r10 1) a layer-2 fix row is not acted on
ak '    if row.layer == 2 && row.kind == "fix" {' '    if row.layer == 2 && row.kind == "fix" && false { // knife' \
   a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on
# A29 (r11 4) an infrastructure red goes to the author
ak 'Some(MERGE_QUEUE) } else { author }' 'Some(MERGE_QUEUE).filter(|_| false).or(author) } else { author }' \
   an_infra_red_goes_to_the_merge_queue_not_the_author
# A30 the fix line leaves out the colon between what and why
ak '            format!("派工: 修 {target}:{why} → {seat}")' '            format!("派工: 修 {target}{why} → {seat}") // knife' \
   a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on
# A31 a pause does not hold back a fix request
ak 'matches!(action, "dispatch" | "open_review" | "notify_fix")' 'matches!(action, "dispatch" | "open_review")' \
   a_pause_holds_back_only_new_work
```

整合(a9136,收據 1a2cd1188):A15、A18 進了函式庫後原寫法讓東西變成沒用到(dead_code 編譯錯，刀不算咬),改寫成拿掉同一條規則、東西仍被用到。

刀 A32、IN1–IN4(E4)、SN1–SN19、IN5–IN8(E5/E9/E10 與 7b81e E4 M1/L1,043dd 11-15)、TR1–TR9 / FW1–FW5(E10 跟進四段，tk = track.rs)、SD1–SD4(E6/E7 真的發)的 bash 原文以 aibd.yml「aib-drive act knives (macOS)」step 為準。
