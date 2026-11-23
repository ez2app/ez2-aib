#!/usr/bin/env bash
# The page's time-saved act (PLAN-skipstop-master F2, c5d88 11-23): each rule loosened must
# turn its own test red. From the repo root; -eo pipefail like GitHub's shell.
set -euo pipefail
ts() { python3 -I scripts/knife.py crates/aibd/src/time_saved.rs "$1" "$2" --expect-fail "time_saved::tests::$3" \
        -- cargo test -p aibd --locked --lib "time_saved::tests::$3"; }
# TSK1 an answer of another format is read as this one
ts '    if o.get("v").and_then(Value::as_u64) != Some(FORMAT_V) {' '    if o.get("v").and_then(Value::as_u64).is_none() {' the_boxes_are_built_from_the_answer_s_numbers_and_only_from_a_whole_answer
# TSK2 a number that is not there is shown as 0 (a board of zeros is a claim)
ts '                "clock_ms": num(b, "clock_ms")?,' '                "clock_ms": num(b, "clock_ms").unwrap_or(0),' the_boxes_are_built_from_the_answer_s_numbers_and_only_from_a_whole_answer
# TSK3 a clock no place has is passed to the program
ts '        .filter(|m| (-720..=840).contains(m))' '        .filter(|m| (-7200..=8400).contains(m))' the_clock_is_what_the_page_says_when_a_clock_can_be_that
# TSK4 a program that ended badly is read as an answer
ts '    if rc != Some(0) {' '    if rc.is_none() {' with_a_plugin::the_plugin_s_numbers_reach_the_page_only_from_the_installed_executable
# TSK5 an answer that is not understood gives an empty ok
ts '        Err(_) => json!({ "state": "bad_answer" }),' '        Err(_) => json!({ "state": "ok" }),' with_a_plugin::the_plugin_s_numbers_reach_the_page_only_from_the_installed_executable
# TSK6 the program is not asked on the page's clock
ts '    c.args(["saved", "--json", "--utc-offset", &offset.to_string()])' '    c.args(["saved", "--json", "--utc-offset", "0"])' with_a_plugin::the_program_is_asked_for_saved_json_on_the_page_s_clock
# TSK7 a plugin switched off is said to be broken, not off
ts '        aib_plugin::No::Off => ("off", ""),' '        aib_plugin::No::Off => ("not_runnable", "off"),' with_a_plugin::the_plugin_s_numbers_reach_the_page_only_from_the_installed_executable
# TSK8 an old answer, or one to another question, is given again
ts '        Some((at, o, v)) if *o == offset && now.saturating_duration_since(*at) < KEEP => {' '        Some((at, o, v)) if *o == offset || now.saturating_duration_since(*at) < KEEP => {' an_answer_is_given_again_only_to_the_same_question_and_only_while_it_is_new
