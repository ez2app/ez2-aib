#!/usr/bin/env bash
# Copilot safe seat (08de5 11-25, ebab8 q_cd554b24 conditions 1, 2, 4; TAKEOVER-brands-08de5-1125 §2): checked at the gate (safe flags, version read, every listed MCP server turned off by name), the prompt on the turn's stdin and never in argv, turn 2 resuming turn 1. Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# c-required (a required flag left out of the copilot list still opens)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '    safe_refusal(argv, COPILOT_SAFE_REQUIRED, COPILOT_SAFE_FORBIDDEN)' \
  '    safe_refusal(argv, &COPILOT_SAFE_REQUIRED[1..], COPILOT_SAFE_FORBIDDEN)' \
  --expect-fail 'pipe::tests::a_copilot_safe_seat_missing_any_flag_is_refused' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::a_copilot_safe_seat_missing_any_flag_is_refused'
# c-forbid (a word that gives a tool back or puts the prompt in argv opens)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '    safe_refusal(argv, COPILOT_SAFE_REQUIRED, COPILOT_SAFE_FORBIDDEN)' \
  '    safe_refusal(argv, COPILOT_SAFE_REQUIRED, &[])' \
  --expect-fail 'pipe::tests::a_copilot_safe_seat_missing_any_flag_is_refused' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::a_copilot_safe_seat_missing_any_flag_is_refused'
# c-version (another 1.0.11x version is taken)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '            .is_some_and(|r| r.is_empty() || r == ".")' \
  '            .is_some_and(|r| r.is_empty() || r.ends_with('"'"'.'"'"'))' \
  --expect-fail 'pipe::tests::only_the_read_copilot_version_starts' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::only_the_read_copilot_version_starts'
