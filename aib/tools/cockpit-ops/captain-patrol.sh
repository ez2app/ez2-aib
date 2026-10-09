#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 艦長督導迴圈正本(程式化,取代臨時手寫;the maintainer 令 2026-08-08「不能只靠 AI 記憶」)
# 每輪五件事:1 大綱/板進度 2 逐工位狀態 3 停滯偵測 4 選項/中斷/@ 偵測 5 完成標記
# 模式:--once 印快照後退出;預設=迴圈,任一觸發即 exit 0 並印 TRIGGER 行(喚醒督導 AI)
SP="${EZ2_SANDBOX:-/private/tmp/claude-501/-Users-user/bb890cd5-5952-492c-9aae-cf174cba460d/scratchpad/acb-sandbox}"
[ -d "$SP" ] || SP="$HOME/ez2-cockpit-demo/acb-sandbox"
REPO="$HOME/ez2-aib"
STATE="$HOME/ez2-cockpit-demo/.patrol-state"
LOG="$HOME/ez2-cockpit-demo/patrol.log"
WORKERS="${EZ2_WORKERS:-cl2 cl3 dev2 flt2 cap2}"
STALL_MIN="${EZ2_STALL_MIN:-10}"
mkdir -p "$STATE"

snapshot() {
  /usr/bin/python3 "$HOME/ez2-cockpit-demo/board-sync.py" "$SP" 2>/dev/null
  echo "== 進度 $(date '+%H:%M:%S') =="
  /usr/bin/python3 - "$SP" <<'PY'
import glob, json, os, sys
sb = sys.argv[1]
st = {}
for p in glob.glob(os.path.join(sb, 'tasks', '*.json')):
    t = json.load(open(p))
    st.setdefault(t.get('status'), []).append((t['id'][2:], t.get('assignee') or '-'))
d = st.get('done', []); r = st.get('in_progress', []); o = st.get('open', [])
print(f"✓{len(d)} 🔧{len(r)} ⬜{len(o)}")
for tid, who in sorted(r):
    print(f"  🔧 {tid} @{who}")
PY
  for w in $WORKERS; do
    if tmux -L aihub list-windows -t aihub -F '#W' 2>/dev/null | grep -qx "$w"; then
      line=$(tmux -L aihub capture-pane -pt "aihub:$w" 2>/dev/null | grep -E "CTX" | tail -1)
      echo "  $w: ${line:-畫面滾動中(活體)}"
    else
      echo "  $w: 窗口不存在"
    fi
  done
}

check_triggers() {
  for w in $WORKERS; do
    tmux -L aihub list-windows -t aihub -F '#W' 2>/dev/null | grep -qx "$w" || { echo "TRIGGER: $w 窗口消失"; return 0; }
    out=$(tmux -L aihub capture-pane -pt "aihub:$w" 2>/dev/null | grep -v '^$' | tail -8)
    echo "$out" | grep -q "❯ 1\." && { echo "TRIGGER: $w 有現行選項"; return 0; }
    echo "$out" | grep -q "Login expired\|usage credits — you have" && { echo "TRIGGER: $w 帳務/登入中斷"; return 0; }
    echo "$out" | grep -q "@pm9aee1\|@第一艦長\|@艦長" && { echo "TRIGGER: $w @艦長"; return 0; }
    # 停滯偵測:畫面內容 hash 連續 STALL_MIN 分鐘未變且非等待輸入狀態
    h=$(echo "$out" | /sbin/md5 -q 2>/dev/null || echo "$out" | md5sum | cut -d' ' -f1)
    sf="$STATE/$w.hash"
    prev=$(cat "$sf" 2>/dev/null)
    now=$(date +%s)
    if [ "$h" = "${prev%% *}" ]; then
      t0="${prev##* }"
      if [ $((now - ${t0:-$now})) -ge $((STALL_MIN * 60)) ]; then
        echo "$h $now" > "$sf"
        echo "TRIGGER: $w 疑似停滯(${STALL_MIN}分無變化)"
        return 0
      fi
    else
      echo "$h $now" > "$sf"
    fi
  done
  # 完成標記(逐一列點,新增標記在此登記)
  for m in \
    "$HOME/ez2-cockpit-demo/E6.1-checkpoint-protocol.md:E6.1 就緒" \
    "$HOME/ez2-cockpit-demo/E8.2-lesson-loop.md:E8.2 就緒" \
    "$REPO/src/messaging/R30-REVIEWED:E2.4 初審過" \
    "$REPO/src/exam/EXAM-REVIEWED:E4.3 初審過" \
    "$REPO/src/persona-merge/MERGE-REVIEWED:E5.8 初審過"; do
    f="${m%%:*}"; label="${m##*:}"
    [ -f "$f" ] && { echo "TRIGGER: $label"; return 0; }
  done
  # 整點回報
  [ "$(date +%M)" = "00" ] && { echo "TRIGGER: 整點回報 $(date '+%H:%M')"; return 0; }
  return 1
}

if [ "$1" = "--once" ]; then
  snapshot
  check_triggers || echo "(無觸發)"
  exit 0
fi

snapshot >> "$LOG"
[ "$(date +%M)" = "00" ] && sleep 60
i=0
while [ $i -lt 100 ]; do
  if msg=$(check_triggers); then
    echo "$msg" | tee -a "$LOG"
    exit 0
  fi
  sleep 20
  i=$((i+1))
done
echo "HEARTBEAT" | tee -a "$LOG"
