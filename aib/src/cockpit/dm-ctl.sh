#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# aib dm — 異質艦隊 DM CLI(2026-08-14 the maintainer 核准;E2.7 咽喉+v3 引擎接線)
#
#   aib dm <目標> [訊息...] [--file F] [--dry-run] [--verify]
#              [--thread <項號>/<字串>] [--kind order|rule|pipeline|deliver|decide|report|other|notice]
#              [--close <原信 q_ id>](10-13 S7-3:這封回信送達後,把自己佇列裡的原信標完成,證據 = 這封的 id)
#              [--expires <epoch> | --ttl-min <分鐘>]          ← 第 70 項 P3(接口 IFACE-dm-queue-v2 §1/§3)
#              [--retry-until <秒>] [--retry-every <秒>]       ← 被擋(rc=2)時重試;沒給 --retry-until 則行為與從前逐位元組相同
#              [--probe-box]                                  ← 只讀對方輸入框,印 box=empty|draft|placeholder|unknown,不貼不送
#              [--park-on-draft]                              ← 與 --retry-until 併用:到上限仍被草稿擋 ⇒ 入既有排序暫存
#   aib dm --to <目標,目標,…> [訊息...] [同上旗標]      ← EH.20 多目標
#   aib dm --resolve-only <目標>                       ← EH.20 階段 A 用,不碰目標端
#
# 目標單軌(B11):只認席位檔 session_uuid 尾五碼。
#   角色、窗號與 seat_id 能辨識時具名拒投；辨識不到不猜。
# 訊息取法優先序:argv > --file F > stdin(長派工單免轉義)。
#
# rc 契約(對外穩定,細分原因碼在輸出 JSON 行):
#   0=受理成功:
#     stage=delivered / queued → 已貼進對方輸入區(queued=對方引擎自己的佇列)
#     stage=sort-queued → 已寫入本機排序暫存,**尚未貼稿**;帶 queue_id。
#     呼叫端要看 stage,不能把 rc=0 當成「已經在對方框裡」。
#   2=一個字都沒進目標,**可安全重送**(R30 lint 擋/血緣 deny/卡稿/BIND 提示/
#     死窗/目標不明)——★ 給這個承諾之前必須**看過目標端**確認零新增,
#     只信 lint 或血緣閘自己的收據不算(那是寫在我這端的紙,不是對方框裡的事實)
#   3=已送未驗(Enter 已按或已貼但回讀不確定;**勿重貼**,會疊兩份)
#   4=**進了沒送出,或框內狀態不可判 —— 絕不重送**(2026-08-22 新增)
# ★★ 2 與 4 為什麼必須分開(SPEC-dm-auto-enter-0821 §三,the maintainer 直令):
#   舊契約逐字寫「rc=2 = 一個字都沒進目標」而**兩席框內確實有 `[Pasted …]`
#   (我的稿),只是沒按 Enter**,aib dm 照樣回 2 —— 照契約字面該重送,而重送
#   會疊成兩份。08-21 一晚咬了架構總管三次,最近一次是十分鐘前發開工令。
#   **兩者的處置相反(可重送 / 絕不重送),合成一個 rc 就是把兩件事講成一件。**
#   ★ 收據的失效值,剛好是它表示安全的那個值。
# 送達≠送到工作(HANDOFF 第十一形):rc0 只證按鍵落進對方輸入區並送出,
# 不證明對方讀了或做了;--verify 只是投遞後畫面抽查,不改變此語意。
#
# stdout 最後一行(第 70 項 P3,2026-09-14):
#   RESULT stage=<stage> id=<佇列 id> relay=<留痕檔絕對路徑> thread=<thread>
#   恰四鍵、鍵序固定、空值 `-`、零 ANSI;`--resolve-only` 不印。多目標 = 每席一行 + 最後一行
#   stage=batch。每一則(長短都算)都留痕:長訊在 pending(指標檔)、短訊在本艙
#   state/relay/<日期>/;兩者都寫一列進 state/relay/index.jsonl;dry-run 檔名尾 `.dry`。
#   id= 是這一則在佇列歷史裡的 id:忙碌入列時是 sort-queued 那一則;直送 rc0/3/4 之後由本檔呼叫
#   `dm_queue.py record` 記進歷史(delivered / hold_human),id 就是它;record 失敗 rc 不變、id=FAILED。
#   入佇列走 `dm_queue.py upsert`(廣播型同 thread 取代舊版);upsert 失敗**拒送 rc=2 不退回直送**。
#   細節在下面「RESULT 行」與「留痕」兩段;尺 tests/test-dm-result-line-p3.py。
#
# 流程:解析目標→推導發送席(**只可推導不可偽填**,無旗標可冒他席)→
# 自動標頭→交 E2.7 咽喉(R30 lint+血緣閘一行未動)→v3 引擎安全投遞。
set -uo pipefail

SOURCE="${BASH_SOURCE[0]}"
HERE="$(cd -P "$(dirname "$SOURCE")" && pwd)"
# 呼叫端有沒有自己指定艙,要在 cockpit-env 補預設值之前記下來。
# 沒指定才准自動改去席位真正活著的那一艙;指定了還是不猜。
_DM_SOCKET_WAS_SET=0
if [ -n "${EZ2_AIHUB_SOCKET+x}" ]; then
  _DM_SOCKET_WAS_SET=1
fi
_DM_ORIG_ARGS=("$@")
. "$HERE/cockpit-env.sh"

PY3="${EZ2_PY3:-python3}"
# 畫面字走四語字典(語系盤查第 14 條,10-14):_dmt <鍵> [名=值 …];每次照 i18n_lib.resolve_locale() 取。
#   python 起不來 ⇒ 印鍵名(可見即可抓),不讓投遞因為印不出字而失敗。
_dmt() {
  local key="$1"; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  "$PY3" "$HERE/i18n_lib.py" ${args[@]+"${args[@]}"} "$key" 2>/dev/null || printf '%s\n' "$key"
}
HUB="${EZ2_AIHUB_SOCKET:-aihub}"
# ★★ 席位登記簿由**上一行已經有的 HUB** 導出,不是只認 env(aihub-9 的 a seat
#   找到、the maintainer 08-18 17:36 親自查證;同族第五個呼叫點)。
#   實況:~/.ez2-aib/seats/w11.json 是 08-15 的舊登記(a seat),
#         ~/.ez2-aib/instances/13/seats/w11.json 才是現任(a seat)。
#   沒帶 EZ2_AIB_INSTANCE_HOME 時,dm-ctl 會拿**共用那份舊登記簿**去解席位。
#   ★ 它最毒的地方:訊息照樣送到正確的窗,只有**抬頭掛別人的名字**——
#     沒有錯誤、沒有 rc 非零、兩邊都不會發現,對話就這樣歸錯人。
#     **「@窗號送得出去」不能當成「定址是好的」。**
#   修法同 fleet_patrol._seats_dir(hub):順位 hub 導出 > INSTANCE_HOME > 主艙。
#   (aihub-9 建議「每次帶兩個環境變數」——那是暫行做法不是修法:
#    靠人記得帶的東西一定會有人忘記。)
_AIB_ROOT="${EZ2_AIB_HOME:-$HOME/.ez2-aib}"
case "$HUB" in
  aihub-*) _AIB_INST="${HUB#aihub-}" ;;
  *) _AIB_INST="" ;;
esac
case "$_AIB_INST" in *[!0-9a-zA-Z-]*) _AIB_INST="" ;; esac
if [ -n "$_AIB_INST" ]; then
  SEATS_DIR="$_AIB_ROOT/instances/$_AIB_INST/seats"
else
  # ★★ 2026-08-29 a seat 量到、**刻意沒有在本分支修**:
  #   這條「導不出艙就讀主艙那本」是真的洞 —— 實測 /tmp/tmux-501/ 六個 socket,
  #   `aihub` 與 `default` 都落到這一本,而 @2/@3 在兩邊同時是活窗。
  #   但它同時是四支既有測試(ed12 / dm-cli / dm-multitarget / receipt-contradiction)
  #   依賴的契約:它們用 `dmtest` 這種 hub 名而不指定登記簿。改它 = 契約變更,要另裁。
  #   ★ 不修它仍然安全,因為安全性由下面的**席位 socket 閘**承擔:
  #     席位檔記了 socket 之後,讀錯登記簿只會**拒投**,不會誤投。
  #     (亦即本行是衛生問題,不是安全問題;裁定請見交付單。)
  SEATS_DIR="${EZ2_AIB_INSTANCE_HOME:-$_AIB_ROOT}/seats"
fi
THROAT="$(cd "$HERE/../messaging" && pwd)/aib-send.sh"
# 狀態根(與下面留痕那段同一套順位;這裡先算一份給冒用留痕用,兩處必須同值——尺對帳)
_STATE_ROOT_EARLY="${EZ2_AIB_STATE_ROOT:-$_AIB_ROOT}"
if [ -n "$_AIB_INST" ]; then _STATE_ROOT_EARLY="$_STATE_ROOT_EARLY/instances/$_AIB_INST"
else _STATE_ROOT_EARLY="${EZ2_AIB_INSTANCE_HOME:-$_STATE_ROOT_EARLY}"; fi
# ★ 佔位符文法的**唯一**一份:本檔原本在 emit_json 裡自帶一份正則,而引擎
#   `aib-dm.sh` 另有兩份(BEFORE 一份、AFTER 一份)。同一支鏈上三份會分岔——
#   引擎的 AFTER 那份至今仍看不見無 `+K` 的單行型,而 BEFORE 那份看得見。
#   本刀新增的判準一律走這個模組,不再新增第四份。
DM_PASTE="$(cd "$HERE/../messaging" && pwd)/dm_paste.py"
COMPOSER_DIALECT="$HERE/composer_dialect.py"

die() { echo "aib-dm-ctl: $1" >&2; exit "${2:-2}"; }

# ══════════════════════════════════════════════════════════════════════════
# RESULT 行(第 70 項 P3;來源 OPT-pending-review-inbox §一病 5 / §七 7.2)
#
# 病:送信結束那一行 JSON 鍵序不固定、人話行夾著色碼,寄件人用 grep 撈結果
#   撈不到就以為沒送,同一則送了兩次(09-12 13:46/13:47 實例)。
# 解:**每一條結束路徑**的 stdout 最後一行固定是
#     RESULT stage=<stage> id=<queue id> relay=<留痕檔絕對路徑> thread=<thread>
#   恰四鍵、鍵序固定、零 ANSI、空值印 `-`;值裡的空白/換行/`%` 以 %XX 逸出
#   (CJK 原樣,人讀得懂、機器 split 不會斷)。既有 `{"rc_contract":…}` 那行
#   **照印不動**(舊消費者掃它),本行是加在後面的,不取代。
# ★ 用 EXIT trap 做,不逐條 exit 補:這支檔有 40 多個 exit 點,逐條補一定漏,
#   而漏掉的那一條在版面上長得跟「這條路不出 RESULT」一模一樣。
# ★ `--resolve-only` 不印:那是多目標行程的階段 A 內部再入,不是一次送信;
#   它的 stdout 由父行程逐行找 JSON,多一行無害但語意上不該有「結果」。
# ★ 多目標 `--to a,b`:子行程各印自己的 RESULT(父行程只濾 rc_contract 行,
#   RESULT 行原樣透出),父行程最後再印一行 stage=batch ⇒ N+1 行,最後一行是 batch。
# ★ 沒走到任何 stage 就退出(用法錯、die 在解析前)⇒ stage=abort;不印 `-`,
#   `-` 留給「這一格結構上沒有值」,abort 是「有值但它是失敗」。
_RESULT_STAGE="-"; _RESULT_ID="-"; _RESULT_RELAY="-"; _RESULT_THREAD="-"
_RESULT_DONE=0
_RECORD_ELIGIBLE=0
# 直送之後把這一則記進佇列歷史(接口 §3 record;P3 第二包)。住在 EXIT trap 裡的理由與 RESULT 行同:
# 投遞的結束點有十幾個,rc 在那一刻才定。**record 失敗不改 dm-ctl 的 rc**(rc 講投遞不講留痕),
# 只在 stderr 具名一行、RESULT `id=FAILED`(`-`=沒嘗試、`<qid>`=記成了、FAILED=試了沒成;與 relay 同形)。
# 抬頭標籤 → 佇列的 sender_kind。★ 單一資料源。
#   這條判準本來有**三份逐字拷貝**(_park_on_draft、主入佇列、貼後入佇列),
#   而第四個寫入點吃前面設好的變數。新增一種標籤時只改到其中一份 ⇒ 另外兩條路
#   靜靜落回 unknown,**而 unknown 正是「這封信沒帶身分」的長相** —— 失效值與
#   它表示安全的那個值同形。所以這裡收成一支,呼叫端不再自己 case。
_dm_queue_kind() {
  case "${1:-}" in
    @*)             printf 'ai\n' ;;
    human:*)        printf 'human\n' ;;
    seat_claimed:*) printf 'seat_claimed\n' ;;
    *)              printf 'unknown\n' ;;
  esac
}

_record_delivery() {   # $1=rc
  local rc="$1" status out qid
  local _rec_extra=()
  # ★ 第 159 項:重送那一輪(AIB_DM_RESEND=1)再回 2 ⇒ 轉待人處理(hold_human,rc=2,attempts=1);
  #   第一輪的 2 不記(它會被重送,記了就是同一則兩筆)。其餘 rc 的記法不變。
  case "$rc" in
    0) status=delivered ;;
    3|4) status=hold_human ;;
    2) [ "${AIB_DM_RESEND:-0}" = "1" ] || return 0; status=hold_human; _rec_extra=(--attempts 1) ;;
    *) return 0 ;;
  esac
  [ -n "${T_CODE:-}" ] || return 0
  [ -f "$HERE/dm_queue.py" ] || return 0
  out="$(printf '%s' "$MSG" | "$PY3" "$HERE/dm_queue.py" record \
      --seat "$T_CODE" --sender-label "${S_LABEL:-}" --sender-kind "${_Q_KIND:-unknown}" \
      --session-uuid "${_Q_UUID:-}" --wid "${T_WID:-}" --provider "${T_PROVIDER:-}" \
      --priority "${AIB_DM_QUEUE_PRIORITY:-normal}" --status "$status" --rc "$rc" \
      ${_rec_extra[@]+"${_rec_extra[@]}"} \
      ${_V2_ARGS[@]+"${_V2_ARGS[@]}"} 2>/dev/null)" || out=""
  qid="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("id") or "")' "$out" 2>/dev/null || true)"
  if [ -n "$qid" ]; then
    _RESULT_ID="$qid"
  else
    _RESULT_ID="FAILED"
    echo "aib-dm-ctl: $(_dmt aib.dmctl.record_fail status="$status")" >&2
  fi
  # 私訊對話區：只記真人發出且 delivered 的 DM（AI→AI 不寫）
  if [ "$status" = "delivered" ] && [ -n "${HUMAN_ID:-}" ]; then
    _dlg_cockpit="${EZ2_COCKPIT_STATE_DIR:-}"
    [ -n "$_dlg_cockpit" ] || _dlg_cockpit="${EZ2_AIB_INSTANCE_HOME:-}/cockpit"
    if [ -n "$_dlg_cockpit" ] && [ -d "$_dlg_cockpit" ] && [ -f "$HERE/dialog_log.py" ]; then
      printf '%s' "$MSG" | "$PY3" "$HERE/dialog_log.py" append         --cockpit "$_dlg_cockpit"         --human-id "$HUMAN_ID"         --to "${T_LABEL:-$T_CODE}"         --body-file /dev/stdin >/dev/null 2>&1 || true
    fi
  fi
}
_result_enc() {   # 單一值的逸出:空白/換行/tab/% → %XX;其餘原樣
  "$PY3" -c 'import sys
s = sys.argv[1]
out = []
for ch in s:
    if ch in " \t\n\r%":
        out.append("%%%02X" % ord(ch))
    else:
        out.append(ch)
print("".join(out) or "-")' "$1" 2>/dev/null || printf '%s\n' "-"
}
_result_line() {
  local _rc=$?
  [ "${_RESULT_DONE:-0}" = "1" ] && return 0
  _RESULT_DONE=1
  [ "${RESOLVE_ONLY:-0}" = "1" ] && return 0
  [ "${_RECORD_ELIGIBLE:-0}" = "1" ] && _record_delivery "$_rc"
  local _st="$_RESULT_STAGE"
  [ "$_st" = "-" ] && _st="abort"
  # ★ 第 159 項:重送那一輪多一鍵 ` resend=1`(只在重送輪出現;第一輪恰四鍵不變,消費者用 split 讀前四段照舊)。
  local _rs=""
  [ "${AIB_DM_RESEND:-0}" = "1" ] && _rs=" resend=1"
  printf 'RESULT stage=%s id=%s relay=%s thread=%s%s\n' \
    "$(_result_enc "$_st")" "$(_result_enc "$_RESULT_ID")" \
    "$(_result_enc "$_RESULT_RELAY")" "$(_result_enc "$_RESULT_THREAD")" "$_rs"
}
trap '_result_line' EXIT
# ── 第 159 項(pm a seat 12:1x 派;艦長 12:16 更正「04:56 那則 dm 消失」= 投遞端回 rc_contract=2「可安全重送」而沒人重送)──
# 收據說「目標端零新增,可安全重送」時,**自動重送一次**(上限 1、間隔 ≥30 秒、重送輪 RESULT 帶 resend=1);
# 再失敗 ⇒ 轉 hold_human 給人(record rc=2、attempts=1,見 _record_delivery)。
# ★ 只在兩個「可安全重送」出口:blocked 且回讀兩幀相同無殘留 / browse-mode。「不要自動重送」(兩幀不同)與 rc=4
#   不重送 —— 它們的失效方向是**重複投遞**,那比少送一次貴(缺陷十六:32 次重送目標端實收 3 份)。
# ★ 重送 = 再入本檔自己(同多目標的做法,不複製投遞的任何一段):稿走 stdin(第三順位取法),引數只有目標與分類旗標;
#   AIB_DM_RESEND=1 讓子輪(a)不再重送(上限 1)(b)不再留痕(第一輪已留,AIB_DM_RESEND_RELAY 帶路徑進 RESULT)(c)回 2 記 hold_human。
# ★ 父輪不印自己的 RESULT(_RESULT_DONE=1)也不 record:stdout 最後一行是子輪的 RESULT(P3 契約),歷史裡一則一筆。
# ★ 開關 AIB_DM_AUTO_RESEND **本檔預設關**,由入口 `aib dm`(src/cockpit/aib)打開:本檔被 43 支尺直接叫、也被排空器直接叫,
#   它們量的都是單次投遞的契約;要自動重送的是人與 AI 打 `aib dm` 那條路。重送子輪與多目標子輪繼承父輪的值。
# ★ 不重送:dry-run / 排空器重投(AIB_DM_SORT_DELIVER=1,佇列自己有 attempts 語意)/ 開關沒開。
# ★ 間隔:AIB_DM_RESEND_WAIT_S 預設 30;治具可設小,但預設值由尺釘住(test-dm-auto-resend-159 那一格讀本檔字面)。
AIB_DM_RESEND_WAIT_S_DEFAULT=30
_auto_resend() {   # $1=stage(收據的 stage,只給 stderr 用);回來 = 本輪不重送,呼叫端照原路印收據並 exit 2
  [ "${AIB_DM_AUTO_RESEND:-0}" = "1" ] || return 0
  [ "${AIB_DM_RESEND:-0}" != "1" ] || return 0            # 上限 1:重送輪自己不再重送
  [ "${DRYRUN:-0}" != "1" ] || return 0
  [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ] || return 0
  [ -n "${TARGET:-}" ] && [ -n "${MSG:-}" ] || return 0
  local _wait="${AIB_DM_RESEND_WAIT_S:-$AIB_DM_RESEND_WAIT_S_DEFAULT}"
  case "$_wait" in ''|*[!0-9]*) _wait="$AIB_DM_RESEND_WAIT_S_DEFAULT" ;; esac
  echo "aib-dm-ctl: $(_dmt aib.dmctl.auto_resend stage="$1" seconds="${_wait}")" >&2
  # 留痕在 session 之外(艦長 12:16 那課:證據只活在寄件人 session 裡等於沒有):一行 JSON 進 state/dm-resend.jsonl;寫不進只吵不擋。
  local _rlog="${AIB_DM_RESEND_LOG:-${_STATE_ROOT:-$_AIB_ROOT}/state/dm-resend.jsonl}"
  mkdir -p "$(dirname "$_rlog")" 2>/dev/null
  printf '{"ts":%s,"target":"%s","stage":"%s","wait_s":%s,"relay":"%s","pid":%s}\n' \
    "$(date +%s)" "$TARGET" "$1" "$_wait" "${_RESULT_RELAY:--}" "$$" >> "$_rlog" 2>/dev/null \
    || echo "aib-dm-ctl: $(_dmt aib.dmctl.resend_log_fail path="$_rlog")" >&2
  [ "$_wait" = "0" ] || sleep "$_wait"
  local _argv=("$TARGET")
  [ -n "${DM_THREAD:-}" ] && _argv+=(--thread "$DM_THREAD")
  [ -n "${DM_KIND:-}" ] && _argv+=(--kind "$DM_KIND")
  [ -n "${DM_CLOSE:-}" ] && _argv+=(--close "$DM_CLOSE")
  [ -n "${DM_EXPIRES:-}" ] && _argv+=(--expires "$DM_EXPIRES")
  [ -n "${DM_TTL_MIN:-}" ] && _argv+=(--ttl-min "$DM_TTL_MIN")
  [ "${VERIFY:-0}" = "1" ] && _argv+=(--verify)
  # 跨主機收件那一則:重送輪也要帶寄件機的身分(子輪自己再驗一次;不帶就退回未認證,真機 Mini→Pro 量到)
  [ -n "${XHOST_IDENT:-}" ] && _argv+=(--xhost-ident "$XHOST_IDENT")
  [ -n "${_ENG_LOG:-}" ] && rm -f "$_ENG_LOG"
  _RESULT_DONE=1                                           # 父輪不印 RESULT、不 record;子輪的算數
  # env -u:父輪引擎 export 的貼前佔位符不得帶進子輪(子輪要自己抓;帶進去的是另一幀的事實)
  # `bash "$0"` 不是 `"$0"`:本檔沒有執行位元,入口 aib 與所有尺都是 `bash dm-ctl.sh` 叫它(直接 exec 會 rc=126)。
  printf '%s' "$MSG" | env -u AIB_DM_BEFORE_PLACEHOLDERS AIB_DM_RESEND=1 AIB_DM_RESEND_RELAY="${_RESULT_RELAY:--}" bash "$0" "${_argv[@]}"
  exit $?
}
# 暫存檔一律走這裡(可寫性探測),不准自己推導 ${TMPDIR:-…}——沙箱下 mktemp 會 rc=1 而失敗被吞
# (test-dm-fingerprint.py TmpdirDerivationChainNail 釘整條 dm 鏈)。$1=檔名前綴,預設 aib-dm-eng。
# ★ 09-25 a seat 從檔尾搬到這裡:--retry-until(_retry_until_loop,呼叫點在引擎區塊之前)也要用它,
#   而 bash 函式是**呼叫時**才解析——定義若還在檔尾,重試那條路叫到它時它還不存在。
_dm_ctl_tmp() {
  local d p="${1:-aib-dm-eng}"
  for d in "${AIB_DM_STATE_DIR:-}" "${TMPDIR:-}" /tmp; do
    [ -n "$d" ] || continue
    ( : > "$d/.aib-dm-w.$$" ) 2>/dev/null || continue
    rm -f "$d/.aib-dm-w.$$" 2>/dev/null
    mktemp "$d/$p.XXXXXX" 2>/dev/null && return 0
  done
  return 1
}

# ── --retry-until:被對方草稿擋住(rc=2)時,在上限內自動重送 ────────────────
# 今晚實例:送一席第 1 次被卡稿守衛擋(rc_contract=2,stage=blocked,可安全重送),
# 手寫背景迴圈每 30 秒才進。這件事不該靠人記得。
# 語意(既有 rc 契約一字不改):
#   rc=0         ⇒ 停,成功
#   rc=2         ⇒ 可安全重送,等 --retry-every(預設 30)再試,直到 --retry-until
#   rc=3 或 4    ⇒ 立刻停,絕不重送(3=已送未驗、4=進了沒送出)
#   到上限仍 2   ⇒ 回 2,並逐字印「重試 N 次仍被擋,放棄」
# 每次嘗試印一行:第幾次、rc、stage。沒給 --retry-until ⇒ 本函式不會被叫。
# 子輪關第 159 項自動重送(AIB_DM_AUTO_RESEND=0):上限與間隔由本旗標管,不再疊一次 30 秒。
# 治具:AIB_DM_RETRY_ONCE_CMD 取代子輪(假目標回指定 rc);不准拿它送真 dm。
_retry_until_loop() {
  local _until="$RETRY_UNTIL" _every="${RETRY_EVERY:-30}" _n=0
  local _start _now _deadline _left _wait _once_rc _stage
  local _outf _errf
  local -a _argv
  case "$_until" in ''|*[!0-9]*) die "--retry-until 必須是秒數(非負整數):$_until" 2 ;; esac
  case "$_every" in ''|*[!0-9]*) die "--retry-every 必須是秒數(非負整數):$_every" 2 ;; esac
  _start="$(date +%s)"
  _deadline="$((_start + 10#$_until))"
  if [ "${TO_GIVEN:-0}" = "1" ]; then
    _argv+=(--to "$TO_RAW")
  else
    _argv+=("$TARGET")
  fi
  [ -n "${DM_THREAD:-}" ] && _argv+=(--thread "$DM_THREAD")
  [ -n "${DM_KIND:-}" ] && _argv+=(--kind "$DM_KIND")
  [ -n "${DM_CLOSE:-}" ] && _argv+=(--close "$DM_CLOSE")
  [ -n "${DM_TTL_MIN:-}" ] && _argv+=(--ttl-min "$DM_TTL_MIN")
  [ -z "${DM_TTL_MIN:-}" ] && [ -n "${DM_EXPIRES:-}" ] && _argv+=(--expires "$DM_EXPIRES")
  [ "${VERIFY:-0}" = "1" ] && _argv+=(--verify)
  [ "${DRYRUN:-0}" = "1" ] && _argv+=(--dry-run)
  [ -n "${XHOST_IDENT:-}" ] && _argv+=(--xhost-ident "$XHOST_IDENT")   # 跨主機收件:每一輪都帶寄件機身分
  _RESULT_DONE=1
  _outf="$(_dm_ctl_tmp aib-dm-retry-out)" || die "--retry-until 暫存檔開不了" 2
  _errf="$(_dm_ctl_tmp aib-dm-retry-err)" || die "--retry-until 暫存檔開不了" 2
  while true; do
    _n="$((_n + 1))"
    : > "$_outf"
    : > "$_errf"
    if [ -n "${AIB_DM_RETRY_ONCE_CMD:-}" ]; then
      "$AIB_DM_RETRY_ONCE_CMD" >"$_outf" 2>"$_errf"
      _once_rc=$?
    else
      printf '%s' "$MSG" | env -u AIB_DM_BEFORE_PLACEHOLDERS \
        AIB_DM_RETRY_INNER=1 AIB_DM_AUTO_RESEND=0 \
        bash "$0" "${_argv[@]}" >"$_outf" 2>"$_errf"
      _once_rc=$?
    fi
    cat "$_errf" >&2
    _stage="$("$PY3" -c '
import json, re, sys
text = sys.stdin.read()
stage = "-"
for ln in text.splitlines():
    s = ln.strip()
    if s.startswith("{") and "rc_contract" in s:
        try:
            d = json.loads(s)
        except Exception:
            d = {}
        if d.get("stage"):
            stage = str(d.get("stage"))
    m = re.match(r"RESULT stage=(\S+)", s)
    if m:
        stage = m.group(1)
