#!/usr/bin/env bash
# ErrorCode::ALL knives (a63ab 11-10): ALL one short, holding a code twice, or drifting
# from the contract order must fail the build or its own test. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors (#590) reads every
# scripts/knife.py call before the push and only understands plain text and $1..$9.
# E1 / E2 are build-time knives (scripts/knife-build.py), not read by that checker:
# their anchor is the ALL block, taken from the file itself below.
E=crates/aib-core/src/error.rs
b() { python3 -I scripts/knife-build.py "$E" "$1" "$2" --expect-error "$3" \
        -- cargo build -p aib-core --locked; }
t() { python3 -I scripts/knife.py crates/aib-core/src/error.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-core --locked --lib "$3"; }
# Text from the `ALL` line to the end of `ordinal` (one block: the knives below change
# both at once, the way a careless edit would).
block() { python3 -I -c '
import sys
s = open(sys.argv[1], encoding="utf-8").read()
a = s.index("    pub const ALL: [ErrorCode; ")
b = s.index("    pub fn as_str(self)")
sys.stdout.write(s[a:b])' "$E"; }
# The reader first, on coloured rustc output like CI's (CARGO_TERM_COLOR=always, #643)
python3 -I scripts/knife-build.py --self-test
B=$(block)
[ -n "$B" ] || { echo "knives-errorcode: ALL block not found"; exit 2; }
edit() { python3 -I -c "$1" "$B"; }

# E1 ALL one short (last code dropped, length fixed to match): its ordinal points outside ALL
N1=$(edit '
import sys
s = sys.argv[1].replace("; 26] = [", "; 25] = [", 1)
s = s.replace("        ErrorCode::WebNotBuilt,\n    ];", "    ]; // knife", 1)
sys.stdout.write(s)')
b "$B" "$N1" 'ErrorCode ordinal is outside ALL'

# E2 ALL holds a code twice (the last slot repeats another code)
N2=$(edit '
import sys
sys.stdout.write(sys.argv[1].replace("        ErrorCode::WebNotBuilt,\n    ];",
    "        ErrorCode::PairingFailed, // knife\n    ];", 1))')
b "$B" "$N2" 'ErrorCode::ALL is out of step with ErrorCode::ordinal'

# E0 negative arm: the build breaks for ANOTHER reason (a type error) on the very line
# that holds the assert's message. rustc prints that line, so the message is in the
# output, but it is not the const assert that stopped the build: knife-build must MISS
# (rc 1). rc 0 = it took the type error for the assert; rc 2 = the anchor is gone and
# nothing was tested. Both fail the script.
e0=0
b '    const { assert!(N < ErrorCode::ALL.len(), "ErrorCode ordinal is outside ALL") };' \
  '    const { assert!(N < ErrorCode::ALL.len() as u8, "ErrorCode ordinal is outside ALL") }; // knife' \
  'ErrorCode ordinal is outside ALL' || e0=$?
if [ "$e0" -ne 1 ]; then
  echo "knives-errorcode: E0 negative arm wanted knife-build rc 1 (MISSES), got rc $e0"
  exit 1
fi
echo "E0 negative arm: knife-build misses a build broken for another reason (as it should)"

# E3 two codes swap places on the wire (builds, round-trips): the contract order catches it
t $'            ErrorCode::HostNotAllowed => "host_not_allowed",\n            ErrorCode::OriginNotAllowed => "origin_not_allowed",' \
  $'            ErrorCode::HostNotAllowed => "origin_not_allowed", // knife\n            ErrorCode::OriginNotAllowed => "host_not_allowed",' \
  all_lists_every_code_once_in_contract_order

# E4 a code renamed on the wire
t '            ErrorCode::WebNotBuilt => "web_not_built",' \
  '            ErrorCode::WebNotBuilt => "web_missing", // knife' \
  all_lists_every_code_once_in_contract_order

echo "knives-errorcode: 4/4 bite, E0 negative arm misses"
