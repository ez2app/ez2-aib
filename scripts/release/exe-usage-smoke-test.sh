#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Tests for exe-usage-smoke.sh with fake tools (no network, no cargo). Exit 0 = all pass.
set -u
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/usage-smoke-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fail=0
fake() { # fake <file> <rc> <stderr text>
  printf '#!/bin/sh\nprintf "%%s\\n" "%s" >&2\nexit %s\n' "$3" "$2" > "$1"; chmod +x "$1"
}
check() { # check <case> <want rc> <args…>
  local name=$1 want=$2; shift 2
  local rc=0; bash "$here/exe-usage-smoke.sh" "$@" >/dev/null 2>&1 || rc=$?
  if [ "$rc" = "$want" ]; then echo "ok $name"; else echo "FAIL $name: rc $rc, want $want"; fail=1; fi
}
fake "$work/good" 2 "usage: aib-drive <observe|next> …"
fake "$work/slice" 64 "aib-slice: the commands are sweep, due and gc
usage: aib-slice sweep [--repo DIR]"
fake "$work/wrongrc" 0 "usage: aib-drive <observe|next> …"
fake "$work/wrongname" 2 "usage: aibd …"
check usage_code_and_line_pass 0 "$work/good" aib-drive 2
check usage_line_on_second_line_passes 0 "$work/slice" aib-slice 64
check another_exit_code_fails 1 "$work/wrongrc" aib-drive 2
check another_tools_usage_fails 1 "$work/wrongname" aib-drive 2
check a_missing_file_fails 1 "$work/none" aib-drive 2
check wrong_call_is_2 2 "$work/good" aib-drive
exit $fail