print(stage)
' < "$_outf" 2>/dev/null)" || _stage="-"
    [ -n "$_stage" ] || _stage="-"
    echo "aib-dm-ctl: $(_dmt aib.dmctl.try_n n="${_n}" rc="${_once_rc}" stage="${_stage}")" >&2
    case "$_once_rc" in
      0)
        cat "$_outf"
        rm -f "$_outf" "$_errf"
        exit 0
        ;;
      3|4)  # retry-until:never-resend-3-or-4
        cat "$_outf"
        rm -f "$_outf" "$_errf"
        exit "$_once_rc"
        ;;
    esac
    _now="$(date +%s)"
    if [ "$_now" -ge "$_deadline" ]; then
      if [ "${PARK_ON_DRAFT:-0}" = "1" ]; then
        echo "aib-dm-ctl: $(_dmt aib.dmctl.retry_park n="${_n}")" >&2
        rm -f "$_outf" "$_errf"
        printf '%s' "$MSG" | env -u AIB_DM_BEFORE_PLACEHOLDERS -u AIB_DM_RETRY_ONCE_CMD \
          AIB_DM_RETRY_INNER=1 AIB_DM_AUTO_RESEND=0 AIB_DM_PARK_NOW=1 \
          bash "$0" "${_argv[@]}"
        exit $?
      fi
      echo "$(_dmt aib.dmctl.retry_give_up n="${_n}")" >&2
      cat "$_outf"
      rm -f "$_outf" "$_errf"
      exit 2
    fi
    _left="$((_deadline - _now))"
    _wait="$_every"
    [ "$_wait" -gt "$_left" ] && _wait="$_left"
    [ "$_wait" -gt 0 ] && sleep "$_wait"
  done
}
# ── --probe-box:只讀對方輸入框,不貼不送不改佇列 ──────────────────────────
# 判準與引擎同源:composer_state.py(貼前輸入框)+佔位符正則(貼前佔位符)。
# rc:0=empty、3=draft、5=placeholder、4=unknown(量不到 ≠ 空)。
_probe_box() {
  _RESULT_DONE=1
  local _screen _plain _ph _cs _box _prc
  _screen="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)" || {
    echo "box=unknown"
    exit 4
  }
  _plain="$(printf '%s' "$_screen" | LC_ALL=C sed $'s/\033\[[0-9;]*m//g')"
  _ph="$(printf '%s\n' "$_plain" \
    | grep -Eo -- '\[Pasted text #[0-9]+( \+[0-9]+ lines?)?\]|\[Pasted: [0-9]+ lines?\]|\[Pasted: [0-9]+ KB\]|\[Pasted: [0-9]+(\.[0-9]+)? MB\]|\[Paste #[0-9]+ - [0-9]+ lines?\]|\[Saved pasted content to workspace \([0-9]+(\.[0-9]+)? KB\) id=[0-9]+\]' \
    | tr '\n' ' ')"
  _ph="${_ph% }"
  # R7(09-25 a seat):與引擎同一條判準——橋接席只看游標那一行;量不到游標就不給(行為不變)。
  local _cy
  _cy="$(hub_tmux display-message -p -t "$T_WID" '#{cursor_y}' 2>/dev/null)" || _cy=""
  case "$_cy" in ''|*[!0-9]*) _cy="" ;; esac
  _cs="$("$PY3" "$HERE/composer_state.py" --stdin ${T_PROVIDER:+--provider "$T_PROVIDER"} ${_cy:+--cursor-y "$_cy"} <<< "$_screen" 2>/dev/null)" || _cs=""
  [ -n "$_cs" ] || _cs="unknown"
  if [ -n "$_ph" ]; then
    _box=placeholder; _prc=5
  else
    case "$_cs" in
      empty|suggestion|n/a) _box=empty; _prc=0 ;;
      draft) _box=draft; _prc=3 ;;  # probe-box:draft-is-draft
      *) _box=unknown; _prc=4 ;;
    esac
  fi
  echo "box=${_box}"
  exit "$_prc"
}
# ── --park-on-draft:重試上限後把這則寫進既有排序暫存 ────────────────────
_park_on_draft() {
  [ -n "${T_CODE:-}" ] || die "停放失敗:沒有目標碼" 2
  [ -f "$HERE/dm_queue.py" ] || die "停放失敗:沒有 dm_queue.py" 2
  local _q_kind=unknown _q_raw _q_take _q_id _q_uuid
  _q_kind="$(_dm_queue_kind "${S_LABEL:-}")"
  _q_uuid="$("$PY3" -c 'import json,sys
print(((json.loads(sys.argv[1]).get("matches") or [{}])[0].get("rec") or {}).get("session_uuid") or "")' \
    "${RESOLVED:-{}}" 2>/dev/null || true)"
  _q_raw="$(printf '%s' "$MSG" | "$PY3" "$HERE/dm_queue.py" upsert \
      --seat "$T_CODE" --sender-label "${S_LABEL:-}" --sender-kind "$_q_kind" \
      --session-uuid "${_q_uuid:-}" --wid "${T_WID:-}" --provider "${T_PROVIDER:-}" \
      --hub "$HUB" --priority "${AIB_DM_QUEUE_PRIORITY:-normal}" --hold yes \
      ${_V2_ARGS[@]+"${_V2_ARGS[@]}"} 2>&1)" || true
  _q_take="$("$PY3" -c '
import json,sys
raw=sys.argv[1]
for ln in raw.splitlines():
    t=ln.strip()
    if not t.startswith("{"):
        continue
    try:
        o=json.loads(t)
    except ValueError:
        continue
    if isinstance(o, dict) and o.get("take"):
        print(o.get("take") or "")
        break
' "$_q_raw" 2>/dev/null || true)"
  _q_id="$("$PY3" -c '
import json,sys
raw=sys.argv[1]
for ln in raw.splitlines():
    t=ln.strip()
    if not t.startswith("{"):
        continue
    try:
        o=json.loads(t)
    except ValueError:
        continue
    if isinstance(o, dict) and "take" in o:
        rec=o.get("receipt") or {}
        print(rec.get("queue_id") or o.get("id") or "")
        break
' "$_q_raw" 2>/dev/null || true)"
  if [ "$_q_take" != "queue" ] || [ -z "$_q_id" ]; then
    echo "aib-dm-ctl: $(_dmt aib.dmctl.queue_not_taken state="${_q_take:-none}" raw="$_q_raw")" >&2
    die "重試後仍被擋,且排序暫存未收下" 2
  fi
  echo "aib-dm-ctl: $(_dmt aib.dmctl.queued_id id="$_q_id")" >&2
  printf '%s\n' "$_q_raw" | grep -v '^{' || true
  emit_json 0 sort-queued "已排入暫存,尚未送到對方輸入框"
  _RESULT_STAGE="sort-queued"
  _RESULT_ID="$_q_id"
  exit 0
}
_missing_seat_hint() {
  local code="${TARGET#@}" hint found="" d f
  hint="查無座位碼,可能已收席:看 ~/.ez2-aib/pending/HANDOFF-*<碼>*"
  for d in "${AIB_DM_RELAY_DIR:-}" "${EZ2_AIB_HOME:-$HOME/.ez2-aib}/pending" "$HOME/.ez2-aib/pending"; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    for f in "$d"/HANDOFF-*-"$code"-*; do
      [ -f "$f" ] || continue
      found="$(basename "$f")"
      break 2
    done
  done
  [ -n "$found" ] && hint="查無座位碼,可能已收席:看 ~/.ez2-aib/pending/$found"
  printf '%s\n' "$hint"
}
# ── L8b:被擋席自己得不到任何訊號,這一段補它 ───────────────────────────────
# 09-05 04:39–09:09 實測:cap2 的框被別席 04:39 的稿卡住,全艙 150+ 次 dm 被拒、
# 4.5 小時。送件席每次都拿到具名 rc=2,**被擋席一個訊號都沒有**。
# ★ 這裡只寫檔,**一個位元組都不碰目標的輸入框** —— 碰框正是這個故障的成因。
# ★ 寫失敗只在 stderr 說一句,不改本次 dm 的 rc:回饋器把 dm 擋住的話,
#   就從「通知被擋的人」變成「多擋一個人」。
_COMPOSER_NOTICE="$HERE/composer_notice.py"
_notice_blocked() {   # $1=被擋席五碼 $2=reason $3=可選 卡稿主五碼
  [ -n "${1:-}" ] || return 0
  [ -f "$_COMPOSER_NOTICE" ] || return 0
  local _owner=()
  [ -n "${3:-}" ] && _owner=(--draft-owner "$3")
  "$PY3" "$_COMPOSER_NOTICE" write --seat "$1" --reason "$2" \
      --sender "${AIB_DM_SELF_CODE:-unknown}" ${_owner[@]+"${_owner[@]}"} 2>/dev/null || \
      echo "aib-dm-ctl: $(_dmt aib.dmctl.notice_write_fail)" >&2
  return 0
}
# 第 55 項:從引擎 log 讀機器可讀的擋下種類。回 browse-mode / foreign-draft / 空(不是被框擋的)。
# ★ 先認 `block_kind=`(引擎印的機器字面),沒有才退到散文比對(舊引擎);瀏覽模式**不是卡稿**,不得寫成 foreign-draft。
_engine_block_kind() {   # $1=引擎 log 檔
  local text
  text="$(cat "$1" 2>/dev/null)" || return 0
  case "$text" in
    *"block_kind=browse"*) printf 'browse-mode\n'; return 0 ;;
    *"block_kind=history"*) printf 'history-mode\n'; return 0 ;;   # 10-14 K8:Claude 框在輸入歷史,不是卡稿
    *"block_kind=modal"*|*"block_kind=draft"*) printf 'foreign-draft\n'; return 0 ;;
    *未送出草稿*|*拒絕投遞*) printf 'foreign-draft\n'; return 0 ;;
  esac
  return 0
}
_notice_clear() {     # 投遞成功 ⇒ 框可用 ⇒ 刪檔。只有寫的這一端刪。
  [ -n "${1:-}" ] || return 0
  [ -f "$_COMPOSER_NOTICE" ] || return 0
  "$PY3" "$_COMPOSER_NOTICE" clear --seat "$1" 2>/dev/null || true
  return 0
}
# ★ EH.20 §四:階段 A 的診斷行與逐席 receipt **欄位不同、不共用 schema**。
#   這兩支刻意都不帶 `rc_contract` —— 帶了的話,讀者看到 rc_contract 就會以為
#   有東西被投遞過。**兩種東西,兩個 stage,不共用欄位。**
_resolve_ok() {   # $1=canonical @wid  $2=label
  "$PY3" -c 'import json,sys; print(json.dumps({"stage":"resolve","input_selector":sys.argv[1],"canonical_target":sys.argv[2],"label":sys.argv[3]},ensure_ascii=False))' \
    "$TARGET" "$1" "$2"
}
_resolve_fail() { # $1=unknown|ambiguous|invalid  $2=可選 detail JSON
  "$PY3" -c '
import json, sys
out = {"stage": "resolve", "input_selector": sys.argv[1], "reason": sys.argv[2]}
if len(sys.argv) > 3 and sys.argv[3]:
    try:
        detail = json.loads(sys.argv[3])
    except (TypeError, ValueError):
        detail = None
    if isinstance(detail, dict):
        out["detail"] = detail
print(json.dumps(out, ensure_ascii=False))' \
    "$TARGET" "$1" "${2-}"
}
emit_json() {  # 機器可讀結果行(seat open 刀 debug 用),不取代人話輸出
  # ★ 刀 M:第四參數 verified=引擎走哪條路徑(fold/literal/…)。
  #   為什麼要有:引擎早就算出 `_verified` 並印在自己的 stdout,而這一行**硬寫
  #   字串**,標籤到不了 JSON 消費者手上——**資訊在引擎有、在介面沒了**
  #   (與 before_placeholders 同一句話的第二個實例,dev1 記為 I2)。
  #   相容:欄位**只增不改**,舊消費者讀 rc_contract/stage/detail 不受影響;
  #   拿不到標籤時填 "unknown",**不猜、不因為「大概是 fold」就升級**。
  # ★ 第五參數 before_placeholders(艦長 23:12 派、dev4 提):引擎在守衛開火前
  #   **本來就抓過 BEFORE**,交出來之後白名單條件一從「人多做一次 capture」
  #   變成「讀一個欄位」;且模態永遠不會產生新的 `[Pasted text #N]`,故
  #   「AFTER 要比 BEFORE 多一枚新佔位符」天然排除模態,不必認得它長什麼樣。
  #   空值=貼前沒有佔位符(不是「沒查」);查不到時上游不設此變數,這裡填 unknown。
  # ★ 三態規格(艦長 23:19 定版,dev4 提):
  #     `[]`   = 抓到了、框是空的  → 白名單條件一**可用**
  #     `null` = 沒抓到(capture rc!=0 或引擎未報)→ 條件一**不可用,必須停手**
  #   **兩者不得都寫成 `[]`**——die 擋住的是本行程,擋不住任何一個讀 JSON 的下游。
  #   故本欄是**真 JSON 型別**(陣列或 null),不是字串:字串會讓下游用 `if x:`
  #   把三態壓回兩態(今晚 `short` 三態就是這樣被呼叫點壓掉的)。
  local _bp="${5-${AIB_DM_BEFORE_PLACEHOLDERS-__aib_absent__}}"
  # ★ 三態的實作搬進 src/messaging/dm_paste.py:normalize_before(逐行原樣,
  #   含「舊引擎的 `[(無)]` 解不出型別 → 落 null,不假裝抓到了」那一條)。
  #   搬的理由:自動 Enter 的判準必須與這一欄用**同一套文法**,而同一支鏈上
  #   已經有三份正則了。★ 模組叫不動時落 `null`——那是「沒抓到」,下游必須
  #   停手,**失效方向朝停手不朝放行**。
  _bp="$("$PY3" "$DM_PASTE" normalize-before "$_bp" 2>/dev/null)" || _bp="null"
  [ -n "$_bp" ] || _bp="null"
  # 3-18b:只有引擎真的命中身份提示時才增列位置收據。兩值先過封閉值域,
  # 再組進 JSON；拿不到或不合法一律缺欄,不得用 none/0 假裝量過。
  local _identity_fields="" _identity_region="${_IDENTITY_PROMPT_REGION:-}"
  local _identity_line="${_IDENTITY_PROMPT_LINE:-}"
  case "$_identity_region" in
    last_line|previous_line)
      case "$_identity_line" in
        ""|0|*[!0-9]*) ;;
        *) _identity_fields=",\"identity_prompt_region\":\"${_identity_region}\",\"identity_prompt_line\":${_identity_line}" ;;
      esac
      ;;
  esac
  printf '{"rc_contract":%s,"stage":"%s","detail":"%s","verified":"%s","before_placeholders":%s%s}\n' \
         "$1" "$2" "$3" "${4:-unknown}" "$_bp" "$_identity_fields"
  _RESULT_STAGE="$2"    # RESULT 行的 stage 與這一行同源;最後一次 emit 的算數
}

TARGET=""; FILE=""; DRYRUN=0; VERIFY=0; MSG_ARGV=()
TO_RAW=""; TO_GIVEN=0; RESOLVE_ONLY=0
DM_THREAD=""; DM_KIND=""; DM_EXPIRES=""; DM_TTL_MIN=""; DM_CLOSE=""
RETRY_UNTIL=""; RETRY_EVERY=""; RETRY_UNTIL_GIVEN=0
PROBE_BOX=0; PARK_ON_DRAFT=0; XHOST_IDENT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO_RAW="${2:-}"; TO_GIVEN=1; shift 2 ;;
    --to=*) TO_RAW="${1#--to=}"; TO_GIVEN=1; shift ;;
    --resolve-only) RESOLVE_ONLY=1; shift ;;
    --file) FILE="${2:-}"; shift 2 ;;
    --dry-run) DRYRUN=1; shift ;;
    --verify) VERIFY=1; shift ;;
    --retry-until) RETRY_UNTIL_GIVEN=1; RETRY_UNTIL="${2:-}"; shift 2 ;;
    --retry-until=*) RETRY_UNTIL_GIVEN=1; RETRY_UNTIL="${1#--retry-until=}"; shift ;;
    --retry-every) RETRY_EVERY="${2:-}"; shift 2 ;;
    --retry-every=*) RETRY_EVERY="${1#--retry-every=}"; shift ;;
    --probe-box) PROBE_BOX=1; shift ;;
    --park-on-draft) PARK_ON_DRAFT=1; shift ;;
    # 第 70 項 P3 第二包(接口 IFACE-dm-queue-v2 §1/§3):主題、用途、時效。三個都是**寄件人自報的
    # 分類**,不是身份;身份欄(sender_seat / human_id)只可推導,見下面「不可偽填」那條。
    --thread) DM_THREAD="${2:-}"; shift 2 ;;
    --thread=*) DM_THREAD="${1#--thread=}"; shift ;;
    --kind) DM_KIND="${2:-}"; shift 2 ;;
    --kind=*) DM_KIND="${1#--kind=}"; shift ;;
    --close) DM_CLOSE="${2:-}"; shift 2 ;;
    --close=*) DM_CLOSE="${1#--close=}"; shift ;;
    --expires) DM_EXPIRES="${2:-}"; shift 2 ;;
    --expires=*) DM_EXPIRES="${1#--expires=}"; shift ;;
    --ttl-min) DM_TTL_MIN="${2:-}"; shift 2 ;;
    --ttl-min=*) DM_TTL_MIN="${1#--ttl-min=}"; shift ;;
    # ★ 這是**證據**不是身份:遞進來的是完整 session_uuid,由本檔拿去比對席位登記簿,
    #   對得上唯一一個活席才推得出五碼。自填身分的入口仍在下面兩條 die,一個字沒動。
    #   為什麼需要它:MCP 這條路的行程掛在 codex 的常駐服務底下(父程是 init),
    #   祖先鏈**結構上**永遠回不到任何一席的 pane ⇒ 沒有這個入口就永遠不具名。
    --codex-session-uuid) DM_CODEX_SESSION_UUID="${2:-}"; shift 2 ;;
    --codex-session-uuid=*) DM_CODEX_SESSION_UUID="${1#--codex-session-uuid=}"; shift ;;
    # 跨主機收件端(dm_xhost.py):寄件機推導好的身分 token。只在 sshd 底下才收,驗不過 rc=2;見下面「跨主機」兩段。
    --xhost-ident) XHOST_IDENT="${2:-}"; shift 2 ;;
    --xhost-ident=*) XHOST_IDENT="${1#--xhost-ident=}"; shift ;;
    --human-id*|--sender-seat*)
      die "sender_seat / human_id 只可推導不可指定($1):那是身份欄,自填=冒充" 2 ;;
    --sender*|--as*)
      # 發送席只可推導:任何指定發送身份的旗標=拒(不可偽填他席)
      die "發送席身份只可推導不可指定($1)" 2 ;;
    -*) die "未知選項:$1" 2 ;;
    *)
      # ★ EH.20:`--to` 之**後**的位置參數一律是訊息,不是目標。
      #   沒有這條,`aib dm --to @a,@b "訊息"`(§二 介面逐字允許的形狀)會把
      #   「訊息」當成位置目標,然後被互斥檢查擋掉 —— 規格自己的例子跑不起來。
      #   而 N4 仍測得到:位置目標只可能出現在 `--to` **之前**
      #   (`aib dm @1 --to @2,@3 …`),那一格照樣 rc=2。
      #   單目標路徑(沒有 --to)行為一字不變。
      if [ -z "$TARGET" ] && [ "$TO_GIVEN" = "0" ]; then TARGET="$1"
      else MSG_ARGV+=("$1"); fi
      shift ;;
  esac
done
# ★ EH.20 §二 相容性紅線:`--to` 與位置參數**互斥**,兩者同時給 → rc=2 用法錯。
#   這一格刻意**不出逐席 receipt**(§四):沒有 canonical_target 也沒有投遞發生,
#   出一筆欄位可空的 receipt 就是把「解析失敗」與「解析成功但沒送」壓成同一種。
# ★★ 必修二(a seat 50eb55b):`--to` 與 `--resolve-only` 併用**沒有被擋**。
#   父行程走解析模式時整段跳過取稿,連空稿檢查也一起跳過,然後拿**空稿**對每一席
#   進階段 B,收據還寫 stage:delivery。目標端什麼都沒收到,而真正擋住它的是
#   子行程裡一個**與本功能無關**的空稿檢查。
#   ★ 這一格的形狀值得留:**它今天沒出事,靠的是別人為了別的理由設的閘。**
#     那個閘哪天為了它自己的理由被改掉,這裡就開始送空稿,而沒有一格在看。
if [ "$TO_GIVEN" = "1" ] && [ "$RESOLVE_ONLY" = "1" ]; then
  "$PY3" -c 'import json,sys; print(json.dumps({"stage":"usage","rc":2,"detail":"--to 與 --resolve-only 不得併用"},ensure_ascii=False))'
  die "--to 與 --resolve-only 不得併用(解析模式是單目標用的;階段 A 由多目標行程自己再入)" 2
fi
if [ "$TO_GIVEN" = "1" ] && [ -n "$TARGET" ]; then
  "$PY3" -c 'import json,sys; print(json.dumps({"stage":"usage","rc":2,"detail":"--to 與位置目標不得同時給"},ensure_ascii=False))'
  die "--to 與位置目標不得同時給(§二 互斥)" 2
fi
if [ "$TO_GIVEN" != "1" ]; then
  [ -n "$TARGET" ] || die "用法:aib dm <目標> [訊息...] [--file F] [--dry-run] [--verify]" 2
fi

# ── 跨主機(the maintainer 10-14 20:5x「先修這個」;拆法、驗法、能力邊界在 dm_xhost.py 檔頭)─────────────
#   寄件機:地址的 IP 不是本機 ⇒ 下面完整地址那段記下 _XH_OUT_*,讀完稿後 _xhost_send 用 ssh 交給收件機。
#     寄件人用 AIB_DM_XHOST_WHOAMI=1 再入本檔推導 —— 同一套規則,不另抄一份;那一輪只印 token、不送、不留痕。
#   收件機:--xhost-ident 驗過(sshd 底下、對方不是本機、寄件方過白名單)才換抬頭寄件方;驗不過 rc=2,一個字都沒送。
_XH_WHOAMI=0; _XH_OUT_IP=""; _XH_IN=0; _XH_LABEL=""; _XH_ORIGIN=""; _XH_HUB=""; _XH_IP=""
if [ "${AIB_DM_XHOST_WHOAMI:-0}" = "1" ] && [ -z "$XHOST_IDENT" ] && [ "$TO_GIVEN" != "1" ]; then
  # 只推導寄件人:跳過完整地址、取稿、真人三條件、留痕(RESOLVE_ONLY 那條路),下面目標閘整段跳過。
  _XH_WHOAMI=1; RESOLVE_ONLY=1; AIB_DM_ADDR_DONE=1; TARGET="${TARGET:-@xhost}"
fi
if [ -n "$XHOST_IDENT" ]; then
  [ "$TO_GIVEN" = "1" ] && die "--xhost-ident 不得與 --to 併用" 2
  if ! _xh_line="$("$PY3" "$HERE/dm_xhost.py" accept "$XHOST_IDENT" 2>&1)"; then
    emit_json 2 resolve "xhost:${_xh_line}"
    die "跨主機投遞拒收:${_xh_line}" 2
  fi
  IFS=$'\x1f' read -r _XH_LABEL _XH_ORIGIN _XH_HUB _XH_IP <<<"$_xh_line"
  _XH_IN=1
fi



# ── 完整地址(the maintainer 09-28:aib dm 一律含艙位碼;跨主機再加 IP)──────────────────
#   艙位碼/席碼、IP/艙位碼/席碼、或從抬頭抄下的 IP:位址/艙位碼/名@席碼(拆法只在 dm_address.py)。
#   艙位碼 = 艙 id 最後一個 `-` 之後的四碼。寫了艙 ⇒ 換到那一艙、目標改回 @席碼後重進本檔一次,
#   並設 AIB_DM_HOPPED=1:指定了艙就不再自動跳艙。別台的 IP ⇒ rc=2,一個字都沒送。
#   只寫席碼的舊寫法:`aib dm` 入口(人與 AI 打的那一條,含 API 席的 dm 工具)一律設 AIB_DM_REQUIRE_CABIN=1 ⇒ 拒,
#   並列出這個席碼在這台機器的正確寫法(the maintainer 09-28「立刻切換新法,舊的停用」)。直接叫 dm-ctl.sh 的內部管線
#   (排序暫存排空、滑鼠送件、開席通知)是在已知的艙裡投遞,不經入口,照舊。
#   多目標 --to 不在這裡拆:階段 A/B 都是逐席再入本檔,每一席在自己那一次拆。
if [ "$TO_GIVEN" != "1" ] && [ -z "${AIB_DM_ADDR_DONE:-}" ]; then
  # 主艙名只有解析器知道(不外漏常數):helper 在 instances/ 找不到時,由 aib_cabin_is_primary 判。
  _addr_line="$("$PY3" "$HERE/dm_address.py" resolve "$TARGET" "$(aib_cabin_state_root)" "" 2>/dev/null)"
  _addr_rc=$?
  _addr_get() { printf '%s\n' "$_addr_line" | tr ' ' '\n' | sed -n "s/^$1=//p" | head -1; }
  if [ "$_addr_rc" != "0" ] && [ "$(_addr_get reason)" = "no-cabin" ] && aib_cabin_is_primary "$(_addr_get cabin)"; then
    _addr_line="kind=cabin seat=$(_addr_get seat) cabin=$(_addr_get cabin) instance=$(_addr_get cabin)"; _addr_rc=0
  fi
  # 認不出的寫法(窗號、角色名…)不在這裡拒:交給下面既有的具名拒投,理由才講得準。
  if [ "$_addr_rc" != "0" ] && [ "$(_addr_get reason)" = "bad" ]; then
    _addr_line="kind=other"; _addr_rc=0
  fi
  if [ "$_addr_rc" != "0" ] && [ "$(_addr_get reason)" = "remote" ] && [ "$_XH_IN" != "1" ]; then
    # 跨主機:這裡不送,讀完稿再交給 _xhost_send(要先有稿;身分也在那裡推)。
    [ -n "$(_addr_get cabin)" ] || { emit_json 2 resolve "address:remote-no-cabin"; die "跨主機要寫 IP/艙位碼/席碼" 2; }
    if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail invalid; exit 2; fi
    _XH_OUT_IP="$(_addr_get ip)"; _XH_OUT_ADDR="$(_addr_get cabin)/$(_addr_get seat)"
    _addr_line="kind=xhost"; _addr_rc=0
  fi
  if [ "$_addr_rc" != "0" ]; then
    _addr_detail="$(printf '%s\n' "$_addr_line" | tr ' ' '\n' | sed -n 's/^detail=//p' | sed 's/\xc2\xa0/ /g')"
    if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail invalid; exit 2; fi
    emit_json 2 resolve "address:$(_addr_get reason)"
    die "${_addr_detail:-認不出地址:$TARGET}" 2
  fi
  case "$(_addr_get kind)" in
    bare)
      if [ "${AIB_DM_REQUIRE_CABIN:-0}" = "1" ]; then
        # 建議寫法:這個席碼登記在哪幾艙(活的在前);socket ⇒ 艙 id 走解析器,艙位碼取最後一個 - 之後四碼。
        _addr_seat="$(_addr_get seat)"; _addr_hint=""
        while read -r _s_sock _s_live; do
          [ -n "$_s_sock" ] || continue
          _s_inst="$(aib_cabin_instance_from_hub "$_s_sock" 2>/dev/null)" || continue
          _s_tail="${_s_inst##*-}"; [ "${#_s_tail}" -gt 4 ] && _s_tail="${_s_tail:${#_s_tail}-4}"
          _addr_hint="${_addr_hint}  aib dm ${_s_tail}/${_addr_seat}   $([ "$_s_live" = "1" ] && _dmt aib.dmctl.alive || _dmt aib.dmctl.not_running)"$'\n'
        done < <("$PY3" "$HERE/dm_address.py" lookup "$_addr_seat" "$(aib_cabin_state_root)" 2>/dev/null)
        if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail invalid; exit 2; fi
        emit_json 2 resolve "address:no-cabin-given"
        _s_self="${EZ2_AIB_INSTANCE##*-}"; [ "${#_s_self}" -gt 4 ] && _s_self="${_s_self:${#_s_self}-4}"
        if [ -n "$_addr_hint" ]; then
          die "aib dm 一律要寫艙位碼:<艙位碼>/<席碼>(只寫席碼的舊寫法已停用)。這個席碼在這台機器是:"$'\n'"${_addr_hint}(本艙艙位碼 ${_s_self};抬頭 IP:位址/艙位碼/名@席碼 裡就有)" 2
        fi
        die "aib dm 一律要寫艙位碼:<艙位碼>/<席碼>(只寫席碼的舊寫法已停用)。這台機器找不到 ${_addr_seat} 的席位檔;本艙艙位碼 ${_s_self},對方的艙位碼看它 dm 抬頭 IP:位址/艙位碼/名@席碼。" 2
      fi ;;
    cabin)
      _addr_inst="$(_addr_get instance)"
      _addr_hub="$(aib_cabin_hub_name "$_addr_inst")" || die "艙 $_addr_inst 的名字拼不出來" 2
      _addr_args=(); _addr_swapped=0
      for _a in "${_DM_ORIG_ARGS[@]}"; do
        if [ "$_addr_swapped" = "0" ] && [ "$_a" = "$TARGET" ]; then
          _addr_args+=("@$(_addr_get seat)"); _addr_swapped=1
        else
          _addr_args+=("$_a")
        fi
      done
      # 就是本艙 ⇒ 不重進,目標改回 @席碼往下走(後面的轉信/跳艙看的是改過的參數);不再自動跳艙。
      if [ "$_addr_inst" = "${EZ2_AIB_INSTANCE:-}" ]; then
        export AIB_DM_INPUT_SELECTOR="${AIB_DM_INPUT_SELECTOR:-$TARGET}" AIB_DM_HOPPED=1 AIB_DM_ADDR_DONE=1
        TARGET="@$(_addr_get seat)"
        _DM_ORIG_ARGS=("${_addr_args[@]}")
      else
      exec env -u EZ2_AIB_INSTANCE -u EZ2_AIB_INSTANCE_HOME -u EZ2_TMUX_SESSION \
        EZ2_AIHUB_SOCKET="$_addr_hub" AIB_DM_HOPPED=1 AIB_DM_ADDR_DONE=1 \
        AIB_DM_INPUT_SELECTOR="${AIB_DM_INPUT_SELECTOR:-$TARGET}" bash "$SOURCE" "${_addr_args[@]}"
      fi ;;
  esac
