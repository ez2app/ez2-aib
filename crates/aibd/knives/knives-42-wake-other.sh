#!/bin/bash
# 0eed7 11-20 01:2x: aibd ctl web naming another folder / address never asks launchd to start
# the resident aibd. Knives WK1-WK2.
set -eo pipefail
wk() { python3 -I scripts/knife.py crates/aibd/src/wake.rs "$1" "$2" --expect-fail a_line_naming_another_folder_or_address_never_wakes_the_resident \
        -- cargo test -p aibd --locked --lib wake::tests::a_line_naming_another_folder_or_address_never_wakes_the_resident; }
# WK1 AIBD_BIND is not looked at
wk '    !bind_set' '    !bind_set || bind_set // knife'
# WK2 any AIBD_DATA_DIR counts as the resident
wk '            Some(d) => default.is_some_and(|f| f == d),' '            Some(d) => default.is_some_and(|f| f == d) || !d.as_os_str().is_empty(), // knife'
