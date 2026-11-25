#!/usr/bin/env bash
# Grok safe seat (08de5 11-25, ebab8 q_cd554b24 conditions 1-3; TAKEOVER-brands-08de5-1125 §1): checked at the gate (flags, version, the user's own set-up), every turn through aibd's spawn with its stop reported, the prompt file new / private / removed. The turn shell is shared with copilot since 08de5 11-25 (crates/aibd/src/turn_wire.rs). Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# g-flag-dup (a required flag missing or given twice still opens)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '            _ => false,' \
  '            _ => true,' \
  --expect-fail 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused'
# g-flag-value (a required flag with another value opens)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '            ([i], Some(v)) => words[*i] == *flag && words.get(i + 1).is_some_and(|x| x == v),' \
  '            ([i], Some(v)) => words[*i] == *flag && words.get(i + 1).is_some_and(|x| !x.is_empty() || x == v),' \
  --expect-fail 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused'
# g-forbid (a word that gives a tool back opens)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '        if forbidden.contains(&name(w).as_str()) {' \
  '        if forbidden.contains(&name(w).as_str()) && w.is_empty() {' \
  --expect-fail 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::a_grok_safe_seat_missing_any_flag_is_refused'
# g-version (another 1.0.7x version is taken)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '            .strip_prefix("grok 1.0.70")' \
  '            .strip_prefix("grok 1.0.7").map(|r| r.trim_start_matches(char::is_numeric))' \
  --expect-fail 'pipe::tests::only_the_measured_grok_version_starts' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::only_the_measured_grok_version_starts'
# g-gate-flags (the gate does not check the safe flags)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if let Some(why) = aib_seatopen::grok_safe_refusal(argv) {' \
  '    if let Some(why) = aib_seatopen::grok_safe_refusal(argv).filter(|_| false) {' \
  --expect-fail 'sessions::tests::a_grok_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_grok_seat_is_checked_at_the_gate'
# g-gate-version (the gate takes any version)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if !aib_seatopen::grok_version_ok(&printed) {' \
  '    if !aib_seatopen::grok_version_ok(&printed) && printed.is_empty() {' \
  --expect-fail 'sessions::tests::a_grok_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_grok_seat_is_checked_at_the_gate'
# g-gate-setup (the gate ignores the user's own MCP / hooks / plugins)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if let Err(why) = crate::grok_wire::nothing_loaded(&listed) {' \
  '    if let Err(why) = crate::grok_wire::nothing_loaded(&listed).or_else(|_| Ok::<(), String>(())) {' \
  --expect-fail 'sessions::tests::a_grok_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_grok_seat_is_checked_at_the_gate'
# g-plugins (a plugin that does not say enabled is let through)
python3 -I scripts/knife.py crates/aibd/src/grok_wire.rs \
  '        .filter(|p| p.get("enabled").and_then(Value::as_bool) != Some(false))' \
  '        .filter(|p| p.get("enabled").and_then(Value::as_bool) == Some(true))' \
  --expect-fail 'grok_wire::tests::a_grok_that_would_load_the_users_own_set_up_is_refused' \
  -- cargo test -p aibd --locked --lib 'grok_wire::tests::a_grok_that_would_load_the_users_own_set_up_is_refused'
# g-spawn (a turn started around aibd's own spawn)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '    let mut run = (turns.spawn)(&turn_spec, turns.lineage.clone())' \
  '    let mut run = crate::platform::default_spawn()(&turn_spec, turns.lineage.clone())' \
  --expect-fail 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported'
# g-stop (a stop says clean whatever the turn left)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '        turn.or(left).unwrap_or_else(idle_stop)' \
  '        turn.or(left).map(|_| idle_stop()).unwrap_or_else(idle_stop)' \
  --expect-fail 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported'
# g-left (a finished turn's unclean stop forgotten)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '            if !matches!(s.status, StopStatus::Clean) {' \
  '            if !matches!(s.status, StopStatus::Clean) && s.survivors.is_empty() {' \
  --expect-fail 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported'
