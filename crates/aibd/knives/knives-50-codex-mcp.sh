#!/bin/bash
# N-codex-user-mcp knives MC1-MC6 (7ebca 11-20): a codex seat turns off every MCP server of the
# user's own but aibd's (a server is a program codex starts outside the seat's sandbox).
set -eo pipefail
lk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
it() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seat_codex "$4"; }
# MC1 every server is taken as aibd's and left on
lk crates/aibd/src/codex_wire.rs '        if name == "aibd" || r["enabled"] == Value::Bool(false) {' \
   '        if !name.is_empty() || r["enabled"] == Value::Bool(false) {' \
   a_codex_seat_turns_off_the_users_own_mcp_servers
# MC2 a server without the field is taken as off
lk crates/aibd/src/codex_wire.rs '        if name == "aibd" || r["enabled"] == Value::Bool(false) {' \
   '        if name == "aibd" || r["enabled"] != Value::Bool(true) {' \
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
