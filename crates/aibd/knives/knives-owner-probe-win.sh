#!/usr/bin/env bash
# OW1–OW4 (W-3, PLAN-ap26-windows-first-c2f0b-1125 r2 §1; 3e53b READ-ap26-r2 L): the Windows class reads the desk first, a software Hello key is never the strong class, an unreadable read is never the strong class, and Hello not set up is "no Hello"
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ow() { python3 -I scripts/knife.py crates/aibd/src/owner_probe_win.rs "$1" "$2" --expect-fail "owner_probe_win::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_probe_win::tests::$3"; }
# OW1 a "yes" read from another session is taken
ow '    if !d.at_desk() {' \
   '    if !d.at_desk() && !d.hello_provider() {' \
   not_at_the_desk_comes_before_anything_hello_says
# OW2 a software Passport key is the strong class
ow '        (Some(true), Some(false)) => WinClass::HelloSoftware,' \
   '        (Some(true), Some(false)) => WinClass::HelloHardware,' \
   only_a_hardware_key_with_hello_is_the_strong_class
# OW3 an unreadable enrolment is taken as set up
ow '        (None, _) => WinClass::Unknown,' \
   '        (None, _) => WinClass::HelloKeyUnknown,' \
   no_provider_or_not_set_up_is_no_hello_and_unreadable_is_unknown
# OW4 Hello not set up is let through to the key read
ow '        (Some(false), _) => WinClass::NoHello,' \
   '        (Some(false), _) => WinClass::Unknown,' \
   no_provider_or_not_set_up_is_no_hello_and_unreadable_is_unknown
