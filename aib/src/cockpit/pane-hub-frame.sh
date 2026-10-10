#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# hub 對話欄頂框：標題列之下、功能區 Plus 之上。
# 畫：短「訊息指令」圓角框 +「私訊對話」抬頭（[AIB 私訊對話區] [≡N]）。
# 真 AI 仍在 hub 活窗；這裡不 capture、不雙顯。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/cockpit-env.sh"
. "$HERE/pane-tick.sh"            # 只借 pane_wait_if_composing(10-10);不叫 pane_tick_init,等待仍是原本的 sleep 1
PY3="${EZ2_PY3:-python3}"

_paint() {
  COLUMNS="${COLUMNS:-80}" TMUX_PANE="${TMUX_PANE:-}" \
  EZ2_COCKPIT_STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-}" \
  EZ2_AIB_FOCUS_SEAT="${EZ2_AIB_FOCUS_SEAT:-}" \
  EZ2_TMUX_SESSION="${EZ2_TMUX_SESSION:-}" \
  EZ2_AIHUB_SOCKET="${EZ2_AIHUB_SOCKET:-}" \
  "$PY3" - "$HERE" <<'PY'
import os, sys, json
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], "render"))
import panel_layout as PL
import panel_chrome as PC
import time

# ── aihub 席 → realtime-lines：回應顯示區＝席原文直印（不總結）；輸入區只留打字
def sync_hub_messages_to_realtime(state_dir, hub_pane):
    # hub_pane 保留參數相容；真正來源是 EZ2_AIHUB_SOCKET 作用中席
    try:
        import realtime_log as RL
        sock = os.environ.get("EZ2_AIHUB_SOCKET") or ""
        RL.sync_from_aihub(state_dir, socket=sock)
    except Exception:
        return



shine = 0.0  # static green; no per-tick breath (avoids 指令區 flicker)

cols = int(os.environ.get("COLUMNS") or "80")
pane = os.environ.get("TMUX_PANE") or ""
if pane:
    try:
        import subprocess
        p = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{pane_width}"],
            capture_output=True, text=True, timeout=3)
        if p.returncode == 0 and (p.stdout or "").strip().isdigit():
            cols = int(p.stdout.strip())
    except Exception:
        pass

state = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""

# hub 訊息同步到指令區檔（輸入區本體仍是 hub，但指令區顯示歷史訊息）
_hub_pane = ""
try:
    import subprocess as _sp
    _sess = os.environ.get("EZ2_TMUX_SESSION") or ""
    _self = os.environ.get("TMUX_PANE") or ""
    if not _sess and _self:
        _r = _sp.run(
            ["tmux", "display-message", "-p", "-t", _self, "#{session_name}"],
            capture_output=True, text=True, timeout=2,
        )
        _sess = (_r.stdout or "").strip()
    if _sess:
        _ls = _sp.run(
            ["tmux", "list-panes", "-t", _sess, "-F", "#{pane_id} #{@aib_role}"],
            capture_output=True, text=True, timeout=2,
        )
        for _ln in (_ls.stdout or "").splitlines():
            _bits = _ln.split(None, 1)
            if len(_bits) == 2 and _bits[1].strip() == "hub":
                _hub_pane = _bits[0]
                break
except Exception:
    _hub_pane = ""
if state:
    sync_hub_messages_to_realtime(state, _hub_pane)


lines, rowmap = [], []
# 焦點席 provider 在 aihub 窗的 @aib_provider，不在 hub-frame pane。
# 跟 mouse-ctl hub-io-toggle 同一條讀法。
import subprocess as _sp_prov
_prov = (os.environ.get("EZ2_AIB_FOCUS_PROVIDER") or os.environ.get("AIB_FOCUS_PROVIDER") or "").strip()
if not _prov:
    try:
        _sock = (os.environ.get("EZ2_AIHUB_SOCKET") or "").strip()
        if _sock:
            _out = (_sp_prov.run(
                ["tmux", "-L", _sock, "list-windows", "-t", "aihub",
                 "-F", "#{window_active} #{@aib_provider}"],
                capture_output=True, text=True, timeout=2,
            ).stdout or "")
            for _ln in _out.splitlines():
                _bits = _ln.split(None, 1)
                if len(_bits) == 2 and _bits[0] == "1" and _bits[1].strip():
                    _prov = _bits[1].strip()
                    break
        if not _prov:
            # 後備：本 session 作用中窗（極少用）
            _prov = (_sp_prov.run(
                ["tmux", "display-message", "-p", "#{@aib_provider}"],
                capture_output=True, text=True, timeout=2,
            ).stdout or "").strip()
    except Exception:
        _prov = ""
_may_close = PL.hub_io_may_close(_prov) is True
rt_on, _ = PL.effective_open("realtime", provider_id=_prov or None)
dlg_on, _ = PL.effective_open("dialog")
lay = PL.load_layout()
dh = PL._clamp_height(lay.get("dialog_height"), PL.DEFAULT_DIALOG_HEIGHT)
rh = PL._clamp_realtime_height(lay.get("realtime_height"), PL.DEFAULT_REALTIME_HEIGHT)
ih = PL._clamp_height(lay.get("input_height"), PL.DEFAULT_INPUT_HEIGHT)
try:
    _off = int(open(os.path.join(state or ".", "dialog-scroll-offset"), encoding="utf-8").read().strip() or "0")
except Exception:
    _off = 0
seat = os.environ.get("EZ2_AIB_FOCUS_SEAT") or ""
# ≡N 是偏好；實畫列數先看 hub-frame 現高（吃掉輸入區底下誤留的空白），再夾窗高。
try:
    import subprocess
    wh = int(subprocess.run(
        ["tmux", "display-message", "-p", "-t", pane or "", "#{window_height}"],
        capture_output=True, text=True, timeout=3).stdout.strip() or "0")
    fh = int(subprocess.run(
        ["tmux", "display-message", "-p", "-t", pane or "", "#{pane_height}"],
        capture_output=True, text=True, timeout=3).stdout.strip() or "0")