fi

# ── 第 70 項 P3:主題/用途/時效的形狀檢查(在讀稿之前;錯就 rc=2,一個字都沒進目標)──
# thread 寫法 `<項號>/<字串>`(the maintainer 09-12 14:3x 裁):恰一個 `/` 分兩半、兩半非空、不含換行。
# 同項號不同字串算不同主題,所以字串那一半不正規化(不去空白、不改大小寫)。
if [ -n "$DM_THREAD" ]; then
  case "$DM_THREAD" in
    */*/*) die "--thread 只能有一個 /(<項號>/<字串>):$DM_THREAD" 2 ;;
    /*|*/) die "--thread 的兩半都不得為空(<項號>/<字串>):$DM_THREAD" 2 ;;
    */*) ;;
    *) die "--thread 要寫成 <項號>/<字串>(例 24/租約鎖):$DM_THREAD" 2 ;;
  esac
  case "$DM_THREAD" in *$'\n'*|*$'\r'*) die "--thread 不得含換行" 2 ;; esac
fi
if [ -n "$DM_KIND" ]; then
  case "$DM_KIND" in
    order|rule|pipeline|deliver|decide|report|other|notice) ;;
    *) die "--kind 只認 order/rule/pipeline/deliver/decide/report/other/notice(接口 §1):$DM_KIND" 2 ;;
  esac
fi
if [ -n "$DM_CLOSE" ]; then
  case "$DM_CLOSE" in
    q_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) die "--close 要原信的佇列 id(q_ 加 16 碼):$DM_CLOSE" 2 ;;
  esac
fi
if [ -n "$DM_EXPIRES" ] && [ -n "$DM_TTL_MIN" ]; then
  die "--expires 與 --ttl-min 不得同時給" 2
fi
if [ -n "$DM_TTL_MIN" ]; then
  case "$DM_TTL_MIN" in ''|*[!0-9]*|0) die "--ttl-min 必須是正整數分鐘:$DM_TTL_MIN" 2 ;; esac
  DM_EXPIRES="$(( $(date +%s) + DM_TTL_MIN * 60 ))"
fi
if [ -n "$DM_EXPIRES" ]; then
  case "$DM_EXPIRES" in ''|*[!0-9]*) die "--expires 必須是 epoch 秒(整數):$DM_EXPIRES" 2 ;; esac
fi
[ -n "$DM_THREAD" ] && _RESULT_THREAD="$DM_THREAD"

# 訊息:argv > --file > stdin
MSG=""
if [ "$RESOLVE_ONLY" = "1" ]; then
  # ★ 解析模式一個字都不需要,而且**不得讀 stdin**:階段 A 由多目標行程呼叫,
  #   它的 stdin 是稿本身;在這裡 `cat` 掉,後面逐席投遞就沒有稿可送了。
  #   (失效形狀不是報錯,是**第一席之後全部送出空稿**。)
  :
elif [ "${#MSG_ARGV[@]}" -gt 0 ]; then
  MSG="${MSG_ARGV[*]}"
elif [ -n "$FILE" ]; then
  [ -f "$FILE" ] || die "--file 不存在:$FILE" 2
  MSG="$(cat "$FILE")"
elif [ ! -t 0 ]; then
  MSG="$(cat)"
fi
if [ "$RESOLVE_ONLY" != "1" ] && [ "$PROBE_BOX" != "1" ]; then
  [ -n "${MSG//[[:space:]]/}" ] || die "訊息不得為空白" 2
fi
# 跨主機寄件(上面「跨主機」那段):推導寄件人 ⇒ ssh 交給收件機的 aib dm;rc 與 RESULT 行照收件機的。
#   投不進的兩種:連不上(先 ssh true 試過)或收件機自己拒 ⇒ rc 2;ssh 中途斷 ⇒ 不知道對方收到沒 ⇒ rc 4(勿重送)。
_xhost_send() {
  local ident host cmd out rc ssh_bin="${AIB_DM_XHOST_SSH:-ssh}"
  local -a sshopt=(-o BatchMode=yes -o ConnectTimeout="${AIB_DM_XHOST_TIMEOUT:-8}" -o ForwardAgent=no -o ClearAllForwardings=yes) rargs
  host="$("$PY3" "$HERE/dm_xhost.py" ssh-target "$_XH_OUT_IP")" || host="$_XH_OUT_IP"
  # 先試連線再推導:連不上就什麼都不碰(推導那一輪會讀本機艙)。
  if ! "$ssh_bin" "${sshopt[@]}" "$host" true </dev/null >/dev/null 2>&1; then
    emit_json 2 resolve "xhost:ssh-unreachable"
    die "連不上 $host($_XH_OUT_IP)的 SSH,一個字都沒送" 2
  fi
  # 推導那一輪失敗時 stdout 會是解析失敗的 JSON:rc 不是 0、或不是純 token 字元 ⇒ 都當推不出來。
  # 推導要問寄件人**自己那一艙**:沒指定艙時 HUB 是預設名,這台不一定有 ⇒ 從自己 pane 的 $TMUX 認 aihub 形的 socket。
  #   (cockpit-env.sh 一定會 export 預設名,所以看的是呼叫端有沒有真的指定:_DM_SOCKET_WAS_SET。)
  local own_hub="" tmux_sock
  [ "$_DM_SOCKET_WAS_SET" = "1" ] && own_hub="${EZ2_AIHUB_SOCKET:-}"
  if [ -z "$own_hub" ] && [ -n "${TMUX:-}" ]; then
    tmux_sock="$(basename "${TMUX%%,*}")"
    case "$tmux_sock" in aihub|aihub-*) case "$tmux_sock" in *[!0-9A-Za-z-]*) ;; *) own_hub="$tmux_sock" ;; esac ;; esac
  fi
  ident="$(env ${own_hub:+EZ2_AIHUB_SOCKET="$own_hub"} AIB_DM_XHOST_WHOAMI=1 bash "$SOURCE" @xhost </dev/null 2>/dev/null)" || ident=""
  ident="${ident##*$'\n'}"
  case "$ident" in *[!A-Za-z0-9_=-]*) ident="" ;; esac
  if [ -z "$ident" ]; then
    # 推導那一輪自己死掉(hub 不可達之類):退回最低權格,照實寫推不出來,不猜。
    ident="$("$PY3" "$HERE/dm_xhost.py" encode "unattested:${S_ORIGIN_FALLBACK:-unknown}" "" "" "")" \
      || die "跨主機身分 token 組不出來,未送出" 2
  fi
  # AIB_DM_XHOST_REMOTE_AIB:收件機上 aib 的叫法(預設 aib;可寫成「bash /路徑/aib」,照空白拆成多個字)
  read -r -a rargs <<<"${AIB_DM_XHOST_REMOTE_AIB:-aib}"
  rargs+=(dm --xhost-ident "$ident" "$_XH_OUT_ADDR")
  [ -n "$DM_THREAD" ] && rargs+=(--thread "$DM_THREAD")
  [ -n "$DM_KIND" ] && rargs+=(--kind "$DM_KIND")
  [ -n "$DM_CLOSE" ] && rargs+=(--close "$DM_CLOSE")
  [ -n "$DM_EXPIRES" ] && rargs+=(--expires "$DM_EXPIRES")
  [ "$DRYRUN" = "1" ] && rargs+=(--dry-run)
  [ "$VERIFY" = "1" ] && rargs+=(--verify)
  cmd="$("$PY3" "$HERE/dm_xhost.py" remote-cmd "${rargs[@]}")" || die "跨主機指令組不出來,未送出" 2
  local errf; errf="$(_dm_ctl_tmp aib-dm-xhost-err)" || errf=/dev/null   # 暫存檔走 _dm_ctl_tmp(不自己推 TMPDIR)
  out="$(printf '%s' "$MSG" | "$ssh_bin" "${sshopt[@]}" "$host" "$cmd" 2>"$errf")"; rc=$?
  # 相容退路:收件機的 aib 是還沒有跨主機身分的舊版 ⇒ 它在解析參數就停(rc 2、RESULT stage=abort、
  #   stderr 點名 --xhost-ident),一個字都沒進艙 ⇒ 不帶身分重送一次;抬頭由收件機照舊寫未認證。
  #   三條都要成立才退:別的 rc 2(對方擋下、地址錯)照原樣回,不重送。
  if [ "$rc" = "2" ] && [ "${out##*$'\n'}" = "RESULT stage=abort id=- relay=- thread=-" ] \
       && grep -q -- '--xhost-ident' "$errf" 2>/dev/null; then
    rm -f "$errf"
    echo "aib-dm-ctl: 收件機 $host 的 aib 是舊版、不認跨主機身分 ⇒ 不帶身分重送一次;對方抬頭會是未認證,寄件人只能看正文(請那台更新 aib)" >&2
    local i; for ((i = 0; i < ${#rargs[@]}; i++)); do
      if [ "${rargs[i]}" = "--xhost-ident" ]; then rargs=("${rargs[@]:0:i}" "${rargs[@]:i+2}"); break; fi
    done
    cmd="$("$PY3" "$HERE/dm_xhost.py" remote-cmd "${rargs[@]}")" || die "跨主機指令組不出來,未送出" 2
    # 舊版抬頭會把位址寫成收件機自己 ⇒ 正文第一行標出 sshd 在那台量到的來源 IP 與自稱寄件人。
    local src_ip note
    src_ip="$("$ssh_bin" "${sshopt[@]}" "$host" 'printf "%s" "${SSH_CONNECTION%% *}"' </dev/null 2>/dev/null)" || src_ip=""
    note="$("$PY3" "$HERE/dm_xhost.py" fallback-note "$ident" "$src_ip")" || note=""
    out="$({ [ -n "$note" ] && printf '%s\n' "$note"; printf '%s' "$MSG"; } | "$ssh_bin" "${sshopt[@]}" "$host" "$cmd")"; rc=$?
  else
    cat "$errf" >&2 2>/dev/null
    [ "$errf" = /dev/null ] || rm -f "$errf"
  fi
  [ -n "$out" ] && printf '%s\n' "$out"
  # 收件機的 dm-ctl 有跑到 ⇒ 它自己印了 RESULT 行;本機不再印第二行(最後一行要是收件機那一則的)。
  local remote_ran=0
  case $'\n'"$out" in *$'\nRESULT '*) remote_ran=1; _RESULT_DONE=1 ;; esac
  case "$rc" in
    0|2|3|4) return "$rc" ;;
    255) emit_json 4 xhost-unknown "SSH 中途斷線,收件機收到沒有不知道;勿重送,先到那台看"; return 4 ;;
  esac
  if [ "$remote_ran" = "0" ]; then
    # 收件機的 aib dm 根本沒跑起來(找不到指令、舊版不認 --xhost-ident 以外的錯…):沒進任何艙。
    emit_json 2 resolve "xhost:remote-rc-$rc"
    die "收件機 $host 跑不起 aib dm(rc=$rc),一個字都沒送;看上面那台印的錯" 2
  fi
  emit_json 4 xhost-unknown "收件機回了契約外的 rc=$rc;勿重送,先到那台看"
  return 4
}
if [ -n "$_XH_OUT_IP" ]; then
  [ "${RETRY_UNTIL_GIVEN}" = "1" ] && die "跨主機還不支援 --retry-until" 2
  S_ORIGIN_FALLBACK="$("$PY3" "$HERE/sender_origin.py" 2>/dev/null | tr -cd '0-9A-Za-z_.:-' || true)"
  _xhost_send
  exit $?
fi
# --retry-until:稿已讀完再進迴圈,子輪走 stdin,不會第二輪讀到空稿。
if [ "${RETRY_UNTIL_GIVEN}" = "1" ] && [ "${AIB_DM_RETRY_INNER:-0}" != "1" ] \
     && [ "${RESOLVE_ONLY:-0}" != "1" ] && [ "$PROBE_BOX" != "1" ]; then
  _retry_until_loop
  exit $?
fi

# HDR:席位對映優先於腳本名。長訊指標與 unattested 抬頭共用這一筆。
# 推不出來就空 —— 指標維持舊字面,抬頭仍走下面的 unknown。
S_TAGGED="$("$PY3" "$HERE/sender_origin.py" --tagged 2>/dev/null || true)"
S_ORIGIN_KIND=""
S_ORIGIN=""
case "$S_TAGGED" in
  # bgfork(10-10 a seat,the maintainer 裁):寄件行程是 Claude Code 的背景分身,五碼是它 --resume 的**原席**;
  #   鏈上認到分身就停,不往上爬到「生守護程式的那一席」(真機:44ed3 的分身被記成 a seat)。
  bgfork:*)  S_ORIGIN_KIND="bgfork"; S_ORIGIN="${S_TAGGED#bgfork:}" ;;
  seat:*)    S_ORIGIN_KIND="seat";   S_ORIGIN="${S_TAGGED#seat:}" ;;
  script:*)  S_ORIGIN_KIND="script"; S_ORIGIN="${S_TAGGED#script:}" ;;
esac
# 祖先鏈量不到席時的第二條路:呼叫端遞來的 session_uuid **當證據**,由這裡查登記簿。
#   判準單源走 seat_liveness.holders()(跨艙、同時看登記簿與行程表),不另抄一份「誰還活著」。
#   放行條件刻意窄:**恰好一個**活著的登記席 ⇒ 推五碼;零個或多個都不猜(fail-closed)。
#   對到死席=查不到——今天現成的理由:ba934 的框裡還躺著別人 13:20 之前投進去的信,而那席已死。
S_CLAIMED_SEAT=""
S_CLAIMED_SOURCE=""
S_CLAIMED_HUB=""
_CLAIM_UUID=""
if [ "$S_ORIGIN_KIND" != "seat" ] && [ -n "${DM_CODEX_SESSION_UUID:-}" ]; then
  _CLAIM_UUID="$DM_CODEX_SESSION_UUID"; S_CLAIMED_SOURCE="codex_session_meta"
elif [ "$S_ORIGIN_KIND" != "seat" ] && [ -n "${CODEX_THREAD_ID:-}" ]; then
  # ★ 09-30(the maintainer 首要):codex 席在自己的 shell 工具裡打 `aib dm <艙>/<席> "…"`,抬頭一律
  #   `…/未認證@unknown`(f643、測試艙 b1af 各量到一次)。上面那條只接 MCP 路徑遞來的旗標,
  #   **shell 路徑什麼都沒帶** ⇒ 三條路全落空:
  #   (一)祖先鏈:codex 0.158 的工具行程掛在常駐 daemon 底下(父程 init),回不到 pane;
  #       就算席位帶 --no-daemon、鏈回得到 pane,跨艙寄信時 sender_origin 查的是 EZ2_AIHUB_SOCKET,
  #       而上面位址跳艙那一格(exec env … EZ2_AIHUB_SOCKET="$_addr_hub")已換成**收件人那一艙**。
  #   (二)TMUX/TMUX_PANE:codex 交給工具行程的環境不一定帶,下面「跨艙寄件人回自己那一艙認」也不成立。
  #   (三)MCP 旗標:shell 路徑沒有。
  #   codex 自己會把 CODEX_THREAD_ID(= 這段對話的 thread id)放進每個工具行程的環境,
  #   而 seat-detect 登記的 codex 席 session_uuid 就是那個 thread id(codex_claim_session_id
  #   要求 rollout 邏輯 id == 庫內 thread id 才寫)⇒ 與 MCP 那條同一種證據,走同一個判準。
  #   ★ 強度只給**自稱**,不給 @五碼:環境變數是送方行程自己帶的(sender_origin.py 檔頭同一條理由,
  #     EZ2_AIB_SEAT 不讀),daemon 又是全機共用 ⇒ 行程樹證明不了這個 id 屬於誰。要強態得靠祖先鏈。
  #   ★ 排在 MCP 旗標之後:兩者都有時用旗標那一條(伺服器量到的 turn metadata),不混用。
  #   ★ 來源另記 codex_thread_env,不借 codex_session_meta 的名字:讀帳的人要分得出是哪一條路。
  _CLAIM_UUID="$CODEX_THREAD_ID"; S_CLAIMED_SOURCE="codex_thread_env"
fi
if [ -n "$_CLAIM_UUID" ]; then
  # 一次查兩件事:五碼,以及那一席住哪一艙 —— 抬頭的艙位碼要是**寄件人自己的**艙(058d4a95f 同一條:
  #   跨艙寄件人是它自己、在它自己那一艙)。不查的話抬頭用當下 hub,跳艙後那是收件人的艙。
  #   艙由登記席檔所在名冊目錄反推 socket 名,是下面「socket ⇒ 名冊目錄」那條對應的反方向;
  #   形狀認不出就留空(抬頭照舊用當下 hub,不猜)。
  _CLAIM_OUT="$("$PY3" -c '
import os, re, sys
sys.path.insert(0, sys.argv[1])
import seat_liveness as sl
uuid = sys.argv[2].strip()
reg = sl.holders(uuid).get("registry") or [] if re.fullmatch(r"[0-9a-fA-F-]{36}", uuid) else []
if len(reg) == 1:
    sdir = os.path.dirname(os.path.abspath(reg[0].get("seat_file") or ""))
    inst_dir = os.path.dirname(sdir)
    sock = ""
    if os.path.basename(os.path.dirname(inst_dir)) == "instances":
        inst = os.path.basename(inst_dir)
        # 10-02(a seat):socket 名向單源 cabin_resolve.sh --hub 要,不在這裡拼(test-cabin-resolution R8);
        #   解析器不收或失敗 ⇒ 留空(抬頭照舊用當下 hub,不猜)。
        import subprocess
        try:
            _p = subprocess.run(["bash", os.path.join(sys.argv[1], "cabin_resolve.sh"), "--hub", inst],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
            sock = _p.stdout.strip() if _p.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            sock = ""
    elif os.path.realpath(sdir) == os.path.realpath(os.path.join(sl.default_aib_home(), "seats")):
        sock = "aihub"
    print("%s %s" % (sl.tail5(uuid), sock))
' "$HERE" "$_CLAIM_UUID" 2>/dev/null || true)"
  read -r S_CLAIMED_SEAT S_CLAIMED_HUB <<<"$_CLAIM_OUT" || true
  [ -n "$S_CLAIMED_SEAT" ] || { S_CLAIMED_SEAT=""; S_CLAIMED_SOURCE=""; S_CLAIMED_HUB=""; }
fi

# 佇列 v2 欄位(接口 §1),兩個入佇列點與 record 共用這一組;只放有值的,沒值讓佇列端填 None/other。
# sender_seat 只從推導來(S_ORIGIN 是席位五碼時);human_id 本包**不送**(第三包真人三條件才有)。
# ★ 自稱席那條也填 sender_seat(收件席要回得了頭),但**強度差寫在 sender_source 與 sender_kind**,
#   不是靠措辭 —— 兩者混同的代價見未結案 27(甲乙兩態只靠措辭分開,守得住相同守不住相近)。
_V2_ARGS=()
[ "$S_ORIGIN_KIND" = "seat" ] && [ -n "$S_ORIGIN" ] && _V2_ARGS+=(--sender-seat "$S_ORIGIN" --sender-source pane_pid_chain)
[ "$S_ORIGIN_KIND" = "bgfork" ] && [ -n "$S_ORIGIN" ] && _V2_ARGS+=(--sender-seat "$S_ORIGIN" --sender-source bg_fork_chain)
# ★★ 自稱那條的兩個欄位**不在這裡填**,填在抬頭那條 if/elif 鏈裡真正決定用自稱的那一格。
#   a seat 09-27 抓到的缺陷就是這一格:原本在這裡獨立算一次,而抬頭走的是另一條鏈
#   (送信端帶 CLAUDECODE / EZ2_AIB_AGENT 時會先被判成 agent:offhub)⇒ 同一則信
#   抬頭說「艦外不具名」、帳本說「是 4f1d3」、種類說「沒帶身分」,**三個欄位各說一套而沒有一格會叫**。
#   修法不是排順序,是**一個事實只准有一個決定點** —— 抬頭沒用自稱,帳本就不准填自稱。
[ -n "$DM_THREAD" ] && _V2_ARGS+=(--thread "$DM_THREAD")
[ -n "$DM_KIND" ] && _V2_ARGS+=(--kind "$DM_KIND")
[ -n "$DM_EXPIRES" ] && _V2_ARGS+=(--expires "$DM_EXPIRES")
[ -n "$DM_CLOSE" ] && _V2_ARGS+=(--closes "$DM_CLOSE")   # S7-3:送達(直送 record / 排隊投遞 settle)時才結原信,由 dm_queue 做

# ── 真人三條件(第 70 項 P3 第三包;OPT §六、§八 4;human_attest.py 檔頭有能力邊界)────────────
# 08-18 EG.5 第一刀把 human 從兜底拿掉,說「human 要回來必須帶正面憑證,那是第二刀」——這就是第二刀。
# 三件同時量到(不在 hub 內 / tty 屬登入使用者 / 寄件端無 session)才標真人;缺任一件維持既有低權格,
# human_id 只在真人時有值(接口 §1:空=量不到)。排空器重投與階段 A 再入不量(它們永遠不是人)。
# 冒用:非真人的正文以「真人<…>:」開頭 ⇒ 改「AI 自稱真人:」照送並留痕(the maintainer 09-12 14:3x 裁「不擋」)。
HUMAN_ID=""; HUMAN_ATTEST_JSON=""
if [ "$RESOLVE_ONLY" != "1" ] && [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ] && [ "$S_ORIGIN_KIND" != "seat" ] && [ "$S_ORIGIN_KIND" != "bgfork" ] \
   && [ "$_XH_IN" != "1" ]; then
  HUMAN_ATTEST_JSON="$("$PY3" "$HERE/human_attest.py" --pid "$$" 2>/dev/null || true)"
  HUMAN_ID="$("$PY3" -c 'import json,sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print((d.get("human_id") or "") if d.get("human") is True else "")' "$HUMAN_ATTEST_JSON" 2>/dev/null || true)"
  case "$HUMAN_ID" in *[!A-Za-z0-9._-]*) HUMAN_ID="" ;; esac     # 登入名以外的字元不進抬頭(白名單形)
fi
if [ -n "$HUMAN_ID" ]; then
  _V2_ARGS+=(--human-id "$HUMAN_ID")
  case "$MSG" in 真人*) ;; *) MSG="真人${HUMAN_ID}:${MSG}" ;; esac     # 正文前綴由送信端加,不是人自己打
elif [ "$RESOLVE_ONLY" != "1" ] && [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ]; then
  # 排空器重投不改寫:那一則入佇列時已經判過(真人的前綴是送信端加的,重投時再判會把真人改成冒用)
  _IMP="$("$PY3" -c 'import sys
sys.path.insert(0, sys.argv[1])
import human_attest as ha
new, changed = ha.rewrite_impersonation(sys.stdin.read())
sys.stdout.write(("1" if changed else "0") + "\n" + new)' "$HERE" <<<"$MSG" 2>/dev/null || printf '0\n%s' "$MSG")"
  if [ "${_IMP%%$'\n'*}" = "1" ]; then
    MSG="${_IMP#*$'\n'}"
    echo "aib-dm-ctl: $(_dmt aib.dmctl.fake_human)" >&2
    _IMP_LOG="$_STATE_ROOT_EARLY/state/dm-impersonation.jsonl"
    mkdir -p "$(dirname "$_IMP_LOG")" 2>/dev/null && "$PY3" -c 'import json,sys,time,os
print(json.dumps({"ts": int(time.time()), "sender_origin": sys.argv[1] or None, "target": sys.argv[2] or None,
                  "pid": int(sys.argv[3]), "missing": json.loads(sys.argv[4] or "{}").get("missing")},
                 ensure_ascii=False, sort_keys=True))' "${S_ORIGIN:-}" "${TARGET:-}${TO_RAW:+$TO_RAW}" "$$" "$HUMAN_ATTEST_JSON" >> "$_IMP_LOG" 2>/dev/null || true
  fi
fi

# ══════════════════════════════════════════════════════════════════════════
# 長訊落檔(車道 4 第 2 件;正本 FINDING sha a18445c9,艦長 a6b2e 09-04 裁順序)
#
# 病因不是「Enter 沒按」,是**長訊會被目標 CLI 摺成佔位符**:貼得長、席又在跑,
# Enter 到的時候摺疊還沒處理完,Enter 被吃掉,稿留在框裡;之後全艦對它的 dm 都被
# 草稿守衛擋(守衛是對的),要等該席自己收工或有人按。a seat 09-03 兩次實例
# 卡 9 分鐘與 26 分鐘,而同一晚 177 位元組的短訊**從沒卡過**。
# ⇒ 這一段移掉的是病因本身:超過門檻就落檔,dm 只送一行指標。
#
# ★ 落檔失敗**不得靜默退回送長訊**。退回去的話,收據長得跟「不需要落檔」一模一樣,
#   而那正是我們要移掉的那個病 —— 失效值等於它表示安全的那個值。故失敗=具名拒送 rc=2
#   (rc=2 的承諾是「一個字都沒進目標」,此處貼都還沒開始,承諾為真)。
# ★ 檔名帶**艙尾碼 + pid + 秒**:pending 跨艙跨專案共用,同名檔會被別艦整檔蓋掉
#   (09-02 14:2x 實例),而被蓋掉之後指標仍然指得到一個存在的檔。
# ★ 指標字面刻意**不用**回報檔那個前綴:Stop hook 認得那個前綴就會去 lint 那個檔,
#   RELAY 檔不是七段回報,會紅在一個與本件完全無關的理由上。
# ★ 多目標 `--to a,b`:本段在**父層**跑(在再入之前),子層收到的已經是短指標,
#   所以一封長訊只會產生一個 RELAY 檔,不是每席一個。
AIB_DM_RELAY_BYTES="${AIB_DM_RELAY_BYTES:-600}"
case "$AIB_DM_RELAY_BYTES" in
  ''|*[!0-9]*) die "AIB_DM_RELAY_BYTES 必須是非負整數(0=關閉):$AIB_DM_RELAY_BYTES" 2 ;;
esac
# ── 留痕(第 70 項 P3;OPT §一病 7 / §七 7.2 / §八 6)────────────────────────
# 病 7:「稽核看不到直送的短訊」——短訊直接貼進對方框,事後任何工具都找不到它送過什麼,
#   子 AI 拿 RELAY 檔當母體就把回過的 4 則列成「沒回」。
# 解:**每一則**都落檔,不只長訊。長訊照舊落 pending(收件人要讀那個路徑,搬家是另一件);
#   短訊落**本艙** state/relay/<日期>/(不進 pending —— 那裡已經 11,000 多個檔,
#   而短訊的檔沒有人要用指標去讀,只有稽核要);兩種都寫一行進 state/relay/index.jsonl,
#   稽核母體從此是 index 不是 ls pending。dry-run 的檔名加 `.dry`(它沒送,母體要分得出)。
# ★ 短訊留痕失敗**不擋送**:指標不是它的用途,擋了會把「稽核少一筆」變成「全艦送不出信」。
#   但它要**吵**:stderr 一行具名,RESULT 行 relay=FAILED(不是 `-`,`-` 是「沒嘗試」)。
#   長訊落檔失敗仍是拒送 rc=2(那個檔就是要送的東西,理由在上面那段沒變)。
# ★ 只在**寄件那一刻**留痕一次:多目標子行程(EZ2_DM_MULTI_DEPTH)與排空器重投
#   (AIB_DM_SORT_DELIVER)拿到的是父行程/入佇列時已留痕的同一則,再寫就是病 7 的鏡像
#   (「一次送多席各留一份的檔算成重複投遞」)。
_RELAY_CABIN="${HUB##*-}"
[ -n "$_RELAY_CABIN" ] || _RELAY_CABIN="nohub"
# 狀態根與 SEATS_DIR 同一套順位(hub 導出 > INSTANCE_HOME > 根),根多認 EZ2_AIB_STATE_ROOT
# —— dm_queue.py 的 state_root 認它排第一,留痕若不認就會與佇列落在兩個根(§六「單源定義域小於消費者」)。
_STATE_BASE="${EZ2_AIB_STATE_ROOT:-$_AIB_ROOT}"
if [ -n "$_AIB_INST" ]; then _STATE_ROOT="$_STATE_BASE/instances/$_AIB_INST"
else _STATE_ROOT="${EZ2_AIB_INSTANCE_HOME:-$_STATE_BASE}"; fi
_RELAY_INDEX="${AIB_DM_RELAY_INDEX:-$_STATE_ROOT/state/relay/index.jsonl}"
_RELAY_SUFFIX=".txt"; [ "$DRYRUN" = "1" ] && _RELAY_SUFFIX=".txt.dry"
_relay_index_append() {   # $1=kind(long|short) $2=file $3=bytes;失敗只回非 0,由呼叫端決定吵法
  "$PY3" - "$_RELAY_INDEX" "$1" "$2" "$3" "$_RELAY_CABIN" "${S_ORIGIN:-}" "${TARGET:-}${TO_RAW:+$TO_RAW}" "$DRYRUN" "$$" <<'PY' 2>/dev/null
import json, os, sys, time
idx, kind, path, nbytes, cabin, sender, target, dry, pid = sys.argv[1:10]
os.makedirs(os.path.dirname(idx), exist_ok=True)
row = {"ts": int(time.time()), "kind": kind, "file": path, "bytes": int(nbytes),
       "cabin": cabin, "sender": sender or None, "target": target or None,
       "dry_run": dry == "1", "pid": int(pid)}   # pid = dm-ctl 自己的,與檔名尾碼同一個
with open(idx, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
PY
}
# review r1 m4:長訊這一步會把 MSG 換成「去讀某檔」的指標;下面「排隊投遞保留原寄件人」要比對的是**原文**,
#   拿指標去比永遠對不上 ⇒ 過 600 位元組的排隊信抬頭退回投遞者(系統@aib)。先留一份換之前的正文給它比。
#   (不選「排空重投跳過長訊落檔」:那會讓長信整段貼進對方輸入框,正是這一步要移掉的病。)
_MSG_PRE_RELAY="$MSG"
if [ "$RESOLVE_ONLY" != "1" ] && [ "$AIB_DM_RELAY_BYTES" -gt 0 ]; then
  # 位元組不是字元:摺疊門檻由 CLI 按輸入長度決定,而 CJK 一字三位元組。
  _RELAY_N="$(printf '%s' "$MSG" | LC_ALL=C wc -c | tr -d '[:space:]')"
  if [ "${_RELAY_N:-0}" -gt "$AIB_DM_RELAY_BYTES" ]; then
    _RELAY_DIR="${AIB_DM_RELAY_DIR:-$_AIB_ROOT/pending}"
    _RELAY_FILE="$_RELAY_DIR/RELAY-dm-${_RELAY_CABIN}-$(date '+%m%d-%H%M%S')-$$${_RELAY_SUFFIX}"
    mkdir -p "$_RELAY_DIR" 2>/dev/null
    if ! printf '%s' "$MSG" > "$_RELAY_FILE" 2>/dev/null; then
      emit_json 2 relay-write-failed "長訊落檔失敗($_RELAY_FILE):不改送長訊(那正是要移掉的病因);請自行落檔後送指標,或以 AIB_DM_RELAY_BYTES=0 明示豁免"
      die "長訊落檔失敗,未送出:$_RELAY_FILE" 2
    fi
    # 寫了不等於在那裡:讀回位元組數比對(與本檔其他各處同紀律)。
    _RELAY_BACK="$(LC_ALL=C wc -c < "$_RELAY_FILE" 2>/dev/null | tr -d '[:space:]')"
    if [ "${_RELAY_BACK:-}" != "$_RELAY_N" ]; then
      emit_json 2 relay-verify-failed "長訊落檔後讀回位元組數不符(寫 ${_RELAY_N}、讀 ${_RELAY_BACK:-取不到}):未送出"
      die "長訊落檔驗證不符,未送出:$_RELAY_FILE" 2
    fi
    _RELAY_SEAT=""; _RELAY_BGFORK=0
    if [ "$S_ORIGIN_KIND" = "seat" ]; then
      _RELAY_SEAT="$S_ORIGIN"
    elif [ "$S_ORIGIN_KIND" = "bgfork" ]; then
      _RELAY_SEAT="$S_ORIGIN"; _RELAY_BGFORK=1     # 指標寫「來自 @五碼 的背景分身」,讀的人分得出不是本人
    fi
    if [ "$_XH_IN" = "1" ]; then                   # 跨主機:指標寫寄件機那一席(驗過的 token),不是 SSH 行程
      _RELAY_SEAT=""; _RELAY_BGFORK=0
      case "$_XH_LABEL" in @*) _RELAY_SEAT="${_XH_LABEL#@}"; _RELAY_SEAT="${_RELAY_SEAT%%(*}" ;; esac
      [ "$_XH_ORIGIN" = "bgfork" ] && _RELAY_BGFORK=1
    fi
    MSG="$("$PY3" -c '
import sys
sys.path.insert(0, sys.argv[1])
import sender_origin as so
print(so.relay_pointer_line(int(sys.argv[2]), sys.argv[3], sys.argv[4] or None, bg_fork_of=(sys.argv[5] == "1")))
' "$HERE" "$_RELAY_N" "$_RELAY_FILE" "$_RELAY_SEAT" "$_RELAY_BGFORK")"
    [ -n "$MSG" ] || die "長訊指標組不出來,未送出" 2
    _RESULT_RELAY="$_RELAY_FILE"
    _relay_index_append long "$_RELAY_FILE" "$_RELAY_N" \
      || echo "aib-dm-ctl: $(_dmt aib.dmctl.relay_index_fail path="$_RELAY_INDEX")" >&2
  elif [ "${AIB_DM_RESEND:-0}" = "1" ]; then
    _RESULT_RELAY="${AIB_DM_RESEND_RELAY:--}"     # 第 159 項:重送輪不再留痕,RESULT 指第一輪那份
  elif [ -z "${EZ2_DM_MULTI_DEPTH:-}" ] && [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ]; then
    _RELAY_DIR="$_STATE_ROOT/state/relay/$(date '+%Y%m%d')"
    _RELAY_FILE="$_RELAY_DIR/RELAY-dm-${_RELAY_CABIN}-$(date '+%m%d-%H%M%S')-$$${_RELAY_SUFFIX}"
    mkdir -p "$_RELAY_DIR" 2>/dev/null
    _RELAY_BACK=""
    # ★ `2>/dev/null` 要寫在 `>` 之前:重導向照順序做,先開不了檔就先報錯,後面的 2> 已經來不及
    if printf '%s' "$MSG" 2>/dev/null > "$_RELAY_FILE"; then
      _RELAY_BACK="$(LC_ALL=C wc -c < "$_RELAY_FILE" 2>/dev/null | tr -d '[:space:]')"
    fi
    if [ "${_RELAY_BACK:-}" = "$_RELAY_N" ]; then
      _RESULT_RELAY="$_RELAY_FILE"
      _relay_index_append short "$_RELAY_FILE" "$_RELAY_N" \
        || echo "aib-dm-ctl: $(_dmt aib.dmctl.relay_index_fail path="$_RELAY_INDEX")" >&2
    else
      _RESULT_RELAY="FAILED"
      # ★ 變數後面緊接全形字要用 ${…}:bash 會把 `$_RELAY_N、` 的第一個位元組當成名字的一部分
      #   (set -u ⇒ 未綁定變數 ⇒ 整支 exit 1,而且 stderr 是半個 UTF-8 字)—— HANDOFF §六 那一條的鏡像
      echo "aib-dm-ctl: $(_dmt aib.dmctl.relay_short_fail file="${_RELAY_FILE}" n="${_RELAY_N}" got="${_RELAY_BACK:-$(_dmt aib.dmctl.not_readable)}")" >&2
    fi
  fi
fi

# ══════════════════════════════════════════════════════════════════════════
# EH.20 多目標 `--to a,b[,c]`(規格 docs/reports/SPEC-EH20-dm-multitarget-0822.md)
#
# 設計:**再入本檔自己**,不複製解析或投遞的任何一段。
#   ★ 理由不是省事:同一支鏈上「佔位符文法三份」就是複製出來的,
#     而三份的分岔至今還在(檔頭 :62 那段註解)。多目標若自己再寫一套解析,
#     第四份就出生了。**不要重寫產品碼已經做過的取法。**
#   階段 A 走 `--resolve-only` 再入(目標端零位元組)。
#   階段 B 走**位置式單目標**再入。
#   ★★ 必修一(a seat 50eb55b):本檔上一版寫「相容性紅線靠**那條路徑根本沒被改**
#     成立」——**那句話是假的**。旗標解析被換掉四行,單目標的舊行為是靠
#     `TO_GIVEN=0` **退化**回去的,不是沒被碰過。紅線今天成立(六個形狀兩樹逐位元組
#     比過),但它成立的方式是**條件式**的:「沒被改」是永久的,
#     「靠一個條件退化」會被下一次編輯打破。
#     → 實話是:這條紅線由治具的 **G2 五格**守著(五個單目標形狀在改動前/後兩棵樹上
#       stdout 與 rc 逐位元組相同),不是由「沒動過」守著。
#     ★ 同族於本單其他幾格:**我宣稱了一個結構性保證,而手上只有條件式的。**
#   稿以 **stdin** 交給子行程(子行程既有的第三順位取法),不落任何暫存檔:
#   少一個檔就少一個「清不掉會留下別席的稿」的面。
if [ "$TO_GIVEN" = "1" ]; then
  [ -z "${EZ2_DM_MULTI_DEPTH:-}" ] || die "--to 不得在多目標行程內再入(防再入迴圈)" 2
  _SELF="$HERE/$(basename -- "$SOURCE")"
  [ -f "$_SELF" ] || die "再入路徑不存在:$_SELF" 2
  _VLINT="$(cd "$HERE/../messaging" && pwd)/vocative_lint.py"

  _SELS=()
  IFS=',' read -r -a _SELS <<< "$TO_RAW"
  _NSEL="${#_SELS[@]}"
  [ "$_NSEL" -gt 0 ] || die "--to 需要至少一個目標" 2

  # ── 階段 A(解析):全解析,全有全無 ────────────────────────────────
  # ★ 為什麼解析要全有全無(§三):階段 A 尚未碰任何目標,**放棄的成本是零**;
  #   一旦開始投遞,放棄的成本是「已送出的那幾席收到了一封提到別席的稿」。
  #   兩個階段的可逆性不同,所以失敗處置不同。
  _CANON=(); _REASON=(); _DETAIL=(); _ABAD=0; _i=0
  while [ "$_i" -lt "$_NSEL" ]; do
    _s="${_SELS[$_i]}"
    if [ -z "$_s" ]; then
      _CANON[$_i]=""; _REASON[$_i]="invalid"; _DETAIL[$_i]=""; _ABAD=1
    else
      _ro="$(EZ2_DM_MULTI_DEPTH=1 bash "$_SELF" --resolve-only "$_s" </dev/null 2>/dev/null)"
      _cls="$("$PY3" -c '
import json, sys
obj = None
for ln in sys.argv[1].splitlines():
    t = ln.strip()
    if not t.startswith("{"):
        continue
    try:
        o = json.loads(t)
    except Exception:
        continue
    if o.get("stage") == "resolve":
        obj = o
if obj and obj.get("canonical_target"):
    print("ok:" + obj["canonical_target"])
else:
    r = (obj or {}).get("reason")
    # ★ 認不得的失敗一律落 invalid,**不落「解得出來」**。
    #   ★ hub 不可達原本落在這一格(結果對、理由錯)。a seat 50eb55b 把規格的
    #     reason 值域加到五個,於是它現在有自己的名字 `unreachable` ——
    #     **理由才是下一個人拿去行動的東西**,而 `invalid` 會讓人去改選擇子。
    print("bad:" + (r if r in ("unknown", "ambiguous", "invalid", "unreachable")
                    else "invalid"))
' "$_ro")"
      _detail="$("$PY3" -c '
import json, sys
obj = None
for ln in sys.argv[1].splitlines():
    try:
        cand = json.loads(ln.strip())
    except (TypeError, ValueError):
        continue
    if cand.get("stage") == "resolve":
        obj = cand
detail = (obj or {}).get("detail")
print(json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
      if isinstance(detail, dict) else "")
' "$_ro")"
      case "$_cls" in
        ok:*) _CANON[$_i]="${_cls#ok:}"; _REASON[$_i]=""; _DETAIL[$_i]="" ;;
        *)    _CANON[$_i]=""; _REASON[$_i]="${_cls#bad:}"; _DETAIL[$_i]="$_detail"; _ABAD=1 ;;
      esac
    fi
    _i=$(( _i + 1 ))
  done

  # canonical 重複(§三:`--to qa,@6` 兩個不同選擇子可能解到同一席;
  # **字面比對看不見它**,必須比 canonical)
  if [ "$_ABAD" = "0" ]; then
    _DUP="$("$PY3" -c '
import sys
seen = set(); out = []
for i, x in enumerate(sys.argv[1:]):
    if x in seen:
        out.append(str(i))
    seen.add(x)
print(",".join(out))' "${_CANON[@]}")"
    if [ -n "$_DUP" ]; then
      _ABAD=1
      for _d in ${_DUP//,/ }; do _REASON[$_d]="duplicate"; done
    fi
  fi

  if [ "$_ABAD" = "1" ]; then
    "$PY3" -c '
import json, sys
n = int(sys.argv[1]); sels = sys.argv[2:2 + n]; reasons = sys.argv[2 + n:2 + 2 * n]
details = sys.argv[2 + 2 * n:2 + 3 * n]
bad = []
for s, r, raw_detail in zip(sels, reasons, details):
    if not r:
        continue
    item = {"input_selector": s, "reason": r}
    if raw_detail:
        try:
            detail = json.loads(raw_detail)
        except (TypeError, ValueError):
            detail = None
        if isinstance(detail, dict):
            item["detail"] = detail
    bad.append(item)
print(json.dumps({"stage": "preflight", "invalid_selectors": bad,
                  "body_reject": None}, ensure_ascii=False))' \
      "$_NSEL" "${_SELS[@]}" "${_REASON[@]}" "${_DETAIL[@]}"
    echo "aib-dm-ctl: $(_dmt aib.dmctl.batch_a_fail)" >&2
    _RESULT_STAGE="preflight"
    exit 2
  fi

  # ── 階段 A(正文):只在**收件人數 > 1** 時擋第二人稱 ─────────────────
  # ★ P1/P2 靠的就是這個條件:單目標(位置式)根本走不到這裡,`--to @a` 走到了
  #   但 _NSEL=1 → 不擋。少了這個條件,一支「永遠擋第二人稱」的實作會讓 N1
  #   漂亮通過,而它會把艦上每一封現存的單發訊息變成紅的。
  if [ "$_NSEL" -gt 1 ]; then
    _VL_ERR="$(printf '%s' "$MSG" | "$PY3" "$_VLINT" --stdin 2>&1 >/dev/null)"
    _VL_RC=$?
    if [ "$_VL_RC" != "0" ]; then
      # ★ rc=1 是命中;**其餘任何值都不是「沒事」**,是「這道閘沒能回答問題」。
      #   探測器的失效值不得等於它表示安全的那個值 → 一律落擋(零投遞、可重送)。
      _VL_KIND="vocative_at_address_position"
      [ "$_VL_RC" = "1" ] || _VL_KIND="lint_unavailable"
      "$PY3" -c '
import json, sys
print(json.dumps({"stage": "preflight", "invalid_selectors": [],
                  "body_reject": {"reason": sys.argv[1],
                                  "recipients": int(sys.argv[2]),
                                  "lint_rc": int(sys.argv[3]),
                                  "detail": sys.argv[4]}}, ensure_ascii=False))' \
        "$_VL_KIND" "$_NSEL" "$_VL_RC" "$_VL_ERR"
      echo "aib-dm-ctl: $(_dmt aib.dmctl.multi_body_blocked kind="$_VL_KIND" err="$_VL_ERR")" >&2
      _RESULT_STAGE="preflight"
      exit 2
    fi
  fi

  # ── 階段 B(投遞):逐席獨立,一席失敗不中止其餘 ──────────────────────
  _ANYNZ=0; _i=0
  while [ "$_i" -lt "$_NSEL" ]; do
    # B11 前把 phase A 的 canonical 窗號餵回 child；窗號入口退役後那會讓
    # **每一個合法多目標都在 phase B 自己拒絕自己**。phase A 已證原選擇子是
    # 唯一 UUID 座位碼,故 child 重用原選擇子；canonical 只留給去重與收據。
    _CHILD=("${_SELS[$_i]}")
    [ "$DRYRUN" = "1" ] && _CHILD+=(--dry-run)
    [ "$VERIFY" = "1" ] && _CHILD+=(--verify)
    # 原始選擇子同時是 child 的合法座位碼與收據 carrier；canonical 窗號不再當目標。
    _out="$(printf '%s' "$MSG" | EZ2_DM_MULTI_DEPTH=1 AIB_DM_INPUT_SELECTOR="${_SELS[$_i]}" bash "$_SELF" "${_CHILD[@]}" 2>&1)"
    _crc=$?
    # 人話原樣透出;子行程那一行 JSON **不重印** —— 重印會讓「掃 rc_contract 行」
    # 的消費者把同一席數兩次。它整包原封不動進本席收據的 engine_receipt 欄。
    printf '%s\n' "$_out" | grep -v '^{"rc_contract"' || true
    _rcv="$("$PY3" -c '
import json, sys
idx, sel, canon, crc, raw = sys.argv[1:6]
eng = None
for ln in raw.splitlines():
    t = ln.strip()
    if t.startswith("{\"rc_contract\""):
        try:
            eng = json.loads(t)
        except Exception:
            pass
if isinstance(eng, dict) and isinstance(eng.get("rc_contract"), int):
    rcc = eng["rc_contract"]        # ★ 不覆寫:逐席那一層維持既有 0/2/3/4 語意
else:
    # 子行程在 emit_json 之前就 die,沒留下收據。**不猜「大概沒送出去」**:
    # 退出碼在契約值域內就照用;不在值域內落 4(絕不重送),不落 2。
    rcc = int(crc) if crc in ("0", "2", "3", "4") else 4
print(json.dumps({"stage": "delivery", "index": int(idx), "input_selector": sel,
                  "canonical_target": canon, "rc_contract": rcc,
                  "engine_receipt": eng}, ensure_ascii=False))' \
      "$_i" "${_SELS[$_i]}" "${_CANON[$_i]}" "$_crc" "$_out")"
    printf '%s\n' "$_rcv"
    case "$_rcv" in *'"rc_contract": 0,'*) : ;; *) _ANYNZ=1 ;; esac
    _i=$(( _i + 1 ))
  done

  # ── 行程 rc:只有三個值 ────────────────────────────────────────────
  # 0=全席皆 0 / 5=進了階段 B 且非全 0 / 2=沒進階段 B(上面兩個 exit 2)。
  # ★ **禁止** max / min / any / all 縮併:2(可安全重送)與 4(絕不重送)處置相反,
  #   合成不是失真是**主動說錯話**。5 刻意不重用 0/2/3/4——那四個值都帶處置,
  #   5 不帶處置,它的語意逐字是「答案在收據裡,不在這個數字裡」。
  if [ "$_ANYNZ" = "0" ]; then _PRC=0; else _PRC=5; fi
  "$PY3" -c '
import json, sys
print(json.dumps({"stage": "batch", "process_rc": int(sys.argv[1]),
                  "receipts": int(sys.argv[2])}, ensure_ascii=False))' "$_PRC" "$_NSEL"
  if [ "$_PRC" = "5" ]; then
    echo "aib-dm-ctl: $(_dmt aib.dmctl.rc5_head)" >&2
    echo "aib-dm-ctl: $(_dmt aib.dmctl.rc5_rule)" >&2
  fi
  _RESULT_STAGE="batch"    # 逐席的 RESULT 行已由子行程各印一行透出;本行是整批的收尾
  exit "$_PRC"
fi

hub_tmux() { env -u TMUX tmux -L "$HUB" "$@"; }

# 當下這艙沒有這席時,到其他指揮艙找唯一活著的那格再送。
# 人在哪一艙、有沒有自己設 socket,都不用先想。已經跳過一次、或同一個碼活在多艙,就不猜。
_dm_hop_to_live_seat() {
  [ "${AIB_DM_HOPPED:-}" = "1" ] && return 1
  local sock=""
  sock="$("$PY3" - "$TARGET" "$_AIB_ROOT" "$HERE" <<'PY'
import glob, json, os, re, sys
target, root, here = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, here)
import seat_address as sa
if re.fullmatch(r"@[0-9a-z]{4,8}", target) and not re.fullmatch(r"@[0-9]+", target):
    target = target[1:]
if not re.fullmatch(r"[0-9a-z]{4,8}", target):
    raise SystemExit(0)
dirs = []
primary = os.path.join(root, "seats")
if os.path.isdir(primary):
    dirs.append(primary)
inst_root = os.path.join(root, "instances")
if os.path.isdir(inst_root):
    for name in sorted(os.listdir(inst_root)):
        seats = os.path.join(inst_root, name, "seats")
        if os.path.isdir(seats):
            dirs.append(seats)
found = []
for seats in dirs:
    for path in glob.glob(os.path.join(seats, "w*.json")):
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rec, dict) or sa.code_of(rec) != target:
            continue
        sock = str(rec.get("socket") or "")
        wid = str(rec.get("window_id") or "")
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if not sock or not re.fullmatch(r"@[0-9]{1,10}", wid) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        if sock not in found:
            found.append(sock)
if len(found) == 1:
    print(found[0])
elif len(found) > 1:
    print("AMBIGUOUS " + " ".join(found))
PY
)" || sock=""
  case "$sock" in
    AMBIGUOUS*)
      echo "aib-dm-ctl: $(_dmt aib.dmctl.ambiguous_cabin detail="${sock#AMBIGUOUS }")" >&2
      exit 2
      ;;
  esac
  [ -n "$sock" ] || return 1
  [ "$sock" = "$HUB" ] && return 1
  echo "aib-dm-ctl: $(_dmt aib.dmctl.hop_cabin target="${sock}")" >&2
  # 外層已經把預設艙寫進環境。不拿掉的話,內層會當成「兩個艙打架」而拒絕。
  exec env -u EZ2_AIB_INSTANCE -u EZ2_AIB_INSTANCE_HOME -u EZ2_TMUX_SESSION \
    EZ2_AIHUB_SOCKET="$sock" AIB_DM_HOPPED=1 bash "$SOURCE" "${_DM_ORIG_ARGS[@]}"
}

# ★★ 2026-09-24(a seat)分出新席後的轉信:舊碼已沒有活席(本艙與跨艙都找過)、而恰好一個活席是從它
#   分出來的(席位檔 forked_from)⇒ 改投那一席,並說清楚「@舊 已由 @新 接續」。
#   舊碼還活著就不會走到這裡(那是兩個不同的席,不轉)。多個後繼不猜。設計 docs/DESIGN-seat-fork-unique-0924.md。
_dm_forward_to_successor() {
  [ "${AIB_DM_FORWARDED:-}" = "1" ] && return 1
  local old="${TARGET#@}" line="" rc=0
  line="$("$PY3" "$HERE/seat_liveness.py" successors "$old" 2>/dev/null)" || rc=$?
  case "$rc" in
    0) ;;
    4) echo "aib-dm-ctl: $(_dmt aib.dmctl.fork_ambiguous old="$old" detail="$(printf '%s' "$line" | tr '\n' ' ')")" >&2; return 1 ;;
    *) return 1 ;;
  esac
  local -a args=(); local a replaced=0
  for a in "${_DM_ORIG_ARGS[@]}"; do
    if [ "$replaced" = "0" ] && { [ "$a" = "$TARGET" ] || [ "$a" = "@$old" ]; }; then
      args+=("$line"); replaced=1
    else
      args+=("$a")
    fi
  done
  [ "$replaced" = "1" ] || return 1
  echo "aib-dm-ctl: $(_dmt aib.dmctl.fork_hop old="$old" new="$line")" >&2
  # review r2 m6:排隊投遞轉給後繼席之後,T_CODE 變成後繼席,而那一則還住在**舊席**的佇列底下 ⇒
  #   把舊席碼帶過去(投遞者已帶就不蓋),原寄件人那一段拿它回佇列找
  if [ -n "${AIB_DM_QUEUED_ITEM_ID:-}" ]; then
    exec env AIB_DM_FORWARDED=1 AIB_DM_QUEUED_SEAT="${AIB_DM_QUEUED_SEAT:-$old}" bash "$SOURCE" "${args[@]}"
  fi
  exec env AIB_DM_FORWARDED=1 bash "$SOURCE" "${args[@]}"
}

# 2026-09-20:活席危險鍵硬擋(見 dangerous_seat_actions.py)。
# hub_tmux_send_keys 取代「直接 hub_tmux send-keys … C-c」;否決則不送、非 0。
_AIB_DANGEROUS_PY="${HERE}/dangerous_seat_actions.py"
hub_tmux_send_keys() {
  # argv: -t TARGET KEY...  或 KEY...
  if ! /usr/bin/env python3 "$_AIB_DANGEROUS_PY" guard-tmux -- send-keys "$@"; then
    echo "aib-dm-ctl: $(_dmt aib.dmctl.deny_keys args="$*")" >&2
    return 2
  fi
  hub_tmux send-keys "$@"
}


# 活窗快照(ids-only,無注入面;與 pane-right-ai 同紀律)
LIVE_IDS="$(hub_tmux list-windows -t aihub -F '#{window_id}' 2>/dev/null)" \
  || { _dm_hop_to_live_seat || true
       _dm_forward_to_successor || true          # 09-24:預設艙不可達那條路也要轉(兩條「找不到活席」出口都掛)
       if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail unreachable; exit 2; fi
       emit_json 2 resolve "hub 不可達"; die "hub tmux 不可達(-L $HUB)。沒有指定艙,也沒有在其他活艙找到這席。" 2; }

# 目標解析+發送席推導(單次 python:JSON 讀寫不在 bash 裡手拼)
_SELF_WID=""
if [ -n "${TMUX:-}" ]; then
  _sock_path="${TMUX%%,*}"
  if [ "$(basename "$_sock_path")" = "$HUB" ]; then
    # ★P1 身份缺陷(艦 13 三席獨立實測,08-17):裸 display-message 回的是
    #   **client 正在看的窗**,不是呼叫 pane——the maintainer 看 @7 時,@1 發的 dm 簽成
    #   dev2 抬頭(08-15「掛別人抬頭」同型)。必以 $TMUX_PANE 定址;無
    #   TMUX_PANE(非 pane 內呼叫)不猜窗;推導失敗落最低權格 `unattested`,
    #   **不落 human**——human 需正面憑證(第二刀)。★ 這行原本寫「寧可留空走
    #   human 署名」,而刀四之後 dm-ctl 永不產生 human:註解與碼相反,而下一個人
    #   會照註解去改碼(dev4 刀 H 第 1 條;零行為變更)。
    if [ -n "${TMUX_PANE:-}" ]; then
      _SELF_WID="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
    fi
  elif [ -n "${TMUX_PANE:-}" ]; then
    # ★ 09-30 a seat(the maintainer 01:0x 抓到「為什麼會有艙外」):**跨艙寄信**時 HUB 已換成收件人那一艙,
    #   寄件人的 pane 在自己那一艙 ⇒ 上面那一格永遠不成立 ⇒ 寄件人落到「艙外@offhub」,
    #   抬頭的艙位碼還填成收件人的艙(實測:a seat 寄 997f/f8ce5 ⇒ `997f/艙外@offhub`)。
    #   ⇒ 寄件人要回**自己那一艙**認:同一個 pane 在自己的 socket 上問窗號,席位檔從那一艙的名冊讀。
    #   只認 aihub 形的 socket;認不出就照舊(不猜、不升權)。
    _sender_sock="$(basename "$_sock_path")"
    case "$_sender_sock" in
      aihub) _SENDER_SEATS_DIR="$_AIB_ROOT/seats" ;;
      aihub-*) _sender_inst="${_sender_sock#aihub-}"
               case "$_sender_inst" in *[!0-9a-zA-Z-]*) _sender_inst="" ;; esac
               [ -n "$_sender_inst" ] && _SENDER_SEATS_DIR="$_AIB_ROOT/instances/$_sender_inst/seats" ;;
    esac
    if [ -n "${_SENDER_SEATS_DIR:-}" ] && [ -d "$_SENDER_SEATS_DIR" ]; then
      _SELF_WID="$(env -u TMUX tmux -L "$_sender_sock" display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
      [ -n "$_SELF_WID" ] && _SENDER_HUB="$_sender_sock" && export DM_SENDER_SEATS_DIR="$_SENDER_SEATS_DIR"
    fi
  fi
fi

export DM_LIVE_IDS="$LIVE_IDS"
RESOLVED="$("$PY3" - "$TARGET" "$SEATS_DIR" "$_SELF_WID" "$HERE/seat_address.py" <<'PY'
import glob, json, os, re, sys

raw_target, seats_dir, self_wid = sys.argv[1], sys.argv[2], sys.argv[3]
# ★ L8(a seat 派工 0905,交接單 §四.6):全艦訊息裡座位碼一律寫成 `@五碼`,
#   人照著打 `aib dm a seat …` 就整條被拒,而那句話長得像「那席不存在」。
#   這裡只剝**解析用**的那一份;`raw_target` 原封不動留給 input_selector,
#   canonical_target 仍由命中的席位算出,兩欄一個字都不改形。
# ★★ 邊界(不是保險,是真的會撞):尾碼字元集 `[0-9a-z]{4,8}` **含數字**,
#   所以 `@1234` 同時符合「窗號」與「尾碼」兩個形狀。先擋純數字再剝,
#   否則 `@1234` 會從具名的 window_alias_retired 掉進「查無座位碼」——
#   方向剛好與本刀相反。`@16` 位數不足本來就不會進這條,靠的是它剛好短,
#   那不是判準;判準是下面這個 `not fullmatch(@[0-9]+)`。
target = raw_target
at_stripped = False
if re.fullmatch(r"@[0-9a-z]{4,8}", raw_target) and not re.fullmatch(r"@[0-9]+", raw_target):
    target = raw_target[1:]
    at_stripped = True
# ★ B11:合法投遞只認 seat_address.code_of() 的 UUID 尾五碼。
#   seat_address.match_code() 的 legacy 分支仍保留,但本區只拿它產生具名拒投診斷;
#   把 legacy 命中放進 matches 就會復活 the maintainer 已裁退的第二條投遞軌。
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[4])))
import seat_address as _sa
live = set(l.strip() for l in os.environ.get("DM_LIVE_IDS", "").splitlines()
           if l.strip())

def load_seats():
    out = {}
    for p in sorted(glob.glob(os.path.join(seats_dir, "w*.json"))):
        base = os.path.basename(p)
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:
            print("aib-dm-ctl: 壞席位檔跳過:%s" % base, file=sys.stderr)
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
            print("aib-dm-ctl: schema 不符跳過:%s" % base, file=sys.stderr)
            continue
        wid = str(rec.get("window_id") or "")
        if not re.fullmatch(r"@[0-9]{1,10}", wid) \
                or base != "w%s.json" % wid.lstrip("@"):
            print("aib-dm-ctl: window_id 交叉驗不符跳過:%s" % base,
                  file=sys.stderr)
            continue
        if wid not in live:
            continue                     # 死窗不入解析集合(接口契約:rc2)
        role = rec.get("role") if isinstance(rec.get("role"), str) else ""
        seat = rec.get("seat_id") if isinstance(rec.get("seat_id"), str) else ""
        rec_full = rec
        prov = rec.get("provider") if isinstance(rec.get("provider"), str) else "?"
        out[wid] = {"wid": wid, "role": role, "seat": seat, "provider": prov,
                    "code": _sa.code_of(rec_full), "rec": rec_full}
    return out

seats = load_seats()

def addr(s):
    code = s.get("code")
    return "@" + code if code else "量不到"

def named(s):
    wid = s.get("wid") or "?"
    window = wid[1:] if wid.startswith("@") else wid
    return "窗 %s role=%s seat_id=%s 正確座位碼=%s" % (
        window, s.get("role") or "?",
        s.get("seat") or "?", addr(s))

# B11 單軌:matches 只准放 UUID 尾碼命中。其餘舊形式不是 unknown ——
# 能辨識時要具名拒投,讓人一眼改對；辨識不到時誠實印「量不到」。
matches = []
refusal = None
# ★ 09-29 a seat:UUID 尾五碼有一成左右**全是數字**(真機:grok 席 a seat)。上面「先擋純數字」
#   那條邊界把它整席關在門外:`f643/77324` 在地址段被改回 `a seat`,到這裡長得像窗號 ⇒ 拒,
#   而帶艙位碼是 09-28 起唯一合法的寫法,所以那一席**任何寫法都寄不進去**。
#   判準改成看事實,不看形狀:
#     (1) 目標是從完整地址(艙位碼/席碼)拆出來的 ⇒ 它就是席碼,不可能是窗號;
#     (2) 其餘 `@純數字`:有活席的尾碼等於它、而且沒有活窗叫這個號 ⇒ 席碼。
#   兩條都不成立才是窗號(`@16`、`@1234` 照舊具名拒投)。
digit_code_hits = []
if re.fullmatch(r"@[0-9]{4,8}", target):
    _sel = os.environ.get("AIB_DM_INPUT_SELECTOR") or ""
    try:
        import dm_address as _da
        _full = _da.parse(_sel)
    except Exception:
        _full = None
    from_full_address = bool(_full and _full.get("cabin")
                             and "@" + _full.get("seat", "") == target)
    digit_code_hits = [s for s in seats.values() if s.get("code") == target[1:]]
    if from_full_address or (digit_code_hits and target not in seats):
        target = target[1:]
        at_stripped = not from_full_address
if re.fullmatch(r"@[0-9]{1,10}", target):
    hit = seats.get(target)
    detail = named(hit) if hit else "window=%s 正確座位碼=量不到" % target
    refusal = {"kind": "window_alias_retired",
               "message": "窗號不是 dm 位址:輸入=%s;%s" % (target, detail),
               # L8b 裁定二:窗號拒投那條路也要寫 NOTICE,而它需要被擋席的五碼。
               # 查不到就 None —— 沒有五碼就不寫,不編一個。
               # ★ B15 §三(2026-09-05,發版紅 1/3):這個鍵原本叫「席位碼」的舊名,
               #   而那個名字在產品面等於宣告一個執行期的第二真相源 —— 合法位址
               #   只從 session_uuid 導出。這裡要的是「被擋席的座位碼」,語意上
               #   不是已退役的那個欄位,所以改名 blocked_code。
               #   值的來源一個位元組沒動:仍是 (hit or {}).get("code")。
               #   ★ 註解本身也不得寫出舊名的字面 —— B15 的尺搜的是字面、
               #     不豁免註解,而那正是它該有的行為(談論它就會被它讀到)。
               "blocked_code": (hit or {}).get("code")}
elif re.fullmatch(r"[0-9a-z]{4,8}", target):
    uuid_hits = [s for s in seats.values() if s.get("code") == target]
    legacy_hits = [s for s in seats.values()
                   if _sa.match_code(s["rec"], target) == "legacy"]
    uuid_wids = set(s["wid"] for s in uuid_hits)
    cross_legacy = [s for s in legacy_hits if s["wid"] not in uuid_wids]
    if uuid_hits and cross_legacy:
        all_hits = uuid_hits + cross_legacy
        refusal = {"kind": "uuid_legacy_collision",
                   "message": "座位碼與 seat_id 跨席碰撞:輸入=%s;%s" % (
                       target, ";".join(named(s) for s in all_hits))}
    elif uuid_hits:
        matches = uuid_hits
    elif legacy_hits:
        refusal = {"kind": "seat_id_retired",
                   "message": "seat_id 已退役:輸入=%s;%s" % (
                       target, ";".join(named(s) for s in legacy_hits)),
                   "correct_addresses": sorted(set(
                       addr(s) for s in legacy_hits if addr(s) != "量不到"))}
    else:
        role_hits = [s for s in seats.values() if s["role"] == target]
        if role_hits:
            refusal = {"kind": "role_alias_retired",
                       "message": "角色不是 dm 位址:輸入=%s;%s" % (
                           target, ";".join(named(s) for s in role_hits))}
else:
    role_hits = [s for s in seats.values() if s["role"] == target]
    if role_hits:
        refusal = {"kind": "role_alias_retired",
                   "message": "角色不是 dm 位址:輸入=%s;%s" % (
                       target, ";".join(named(s) for s in role_hits))}

sender = None
# ★ 09-30:跨艙寄信 ⇒ 寄件人在自己那一艙的名冊(dm-ctl 上面已經用自己的 socket 問到窗號)。
#   **不得**拿這個窗號去收件人那一艙的名冊查:窗號每一艙各自編,會撞到別人
#   (實測:a seat 在 f643 是 @2,997f 的 @2 是 a seat ⇒ 抬頭被簽成 a seat)。
#   只讀那一個窗號的席位檔,形狀驗法與上面同一條;寄件人就是呼叫者本身,不用再驗活窗。
_sender_dir = os.environ.get("DM_SENDER_SEATS_DIR") or ""
if not _sender_dir and self_wid and self_wid in seats:
    sender = seats[self_wid]
if _sender_dir and re.fullmatch(r"@[0-9]{1,10}", self_wid or ""):
    try:
        _rec = json.load(open(os.path.join(_sender_dir, "w%s.json" % self_wid.lstrip("@")), encoding="utf-8"))
    except Exception:
        _rec = None
    if isinstance(_rec, dict) and _rec.get("schema") == "aib-seat/v1" and _rec.get("window_id") == self_wid:
        sender = {"wid": self_wid, "role": _rec.get("role") if isinstance(_rec.get("role"), str) else "",
                  "seat": _rec.get("seat_id") if isinstance(_rec.get("seat_id"), str) else "",
                  "provider": _rec.get("provider") if isinstance(_rec.get("provider"), str) else "?",
                  "code": _sa.code_of(_rec), "rec": _rec}

print(json.dumps({"matches": sorted(matches, key=lambda m: m["wid"]),
                  "sender": sender, "refusal": refusal,
                  "at_stripped": at_stripped}, ensure_ascii=False))
PY
)" || die "解析器失敗" 2
unset DM_LIVE_IDS

# 跨主機推導那一輪(_XH_WHOAMI)沒有本機目標:目標閘到 provider 那段整段跳過(縮排不動,範圍到「發送席推導」之前)。
if [ "$_XH_WHOAMI" != "1" ]; then
REFUSAL_KIND="$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1]).get("refusal") or {}).get("kind") or "")' "$RESOLVED")"
if [ -n "$REFUSAL_KIND" ]; then
  REFUSAL_DETAIL="$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1]).get("refusal") or {}).get("message") or "量不到")' "$RESOLVED")"
  REFUSAL_JSON="$("$PY3" -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1]).get("refusal") or {},ensure_ascii=False,separators=(",",":")))' "$RESOLVED")"
  echo "aib-dm-ctl: $REFUSAL_DETAIL" >&2
  if [ "$REFUSAL_KIND" = "window_alias_retired" ]; then
    _notice_blocked "$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1]).get("refusal") or {}).get("blocked_code") or "")' "$RESOLVED")" window-alias
  fi
  if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail "$REFUSAL_KIND" "$REFUSAL_JSON"; exit 2; fi
  emit_json 2 resolve "$REFUSAL_DETAIL"
  exit 2
fi

N_MATCH="$("$PY3" -c 'import json,sys; print(len(json.loads(sys.argv[1])["matches"]))' "$RESOLVED")"
AT_STRIPPED="$("$PY3" -c 'import json,sys; print("1" if json.loads(sys.argv[1]).get("at_stripped") else "0")' "$RESOLVED")"
if [ "$N_MATCH" = "0" ]; then
  # ★ L8:只在**剝過**的路徑印。舊訊息回答不了使用者下一步的問題——
  #   「是我多打了 @,還是這席真的不在?」兩者在舊輸出上逐字相同。
  # ★★ 這一行要在 RESOLVE_ONLY 的出口**之前**:解析模式在下一行就 exit,
  #   放在 `die` 旁邊的話 `--resolve-only @zzzzz` 的 stderr 會是空的
  #   (而空 stderr 與「沒剝過」在那一格上也逐字相同)。
  if [ "$AT_STRIPPED" = "1" ]; then
    echo "aib-dm-ctl: $("$PY3" "$HERE/i18n_lib.py" aib.dm.target.at_stripped)" >&2
  fi
  _dm_hop_to_live_seat || true
  _dm_forward_to_successor || true
  if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail unknown; exit 2; fi
  _MISS="$(_missing_seat_hint)"
  emit_json 2 resolve "$_MISS"
  die "$_MISS" 2
fi
if [ "$N_MATCH" != "1" ]; then
  if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail ambiguous; exit 2; fi
  emit_json 2 resolve "座位碼多重命中,拒投"
  echo "aib-dm-ctl: $(_dmt aib.dmctl.code_ambiguous)" >&2
  "$PY3" -c 'import json,sys
for m in json.loads(sys.argv[1])["matches"]:
    print("  window=%s role=%s 座位碼=%s" % (
        m["wid"], m["role"] or "?", "@" + m["code"] if m.get("code") else "量不到"))' \
    "$RESOLVED" >&2
  exit 2
fi

T_WID="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["matches"][0]["wid"])' "$RESOLVED")"
T_CODE="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["matches"][0].get("code") or "")' "$RESOLVED")"
T_PROVIDER="$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1])["matches"][0].get("provider") or "").strip().lower())' "$RESOLVED")"
# ★ 位址與抬頭一律 UUID 尾五碼,不用角色名、窗號或 seat_id。
#   角色名是顯示標籤——會撞名、會換人、會隨編制改;位址必須是身份本身。
#   五碼來源只寫在 src/cockpit/seat_address.py 的 code_of() 一處。
# ★★ EH.17:這裡原本傳 `{"seat_id": ...}` —— 一個**只有 seat_id 的合成 dict**,
#   於是 code_of() 永遠看不到 session_uuid。**端到端實測才抓到**:標籤仍是舊碼,
#   而且每次都觸發遷移期補位出聲 —— 讀碼看不出來,因為 label_of 本身是對的。
#   → 傳整份席位紀錄(解析端已把 `rec` 帶出來)。
# ★ 拒投器:`--resolve-only` 原本只回一個裸 `invalid`,三種拒投理由
#   (沒記 socket / socket 不對 / pid 已死)在那個模式下**逐字相同**。
#   分不出理由的錯誤訊息會把人送去查錯的東西 —— 所以人話理由兩種模式都印。
_seat_refuse() {  # $1=人話(stderr)  $2=emit_json 的 detail
  echo "aib-dm-ctl: $1" >&2
  if [ "$RESOLVE_ONLY" = "1" ]; then _resolve_fail invalid; exit 2; fi
  emit_json 2 resolve "$2"
  exit 2
}

# ★★ 席位 socket 閘(2026-08-29 a seat,規格 ~/.ez2-aib/pending/
#   from-line-restore-ai-20260829-seat-socket.md)。三件事在這裡一起判,
#   B11 前三種目標形式到這裡才合流,其中角色與舊碼路徑曾漏查活性。
#   B11 後只有 UUID 座位碼能走到這裡,但 socket/pid 仍需再次 fail-closed 重驗。
#
#   甲 席位檔沒記 socket ⇒ 拒投並點名那個檔。**不得預設成當下 socket**:
#     window_id 在六個 socket 上都從 @1 起算,猜錯的表現是「送到了、送給別人、
#     收據 rc=0」。這種洞不會有紅燈,只會有一段歸錯人的對話。
#   乙 記的 socket 與當下 hub 不同 ⇒ 拒投並告訴他該用哪個。
#     ★ 這裡刻意**不**改成「自動改用席位檔的 socket 送」:
#       tests/test-aib-dm.py:213-247 用四支臂釘死「一次執行只碰一個 socket」,
#       中途改指向會把那個性質弄掉。跨 socket 投遞是**能力**不是**安全**,
#       要開得另案裁(已寫進交付單「我知道而沒做的」)。
#   丙 席位檔記的 pid 已死 ⇒ 拒投並點名 stale。窗關了席位檔不會自己消失,
#     留下的是一個「看起來收得到、其實那個窗現在住了別人」的地址。
_SEAT_JSON="$("$PY3" -c 'import json,sys
m=json.loads(sys.argv[1])["matches"][0]
r=m.get("rec") or {}
print("%s\t%s" % (r.get("socket") or "", r.get("pid") or 0))' "$RESOLVED")"
T_SOCKET="${_SEAT_JSON%%	*}"
T_PID="${_SEAT_JSON##*	}"
T_SEATFILE="$SEATS_DIR/w${T_WID#@}.json"
if [ -z "$T_SOCKET" ]; then
  # ★★ 2026-08-29 cap 5c59e QA.1 裁 fail-closed(取代前一版的遷移期兩段式)。
  #   前一版是「出聲但放行」,理由是現存席位檔一筆都沒有 socket 欄,直接擋會讓
  #   全艦 dm 同時斷掉。那個理由今天被 `aib seat socket-backfill` 解掉了:
  #   socket 可以從席位檔路徑導出,不必等每一席重開。
  #   ⇒ 翻轉條件已達成的判準是機器形的:**缺 socket 的席位檔數 = 0**,不是「一輪巡查」。
  #   ★ 為什麼不留旗標:留下 AIB_DM_REQUIRE_SEAT_SOCKET 這種可以關掉的旗標,
  #     等於把「要不要守」交給每一個呼叫端,而警告在 pane 上沒有人看
  #     (那正是前一版付過的代價)。這裡不再讀任何環境變數。
  _seat_refuse "席位檔沒記 socket,無法確認它在哪個 hub,拒投:$T_SEATFILE
  用法:先跑 \`aib seat socket-backfill --apply\` 由路徑補上,或重開該席;
        導不出的席位檔要自己確認它活在哪個 hub,本工具不猜。" \
               "席位檔沒有 socket 欄,無法確定要送到哪裡"
fi
# ★ `-n` 這一格是必要的:上面那支在遷移期是「出聲但放行」,放行之後
#   $T_SOCKET 仍然是空字串。少了 -n,空字串會被下一行當成「別的 socket」,
#   於是遷移期的放行在下一行又被擋掉 —— 兩段之間的接縫,我第一版就漏了。
if [ -n "$T_SOCKET" ] && [ "$T_SOCKET" != "$HUB" ]; then
  _seat_refuse "席位 $TARGET 在 socket '$T_SOCKET',當下 hub 是 '$HUB',拒投。請用 EZ2_AIHUB_SOCKET=$T_SOCKET 重送($T_SEATFILE)" \
               "席位在別的 socket 上"
fi
if [ "${T_PID:-0}" -gt 0 ] 2>/dev/null && ! kill -0 "$T_PID" 2>/dev/null; then
  # 09-28:寫了艙位碼就直接落在這一艙,不再經過「找不到活席」那兩個出口;舊碼已死而恰好一席從它分出 ⇒ 一樣轉信。
  _dm_forward_to_successor || true
  _seat_refuse "席位 $TARGET 的 pid $T_PID 已不存在(stale 席位檔),拒投:$T_SEATFILE" \
               "席位檔的行程已死(stale)"
fi

T_LABEL="$("$PY3" -c 'import json,sys,os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), ""))
import seat_address as sa
m=json.loads(sys.argv[1])["matches"][0]
print(sa.display_label(m.get("rec") or {"seat_id": m["seat"]}, m["wid"],
                       allow_legacy=False))' "$RESOLVED" "$HERE/seat_address.py")"

# ★ EH.20 階段 A 的出口:解析完就走,**目標端一個位元組都沒碰過**
#   (沒有 load-buffer、沒有 paste-buffer、沒有 send-keys)。
#   §三 為什麼解析要全有全無就是靠這一點成立的:放棄的成本在這裡是零。
if [ "$RESOLVE_ONLY" = "1" ]; then
  _resolve_ok "$T_WID" "$T_LABEL"
  exit 0
fi
if [ "$PROBE_BOX" = "1" ]; then
  _probe_box
  exit $?
fi

# The seat record is the provider source for both the pre-paste guard and the
# post-Enter confirmation. Unknown/unregistered targets keep legacy detection.
# ★★ 2026-09-02(第二方 b6fdd 複驗 73a1f402 抓到):這裡原本是**寫死名單**
#   `claude|codex|agy|copilot` —— grok 上線為 provider 之後,這張名單沒補,
#   於是 `AIB_DM_TARGET_PROVIDER` 對 grok 一律 unset,**送出後的確認回讀不帶
#   provider** → composer_state 回 unknown → 收據 rc=3「未能確認送出」,
#   而訊息其實已經送到。與方言表缺席同一個病:**第五道閘的人口也是寫死的。**
#   ★ 更糟的是我的 18 格臂**永遠自己傳 provider="grok"**,所以改這張名單
#     臂照樣全綠 —— 臂量的是函式參數,不是產品接線。
#   ⇒ 人口改成向方言表現問(`normalize_provider` 認得就導出),不再各自維護一份。
if "$PY3" -c 'import sys; sys.path.insert(0, sys.argv[1])
import composer_dialect as d
sys.exit(0 if d.normalize_provider(sys.argv[2]) else 1)' \
     "$HERE" "$T_PROVIDER" 2>/dev/null; then
  export AIB_DM_TARGET_PROVIDER="$T_PROVIDER"
else
  unset AIB_DM_TARGET_PROVIDER
fi
# The lower engine's unknown fallback can advertise the all-states bypass when
# its weak regex happens to match. Canonical `aib dm` makes unknown a hard stop;
# known draft/modal states still take their dedicated branches before fallback.
export AIB_DM_STRICT_UNKNOWN=1
# 同一件事的第二張名單:弱樣式守衛只對「分類器認得的框」關掉。
# grok 也是分類器認得的(畫框式),原本卻不在名單裡 → 空框仍走弱樣式而被誤擋。
case "$T_PROVIDER" in
  codex|agy|copilot|grok) export AIB_DM_DRAFT_RE='a^' ;;
esac
fi   # _XH_WHOAMI 跳過的目標閘到這裡

# 發送席推導(不可偽填):hub 窗內=自己的席位檔;hub 外=human 委託人。
# 席位=經機器驗證的綁定(schema+檔名交叉+活窗)而無人證,對映血緣閘的
# bound_unattested(T1,trust_tiers 獨立碼)——這是推導不是自封;判定事件
# 帶 authority_level 留痕(lineage 既有行為)。此對映屬語意裁決點,終審可攔。
SENDER_HAS_SEAT="$("$PY3" -c 'import json,sys; print(1 if json.loads(sys.argv[1])["sender"] else 0)' "$RESOLVED")"
# L8b:通知檔要記「哪幾席被擋過」,所以要知道自己的五碼;推不出來就是 unknown,不編。
AIB_DM_SELF_CODE="$("$PY3" -c 'import json,sys
s=json.loads(sys.argv[1]).get("sender") or {}
print(s.get("code") or "unknown")' "$RESOLVED" 2>/dev/null || echo unknown)"
# L8b R3(派工單原本那條,保留為第三順位):被擋席自己送訊息時也提醒一次。
# ★ 它單獨不夠 —— 09-05 那件裡被擋席 4.5 小時沒有 dm 任何人,這條剛好不會開火。
#   主路徑是右欄每輪都跑的那一條。
if [ "$AIB_DM_SELF_CODE" != "unknown" ] && [ -f "$_COMPOSER_NOTICE" ]; then
  _SELF_NOTICE="$("$PY3" "$_COMPOSER_NOTICE" render --seat "$AIB_DM_SELF_CODE" 2>/dev/null || true)"
  if [ -n "$_SELF_NOTICE" ]; then
    echo "aib-dm-ctl: $(_dmt aib.dmctl.self_notice text="$_SELF_NOTICE")" >&2
    echo "aib-dm-ctl: $(_dmt aib.dmctl.self_notice_tail)" >&2
  fi
fi
if [ "$SENDER_HAS_SEAT" = "1" ]; then
  S_LABEL="$("$PY3" -c 'import json,sys,os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), ""))
import seat_address as sa
s=json.loads(sys.argv[1])["sender"]
print(sa.display_label(s.get("rec") or {"seat_id": s["seat"]}, s["wid"],
                       allow_legacy=False))' "$RESOLVED" "$HERE/seat_address.py")"
  SENDER_JSON="$("$PY3" -c 'import json,sys
s=json.loads(sys.argv[1])["sender"]
print(json.dumps({
    "kind": "main",
    "ai_id": "seat:%s" % s["wid"],
    "sig": "%s@%s/%s" % (s["role"] or "seat", (s["seat"] or "00000")[-5:],
                          (s["provider"] or "?").capitalize()),
    "engine": s["provider"] or "?",
    "trust": "self_reported",
    "registry_verified": False,
    "bound_unattested": True,
}))' "$RESOLVED")"
else
  # ★ EG.5 第一刀(艦長 a seat 08-18 23:39 派工):這裡原本只有一格——
  #   **推不出席位就叫真人**。那是 fail-open,而且是最貴的那一種:`human` 是全艦
  #   權限最高的標籤(血緣閘 lineage.py 對 kind=human 直接 ALLOW human_principal,
  #   理由寫的是「人類是委託人」),卻被當成「什麼都證明不了」時的預設值。
  #   實證(艦外 23:39):艦外 AI 送的 dm 就這樣掛上 human 抬頭,收件端無從分辨。
  #   本刀只拆一件事:能被機器看出是 AI 的,拆進第三格 agent_offhub。
  #
  # ★★ 這一格的能力邊界(寫在這裡,免得下一個人以為它比實際更強):
  #   下面的標記全是**送方行程自己的環境變數**——判準住在對方的機器上。
  #   所以它擋得住「誠實的 AI 被誤認成人」,擋不住「刻意把標記拿掉來冒充人」。
  #   後者需要人類憑證(正面證明「我是 the maintainer」),那是第二刀,不在本刀。
  #   之所以現在就值得做:今天走艦外通道的 100% 是誠實的 AI,而它們**全部**
  #   被標成人類;把這一格拆出來,等於把今天實際在發生的那句謊話關掉。
  #   ★ 順序也是刻意的:先判 AI 再落 human。human 是**兜底**,不是**預設**——
  #     兜底可以錯,預設會被人拿去當證據。$USER 一律不參與 AI 那一格的判定
  #     (負向測試 test_user_env_cannot_flip_agent_back_to_human 釘住)。
  OFFHUB_AGENT=0
  if [ -n "${EZ2_AIB_AGENT:-}" ]; then
    OFFHUB_AGENT=1              # 自報通道:艦外行程明說自己是 AI(最乾淨的一條)
  elif [ -n "${CLAUDECODE:-}" ] || [ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ]; then
    OFFHUB_AGENT=1              # 引擎標記:本機實測 claude code 行程必帶(08-18 dev4 量)
  fi
  if [ -n "$HUMAN_ID" ]; then
    # 第二刀:三件正面憑證全到(上面 human_attest 那段)才走到這裡。抬頭用白名單既有的 `human:名`
    # (seat_address.py 的白名單不在本包寫集;規格寫「真人<識別>」那個字面留給正文前綴)。
    S_LABEL="human:$HUMAN_ID"
    SENDER_JSON="$("$PY3" -c 'import json,sys
d = {}
try:
    d = json.loads(sys.argv[2])
except Exception:
    d = {}
print(json.dumps({
    "kind": "human",
    "ai_id": "human:%s" % sys.argv[1],
    "sig": "human@0000/%s" % sys.argv[1],
    "engine": "human",
    "trust": "self_reported",
    "registry_verified": False,
    "attested": True,
    "attest_evidence": d.get("evidence"),
}))' "$HUMAN_ID" "$HUMAN_ATTEST_JSON")"
  elif [ "$OFFHUB_AGENT" = "1" ]; then
  # ★★ 靶心修正(艦長 00:08 併入,root2 真實樣本+雙端復現,不是設想):
  #   hub 內綁定良好的席位,只因為在同一個 shell 裡 `export TMUX=`(為了用
  #   capture-pane 驗稿)就無聲降級成 human:user——上面 :94 的首條件
  #   `[ -n "${TMUX:-}" ]` 落空 → _SELF_WID 空 → 掉進這個 else。
  #   root2 原話值得留給後人:**「為了驗證通訊可信度而弄壞通訊可信度」**。
  #   ★ 所以真正該問的問題不是「他是不是 hub 外」,而是
  #     **「身份推導失敗的時候,預設落哪一格?」**——落 human 就是 fail-open 的本體:
  #     一個環境變數被清掉,就從「證明不了」變成「權限最高」。
  #   → 本刀把 human 從**兜底**也一起拿掉:推導失敗一律落低權格。
  #     dm-ctl 現在**不再產生任何 human 標籤**;human 要回來,必須帶著正面憑證
  #     (證明「我是 the maintainer」),那是第二刀。在那之前,「不知道」就誠實寫「不知道」。
    # 抬頭刻意**不帶名字**:判準是「不需知道是誰,只需知道不是誰」。
    # 讓送方自選顯示名 = 又開一個冒充面(agent:root-brain 這種),本刀不開。
    S_LABEL="agent:offhub"
    SENDER_JSON="$("$PY3" -c 'import json,os
declared = (os.environ.get("EZ2_AIB_AGENT") or "").strip()
engine = (os.environ.get("EZ2_AIB_AGENT_ENGINE") or "").strip()
if not engine:
    engine = "claude" if (os.environ.get("CLAUDECODE")
                          or os.environ.get("CLAUDE_CODE_ENTRYPOINT")) else "?"
print(json.dumps({
    "kind": "agent_offhub",
    "ai_id": "agent:offhub",
    "sig": "agent@0000/Offhub",
    "engine": engine,
    # 自報名只進 JSON 不進抬頭:留痕用,不可當身份用。
    "declared_name": declared or None,
    "trust": "self_reported",
    "registry_verified": False,
    "attested": False,
}))')"
  else
    # 什麼都推導不出來:**不猜、也不升權**。不寫 human(那是在替對方作證),
    # 不寫 agent(那是在替對方分類);只寫「這個身份沒有經過任何證實」。
    # ★ $USER 連在這一格都不進標籤:它是自報值,寫進去會讓收件端誤以為
    #   系統認得這個人。要留痕就留在 JSON 的 declared_user,標明是自報。
    # ★ 來源名(2026-09-05,the maintainer 問「為什麼前面是 unattested:unknown」)。
    #   上面 offhub 那一段拒絕的是**送方自選**顯示名(那是再開一個冒充面);
    #   這裡不繞過那條裁定,而是讓它的前提消失 —— 名字由 `sender_origin.py`
    #   從**行程祖先鏈**推導,取最近一支本倉 `src/` 產品腳本的檔名。
    #   要冒充得先偽造自己的 ppid 鏈,不是設一個環境變數。
    #   ★ kind 與權限**一格不動**:仍是 `unattested`,血緣閘仍走 `unattested_sender`。
    #     這一刀只讓抬頭說得出來源,不動任何一格權限。
    #   ★ 推不出來(真人從純終端送、測試 harness 送)一律回 `unknown` ——
    #     失效方向刻意落在低權格,不猜、不升權。
    #   ★ 白名單本來就准 `unattested:名`(seat_address.py:434),不必動白名單。
    #   ★ 09-05 追加(the maintainer 令):機器名後面掛一個 i18n 白話名,形狀與 `@五碼(暱稱)` 同。
    #     機器名永遠留著 —— 它可 grep、可反查是哪一支檔;白話名只是給人看的。
    #     查不到白話名就只有機器名,不編一個假的。
    #   ★ 09-10 追加(the maintainer 令):畫面那一行,來源推導得出且有白話名 ⇒ 只印白話名;
    #     帳本 ai_id/kind 仍是 unattested:<origin>,權限一格不動。
    #     unknown 或無白話名 ⇒ 照舊 unattested:<name>。
    #   ★ 09-10 HDR:席位對映優先於腳本名。對到五碼 ⇒ 抬頭印 @五碼,
    #     不再印「AI 啟動器」。kind 仍是 unattested,權限一格不動。
    if [ "$S_ORIGIN_KIND" = "seat" ] && [ -n "${S_ORIGIN//[[:space:]]/}" ]; then
      S_LABEL="@$S_ORIGIN"
      # ★ 09-29 a seat:這條路的抬頭一直印「未綁@五碼」,不管那一席綁了誰(真機:grok 席 a seat
      #   綁了 SEO-GEO大師,回信抬頭兩次都是「a seat」)。艙門洗過環境的席沒有 TMUX ⇒ 上面
      #   SENDER_HAS_SEAT=0 ⇒ 走到這裡,而這裡只放了五碼、沒有去查名字。
      #   名字與上面那一格同一個來源(seat_address.display_label ⇒ 顯示投影),不是送方自選;
      #   查不到、或查回來的五碼不是推導出來的那一個 ⇒ 維持只有五碼。kind/權限一格不動。
      _S_NAMED="$("$PY3" -c 'import glob, json, os, sys
sys.path.insert(0, sys.argv[1])
import seat_address as sa
code, seats_dir = sys.argv[2], sys.argv[3]
label = ""
for path in sorted(glob.glob(os.path.join(seats_dir, "w*.json"))):
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if isinstance(rec, dict) and sa.code_of(rec) == code:
        label = sa.display_label(rec, rec.get("window_id"), allow_legacy=False)
        break
print(label if sa.code_in_label(label) == code else "")' "$HERE" "$S_ORIGIN" "$SEATS_DIR" 2>/dev/null || true)"
      case "$_S_NAMED" in "@$S_ORIGIN("*")") S_LABEL="$_S_NAMED" ;; esac
      S_ORIGIN_SOURCE="pane_pid_chain"
    elif [ "$S_ORIGIN_KIND" = "bgfork" ] && [ -n "${S_ORIGIN//[[:space:]]/}" ]; then
      # ★ 10-10 a seat(the maintainer 裁):Claude Code 背景分身寄的信,抬頭標「@原席五碼(背景分身)」——
      #   五碼是機器從分身命令列的 --resume 量出來的(不是自報),回信回到原席;括號是結構性標記,
      #   讀的人分得出不是那一席本人在 pane 裡寫的。白名單形狀 `@五碼(…)` 本來就准,不必動白名單。
      S_LABEL="@$S_ORIGIN(背景分身)"
      S_ORIGIN_SOURCE="bg_fork_chain"
    elif [ -n "$S_CLAIMED_SEAT" ]; then
      # ★ 弱態要**結構性標記**,不是換個說法:「自稱」兩個字必須在,強態不得有。
      #   只讓兩種字樣「不一樣」守不住 —— 下一個改字樣的人會把它改成相近的另一句,
      #   而相近與相同在版面上分不出來(未結案 27 已經付過這個代價)。
      #   刻意不以 @ 開頭:@ 開頭是「量到的席」那一格的形狀,借用它等於把弱的裝成強的。
      S_LABEL="seat_claimed:$S_CLAIMED_SEAT(自稱)"
      # 來源照實記是哪一條路帶來的證據(MCP 旗標 / codex 工具行程環境),不一律寫成 MCP 那條。
      S_ORIGIN_SOURCE="${S_CLAIMED_SOURCE:-codex_session_meta}"
      # 同一個決定點:抬頭認了自稱,帳本才跟著記。四個欄位(label / kind / seat / source)
      # 從此只有一個出處,不會互相矛盾。
      _V2_ARGS+=(--sender-seat "$S_CLAIMED_SEAT" --sender-source "$S_ORIGIN_SOURCE")
      # 抬頭的艙位碼跟著那一席自己的艙走;前面已從寄件人自己的 pane 認出 hub 的,不蓋。
      [ -z "${_SENDER_HUB:-}" ] && [ -n "$S_CLAIMED_HUB" ] && _SENDER_HUB="$S_CLAIMED_HUB"
    else
      [ -n "${S_ORIGIN//[[:space:]]/}" ] || S_ORIGIN="unknown"
      S_PLAIN="$("$PY3" -c 'import sys,os
sys.path.insert(0, sys.argv[1])
import sender_origin as so
print(so.display_label(sys.argv[2]) or "")
' "$HERE" "$S_ORIGIN" 2>/dev/null || true)"
      if [ "$S_ORIGIN" != "unknown" ] && [ -n "${S_PLAIN//[[:space:]]/}" ]; then
        S_LABEL="$S_PLAIN"
      else
        S_LABEL="unattested:$S_ORIGIN"
      fi
      S_ORIGIN_SOURCE="ppid_chain"
      [ "$S_ORIGIN" = "unknown" ] && S_ORIGIN_SOURCE=""
    fi
    SENDER_JSON="$("$PY3" -c 'import json,os,sys
origin = sys.argv[1]
source = sys.argv[2] or None
print(json.dumps({
    "kind": "unattested",
    "ai_id": "unattested:%s" % origin,
    "sig": "unattested@0000/%s" % (origin.capitalize() if origin else "Unknown"),
    "declared_user": os.environ.get("USER") or None,
    # 留痕:名字是推導來的,不是自報的 —— 讀帳的人要分得出這兩件事
    "origin_name": None if origin == "unknown" else origin,
    "origin_source": source,
    "trust": "self_reported",
    "registry_verified": False,
    "attested": False,
}))' "$S_ORIGIN" "$S_ORIGIN_SOURCE")"
  fi
fi

# 跨主機推導那一輪到這裡為止:寄件人已照上面同一套規則推完,印 token 給寄件那一輪,不送。
if [ "$_XH_WHOAMI" = "1" ]; then
  _xh_kind="$S_ORIGIN_KIND"; case "$S_LABEL" in *"(背景分身)") _xh_kind="bgfork" ;; esac
  exec "$PY3" "$HERE/dm_xhost.py" encode "$S_LABEL" "${S_ORIGIN:-}" "$_xh_kind" "${_SENDER_HUB:-$HUB}"
fi



# ── 排隊投遞保留原寄件人(the maintainer 09-30 15:3x,收件匣 A 項 2)────────────────────
#   災情:佇列只存正文不存抬頭,排空器(或真人按「立刻送出」)重投時,上面那一大段是從**投遞者自己的**
#   行程鏈推寄件人 ⇒ 排空器送的抬頭變「系統@aib」、滑鼠送的變「the maintainer」,收件席看不出是誰寄的、也回錯人。
#   修法:投遞者只遞**這一則的 id**(AIB_DM_QUEUED_ITEM_ID),本檔自己回佇列讀那一則:
#     ・只在 AIB_DM_SORT_DELIVER=1 時才看(一般 aib dm 設了也不理);
#     ・那一則要是 claimed(投遞者手上正在貼的那一則),且這次要貼的正文結尾要等於它的原文
#       (前面可以多一行時效)—— 拿別則的 id 套不到這一則的正文上;
#     ・原寄件人 token 要過同一道抬頭白名單、而且 dm_header 組得出抬頭,才採用;任一不成立 ⇒ 照舊用推導的。
#   ★ 能力邊界(寫清楚免得有人以為比實際強):佇列的 enqueue CLI 本來就收任意 --sender-label,
#     本機行程若刻意先入列再自己 claim,仍可讓抬頭寫成它入列時填的字;這一段擋的是「設一個環境變數就改抬頭」。
#   ★ 只改抬頭的寄件方;SENDER_JSON(帳本/血緣閘看的那筆)仍記**實際貼稿的行程**,權限一格不動。
#   ★ 自填寄件身分的旗標(--sender / --as / --sender-seat / --human-id)仍一律 die,這裡沒有開那扇門。
# 寄件端把自己那一艙與來源機器名交給佇列(入列/留歷史時記下),日後排隊投遞才寫得出原寄件人的艙位碼。
# 跨主機收件(上面驗過):抬頭寄件方換成寄件機推導的那一席;來源記成 xhost:<sshd 量到的對方 IP>,
#   忙碌入列後排空重投也認得出是跨主機(下面排隊那段)。帳本 SENDER_JSON 不動:仍是實際貼稿的 SSH 行程,低權格。
if [ "$_XH_IN" = "1" ]; then
  S_LABEL="$_XH_LABEL"; S_ORIGIN="xhost:$_XH_IP"
  [ -n "$_XH_HUB" ] && _SENDER_HUB="$_XH_HUB"
fi
[ -n "${_SENDER_HUB:-$HUB}" ] && _V2_ARGS+=("--sender-hub=${_SENDER_HUB:-$HUB}")
[ -n "${S_ORIGIN:-}" ] && _V2_ARGS+=("--sender-origin=${S_ORIGIN}")

_HDR_S_LABEL="$S_LABEL"; _HDR_S_ORIGIN="${S_ORIGIN:-}"; _HDR_S_ADDR=""; _HDR_S_NOTE=""
_xh_hdr() { _HDR_S_ADDR="${1#xhost:}"; _HDR_S_NOTE="跨主機·經SSH"; }
if [ "$_XH_IN" = "1" ]; then _xh_hdr "$_XH_IP"; fi
# review r2 m6:那一則住在哪一席的佇列 = 投遞者說的(AIB_DM_QUEUED_SEAT;轉給後繼席時由上面帶過來),沒說才用收件席
_Q_SEAT="${AIB_DM_QUEUED_SEAT:-${T_CODE:-}}"
if [ "${AIB_DM_SORT_DELIVER:-0}" = "1" ] && [ -n "${AIB_DM_QUEUED_ITEM_ID:-}" ] && [ -n "$_Q_SEAT" ]; then
  _q_orig="$(printf '%s' "${_MSG_PRE_RELAY:-$MSG}" | "$PY3" -c 'import sys, os
sys.path.insert(0, sys.argv[1])
import dm_queue as Q
seat, qid = sys.argv[2], sys.argv[3]
if not Q.ITEM_RE.fullmatch(qid or "") or not Q.SEAT_RE.fullmatch(seat or ""):
    raise SystemExit(1)
it = Q._load_item(seat, qid) or {}
body = str(it.get("msg") or "").rstrip("\n")
sent = sys.stdin.read().rstrip("\n")
if it.get("status") != "claimed" or not body or not sent.endswith(body):
    raise SystemExit(1)
lab = str(it.get("sender_label") or "")
if not lab or "\n" in lab or "\x1f" in lab or lab == "未知":
    raise SystemExit(1)
print("%s\x1f%s\x1f%s" % (lab, it.get("sender_origin") or "", Q._hub_or_empty(it.get("sender_hub"))))' \
    "$HERE" "$_Q_SEAT" "$AIB_DM_QUEUED_ITEM_ID" 2>/dev/null)" || _q_orig=""
  if [ -n "$_q_orig" ]; then
    # 分隔用 \x1f 不用 tab:tab 是 IFS 空白字元,連續兩個會被併成一個 ⇒ 來源名空的時候艙名會跑到來源那一欄
    IFS=$'\x1f' read -r _q_lab _q_org _q_hub <<<"$_q_orig"
    if [ -n "$_q_lab" ] && "$PY3" -c 'import sys,os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[2])))
import seat_address as sa
sa.assert_address_whitelist(sys.argv[1], "dm 抬頭")' "[$_q_lab > $T_LABEL / 台北 $(date '+%m-%d %H:%M')]" "$HERE/seat_address.py" 2>/dev/null \
       && "$PY3" "$HERE/dm_header.py" from-labels "$_q_lab" "$T_LABEL" "$_q_org" "${_q_hub:-$HUB}" "" >/dev/null 2>&1; then
      # 艙位碼也換成原寄件人那一艙(_SENDER_HUB 在這之後只剩抬頭這一個讀者);信上沒記艙 ⇒ 不動
      _HDR_S_LABEL="$_q_lab"; _HDR_S_ORIGIN="$_q_org"; [ -n "$_q_hub" ] && _SENDER_HUB="$_q_hub"
      case "$_q_org" in xhost:*) _xh_hdr "$_q_org" ;; esac      # 跨主機那一則排隊後重投:位址與標記照入列時的
    else
      echo "aib-dm-ctl: $(_dmt aib.dmctl.queued_sender_fallback label="$_q_lab")" >&2
    fi
  fi
fi
# 自動標頭(規格3)。★ 包 I(the maintainer 09-26 18:5x「所有的 aib DM 都應如此,必要程式化」):
#   新式 `IP:位址/艙號/名@席碼 >> IP:…/…/名@席碼 MM-DD HH:MM(台北時間)`,**唯一產生處是 dm_header.py**,
#   本檔只把內部的寄件/收件 token 交給它。白名單閘照舊驗 token(組成舊式那一行餵給同一支 assert,語意不變)。
_LEGACY_HEADER="[$S_LABEL > $T_LABEL / 台北 $(date '+%m-%d %H:%M')]"
# 閘(the maintainer 直令三 + dev4⚓ 二方審 A):抬頭的位址半邊走**白名單**,不是黑名單。
# 黑名單只擋得住今天在編的角色名——root3/pm4/integ/cap3/flt2/dev6 這些歷史用過與
# 未來新增的一律放行,**每多一個角色就多一個洞而沒人會收到通知**。
# 白名單只准 @五碼[(暱稱)] / human:名 / agent:名 / unattested:名 / seat_claimed:五碼(自稱),
# 新角色名不必登記就自動被擋。
# ★ B11:窗號不只從抬頭白名單移除,也已從 dm 目標形式退役；兩層同軌。
# ★ 型別集合與 `seat_address.py` 同源(該檔 docstring 與 ValueError 各一份);
#   三處字面必須一致,測試以「集合相等」釘住——只改字面不加釘子=等下一個人再犯。
if ! "$PY3" -c 'import sys,os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[2])))
import seat_address as sa
sa.assert_address_whitelist(sys.argv[1], "dm 抬頭")' "$_LEGACY_HEADER" "$HERE/seat_address.py" 2>/dev/null; then
  emit_json 2 header "抬頭位址不合白名單,拒送(只准 @五碼[(暱稱)] / human:名 / agent:名 / unattested:名 / seat_claimed:五碼(自稱))"
  die "抬頭位址不合白名單,拒送:$_LEGACY_HEADER" 2
fi
_T_SOCKET="$("$PY3" -c 'import json,sys
m=json.loads(sys.argv[1])["matches"][0]
print((m.get("rec") or {}).get("socket") or "")' "$RESOLVED" 2>/dev/null || true)"
if ! HEADER="$("$PY3" "$HERE/dm_header.py" from-labels "$_HDR_S_LABEL" "$T_LABEL" "$_HDR_S_ORIGIN" "${_SENDER_HUB:-$HUB}" "$_T_SOCKET" "$_HDR_S_ADDR" "$_HDR_S_NOTE")" \
   || [ -z "$HEADER" ]; then
  emit_json 2 header "抬頭產生失敗(dm_header.py from-labels)"
  die "抬頭產生失敗:[$S_LABEL > $T_LABEL]" 2
fi
# 抬頭與正文同一行、中間一個空格(a seat 22:54 裁;多行貼上會被 Claude Code 折成 [Pasted text])。
BODY="$HEADER $MSG"

# ★★ EH.17 階段 3a(a seat 找到、a seat 量到、艦長 07:19 開工):
#   `label_of()` 的 kind=emit 只看得到**席位紀錄**,而**手寫在正文裡的舊碼
#   一次都不經過它** —— 於是 emit 恆為 0,而那個 0 正是階段 4 的翻轉條件之一。
#   **失效值等於它表示安全的那個值。**
#   實測(a seat 自己 08-22 那晚 24 份稿):舊碼 43 次 / 新碼 3 次 ——
#   **光把產生端換掉不會讓舊碼消失,因為引用別席位址是散文行為不是 API 呼叫。**
# ★ 只掃**位址位置**(`@` 前綴 + 詞界);全文數字另印成趨勢指標,不當翻轉條件。
#   否則掃描器會掃到「討論舊碼的訊息」——包括宣告這條規則的那一封。
# ★ 出聲不改 rc:散文裡引用別席位址現在是正常行為,報錯會讓遷移期無法運作。
"$PY3" -c 'import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[3])))
import seat_address as sa
codes = sa.legacy_codes_from_seats(sys.argv[2])
sa.voice_legacy_in_body(sys.argv[1], codes)' \
  "$BODY" "$SEATS_DIR" "$HERE/seat_address.py" >/dev/null || true
# ★ stderr 本來就透傳,不要用 `2>&1 >/dev/null | tee /dev/stderr` 那種寫法:
#   那串多製造兩個失效點(管線 rc、/dev/stderr 在某些環境不可寫),
#   而它要達成的事「讓出聲上 stderr」預設就成立。**繞路本身就是缺陷來源。**


# 咽喉(R30+血緣閘一行未動)→引擎。CLI 層真送預設=DM UX;--dry-run 走
# 咽喉原生預覽(咽喉層預設 dry-run 的原語意不動,見 aib-send.sh 註)。
# ★★ 刀 M(艦長 04:44 裁(甲)):抬頭邊界由**產生抬頭的人**明示,引擎不自己解析。
#   引擎拿它跳過抬頭取指紋——抬頭是每封都一樣的樣板,**進了指紋就等於指紋恆真**
#   (root2:前 24 字是一個預算,抬頭花掉多少、時戳就剩多少機會擠進去;而預算
#   消耗量取決於送件席與收件席兩邊的標籤長度,**改一次角色名就可能讓原本安全的
#   一對變成暴露**)。故不是縮短抬頭,是指紋不碰抬頭。
#   值=`$HEADER ` 在 BODY 前面佔的**字元數**(結構,不是常數 N);算它要釘 locale,
#   理由與引擎同段:`${#var}` 的字元/位元組語意隨 locale 變。
_hdr_prefix="$HEADER "
# ★ 10-04(雲端實踩):en_US.UTF-8 不是每台都有(Linux 雲端只有 C.UTF-8)。設不進去時 bash 只印警告、
#   照舊用位元組切 ⇒ 中文指紋切在字元中間、回讀比不到 ⇒ 長稿誤判。改:依序試 AIB_DM_FP_LOCALE、en_US.UTF-8、
#   C.UTF-8、C.utf8,取第一個「一個漢字長度 = 1」的;不叫外部程式(locale -a),不改呼叫端的 LC_ALL。
#   ★ 兩處同一套挑法(dm-ctl.sh 算 offset、aib-dm.sh 取指紋),挑出來的必須一樣;改一處要改另一處。
_aib_pick_utf8_lc() {
  local _c _s='中' _sv="${LC_ALL-__aib_unset__}" _pick=""
  for _c in "${AIB_DM_FP_LOCALE:-}" en_US.UTF-8 C.UTF-8 C.utf8; do
    [ -n "$_c" ] || continue
    LC_ALL=C   # ★ 先回 C:設不進去的 locale 會讓 bash 留在前一個,若前一個已是 UTF-8 就誤判(雲端實踩)
    { LC_ALL="$_c"; } 2>/dev/null
    if [ "${#_s}" = 1 ]; then _pick="$_c"; break; fi
  done
  if [ "$_sv" = "__aib_unset__" ]; then unset LC_ALL; else LC_ALL="$_sv"; fi
  printf '%s' "${_pick:-${AIB_DM_FP_LOCALE:-en_US.UTF-8}}"
}
_lc_pick="$(_aib_pick_utf8_lc)"
_lc_saved="${LC_ALL-__aib_unset__}"
LC_ALL="$_lc_pick"
AIB_DM_BODY_OFFSET="${#_hdr_prefix}"
# ★★ SPEC §二:那個「+45」**不是常數**。08-21 同一席同一晚兩種結果——
#   稿 1237 → 框內 1282(差 45,含抬頭)、稿 1163 → 框內 1163(差 0)。
#   連對兩次就被當成定值,而補 Enter 白名單第三條建在它上面。
#   → **工具知道自己貼了什麼,不要讓使用者去推**:抬頭由本檔自己組,
#     長度它自己算得出來,故比對的是**集合** {整串, 去抬頭},不是任何差值。
#   兩個值都在**同一個釘死的 locale** 下量(理由同上一段:`${#var}` 的
#   字元/位元組語意隨 locale 變)。
_AE_CHARS_BODY="${#BODY}"
if [ "$_lc_saved" = "__aib_unset__" ]; then unset LC_ALL; else LC_ALL="$_lc_saved"; fi
_AE_CHARS_MSG=$(( _AE_CHARS_BODY - AIB_DM_BODY_OFFSET ))
# 摺疊型(claude 方言)比的是行數:引擎用 `K+1 == 檔行數`,這裡同式同源。
_AE_LINES_BODY="$(printf '%s\n' "$BODY" | wc -l | tr -d '[:space:]')"
export AIB_DM_BODY_OFFSET
# ★ 耦合(qa3 22:51 第 1 條)另一半 —— 載體 env 名正本:AIB_DM_INPUT_SELECTOR。
#   艦長 a seat 2026-08-29 裁:原提名席 a seat 已關席,採引擎既有唯一位置字面
#   (aib-dm.sh 的 _DM_SEL_CARRIER_NAME 預設值),自此兩半同引;改名時兩處同改。
#   語意=第六欄「人打進 aib dm 的那個字」:單席時是 $TARGET;
#   多席時父層已按 _SELS 逐席設好(child 的 $TARGET 是 canonical @N,不得覆寫)。
if [ -z "${AIB_DM_INPUT_SELECTOR:-}" ]; then AIB_DM_INPUT_SELECTOR="$TARGET"; fi
export AIB_DM_INPUT_SELECTOR
# 寄件人選擇子交給引擎寫 v3。有席=uuid 尾五碼;推不出=哨兵,不記空字串。
# 載體名與 aib-dm.sh 的 _DM_SENDER_CARRIER_NAME 預設值同一字面。
if [ -z "${AIB_DM_SENDER_SELECTOR:-}" ]; then
  AIB_DM_SENDER_SELECTOR="$("$PY3" -c '
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[2])))
import seat_address as sa
d = json.loads(sys.argv[1])
s = d.get("sender") or {}
rec = s.get("rec") or {}
print(sa.code_of(rec) or "")
' "$RESOLVED" "$HERE/seat_address.py" 2>/dev/null || true)"
fi
if [ -z "${AIB_DM_SENDER_SELECTOR//[[:space:]]/}" ]; then
  AIB_DM_SENDER_SELECTOR="-"
fi
export AIB_DM_SENDER_SELECTOR
# 進階版+開關打開且對方忙碌:入排序暫存,不貼稿。AIB_DM_SORT_DELIVER=1 是暫存
# 真正送出時走原路,不再排第二次。dry-run 不入列,既有臂才不會變色。
# --verify(測試送出)標 origin=test,真信與測試信才分得開;未指定才寫。
if [ "${VERIFY:-0}" = "1" ] && [ -z "${AIB_DM_QUEUE_ORIGIN:-}" ]; then
  AIB_DM_QUEUE_ORIGIN=test
  export AIB_DM_QUEUE_ORIGIN
fi
if [ "${AIB_DM_PARK_NOW:-0}" = "1" ]; then
  _park_on_draft
  exit $?
fi
if [ "$DRYRUN" != "1" ] && [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ] \
     && [ -n "$T_CODE" ] && [ -f "$HERE/dm_queue.py" ]; then
  _Q_KIND="$(_dm_queue_kind "$S_LABEL")"
  _Q_UUID="$("$PY3" -c 'import json,sys
print(((json.loads(sys.argv[1]).get("matches") or [{}])[0].get("rec") or {}).get("session_uuid") or "")' \
    "$RESOLVED" 2>/dev/null || true)"
  # ★ P3 第二包:intercept → upsert(接口 §3;廣播型同 thread 取代舊版,其餘 = intercept)。
  #   **失敗一律拒送 rc=2,不退回直送**:原本 `|| true` 吞掉錯誤後會直送,而直送的對象正是
  #   「被判忙碌」的席 —— 疊稿。這也把合入順序做成機器擋:P2 沒上 main 時這裡每一封都具名紅,
  #   不會有一封悄悄變成舊行為。
  # stderr 併進 stdout 一起收(不開暫存檔:test-dm-fingerprint 釘著「鏈上不得自己推導 TMPDIR」),
  # JSON 那一行由下面的 python 從混合輸出裡挑出來;其餘行是失敗理由。
  _Q_RAW="$(printf '%s' "$MSG" | "$PY3" "$HERE/dm_queue.py" upsert \
      --seat "$T_CODE" --sender-label "$S_LABEL" --sender-kind "$_Q_KIND" \
      --session-uuid "$_Q_UUID" --wid "$T_WID" --provider "$T_PROVIDER" \
      --hub "$HUB" --priority "${AIB_DM_QUEUE_PRIORITY:-normal}" \
      ${_V2_ARGS[@]+"${_V2_ARGS[@]}"} 2>&1)"
  _Q_RC=$?
  _Q_OUT=""; _Q_TAKE="__unparsable__"
  _Q_PARSED="$("$PY3" -c '
import json, sys
raw = sys.argv[1]
for ln in raw.splitlines():
    t = ln.strip()
    if not t.startswith("{"):
        continue
    try:
        o = json.loads(t)
    except ValueError:
        continue
    if isinstance(o, dict) and "take" in o:
        print(t)
        break
' "$_Q_RAW" 2>/dev/null || true)"
  if [ -n "$_Q_PARSED" ]; then
    _Q_OUT="$_Q_PARSED"
    _Q_TAKE="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("take") or "")' "$_Q_OUT" 2>/dev/null || echo "__unparsable__")"
  fi
  if [ "$_Q_RC" != "0" ] || [ "$_Q_TAKE" = "__unparsable__" ]; then
    _Q_WHY="$(printf '%s\n' "$_Q_RAW" | grep -v '^{' | tail -n 2 | tr '\n' ' ')"
    emit_json 2 queue-unavailable "佇列 upsert 失敗(rc=$_Q_RC):拒送、不退回直送;一個字都沒進目標,可重送。${_Q_WHY}"
    die "佇列 upsert 失敗(rc=${_Q_RC}):${_Q_WHY}" 2
  fi
  if [ "$_Q_TAKE" = "queue" ]; then
    "$PY3" -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["receipt"], ensure_ascii=False))' \
      "$_Q_OUT"
    echo "aib-dm-ctl: $(_dmt aib.dmctl.queued)"
    _RESULT_STAGE="sort-queued"
    _RESULT_ID="$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1]).get("receipt") or {}).get("queue_id") or "-")' "$_Q_OUT" 2>/dev/null || echo "-")"
    exit 0
  fi
fi
THROAT_ARGS=(--verb dm --to "$T_LABEL" --body "$BODY" \
             --sender-json "$SENDER_JSON" \
             --tmux-socket "$HUB" --tmux-window "$T_WID")
if [ "$DRYRUN" = "1" ]; then
  bash "$THROAT" "${THROAT_ARGS[@]}"
  RC=$?
  [ "$RC" = "0" ] && emit_json 0 dry-run "會送(未送)" || emit_json 2 dry-run "閘門或內容擋下 rc=$RC"
  exit "$([ "$RC" = "0" ] && echo 0 || echo 2)"
fi

# 從這裡往下是真投遞:結束時(EXIT trap)按 rc 把這一則記進歷史(接口 §3 record):
#   rc0 → delivered、rc3/4 → hold_human;rc2 一個字都沒進目標,不記。排空器重投的那則
#   已經在佇列裡(由排空器 _settle_delivery 改狀態),不記第二份。
[ "${AIB_DM_SORT_DELIVER:-0}" != "1" ] && _RECORD_ELIGIBLE=1
# ★ --verify 的 BEFORE 必須在**送出之前**抓(dev1 I3:舊版只比 AFTER、零 BEFORE,
#   且探針取**抬頭**前 24 字 → 對任何曾送過該窗的席位結構性恆真,卻印「已見於畫面」)。
_VBEFORE=""
if [ "$VERIFY" = "1" ]; then
  _VBEFORE="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null | tr -d '[:space:]')"
fi
# 引擎 stdout 收一份:標籤要進 JSON(見 emit_json 註)。tee 保留人話輸出;
# rc 取 PIPESTATUS[0]——本檔 `set -o pipefail`,直接用 $? 會拿到管線最右的碼。
# ★★ 落點要探可寫性,不能只信 $TMPDIR(實測:沙箱 TMPDIR
#   `/var/folders/.../T/` 下 `mktemp` rc=1)。**這正是本鏈引擎註解裡記著的那個疤**
#   ——`aib-dm.sh` 檔頭寫「:84 mktemp 又重複推導同一式子,此處才是真阻斷」,
#   而我在 dm-ctl 又重寫了一次同一式子。**同一個坑,同一條鏈,第三次。**
#   ★ 失效形狀是靜默降級:log 拿不到 → `verified` 與 `before_placeholders`
#     **同時**退成 unknown/null,而投遞照樣成功——**兩個欄位一起壞,正是上游同源的指紋**
#     (艦長 23:40 兩端量到的就是這個,他猜的 `${5-}` 不是成因)。
_ENG_LOG="$(_dm_ctl_tmp)" || _ENG_LOG=""
_IDENTITY_PROMPT_REGION=""
_IDENTITY_PROMPT_LINE=""
if [ -n "$_ENG_LOG" ]; then
  # ★★ EH.27:這裡原本是 `| tee "$_ENG_LOG"` —— 引擎的話**即時**印到畫面上。
  #   那在引擎是最後一個發言者的年代是對的。2026-08-22 自動補 Enter 上線之後
  #   **引擎變成了中間步驟**,而它的措辭沒有跟著降級:rc5 那句逐字是
  #   「已中止**且不送 Enter**;請 capture 確認框空後才重送」,
  #   而同一次執行裡 dm-ctl **送了 Enter 而且成功**。
  #   → 於是人看到的順序是:先一句已作廢的「不送 Enter、請重送」,再一句「已送出」。
  #   ★ 問題不只是「重送」那兩個字:**那是一個中間狀態的宣稱被印成了結局**,
  #     而它在前面 —— 由上往下讀的人會照它做,而它叫人做的正是白名單禁止的事。
  #   ★★ 修法選「先收起來、結局定了再印」而不是「事後再補一句否決」:
  #     否決句在**後面**,而畫面被截斷、被 grep、被引用時**後面那句最先掉**。
  #     註記要跟著那一行走,不能跟在它後面 —— 與「追記寫進 commit 自己身上」同一條。
  bash "$THROAT" "${THROAT_ARGS[@]}" --execute > "$_ENG_LOG" 2>&1
  RC=$?
  VERIFIED_LABEL="$(sed -n 's/.*傳輸已驗證\[\([^]]*\)\].*/\1/p' "$_ENG_LOG" | tail -n 1)"
  # 引擎是子行程,它的 export 不會回流;從它印出來的訊息把貼前佔位符撈回來。
  # 抓「貼前佔位符=」整段(含哨兵 NONE);沒有這一行=引擎沒報=null。
  AIB_DM_BEFORE_PLACEHOLDERS="$(sed -n 's/.*貼前佔位符=\(.*\)$/\1/p' "$_ENG_LOG" | tail -n 1)"
  # ★ 貼前**輸入框狀態**(引擎 2026-08-22 起交出來)。與上一行是兩件事:
  #   佔位符正則看不見純文字,composer_state 看得見。撈不到=舊引擎=unknown,
  #   而 unknown 一律不足以支撐自動 Enter(fail-closed,不補預設成 empty)。
  _AE_BEFORE_CS="$(sed -n 's/.*貼前輸入框=\(.*\)$/\1/p' "$_ENG_LOG" | tail -n 1)"
  _IDENTITY_PROMPT_REGION="$(sed -n 's/^aib-dm: identity_prompt_region=\([^ ]*\) identity_prompt_line=[0-9][0-9]*$/\1/p' "$_ENG_LOG" | tail -n 1)"
  _IDENTITY_PROMPT_LINE="$(sed -n 's/^aib-dm: identity_prompt_region=[^ ]* identity_prompt_line=\([0-9][0-9]*\)$/\1/p' "$_ENG_LOG" | tail -n 1)"
  # 沒撈到=引擎沒報(它 die 在 capture 之前,或舊版引擎)→ 保持缺席,序列化成 null
  export AIB_DM_BEFORE_PLACEHOLDERS
  # ★ EH.27:原本這裡就 `rm` 了。改成 trap 收尾 —— 現在引擎的話要留到
  #   **結局定了之後**才印,提早刪掉的話印出來的會是空的,
  #   而空的輸出看起來像「引擎什麼都沒說」。
  # 串上 RESULT 行:trap 只能有一個,覆蓋掉就沒有最後一行了。★ _result_line 要排第一:它讀 $? 當投遞 rc,
  # 前面多一個 rm 就把 rc 洗成 0(record 會把 rc=4 的信記成 delivered)。
  trap '_result_line; rm -f "$_ENG_LOG"' EXIT
