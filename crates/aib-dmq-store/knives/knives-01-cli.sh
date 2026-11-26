#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-dmq CLI knives (macOS)" step of aibd.yml's rust job (#756, aib-dmq-store B1 S3b), moved here
# word for word (11-16 裁定:新刀放 crates/<套件>/knives/;519d0 IFACE-drive-shadow-519d0-1117 §5; the
# shape of #804 / #823). aibd.yml runs every crates/aib-dmq-store/knives/*.sh in name order in one step;
# aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root; -eo pipefail is what
# GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
cb() { python3 -I scripts/knife.py crates/aib-dmq-store/src/bin/aib-dmq.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-dmq-store --locked --test cli "$3"; }
cr() { python3 -I scripts/knife.py crates/aib-dmq-store/src/recv.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-dmq-store --locked --test cli "$3"; }
cs() { python3 -I scripts/knife.py aib/src/cockpit/dmq-route.sh "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-dmq-store --locked --test cli "$3"; }
cu() { python3 -I scripts/knife.py crates/aib-dmq-store/src/bin/aib-dmq.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-dmq-store --locked --bin aib-dmq "$3"; }
# CK1 a global dm is moved in Rust (its receipt never sent)
cb 'if truthy(item.get("global"))' 'if truthy(item.get("global")) && false' \
   a_global_dm_goes_back_to_python_untouched
# CK2 `--seat ""` is taken as a seat instead of asking seat_who
cb '.filter(|s| !s.is_empty())' '.filter(|_| true)' \
   what_argparse_or_seat_who_must_answer_goes_back_to_python
# CK3 a blank reopen reason is not refused up front
cr 'let text = py_strip(why);' 'let text = why;' reopen_refusals_name_the_move_like_python
# CK4 a reopen refusal loses from / to / actor
cr '(m.from, m.reason == Reason::Ok)' '(m.from, true)' reopen_refusals_name_the_move_like_python
# CK5 a missing evidence exits 4 instead of 9
cb '"evidence" => 9,' '"evidence" => 4,' close_needs_evidence_then_closes_with_it
# CK6 the full-text block loses its line break
cb 's.push_str("----- 全文 -----\n");' 's.push_str("----- 全文 -----");' \
   take_prints_one_json_line_then_the_text
# CK7 the dispatcher does not go back to Python on 75
cs 'if [ "$rc" -eq 75 ]; then' 'if [ "$rc" -eq 76 ]; then' \
   the_bash_dispatcher_runs_rust_when_listed_and_python_on_75
# CK8 a flag of another parser (take --wait on close) is taken here
cu '("--wait", Cmd::Take) => &mut a.wait,' '("--wait", _) => &mut a.wait,' \
   the_flags_are_argparse_s_and_nothing_else
# CK9 a tombstone goes into the move (rev null + an audit line, unlike Python)
cr 'let Some(found) = live_item(store, seat, id)? else {' \
   'let Some(found) = store.read_item(seat, id).ok().and_then(|r| match r { ItemRead::Item { item, .. } => Some(item), _ => None }) else {' \
   reopen_of_a_tombstone_is_not_found_before_any_move
# CK10 a relative AIB_DMQ_BIN is run
cs '    /*) ;;' '    *) ;;' the_bash_dispatcher_runs_rust_when_listed_and_python_on_75
rl() { python3 -I scripts/knife.py crates/aib-dmq-store/src/recv.rs "$1" "$2" --expect-fail "recv::tests::$3" \
         -- cargo test -p aib-dmq-store --locked --lib "recv::tests::$3"; }
# RW1 (#871 Windows) a drive-letter path reads as a path on unix too
rl "    let absolute = path.starts_with('/') || (cfg!(windows) && Path::new(path).is_absolute());" "    let absolute = path.starts_with('/') || (cfg!(windows) && Path::new(path).is_absolute()) || path.contains(':');" relay_pointer_forms