except Exception:
    wh = 0
    fh = 0
# 窗高預留 hub(≡) + 待審地板；若 frame 已被灌高，用 fh 把多餘列消化進回應顯示區
# 實畫硬頂 = pane_height（勿用 window_height 當預算，否則 rowmap 超過可視列，[≡] 點空）
import hub_frame_fit as HF
_max_frame = HF.paint_budget(fh, wh)
# hub-io 開且 frame 仍過矮：先用窗高估預算組畫（避免 fh=2 雞生蛋）；clamp 仍用實高
_OPEN_CHROME_FLOOR = 10
if rt_on and fh > 0 and fh < _OPEN_CHROME_FLOOR and wh > 0:
    try:
        _hub_want = PL._clamp_height(lay.get("input_height"), PL.DEFAULT_INPUT_HEIGHT)
        _fh_want = PL.hub_io_open_pane_heights(wh, _hub_want)[0]
        _max_frame = max(int(_max_frame or 0), int(_fh_want))
    except Exception:
        _max_frame = max(int(_max_frame or 0), HF.paint_budget(0, wh))
# 選單 2 + 抬頭 1 + 底 1 + 正文 = 4 + body
# 預留回應顯示區最小圓框(~3) + 輸入頂蓋(1)；hub 本文在另一 pane，不從 frame 預算扣 ih
_rt_reserve = 4 if rt_on else 0
_in_reserve = 1 if rt_on else 0
_max_body = max(1, _max_frame - 4 - _rt_reserve - _in_reserve) if _max_frame else dh
body_rows = min(dh, _max_body)

# 回應顯示區資料先讀；畫在私訊對話下方（中間帶），空也畫待審同款圓框
rt_rows = []
if rt_on:
    try:
        path = os.path.join(state, "realtime-lines")
        rt_rows = [ln.rstrip("\n") for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()][-max(1, int(rh)):]
    except OSError:
        rt_rows = []




# 私訊對話正文：只讀 dialog-lines，且只留真人 DM 列（AI 訊不進此區）。
hub_lines = []
try:
    import dialog_log as DL
    hub_lines = DL.read_human_dialog_lines(state)
except Exception:
    hub_lines = []
if hub_lines:
    _off = max(0, min(_off, max(0, len(hub_lines) - max(1, body_rows))))

if dlg_on:
    # 選單列（未來功能位）在上；其下完整圓框，正文列數對應 [≡N]
    for tl, tm in (
        PC.render_dialog_menu_bar(cols),
        PC.render_dialog_box(hub_lines, cols, size_mark=dh, max_height=body_rows, offset=max(0, _off), identity="", seat=seat, edge_shine=shine),
    ):
        off = len(lines)
        lines.extend(tl)
        for row in tm or []:
            if not isinstance(row, dict):
                continue
            rr = dict(row)
            try:
                rr["line"] = int(row.get("line") or 0) + off
            except (TypeError, ValueError):
                rr["line"] = off
            rowmap.append(rr)

# 中：回應顯示區完整圓框（純狀態，無艦長）
if rt_on:
    # ≡N 是偏好；實畫高度永遠受窗／已畫列夾住，避免 ≡40 把對話區頂出可視區
    _budget = int(_max_frame) if _max_frame else 12
    _used = len(lines) + 1  # 已畫 + 輸入頂蓋（hub 本文在下方獨立 pane，不扣 ih）
    # 剩餘列全部餵給回應區，避免輸入標題底下留空；[≡N] 仍顯示偏好 rh
    _slack = max(1, _budget - _used)
    _rt_cap = max(int(rh), _slack) if _budget else int(rh)
    _rt_cap = max(1, min(_rt_cap, _slack))
    rl, rm = PC.render_realtime_status_box(
        rt_rows, cols, size_mark=rh, identity="", seat=seat, shine=shine, edge_shine=shine, max_height=_rt_cap,
        open_bottom=True, show_hub_io_toggle=_may_close, hub_io_open=rt_on)
    off = len(lines)
    lines.extend(rl)
    for row in rm or []:
        if not isinstance(row, dict):
            continue
        rr = dict(row)
        try:
            rr["line"] = int(row.get("line") or 0) + off
        except (TypeError, ValueError):
            rr["line"] = off
        rowmap.append(rr)

# 下：訊息輸入區頂蓋（正文=下方 hub；底蓋+艦長掛待審頂）
if rt_on:
    il, im = PC.render_input_header(cols, size_mark=ih, identity="", seat=seat, shine=shine,
                                    show_hub_io_toggle=_may_close, hub_io_open=rt_on)
    off = len(lines)
    lines.extend(il)
    for row in im or []:
        if not isinstance(row, dict):
            continue
        rr = dict(row)
        try:
            rr["line"] = int(row.get("line") or 0) + off
        except (TypeError, ValueError):
            rr["line"] = off
        rowmap.append(rr)

# OAuth 席成對關閉後：畫一列 [+] 重開條（api_key 不畫；使用者 reopen_strip=false 也不畫，與 _need_frame 同一問）
if (not rt_on) and _may_close and PL.reopen_strip_on(layout=lay):
    ol, om = PC.render_hub_io_reopen_strip(cols, identity="", seat=seat)
    off = len(lines)
    lines.extend(ol)
    for row in om or []:
        if not isinstance(row, dict):
            continue
        rr = dict(row)
        try:
            rr["line"] = int(row.get("line") or 0) + off
        except (TypeError, ValueError):
            rr["line"] = off
        rowmap.append(rr)

