#!/usr/bin/env bash
# S57 knives (b433b 11-14): Windows load is read by aibd natively, no PowerShell. Each knife
# takes one check away and must turn its own test red. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors reads every
# scripts/knife.py call and only understands plain text and $1..$9.
kl() { python3 -I scripts/knife.py crates/aibd/src/load.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3" && echo "BITE $3"; }
kc() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3" && echo "BITE $3"; }
kg() { python3 -I scripts/knife.py crates/aibd/src/guard.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test guard "$3" && echo "BITE $3"; }
kh() { python3 -I scripts/knife.py crates/aib-lease/src/hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-lease --locked --lib "$3" && echo "BITE $3"; }
kp() { python3 -I scripts/knife.py crates/aib-plan/src/bin/aib-hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-plan --locked --bin aib-hosts "$3" && echo "BITE $3"; }
echo "KNIFE KL1 a reading over 100 % is passed on"
kl '    if !pct.is_finite() || !(0.0..=100.0).contains(&pct) {' \
   '    if !pct.is_finite() { // knife' \
   out_of_range_values_are_refused_not_clamped
echo "KNIFE KL2 no core count is answered as 0 cores"
kl '        .filter(|n| *n > 0)' \
   '        .filter(|_| true) // knife' \
   out_of_range_values_are_refused_not_clamped
echo "KNIFE KL3 no shared sample: every call reads again (a polling page keeps a core busy)"
kl '        if at.elapsed() < CACHE_FOR {' \
   '        if false && at.elapsed() < CACHE_FOR { // knife' \
   a_real_sample_is_in_range_and_cached_for_a_second
echo "KNIFE KC1 load goes through the runtime client (needs a running aibd and its token file)"
kc '    if let ["load", rest @ ..] = args {' \
   '    if let ["load-knife", rest @ ..] = args {' \
   load_needs_no_running_aibd_and_takes_only_json
echo "KNIFE KG1 the route is not mounted"
kg $'    METRICS,\n    HOST_LOAD,\n    SEATS,' \
   $'    METRICS,\n    SEATS,' \
   host_load_needs_the_token_and_answers_in_range
echo "KNIFE KH1 the Windows probe goes back to a PowerShell line"
kh '    "aibd ctl load --json"' \
   '    "powershell -NoProfile -Command \"Write-Output 1\""' \
   windows_is_read_by_cpu_use_and_taken_only_when_asked
echo "KNIFE KH2 another version of the load object is read as version 1"
kh '        if j.get("aib_load")?.as_u64()? != 1 {' \
   '        if j.get("aib_load")?.as_u64()? == 0 { // knife' \
   windows_is_read_by_cpu_use_and_taken_only_when_asked
echo "KNIFE KH3 a reading over 100 % makes the machine free"
kh '        Some((pct, n)) if n > 0.0 && pct.is_finite() && (0.0..=100.0).contains(&pct) => {' \
   '        Some((pct, n)) if n > 0.0 && pct.is_finite() => { // knife' \
   windows_is_read_by_cpu_use_and_taken_only_when_asked
echo "KNIFE KP1 the cockpit's Windows probe goes back to PowerShell"
kp 'const WIN_REMOTE: &str = "aibd ctl load --json";' \
   'const WIN_REMOTE: &str = "powershell -NoProfile -Command 1";' \
   config_adds_windows_and_github
echo "KNIFE KP2 the cockpit reads 0 cores / over 100 % as online"
kp '        if n > 0.0 && pct.is_finite() && (0.0..=100.0).contains(&pct) {' \
   '        if pct.is_finite() { // knife' \
   config_adds_windows_and_github
echo "knives-hostload: all knives bit"
