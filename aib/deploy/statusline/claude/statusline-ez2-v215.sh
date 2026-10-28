#!/bin/bash
# Ez2 status line for Claude Code(檔名 v215 沿用:settings.json 指著這個名字)。
# 輸出兩行:
#   1. CTX 那一行:[AI 圖示]  模型 | @席位 等級 | 💧 Ez2 CTX …(statusline-ez2-ctx.js 算)
#   2. 📌 那一行:aib now 記下的主項目 | 步數 | 更新時間(seat_now.py 算)
# 副作用只有一個:/clear 換對話 id 之後寫 ~/.ez2/ctx/by-seat/<席位碼>.json,右欄靠它找現用 session。
#
# 10-25(@044e0):舊載體 09-03 已關,原本這支有一半是它的身份 / 標題 / 收據 / 背景刷新 ——
#   載體不在時每次照樣清標題檔、每 10 秒背景起一支大段 python 去查一個不存在的執行檔
#   (量到一次 474 ms CPU、全機每秒 8 次,是過熱主因之一)。整段拿掉;畫面上看得到的兩行不變。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH:-}"

# 舊版的背景刷新入口。還沒換掉的舊行程可能照舊這樣叫;現在沒有要刷新的東西,直接走。
[ "${1:-}" = "--refresh" ] && exit 0

SL_DIR=$(CDPATH= cd -P -- "$(dirname -- "$0")" 2>/dev/null && pwd -P)
if [ -z "$SL_DIR" ] || [ -L "$0" ]; then
  SL_DIR="$HOME/.claude"
fi

sl_safe_sid() {
  case "$1" in
    ""|*[!A-Za-z0-9_-]* ) return 1 ;;
  esac
  [ "${#1}" -le 128 ]
}

INPUT=$(cat)
# session_id:輸入裡只出現一次、而且是單純字串 ⇒ bash 直接取(不起 python);
# 一次都沒有 ⇒ 空;其他形狀(出現兩次、值不是單純字串)才交給 python 解 JSON。
SID=""
_sl_rest="${INPUT#*\"session_id\"}"
if [ "$_sl_rest" != "$INPUT" ]; then
  _sl_re='^[[:space:]]*:[[:space:]]*"([A-Za-z0-9_-]{1,128})"'
  case "$_sl_rest" in
    *'"session_id"'*) _sl_one=0 ;;
    *) _sl_one=1 ;;
  esac
  if [ "$_sl_one" = 1 ] && [[ $_sl_rest =~ $_sl_re ]]; then
    SID="${BASH_REMATCH[1]}"
  else
    SID=$(printf '%s' "$INPUT" | /usr/bin/python3 -I -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
  fi
fi

# 2026-10-10 the maintainer:CTX 那一行也要有席位、上面掛主項目(全部的 AI 席都要)。席位碼與 📌 那一行都問 aib now --statusline
#   (seat_now.py:窗 id → 席位檔 → seat_address.uuid_tail5;/clear 後對話 id 換、席位碼不換;非艦長席也有)。
#   不在 AIB 席上(沒有 TMUX_PANE、查不到席位檔)就是空,兩樣都不畫。
_EZ2_SEAT_CODE=""
_EZ2_NOW_LINE=""
_EZ2_LOGO=""
_EZ2_RANK=""
_EZ2_TAIL=""
# 10-10 a seat:有的 Claude 席沒有 TMUX_PANE ⇒ 也遞 session_id(seat_now 拿去比席位檔,恰好一席才用)
if [ -n "${TMUX_PANE:-}" ] || [ -n "$SID" ]; then
  _ez2_now_out=""
  if [ -n "${EZ2_AIB_ROOT:-}" ] && [ -f "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" ]; then
    _ez2_now_out="$(/usr/bin/python3 "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" --statusline --rank-field --tail-field --session "$SID" 2>/dev/null || true)"
    # 10-28:舊版 seat_now 不認得 --tail-field ⇒ 空 ⇒ 只帶 --rank-field 再問(沒有尾巴;mod 照舊自己一行)
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(/usr/bin/python3 "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" --statusline --rank-field --session "$SID" 2>/dev/null || true)"
    # 10-17:舊版 seat_now 不認得 --rank-field ⇒ 空 ⇒ 不帶旗再問一次(等級就接在 📌 行尾)
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(/usr/bin/python3 "$EZ2_AIB_ROOT/src/cockpit/seat_now.py" --statusline --session "$SID" 2>/dev/null || true)"
  elif command -v aib >/dev/null 2>&1; then
    _ez2_now_out="$(aib now --statusline --rank-field --tail-field --session "$SID" 2>/dev/null || true)"
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(aib now --statusline --rank-field --session "$SID" 2>/dev/null || true)"
    [ -n "$_ez2_now_out" ] || _ez2_now_out="$(aib now --statusline --session "$SID" 2>/dev/null || true)"
  fi
  # 欄位用 \x1f 分(不是 tab:bash read 會把連續 tab 併成一個,📌 欄空時 AI 標誌會跑錯欄)
  # 第 4 欄 = 等級與資源(10-17 the maintainer:「5⭐ 30木材/12石塊」放在 CTX 那一行 @席位 後面;舊版只有 3 欄 ⇒ 空)
  # 第 5 欄 = mod 狀態段(10-28 Andy「這個 aib 太多行了,我希望縮成一行」:未讀 dm · 🌐 語系,接在 CTX 那一行尾端;
  #   statusline_tail.py 只收裝了、還在跑的 mod 寫的段 ⇒ 沒裝的 mod 不會出現;舊版只有 4 欄 ⇒ 空)
  IFS=$'\x1f' read -r _EZ2_SEAT_CODE _EZ2_NOW_LINE _EZ2_LOGO _EZ2_RANK _EZ2_TAIL <<<"$_ez2_now_out"
  case "$_EZ2_SEAT_CODE" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) _EZ2_SEAT_CODE=""; _EZ2_NOW_LINE=""; _EZ2_LOGO=""; _EZ2_RANK=""; _EZ2_TAIL="" ;; esac