if not lines:
    sys.exit(3)

# 最後保險：裁到 pane_height，避免 overflow 捲走熱區
lines, rowmap = HF.clamp_paint(lines, rowmap, fh if fh > 0 else _max_frame)

if state:
    path = os.path.join(state, "hub-frame-rowmap.json")
    tmp = path + ".%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rowmap, fh, ensure_ascii=False)
    os.replace(tmp, path)

sys.stdout.write("\n".join(lines) + "\n")
PY
}

_loop() {
  if [ -n "${TMUX_PANE:-}" ]; then
    tmux set-option -p -t "$TMUX_PANE" @aib_role hub-frame >/dev/null 2>&1 || true
    tmux set-option -p -t "$TMUX_PANE" @aib_pane hub-frame >/dev/null 2>&1 || true
  fi
  stty -echo 2>/dev/null || true
  printf '\033[?25l'
  local last="" cur n last_n=0 prc _HF_EMPTY=$'\x1f(empty)' _hf_empty_n=0
  while :; do
    # 點捲軸會 bump hub-frame-repaint.stamp → 強制下一拍重畫
    stamp="${EZ2_COCKPIT_STATE_DIR:-}/hub-frame-repaint.stamp"
    if [ -f "$stamp" ]; then
      last=""
      rm -f "$stamp" 2>/dev/null || true
    fi
    # 每拍重跑本檔 --paint，tip 更新不必殺艙也能吃到
    prc=0
    cur="$(bash "$0" --paint 2>/dev/null)" || prc=$?
    [ "$prc" = "0" ] || cur=""
    # ★ review r2 m9:--paint 回 3 = 現在沒有東西可畫(私訊區關 + 使用者關掉重開細框)。
    #   舊版保留上一幀 ⇒ 細框一直掛在畫面上、rowmap 也還指著它(點下去還會觸發)。
    #   改成清掉畫面、刪掉 rowmap(只做一次;之後 --paint 又有東西就照常重畫)。
    # ★ review r3 m2:要**連續 3 拍**都回 3 才清(一拍 1 秒)——焦點席引擎一時查不到(換窗、tmux 慢)也會回 3,
    #   一拍就清會讓頂框閃一秒空白;中間任何一拍不是 3 就重新數。
    if [ "$prc" = "3" ]; then
      _hf_empty_n=$((_hf_empty_n + 1))
    else
      _hf_empty_n=0
    fi
    if [ "$prc" = "3" ] && [ "$_hf_empty_n" -ge 3 ] && [ "$last" != "$_HF_EMPTY" ]; then
      tmux clear-history -t "${TMUX_PANE:-}" >/dev/null 2>&1 || true
      printf '\033[H\033[J'
      [ -n "${EZ2_COCKPIT_STATE_DIR:-}" ] && rm -f "$EZ2_COCKPIT_STATE_DIR/hub-frame-rowmap.json" 2>/dev/null
      last="$_HF_EMPTY"
    fi
    if [ -n "$cur" ]; then
      n=$(printf '%s\n' "$cur" | wc -l | tr -d ' ')
      # 高度停火：迴圈內禁止 resize frame（待審縮高會把列灌進 hub，這裡再收 → 1↔2 跳）。
      true
      if [ "$cur" != "$last" ]; then
        # 清捲動史，讓可視列 0 = rowmap line 0，否則 [≡] 滑鼠 y 會對錯列
        tmux clear-history -t "${TMUX_PANE:-}" >/dev/null 2>&1 || true
        printf '\033[H\033[J%s' "$cur"
        last="$cur"
      fi
    fi

    # 高度停火：迴圈內禁止鎖 hub。≡N 只在 --ensure／人工切換時設定一次。
    pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10;見 pane-tick.sh)
    sleep 1
  done
}

_dialog_height() {
  "$PY3" - "$HERE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
print(PL.load_layout().get("dialog_height", 1))
PY
}

_input_height() {
  # 實作列數（已夾窗高）；標籤仍顯示偏好 input_height
  local _pane="${TMUX_PANE:-}"
  "$PY3" - "$HERE" "$_pane" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
lay = PL.load_layout()
want = lay.get("input_height", PL.DEFAULT_INPUT_HEIGHT)
wh = 0
pane = sys.argv[2] if len(sys.argv) > 2 else ""
if pane:
    import subprocess
    try:
        wh = int(subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{window_height}"],
            capture_output=True, text=True, timeout=2).stdout.strip() or "0")
    except Exception:
        wh = 0
_prov = (os.environ.get("EZ2_AIB_FOCUS_PROVIDER") or "").strip()
if not _prov:
    try:
        import subprocess as _sp
        _sock = (os.environ.get("EZ2_AIHUB_SOCKET") or "").strip()
        if _sock:
            _out = (_sp.run(
                ["tmux", "-L", _sock, "list-windows", "-t", "aihub",
                 "-F", "#{window_active} #{@aib_provider}"],
                capture_output=True, text=True, timeout=2,
            ).stdout or "")
            for _ln in _out.splitlines():
                _bits = _ln.split(None, 1)
                if len(_bits) == 2 and _bits[0] == "1" and _bits[1].strip():
                    _prov = _bits[1].strip()
                    break
    except Exception:
        pass
print(PL.effective_hub_io_hub_rows(want, wh, provider_id=_prov or None))
PY
}

