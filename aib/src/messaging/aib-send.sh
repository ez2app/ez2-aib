#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.7 訊息咽喉:過血緣律才准送。
#
#   aib-send.sh --verb send|dm|broadcast|reply|report --to <對象> --body <內容>
#               [--sender-json JSON | --sender-uuid UUID] [--execute]
#
# 閘門設在咽喉不設在善意(原理 5):要送訊息就得經過這裡,判定由
# lineage.py 出,本腳本只負責「不過就不送」與「過了才轉交真通道」。
#
# 預設 dry-run:ACB 是生產通道,送出去就收不回來,--execute 才真送。
# 判定為 deny 時,即使帶 --execute 也不送 —— 閘門在前,旗標在後。
#
# rc(本咽喉層;對外那一份在 src/cockpit/dm-ctl.sh 檔頭):
#   0=判定 allow 且(帶 --execute 時)引擎回 0
#   1=血緣 deny —— **一個字都沒進目標**(判定在 load-buffer 之前)
#   2=用法錯/缺 --tmux-window —— 同上,零進入
#   3=R30 lint 擋 —— 同上,零進入
#   引擎(tools/cockpit-ops/aib-dm.sh)的碼原樣往上拋:4=BIND 或卡稿守衛
#   (零進入)、5=貼進去了但**未確認在框、未送 Enter**(**非零進入**)。
# ★★ 2026-08-22(SPEC-dm-auto-enter-0821 §三,the maintainer 直令):上面這個「零進入 /
#   非零進入」的分野**以前沒有寫在任何地方**,於是 dm-ctl 把 1/2/3/4 全部翻成
#   對外的 rc=2,而 rc=2 的契約逐字是「一個字都沒進目標,可安全重送」。
#   實況:08-21 有兩席框內確實有 `[Pasted …]`(送方的稿),只是沒按 Enter,
#   而 aib dm 回 2 —— **照契約字面該重送,而重送會疊成兩份**。
#   → 對外契約已拆出 rc=4(進了沒送出/框內狀態不可判,**絕不重送**);
#     dm-ctl 不再靠「原 rc 是幾」猜,而是**貼後回讀目標端**證明零新增才給 2。
#   ★ 只改工具而契約字面沒改,下一個讀契約的人仍會照舊重送——故兩者同批。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY3="${EZ2_PY3:-}"
if [ -z "$PY3" ]; then
  for p in /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$p" ]; then PY3="$p"; break; fi
  done
fi
PY3="${PY3:-python3}"

# ★ 08-21 車道 C(a seat 交付):ACB session registry 這條身份來源**已廢**。
#   廢的理由不是「舊」,是它從來沒有被走過(閘門三段卡在第一段:存在→被經過→
#   響了有人聽):唯一的生產呼叫點 ctl-send-message.sh:63 條件是
#   `[ -n "${EZ2_SESSION_UUID:-}" ]`,而活艙的啟動命令逐字含 `env -u
#   EZ2_SESSION_UUID`,`tmux show-environment` 回 `-EZ2_SESSION_UUID`(明確 unset)。
#   來源本身也早就停了:~/.acb/sessions/by-id 最新一筆 08-11,十天未更新。
#   ★ 廢掉的是**來源路徑**,不是 --sender-uuid 這個能力:登記簿目錄改成
#     EZ2_AIB_REGISTRY_DIR 且**沒有預設**——不猜路徑,沒設就是查不到身份,
#     fail-closed 不送(deny identity_not_found)。
#   ★ 要把它接到 AIB 身份(~/.ez2-aib/identity/sessions/*.json)的人先讀這段:
#     那批紀錄 schema=role-model-session-v1,**沒有 registry_verified 欄位**,
#     而 lineage.py:97-99 對缺欄是嚴格 fail-closed(registry_field_absent),
#     且 lineage.py:25 明寫「不從心跳/檔案存在這類旁證推論」。所以接的前置是
#     **先定義欄位映射並讓它有真的依據**,不是把路徑指過去就好——
#     把 trust=self_reported 映成 registry_verified 等於自己發給自己一張證書。
REGISTRY_DIR="${EZ2_AIB_REGISTRY_DIR:-}"
# 通道更換(2026-08-14,aib dm 刀):ACB 送信段廢(acb 已死,EM 軸拆除),
# 真通道=v2/v3 DM 引擎(tools/cockpit-ops/aib-dm.sh,fail-closed 五守衛+
# 卡稿偵測)。--to 仍是血緣判定的對象;實體投遞落點由 --tmux-window 指定。
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
DM_ENGINE="$REPO_ROOT/tools/cockpit-ops/aib-dm.sh"
# 每次判定都留痕:設了帳本就入帳(擋下的嘗試尤其要留,否則越權查無此事)
LEDGER="${EZ2_AIB_LEDGER:-}"
LEDGER_ARGS=()
[ -n "$LEDGER" ] && LEDGER_ARGS=(--ledger "$LEDGER")

