#!/usr/bin/env bash
# aib-knives: os=unix
# G1–G9 B2 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126; 3e53b REVIEW-b2consent L1 / L2 / L3):
# the Windows Hello gesture as the owner check, no key. Only the window's yes is a yes, every ask
# is a window (no cache), the window says only what aibd composed, readiness is read at each ask,
# and a keyless check takes no batch. Every knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
# every knife runs; a miss is counted, not a stop (3e53b 05:46)
fail=0
hs() { python3 -I scripts/knife.py crates/aib-sys/src/hello.rs "$1" "$2" --expect-fail "hello::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "hello::tests::$3" || fail=1; }
tg() { python3 -I scripts/knife.py crates/aibd/src/touch.rs "$1" "$2" --expect-fail "touch::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch::tests::$3" || fail=1; }
# G1 a cancelled Hello window counts as yes
hs '        6 => Err(HelloError::Cancel),' '        6 => Ok(()),' only_a_verified_gesture_is_a_yes
# G2 any result Windows adds later counts as yes (the Verified check gone)
hs '        other => Err(HelloError::Os(other as u32)),' '        _other => Ok(()),' only_a_verified_gesture_is_a_yes
# G3 no Hello on this machine / user / by policy counts as yes
hs '        1..=3 => Err(HelloError::NoHello),' '        1..=3 => Ok(()),' only_a_verified_gesture_is_a_yes
# G4 a machine without Hello is taken as ready
hs '        0 | 4 => Ok(()),' '        0 | 4 | 1 => Ok(()),' only_a_verified_gesture_is_a_yes
# G5 the owner check says yes without showing the window
tg '        (self.window)(reason, limit)' '        { let _ = (&self.window, reason, limit); Ok(()) }' a_gesture_yes_comes_only_from_the_window
# G6 the first answer is kept and reused for every later ask
tg '        (self.window)(reason, limit)' '        { static ONCE: std::sync::OnceLock<Result<(), &'"'"'static str>> = std::sync::OnceLock::new(); *ONCE.get_or_init(|| (self.window)(reason, limit)) }' a_gesture_yes_is_not_kept_for_the_next_ask
# G7 the window shows the request's own bytes
tg '        (self.window)(reason, limit)' '        (self.window)(std::str::from_utf8(msg).unwrap_or(reason), limit)' a_gesture_window_says_only_what_aibd_composed
# G8 readiness is not read at the ask (a PIN set later needs a restart; no Hello shows a window)
tg '        (self.ready)()?;' '        let _ = &self.ready;' without_the_gesture_set_up_it_refuses_and_a_later_pin_counts
# G9 a keyless owner check answers a whole list in one window
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs '        if !self.presence.signs() {' '        if !self.presence.signs() && false {' --expect-fail "owner_ok::tests::a_keyless_owner_check_does_not_take_a_batch" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::a_keyless_owner_check_does_not_take_a_batch" || fail=1
exit "$fail"