_need_frame() {
  "$PY3" - "$HERE" <<'PY'
import os, sys, subprocess
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
_prov = (os.environ.get("EZ2_AIB_FOCUS_PROVIDER") or "").strip()
if not _prov:
    try:
        _sock = (os.environ.get("EZ2_AIHUB_SOCKET") or "").strip()
        if _sock:
            _out = (subprocess.run(
                ["tmux", "-L", _sock, "list-windows", "-t", "aihub",
                 "-F", "#{window_active} #{@aib_provider}"],
                capture_output=True, text=True, timeout=2,
            ).stdout or "")
            for _ln in _out.splitlines():
                _bits = _ln.split(None, 1)
                if len(_bits) == 2 and _bits[0] == "1" and _bits[1].strip():
                    _prov = _bits[1].strip()
                    break
    except Exception:
        _prov = ""
rt, _ = PL.effective_open("realtime", provider_id=_prov or None)
dg, _ = PL.effective_open("dialog")
# the maintainer 09-30 16:5x:使用者把「重開回應區」細框也關掉(reopen_strip=false)⇒ 回應+輸入關著時整個頂框不長
need_reopen = ((not rt) and (PL.hub_io_may_close(_prov) is True)
               and PL.reopen_strip_on())
sys.exit(0 if (rt or dg or need_reopen) else 1)
PY
}


_scroll() {
  local dir="${1:-down}"
  local state="${EZ2_COCKPIT_STATE_DIR:-}"
  [ -n "$state" ] || return 0
  "$PY3" - "$state" "$dir" <<'PY' || true
import sys
from pathlib import Path
root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
p = root / "dialog-scroll-offset"
try:
    cur = int(p.read_text(encoding="utf-8").strip() or "0")
except Exception:
    cur = 0
delta = -3 if sys.argv[2] == "up" else 3
p.write_text("%d\n" % max(0, cur + delta), encoding="utf-8")
PY
}

_pad_l_loop() {
  # hub 左緣 3 欄：前兩格空白，第 3 格對齊 ╭ 內側畫靜態綠 •（訊息輸入區正文）。
  # 不呼吸、不全清；只在必要項目第一行。
  printf '\033[?25l' 2>/dev/null || true
  local h last="" cur
  while :; do
    h=$(tmux display-message -p -t "${TMUX_PANE:-}" '#{pane_height}' 2>/dev/null || echo 1)
    case "$h" in ''|*[!0-9]*) h=1 ;; esac
    [ "$h" -lt 1 ] && h=1
    cur="$("$PY3" - "$HERE" "$h" <<'PZ'
import os, sys, subprocess
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], "render"))
from render_lib import (
    edge_cell, is_edge_continuation, is_necessary_edge_item,
    strip_leading_edge_dot, rewrite_human_prompt, visible_width,
)
h = max(1, int(sys.argv[2]))
hub = os.environ.get("EZ2_HUB_PANE") or ""
lines = []
if hub:
    try:
        cap = subprocess.run(
            ["tmux", "capture-pane", "-t", hub, "-p", "-J"],
            capture_output=True, text=True, timeout=2,
        )
        lines = (cap.stdout or "").splitlines()
    except Exception:
        lines = []
vis = lines[-h:] if lines else []
while len(vis) < h:
    vis.insert(0, "")
full_w = max([visible_width(x) for x in vis if x.strip()] or [80])
out, prev = [], None
for row in vis:
    s = strip_leading_edge_dot(rewrite_human_prompt(row))
    cont = is_edge_continuation(prev, s, full_w)
    need = is_necessary_edge_item(s) and not cont
    cell = edge_cell(0.0, active=True, continued=cont, necessary=need)
    out.append("  " + cell)
    prev = s if s.strip() else None
sys.stdout.write("\n".join(out) + "\n")
PZ
)"
    # 鎖寬 3：暴板時 pad 曾被撐到數十欄，指令區正文右移、• 跑到彎線外
    w_now="$(tmux display-message -p -t "${TMUX_PANE:-}" "#{pane_width}" 2>/dev/null || echo 0)"
    case "$w_now" in ''|*[!0-9]*) w_now=0 ;; esac
    if [ "$w_now" -ne 3 ]; then
      tmux resize-pane -t "${TMUX_PANE:-}" -x 3 >/dev/null 2>&1 || true
    fi
    if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
      printf '\033[H%s\033[J' "$cur"
      last="$cur"
    fi
    pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10;見 pane-tick.sh)
    sleep 1
  done
}


_apply_input_gray_bg() {
  # 訊息輸入區底：hub + 左右 pad 全席統一深灰 #1e1e1e（對齊 Codex 近黑 rgb 30,30,30；勿用偏亮中灰）。
  local s="$1" id role
  local style='fg=default,bg=#1e1e1e'
  while read -r id role; do
    case "$role" in
      hub|hub-pad-l|hub-pad-r)
        env -u TMUX tmux select-pane -t "$id" -P "$style" 2>/dev/null || true
        ;;
    esac
  done < <(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null)
}

