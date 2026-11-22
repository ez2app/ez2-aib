#!/usr/bin/env bash
# run-knives.sh <job> — the one knife step (S66-loop, DESIGN-knives-loop-ebb36-1117 r0, 519d0 11-17 00:07):
# runs every crates/*/knives/*.sh whose header says it belongs to <job>, in name order, so a new
# crate's knives need no new workflow step. Mac and Windows (Git Bash) alike; no GNU timeout
# (macOS has none): a watchdog polls and kills the script's process group at its limit.
#
# Header (first 10 lines of a knife script, one per line; not written = default):
#   # aib-knives: os=macos        macos (default) | windows | unix (not Windows) | any
#   # aib-knives: job=rust        rust (default) | another job, which names the script itself
#   # aib-knives: timeout=30m     default 30m, at most 240m
#   # aib-knives: env=NAME=value  set for this script only (one line each; NAME is A-Z 0-9 _,
#                                 value is letters, digits and . _ / : -), e.g. AIB_KNIVES_NO_SE=1
#
# Shards (S88-split, f1fae 11-20; Andy 11-20 00:2x): AIB_KNIVES_SHARD=k/N runs only every N-th of the
# scripts of <job> that run on this OS, starting at the k-th (name order), so N cells side by side run
# each script exactly once. Not set = 1/1 = all. A shard that gets no script is red, like an empty job.
# A value not known is an error, never a guess. Run from the repo root.
#
# Shards by time (Andy 11-22: use each run's seconds, do not only print them): name order gave #875's four
# cells 63 / 90 / 139 / 88 minutes. AIB_KNIVES_PLAN=<file> (lines "<k>\t<path>", made by
# `aib-xtask knife-shards plan` from the committed seconds table) names each script's cell instead of the
# name-order turn; a script that runs here and is not in the plan is red in every cell, a line not understood
# is rc 2. AIB_KNIVES_LIST=1 runs nothing and prints "KNIVES-ELIGIBLE <file>" for each script that would run
# here (the list the plan is made from, so this file stays the only reader of the headers). Not set = as before.
#
# Lines (read by the ledger and by people):
#   KNIVES-SCRIPT <file>: rc=<n> secs=<n>
#   KNIVES-SCRIPT <file>: missed=<n> rc=0        the script returned 0 over <n> knife lines that did not bite
#   KNIVES-SCRIPT <file>: SKIPPED why=not_this_os os=<wanted>
#   KNIVES-SCRIPT <file>: SKIPPED why=other_shard shard=<k>/<N>
#   KNIVES-SCRIPT <file>: not_run why_not=timeout secs=<n> limit=<n>m
#   KNIVES-HEADER-BAD <file>: <what>
#   KNIVES-ELIGIBLE <file>                       AIB_KNIVES_LIST=1 only
#   KNIVES-PLAN-MISSING <file>: the plan has no cell for it
# rc 1 when any script failed, timed out or has a bad header, when no script belongs to <job>, or
# when this shard ran none; rc 2 when AIB_KNIVES_SHARD is not k/N with 1 <= k <= N.
#
# A knife that did not bite is red whatever its script returns (5d33a 11-20, M; card N-run-knives-miss-red):
# six scripts without set -e returned 0 over a "KNIFE ...: MISSES" line and their cell stayed green. The
# script's stdout is shown as it comes and kept in a file; a line that starts with "KNIFE " (or
# "KNIFE-BUILD ", scripts/knife-build.py's compile-time knives; 5d33a 11-20) and says
# ": MISSES", ": INVALID" or ": pattern found " (scripts/knife.py: the text to change was not there once)
# makes that script red.
set -uo pipefail
job=${1:?usage: run-knives.sh <job>}
case "$(uname -s)" in
  Darwin) here=macos ;;
  MINGW* | MSYS* | CYGWIN* | Windows_NT) here=windows ;;
  *) here=linux ;;
