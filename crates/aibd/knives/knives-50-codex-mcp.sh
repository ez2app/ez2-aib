#!/bin/bash
# aib-knives: os=unix
# N-codex-user-mcp knives MC1-MC15 (7ebca 11-20; MC13-15 the list runs with the seat's env; 5d33a PREREAD-codexmcp M1 L1): a codex seat
# turns off every MCP server of the user's own, refuses one already named aibd, reads the list
# without aibd's own -c and never waits for it past MCP_LIST_WAIT.
set -eo pipefail
lk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
it() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seat_codex "$4"; }
# MC1 a server of the user's named aibd is taken as ours (5d33a M1)
lk crates/aibd/src/codex_wire.rs '        if name == "aibd" {' '        if name == "aibd" && false {' \
   a_codex_seat_turns_off_the_users_own_mcp_servers
# MC2 a server without the field is taken as off
lk crates/aibd/src/codex_wire.rs '        if r["enabled"] == Value::Bool(false) {' \
   '        if r["enabled"] != Value::Bool(true) {' \
   a_codex_seat_turns_off_the_users_own_mcp_servers
# MC3 a name -c cannot spell is passed on
lk crates/aibd/src/codex_wire.rs '        if !spelt {' '        if !spelt && false {' \
   a_codex_seat_turns_off_the_users_own_mcp_servers
# MC4 a list that is not an array is an empty one
lk crates/aibd/src/codex_wire.rs '    let rows = v.as_array().ok_or("codex_mcp_unlisted")?;' \
   '    let rows = v.as_array().cloned().unwrap_or_default(); let rows = &rows;' \
   a_codex_seat_turns_off_the_users_own_mcp_servers
# MC5 the seat starts with the servers left on
it crates/aibd/src/sessions.rs '            Ok(off) => req.argv.extend(off),' '            Ok(off) => drop(off),' \
   a_codex_seat_is_spoken_to_in_its_protocol
# MC6 an app-server seat is never listed
it crates/aibd/src/sessions.rs "        && req.argv.get(1).map(String::as_str) == Some(\"app-server\")" \
   "        && req.argv.get(1).map(String::as_str) == Some(\"no-such\")" \
   a_codex_seat_is_spoken_to_in_its_protocol
# MC7 aibd's own -c pairs are kept in the list's flags
lk crates/aibd/src/codex_wire.rs '                .is_some_and(|v| v.starts_with("mcp_servers.aibd."))' \
   '                .is_some_and(|v| v.starts_with("no-such."))' \
   the_list_is_read_without_aibds_own_server
# MC8 the seat's start lists with aibd's own server in (so ours reads as the user's)
it crates/aibd/src/sessions.rs '        let flags = crate::codex_wire::without_ours(&req.argv[2..]);' \
   '        let flags = req.argv[2..].to_vec();' \
   a_codex_seat_is_spoken_to_in_its_protocol
# MC9 a list that hangs holds the seat's start (5d33a L1)
lk crates/aibd/src/sessions.rs '    let got = rx.recv_timeout(MCP_LIST_WAIT);' \
   '    let got = rx.recv_timeout(MCP_LIST_WAIT * 6);' \
   a_codex_mcp_list_that_hangs_is_cut_and_read_as_nothing
# MC10 a .codex above the seat's folder inside the repo is not looked for (r2)
lk crates/aibd/src/codex_wire.rs '        if d == top {' '        if d == top || true {' \
   a_codex_folder_in_the_seats_repo_is_found
# MC11 the user's own settings folder is taken as a project's; theirs kept used (k12-e1d1b44a8: the old mutant was an unused-variable compile error, never a bite)
lk crates/aibd/src/codex_wire.rs '        if has(d, ".codex") && !theirs {' '        if has(d, ".codex") && (!theirs || true) {' \
   a_codex_folder_in_the_seats_repo_is_found
# MC12 a seat starts in a folder with codex project settings (r2); codex_home kept used (cf-2f895bb11: the old mutant was an unused-variable compile error, never a bite)
it crates/aibd/src/sessions.rs '            .and_then(|d| crate::codex_wire::folder_settings(d, codex_home.as_deref()))' \
   '            .and_then(|d| crate::codex_wire::folder_settings(d, codex_home.as_deref()).filter(|_| false))' \
   a_codex_seat_in_a_folder_with_codex_settings_is_not_started
# MC13 the list keeps the session marker (0eed7 VERIFY-codexcfg-65be8029)
lk crates/aibd/src/sessions.rs '        !allowlist::env_name_eq(&k.to_string_lossy(), crate::platform::session::SESSION_ENV)' \
   '        !k.is_empty() || allowlist::env_name_eq(&k.to_string_lossy(), crate::platform::session::SESSION_ENV)' \
   a_codex_list_runs_with_the_seats_own_environment
# MC14 the list loses the policy's names the seat gets
lk crates/aibd/src/sessions.rs '        pass: without(p.env.clone(), &req.withhold),' \
   '        pass: without(p.env.clone(), &p.env),' \
   a_codex_list_runs_with_the_seats_own_environment
# MC15 the list runs with aibd's own environment on top of the seat's
lk crates/aibd/src/sessions.rs '    c.env_clear()' '    c.env_remove("AIBD_KNIFE_NONE")' \
   a_codex_mcp_list_that_hangs_is_cut_and_read_as_nothing