_ensure_hub_content_pads() {
  # 讓 hub 內文左緣對齊對話／待審正文（PAD_L+空白側+INNER = 3）。
  # pad 寬 2 + tmux 邊框 1 格 → hub.left=3。幾何不對就拆掉，絕不留下暴板狀態。
  local s="${EZ2_TMUX_SESSION:-}"
  [ -n "$s" ] || return 0
  local hub pad_l pad_r new PAD=3
  hub="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role} #{pane_height} #{pane_width} #{pane_left} #{pane_top}' \
        2>/dev/null | awk '$2=="hub"{print; exit}')"
  [ -n "$hub" ] || return 0
  local hub_id hub_h hub_w hub_left hub_top
  hub_id="$(awk '{print $1}' <<<"$hub")"
  hub_h="$(awk '{print $3}' <<<"$hub")"
  hub_w="$(awk '{print $4}' <<<"$hub")"
  hub_left="$(awk '{print $5}' <<<"$hub")"
  hub_top="$(awk '{print $6}' <<<"$hub")"

  _kill_pads() {
    local p role
    for role in hub-pad-l hub-pad-r; do
      p="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk -v r="$role" '$2==r{print $1; exit}')"
      [ -n "$p" ] && env -u TMUX tmux kill-pane -t "$p" 2>/dev/null || true
    done
  }

  _pad_ok() {
    # $1=pane_id expected side l|r — 寬必須是 PAD，高必須≈hub，且與 hub 同 top
    local pid="$1" side="$2"
    [ -n "$pid" ] || return 1
    local meta w h left top
    meta="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{pane_width} #{pane_height} #{pane_left} #{pane_top}' \
      2>/dev/null | awk -v p="$pid" '$1==p{print; exit}')"
    [ -n "$meta" ] || return 1
    w="$(awk '{print $2}' <<<"$meta")"
    h="$(awk '{print $3}' <<<"$meta")"
    left="$(awk '{print $4}' <<<"$meta")"
    top="$(awk '{print $5}' <<<"$meta")"
    [ "$w" -eq "$PAD" ] || return 1
    # 高不得超過 hub+1，也不得變成整欄（> hub+2 就當暴板）
    [ "$h" -ge 1 ] && [ "$h" -le $((hub_h + 1)) ] || return 1
    [ "$top" -eq "$hub_top" ] || return 1
    if [ "$side" = l ]; then
      [ "$left" -eq 0 ] || [ "$left" -lt "$hub_left" ] || return 1
    fi
    return 0
  }

  pad_l="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub-pad-l"{print $1; exit}')"
  pad_r="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub-pad-r"{print $1; exit}')"

  # 既有 pad 幾何怪 → 全拆重來
  if [ -n "$pad_l" ] && ! _pad_ok "$pad_l" l; then _kill_pads; pad_l=; pad_r=; fi
  if [ -n "$pad_r" ] && ! _pad_ok "$pad_r" r; then _kill_pads; pad_l=; pad_r=; fi

  # 重讀 hub（可能因拆 pad 變寬）
  hub="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role} #{pane_height} #{pane_width} #{pane_left} #{pane_top}' \
        2>/dev/null | awk '$2=="hub"{print; exit}')"
  hub_id="$(awk '{print $1}' <<<"$hub")"
  hub_h="$(awk '{print $3}' <<<"$hub")"
  hub_top="$(awk '{print $6}' <<<"$hub")"

  if [ -z "$pad_l" ]; then
    new="$(env -u TMUX tmux split-window -h -b -t "$hub_id" -l "$PAD" -P -F '#{pane_id}' \
      "EZ2_HUB_PANE=$hub_id bash '$HERE/pane-hub-frame.sh' --pad-l-loop" 2>/dev/null || true)"
    if [ -n "$new" ]; then
      env -u TMUX tmux set-option -p -t "$new" @aib_role hub-pad-l >/dev/null 2>&1 || true
      env -u TMUX tmux resize-pane -t "$new" -x "$PAD" 2>/dev/null || true
      pad_l="$new"
    fi
  fi

  hub_id="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub"{print $1; exit}')"
  if [ -z "$pad_r" ] && [ -n "$hub_id" ]; then
    new="$(env -u TMUX tmux split-window -h -t "$hub_id" -l "$PAD" -P -F '#{pane_id}' \
      'printf "\033[?25l"; while true; do sleep 3600; done' 2>/dev/null || true)"
    if [ -n "$new" ]; then
      env -u TMUX tmux set-option -p -t "$new" @aib_role hub-pad-r >/dev/null 2>&1 || true
      env -u TMUX tmux resize-pane -t "$new" -x "$PAD" 2>/dev/null || true
      pad_r="$new"
    fi
  fi

  # 夾住寬度 + 最終幾何閘：hub.left 必須是 3（對齊正文）；否則拆掉以免暴板
  [ -n "$pad_l" ] && env -u TMUX tmux resize-pane -t "$pad_l" -x "$PAD" 2>/dev/null || true
  [ -n "$pad_r" ] && env -u TMUX tmux resize-pane -t "$pad_r" -x "$PAD" 2>/dev/null || true

  hub_meta="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role} #{pane_left} #{pane_height} #{pane_top}' \
        2>/dev/null | awk '$2=="hub"{print; exit}')"
  hub_left="$(awk '{print $3}' <<<"$hub_meta")"
  hub_h="$(awk '{print $4}' <<<"$hub_meta")"
  hub_top="$(awk '{print $5}' <<<"$hub_meta")"
  pad_l="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub-pad-l"{print $1; exit}')"
  pad_r="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub-pad-r"{print $1; exit}')"

  if ! _pad_ok "$pad_l" l || ! _pad_ok "$pad_r" r || [ "$hub_left" -ne 4 ]; then
    _kill_pads
    return 0
  fi
  _apply_input_gray_bg "$s"
}



_foot_force_h2() {
  # 停用：pad 與 hub 同行，對 pad -U 會把 ≡1 整排扯成 2，主迴圈再鎖回 1 → 一直跳。
  return 0
}

_cmd_foot_loop() {
  # 兩行：① 艦長列 ② ╰───╯ —— 同左緣縮進，貼在彎曲線內；不全清以免閃。
  printf '\033[?25l' 2>/dev/null || true
  local last="" cur w sock h
  sock="${EZ2_AIHUB_SOCKET:-}"
  while :; do
    if [ -n "${TMUX_PANE:-}" ]; then
      _foot_force_h2 "$TMUX_PANE"
    fi
    w=$(tmux display-message -p -t "${TMUX_PANE:-}" "#{pane_width}" 2>/dev/null || echo 80)
    case "$w" in ''|*[!0-9]*) w=80 ;; esac
    [ "$w" -lt 16 ] && w=16
    cur="$("$PY3" - "$HERE" "$w" "$sock" <<'PY'
import os, sys, subprocess
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], 'render'))
import panel_chrome as PC
from render_lib import PENDING_PAD_L, visible_width, trunc

