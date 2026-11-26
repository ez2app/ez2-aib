#!/usr/bin/env bash
# aib-knives: os=windows
# aib-knives: job=rust-windows
# FW1–FW3 the Hello window belongs to a window of aibd's own (ebab8 11-26 11:56; Microsoft: a desktop program asks with
# IUserConsentVerifierInterop::RequestVerificationForWindowAsync): the own window is made, closed when the ask ends, and
# closed by its own thread when nobody waits for it any more (3e53b REVIEW-pack2 L2). Whether Windows puts the Hello
# window in front of it is checked on the laptop (W0, owner present).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py crates/aib-sys/src/hello_consent.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-sys --locked --lib "$3" || fail=1; }
# FW1 no own window: every ask goes without one
kn '        let thread = std::thread::Builder::new()' '        if true { return None; } let thread = std::thread::Builder::new()' hello_consent::tests::the_hello_window_gets_an_own_window_that_closes_after
# FW2 the own window is left open after the ask (closing it is skipped)
kn '        unsafe { PostMessageW(self.hwnd as _, WM_CLOSE, 0, 0) };' '        let _ = (PostMessageW, WM_CLOSE); if let Some(t) = self.thread.take() { std::mem::forget(t); }' hello_consent::tests::the_hello_window_gets_an_own_window_that_closes_after
# FW3 a window nobody waits for is left behind
kn '        unsafe { DestroyWindow(hwnd) };' '        let _ = DestroyWindow;' hello_consent::tests::a_window_nobody_waits_for_is_closed_by_its_thread
exit "$fail"