esac
poll=${AIB_KNIVES_POLL_SECS:-1}
# seconds in one header minute: 60; xtask's run-knives tests set 1 so a 1m limit is 1 second
per_min=${AIB_KNIVES_SECS_PER_MIN:-60}
[[ "$per_min" =~ ^[1-9][0-9]*$ ]] || { echo "AIB_KNIVES_SECS_PER_MIN=$per_min is not a whole number of seconds"; exit 2; }
shard=${AIB_KNIVES_SHARD:-1/1}
if [[ "$shard" =~ ^([1-9][0-9]*)/([1-9][0-9]*)$ ]] && [ "${BASH_REMATCH[1]}" -le "${BASH_REMATCH[2]}" ]; then
  shard_k=${BASH_REMATCH[1]} shard_n=${BASH_REMATCH[2]}
  # the scripts never see it: a knife that runs this file under test (knives-run-knives.sh) would be sharded too
  unset AIB_KNIVES_SHARD
else
  echo "AIB_KNIVES_SHARD=$shard is not k/N with 1 <= k <= N"
  exit 2
fi
# the scripts never see these two either (see AIB_KNIVES_SHARD above)
list_only=${AIB_KNIVES_LIST:-}
plan_file=${AIB_KNIVES_PLAN:-}
unset AIB_KNIVES_LIST AIB_KNIVES_PLAN
plan_f=()
plan_k=()
if [ -n "$plan_file" ]; then
  [ -r "$plan_file" ] || { echo "AIB_KNIVES_PLAN=$plan_file cannot be read"; exit 2; }
  plan_re=$'^([1-9][0-9]*)\t([^\t]+)$'
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ $plan_re ]] && [ "${BASH_REMATCH[1]}" -le "$shard_n" ]; then
      plan_k+=("${BASH_REMATCH[1]}")
      plan_f+=("${BASH_REMATCH[2]}")
    else
      echo "AIB_KNIVES_PLAN line is not <k><tab><path> with 1 <= k <= $shard_n: $line"; exit 2
    fi
  done < "$plan_file"
fi
mine=0
here_ok=0 # scripts of <job> that run on this OS, the ones shards take turns over
ran=0
bad=()