VERB=""; TO=""; BODY=""; SENDER_JSON=""; SENDER_UUID=""; EXECUTE=0
TMUX_SOCKET="${EZ2_AIHUB_SOCKET:-aihub}"; TMUX_WINDOW=""

while [ $# -gt 0 ]; do
  case "$1" in
    --verb) VERB="${2:-}"; shift 2 ;;
    --to) TO="${2:-}"; shift 2 ;;
    --body) BODY="${2:-}"; shift 2 ;;
    --sender-json) SENDER_JSON="${2:-}"; shift 2 ;;
    --sender-uuid) SENDER_UUID="${2:-}"; shift 2 ;;
    --tmux-socket) TMUX_SOCKET="${2:-}"; shift 2 ;;
    --tmux-window) TMUX_WINDOW="${2:-}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$VERB" ] || { echo "需要 --verb" >&2; exit 2; }

# R-30 前置 lint(E2.4):**在身份判定之前**跑 —— 內容不合規就沒有「誰可以送」
# 的問題了,而且先擋掉可以少一次身份查詢。違規一律不送,沒有繞過旗標:
# 若允許 --force,那條路遲早變成常態,R-30 就名存實亡。要送就把字改掉
# (--fix 會列出可自動替代的部分,但不自動改你的內容)。
if [ -n "$BODY" ]; then
  set +e
  "$PY3" "$HERE/r30_lint.py" --text "$BODY"
  LINT_RC=$?
  set -e
  if [ "$LINT_RC" != "0" ]; then
    echo '{"decision":"deny","code":"r30_lint","reason":"R-30 lint 擋下,訊息未送出"}'
    exit 3
  fi
fi

if [ -n "$SENDER_JSON" ]; then
  set +e
  RESULT="$("$PY3" "$HERE/lineage.py" --verb "$VERB" --target "$TO" \
            --sender-json "$SENDER_JSON" ${LEDGER_ARGS[@]+"${LEDGER_ARGS[@]}"})"
  RC=$?
  set -e
elif [ -n "$SENDER_UUID" ]; then
  if [ -z "$REGISTRY_DIR" ]; then
    # 沒有登記簿=查不到這個 uuid 的身份。看不見來源就不准送(原理 10 的閘門側)。
    # 語意與 lineage.py 查無此人時一致,額外帶 retired_channel 說明**為什麼**沒有來源。
    RESULT='{"decision":"deny","code":"identity_not_found","retired_channel":"acb_session_registry","reason":"沒有身份登記簿:ACB registry 通道已廢,EZ2_AIB_REGISTRY_DIR 未設(不猜路徑)。改用 --sender-json,或先完成 registry_verified 欄位映射再接新來源。"}'
    RC=1
  else
    set +e
    RESULT="$("$PY3" "$HERE/lineage.py" --verb "$VERB" --target "$TO" \
              --sender-uuid "$SENDER_UUID" --registry-dir "$REGISTRY_DIR" \
              ${LEDGER_ARGS[@]+"${LEDGER_ARGS[@]}"})"
    RC=$?
    set -e
  fi
else
  echo "需要 --sender-json 或 --sender-uuid" >&2
  exit 2
fi

echo "$RESULT"

if [ "$RC" != "0" ]; then
  # deny:一個字都不送。理由碼在 RESULT 裡,給人看也給機器讀。
  exit "$RC"
fi

# ★兩層預設刻意不同(整合席核可入註):CLI 層(aib dm)真送是 DM 的 UX;
# **本咽喉層預設 dry-run+deny 不送的原語意一行不動**——未來誰想「統一」
# 這兩個預設,先讀這行:統一即拆掉閘門的保險。
if [ "$EXECUTE" = "1" ]; then
  [ -n "$TMUX_WINDOW" ] || { echo "需要 --tmux-window(真通道=DM 引擎,ACB 段已廢)" >&2; exit 2; }
  AIB_DM_SOCKET="$TMUX_SOCKET" bash "$DM_ENGINE" "$TMUX_WINDOW" "$BODY"
else
  # 刻意不用 printf '%q' 包內容:macOS 內建 bash 3.2 對非 ASCII 會吐出
  # 混雜八進位跳脫的非法 UTF-8(中文訊息一律變亂碼,而中文是常態)。
  # 預覽是給人看的,原樣印比 shell-escape 正確。
  printf 'DRY-RUN: dm-engine %s -> %s(%s)\n' "$TMUX_SOCKET" "$TO" "${TMUX_WINDOW:-未指定窗}"
  printf '%s\n' "$BODY"
fi
