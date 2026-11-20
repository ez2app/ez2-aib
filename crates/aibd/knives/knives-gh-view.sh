#!/usr/bin/env bash
# GV1–GV25 G4 gh_view for a trusted seat (DESIGN-trusted-g1g4 §1; 7b81e 11-20): one knife a line of defence
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
gv() { python3 -I scripts/knife.py crates/aibd/src/gh_view.rs "$1" "$2" --expect-fail "gh_view::tests::$3" \
        -- cargo test -p aibd --locked --lib "gh_view::tests::$3"; }
ts() { python3 -I scripts/knife.py crates/aibd/src/trusted_seats.rs "$1" "$2" --expect-fail "trusted_seats::tests::$3" \
        -- cargo test -p aibd --locked --lib "trusted_seats::tests::$3"; }
bc() { python3 -I scripts/knife.py crates/aibd/src/bridge_cfg.rs "$1" "$2" \
  --expect-fail bridge_cfg::tests::a_missing_or_bad_file_is_every_switch_off \
  -- cargo test -p aibd --locked --lib bridge_cfg::tests::a_missing_or_bad_file_is_every_switch_off; }
ge() { python3 -I scripts/knife.py crates/aibd/src/gh_view.rs "$1" "$2" \
  --expect-fail a_trusted_seat_reads_a_pr_through_aibds_own_gh \
  -- cargo test -p aibd --locked --test gh_view a_trusted_seat_reads_a_pr_through_aibds_own_gh; }
# GV1 a number with letters or a flag is taken
gv '        || !number.bytes().all(|b| b.is_ascii_digit())' \
   '        || number.len() > 99 // knife' \
   only_three_whats_and_a_plain_number_are_asked
# GV2 the body names a repo or a flag
gv '        .is_some_and(|o| o.keys().all(|k| fields.contains(&k.as_str())))' \
   '        .is_some_and(|o| o.keys().all(|k| fields.contains(&k.as_str()) || k.len() < 9))' \
   only_three_whats_and_a_plain_number_are_asked
# GV3 any subcommand gets an argv
gv '        _ => return None,' \
   '        _ => ("api", "view", "x"), // knife' \
   the_argv_is_one_of_three_read_only_shapes
# GV4 a long answer is not marked cut
gv '    let cut = raw.len() > OUT_MAX;' \
   '    let cut = raw.len() > OUT_MAX * 2; // knife' \
   a_long_answer_is_cut_and_cleaned
# GV5 gh's answer is given back uncleaned
gv '    (crate::dm_import::clean(&s), cut)' \
   '    (s.into_owned(), cut) // knife' \
   a_long_answer_is_cut_and_cleaned
# GV6 a seat asks as often as it likes
gv '        if self.seat.contains_key(seat) {' \
   '        if self.seat.contains_key(seat) && seat.is_empty() {' \
   one_ask_per_seat_and_thirty_a_minute
# GV7 the machine's cap is gone
gv '        if self.all.len() >= ALL_MAX {' \
   '        if self.all.len() >= ALL_MAX * 4 {' \
   one_ask_per_seat_and_thirty_a_minute
# GV8 asked with the switch off
gv '    if !switch_on {' \
   '    if !switch_on && now == 0 {' \
   only_a_trusted_safe_or_work_seat_asks_and_only_with_the_switch
# GV9 asked while a user page serves
gv '    if user_page {' \
   '    if user_page && now == 0 {' \
   only_a_trusted_safe_or_work_seat_asks_and_only_with_the_switch
# GV10 a dgrs seat asks
gv '    if !matches!(mode, Some("safe" | "work")) {' \
   '    if !matches!(mode, Some("safe" | "work" | "dgrs")) {' \
   only_a_trusted_safe_or_work_seat_asks_and_only_with_the_switch
# GV11 an untrusted seat asks
gv '    if !crate::trusted_seats::holds(trusted_until, now) {' \
   '    if trusted_until.is_none() && now == 0 {' \
   only_a_trusted_safe_or_work_seat_asks_and_only_with_the_switch
