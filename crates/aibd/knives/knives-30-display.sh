#!/usr/bin/env bash
# aib-knives: os=macos
# N-workspace-c3 + N-pv-needs knives DS1–DS3, ND1–ND2, FL1 (7b81e 11-19; 519d0 11-18 21:17 / SPEC-workspace-multiseat
# §23-1; Andy 定版 11-19 00:0x): aibd gives the display station and its tag, a card's 「要靠誰」 ids, and aib-plan
# writes the verify-fail record without moving the station. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# DS1 the old 「實查不通過」 shows where it is written, with no tag
python3 -I scripts/knife.py crates/aibd/src/display.rs \
  '        Some(OLD_FAIL) => (' \
  '        Some(OLD_FAIL) if false => ( // knife' \
  --expect-fail display::tests::the_old_fail_name_shows_at_station_three_with_its_tag \
  -- cargo test -p aibd --locked --lib display::tests::the_old_fail_name_shows_at_station_three_with_its_tag
# DS2 an open verify fail at 未驗 gets no 「驗不通過」 tag
python3 -I scripts/knife.py crates/aibd/src/display.rs \
  '        Some(UNVERIFIED) if verify_fail_of(n).is_some() => (' \
  '        Some(UNVERIFIED) if verify_fail_of(n).is_some() && false => ( // knife' \
  --expect-fail display::tests::an_open_verify_fail_keeps_the_station_and_adds_its_tag \
  -- cargo test -p aibd --locked --lib display::tests::an_open_verify_fail_keeps_the_station_and_adds_its_tag
# DS3 a closed record still tags the card
python3 -I scripts/knife.py crates/aibd/src/display.rs \
  '                (false, Some(s)) => Some(json!({"closed": false, "sev": s})),' \
  '                (_, Some(s)) => Some(json!({"closed": closed, "sev": s})), // knife' \
  --expect-fail display::tests::an_open_verify_fail_keeps_the_station_and_adds_its_tag \
  -- cargo test -p aibd --locked --lib display::tests::an_open_verify_fail_keeps_the_station_and_adds_its_tag
# ND1 a 「要靠誰」 id that is not on the tree is written
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '        if !exists(x) {' \
  '        if !exists(x) && false { // knife' \
  --expect-fail card::tests::needs_lands_as_a_list_of_the_trees_own_ids \
  -- cargo test -p aib-plan --locked --lib card::tests::needs_lands_as_a_list_of_the_trees_own_ids
# ND2 GET nodes leaves the 「要靠誰」 ids out
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '    o.insert("needs".into(), json!(needs));' \
  '    o.insert("needs".into(), json!(needs.iter().take(0).collect::<Vec<_>>())); // knife' \
  --expect-fail workspace::tests::a_node_carries_its_display_station_tag_and_needs \
  -- cargo test -p aibd --locked --lib workspace::tests::a_node_carries_its_display_station_tag_and_needs
# FL1 closing a verify fail leaves it open
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '            serde_json::json!({"sev": sev, "closed": true, "at": at, "by": seat})' \
  '            serde_json::json!({"sev": sev, "closed": false, "at": at, "by": seat}) // knife' \
  --expect-fail card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station \
  -- cargo test -p aib-plan --locked --lib card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station
# VF1 the author closes or lowers a record another seat opened (5d33a L1)
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '        let may = seat == by || reviewer == Some(seat);' \
  '        let may = true; let _ = reviewer; // knife' \
  --expect-fail card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station \
  -- cargo test -p aib-plan --locked --lib card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station
# VF2 an open H is lowered to M (5d33a L1)
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '        if sev == "H" && v.trim() == "M" {' \
  '        if sev == "H" && v.trim() == "M" && false { // knife' \
  --expect-fail card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station \
  -- cargo test -p aib-plan --locked --lib card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station
# VF3 a closed record is closed again, its at / by rewritten (5d33a L1)
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '    } else if old.get("closed").and_then(serde_json::Value::as_bool) == Some(true)' \
  '    } else if false // knife' \
  --expect-fail card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station \
  -- cargo test -p aib-plan --locked --lib card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station
# VF4 the author names themself reviewer and closes a record another seat opened (5d33a REVIEW-wsdisp-36dba2ae L1)
python3 -I scripts/knife.py crates/aib-plan/src/card.rs \
  '        let reviewer = reviewer.filter(|r| Some(*r) != author);' \
  '        let reviewer = reviewer.filter(|_| author.is_some() || true); // knife' \
  --expect-fail card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station \
  -- cargo test -p aib-plan --locked --lib card::tests::fail_opens_and_closes_a_record_and_never_moves_the_station
