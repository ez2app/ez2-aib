#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Smoke for a shipped tool that has no --version (aib-drive, aib-slice; preview.7 C-1, 56cda):
# run it with no arguments on the platform it was built for; it must exit with its usage code and
# print its own usage line on stderr. That proves the file starts on this OS / CPU and is the tool
# named, not that any command works (each tool's tests and knives cover that).
#   exe-usage-smoke.sh <exe> <tool name> <usage exit code>
# Exit 0 = pass, 1 = the tool did not behave, 2 = this script was called wrong.
set -u
[ $# -eq 3 ] || { echo "usage: exe-usage-smoke.sh <exe> <tool name> <usage exit code>" >&2; exit 2; }
exe=$1 name=$2 want=$3
[ -f "$exe" ] || { echo "::error::$exe is not there"; exit 1; }
chmod +x "$exe" 2>/dev/null || true
err=$(mktemp "${TMPDIR:-/tmp}/usage-smoke.XXXXXX")
trap 'rm -f "$err"' EXIT
rc=0; "$exe" >/dev/null 2>"$err" </dev/null || rc=$?
first=$(grep -m1 -F "usage: $name" "$err" | tr -d '\r')
echo "$name with no arguments: rc $rc; ${first:-<no usage line>}"
[ "$rc" = "$want" ] || { echo "::error::$exe exited $rc with no arguments, want $want (its usage code)"; exit 1; }
[ -n "$first" ] || { echo "::error::$exe printed no 'usage: $name' line on stderr"; head -5 "$err"; exit 1; }
exit 0
