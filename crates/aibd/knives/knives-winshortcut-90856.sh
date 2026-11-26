#!/usr/bin/env bash
# aib-knives: os=macos
# SC1–SC12: the Windows setup's "AIB" shortcut and `ctl web` starting a stopped AIB (ebab8 11-26 12:20,
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
   '            if true { panic!("3/5 no AIB shortcut: {e}") }' \
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
# SC8 (3e53b r1 #4) a shortcut is reported made without reading it back
sc '                if read_back_ours {' \
   '                if read_back_ours || !read_back_ours {' \
   a_shortcut_is_checked_by_reading_it_back
# SC9 (3e53b r1 wake #1) `ctl web` starts the path it was run from instead of the installed one
sc '    start_and_wait(&p.exe, ops).map(|()| true)' \
   '    start_and_wait(current, ops).map(|()| true)' \
   web_from_the_installed_copy_starts_a_stopped_aib
# SC10 (3e53b r1 wake #1) an installed aibd.exe that is a link is started
sc '    let plain = !ops.is_link(&p.exe) && ops.exists(&p.exe);' \
   '    let plain = ops.exists(&p.exe);' \
   web_does_not_start_an_installed_path_that_is_a_link
# SC11 (3e53b r1 wake #2, static) the real start goes through cmd
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '        let mut c = std::process::Command::new(exe);' \
  '        let mut c = std::process::Command::new("cmd");' \
  --expect-fail winsetup::tests::the_real_start_runs_the_program_directly_no_shell \
  -- cargo test -p aibd --locked --lib winsetup::tests::the_real_start_runs_the_program_directly_no_shell
# SC12 (ebab8 12:39; 3e53b r1: the code is not hidden) the shortcut window's line drops the pairing code
sc '            format!("配對碼 {code}(頁面上已自動填好,{secs} 秒內有效)"),' \
   '            format!("配對碼(頁面上已自動填好,{secs} 秒內有效)"),' \
   the_shortcut_window_says_two_plain_lines_with_the_code