fi
sl_safe_sid "$SID" || SID=""

# 底部列 = Ez2 CTX 儀表(ez2-ctx-core 自己 fail-open;node 缺 / 炸 ⇒ 空,那一行就不畫)。
EZ2LINE=$(printf '%s' "$INPUT" | node "$SL_DIR/statusline-ez2-ctx.js" 2>/dev/null)
# 2026-10-10 the maintainer:「Opus 5.5 | 席位 | 💧 Ez2 CTX …」—— 席位插在模型名後面。
#   10-17:席位後面接等級與資源(@570a9 5⭐ 30木材/12石塊),📌 那一行就只剩主項目、步數與更新時間。
[ -n "$_EZ2_SEAT_CODE" ] && [ -n "$EZ2LINE" ] && EZ2LINE="${EZ2LINE/ | / | @$_EZ2_SEAT_CODE${_EZ2_RANK:+ $_EZ2_RANK} | }"
# 2026-10-10 the maintainer:「最前面要加上對應的 AI logo,比照右方 ai 席的 logo」—— 右欄同一個圖示與品牌色(seat_now.logo_of)。
#   圖示後面空**兩**格:Nerd Font 圖示畫得比一格寬,會蓋掉一格空白(the maintainer 10-10「icon 與字要空一格」;右欄 09-02 同一件事)。
[ -n "$_EZ2_LOGO" ] && [ -n "$EZ2LINE" ] && EZ2LINE="$_EZ2_LOGO  $EZ2LINE"
# 10-28 Andy:「… 100k/1M 未讀 dm 5 · 🌐 zh-TW」—— mod 狀態段接在 CTX 那一行最後面(取代輸入框下方的 mod 狀態列)。
[ -n "$_EZ2_TAIL" ] && [ -n "$EZ2LINE" ] && EZ2LINE="$EZ2LINE $_EZ2_TAIL"