# Byte order (LC_ALL=C), not the glob's locale order: shards take turns over this list, so every cell
# must see it in the same order whatever its locale (5d33a PREREAD-shard L1). The scripts themselves
# still run under the cell's own locale.
scripts=()
while IFS= read -r f; do scripts+=("$f"); done < <(printf '%s\n' crates/*/knives/*.sh | LC_ALL=C sort)
for f in "${scripts[@]}"; do
  [ -f "$f" ] || continue
  want_os=macos want_job=rust limit=30 broken=""
  envs=()
  while IFS= read -r line; do
    case "$line" in
      "# aib-knives: "*) ;;
      *) continue ;;
    esac
    kv=${line#"# aib-knives: "}
    kv=${kv%%[[:space:]]*}
    k=${kv%%=*} v=${kv#*=}
    case "$k" in
      os)
        case "$v" in
          macos | windows | unix | any) want_os=$v ;;
          *) broken="os=$v" ;;
        esac
        ;;
      job)
        if [[ "$v" =~ ^[a-z][a-z0-9_-]*$ ]]; then want_job=$v; else broken="job=$v"; fi
        ;;
      timeout)
        if [[ "$v" =~ ^([0-9]+)m$ ]] && [ "${BASH_REMATCH[1]}" -ge 1 ] && [ "${BASH_REMATCH[1]}" -le 240 ]; then
          limit=${BASH_REMATCH[1]}
        else
          broken="timeout=$v"
        fi
        ;;
      env)
        if [[ "$v" =~ ^[A-Z_][A-Z0-9_]*=[A-Za-z0-9._/:-]*$ ]]; then envs+=("$v"); else broken="env=$v"; fi
        ;;
      *) broken="$kv" ;;
    esac
  done < <(head -n 10 "$f")
  if [ -n "$broken" ]; then
    echo "KNIVES-HEADER-BAD $f: $broken"
    bad+=("$f")
    continue
  fi
  [ "$want_job" = "$job" ] || continue
  mine=$((mine + 1))
  case "$want_os" in
    any) ok=1 ;;
    unix) [ "$here" != windows ] && ok=1 || ok=0 ;;
    *) [ "$want_os" = "$here" ] && ok=1 || ok=0 ;;
  esac
  if [ "$ok" != 1 ]; then
    echo "KNIVES-SCRIPT $f: SKIPPED why=not_this_os os=$want_os"
    continue
  fi
  here_ok=$((here_ok + 1))
  turn=$here_ok
  if [ -n "$plan_file" ]; then
    turn=0
    pi=0
    while [ "$pi" -lt "${#plan_f[@]}" ]; do
      if [ "${plan_f[$pi]}" = "$f" ]; then
        turn=${plan_k[$pi]}
        break
      fi
      pi=$((pi + 1))
    done
    if [ "$turn" = 0 ]; then
      echo "KNIVES-PLAN-MISSING $f: the plan has no cell for it"
      bad+=("$f")
      continue
    fi
  fi
  if [ $(((turn - 1) % shard_n + 1)) != "$shard_k" ]; then
    echo "KNIVES-SCRIPT $f: SKIPPED why=other_shard shard=$shard_k/$shard_n"
    continue
  fi
  if [ -n "$list_only" ]; then
    echo "KNIVES-ELIGIBLE $f"
    ran=$((ran + 1))
    continue
  fi
  ran=$((ran + 1))
  echo "== $f"
  start=$SECONDS
  out=$(mktemp "${TMPDIR:-/tmp}/run-knives.XXXXXX")
  tee_pid=""
  # shown as it comes through a named pipe; where mkfifo is not to be had, shown when the script ends
  if mkfifo "$out.fifo" 2>/dev/null; then
    tee "$out" <"$out.fifo" &
    tee_pid=$!
  fi
  set -m
  # bash 3.2 (macOS) calls an empty "${envs[@]}" unbound under set -u
  if [ -n "$tee_pid" ]; then
    env ${envs[@]+"${envs[@]}"} bash "$f" >"$out.fifo" &
  else
    env ${envs[@]+"${envs[@]}"} bash "$f" >"$out" &
  fi
  pid=$!
  set +m
  timed_out=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ $((SECONDS - start)) -ge $((limit * per_min)) ]; then
      timed_out=1
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
      break
    fi
    sleep "$poll"
  done
  wait "$pid"
  rc=$?
  secs=$((SECONDS - start))
  if [ -n "$tee_pid" ]; then
    # tee ends when the last writer is gone; a child the script left with the pipe open gets 5 seconds
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
      kill -0 "$tee_pid" 2>/dev/null || break
      sleep 0.2
    done
    kill "$tee_pid" 2>/dev/null
    wait "$tee_pid" 2>/dev/null
  else
    cat "$out"
  fi
  missed=$(grep -Ec '^KNIFE(-BUILD)? .*: (MISSES|INVALID|pattern found )' "$out")
  rm -f "$out" "$out.fifo"
  if [ "$timed_out" = 1 ]; then
    echo "KNIVES-SCRIPT $f: not_run why_not=timeout secs=$secs limit=${limit}m"
    bad+=("$f")
  else
    echo "KNIVES-SCRIPT $f: rc=$rc secs=$secs"
    [ "$rc" = 0 ] || bad+=("$f")
    if [ "$rc" = 0 ] && [ "$missed" != 0 ]; then
      echo "KNIVES-SCRIPT $f: missed=$missed rc=0"
      bad+=("$f")
    fi
  fi
done

if [ "$mine" = 0 ]; then
  echo "KNIVES-NONE job=$job: no crates/*/knives/*.sh belongs to it (a broken glob reads as clean otherwise)"
  exit 1
fi
if [ "$shard_n" != 1 ] && [ "$ran" = 0 ]; then
  echo "KNIVES-NONE job=$job shard=$shard_k/$shard_n: this shard ran no script (more shards than scripts?)"
  exit 1
fi
if [ "${#bad[@]}" -gt 0 ]; then
  echo "KNIVES-RED ${#bad[@]}: ${bad[*]}"
  exit 1
fi
if [ "$shard_n" = 1 ]; then
  echo "KNIVES-OK job=$job scripts=$mine"
else
  echo "KNIVES-OK job=$job scripts=$mine shard=$shard_k/$shard_n ran=$ran"
fi
