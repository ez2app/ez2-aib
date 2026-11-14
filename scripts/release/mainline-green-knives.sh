#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Knives for mainline-green.sh: each takes one check away in a temporary copy and the named test
# case must then fail. The tree is never changed. Exit 0 = every knife bit.
# Needs bash, jq and perl.
set -u
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/mg-knife.XXXXXX")
trap 'rm -rf "$work"' EXIT
missed=0

knife() { # knife <name> <old text, exactly once> <new text> <test case>
  local name=$1 old=$2 new=$3 case=$4
  rm -rf "$work/k"; mkdir -p "$work/k"
  cp "$here/mainline-green.sh" "$here/mainline-green-test.sh" "$work/k/"
  if ! perl -e 'my ($p,$o,$n)=@ARGV; local $/; open my $f,"<",$p or exit 9; my $s=<$f>; close $f;
      my $c=()=$s=~/\Q$o\E/g; exit 8 if $c!=1; $s=~s/\Q$o\E/$n/; open $f,">",$p or exit 9; print $f $s;' \
      "$work/k/mainline-green.sh" "$old" "$new"; then
    echo "KNIFE $name: ANCHOR-MISSING"; missed=$((missed + 1)); return
  fi
  # the mutant must still be a working script: the green case holds, or the knife proves nothing
  if ! bash "$work/k/mainline-green-test.sh" green >/dev/null 2>&1; then
    echo "KNIFE $name: INVALID (the mutant breaks the green case)"; missed=$((missed + 1)); return
  fi
  if bash "$work/k/mainline-green-test.sh" "$case" >/dev/null 2>&1; then
    echo "KNIFE $name: MISSES ($case still holds)"; missed=$((missed + 1))
  else
    echo "KNIFE $name: BITES ($case)"
  fi
}

knife on_main '    identical | behind) ;;' '    identical | behind | diverged) ;;' not_on_main
knife ahead '    identical | behind) ;;' '    identical | behind | ahead) ;;' ahead_of_main
knife event 'and .event == "push" and' 'and' pr_run
knife branch '.head_branch == "main"' 'true' other_branch
knife repo '.head_repository.full_name == $r' 'true' fork_run
knife sha 'select(.head_sha == $s and' 'select(' other_sha
knife completed 'and .status == "completed")]' ')]' still_running
knife last '| sort_by(.updated_at) | last // empty' '| sort_by(.updated_at) | first // empty' last_run_counts
knife no_run '[ -n "$run" ] || { reason="no_main_push_run"; return 1; }' 'true' no_run
knife run_green '[ "$(jq -r '"'"'.conclusion // ""'"'"' <<<"$run")" = success ] ||' 'true ||' run_red
knife windows '      need("rust (windows-latest)"),' '' windows_missing
knife job_green 'elif .[0].conclusion != "success" then' 'elif false then' macos_red
knife knives 'def knives: .name | endswith("knives (macOS)");' 'def knives: false;' knives_red
knife not_run '.conclusion != "skipped")]' '.conclusion == "nope")]' nothing_ran
knife knives_not_run '(.jobs // [] | .[] | select(rust or knives) | ran(.)),' '(.jobs // [] | .[] | select(rust) | ran(.)),' knives_ran_nothing
knife clippy '(["fmt", "clippy"][] as $n' '(["fmt"][] as $n' clippy_skipped
knife tests 'if any(.conclusion == "success") and' 'if true or' tests_skipped
knife unreadable '|| { reason="jobs_unreadable"; return 2; }
  local bad' '|| jobs='"'"'{"jobs":[]}'"'"'
  local bad' unreadable
knife ledger_decides 'result=fail; [ $rc = 0 ] && result=pass' 'result=fail; [ $rc = 0 ] && result=pass; [ -n "${MG_LEDGER_IN:-}" ] && grep -q fail "$MG_LEDGER_IN" && rc=1' ledger

[ $missed = 0 ] && echo "mainline-green knives: all bit" || { echo "mainline-green knives: $missed missed"; exit 1; }