else
  bash "$THROAT" "${THROAT_ARGS[@]}" --execute
  RC=$?
  VERIFIED_LABEL=""
fi
# 拿不到引擎 stdout(上面那條路徑)→ 貼前輸入框狀態不可知,同樣落 unknown。
[ -n "${_AE_BEFORE_CS:-}" ] || _AE_BEFORE_CS="unknown"
[ -n "${VERIFIED_LABEL:-}" ] || VERIFIED_LABEL="unknown"

# ★ EH.27:把引擎的話印出來,而且**每一行自己帶著它是不是還算數**。
#   $1 = superseded|final。superseded 時逐行加前綴,前綴與內容同一行 ——
#   截斷、grep、引用都拆不開它們。
_aib_emit_engine_log() {
  [ -n "${_ENG_LOG:-}" ] && [ -s "$_ENG_LOG" ] || return 0
  if [ "${1:-final}" = "superseded" ]; then
    while IFS= read -r _l; do
      # ★★ 光加前綴還不夠:艦長 08:09 第三條逐字是「**rc=0 的輸出不得出現「請重送」**」,
      #   而前綴留在行首、那個祈使句仍在行尾。**由上往下讀的人會讀到它,
      #   grep 的人會命中它** —— 一句已作廢的指示,只要還在畫面上就還會被照做。
      #   → 含重新投遞指示的整行**不印**,改印一行說明「有一行被抑制、為什麼」。
      #   ★ 說明行本身刻意**不寫出那兩個字**,否則 grep 判準會被說明行自己餵成假紅
      #     (與「談論被過濾的字元時用碼位描述」同一條)。
      case "$_l" in
        *重送*|*重貼*)
          printf 'aib-dm-ctl: %s\n' "$(_dmt aib.dmctl.engine_obs_void)" ;;
        *)
          printf 'aib-dm-ctl: %s\n' "$(_dmt aib.dmctl.engine_obs_superseded line="$_l")" ;;
      esac
    done < "$_ENG_LOG"
  else
    cat "$_ENG_LOG"
  fi
}

