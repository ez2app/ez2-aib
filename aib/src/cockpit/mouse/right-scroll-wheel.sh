#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 右欄滾輪。滑一下是幾十到上百次；若每次都等整支滑鼠程式跑完，畫面就卡死。
# 同時只留一個人去改捲動位置。後來的那幾次各留一個數字就結束。
set -euo pipefail
dir="${1:-}"
sess="${2:-}"
case "$dir" in
  up) step=-1 ;;
  down) step=1 ;;
  *) exit 0 ;;
esac
sd="${EZ2_COCKPIT_STATE_DIR:-}"
if [ -n "$sess" ]; then
  looked="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_state_dir 2>/dev/null || true)"
  if [ -z "$looked" ]; then
    inst="$(env -u TMUX tmux show-option -qv -t "$sess" @aib_instance 2>/dev/null || true)"
    [ -n "$inst" ] && looked="${EZ2_AIB_INSTANCE_ROOT:-$HOME/.ez2-aib/instances}/$inst/cockpit"
  fi
  [ -n "$looked" ] && sd="$looked"
fi
[ -n "$sd" ] && [ -d "$sd" ] || exit 0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${EZ2_PY3:-python3}"
qdir="$sd/.scroll-wheel.q"
lockdir="$sd/.scroll-wheel.lock"
mkdir -p "$qdir"
ticket="$qdir/.$$-$RANDOM"
printf '%s\n' "$step" > "$ticket"
mv -f "$ticket" "$qdir/s-$$-$RANDOM"
if ! mkdir "$lockdir" 2>/dev/null; then
  # 同 mouse-ctl.sh:拿鎖的人死了、或鎖超過 1 分鐘(停電/重開機留下的)⇒ 接手;否則讓它做完。
  wpid=""
  [ -f "$lockdir/pid" ] && IFS= read -r wpid < "$lockdir/pid" || true
  if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null && [ -n "$(find "$lockdir" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then
    exit 0
  fi
  rm -rf "$lockdir" 2>/dev/null || exit 0
  mkdir "$lockdir" 2>/dev/null || exit 0
fi
printf '%s\n' "$$" > "$lockdir/pid" || true
trap 'rm -rf "$lockdir" 2>/dev/null || true' EXIT
while true; do
  steps=0
  found=0
  for f in "$qdir"/s-*; do
    [ -f "$f" ] || continue
    found=1
    n="$(cat "$f" 2>/dev/null || echo 0)"
    steps=$((steps + n))
    rm -f "$f"
  done
  if [ "$found" -eq 0 ]; then
    sleep 0.05
    for f in "$qdir"/s-*; do
      [ -f "$f" ] || continue
      found=1
      n="$(cat "$f" 2>/dev/null || echo 0)"
      steps=$((steps + n))
      rm -f "$f"
    done
    [ "$found" -eq 0 ] && exit 0
  fi
  if [ "$steps" -gt 0 ]; then
    wdir=down
    rows="$steps"
  elif [ "$steps" -lt 0 ]; then
    wdir=up
    rows=$((0 - steps))
  else
    continue
  fi
  rows=$((rows * 3))
  "$PY" "$HERE/../render/right_fold_ctl.py" --state-dir "$sd" \
    --action "{\"action\":\"fold-scroll\",\"dir\":\"$wdir\",\"rows\":$rows}" >/dev/null 2>&1 || true
done
