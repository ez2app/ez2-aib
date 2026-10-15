#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# aib-reply — 對 the maintainer 的回覆預設路徑:渲染 → 落回報檔 → tmux popup 播流光與羽翼呼吸 → 印路徑。
#
# 用法:bash tools/report/aib-reply.sh <輸入.json|->
#
# ★ 為什麼播放參數住在這裡而不是 aib-report.py 的常數:
#   tests/test-report-shimmer-r2b.py 把「3 趟 = 135 幀」寫死在 9 個斷言裡,
#   改模組常數會弄紅別人的尺。這一層是呼叫者,不是常數的第二份拷貝。
# ★ 趟數為什麼是 2:呼吸週期 3 s = 90 幀 = 剛好 2 趟。停在 2 趟末尾時羽翼與 ♡
#   與定格逐位元組相同(量過);預設 3 趟停在 1.5 圈,也就是停在呼吸峰上,
#   收尾會看到羽翼從玫瑰啪一下跳回曜石灰。
# ★ 播不了不是靜默退預設:每一條失敗路徑在 stderr 說自己的名字,並寫進留痕。
set -uo pipefail

PASSES="${EZ2_AIB_REPLY_PASSES:-2}"
SECONDS_PER_PASS="${EZ2_AIB_REPLY_SECONDS:-0.75}"
POPUP="${EZ2_AIB_REPLY_POPUP:-on}"
# 10-08 the maintainer:流光播完不要馬上關 —— 接 reply_viewer:右上 [X 關閉]、最上方倒數(預設 30 秒)到 0 自動關,上下捲動就取消倒數。
VIEW_SECONDS="${EZ2_AIB_REPLY_VIEW_SECONDS:-30}"
PENDING="${HOME}/.ez2-aib/pending"
LOG="${HOME}/.ez2-aib/logs/aib-reply.jsonl"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ "$#" -lt 1 ]; then
  echo "用法:bash tools/report/aib-reply.sh <輸入.json|->" >&2
  exit 64
fi
IN="$1"

mkdir -p "${PENDING}" "$(dirname "${LOG}")"
TMP="$(mktemp "${TMPDIR:-/tmp}/aib-reply.XXXXXX")"

python3 "${REPO}/tools/report/aib-report.py" "${IN}" > "${TMP}"
RC=$?
if [ "${RC}" -ne 0 ]; then
  rm -f "${TMP}"
  echo "aib-reply: 渲染或 lint 未過(rc=${RC}),沒有落檔、沒有播放;理由在上面" >&2
  exit "${RC}"
fi

# 席碼由渲染出來的抬頭現抓,不手抄、不寫死(抬頭本身是 aib-hdr 由 ppid 鏈解出來的)
SEAT="$(sed -n '1p' "${TMP}" | grep -oE '@[0-9a-f]{5}' | head -1 | tr -d '@')"
if [ -z "${SEAT}" ]; then
  SEAT="seat-unresolved"
  echo "aib-reply: 抬頭裡抓不到席碼,檔名用 ${SEAT}(不猜、不掉回別的碼)" >&2
fi

OUT="${PENDING}/to-maintainer-from-${SEAT}-$(date +%m%d-%H%M%S).txt"
mv "${TMP}" "${OUT}"

PLAYED=false
WHY=""
if [ "${POPUP}" != "on" ]; then
  WHY="EZ2_AIB_REPLY_POPUP=${POPUP}"
elif [ -z "${TMUX:-}" ]; then
  WHY="不在 tmux 裡(TMUX 空)"
elif [ -z "${TMUX_PANE:-}" ]; then
  WHY="TMUX_PANE 空"
else
  SOCK="$(basename "${TMUX%%,*}")"
  if ! tmux -L "${SOCK}" display-message -p -t "${TMUX_PANE}" '#{pane_id}' >/dev/null 2>&1; then
    WHY="tmux -L ${SOCK} 打不到 ${TMUX_PANE}"
  else
    # ★ 10-06 the maintainer 截圖(02:05):popup 右下角、正在播的那條分隔線尾端有一塊灰框。那是 popup 內
    #   pty 的游標——流光只用 \r 重寫當前行,游標整段動畫都停在該行行尾,未聚焦 pane 的游標畫成灰實心塊。
    #   渲染器的位元組不能動(R0-1/R0-5/R2b 九個斷言釘著 tty 串流的形狀),所以在 popup 這一層
    #   用 DECTCEM 藏游標(ESC[?25l),播完還原(ESC[?25h),rc 原樣帶出。-E 關 popup 時 pty 一起消失,
    #   還原只為了同一條指令被人拿去非 popup 的 tty 跑時不留下沒游標的殼。
    tmux -L "${SOCK}" display-popup -E -t "${TMUX_PANE}" -w 92% -h 88% \
      "cd '${REPO}' && printf '\033[?25l'; EZ2_AIB_SWEEP_PASSES=${PASSES} EZ2_AIB_SWEEP_SECONDS=${SECONDS_PER_PASS} python3 tools/report/aib-report.py --replay; rc=\$?; python3 tools/report/reply_viewer.py --seconds ${VIEW_SECONDS}; printf '\033[?25h'; exit \$rc" \
      >/dev/null 2>&1 &
    # 10-08:display-popup 會等 popup 關掉才回來;加了看回報畫面(可開 30 秒以上)後放背景,席位不被卡住。
    #   監督:0.3 秒內就結束 = popup 沒開成 ⇒ 取它的 rc,非 0 照實記沒播;還在跑 = 已開出來(之後的 rc 席位拿不到)。
    _popup_pid=$!
    sleep 0.3
    if kill -0 "${_popup_pid}" 2>/dev/null; then
      PLAYED=true
    elif wait "${_popup_pid}"; then
      PLAYED=true
    else
      WHY="display-popup rc 非 0"
    fi
  fi
fi
if [ "${PLAYED}" != true ]; then
  echo "aib-reply: 流光沒播 —— ${WHY}(回報檔已經落好,rc 不受影響)" >&2
fi

python3 - "${LOG}" "${SEAT}" "${OUT}" "${PLAYED}" "${WHY}" "${PASSES}" "${SECONDS_PER_PASS}" <<'PY'
import json, sys, datetime
log, seat, out, played, why, passes, secs = sys.argv[1:8]
rec = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "seat": seat, "report_file": out, "played": played == "true",
       "why_not_played": why or None, "passes": int(passes), "seconds_per_pass": float(secs)}
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
PY

echo "回報檔:${OUT}"
