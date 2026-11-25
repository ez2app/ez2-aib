#!/usr/bin/env bash
# aib-knives: os=unix
# aib-drive budget BG1–BG8 (a9136 11-17, S107-product slice 3b; DECISION-council-andyopen-519d0-1117 a1):
# the 「AI 推進」 button's budget is counted from drive_send events — 20 a day, 1 an hour for one card, the
# day in the set offset, tokens only added up. Each rule, broken, turns its own test red. Run on macOS (the
# header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust). Run from the repo
# root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gb() { python3 -I scripts/knife.py crates/aib-drive/src/budget.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# BG1 act's own sends count toward the button's budget
gb '        && v["action"] == PUSH_ACTION' \
   '        && v["action"].is_string() // knife' \
   only_todays_pushes_that_may_have_cost_count
# BG2 a held or unsent push counts
gb '        && matches!(v["outcome"].as_str(), Some("sent" | "queued" | "unknown"))' \
   '        && v["outcome"].is_string() // knife' \
   only_todays_pushes_that_may_have_cost_count
# BG3 yesterday counts
gb '        if t < start || t > now_ms {' \
   '        if t > now_ms { let _ = start; // knife' \
   only_todays_pushes_that_may_have_cost_count
# BG4 the day is the UTC day, not the set offset
gb '    (now_ms + off).div_euclid(86_400_000) * 86_400_000 - off' \
   '    { let _ = off; now_ms.div_euclid(86_400_000) * 86_400_000 } // knife' \
   the_day_is_the_day_in_the_offset
# BG5 a card pushed an hour ago may be pushed again at once
gb '    } else if card.is_some() && card_hour_used >= l.per_card_hour {' \
   '    } else if card.is_some() && card_hour_used > l.per_card_hour { // knife' \
   only_todays_pushes_that_may_have_cost_count
# BG6 a full day still lets a push go
gb '    let why = if day_used >= l.per_day {' \
   '    let why = if day_used > l.per_day { // knife' \
   a_full_day_stops_every_card
# BG7 tokens are not added up
gb '        day_tokens = day_tokens.saturating_add(v["tokens"].as_u64().unwrap_or(0));' \
   '        day_tokens = day_tokens.saturating_add(0); // knife' \
   only_todays_pushes_that_may_have_cost_count
# BG8 a limit of 0 in the settings is taken
gb '                .filter(|n| (1..=10_000).contains(n))' \
   '                .filter(|n| (0..=10_000).contains(n)) // knife' \
   settings_keep_defaults_for_missing_keys_and_refuse_bad_values
# BG9 an events file that cannot be read counts as no pushes (5d33a L1)
gb '            Err(e) => return Err(format!("{}: {e}", p.display())),' \
   '            Err(_) => {} // knife' \
   the_command_reads_the_events_and_the_settings
# BG10 settings that cannot be read fall back to the defaults (5d33a L1)
gb '        Ok(t) => parse_limits(&t),' \
   '        Ok(t) => Ok(parse_limits(&t).unwrap_or_default()), // knife' \
   the_command_reads_the_events_and_the_settings