# D41(2026-09-06):pane id 只在單一 tmux server 內唯一,不能當跨艙身份鍵。
# /clear 後位址仍是登記 session_uuid 的末五碼,所以 live session 改寫
# by-seat/<登記五碼>.json。找不到艙內席位檔就不寫,也不退回 by-pane。
# ★ 09-07 root744f4:`EZ2_AIB_INSTANCE_HOME` 在 Claude Code 起 statusline 的環境裡是空的
#   ⇒ 從 `$TMUX` 的 socket 名導出(取法與 `dm-ctl.sh` 的 `${TMUX%%,*}` 同源)。
#   fail-closed:導不出來、或那個目錄沒有 `seats/`,就不寫 —— 不猜一個路徑。
_EZ2_SEAT_HOME="${EZ2_AIB_INSTANCE_HOME:-}"
if [ -z "$_EZ2_SEAT_HOME" ] && [ -n "${TMUX:-}" ]; then
  _ez2_sock="${TMUX%%,*}"
  _ez2_sock="${_ez2_sock##*/}"
  case "$_ez2_sock" in
    aihub-*) _ez2_try="$HOME/.ez2-aib/instances/${_ez2_sock#aihub-}" ;;
    aihub)   _ez2_try="$HOME/.ez2-aib/instances/main" ;;
    *)       _ez2_try="" ;;
  esac
  [ -n "$_ez2_try" ] && [ -d "$_ez2_try/seats" ] && _EZ2_SEAT_HOME="$_ez2_try"
fi
if [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ] && [ -n "$SID" ] && [ -n "${_EZ2_SEAT_HOME:-}" ]; then
  _EZ2_LIVE_WINDOW="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
  _EZ2_CTX_ROOT="${EZ2_CTX_DIR:-$HOME/.ez2/ctx}"
  # 10-25:對應(席位、窗、對話 id、行程)沒變就不重寫 —— 讀端只要求這份比換對話前那份新,
  #   換對話那一次一定會寫。以前每次呼叫都起一支 python 重寫同樣的內容(約 100 ms)。
  _ez2_bridge_same=0
  if [ -n "$_EZ2_SEAT_CODE" ] && [ -f "$_EZ2_CTX_ROOT/by-seat/$_EZ2_SEAT_CODE.json" ] \
     && [ ! -L "$_EZ2_CTX_ROOT/by-seat/$_EZ2_SEAT_CODE.json" ]; then
    _ez2_bridge=""
    IFS= read -r _ez2_bridge < "$_EZ2_CTX_ROOT/by-seat/$_EZ2_SEAT_CODE.json" 2>/dev/null || true
    case "$_ez2_bridge" in
      *"\"seat\":\"$_EZ2_SEAT_CODE\","*"\"window\":\"$_EZ2_LIVE_WINDOW\",\"session_id\":\"$SID\","*"\"pid\":$PPID}")
        _ez2_bridge_same=1 ;;
    esac
  fi
  [ "$_ez2_bridge_same" = 1 ] || /usr/bin/python3 -I - "$SID" "$_EZ2_LIVE_WINDOW" "$_EZ2_SEAT_HOME" \
    "$_EZ2_CTX_ROOT" "${EZ2_AIB_INSTANCE:-}" "$PPID" <<'PYBYSEAT' 2>/dev/null || true
import json, os, re, sys, tempfile

sid, window, instance_home, root, instance, pid_text = sys.argv[1:7]
uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
window_re = re.compile(r"@([0-9]{1,10})")
instance_re = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}")
utc_re = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")
m = window_re.fullmatch(window)
if not instance:
    base = os.path.basename(os.path.realpath(instance_home))
    instance = base if os.path.basename(os.path.dirname(os.path.realpath(instance_home))) == "instances" else "main"
try:
    pid = int(pid_text)
except (TypeError, ValueError):
    pid = 0