# g-prompt-left (the prompt file left after the turn)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '                            (args, Some(PromptGuard(path)), None)' \
  '                            (args, Some(PromptGuard(path.with_extension("x"))), None)' \
  --expect-fail 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported'
# g-prompt-excl (a file already at the name written over)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '    opts.write(true).create_new(true);' \
  '    opts.write(true).create(true).truncate(true);' \
  --expect-fail 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link'
# g-prompt-mode (the prompt file readable by others)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '        opts.mode(0o600).custom_flags(nix::libc::O_NOFOLLOW);' \
  '        opts.mode(0o644).custom_flags(nix::libc::O_NOFOLLOW);' \
  --expect-fail 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link'
# g-prompt-dir (the prompt folder not owner only)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '    crate::platform::create_private_dir(dir)?;' \
  '    std::fs::create_dir_all(dir)?;' \
  --expect-fail 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_prompt_file_is_new_private_and_never_written_through_a_link'
# g-turn-check (a turn started without the gate run again: what was added after the seat opened is loaded; 3e53b M1)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '    if let Err(why) = (turns.check)(&turn_spec) {' \
  '    if let Err(why) = (turns.check)(&turn_spec).or(Ok::<(), String>(())) {' \
  --expect-fail 'turn_wire::tests::a_turn_after_the_set_up_changed_is_refused_and_not_started' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_turn_after_the_set_up_changed_is_refused_and_not_started'
# g-turn-gate (the per-turn check passes whatever the turn would load; 3e53b M1)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    grok_gate(real, &spec.argv, Some(&spec.cwd), &env)' \
  '    grok_gate(real, &spec.argv, Some(&spec.cwd), &env).or(Ok(()))' \
  --expect-fail 'sessions::tests::a_grok_turn_is_checked_again_before_it_starts' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_grok_turn_is_checked_again_before_it_starts'
# g-opener (the user's words first in the prompt file: a slash command there runs on this machine; 3e53b M2)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '                let prompt = prompt_text(&turn_prompt(persona.as_deref(), &text, turn == 0));' \
  '                let prompt = turn_prompt(persona.as_deref(), &text, turn == 0);' \
  --expect-fail 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::every_turn_goes_through_aibds_spawn_and_its_stop_is_reported'
# g-unknown (a key the measured listing did not have, not empty, opens; 3e53b L2)
python3 -I scripts/knife.py crates/aibd/src/grok_wire.rs \
  '        if !INSPECT_KEYS.contains(&key.as_str()) && !empty {' \
  '        if !INSPECT_KEYS.contains(&key.as_str()) && !empty && key.is_empty() {' \
  --expect-fail 'grok_wire::tests::a_grok_that_would_load_the_users_own_set_up_is_refused' \
  -- cargo test -p aibd --locked --lib 'grok_wire::tests::a_grok_that_would_load_the_users_own_set_up_is_refused'
# g-x-brand (the brand line no longer says grok's safe seat can still search X; Andy 11-25 (a))
python3 -I scripts/knife.py apps/web/dist/ws.js \
  '  const GROK_X = '\''Grok 安全席仍可搜尋 X(關不掉)'\'';' \
  '  const GROK_X = '\'''\'';' \
  --expect-fail 'the_page_is_checked_without_a_browser' \
  -- cargo test -p aibd --locked --test page_static 'the_page_is_checked_without_a_browser'
# g-x-open (the line before the seat opens leaves it out)
python3 -I scripts/knife.py apps/web/dist/ws.js \
  'pv.key === '\''grok'\'' ? '\'' · '\'' + GROK_X : '\'''\''' \
  'pv.key === '\''grok'\'' ? '\'''\'' : '\'''\''' \
  --expect-fail 'the_page_is_checked_without_a_browser' \
  -- cargo test -p aibd --locked --test page_static 'the_page_is_checked_without_a_browser'
