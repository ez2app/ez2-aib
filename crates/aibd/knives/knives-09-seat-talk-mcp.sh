#!/usr/bin/env bash
# S89-talk-1 knives TK1–TK5 (b4297 11-18, DECISION-council-seat-talk-519d0-1118 §驗收刀): a seat
# gets aibd's one MCP server and its two tools only, and `aibd mcp` sends as seat_send.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
tk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }
# TK1 a shell is allowed in place of a talk tool (B1dm-S3c: the list is four now)
tk crates/aibd/src/seats.rs \
   '    "mcp__aibd__seat_done",' \
   '    "Bash", // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# TK2 aibd mcp types to a seat as a person would (seat_input) instead of seat_send
tk crates/aibd/src/ctl.rs \
   '                            Some(json!({"type": "seat_send", "to": to, "text": text,' \
   '                            Some(json!({"type": "seat_input", "seat_id": to, "text": text, // knife' \
   the_mcp_server_has_two_tools_and_sends_with_the_seat_token
# TK3 the seat's aibd mcp is not told which aibd opened it (0eed7 VERIFY-talk1 M1: it reached another)
tk crates/aibd/src/seats.rs \
   '            "type": "stdio", "command": aibd_exe, "args": ["mcp", "--data-dir", data_dir]' \
   '            "type": "stdio", "command": aibd_exe, "args": ["mcp", "--data-dir", data_dir.get(..0).unwrap_or("")] // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# TK4 the seat's MCP config is an ordinary file others can read (DECISION seat-talk: 0600, aibd only)
tk crates/aibd/src/seats.rs \
   '    crate::platform::write_private_file(&p, talk_config(aibd_exe, data_dir).as_bytes())?;' \
   '    std::fs::write(&p, talk_config(aibd_exe, data_dir).as_bytes())?; // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# TK5 the MCP seat_send sends no idempotency key, so a request sent twice is sent twice (5d33a)
tk crates/aibd/src/ctl.rs \
   '                                "idempotency_key": format!("mcp-{}-{}", std::process::id(), id)})),' \
   '                                "idempotency_key": Value::Null})), // knife' \
   the_mcp_server_has_two_tools_and_sends_with_the_seat_token
# TK6 seat_done closes a message it has not read (B1dm-S3c: the core refuses; the tool must read first)
tk crates/aibd/src/ctl.rs \
   '    if m["status"] != "read" {' \
   '    if m["status"] != "read" && m["status"] == "never" { // knife' \
   seat_done_reads_then_closes_at_the_revision_it_has_now
