#!/usr/bin/env bash
# aib-knives: os=unix
# IN1–IN3 / KK1–KK2 / MA1–MA3 / AC1–AC4 Andy's Mac clean install, then "optimise more" (ebab8 11-27 10:08): with AIBD_DATA_DIR
# set the install record is read there only, and the real aibd's plist is still refused from another folder; the AIB app
# in Applications is the default aibd's only; the page's waiting box gets the one window a signature opens (touch_id or
# login_password, never both); the app's click is `ctl web` only for its own executable with nothing after it, and an
# AIB.app that is not ours is left; `aib web` is ctl web
# and only that, beside the default aibd, never over AIB 1.x's aib. Each knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
k() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
# IN1 another aibd's record in the default folder answers for AIBD_DATA_DIR
k install.rs '        return Ok((read_record(d)?, Some(d.clone()), "AIBD_DATA_DIR"));' '        let _ = d;' install::tests::aibd_data_dir_alone_answers_and_the_other_aibds_plist_is_refused
# IN2 the real aibd's plist is rewritten from another folder
k install.rs '        if let Some(why) = plist_of_another(h, existing.as_ref(), &pl) {' '        if let Some(why) = plist_of_another(h, existing.as_ref(), &pl).filter(|_| false) {' install::tests::aibd_data_dir_alone_answers_and_the_other_aibds_plist_is_refused
# IN3 a test label takes the AIB app
k install.rs '    if pl.label.as_str() == DEFAULT_LABEL {' '    if !pl.label.as_str().is_empty() {' install::tests::the_default_aibd_gets_the_aib_app_and_a_test_label_does_not
# KK1 the finger is said where the password window opens
k touch.rs '        (Class::Fingerprint, true) | (Class::PasswordOnly, _) => Some("login_password"),' '        (Class::Fingerprint, true) | (Class::PasswordOnly, _) => Some("touch_id"),' touch::tests::the_key_kind_is_the_window_a_signature_opens_and_one_word
# KK2 the password is said where Touch ID is asked
k touch.rs '        (Class::Fingerprint, false) => Some("touch_id"),' '        (Class::Fingerprint, false) => Some("login_password"),' touch::tests::the_key_kind_is_the_window_a_signature_opens_and_one_word
# MA1 any aibd started with nothing after it is taken for the app
k macapp.rs '    named && (rest.is_empty() || (rest.len() == 1 && rest[0].starts_with("-psn_")))' '    named || rest.is_empty()' macapp::tests::only_the_apps_own_executable_with_nothing_after_it_is_the_app
# MA2 somebody else's AIB.app (another bundle id) is overwritten
k macapp.rs '    id.as_deref() == Some(BUNDLE_ID) && (!exists(&exec) || is_link(&exec))' '    id.is_some() && (!exists(&exec) || is_link(&exec))' macapp::tests::install_writes_ours_rewrites_ours_and_leaves_anything_else
# MA3 our bundle id with a real program as its executable is overwritten
k macapp.rs '    id.as_deref() == Some(BUNDLE_ID) && (!exists(&exec) || is_link(&exec))' '    id.as_deref() == Some(BUNDLE_ID) && (!exists(&exec) || is_link(&exec) || true)' macapp::tests::install_writes_ours_rewrites_ours_and_leaves_anything_else
# AC1 aib takes more than web
k aib_cmd.rs '        ["web", rest @ ..] => {' '        [_, rest @ ..] => {' aib_cmd::tests::aib_web_is_ctl_web_and_nothing_else_is_taken
# AC2 AIB 1.x's aib is taken for ours and replaced
k aib_cmd.rs '                .is_some_and(|n| n == OsStr::new(crate::install::EXE_NAME))' '                .is_some_and(|n| n != OsStr::new(""))' aib_cmd::tests::install_links_aib_to_aibd_and_leaves_another_aib
# AC3 the AIB app's own executable is taken for aib
k aib_cmd.rs '        .is_some_and(|s| s == OsStr::new(NAME) || (cfg!(windows) && s.eq_ignore_ascii_case(NAME)))' '        .is_some_and(|s| s.eq_ignore_ascii_case(NAME))' aib_cmd::tests::aib_web_is_ctl_web_and_nothing_else_is_taken
# AC4 install does not put aib beside aibd
k install.rs '        for l in crate::aib_cmd::place_lines(&dir, &target, h.path_env.as_ref()) {' '        for l in crate::aib_cmd::place_lines(&dir, &target, h.path_env.as_ref()).into_iter().skip(9) {' install::tests::the_default_aibd_gets_the_aib_app_and_a_test_label_does_not
exit "$fail"
