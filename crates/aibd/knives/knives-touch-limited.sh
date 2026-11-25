#!/usr/bin/env bash
# aib-knives: os=unix
# TL1–TL6: a window held back by the 3 in 10 minutes / 6 an hour says so and says when one fits again, not "cannot be asked on this machine" (0eed7 pv7 L; 519d0 11-21 07:46)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
tp() { python3 -I scripts/knife.py crates/aibd/src/pairing.rs "$1" "$2" --expect-fail "pairing::tests::$3" \
        -- cargo test -p aibd --locked --lib "pairing::tests::$3"; }
to() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
# TL1 the ten-minute wait counts from the wrong window, over the hour
tp '        ends.push(short[short.len() - RECONFIRM_SHORT_MAX] + RECONFIRM_SHORT);' \
   '        ends.push(short[0] + RECONFIRM_LONG);' \
   fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
# TL2 the hour's wait counts from the newest window
tp '        ends.push(long[long.len() - RECONFIRM_LONG_MAX] + RECONFIRM_LONG);' \
   '        ends.push(long[long.len() - 1] + RECONFIRM_LONG);' \
   fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
# TL3 the gate never says how long
tp '        limit_wait(&inner.touch_asks, (self.clock)())' \
   '        limit_wait(&inner.touch_asks, (self.clock)()).filter(|_| false)' \
   fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
# TL4 the time to try again is now
to '        Some((self.clock)() + w.as_secs() + u64::from(w.subsec_nanos() > 0))' \
   '        Some((self.clock)() + u64::from(w.subsec_nanos() > w.subsec_nanos()))' \
   a_no_cools_the_window_and_an_hour_holds_six
# TL5 the time goes with the wrong refusal
to '    if why == "pair_touch_limited" {' \
   '    if why == "pair_touch_busy" {' \
   a_no_cools_the_window_and_an_hour_holds_six
# TL6 the limit still says it cannot be asked here
to '"at most 3 fingerprint windows in 10 minutes and 6 in an hour; try again later"' \
   '"this needs the owner'"'"'s fingerprint, and it cannot be asked on this machine yet"' \
   a_no_cools_the_window_and_an_hour_holds_six
