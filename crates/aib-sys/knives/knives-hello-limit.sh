#!/usr/bin/env bash
# S10-3b-2 (PLAN-ap26-windows-first-c2f0b-1125 W-1b; 3e53b READ-winhello-wiring W2, READ-ap26-r2 L):
# the Windows Hello window limit (one window at a time) and the hardware flag. Written for every
# platform, so these bite on every receipt machine. Each knife, put in, must turn its own test red.
# Run from the repo root.
set -eu
kh() { python3 -I scripts/knife.py crates/aib-sys/src/hello.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-sys --locked --lib "$3"; }
# KH1 a window still up does not stop a second one
kh '    if open.swap(true, Ordering::SeqCst) {' \
   '    if open.swap(true, Ordering::SeqCst) && limit.is_zero() { // knife' \
   a_window_still_up_refuses_a_second_and_ends_cleanly
# KH2 a window that ended is still counted as open (every later call refused)
kh '            open.store(false, Ordering::SeqCst);' \
   '            open.store(true, Ordering::SeqCst); // knife' \
   a_window_still_up_refuses_a_second_and_ends_cleanly
# KH3 an implementation type that could not be read counts as hardware
kh '        Err(_) => false,' \
   '        Err(_) => true, // knife' \
   only_the_hardware_flag_is_hardware
# KH4 a software key counts as hardware
kh '        Ok(t) => t & IMPL_HARDWARE_FLAG != 0,' \
   '        Ok(t) => t & IMPL_HARDWARE_FLAG != 0 || t != 0, // knife' \
   only_the_hardware_flag_is_hardware
