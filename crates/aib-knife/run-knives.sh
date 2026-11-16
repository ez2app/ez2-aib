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
# A value not known is an error, never a guess. Run from the repo root.
#
# Lines (read by the ledger and by people):
#   KNIVES-SCRIPT <file>: rc=<n> secs=<n>
#   KNIVES-SCRIPT <file>: SKIPPED why=not_this_os os=<wanted>
#   KNIVES-SCRIPT <file>: not_run why_not=timeout secs=<n> limit=<n>m
#   KNIVES-HEADER-BAD <file>: <what>
# rc 1 when any script failed, timed out or has a bad header, or when no script belongs to <job>.
set -uo pipefail
job=${1:?usage: run-knives.sh <job>}
case "$(uname -s)" in
  Darwin) here=macos ;;
  MINGW* | MSYS* | CYGWIN* | Windows_NT) here=windows ;;
  *) here=linux ;;
esac
poll=${AIB_KNIVES_POLL_SECS:-1}
mine=0
bad=()

for f in crates/*/knives/*.sh; do
  [ -f "$f" ] || continue
  want_os=macos want_job=rust limit=30 broken=""
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
  echo "== $f"
  start=$SECONDS
  set -m
  bash "$f" &
  pid=$!
  set +m
  timed_out=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ $((SECONDS - start)) -ge $((limit * 60)) ]; then
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
  if [ "$timed_out" = 1 ]; then
    echo "KNIVES-SCRIPT $f: not_run why_not=timeout secs=$secs limit=${limit}m"
    bad+=("$f")
  else
    echo "KNIVES-SCRIPT $f: rc=$rc secs=$secs"
    [ "$rc" = 0 ] || bad+=("$f")
  fi
done

if [ "$mine" = 0 ]; then
  echo "KNIVES-NONE job=$job: no crates/*/knives/*.sh belongs to it (a broken glob reads as clean otherwise)"
  exit 1
fi
if [ "${#bad[@]}" -gt 0 ]; then
  echo "KNIVES-RED ${#bad[@]}: ${bad[*]}"
  exit 1
fi
echo "KNIVES-OK job=$job scripts=$mine"