w = max(16, int(sys.argv[2]))
sock = (sys.argv[3] if len(sys.argv) > 3 else '') or ''
pad = ' ' * int(PENDING_PAD_L)

def captain_line(width, sock):
    text = ''
    if sock:
        try:
            env = os.environ.copy()
            env.pop('TMUX', None)
            wins = subprocess.check_output(
                ['tmux', '-L', sock, 'list-windows', '-t', 'aihub',
                 '-F', '#{?#{==:#{@aib_captain},1}, #{@aib_captain_icon} #{@aib_persona}@#{@aib_seat} ,}'],
                stderr=subprocess.DEVNULL, text=True, timeout=1, env=env,
            )
            marks = ''.join(x for x in wins.splitlines() if x.strip())
            text = marks.strip()
        except Exception:
            text = ''
    body = ('艦長: ' + text).rstrip() if text else '艦長:'
    inner = max(8, width - int(PENDING_PAD_L) - 1)
    body = trunc(body, inner)
    line = pad + body
    if visible_width(line) > width:
        line = trunc(line, width)
    return line

cap = captain_line(w, sock)
foot_lines, _ = PC.render_realtime_footer(w)
foot = foot_lines[0] if foot_lines else ''

def _pad_row(s, width):
    # 右側空白補滿，蓋掉舊殘字；不用 \033[J 全清
    from render_lib import visible_width as vw, trunc as tr
    s = tr(s, width)
    pad_n = max(0, width - vw(s))
    return s + (' ' * pad_n)

sys.stdout.write(_pad_row(cap, w) + chr(10) + _pad_row(foot, w))
PY
)"
    if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
      printf '\033[H%s' "$cur"
      last="$cur"
    fi
    pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10;見 pane-tick.sh)
    sleep 1
  done
}

_foot_force_h2_outer() {
  # 同 _foot_force_h2：禁止垂直扯高，避免 hub 行 1↔2 跳
  return 0
}

_ensure_cmd_foot() {
  # 2026-09-20: 艦長+╰ 改畫在待審頂，獨立 hub-cmd-foot 廢除（避免互搶高與「跑出彎外」）。
  local s="${EZ2_TMUX_SESSION:-}"
  [ -n "$s" ] || return 0
  local foot
  foot="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}'         2>/dev/null | awk '$2=="hub-cmd-foot"{print $1; exit}')"
  if [ -n "$foot" ]; then
    env -u TMUX tmux kill-pane -t "$foot" 2>/dev/null || true
  fi
  # aihub status 仍關，免重複艦長列
  if [ -n "${EZ2_AIHUB_SOCKET:-}" ]; then
    env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" set-option -g status off >/dev/null 2>&1 || true
  fi
}

_apply_input_height() {
  # 優先序：1)私訊完整 2)待審維持 3)中間吃剩餘（開=回應+輸入／關=CLI hub）
  # 畫面變動請走 cockpit-reflow-left-heights.sh（resize hook 已掛）
  # 硬守門：沒有 @aib_role=hub 就拒做（不可 silent no-op，也絕不可 kill hub）
  local s="${EZ2_TMUX_SESSION:-}"
  [ -n "$s" ] || return 0
  local hub pad_l pad_r frame rev n win_h rev_h frame_h hub_h paint_n sizes rev_floor=4 rev_target dh need
  hub="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub"{print $1; exit}')"
  if [ -z "$hub" ]; then
    echo "pane-hub-frame: _apply_input_height: missing hub pane (refuse; remount hub first)" >&2
    return 2
  fi
  pad_l="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub-pad-l"{print $1; exit}')"
  pad_r="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub-pad-r"{print $1; exit}')"
  frame="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub-frame"{print $1; exit}')"
  rev="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="review"{print $1; exit}')"
  n="$(_input_height)"
  case "$n" in ""|*[!0-9]*) n=0 ;; esac
  win_h="$(env -u TMUX tmux display-message -p -t "$s" '#{window_height}' 2>/dev/null || echo 0)"
  case "$win_h" in ""|*[!0-9]*) win_h=0 ;; esac
  rev_h=0
  [ -n "$rev" ] && rev_h="$(env -u TMUX tmux display-message -p -t "$rev" '#{pane_height}' 2>/dev/null || echo 0)"
  case "$rev_h" in ""|*[!0-9]*) rev_h=0 ;; esac

  if [ "$n" -le 0 ]; then
    # 關 hub-io：frame 至少=私訊≡N完整列+[+]；勿用被矮 frame 裁過的 paint 回推
    dh="$("$PY3" - "$HERE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
lay = PL.load_layout()
print(PL._clamp_height(lay.get("dialog_height"), PL.DEFAULT_DIALOG_HEIGHT))
PY
)"
    case "$dh" in ""|*[!0-9]*) dh=1 ;; esac
    need="$("$PY3" - "$HERE" "$dh" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
