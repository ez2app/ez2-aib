#!/usr/bin/env bash
# aib-knives: os=unix
# aib-seatopen knives WT1–WT4 (a9136 11-19, N-ws-live-in W1, DESIGN-seat-work-tier-a9136-1119): a work claude seat
# gets a fixed tool list, confined to its folder, asking nobody. Each rule, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form)
wt() { python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "pipe::tests::$3"; }
wl() { python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "pipe::tests::$3"; }
# WT1 the file tools are no longer confined to the seat's folder
wt '                    "--restricted",' '                    "--verbose", // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT2 what is off the list is asked of someone instead of refused
wt '                    "none",' '                    "host", // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT3 a work seat gets no tool list at all (a safe seat's work args are skipped)
wt '            if mode == "work" {' '            if mode == "work-not" { // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT4 a Bash rule beyond reading slips onto the list
wl '    "Bash(pwd)",' '    "Bash(rm *)", // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT5 a free-argument rule is listed that WORK_FREE_ARGS does not name (5d33a M2 on ecf54f91d).
# It used to cut in `Bash(git diff *)`, but G4a (fdd1d54ec9, DECISION-council-cutover-519d0-1121
# §2) lists that rule on purpose, so that cut only added a duplicate and missed (CI #872 run
# 44928976366); the rule M2 kept is "every free-argument rule is a named one", cut here.
wl '    "Bash(git diff --stat)",' '    "Bash(git diff --stat *)", // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT6 a work seat's commands run outside claude's sandbox (condition 5)
wt '                    work_settings_for(crate::home_uid(), cfg!(unix))?,' '                    { let _ = work_settings_for; String::from("{}") }, // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT7 a work seat loads the project / local settings its folder may hold (a hook it wrote)
wt '                    a[i + 1] = crate::WORK_SETTING_SOURCES.to_owned();' '                    a[i + 1] = String::from("project,local"); // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT8 the account's temp folders are readable
wl 'pub const WORK_SETTINGS: &str = r#"{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false,"filesystem":{"denyRead":["/var/folders"]}},"permissions":{"blockReadsOutsideWorkingDirectories":true}}"#;' 'pub const WORK_SETTINGS: &str = r#"{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false},"permissions":{"blockReadsOutsideWorkingDirectories":true}}"#; // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT9 claude's own temp folder (other sessions' outputs) is readable
wl '            &format!(r#""denyRead":["/var/folders","/private/tmp/claude-{u}"]"#),' '            &format!(r#""denyRead":["/var/folders"]{u}"#), // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT10 the uid is not known on unix and the seat opens anyway, temp folder readable
wt '    if uid.is_none() && unix {' '    if uid.is_none() && unix && false { // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
# WT11 the argv leaves claude's temp folder out (the uid dropped)
wt '                    work_settings_for(crate::home_uid(), cfg!(unix))?,' '                    { let _ = work_settings_for; crate::work_settings(None) }, // knife' \
   a_work_seat_has_its_listed_tools_confined_and_asks_nobody