# c-gate-flags (the gate does not check the safe flags)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if let Some(why) = aib_seatopen::copilot_safe_refusal(argv) {' \
  '    if let Some(why) = aib_seatopen::copilot_safe_refusal(argv).filter(|_| false) {' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-gate-version (the gate takes a version not read)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    if !aib_seatopen::copilot_version_ok(&printed) {' \
  '    if !aib_seatopen::copilot_version_ok(&printed) && printed.is_empty() {' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-gate-mcp (the listed MCP servers are not turned off)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '        req.argv.extend(off_words);' \
  '        req.argv.extend(off_words.into_iter().take(0));' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-gate-mcp-unread (an MCP list not read starts the seat)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    crate::copilot_wire::mcp_off(&listed).map_err(|why| format!("{why} (copilot_mcp)"))' \
  '    crate::copilot_wire::mcp_off(&listed).or_else(|_| Ok(Vec::new()))' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-mcp-each (one listed MCP server left on)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    for name in servers.keys() {' \
  '    for name in servers.keys().skip(1) {' \
  --expect-fail 'copilot_wire::tests::every_listed_mcp_server_is_turned_off' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::every_listed_mcp_server_is_turned_off'
# c-mcp-plain (an MCP name that is not plain goes in the words)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        if !plain {' \
  '        if !plain && name.is_empty() {' \
  --expect-fail 'copilot_wire::tests::every_listed_mcp_server_is_turned_off' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::every_listed_mcp_server_is_turned_off'
# c-mcp-unread (an MCP list not read reads as none)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    let v: Value = serde_json::from_str(listed.trim())' \
  '    let v: Value = serde_json::from_str::<Value>(listed.trim()).or_else(|_| serde_json::from_str(r#"{"mcpServers":{}}"#))' \
  --expect-fail 'copilot_wire::tests::every_listed_mcp_server_is_turned_off' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::every_listed_mcp_server_is_turned_off'
# c-stdin-brand (copilot takes its prompt from a file in its words)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        PromptBy::Stdin // the prompt never in argv (condition 4)' \
  '        PromptBy::File // the prompt never in argv (condition 4)' \
  --expect-fail 'copilot_wire::tests::the_copilot_prompt_goes_on_stdin_never_in_its_words' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::the_copilot_prompt_goes_on_stdin_never_in_its_words'
# c-stdin-turn (a stdin-prompt turn gets no stdin)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '        input: stdin_prompt.is_some(),' \
  '        input: false,' \
  --expect-fail 'turn_wire::tests::a_stdin_prompt_never_goes_in_argv' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_stdin_prompt_never_goes_in_argv'
# c-stdin-write (the prompt not written on the turn's stdin)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '        let _ = w.write_all(prompt.as_bytes());' \
  '        let _ = w.write_all(&prompt.as_bytes()[..0]);' \
  --expect-fail 'turn_wire::tests::a_stdin_prompt_never_goes_in_argv' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_stdin_prompt_never_goes_in_argv'
# c-resume (turn 2 does not resume turn 1's session)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        out.push(format!("--resume={sid}"));' \
  '        out.push(format!("--session-id={sid}"));' \
  --expect-fail 'copilot_wire::tests::turn_args_adds_resume_only_when_session_id_is_present' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::turn_args_adds_resume_only_when_session_id_is_present'
# c-model (no model given: the user's config default is used, which copilot may not serve)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
  '                model.unwrap_or(COPILOT_ANY_MODEL).to_owned(),' \
  '                model.unwrap_or("grok-4.6").to_owned(),' \
  --expect-fail 'pipe::tests::copilot_safe_has_all_safety_flags_and_refuses_other_modes' \
  -- cargo test -p aib-seatopen --locked --lib 'pipe::tests::copilot_safe_has_all_safety_flags_and_refuses_other_modes'
# c-turn-mcp (an MCP server added after the seat opened is loaded on the next turn; 3e53b M1)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '        if !spec.argv.windows(2).any(|w| w == pair) {' \
  '        if !spec.argv.windows(2).any(|w| w == pair) && pair.is_empty() {' \
  --expect-fail 'sessions::tests::a_copilot_turn_is_checked_again_before_it_starts' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_turn_is_checked_again_before_it_starts'
# c-mcp-dash (an MCP name starting with - goes after --disable-mcp-server and is read as a flag)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  "            && !name.starts_with('-')" \
  "            && !name.starts_with('=')" \
  --expect-fail 'copilot_wire::tests::every_listed_mcp_server_is_turned_off' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::every_listed_mcp_server_is_turned_off'
# c-hook-gate (the seat gate does not look at what copilot runs by itself; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '    crate::copilot_wire::nothing_runs(env, &here)' \
  '    Ok::<(), String>(())' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-hook-dir (a hook file under COPILOT_HOME/hooks opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    for dir in ["hooks", "installed-plugins"] {' \
  '    for dir in ["installed-plugins"] {' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-plugin-dir (an installed plugin opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    for dir in ["hooks", "installed-plugins"] {' \
  '    for dir in ["hooks"] {' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-hook-settings (hooks written in settings / config open; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        if some(v.get("hooks")) {' \
  '        if some(v.get("hooks_")) {' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-plugin-on (a plugin turned on in the settings opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '            .is_some_and(|o| o.values().any(|x| x != &Value::Bool(false)))' \
  '            .is_some_and(|o| o.values().any(|x| x == &Value::Null))' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-trusted (a seat folder below a trusted one opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '                if cwd.starts_with(f) {' \
  '                if cwd == Path::new(f) {' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-copilot-home (COPILOT_HOME not followed; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        (Some(c), _) if !c.is_empty() => std::path::PathBuf::from(c),' \
  '        (Some(c), _) if c.is_empty() => std::path::PathBuf::from(c),' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-allow-all (COPILOT_ALLOW_ALL=yes opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '        !(v.is_empty() || ["false", "0", "no", "off", "n"].contains(&v.as_str()))' \
  '        v == "true"' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-config-need (no config read opens; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    for (name, need) in [("settings.json", false), ("config.json", true)] {' \
  '    for (name, need) in [("settings.json", false), ("config.json", false)] {' \
  --expect-fail 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_set_up_that_runs_anything_by_itself_is_refused'
# c-sid-keep (a session id that is not a UUID goes into the next turn; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '            if let Some(s) = sid.filter(|s| uuid_shaped(s)) {' \
  '            if let Some(s) = sid {' \
  --expect-fail 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept'
# c-sid-err (a session id that is not a UUID is not said; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '            let is_err = exit_code != 0 || sid_bad;' \
  '            let is_err = exit_code != 0;' \
  --expect-fail 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept'
# c-uuid-hex (any characters of the UUID lengths pass; 3e53b REVIEW-copilot M1 / L1)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '            .all(|(p, n)| p.len() == n && p.bytes().all(|b| b.is_ascii_hexdigit()))' \
  '            .all(|(p, n)| p.len() == n)' \
  --expect-fail 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_session_id_that_is_not_a_uuid_is_not_kept'
# c-login-users (an empty loggedInUsers reads as logged in; ebab8 q_17279e54)
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '                .is_some_and(|a| !a.is_empty())' \
  '                .is_some()' \
  --expect-fail 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in' \
  -- cargo test -p aibd --locked --lib 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in'
# c-login-comment (copilot config comment lines not taken out: a real login reads as none; ebab8 q_17279e54)
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '                .filter(|l| !l.trim_start().starts_with("//"))' \
  '                .filter(|_| true)' \
  --expect-fail 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in' \
  -- cargo test -p aibd --locked --lib 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in'
# c-login-keychain (a keychain item not there reads as a stored token (macOS); ebab8 q_17279e54)
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '        code == Some(0)' \
  '        code.is_some()' \
  --expect-fail 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in' \
  -- cargo test -p aibd --locked --lib 'workspace::tests::a_brand_without_a_login_under_the_seat_home_is_not_logged_in'
# c-home-keys (the seat home takes the whole user config (hooks, trusted folders, plugins); DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    let mut cfg = json!({"loggedInUsers": users});' \
  '    let mut cfg = v.clone();' \
  --expect-fail 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only'
# c-home-host (a signed-in user on another host opens; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    v.get("host").and_then(Value::as_str) == Some("https://github.com") && login_ok' \
  '    login_ok' \
  --expect-fail 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only'
# c-env-dropped (a COPILOT_* name other than the home reaches the seat; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    (up.starts_with("COPILOT_") && up != "COPILOT_HOME") || DROPPED_ENV.contains(&up.as_str())' \
  '    DROPPED_ENV.contains(&up.as_str())' \
  --expect-fail 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only'
# c-env-turn (a name that moves copilot set-up does not refuse the turn; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    if let Some((k, _)) = env.iter().find(|(k, _)| env_dropped(&k.to_string_lossy())) {' \
  '    if let Some((k, _)) = env.iter().find(|(_, _)| false) {' \
  --expect-fail 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only' \
  -- cargo test -p aibd --locked --lib 'copilot_wire::tests::a_copilot_seat_home_holds_the_signed_in_user_only'
# c-made-shape (a folder of the same shape outside aibd's homes is taken as aibd's; c2f0b whole path)
python3 -I scripts/knife.py crates/aibd/src/copilot_wire.rs \
  '    std::fs::canonicalize(home).is_ok_and(|real| real.parent() == Some(root.as_path()))' \
  '    std::fs::canonicalize(home).is_ok() || root.as_os_str().is_empty()' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-seat-env (the request keeps a name that moves copilot set-up; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '        !crate::copilot_wire::env_dropped(k) && !k.eq_ignore_ascii_case("COPILOT_HOME")' \
  '        !k.eq_ignore_ascii_case("COPILOT_HOME")' \
  --expect-fail 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it'
# c-seat-withhold (a policy name that moves copilot set-up is passed on; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '        .filter(|n| crate::copilot_wire::env_dropped(n) || n.eq_ignore_ascii_case("COPILOT_HOME"))' \
  '        .filter(|n| n.eq_ignore_ascii_case("COPILOT_HOME"))' \
  --expect-fail 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_gets_its_own_home_and_nothing_that_moves_it'
# c-own-home (any COPILOT_HOME passes the gate without a policy name; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '            .filter(|(k, v)| !own_home(k, v))' \
  '            .filter(|(_, _)| true)' \
  --expect-fail 'sessions::tests::a_copilot_seat_is_checked_at_the_gate' \
  -- cargo test -p aibd --locked --lib 'sessions::tests::a_copilot_seat_is_checked_at_the_gate'
# c-own-dir (the seat home is left when the seat ends; DESIGN-copilot-seat-home / c2f0b PRE-READ)
python3 -I scripts/knife.py crates/aibd/src/turn_wire.rs \
  '                let _ = std::fs::remove_dir_all(d);' \
  '                let _ = d;' \
  --expect-fail 'turn_wire::tests::a_turn_after_the_set_up_changed_is_refused_and_not_started' \
  -- cargo test -p aibd --locked --lib 'turn_wire::tests::a_turn_after_the_set_up_changed_is_refused_and_not_started'