# ══ 10-01 長稿(the maintainer 令;a seat copilot+Kimi K3 現量,3060/7932 位元組單行稿)══════════════
# 這次投遞的稿有多大(位元組 / 1024)。1KB 以下的稿,下面兩段長稿邏輯一個位元組都不跑。
_LP_KB=$(( $(printf '%s' "$BODY" | wc -c | tr -d '[:space:]') / 1024 ))
# 稿寫進一個暫存檔給 dm_paste 比對(長字串走檔不走 argv,本鏈的老規矩)。
_lp_body_file() {   # 印暫存檔路徑;呼叫端用完自己 rm。$1=msg ⇒ 只寫正文(不含抬頭),預設整張 BODY
  local _f
  _f="$(_dm_ctl_tmp aib-dm-lp)" || return 1
  if [ "${1:-}" = "msg" ]; then
    printf '%s' "$MSG" > "$_f" || { rm -f "$_f"; return 1; }
  else
    printf '%s' "$BODY" > "$_f" || { rm -f "$_f"; return 1; }
  fi
  printf '%s\n' "$_f"
}
# 引擎自己按了 Enter(rc=0)之後,長稿要**正面看見一個空的輸入框**才算送出。
#   舊碼:引擎按完 Enter 就回 0,沒有人回頭看 —— 對方還在吃 paste 時 Enter 被吞掉,收據照樣寫 delivered。
#   等待預算隨稿長放大:2 + 每 KB 一次(上限 30),間隔 AIB_DM_LONG_CONFIRM_POLL_S(預設 0.5 秒)。
#   印一個字給呼叫端:
#     clear              框清了(或是 CLI 建議)——照舊 rc 0
#     unknown            框判不出來 / 框裡不是我的字 —— 照舊 rc 0(量不到不改判,與從前同)
#     stuck-mine         框裡還是我的稿(頭或尾指紋)、而不是恰好整框 —— rc 4
#     repressed-clear    整框恰好是我的稿 ⇒ 自己的稿自己再按一次 ⇒ 清了 —— rc 0
#     stuck-after-repress 再按一次仍沒清 —— rc 4
#     repress-failed     再按那一下 send-keys 失敗 —— rc 4
#     changed-before-repress 等待之後、按之前再看一次,框已經不是恰好我的稿(有人在打字/又貼了東西)——不按,rc 4
#   ★ 「不代按別人的稿」:再按一次只在**整框逐字等於我貼的稿**(composer-match=exact)且貼前框是空的時候;
#     含有不夠(別人可能接著打字,Enter 送的是整個框)。
#   ★ R16 M2b:那個 exact 要在**按下去之前那一刻**還成立。舊碼 match → 睡 3 秒 → 直接按:
#     3 秒裡有人打字(或又貼一份),按下去送出的就是別人的字。所以睡完再抓一幀、再比一次,還是 exact 才按。
#   ★ R16 M2:input() 型 REPL 席(deepseek 這類 API 席、橋接席)按 Enter 後,剛送出那行會原樣留著當回音
#     (`DeepSeek> <稿>`),下一行才是 CLI 印的 `deepseek> `。框判定器把回音讀成 draft、composer-match 說 mine ⇒
#     已收下的稿被判 stuck-mine。這種席(方言宣告 input_repl)回音後面還有輸出就算框清了
#     (composer_state --repl-submitted;判準見 composer_state.repl_submitted)。copilot 畫框不宣告,照舊。
_lp_wait_clear() {   # $1=最多回讀幾次 $2=稿檔(給 REPL 回音判準);**不印東西**,設 _LP_ST(clear / 最後一幀的 composer_state)與 _LP_LAST(最後一幀)
  local _n=0 _max="$1" _cs _poll="${AIB_DM_LONG_CONFIRM_POLL_S:-0.5}"
  while :; do
    _LP_LAST="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)" || { _LP_LAST=""; _LP_ST=unreadable; return 0; }
    _cs="$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_LP_LAST" 2>/dev/null)"
    case "$_cs" in empty|suggestion) _LP_ST=clear; return 0 ;; esac
    if [ "$_cs" = "draft" ] && [ -n "${2:-}" ] \
         && [ "$("$PY3" "$HERE/composer_state.py" --stdin --repl-submitted "$2" <<< "$_LP_LAST" 2>/dev/null)" = "yes" ]; then
      _LP_ST=clear; return 0
    fi
    _n=$((_n+1)); [ "$_n" -ge "$_max" ] && { _LP_ST="${_cs:-unknown}"; return 0; }
    sleep "$_poll"
  done
}
_long_post_enter_confirm() {
  local _tries=$(( 2 + _LP_KB )) _ct _m _bf
  [ "$_tries" -gt 30 ] && _tries=30
  _bf="$(_lp_body_file)" || { echo unknown; return 0; }
  _lp_wait_clear "$_tries" "$_bf"
  [ "$_LP_ST" = "clear" ] && { rm -f "$_bf"; echo clear; return 0; }
  [ "$_LP_ST" = "draft" ] || { rm -f "$_bf"; echo unknown; return 0; }
  _ct="$("$PY3" "$HERE/composer_state.py" --stdin --composer-text <<< "$_LP_LAST" 2>/dev/null)"
  _m="$(printf '%s' "$_ct" | "$PY3" "$DM_PASTE" composer-match "$_bf" 2>/dev/null)"
  case "$_m" in
    exact)
      case "${_AE_BEFORE_CS:-unknown}" in empty|suggestion) : ;; *) rm -f "$_bf"; echo stuck-mine; return 0 ;; esac
      [ "${AIB_DM_SKIP_DRAFT_GUARD:-0}" = "1" ] && { rm -f "$_bf"; echo stuck-mine; return 0; }
      sleep "${AIB_DM_REPRESS_SLEEP:-3}"
      # R16 M2b:按之前那一刻再抓一幀、再比一次(見上面註)。抓不到 / 框已不是 draft 而且不是空 / 不再 exact ⇒ 不按。
      local _now _cs2 _m2
      _now="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)" || _now=""
      _cs2="$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_now" 2>/dev/null)"
      case "$_cs2" in empty|suggestion) rm -f "$_bf"; echo clear; return 0 ;; esac
      _m2="$("$PY3" "$HERE/composer_state.py" --stdin --composer-text <<< "$_now" 2>/dev/null \
             | "$PY3" "$DM_PASTE" composer-match "$_bf" 2>/dev/null)"
      if [ -z "$_now" ] || [ "$_cs2" != "draft" ] || [ "$_m2" != "exact" ]; then
        rm -f "$_bf"; echo changed-before-repress; return 0
      fi
      hub_tmux send-keys -t "$T_WID" Enter 2>/dev/null || { rm -f "$_bf"; echo repress-failed; return 0; }
      _lp_wait_clear "$_tries" "$_bf"
      rm -f "$_bf"
      if [ "$_LP_ST" = "clear" ]; then echo repressed-clear; else echo stuck-after-repress; fi ;;
    mine) rm -f "$_bf"; echo stuck-mine ;;
    *) rm -f "$_bf"; echo unknown ;;
  esac
}

