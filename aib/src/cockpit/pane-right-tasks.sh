#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Right task summary, formerly the 2.任務 content tab.
# ★ 2026-09-12 the maintainer 令:右欄第 2 頁改「AI用量」,本檔**不再被任何頁簽呼叫**(pane-right-tabs.sh 檔頭)。
#   留檔只因三支尺仍把它當工作項帳本的消費面在跑(test-work-item-ledger-t1-t4 / work-ledger-hardening /
#   env-consumer-errexit);要退役連那三支一起處理,不在這一顆。真正的 AI任務區住在待審框頁簽(第 38/39/68 項)。
# ★ 這一幀的每一個位元組都必須是**資料的函數**:pane-right-tabs.sh:38-42 拿整幀
#   做 diff,幀變了才重畫,而重畫會清掉終端選取。2026-08-23(SPEC-cockpit-render-cost
#   -0822 刀 1)刪掉此處的 `更新 HH:MM:SS`——它每秒都變,讓 FRAME != LAST_FRAME 恆真,
#   於是每 4 秒全幀重畫一次,把 diff 機制存在的唯一理由抵銷掉。
#   任何「腳本還活著」的指示都不要印在這裡,那是心跳的事不是渲染的事。
. "$(dirname "$0")/cockpit-env.sh"
while true; do
  [ "${EZ2_ONESHOT:-0}" = "1" ] || printf '\033[H'
  if [ -n "${TMUX_PANE:-}" ]; then
    read -r EZ2_PANE_W EZ2_PANE_H <<EOF
$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-44}" "${LINES:-30}")
EOF
    export EZ2_PANE_W EZ2_PANE_H
  fi
  "$EZ2_PY3" - <<'PY' | "$EZ2_PY3" "$EZ2_COCKPIT_DIR/pane-fit.py" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
import glob, json, os
from render.render_lib import clean_data

tasks = []
for path in glob.glob(os.path.join(os.environ.get("EZ2_TASKS_DIR", ""), "*.json")):
    try:
        tasks.append(clean_data(json.load(open(path, encoding="utf-8"))))
    except Exception:
        pass
terminal = {"done", "cancelled", "closed", "abandoned"}
running = [t for t in tasks if t.get("status") not in terminal]
done = sum(1 for t in tasks if t.get("status") in terminal)
print()
print(" 任務 %d/%d" % (done, len(tasks)))
print()
print(" 進行中:")
for task in sorted(running, key=lambda item: item.get("id", "")):
    print(" * " + str(task.get("id", "?")))
    print("    " + str(task.get("status") or "unknown"))
    print("    " + str(task.get("assignee") or "-"))
    print("    " + str(task.get("title") or ""))
PY
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  sleep 5
done