# GV12 GH_REPO and GH_HOST reach gh
gv '        c.env_remove(k);' \
   '        c.env(k, ""); // knife' \
   gh_runs_without_the_names_that_point_it_elsewhere
# GV13 a repo setting with a third part or a flag is taken
gv "    matches!(repo.split_once('/'), Some((o, n)) if part(o) && part(n))" \
   "    matches!(repo.split_once('/'), Some((o, n)) if part(o) && !n.is_empty())" \
   the_repo_setting_is_owner_slash_name_only
# GV14 a trust that just ended still holds
ts '    until.is_some_and(|u| u > now && u - now <= MAX_AHEAD_S)' \
   '    until.is_some_and(|u| u >= now && u - now <= MAX_AHEAD_S)' \
   a_trust_holds_until_its_end_and_never_past_a_day
# GV15 a trust runs longer than a day
ts '    until.is_some_and(|u| u > now && u - now <= MAX_AHEAD_S)' \
   '    until.is_some_and(|u| u > now && u - now <= MAX_AHEAD_S * 1000)' \
   a_trust_holds_until_its_end_and_never_past_a_day
# GV16 the trust file is read through a link
ts '    let m = std::fs::symlink_metadata(&p).ok()?;' \
   '    let m = std::fs::metadata(&p).ok()?;' \
   the_file_is_read_at_each_ask_and_only_as_a_regular_file
# GV17 a huge trust file is read
ts '    if !m.is_file() || m.len() > MAX_FILE {' \
   '    if !m.is_file() || m.len() > MAX_FILE * 64 {' \
   the_file_is_read_at_each_ask_and_only_as_a_regular_file
# GV18 a text end of trust is read as a number
ts '    v.get("seats")?.as_object()?.get(seat_id)?.as_u64()' \
   '    v.get("seats")?.as_object()?.get(seat_id).and_then(|x| x.as_u64().or_else(|| x.as_str()?.parse().ok()))' \
   the_file_names_each_seats_end_and_a_bad_file_names_none
# GV19 the switch is on for any value
bc '        trusted_seats: v.get("trusted_seats").and_then(Value::as_bool) == Some(true),' \
   '        trusted_seats: v.get("trusted_seats").is_some_and(|x| x.as_bool() != Some(false)),'
# GV20 every seat is trusted in the act
ge '        let until = crate::trusted_seats::until(&dir, &sid);' \
   '        let until = Some(crate::pair_carry::now_unix() + 60).filter(|_| dir.exists() && !sid.is_empty());'
# GV21 gh runs in aibd's own working folder, maybe a repo
ge '    c.args(args).current_dir(cwd);' \
   '    c.args(args).current_dir(cwd.parent().unwrap_or(cwd));'
# GV22 the act does not count the seat's asks
ge '    if let Err(code) = rate_take(seat_id) {' \
   '    if let Err(code) = rate_take(&format!("{seat_id}{:?}", Instant::now())) {'
# GV23 the switch file is not read in the act
ge '        let switch_on = crate::bridge_cfg::load(&dir).trusted_seats;' \
   '        let switch_on = crate::bridge_cfg::load(&dir).trusted_seats || dir.exists();'
# GV24 a bare name or a folder is run as gh (5d33a PREREAD-ghview L1)
gv '    (p.is_absolute() && std::fs::metadata(&p).is_ok_and(|m| m.is_file())).then_some(p)' \
   '    (!p.as_os_str().is_empty()).then_some(p) // knife' \
   another_gh_is_an_absolute_regular_file_or_none
# GV25 a child left holding the output outlives the limit (5d33a PREREAD-ghview L2)
gv '                nix::sys::signal::Signal::SIGKILL,' \
   '                nix::sys::signal::Signal::SIGCONT,' \
   a_child_left_holding_the_output_is_killed_at_the_limit