# +1 是 [+] 重開細框;reopen_strip=false 時那列不畫,也就不留(與 --paint 同一問)
print(PL.hub_io_closed_frame_need(sys.argv[2]))
PY
)"
    case "$need" in ""|*[!0-9]*) need=12 ;; esac
    if [ -n "$frame" ]; then
      _fh_now="$(env -u TMUX tmux display-message -p -t "$frame" '#{pane_height}' 2>/dev/null || echo 0)"
      case "$_fh_now" in ""|*[!0-9]*) _fh_now=0 ;; esac
      if [ "$_fh_now" -lt "$need" ]; then
        env -u TMUX tmux resize-pane -t "$frame" -y "$need" >/dev/null 2>&1 || true
      fi
    fi
    # --paint 沒東西可畫時 exit 3(私訊區關 + reopen_strip=false 就會);本檔 set -o pipefail ⇒ 不接住的話
    #   整個 _apply_input_height 在這裡中止,hub/待審一格都不調(review r1 M1 帶出來的)
    paint_n="$({ bash "$0" --paint 2>/dev/null || true; } | wc -l | tr -d ' ')"
    case "$paint_n" in ""|*[!0-9]*) paint_n=1 ;; esac
    [ "$paint_n" -lt 1 ] && paint_n=1
    [ "$paint_n" -lt "$need" ] && paint_n=$need
    # 待審 ≡N：鎖 pref+4（≥rev_floor），勿只靠當下已被壓矮的 rev_h
    rev_target=$rev_floor
    pref=1
    _pv_dir="${EZ2_COCKPIT_STATE_DIR:-}"
    if [ -z "$_pv_dir" ] && [ -n "${EZ2_AIB_INSTANCE_HOME:-}" ]; then
      _pv_dir="$EZ2_AIB_INSTANCE_HOME/cockpit"
    fi
    if [ -n "$_pv_dir" ] && [ -f "$_pv_dir/pending-visible" ]; then
      pref="$(tr -d '[:space:]' < "$_pv_dir/pending-visible" 2>/dev/null || echo 1)"
    fi
    case "$pref" in ""|*[!0-9]*) pref=1 ;; esac
    rev_target=$((pref + 4))
    [ "$rev_target" -lt "$rev_floor" ] && rev_target=$rev_floor
    [ "$rev_h" -gt "$rev_target" ] && rev_target=$rev_h
    sizes="$("$PY3" - "$HERE" "$win_h" "$paint_n" "$rev_target" "$rev_floor" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
fh, hh = PL.hub_io_closed_pane_heights(sys.argv[2], sys.argv[3], sys.argv[4], review_floor=int(sys.argv[5]))
print("%d %d" % (fh, hh))
PY
)"
    frame_h="${sizes%% *}"
    hub_h="${sizes#* }"
    case "$frame_h" in ""|*[!0-9]*) frame_h=$paint_n ;; esac
    case "$hub_h" in ""|*[!0-9]*) hub_h=8 ;; esac
    [ "$hub_h" -lt 8 ] && hub_h=8
    if [ -n "$frame" ]; then
      env -u TMUX tmux resize-pane -t "$frame" -y "$frame_h" >/dev/null 2>&1 || true
    fi
    env -u TMUX tmux resize-pane -t "$hub" -y "$hub_h" >/dev/null 2>&1 || true
    [ -n "$pad_l" ] && env -u TMUX tmux resize-pane -t "$pad_l" -y "$hub_h" >/dev/null 2>&1 || true
    [ -n "$pad_r" ] && env -u TMUX tmux resize-pane -t "$pad_r" -y "$hub_h" >/dev/null 2>&1 || true
    if [ -n "$rev" ]; then
      if [ "$win_h" -gt 0 ]; then
        rev_need=$((win_h - frame_h - hub_h))
        [ "$rev_need" -lt "$rev_target" ] && rev_need=$rev_target
        [ "$rev_need" -lt "$rev_floor" ] && rev_need=$rev_floor
      else
        rev_need=$rev_target
      fi
      rev_now="$(env -u TMUX tmux display-message -p -t "$rev" '#{pane_height}' 2>/dev/null || echo 0)"
      case "$rev_now" in ""|*[!0-9]*) rev_now=0 ;; esac
      if [ "$rev_now" -ne "$rev_need" ]; then
        env -u TMUX tmux resize-pane -t "$rev" -y "$rev_need" >/dev/null 2>&1 || true
      fi
    fi
    return 0
  fi

  dh="$("$PY3" - "$HERE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
lay = PL.load_layout()
print(PL._clamp_height(lay.get("dialog_height"), PL.DEFAULT_DIALOG_HEIGHT))
PY
)"
  case "$dh" in ""|*[!0-9]*) dh=1 ;; esac
  need="$("$PY3" - "$HERE" "$dh" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
print(PL.dialog_frame_need(sys.argv[2]))
PY
)"
  case "$need" in ""|*[!0-9]*) need=10 ;; esac
  # 待審 ≡N：與關態同一套 — pref+4（≥rev_floor），勿只靠當下 rev_h
  rev_target=$rev_floor
  pref=1
  _pv_dir="${EZ2_COCKPIT_STATE_DIR:-}"
  if [ -z "$_pv_dir" ] && [ -n "${EZ2_AIB_INSTANCE_HOME:-}" ]; then
    _pv_dir="$EZ2_AIB_INSTANCE_HOME/cockpit"
  fi
  if [ -n "$_pv_dir" ] && [ -f "$_pv_dir/pending-visible" ]; then
    pref="$(tr -d '[:space:]' < "$_pv_dir/pending-visible" 2>/dev/null || echo 1)"
  fi
  case "$pref" in ""|*[!0-9]*) pref=1 ;; esac
  rev_target=$((pref + 4))
  [ "$rev_target" -lt "$rev_floor" ] && rev_target=$rev_floor
  [ "$rev_h" -gt "$rev_target" ] && rev_target=$rev_h
  sizes="$("$PY3" - "$HERE" "$win_h" "$n" "$rev_target" "$need" "$rev_floor" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import panel_layout as PL
fh, hh, rh = PL.hub_io_open_pane_heights(
    sys.argv[2], sys.argv[3], review_height=int(sys.argv[4]),
    dialog_need=int(sys.argv[5]), review_floor=int(sys.argv[6]))
