#!/usr/bin/env bash
# NG1–NG5 (7b81e 11-20; N-webonly-c19, ORDER-webonly-today 11「回合結束的狀態閘」): with
# AIBD_SEAT_NOW_GATE=1 a claude seat (not a work seat) gets a settings file whose Stop and
# SessionStart hooks run `aibd ctl now-gate`; off by default; the switch reaches the preview.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ng() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" \
  --expect-fail seats::tests::a_seat_gets_aibds_one_server_and_its_two_tools_only \
  -- cargo test -p aibd --locked --lib seats::tests::a_seat_gets_aibds_one_server_and_its_two_tools_only; }
# NG1 the gate is on with no switch
ng '    if gate && mode != "work" {' \
   '    if (gate || true) && mode != "work" { // knife'
# NG2 a work seat gets a second --settings after its sandbox one
ng '    if gate && mode != "work" {' \
   '    if gate { // knife'
# NG3 a data dir with a space is split by the hook's shell
ng '    let cmd = format!("{} ctl now-gate --data-dir {}", q(aibd_exe), q(data_dir));' \
   '    let _ = &q; let cmd = format!("{} ctl now-gate --data-dir {}", aibd_exe, data_dir); // knife'
# NG4 a cleared or compacted seat does not get its line back
ng '    serde_json::json!({ "hooks": { "Stop": hook, "SessionStart": hook } }).to_string()' \
   '    serde_json::json!({ "hooks": { "Stop": hook } }).to_string() // knife'
# NG5 the switch stays behind in the parent
python3 -I scripts/knife.py crates/aibd/src/preview.rs '    "AIBD_SEAT_NOW_GATE",' '    "AIBD_NO_SUCH_GATE", // knife' \
  --expect-fail preview::tests::the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting \
  -- cargo test -p aibd --locked --lib preview::tests::the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
# NG6–NG8 (5d33a PREREAD 114ee0ac1 L1): a work seat's gate hooks go into its one sandbox
# --settings, keeping every sandbox key
nw() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" \
  --expect-fail seats::tests::a_work_seats_gate_rides_in_its_sandbox_settings \
  -- cargo test -p aibd --locked --lib seats::tests::a_work_seats_gate_rides_in_its_sandbox_settings; }
# NG6 the hooks are not added
nw '    v["hooks"] = gate["hooks"].clone();' \
   '    let _ = (&gate, &mut v); // knife'
# NG7 the sandbox settings are replaced by the gate's
nw '    v["hooks"] = gate["hooks"].clone();' \
   '    v = if v.is_object() { gate.clone() } else { v }; // knife'
# NG8 any --settings value is taken, a file path included
nw '                .is_ok_and(|v| v.get("sandbox").is_some())' \
   '                .map_or(true, |_| true) // knife'
