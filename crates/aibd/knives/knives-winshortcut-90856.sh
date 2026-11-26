#!/usr/bin/env bash
# aib-knives: os=macos
# SC1–SC7: the Windows setup's "AIB" shortcut and `ctl web` starting a stopped AIB (ebab8 11-26 12:20,
# Andy「要最快讓用戶一鍵可用」). The rules are plain Rust in winsetup.rs, tested on every platform with
# the stand-in Ops; os=macos like knives-winsetup-90856.sh (same tests, same cell). Each knife must turn
# its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
sc() { python3 -I scripts/knife.py crates/aibd/src/winsetup.rs "$1" "$2" --expect-fail "winsetup::tests::$3" \
        -- cargo test -p aibd --locked --lib "winsetup::tests::$3"; }
# SC1 uninstall removes somebody else's AIB.lnk too
sc '            if shortcut_ours(&link, &p.exe, ops) {' \
   '            if !link.as_os_str().is_empty() {' \
   uninstall_removes_only_our_shortcuts
# SC2 the setup writes over somebody else's AIB.lnk
sc '        if ops.exists(&link) && !shortcut_ours(&link, &p.exe, ops) {' \
   '        if ops.exists(&link) && link.as_os_str().is_empty() {' \
   somebody_elses_aib_shortcut_is_left_as_it_is
# SC3 an AIB.lnk that is a link is written through
sc '    if ops.is_link(link) {' \
   '    if ops.is_link(link) && link.as_os_str().is_empty() {' \
   a_shortcut_that_is_a_link_is_not_written_through
# SC4 no desktop folder stops the setup
sc '            say(&format!("3/5 no AIB shortcut: {e}"));' \
   '            panic!("3/5 no AIB shortcut: {e}");' \
   no_shortcut_does_not_stop_the_setup
# SC5 `ctl web` from another program starts the installed aibd.exe
sc '    if !resident || !ops.same_file(current, &p.exe) {' \
   '    if !resident {' \
   web_from_elsewhere_starts_nothing
# SC6 `ctl web` naming another aibd (test / preview) starts the resident one
sc '    if !resident || !ops.same_file(current, &p.exe) {' \
   '    if !ops.same_file(current, &p.exe) {' \
   web_from_elsewhere_starts_nothing
# SC7 the shortcut runs something other than the workbench line
sc 'pub const SHORTCUT_ARGS: &str = "ctl web";' \
   'pub const SHORTCUT_ARGS: &str = "web";' \
   the_setup_puts_aib_on_the_desktop_and_in_the_start_menu
