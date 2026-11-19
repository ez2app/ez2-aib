#!/bin/bash
# N-codex-user-mcp knives MC1-MC9 (7ebca 11-20; 5d33a PREREAD-codexmcp M1 L1): a codex seat
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