if m and uuid_re.fullmatch(sid) and instance_re.fullmatch(instance) and pid > 0:
    seat_path = os.path.join(instance_home, "seats", "w%s.json" % m.group(1))
    try:
        with open(seat_path, encoding="utf-8") as fh:
            seat_rec = json.load(fh)
    except Exception:
        seat_rec = None
    registered = seat_rec.get("session_uuid") if isinstance(seat_rec, dict) else None
    seat = registered.replace("-", "")[-5:].lower() if uuid_re.fullmatch(registered or "") else None
    valid_seat = (isinstance(seat_rec, dict)
                  and seat_rec.get("schema") == "aib-seat/v1"
                  and seat_rec.get("window_id") == window
                  and seat is not None and re.fullmatch(r"[0-9a-f]{5}", seat))
    try:
        with open(os.path.join(root, sid + ".pct"), encoding="utf-8") as fh:
            gauge = json.load(fh)
    except Exception:
        gauge = None
    observed = gauge.get("observed_at") if isinstance(gauge, dict) and gauge.get("session_id") == sid else None
    if valid_seat and utc_re.fullmatch(observed or ""):
        d = os.path.join(root, "by-seat")
        os.makedirs(d, mode=0o700, exist_ok=True)
        out = {"schema": "ez2-ctx-by-seat-v1", "seat": seat, "instance": instance,
               "window": window, "session_id": sid, "observed_at": observed, "pid": pid}
        fd, tmp = tempfile.mkstemp(prefix=".%s." % seat, suffix=".tmp", dir=d)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(json.dumps(out, separators=(",", ":")) + "\n")
            os.replace(tmp, os.path.join(d, seat + ".json"))
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
PYBYSEAT
fi

# CTX 那一行。2026-09-21 the maintainer:AIB 艙內整列不印,避免紅框 Model|Ez2 CTX。
#   2026-09-30:艙內量得到百分比就照常顯示;review r1 m12:艙內改成「正面」量到才印 ——
#   剝色碼後要有「CTX <數字>%」才印,其餘(空字串、❓、別種佔位)一律不印。
sl_ctx_line() {
  local _hide=0
  [ -n "${EZ2_AIB_INSTANCE_HOME:-}${AIB_INSTANCE:-}${EZ2_AIB_INSTANCE:-}" ] && _hide=1
  case "${TMUX:-}" in *aihub-*) _hide=1 ;; esac
  # 巢狀在 AIB 艙時,外層 socket 也可能是 default,但 pane 標題／AIB 標記會在環境
  [ -n "${EZ2_TMUX_SESSION:-}" ] && case "${EZ2_TMUX_SESSION}" in aib-*) _hide=1 ;; esac
  [ -n "${EZ2_AIHUB_SOCKET:-}" ] && _hide=1
  if [ "$_hide" = "1" ]; then
    case "$1" in
      *"❓"*|*"CTX ?"*) return 0 ;;
    esac
    printf '%s' "$1" | LC_ALL=C sed -e $'s/\x1b\\[[0-9;]*m//g' \
      | LC_ALL=C grep -Eq 'CTX[^0-9?]{0,6}[0-9]{1,3}%' || return 0
  fi
  printf '%s' "$1"
}
# 10-10 the maintainer「上下二行對調」⇒ CTX 那一行在上、📌 那一行在下;CTX 那一行不印時只印 📌 那一行。
_main="$(sl_ctx_line "$EZ2LINE")"
if [ -n "$_main" ]; then
  printf '%s' "$_main"
  [ -n "$_EZ2_NOW_LINE" ] && printf '\n%s' "$_EZ2_NOW_LINE"
else
  # 10-28:CTX 那一行沒印(艙內量不到)⇒ mod 狀態段改接在 📌 那一行尾端 —— mod 已經看到「有在畫」收掉自己那一行,這裡不接就整段不見
  [ -n "$_EZ2_TAIL" ] && _EZ2_NOW_LINE="${_EZ2_NOW_LINE:+$_EZ2_NOW_LINE · }$_EZ2_TAIL"
  [ -n "$_EZ2_NOW_LINE" ] && printf '%s' "$_EZ2_NOW_LINE"
fi
exit 0
