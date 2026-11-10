#!/usr/bin/env bash
# ErrorCode::ALL knives (a63ab 11-10): ALL one short, holding a code twice, or drifting
# from the contract order must fail the build or its own test. Run from the repo root.
set -eu
E=crates/aib-core/src/error.rs
b() { python3 -I scripts/knife-build.py "$E" "$1" "$2" --expect-error "$3" \
        -- cargo build -p aib-core --locked; }
t() { python3 -I scripts/knife.py "$E" "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-core --locked --lib "$3"; }
# Text from the `ALL` line to the end of `ordinal` (one block: the knives below change
# both at once, the way a careless edit would).
block() { python3 -I -c '
import sys
s = open(sys.argv[1], encoding="utf-8").read()
a = s.index("    pub const ALL: [ErrorCode; ")
b = s.index("    pub fn as_str(self)")
sys.stdout.write(s[a:b])' "$E"; }
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

# E3 two codes swapped in ALL and in ordinal together (builds): the contract order catches it
N3=$(edit '
import sys
s = sys.argv[1]
s = s.replace("        ErrorCode::HostNotAllowed,\n        ErrorCode::OriginNotAllowed,\n",
              "        ErrorCode::OriginNotAllowed,\n        ErrorCode::HostNotAllowed, // knife\n", 1)
s = s.replace("ErrorCode::HostNotAllowed => at::<0>(),", "ErrorCode::HostNotAllowed => at::<1>(),", 1)
s = s.replace("ErrorCode::OriginNotAllowed => at::<1>(),", "ErrorCode::OriginNotAllowed => at::<0>(),", 1)
sys.stdout.write(s)')
t "$B" "$N3" all_lists_every_code_once_in_contract_order

# E4 a code renamed on the wire
t '            ErrorCode::WebNotBuilt => "web_not_built",' \
  '            ErrorCode::WebNotBuilt => "web_missing", // knife' \
  all_lists_every_code_once_in_contract_order

echo "knives-errorcode: 4/4 bite"