# rc 對映(接口契約):咽喉 deny=1/lint=3/用法=2;引擎 2 3 4=零進入,5=已入
# 輸入區未確認送出。0→0;5→3;其餘→2。
case "$RC" in
  0)
    # ★ 10-01 長稿:引擎按了 Enter,回頭確認框真的清了(見 _long_post_enter_confirm)。
    if [ "$_LP_KB" -ge 1 ] && [ "${AIB_DM_LONG_CONFIRM:-1}" != "0" ]; then
      _LP_V="$(_long_post_enter_confirm)"
      case "$_LP_V" in
        stuck-mine|stuck-after-repress|repress-failed|changed-before-repress)
          _aib_emit_engine_log superseded
          emit_json 4 landed-unsubmitted "長稿(${_LP_KB}KB)已落進對方輸入框、已按 Enter,但等待後框裡仍是本稿(${_LP_V}):進了沒送出,**絕不重送**,請該席自己按 Enter 或清掉" "none"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.long_in_box state="${_LP_V}")" >&2
          exit 4 ;;
        repressed-clear)
          _aib_emit_engine_log superseded
          _notice_clear "$T_CODE"
          emit_json 0 delivered "長稿(${_LP_KB}KB)Enter 後框未清、整框恰為本稿;等待後重按一次,回讀確認框已清 -> $T_WID" "auto-enter-repress"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.long_reenter_ok window="$T_WID")"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.receipt_scope)"
          exit 0 ;;
      esac
    fi
    _aib_emit_engine_log final
    _notice_clear "$T_CODE"
    # A6(09-24 a seat):目標是 codexbot 席且橋在 queue 模式 ⇒ 稿只是排進桌面共用佇列,要等桌面持有者閒下來才執行
    #   (桌面關著就無限等)。收據不能說 delivered 讓寄件者以為對方在處理;stage=queued、detail 講白。rc 仍 0(稿確實進去了)。
    #   模式由目標席的橋自己寫的 status.json 回答(codexbot_receipt.py);讀不到就照舊 delivered,不猜。
    _CB_MODE=""
    if [ "${T_PROVIDER:-}" = "codexbot" ] && [ -n "${_Q_UUID:-}" ] && [ -f "$HERE/codexbot_receipt.py" ]; then
      _CB_MODE="$("$PY3" "$HERE/codexbot_receipt.py" "$_Q_UUID" 2>/dev/null || true)"
    fi
    if [ "$_CB_MODE" = "queue" ]; then
      echo "aib-dm-ctl: $(_dmt aib.dmctl.codexbot_queued)" >&2
      emit_json 0 queued "已排入桌面佇列,等桌面持有者執行(桌面關著就一直等)-> $T_WID" "$VERIFIED_LABEL"
    else
      emit_json 0 delivered "傳輸已驗證 -> $T_WID" "$VERIFIED_LABEL"
    fi
    # 10-09 a seat(第 26 件寄件端):目標是 claudebot 席時,信進了它的信箱不等於有人讀 —— 門鈴過期、沒綁對話等
    #   由 claudebot_receipt.py 從橋的 status.json 與門鈴行程判;量不到就不說。stage 仍 delivered(信確實進了信箱)。
    if [ "${T_PROVIDER:-}" = "claudebot" ] && [ -n "${_Q_UUID:-}" ] && [ -f "$HERE/claudebot_receipt.py" ]; then
      _CBR_NOTE="$("$PY3" "$HERE/claudebot_receipt.py" "$_Q_UUID" 2>/dev/null || true)"
      if [ -n "$_CBR_NOTE" ]; then echo "aib-dm-ctl: $(_dmt aib.dmctl.claudebot_note code="${T_CODE}" text="${_CBR_NOTE}")" >&2; fi
    fi
    if [ "$VERIFY" = "1" ]; then
      # 探針取**正文**(跳過抬頭)且要求「貼前沒有、貼後有」——抬頭前 24 字
      # 對同一對席位每封恆等,拿它當探針等於恆真(dev1 I3)。
      _vbody="$(printf '%s' "$MSG" | tr -d '[:space:]')"
      _probe="${_vbody:0:24}"
      _tail="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null | tr -d '[:space:]')"
      if [ -z "$_probe" ]; then
        echo "aib-dm-ctl: $(_dmt aib.dmctl.verify_empty)"
      else
        case "$_VBEFORE" in
          *"$_probe"*) echo "aib-dm-ctl: $(_dmt aib.dmctl.verify_preexisting)" ;;
          *) case "$_tail" in
               *"$_probe"*) echo "aib-dm-ctl: $(_dmt aib.dmctl.verify_new)" ;;
               *) echo "aib-dm-ctl: $(_dmt aib.dmctl.verify_not_seen)" ;;
             esac ;;
        esac
      fi
    fi
    exit 0 ;;
  5)
    # 引擎預設流=貼後回讀驗稿在框才 Enter(非 --verify 選配;艦長 codex
    # 三席實證:fresh CLI 未就緒會整段吞貼)。rc5=稿未確認在框且**未 Enter**。
    #
    # ★★★ 2026-08-22(the maintainer 直令,規格 root/spec-dm-autoenter@fac6cc2、
    #   釘子 a seat tests/test-dm-auto-enter.py):這裡原本印一段人話,叫人
    #   自己去 capture、自己比對、自己補 Enter。**08-21 一晚人做了十幾次,
    #   每一次跑的都是同一套機械程序——那表示它可以是程式。**
    #   人比引擎多知道的只有一件事:**佔位符的方言**。引擎只認得 claude 的
    #   `[Pasted text #N +K lines]`,而 codex 印的是 `[Pasted Content N chars]`。
    #   文法補齊之後,整套判準就落在碼裡,不在人的判斷裡。
    #
    # ★★ 判準放在 AFTER 的**總數**上,不是「新增了幾枚」——理由寫在
    #   dm_paste.py 檔頭:BEFORE 那一端的解析器(引擎 aib-dm.sh:168)只認得
    #   claude 方言。只把 AFTER 擴寬而 BEFORE 沒跟著寬,**盲區會翻面變成假陽性**
    #   (框裡本來就有的一枚 codex 佔位符,在 BEFORE 眼裡不存在、在 AFTER 眼裡
    #   是新的 → 「新增恰一」成立 → 自動按下別人的 Enter)。
    #   改用「BEFORE 必須已知且空 ∧ AFTER 總數恰一」之後,BEFORE 漏看的那一枚
    #   仍會出現在 AFTER 的總數裡 → 總數 2 → 停手。**一條腿的盲區被另一條腿蓋住。**
    _AE_AFTER="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null)"
    _AE_CAP_RC=$?     # ★ rc 緊接著取,中間一行都不放(本席 08-21 兩次栽在這)
    # ★★ 2026-09-24(the maintainer 直令「強化 dm 之後的 enter」):量「框裡那張稿是不是我的」。
    #   短稿不產生佔位符 ⇒ 判定器的佔位符路徑在結構上看不見它;而引擎的頭指紋
    #   路徑比的是整張畫面含捲動區,我重送同一句就會被 BEFORE 閘正確地否決 ⇒
    #   兩條路都沒有後備,稿留在框裡沒人按 Enter。這裡把**同一個頭指紋縮到輸入框
    #   那一行**量:空框裝不下捲動區的回音,所以這是加一條正面證據,不是放寬那道閘。
    #   量不到就不設變數 ⇒ 判定器 composer_mine=None ⇒ 完全照舊(fail-closed)。
    # ★ 10-04(雲端實踩):原寫 `mktemp -t aib-dm-ae-ct` 是 BSD 寫法,GNU mktemp 要範本裡有 XXX ⇒ Linux 上失敗、
    #   檔名變空 ⇒ 這條正面證據整條沒了。改走本檔規定的 _dm_ctl_tmp(可寫性探測 + 範本帶 XXXXXX,兩邊都通)。
    _AE_CT_F="$(_dm_ctl_tmp aib-dm-ae-ct)" ; _AE_BD_F="$(_dm_ctl_tmp aib-dm-ae-bd)"
    if hub_tmux capture-pane -p -e -t "$T_WID" 2>/dev/null \
         | "$PY3" "$HERE/composer_state.py" --stdin --composer-text \
             --provider "${AIB_DM_TARGET_PROVIDER:-}" > "$_AE_CT_F" 2>/dev/null
    then
      printf '%s' "$BODY" > "$_AE_BD_F"
      export AIB_DM_AE_COMPOSER_TEXT_FILE="$_AE_CT_F" AIB_DM_AE_BODY_FILE="$_AE_BD_F"
    else
      unset AIB_DM_AE_COMPOSER_TEXT_FILE AIB_DM_AE_BODY_FILE
    fi
    _AE_V="$("$PY3" "$DM_PASTE" decide \
             "${AIB_DM_BEFORE_PLACEHOLDERS-__aib_absent__}" "$_AE_CAP_RC" \
             "$_AE_CHARS_BODY,$_AE_CHARS_MSG" "$_AE_LINES_BODY" \
             "$([ "${AIB_DM_SKIP_DRAFT_GUARD:-0}" = "1" ] && echo 0 || echo 1)" \
             "$_AE_BEFORE_CS" \
             <<< "$_AE_AFTER" 2>/dev/null)"
    _AE_DEC_RC=$?
    # 兩個暫存檔只有上面這次判定讀(10-04:改走 _dm_ctl_tmp 後會落在 DM 狀態目錄,不清就每封留兩個)
    rm -f "${_AE_CT_F:-}" "${_AE_BD_F:-}" 2>/dev/null
    unset AIB_DM_AE_COMPOSER_TEXT_FILE AIB_DM_AE_BODY_FILE
    if [ "$_AE_DEC_RC" != "0" ] || [ -z "$_AE_V" ]; then
      # 判定器自己壞掉 → 不是「沒事」,是分不出。fail-closed 落絕不重送那一格。
      _aib_emit_engine_log final
        emit_json 4 undecidable "自動送出判定器不可用(rc=$_AE_DEC_RC),稿可能已在對方框內:**絕不重送**,請 capture 人工確認" "none"
      die "自動送出判定器不可用;稿可能已在對方框內,勿重貼" 4
    fi
    _AE_ACTION="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$_AE_V")"
    _AE_RC="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["rc"])' "$_AE_V")"
    _AE_STAGE="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["stage"])' "$_AE_V")"
    _AE_DETAIL="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["detail"])' "$_AE_V")"
    _AE_PH="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("placeholder") or "")' "$_AE_V")"
    # dm_paste intentionally retains the historical Claude K+1 rule. If that
    # sole failure is a size mismatch, let the target provider's dialect verify
    # the already-isolated placeholder. Unknown dialects remain stopped.
    if [ "$_AE_ACTION" = "stop" ] && [ "$_AE_STAGE" = "size-mismatch" ] \
         && [ -n "$_AE_PH" ]; then
      _AE_DIALECT_DETAIL="$("$PY3" "$COMPOSER_DIALECT" verify-fold \
                           "$T_PROVIDER" "$_AE_LINES_BODY" "$_AE_PH" 2>/dev/null)"
      _AE_DIALECT_RC=$?
      if [ "$_AE_DIALECT_RC" = "0" ]; then
        _AE_QUEUED="$("$PY3" -c 'import json,sys; print(1 if json.loads(sys.argv[1]).get("queued") else 0)' "$_AE_V")"
        _AE_ACTION="enter"
        _AE_RC="0"
        if [ "$_AE_QUEUED" = "1" ]; then _AE_STAGE="queued"; else _AE_STAGE="delivered"; fi
        _AE_DETAIL="$_AE_DIALECT_DETAIL;佔位符=$_AE_PH"
      fi
    fi
    case "$_AE_ACTION" in
      enter)
        # ★★ 這一行我第一版寫成 `-t "$HUB:$T_WID.0"`,理由是「與引擎逐字同源」。
        #   **那個理由是錯的,而且我還「驗證」過它**:引擎 aib-dm.sh:129 確實寫
        #   `PANE="$SOCKET:$TARGET.0"`,但**下一行 :130 立刻覆寫**——
        #   `case "$TARGET" in @*) PANE="$TARGET" ;;`,窗 id 直接當 target。
        #   我讀了 129 沒讀 130。
        #   ★ 更貴的是我拿來驗證的方法:我用 `display-message -t aihub-16:@6.0`
        #     取到 rc=0 就以為形狀對了,而**真正要用它的是 `send-keys`**。
        #     隔離伺服器實測(-L pmtest,零風險):
        #         display-message -t pmtest:@0.0   rc=0   ← 我拿來校準的
        #         send-keys       -t pmtest:@0.0   rc=1   ← 真正要跑的
        #         send-keys       -t @0            rc=0
        #     socket 名不是 session 名;display-message 寬鬆而 send-keys 嚴格。
        #   ★★ **校準端與量測端要是同一個指令形狀**——我用 A 指令驗過的形狀,
        #     不構成 B 指令會接受它的證據。這條是本席自己記過的,今晚再犯一次。
        #   實害:08-22 00:3x 送 a seat 那封,判準全部命中、決定按 Enter,
        #   而 send-keys rc=1 → 落 rc=4,稿滯留在對方框裡。**失效方向是對的
        #   (落 4 不落 0、明寫勿重送),缺陷本身是我的。**
        hub_tmux send-keys -t "$T_WID" Enter 2>/dev/null
        _AE_ENTER_RC=$?
        if [ "$_AE_ENTER_RC" != "0" ]; then
          _aib_emit_engine_log final
        emit_json 4 enter-failed "判準已命中但 send-keys Enter rc=$_AE_ENTER_RC:稿在對方框內而未送出,**絕不重送**" "none"
          die "自動 Enter 失敗(rc=$_AE_ENTER_RC);稿已在對方框內,勿重貼" 4
        fi
        # ★ 按完要**再 capture 一次**確認框已清。send-keys 的 rc=0 只證明按鍵
        #   送進 pane,不證明它被消費掉——**用按鍵的 rc 當送出證據,就是拿
        #   「我喊了」當「他聽見了」**(全艦第 N 次同型)。
        # ★ 用 -e 抓(帶跳脫序列):composer_state 靠 SGR 2(dim)分辨「CLI 預載
        #   的下一步建議」與「真的有人在打字」,不帶 -e 會把建議誤判成草稿。
        _AE_CONFIRM="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)"
        _AE_CONF_RC=$?
        if [ "$_AE_CONF_RC" != "0" ]; then
          _aib_emit_engine_log superseded
          emit_json 3 sent-unverified "已自動補 Enter($_AE_DETAIL),但確認回讀失敗(capture rc=$_AE_CONF_RC):**勿重貼**" "auto-enter"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.enter_readback_fail)"
          exit 3
        fi
        # ★ 2026-09-24(a seat 實測):Enter 送出後 pty 要幾毫秒重畫,首次回讀常抓到
        #   「送出前的最後一幀」—— 提示符行上還掛著剛送出的稿 ⇒ 判 draft ⇒ rc=3,
        #   而訊息其實已送到、對方已回話。這**不是**拿時間當分辨器:分辨器仍是
        #   composer_state,只是**讀之前**讓 pty 把那一幀畫完。非空才等、只等一次、
        #   等完照原判準判;AIB_DM_CONFIRM_SETTLE=0 關(治具用)。
        _AE_CS0="$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_AE_CONFIRM" 2>/dev/null)"
        case "${_AE_CS0:-unknown}" in
          empty|suggestion) : ;;
          *)
            _AE_CONF_SETTLE="${AIB_DM_CONFIRM_SETTLE:-0.5}"
            if [ "$_AE_CONF_SETTLE" != "0" ]; then
              # ★ 10-01 長稿:對方吃完一大段 paste 再送出要更久。短稿(<1KB)逐字照舊等一次;
              #   長稿每 KB 多等一次(上限 20 次),一看見空框就停,不白等。
              _AE_CONF_TRIES=$(( 1 + _LP_KB ))
              [ "$_AE_CONF_TRIES" -gt 20 ] && _AE_CONF_TRIES=20
              _AE_CONF_I=0
              while [ "$_AE_CONF_I" -lt "$_AE_CONF_TRIES" ]; do
                _AE_CONF_I=$((_AE_CONF_I+1))
                sleep "$_AE_CONF_SETTLE"
                _AE_CONFIRM_RE="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)"; _AE_RE_RC=$?
                [ "$_AE_RE_RC" = "0" ] && _AE_CONFIRM="$_AE_CONFIRM_RE"
                case "$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_AE_CONFIRM" 2>/dev/null)" in
                  empty|suggestion) break ;;
                esac
              done
            fi ;;
        esac
        # ★★ 判準要**正面看見一個空的輸入框**,不能只看「佔位符不見了」
        #   (a seat 二審 00:40 打回的第 2 條)。我原本寫的是
        #   `grep -qF 佔位符 → 沒找到就宣稱框已清`,而**「沒找到」也正是
        #   「這份 capture 根本沒照到輸入框」會給的答案**——一份只有舊
        #   scrollback 的畫面同樣比不到佔位符,於是不可觀測被讀成已送出。
        #   ★ 這是本檔上面那句「取不到畫面不等於畫面乾淨」的第二種形狀:
        #     **capture 成功了,但它回答不了我問的那個問題。**
        #     第一種我擋了(capture rc!=0),第二種我漏了——**同一句話的兩個
        #     實例,我只認得長得像第一個的那個。**
        #   改用既有的 composer_state(它本來就有第三態 unknown,而且
        #   「定不出輸入框一律擋」是它自己的紀律),不自己再發明一組標記。
        #   empty 與 suggestion 都算已清:suggestion 是 CLI 預載的下一步建議,
        #   引擎的卡稿守衛同樣視同空框——**兩處判準同源,不各寫一份。**
        # ★★ 已知盲區(2026-08-22 實測,擁有者非本席:composer_state 是 dev2 的寫入面)
        #   composer_state **認不得 codex 的輸入框**。逐字對照:
        #       `❯ `                        → empty
        #       `› Ask Codex to do anything` → unknown   ← codex 的**空框**長這樣
        #   aihub-16 八格普查:unknown 只有兩格 —— @1(watch,本來就沒有輸入框)
        #   與 @6(qa-codex)。六格 claude 全部解得出來。
        #   後果一(本檔):對 codex 席自動補 Enter **即使成功也永遠回 rc=3**。
        #     00:4x 實測:稿確實送出去了(佔位符消失、對方進入 Working),而
        #     這裡判 unknown → rc=3。方向是安全的(勿重貼、不會疊稿),
        #     ★ 但**一個永遠回 rc=3 的路徑會訓練大家把 rc=3 當雜訊**,
        #       而 rc=3 正是「絕對不要重貼」要靠的那個訊號。
        #   後果二(不是本檔):引擎的卡稿守衛也讀 composer_state,所以每一個
        #     codex 席都走 fallback 那條。引擎 :243 把 unknown 記成「忙碌中的席 /
        #     watch 無 composer」兩個成因——**少了第三個:那一席是 codex**。
        #     而 AIB_DM_STRICT_UNKNOWN 的翻轉條件寫的是「各格 unknown 計數 = 0」,
        #     ★ 只要艦上有 codex 席,那個條件就**永遠到不了**——一個到不了的
        #       翻轉條件等於一道永遠不會被打開的閘。
        #   → 已回報 a seat 與 composer_state 擁有者。**本檔不放寬判準來換好看的
        #     數字**:判準(要正面看見空框)是對的,不夠的是儀器。
        _AE_CSTATE="$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_AE_CONFIRM" 2>/dev/null)"
        [ -n "$_AE_CSTATE" ] || _AE_CSTATE="unknown"
        case "$_AE_CSTATE" in
          empty|suggestion) : ;;
          *)
            # ══ 自己的稿自己按一次(車道 4 第 1 件;FINDING a18445c9 第 1 條)══
            # 走到這裡=我按了 Enter,而框**沒有**變空。兩個世界:
            #   (a) 稿送出去了而我看不清楚(codex 席的框判定器認不得)——現行 rc=3 正確;
            #   (b) 摺疊還沒處理完、Enter 被吃掉,稿留在框裡——a seat 09-03 兩次實例。
            # 分辨器不是時間,是**框裡那一枚還在不在、而且是不是恰好是我那一枚**。
            # ★ 「不代按別人的稿」這條規矩不放寬:三條腿全在 dm_paste.repress_verdict,
            #   其中「這一枚在貼前不存在」是艦長 09-04 加的必要條件
            #   (該函式 docstring 逐字寫了它在現行鏈上是被上游蘊含的,不假裝它在守)。
            _AE_AFTER_RAWS="$("$PY3" "$DM_PASTE" raws <<< "$_AE_AFTER" 2>/dev/null)"
            [ -n "$_AE_AFTER_RAWS" ] || _AE_AFTER_RAWS="null"
            _AE_RP="$("$PY3" "$DM_PASTE" repress \
                      "${AIB_DM_BEFORE_PLACEHOLDERS-__aib_absent__}" \
                      "$_AE_AFTER_RAWS" "$_AE_PH" "$_AE_CONF_RC" \
                      <<< "$_AE_CONFIRM" 2>/dev/null)"
            _AE_RP_RC=$?
            _AE_RP_ACTION=""
            if [ "$_AE_RP_RC" = "0" ] && [ -n "$_AE_RP" ]; then
              _AE_RP_ACTION="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$_AE_RP" 2>/dev/null)"
              _AE_RP_DETAIL="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1])["detail"])' "$_AE_RP" 2>/dev/null)"
            fi
            if [ "$_AE_RP_ACTION" = "repress" ]; then
              # 等一下再按:摺疊是目標 CLI 的非同步工作,忙的時候要時間。
              sleep "${AIB_DM_REPRESS_SLEEP:-3}"
              hub_tmux send-keys -t "$T_WID" Enter 2>/dev/null
              _AE_ENTER2_RC=$?
              if [ "$_AE_ENTER2_RC" != "0" ]; then
                _aib_emit_engine_log superseded
                emit_json 4 repress-enter-failed "本次稿卡住,重按時 send-keys rc=$_AE_ENTER2_RC:稿仍在對方框內,**絕不重送**" "auto-enter-repress"
                echo "aib-dm-ctl: $(_dmt aib.dmctl.stuck_reenter_fail rc="$_AE_ENTER2_RC")" >&2
                exit 4
              fi
              _AE_CONFIRM2="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)"
              _AE_CONF2_RC=$?   # rc 緊接著取
              if [ "$_AE_CONF2_RC" != "0" ]; then
                _aib_emit_engine_log superseded
                emit_json 4 repress-unverified "本次稿卡住,已重按一次,但確認回讀失敗(capture rc=$_AE_CONF2_RC):**勿重貼**" "auto-enter-repress"
                echo "aib-dm-ctl: $(_dmt aib.dmctl.stuck_readback_fail)" >&2
                exit 4
              fi
              _AE_CSTATE2="$("$PY3" "$HERE/composer_state.py" --stdin <<< "$_AE_CONFIRM2" 2>/dev/null)"
              [ -n "$_AE_CSTATE2" ] || _AE_CSTATE2="unknown"
              case "$_AE_CSTATE2" in
                empty|suggestion)
                  _aib_emit_engine_log superseded
                  emit_json 0 "$_AE_STAGE" "自動補 Enter 後框未清($_AE_RP_DETAIL);等待後重按一次,回讀確認框已清" "auto-enter-repress"
                  echo "aib-dm-ctl: $(_dmt aib.dmctl.stuck_reenter_ok window="$T_WID")"
                  echo "aib-dm-ctl: $(_dmt aib.dmctl.receipt_scope)"
                  exit 0 ;;
                *)
                  _aib_emit_engine_log superseded
                  emit_json 4 stuck-after-repress "本次稿卡住,已重按一次;框仍未清(composer_state=${_AE_CSTATE2}):稿在對方框內,**絕不重送**,請該席自己處理" "auto-enter-repress"
                  echo "aib-dm-ctl: $(_dmt aib.dmctl.stuck_still state="${_AE_CSTATE2}")" >&2
                  exit 4 ;;
              esac
            fi
            _aib_emit_engine_log superseded
          emit_json 3 sent-unverified "已自動補 Enter($_AE_DETAIL),但送出後看不到一個空的輸入框(composer_state=$_AE_CSTATE):未能確認送出,**勿重貼**" "auto-enter"
            # ★ 大括號不是風格:`$_AE_CSTATE` 後面**緊接非 ASCII 字元**時,
            #   bash 5.3.9 會把那個字元的第一個位元組吃進識別字,變成一個不存在
            #   的變數名,`set -u` 當場 rc=127。實測(獨立取 rc):
            #     X=unknown; echo "值=$X——後面"   → `X\xe2: 未綁定的變數` rc=127
            #     X=unknown; echo "值=${X}——後面" → rc=0
            #     X=unknown; echo "值=$X)後面"    → rc=0
            #   ★ 失效條件在**變數後面那個字元**,不在變數本身——同一個變數在
            #     上一行接 `)` 完全正常,所以「它剛才明明有值」不是反證。
            #   ★ 而它只在走到這條路徑時才發作:八格裡只有一格會走到這裡,
            #     其餘全綠——**這個缺陷的可見度等於那條路徑被走到的機率。**
            echo "aib-dm-ctl: $(_dmt aib.dmctl.enter_box_state state="${_AE_CSTATE}")" >&2
            exit 3 ;;
        esac
        _aib_emit_engine_log superseded
        emit_json 0 "$_AE_STAGE" "自動補 Enter 已送出($_AE_DETAIL);Enter 後回讀確認佔位符已消失" "auto-enter"
        if [ "$_AE_STAGE" = "queued" ]; then
          echo "aib-dm-ctl: $(_dmt aib.dmctl.enter_queued)"
        else
          echo "aib-dm-ctl: $(_dmt aib.dmctl.enter_ok window="$T_WID")"
        fi
        echo "aib-dm-ctl: $(_dmt aib.dmctl.receipt_scope)"
        exit 0 ;;
      unverified)
        # 貼進去了、框上看不見佔位符、未按 Enter。舊契約落 rc=3 勿重貼,
        # 稿卻留在對方框裡——忙碌暫存開著時這是該進待審的信走錯路
        # (17:3x 兩段換行實測)。暫存可收才改排;收不到維持 rc=3。
        _aib_emit_engine_log final
        # ★ 重送迴圈(qa a seat 09-12 13:54 抓):排空器送信時設 AIB_DM_SORT_DELIVER=1,
        #   於是下面那個「改排待審 + C-c 清稿」的補救分支對排空器**永遠不成立**,落到 exit 3;
        #   排空端舊碼把非 0 一律退回 queued 再送 ⇒ 每幀重貼一次而稿從沒被清 —— 對方框裡每重試多疊一份。
        #   排空器手上就是這一則,不能再 intercept(那是第二份);正確處置=清掉剛貼的稿、退 4、
        #   stage=cleared-hold 明說「稿已清、轉待人處理、不要再送」,排空端照契約把 4 轉 hold_human。
        #   ★ 只在**貼了沒 Enter** 這一格做;Enter 之後的 rc=3 不在這裡(那時 C-c 會打斷對方正在跑的回合)。
        if [ "${AIB_DM_SORT_DELIVER:-0}" = "1" ]; then
          # ★ 開發 A a seat 14:11 覆審:C-c 打在**空框**上會武裝「再按一次就退出」,連續幾幀各一則走到這裡
          #   就可能把那一席的 CLI 關掉。所以 C-c 前現讀 composer 只在 draft 才按;按完再讀一次,
          #   清沒清寫進 detail;不管清沒清都退 4 轉 hold(這則不會再被送)。讀不到 composer 也不按。
          # 讀 composer:先帶 provider(方言判準);方言不認得回 unknown 時退回不帶 provider 的通用判準
          #   (治具席、未登記的引擎都會落到這裡;通用判準只看「提示符後有沒有非 dim 可見字」)。
          # ★ dm_queue.py hold 在 stdin 不是終端時**讀 stdin 當畫面**(本殼裡 stdin 是管線 ⇒ 會讀到空字串),
          #   所以畫面要自己用 hub_tmux 抓、從 stdin 餵進去,不靠它自己 capture。
          _ch_read() {
            hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null \
              | "$PY3" "$HERE/dm_queue.py" hold --wid "$T_WID" --hub "$HUB" ${1:+--provider "$1"} 2>/dev/null \
              | "$PY3" -c 'import json,sys; print((json.loads(sys.stdin.read() or "{}").get("reason") or ""))' 2>/dev/null || true
          }
          _CH_STATE="$(_ch_read "$T_PROVIDER")"
          [ "$_CH_STATE" != "unknown" ] || _CH_STATE="$(_ch_read "")"
          _CH_WHAT="composer=${_CH_STATE:-unreadable};未按 C-c"
          if [ "$_CH_STATE" = "draft" ]; then
            hub_tmux_send_keys -t "$T_WID" C-c >/dev/null 2>&1 || echo "aib-dm-ctl: $(_dmt aib.dmctl.cc_denied)" >&2
            _CH_AFTER="$(_ch_read "$T_PROVIDER")"
            [ "$_CH_AFTER" != "unknown" ] || _CH_AFTER="$(_ch_read "")"
            if [ "$_CH_AFTER" = "draft" ]; then _CH_WHAT="已按 C-c 但框仍是 draft(沒清掉)"; else _CH_WHAT="已按 C-c,框=${_CH_AFTER:-unreadable}(已清)"; fi
          fi
          emit_json 4 cleared-hold "貼後看不見佔位符、未送出(排空器路徑):${_CH_WHAT};轉待人處理,**絕不重送**($_AE_DETAIL)" "none"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.drainer_unsent what="${_CH_WHAT}")" >&2
          exit 4
        fi
        _aib_emit_engine_log final
        # ★★ 10-01(the maintainer 令;a seat 7932 位元組那封的根因):下面那段「改排暫存+C-c 清稿」的前提是
        #   **稿沒進去**(貼後看不見任何跡象)。而 copilot 吃長稿要好幾秒、又把一行長稿折進畫框:
        #   引擎 3 次回讀沒看到 ≠ 稿沒進去。現量:12:15:45 直送(稿確實進了框)、12:15:48 同一則
        #   被改排暫存,12:21:47 排空器又把**第二份**貼進去。
        #   ⇒ 改排之前再看一次對方畫面:看得見本稿的頭或尾指紋(去空白、去框線)就是「進了沒送出」,
        #   落 rc=4、不改排、不按 C-c(C-c 打在還在流進來的 paste 上清不乾淨)。
        #   看不見才走原本那條(對方 CLI 整段吞掉的世界,改排是對的)。
        # ★ R16 M1:(1) 只給長稿(≥1KB)—— 短稿逐字是舊行為,這一段一個位元組都不跑;
        #   (2) 比**正文**(MSG,不含抬頭):抬頭對同一對席位每封恆等,上一封還在捲動區就會被讀成「這封進了」
        #       ⇒ 真的沒進去的新稿落 rc 4、不改排(與引擎 AIB_DM_BODY_OFFSET 跳過抬頭同一個理由);
        #   (3) 比**輸入框裡**(composer_state --composer-text),不比整張含捲動區的畫面。
        #       定不出輸入框(composer_state unknown,rc≠0)才退回整張畫面比正文指紋 —— 那時唯一會誤判的是
        #       「同一段正文早就在捲動區」(重送同一句),誤判方向是不改排(少一份),不是疊稿。
        _LP_SEEN=""
        if [ "$_LP_KB" -ge 1 ]; then
          if _LP_F="$(_lp_body_file msg)"; then
            _LP_NOW="$(hub_tmux capture-pane -e -p -t "$T_WID" 2>/dev/null)" || _LP_NOW=""
            if _LP_CT="$("$PY3" "$HERE/composer_state.py" --stdin --composer-text <<< "$_LP_NOW" 2>/dev/null)"; then
              case "$(printf '%s' "$_LP_CT" | "$PY3" "$DM_PASTE" composer-match "$_LP_F" 2>/dev/null)" in
                exact|mine) _LP_SEEN=1 ;;
              esac
            else
              _LP_NOW="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null)" || _LP_NOW=""   # 純文字(landed 不剝 SGR)
              if [ "$(printf '%s\n%s' "$_AE_AFTER" "$_LP_NOW" | "$PY3" "$DM_PASTE" landed "$_LP_F" 2>/dev/null)" != "no" ]; then
                _LP_SEEN=1
              fi
            fi
            rm -f "$_LP_F"
          else
            _LP_SEEN=1   # 量不到就往「不再投」那邊倒
          fi
        fi
        if [ -n "$_LP_SEEN" ]; then
          emit_json 4 landed-unsubmitted "貼後引擎沒驗到,但對方畫面上看得見本稿(頭或尾指紋):進了沒送出,**絕不重送**、不改排暫存;請該席自己按 Enter 或清掉($_AE_DETAIL)" "none"
          echo "aib-dm-ctl: $(_dmt aib.dmctl.draft_on_screen)" >&2
          exit 4
        fi
        if [ "${AIB_DM_SORT_DELIVER:-0}" != "1" ] && [ -n "$T_CODE" ] \
             && [ -f "$HERE/dm_queue.py" ]; then
          _Q_KIND="$(_dm_queue_kind "$S_LABEL")"
          _Q_UUID="${_Q_UUID:-}"
          [ -n "$_Q_UUID" ] || _Q_UUID="$("$PY3" -c 'import json,sys
