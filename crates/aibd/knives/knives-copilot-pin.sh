#!/usr/bin/env bash
# Copilot pinned to the measured version (3e53b 11-26, REVIEW-copilot-08de5-3e53b-be535949 end): aibd writes COPILOT_AUTO_UPDATE=false and COPILOT_CLI_DIST_DIR=<the pinned package in copilot's cache> into the seat's env; only those two exact names and COPILOT_HOME pass; the gate runs --version with that env and refuses another version or a package gone. Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# cp-prefix (a name that only starts like the pin's passes: the list read as prefixes)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    (up.starts_with("COPILOT_") && !OWN_ENV.contains(&up.as_str()))' \
  '    (up.starts_with("COPILOT_") && !OWN_ENV.iter().any(|o| up.starts_with(o)))' \
  --expect-fail 'copilot_wire::tests::only_the_exact_pin_names_pass' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::only_the_exact_pin_names_pass'
# cp-prefix-cli (every COPILOT_CLI_* passes, the feature flags among them)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    (up.starts_with("COPILOT_") && !OWN_ENV.contains(&up.as_str()))' \
  '    (up.starts_with("COPILOT_") && !OWN_ENV.contains(&up.as_str()) && !up.starts_with("COPILOT_CLI_"))' \
  --expect-fail 'copilot_wire::tests::only_the_exact_pin_names_pass' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::only_the_exact_pin_names_pass'
# cp-gate-pin (the gate does not check the pin: an unpinned seat opens)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    crate::copilot_wire::pinned(env).map_err(|why| format!("{why} (copilot_pin)"))?;' \
  '    let _ = crate::copilot_wire::pinned;' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# cp-version (another version still opens: the gate reads --version and lets it by)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if !aib_seatopen::copilot_version_ok(&printed) {' \
  '    if !aib_seatopen::copilot_version_ok(&printed) && !printed.contains("Copilot") {' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# cp-update-unchecked (AUTO_UPDATE not written still counts as pinned)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    if var(PIN_UPDATE.0).as_deref() != Some(PIN_UPDATE.1) {' \
  '    if var(PIN_UPDATE.0).is_some_and(|v| v != PIN_UPDATE.1) {' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# cp-update-unwritten (aibd does not write AUTO_UPDATE into the seat's env)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    req.env.push((update.to_owned(), off.to_owned()));' \
  '    let _ = (update, off);' \
  --expect-fail 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it'
# cp-dist-unwritten (aibd does not name the pinned package: copilot runs whatever it finds)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '            .push((crate::copilot_wire::PIN_DIST.to_owned(), p.to_owned()));' \
  '            .push(("COPILOT_PINNED_NOT".to_owned(), p.to_owned()));' \
  --expect-fail 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it'
# cp-cleared (a pinned package gone from copilot's cache still opens)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    if !Path::new(&dir).is_dir() {' \
  '    if dir.is_empty() {' \
  --expect-fail 'copilot_wire::tests::a_seat_runs_only_pinned' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_seat_runs_only_pinned'
# cp-win-sep (Windows gets the Mac separator)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  "        PinOs::Windows => (var(\"LOCALAPPDATA\")?, '\\\\', [\"copilot\", \"pkg\"].as_slice())," \
  "        PinOs::Windows => (var(\"LOCALAPPDATA\")?, '/', [\"copilot\", \"pkg\"].as_slice())," \
  --expect-fail 'copilot_wire::tests::the_pinned_package_is_found_on_mac_and_windows' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::the_pinned_package_is_found_on_mac_and_windows'
# cp-spawn-keeps-pin (the seat's spawn env keeps the home but loses the pin: the turn runs unpinned)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        .filter(|(k, _)| crate::copilot_wire::OWN_ENV.contains(&k.as_str()))' \
  '        .filter(|(k, _)| k == "COPILOT_HOME")' \
  --expect-fail 'a_copilot_seat_runs_in_the_home_aibd_made_for_it' \
  -- cargo test -p aibd --locked --test seat_copilot_home a_copilot_seat_runs_in_the_home_aibd_made_for_it
# cp-twice (a pin name given twice in another case is read as the first: copilot may read the other)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '            (Some(v), None) => Some(v),' \
  '            (Some(v), _) => Some(v),' \
  --expect-fail 'copilot_wire::tests::a_seat_runs_only_pinned' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_seat_runs_only_pinned'
# cp-login-update (the copilot login may download an update into the user's cache)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    env.push((PIN_UPDATE.0.into(), PIN_UPDATE.1.into()));' \
  '    env.push((PIN_UPDATE.0.into(), "true".into()));' \
  --expect-fail 'copilot_wire::tests::a_seat_runs_only_pinned' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_seat_runs_only_pinned'
# cp-login-call (the login route never pins)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '    if crate::copilot_wire::is_copilot(provider) {' \
  '    if crate::copilot_wire::is_copilot(provider) && provider.is_empty() {' \
  --expect-fail 'provider_login::tests::a_copilot_login_is_pinned' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::a_copilot_login_is_pinned'
