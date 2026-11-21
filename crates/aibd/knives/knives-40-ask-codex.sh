#!/bin/bash
# N-ws-ask-codex knives AC1-AC3 (7ebca 11-19, 519d0 ORDER-allhands-webdev): a codex seat gets
# aibd's talk server (ask_person among its tools) as app-server settings, the seat token passed
# through by name, the tools never waiting for an approval nobody can give.
set -eo pipefail
ku() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seats::tests::$3"; }
kx() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_codex "$3"; }
# AC1 a codex seat opens without the talk server (it cannot ask_person or seat_send)
kx '        a.extend(codex_talk_args(exe, data));' \
   '        a.extend(Vec::<String>::new()); let _ = (exe, data); // knife' \
   a_codex_seat_is_spoken_to_in_its_protocol
# AC2 the seat token is not passed to the server (codex starts it with a short env)
ku '            "mcp_servers.aibd.env_vars={}",' \
   '            "mcp_servers.aibd.env_off={}",' a_codex_seat_gets_the_talk_tools_as_settings
# AC3 the tools wait for an approval (the seat runs approval_policy never: nobody answers)
ku '        "mcp_servers.aibd.default_tools_approval_mode=\"approve\"".to_owned(),' \
   '        "mcp_servers.aibd.default_tools_approval_mode=\"prompt\"".to_owned(),' \
   a_codex_seat_gets_the_talk_tools_as_settings