print(((json.loads(sys.argv[1]).get("matches") or [{}])[0].get("rec") or {}).get("session_uuid") or "")' \
            "$RESOLVED" 2>/dev/null || true)"
          # 這一格保留 `|| true`:稿已經貼進去了,佇列失敗時下面本來就落 rc=3/4(不重送),
          # 沒有「退回直送」可言;失敗的樣子與上面第一個入佇列點不同,理由也不同。
          _Q_OUT="$(printf '%s' "$MSG" | "$PY3" "$HERE/dm_queue.py" upsert \
              --seat "$T_CODE" --sender-label "$S_LABEL" --sender-kind "$_Q_KIND" \
              --session-uuid "$_Q_UUID" --wid "$T_WID" --provider "$T_PROVIDER" \
              --hub "$HUB" --priority "${AIB_DM_QUEUE_PRIORITY:-normal}" \
              ${_V2_ARGS[@]+"${_V2_ARGS[@]}"} --hold yes 2>/dev/null || true)"
          _Q_TAKE="$("$PY3" -c 'import json,sys; print(json.loads(sys.argv[1]).get("take") or "")' \
            "$_Q_OUT" 2>/dev/null || true)"
          if [ "$_Q_TAKE" = "queue" ]; then
            # 剛貼的是我們的稿、未送出。C-c 清輸入,避免待審稍後再送時疊兩份。
            hub_tmux_send_keys -t "$T_WID" C-c >/dev/null 2>&1 || echo "aib-dm-ctl: $(_dmt aib.dmctl.cc_denied)" >&2
            "$PY3" -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["receipt"], ensure_ascii=False))' \
              "$_Q_OUT"
            echo "aib-dm-ctl: $(_dmt aib.dmctl.no_placeholder_parked)"
            _RESULT_STAGE="sort-queued"
            _RESULT_ID="$("$PY3" -c 'import json,sys; print((json.loads(sys.argv[1]).get("receipt") or {}).get("queue_id") or "-")' "$_Q_OUT" 2>/dev/null || echo "-")"
            _RECORD_ELIGIBLE=0      # 這則已在佇列裡,不再 record
            exit 0
          fi
        fi
        emit_json 3 sent-unverified "$_AE_DETAIL" "none"
        echo "aib-dm-ctl: $_AE_DETAIL"
        exit 3 ;;
      *)
        _aib_emit_engine_log final
        emit_json 4 "$_AE_STAGE" "$_AE_DETAIL" "none"
        echo "aib-dm-ctl: $(_dmt aib.dmctl.no_auto_enter detail="$_AE_DETAIL")" >&2
        exit 4 ;;
    esac ;;
  *)
    # ★ rc=2 的承諾是「可安全重送」。給這個承諾之前要**看過目標端**:
    #   只信 lint / 血緣閘自己的收據不算(SPEC §五 N5 逐字:「不能只信 lint
    #   回報」)。看不到目標端 → 分不出 → 落 4,不落 2:
    #   **「分不出」與「零」不共用出口。**
    _AE_AFTER="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null)"
    _AE_CAP_RC=$?
    # ★★ 缺陷十六(a seat 06:3x 抓,cap2 06:5x 登記):這個出口原本印
    #   「貼後回讀目標端零新增,**可安全重送**」。那句話把「我這一幀沒看到新增」
    #   寫成了「確定零新增」。dev6 → a6b2e 那一輪:32 次嘗試每次都印這一句,
    #   而目標端**實收 3 份**。成因是第一次貼進去的字被 TUI 摺疊或延遲渲染,
    #   單看**一幀**永遠分不出「沒進去」與「進去了但還沒畫出來」。
    #   ★ 收據語氣越有把握,重送越理直氣壯 —— 這一句的失效方向直接製造重複投遞。
    #   分辨器:**連續兩幀逐位元組相同**(畫面已經靜下來)**且**無殘留佔位符。
    #   兩幀不同 = 對方還在渲染 ⇒ 這一幀的「沒看到」不算數。
    #   ★ 用「連拍兩幀」而不是「貼前 vs 貼後」:貼前那一幀要在引擎跑之前多抓一次,
    #     而那一次抓本身會改變被觀測端的狀態(治具實測:多抓一幀讓 9 支既有格轉紅,
    #     因為引擎的『貼前』變成了第二幀)。觀測要便宜到不改變被觀測的東西。
    _AE_SETTLE="${AIB_DM_SETTLE_SLEEP:-1}"
    [ "$_AE_SETTLE" = "0" ] || sleep "$_AE_SETTLE"
    _AE_AFTER2="$(hub_tmux capture-pane -p -t "$T_WID" 2>/dev/null)"
    _AE_CAP2_RC=$?
    _AE_RESID="$("$PY3" "$DM_PASTE" residual \
                 "${AIB_DM_BEFORE_PLACEHOLDERS-__aib_absent__}" "$_AE_CAP_RC" \
                 <<< "$_AE_AFTER" 2>/dev/null)" || _AE_RESID="unknown"
    # ★ L8b:擋下的理由若是「對方框裡有別人的稿」,被擋席自己要拿得到訊號。
    #   偵測靠引擎印在 $_ENG_LOG 的那句話(它的 stdout+stderr 都導進去了)。
    #   ★★ 這是**與散文耦合**的偵測器:引擎改字就會靜默失效,而失效形狀是
    #     「通知不再寫,而沒有一格會紅」。字面釘進臂(test_l8b_engine_phrase_is_pinned),
    #     引擎改字時是**臂紅**,不是通知消失。
    #   ★ 位置刻意在 `$_AE_RESID` 分岔**之前**:框被卡住與「這一幀靜不靜得下來」無關,
    #     底下三個出口(可安全重送 / 不要自動重送 / rc=4 不可判)都該通知。
    #     放進其中一個分支,就變成「畫面剛好在動的那幾次才通知」。
    _AE_BLOCK_KIND="$(_engine_block_kind "$_ENG_LOG")"
    case "$_AE_BLOCK_KIND" in
      browse-mode)   _notice_blocked "$T_CODE" browse-mode ;;
      history-mode)  _notice_blocked "$T_CODE" history-mode ;;
      foreign-draft) _notice_blocked "$T_CODE" foreign-draft ;;
    esac
    # ★ 第 55 項:瀏覽模式與卡稿分開講。對方輸入行不在畫面上,一個字都沒進去,它回輸入列後就能收 ——
    #   收據 stage 用自己的名字,不與「BIND 或卡稿」同形(09-12 三次「排入暫存」全是這一種)。
    # ★★ 10-01(the maintainer 令):「可安全再投」的承諾只給**貼之前**就停下的情況。引擎貼完稿之後還有出口
    #   (例:貼後畫面是檔案提及選單 ⇒ die 4),那時對方框裡可能已經有稿 —— 下面的殘留判準只數佔位符,
    #   一行長稿/短稿不產生佔位符 ⇒ 0 ⇒ 兩幀相同 ⇒ 「零新增、可安全再投」⇒ 自動再投一次 = 兩份。
    #   引擎在 paste-buffer 成功的下一行印固定字面;看到它就一律落 4。
    if [ -n "${_ENG_LOG:-}" ] && grep -qF "aib-dm: 已貼入目標(paste-buffer rc=0)" "$_ENG_LOG" 2>/dev/null; then
      _aib_emit_engine_log final
      emit_json 4 pasted-then-stopped "引擎已把稿貼進對方輸入框之後才停下(原 rc=$RC):稿可能在對方框內,**絕不重送**,請 capture 人工確認"
      echo "aib-dm-ctl: $(_dmt aib.dmctl.engine_stopped_after_paste rc="$RC")" >&2
      exit 4
    fi
    if [ "$_AE_BLOCK_KIND" = "browse-mode" ]; then
      _aib_emit_engine_log final
      _auto_resend browse-mode
      emit_json 2 browse-mode "對方 TUI 在瀏覽模式(底列 ←:collapse / Enter:open),輸入行不在畫面上;**不是卡稿**,一個字都沒進目標;該席按 Esc 回輸入列後**可安全重送**(原 rc=$RC)"
      exit 2
    fi
    # 10-14 K8:Claude 輸入框停在輸入歷史(框頂線 History n/m)。引擎已先試著按 ↓ 退回空框,這次沒退出來;
    #   框裡是別席的舊訊息、不是卡稿,這封一個字都沒進目標 ⇒ 可安全重送(與瀏覽模式同一條)。
    if [ "$_AE_BLOCK_KIND" = "history-mode" ]; then
      _aib_emit_engine_log final
      _auto_resend history-mode
      emit_json 2 history-mode "對方 Claude 的輸入框停在輸入歷史(框頂線 History n/m),框裡是別席的舊訊息、**不是卡稿**;引擎試著按 ↓ 退回空框沒成功(畫面在動或按到上限),一個字都沒進目標;該席按 ↓ 直到 History 消失後**可安全重送**(原 rc=$RC)"
      exit 2
    fi
    if [ "$_AE_RESID" = "0" ]; then
      if [ "$_AE_CAP2_RC" = "0" ] && [ "$_AE_AFTER" = "$_AE_AFTER2" ]; then
        _aib_emit_engine_log final
        _auto_resend blocked
        emit_json 2 blocked "咽喉或引擎擋下(原 rc=$RC:1=血緣deny/3=R30或讀不到畫面/4=BIND或卡稿/2=用法或目標);回讀連續兩幀逐位元組相同且無殘留佔位符 ⇒ 目標端零新增,**可安全重送**"
        exit 2
      fi
      _aib_emit_engine_log final
      emit_json 2 blocked "咽喉或引擎擋下(原 rc=$RC:1=血緣deny/3=R30或讀不到畫面/4=BIND或卡稿/2=用法或目標);**未確認送達,不要自動重送**——回讀兩幀不同(對方畫面還在動)或第二幀讀不到,證不出目標端零新增。請對方回一句,或自己 capture 那個 pane 看過再決定。"
      exit 2
    fi
    _aib_emit_engine_log final
    emit_json 4 undecidable "咽喉或引擎擋下(原 rc=$RC),但**證不出目標端零進入**(貼後回讀=$_AE_RESID):稿可能已在對方框內,**絕不重送**,請 capture 人工確認"
    exit 4 ;;
esac