print("%d %d %d" % (fh, hh, rh))
PY
)"
  frame_h="$(echo "$sizes" | awk '{print $1}')"
  hub_h="$(echo "$sizes" | awk '{print $2}')"
  rev_target="$(echo "$sizes" | awk '{print $3}')"
  case "$frame_h" in ""|*[!0-9]*) frame_h=$need ;; esac
  case "$hub_h" in ""|*[!0-9]*) hub_h=$n ;; esac
  case "$rev_target" in ""|*[!0-9]*) rev_target=$rev_floor ;; esac
  [ "$hub_h" -lt 1 ] && hub_h=1
  if [ -n "$frame" ]; then
    env -u TMUX tmux resize-pane -t "$frame" -y "$frame_h" >/dev/null 2>&1 || true
  fi
  env -u TMUX tmux resize-pane -t "$hub" -y "$hub_h" >/dev/null 2>&1 || true
  [ -n "$pad_l" ] && env -u TMUX tmux resize-pane -t "$pad_l" -y "$hub_h" >/dev/null 2>&1 || true
  [ -n "$pad_r" ] && env -u TMUX tmux resize-pane -t "$pad_r" -y "$hub_h" >/dev/null 2>&1 || true
  if [ -n "$rev" ]; then
    env -u TMUX tmux resize-pane -t "$rev" -y "$rev_target" >/dev/null 2>&1 || true
  fi
}

_ensure() {
  local s="${EZ2_TMUX_SESSION:-}"
  [ -n "$s" ] || { echo "pane-hub-frame: EZ2_TMUX_SESSION unset" >&2; return 2; }
  local hub frame
  hub="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub"{print $1; exit}')"
  frame="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk '$2=="hub-frame"{print $1; exit}')"
  [ -n "$hub" ] || { echo "pane-hub-frame: no hub pane" >&2; return 2; }

  if ! _need_frame; then
    [ -n "$frame" ] && env -u TMUX tmux kill-pane -t "$frame" 2>/dev/null || true
    return 0
  fi

  if [ -z "$frame" ]; then
    local cmd
    printf -v cmd 'tmux set-option -p -t "$TMUX_PANE" @aib_role hub-frame >/dev/null; tmux set-option -p -t "$TMUX_PANE" @aib_pane hub-frame >/dev/null; exec bash %q --loop' "$0"
    env -u TMUX tmux split-window -v -b -t "$hub" -l 1 \
      -e EZ2_AIB_INSTANCE="${EZ2_AIB_INSTANCE:-}" \
      -e EZ2_AIHUB_SOCKET="${EZ2_AIHUB_SOCKET:-}" \
      -e EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-}" \
      -e EZ2_TMUX_SESSION="$s" \
      -e EZ2_COCKPIT_STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-}" \
      "$cmd"
  else
    env -u TMUX tmux set-option -p -t "$frame" @aib_role hub-frame >/dev/null 2>&1 || true
    env -u TMUX tmux set-option -p -t "$frame" @aib_pane hub-frame >/dev/null 2>&1 || true
  fi

  # hub-frame 高度由 --loop 依畫出行數自調。
  # 框已存在時禁止在此 resize hub：否則點對話捲軸/--ensure 會拉高 hub、壓扁待審。
  _ensure_hub_content_pads
  _ensure_cmd_foot
  # 艦長列改由 hub-cmd-foot 第 1 行畫，關掉 aihub status 以免重複 + 中間縫
  if [ -n "${EZ2_AIHUB_SOCKET:-}" ]; then
    env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" set-option -g status off >/dev/null 2>&1 || true
  fi
}


_repaint() {
  # 只觸發 --loop 重畫；不 resize 任何 pane（對話捲軸專用）。
  local state="${EZ2_COCKPIT_STATE_DIR:-}"
  [ -n "$state" ] || return 0
  mkdir -p "$state" 2>/dev/null || true
  date +%s > "$state/hub-frame-repaint.stamp" 2>/dev/null || true
}

case "${1:-}" in
  --paint) _paint ;;
  --loop) _loop ;;
  --pad-l-loop) _pad_l_loop ;;
  --cmd-foot-loop) _cmd_foot_loop ;;
  --protect-cmd-foot)
    foot="$(env -u TMUX tmux list-panes -t "${EZ2_TMUX_SESSION:-}" -F '#{pane_id} #{@aib_role}' 2>/dev/null | awk '$2=="hub-cmd-foot"{print $1; exit}')"
    [ -n "$foot" ] && _foot_force_h2_outer "$foot"
    ;;
  --ensure) _ensure ;;
  --apply-input-height) _apply_input_height ;;
  --print-input-height) _input_height ;;
  --repaint) _repaint ;;
  --scroll) _scroll "${2:-down}" ;;

  --hide)
    s="${EZ2_TMUX_SESSION:-}"
    frame="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
      2>/dev/null | awk '$2=="hub-frame"{print $1; exit}')"
    [ -n "$frame" ] && env -u TMUX tmux kill-pane -t "$frame" 2>/dev/null || true
    for _role in hub-pad-l hub-pad-r hub-cmd-foot; do
      _p="$(env -u TMUX tmux list-panes -t "$s" -F '#{pane_id} #{@aib_role}' \
        2>/dev/null | awk -v r="$_role" '$2==r{print $1; exit}')"
      [ -n "$_p" ] && env -u TMUX tmux kill-pane -t "$_p" 2>/dev/null || true
    done
    ;;
  *) echo "usage: pane-hub-frame.sh --paint|--loop|--pad-l-loop|--cmd-foot-loop|--ensure|--apply-input-height|--print-input-height|--repaint|--hide" >&2; exit 2 ;;
esac
