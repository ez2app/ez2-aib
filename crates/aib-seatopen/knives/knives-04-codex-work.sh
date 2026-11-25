#!/bin/bash
# aib-knives: os=macos   # 90856 11-26: on ubuntu a knife here missed (a_codex_work_seat_runs_in_the_seat_profile; #895 run 45610010326)
# N-ws-codex-work knives CW1-CW8 (7ebca 11-20, FEASIBILITY-codex-work-seat-7ebca-1120 C): a codex
# work seat runs in the profile `seat` (no codex sandbox_mode), network off, its git reads no
# user config, and the profile writes only the seat's folder and its .git.
set -eo pipefail
cw() { python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "$3"; }
# CW1 no profile named: codex would run with its own default sandbox
cw '    "default_permissions=\"seat\"", // the profile codex_work_args fills' \
   '    "approval_policy=\"never\"",' \
   a_codex_work_seat_runs_in_the_seat_profile
# CW2 the network left on
cw '    "permissions.seat.network.enabled=false", // no network' \
   '    "permissions.seat.network.enabled=true",' \
   a_codex_work_seat_runs_in_the_seat_profile
# CW3 the seat's git reads the user's config again
cw '    "shell_environment_policy.set={GIT_CONFIG_GLOBAL=\"/dev/null\", GIT_CONFIG_NOSYSTEM=\"1\"}",' \
   '    "features.sleep_tool=false",' \
   a_codex_work_seat_runs_in_the_seat_profile
# CW4 a work seat started with the safe list (codex's read-only sandbox, no shell)
cw '                CODEX_WORK_OFF' \
   '                { let _ = CODEX_WORK_OFF; CODEX_SAFE_OFF }' \
   a_codex_work_seat_runs_in_the_seat_profile
# CW5 the seat's .git only read (no commit) / the profile's writes changed
cw '    fs.push(format!("{}={}", q(&git), q("write")));' \
   '    fs.push(format!("{}={}", q(&git), q("read")));' \
   a_codex_work_seat_writes_only_its_folder
# CW6 a tool folder that is not there is named anyway (codex's sandbox helper fails, exit 71)
cw '    for d in CODEX_WORK_READ.iter().filter(|d| exists(d)) {' \
   '    for d in CODEX_WORK_READ.iter().filter(|d| exists(d) || true) {' \
   a_codex_work_seat_writes_only_its_folder
# CW7 a quote in the folder's path ends the TOML string
cw "            '\"' => o.push_str(\"\\\\\\\"\")," \
   "            '\"' => o.push('\"')," \
   a_codex_work_seat_writes_only_its_folder
# CW8 the shared /private/tmp left readable and writable (0eed7 VERIFY-codexwork-2f27b914 L1)
cw '    fs.push(format!("{}={}", q(CODEX_WORK_NO_TMP), q("none")));' \
   '    fs.push(format!("{}={}", q(CODEX_WORK_NO_TMP), q("read")));' \
   a_codex_work_seat_writes_only_its_folder
