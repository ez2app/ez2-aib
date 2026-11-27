#!/usr/bin/env bash
# aib-knives: os=unix
# P1–P14 `aib web` on Windows (Andy 11-27 10:1x via ebab8; 3e53b PRE-READ-aib-cmd-windows-3e53b-1127 r1):
# aib.exe beside aibd.exe, the AIB folder at the END of the user's PATH with its type kept and read
# unexpanded, there already (any case, %VAR%) ⇒ not added, uninstall takes out only the noted entry
# and only when it is there once, another aib on PATH ⇒ none placed. The PATH rules are plain Rust
# (aib_cmd.rs, winsetup.rs with the stand-in Ops) tested on every platform; the registry calls are
# Windows only, so P1s / P2s bite the source-shape test in aib-sys. Each knife stays warning-free.
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
w() { python3 -I scripts/knife.py crates/aibd/src/winsetup.rs "$1" "$2" --expect-fail "winsetup::tests::$3" \
        -- cargo test -p aibd --locked --lib "winsetup::tests::$3" || fail=1; }
a() { python3 -I scripts/knife.py crates/aibd/src/aib_cmd.rs "$1" "$2" --expect-fail "aib_cmd::tests::$3" \
        -- cargo test -p aibd --locked --lib "aib_cmd::tests::$3" || fail=1; }
s() { python3 -I scripts/knife.py crates/aib-sys/src/winrun.rs "$1" "$2" --expect-fail "winrun::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "winrun::tests::$3" || fail=1; }
# P1 the setup writes the PATH back as REG_SZ whatever it was
w '        match ops.user_path_write(&new, kind, before) {' '        match ops.user_path_write(&new, PathKind::Sz, before) {' the_setup_puts_aib_beside_aibd_and_its_folder_at_the_end_of_the_path
# P1u uninstall writes it back as REG_SZ
w '        match ops.user_path_write(&new, before.1, Some((before.0.as_str(), before.1))) {' '        match ops.user_path_write(&new, PathKind::Sz, Some((before.0.as_str(), before.1))) {' uninstall_takes_out_only_our_path_entry_and_our_aib
# P1s the registry write turns REG_EXPAND_SZ into REG_SZ
s '        PathKind::ExpandSz => REG_EXPAND_SZ,' '        PathKind::ExpandSz => REG_SZ,' only_the_path_value_of_the_environment_key_is_touched
# P2s the registry read expands %VARIABLES% (they would be written back frozen)
s '            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND,' '            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ,' only_the_path_value_of_the_environment_key_is_touched
# P2 an entry written with %LOCALAPPDATA% is not seen as ours: added twice
a '        !k.is_empty() && (want.contains(&k) || want.contains(&kx))' '        !k.is_empty() && (want.contains(&k) || (false && want.contains(&kx)))' our_folder_goes_at_the_end_once_and_nothing_else_changes
# P3 our folder goes in front (it would hide the customer's other commands)
a '        format!("{old};{dir}")' '        format!("{dir};{old}")' our_folder_goes_at_the_end_once_and_nothing_else_changes
# P4 uninstall takes out one of two entries the customer has
a '        .filter(|(_, e)| **e == seg)' '        .filter(|(_, e)| **e == seg).take(1)' uninstall_takes_out_only_what_aib_added_and_only_once
# P5 the comparison is case sensitive: added again in other case
a '        .to_lowercase()' '        .to_string()' our_folder_goes_at_the_end_once_and_nothing_else_changes
# P6 another aib on PATH (AIB 1.x) is not looked for
w '    let path = ops.path_env()?;' '    let path = ops.path_env().filter(|_| false)?;' another_aib_on_the_path_or_in_our_folder_is_left_and_none_placed
# P7 another program named aib.exe in our folder is replaced
w '        if ops.hash(&p.prev).ok().as_deref() != Some(have.as_str()) {' '        if false && ops.hash(&p.prev).ok().as_deref() != Some(have.as_str()) {' another_aib_on_the_path_or_in_our_folder_is_left_and_none_placed
# P8 uninstall removes an aib.exe that is not ours
w '            if ours {' '            if ours || !ours {' uninstall_takes_out_only_our_path_entry_and_our_aib
# P9 uninstall takes our folder out of a PATH the setup did not add it to
w '    let Some(seg) = ops.note_read(&p.path_note) else {' '    let Some(seg) = ops.note_read(&p.path_note).or_else(|| Some(p.dir.to_string_lossy().into_owned())) else {' uninstall_takes_out_only_our_path_entry_and_our_aib
# P10 `aib web` from an aib.exe that is not this aibd.exe's copy starts aibd
w '                .is_ok_and(|h| ops.hash(&p.exe).is_ok_and(|e| e == h))' '                .is_ok_and(|_h| true)' web_from_our_aib_starts_the_installed_aibd
# P11 "put back as it was" said without reading it back (f163c REVIEW-op8 L1)
s '        Some((v, k)) => set(v, k).is_ok() && reads(Some((v, k))),' '        Some((v, k)) => set(v, k).is_ok(),' a_path_write_is_compared_before_read_back_after_and_put_back_only_when_it_reads_so
# P12 none before: the value is taken away whatever it is now (somebody else's too)
s '        None => reads(None) || (reads(Some((new, kind))) && remove().is_ok() && reads(None)),' '        None => reads(None) || (remove().is_ok() && reads(None)),' a_path_write_is_compared_before_read_back_after_and_put_back_only_when_it_reads_so
# P13 uninstall trusts a note naming another folder (3e53b r5 N1)
w '    if crate::aib_cmd::seg_key(&seg) != this {' '    if false && crate::aib_cmd::seg_key(&seg) != this {' uninstall_takes_out_only_our_path_entry_and_our_aib
# P14 a %variable% written in lower case is not expanded: our folder added again (90856 op11)
a '                match var(name).or_else(|| var(&name.to_ascii_uppercase())) {' '                match var(name) {' our_folder_goes_at_the_end_once_and_nothing_else_changes
exit "$fail"
