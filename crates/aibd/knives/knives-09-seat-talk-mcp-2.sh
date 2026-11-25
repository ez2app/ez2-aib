#!/usr/bin/env bash
# S89-talk-1 knives TK-T1, TO1–TO3, SD1–SD2 (split from knives-09-seat-talk-mcp.sh so each stays <=900s).
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
tk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }
# TK-T1 a safe seat is not granted thread_send (0eed7 overnight 2943d806 M2: a called seat cannot answer)
tk crates/aibd/src/seats.rs \
   '    "mcp__aibd__thread_send",' \
   '    "mcp__aibd__seat_list", // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# TO1 a seat is not granted thread_open (519d0 常駐實跑 M 11-18: told to open a thread, it had no tool)
tk crates/aibd/src/seats.rs \
   '    "mcp__aibd__thread_open",' \
   '    "mcp__aibd__seat_list", // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# TO2 the MCP server does not answer thread_open
tk crates/aibd/src/ctl.rs \
   '                Some("thread_open") => thread_open(c, args),' \
   '                Some("thread_open_knife") => thread_open(c, args),' \
   a_seat_opens_a_thread_with_the_seats_it_names
# TO3 the seats named are not passed as members
tk crates/aibd/src/ctl.rs \
   '                Some(s) => Ok(json!({"seat": s, "role": "author"})),' \
   '                Some(_) => Ok(json!(null)), // knife' \
   a_seat_opens_a_thread_with_the_seats_it_names
# SD1 (0eed7 VERIFY-integ3 M1) a message whose rev moved since seat_inbox is closed anyway
tk crates/aibd/src/ctl.rs '    if let Some(s) = seen.filter(|s| *s != rev) {' '    if let Some(s) = seen.filter(|s| *s != rev && *s < 0) {' \
   seat_done_leaves_a_message_whose_rev_moved_since_it_was_seen
# SD2 (M1) more than twenty at once
tk crates/aibd/src/ctl.rs '        (None, Value::Array(a)) if !a.is_empty() && a.len() <= SEAT_DONE_MAX => a' '        (None, Value::Array(a)) if !a.is_empty() && a.len() <= SEAT_DONE_MAX * 2 => a' \
   seat_done_closes_several_and_leaves_the_one_that_moved
