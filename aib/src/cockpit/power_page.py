#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ui power`:艦長動力室正式頁(核心免費;SPEC-power-room §9–§15)。

10-25 Andy「動力室頁要先做出來」:正式頁放核心(動力室 = 核心、免費,沒買外掛也要能用),取代 10-24 的極簡頁。
- 版型(§10):圓頂觀景窗(自轉像素地球 + 極光 + 星空)+ 三塊圓弧操控台:左 01 動力核心(6 個 AI 模組,圓圈光 = 品牌色、
  右上小燈 = 狀態)、中 02 控制台(點左邊 AI 才出現那家的模型 / 人格 / 第二大腦 + 出發 + 最近出發 + 問 AI)、
  右 03 任務板(R / SR / SSR / UR 分頁)+ 04 孵化艙 + 05 維修艙(可收合)。頂列艦時 / aib 版本 / n/6 就緒 / 省電。
- 第一次(艦員檔案沒排名)先看艦橋約 1 秒,再彈「為什麼用 AIB」(16 選、排 1–5 名,底下艦橋霧化;§13 / §15)。
- 付費內容一律不出現(§14):沒有價錢、試用、序號、商店、購買入口;任務板只列 where = power 的 30 條。
  SSR / UR 的鎖是「出發後才開」的進度鎖(§15),不是購買鎖。
- 地球是自己畫的像素地球(簡化海岸線多邊形 + 程式算光影 / 夜燈 / 雲),不用任何外部貼圖(§11)。
- 省電(§11):系統「減少動態效果」或頁上省電開關 ⇒ 地球停在一張、極光 / 呼吸 / 流星都停;視窗在背景 ⇒ 全部暫停。

安全(延續極簡頁的設計):
- 靜態快照:跑一次 power_status.status() + 出發清單 / 艦員檔案 ⇒ 寫 <狀態根>/power-room/index.html ⇒ file:// 開。
- **不起本機伺服器**:按鈕只複製 `aib …` 指令,讓真人貼到終端機跑(升級 / 登入 / 出發 / 排名 / 等級開關都一樣)。
  本機 HTTP 端點會被任何網頁打(CSRF / DNS rebinding);要按鈕直接動作時,另帶一次性權杖 + Host 檢查 + 本人證明再做。
- CSP 不准連任何網路;靜態標記的外來字一律 html.escape;給腳本的資料放 application/json 區塊(< > & 轉成跳脫碼),
  先在這裡照 power_launch 的格式再篩一次(模型 / 人格 uid / 路由代號),腳本只用 textContent 放字、不用 innerHTML。
- 複製出來的指令只由篩過格式的代碼組成,不夾任何自由文字(問 AI 的問題、「其他」那一句都讓人自己打)。
  唯一例外:問 AI 抽屜的建議題是「複製問題」(字典常數,給人貼到 aib power ask 的輸入,不是指令)。
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

NAMES = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "copilot": "Copilot", "agy": "Antigravity", "aib": "aib"}
GLYPH = {"claude": "C", "codex": "X", "grok": "G", "copilot": "P", "agy": "A", "aib": "◆"}
# 品牌色(Andy 10-24 01:5x 版型定案:動力核心圓圈光 = 品牌色;狀態改右上小燈)
BRAND_COLOR = {"claude": "#D97757", "codex": "#10A37F", "grok": "#E8E8E8", "copilot": "#8534F3", "agy": "#4285F4",
               "aib": "#7fd8ff"}
LIGHT_COLOR = {"green": "#3ddc84", "yellow": "#ffd23f", "red": "#ff4d5e", "unknown": "#7a86a8", "off": "#2a3047"}
LAUNCH_BRANDS = ("claude", "codex", "grok", "copilot", "agy")      # 控制台可選的家(power_launch.BRANDS)
TIERS = ("R", "SR", "SSR", "UR")
WHY = ("memory", "multi_ai", "coding", "automation", "ecommerce", "geo_seo", "support", "video", "erp", "warroom",
       "phone", "sites", "art", "room", "save", "other")             # power_crew.WHY(順序 = 4×4 選項順序)
WHY_MAX = 5
REASONS = ("login_missing", "login_expired", "quota_reached", "probe_failed")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,95}$")
UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ROUTE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
QUESTION_RE = re.compile(r"^q_[a-z_]{1,24}$")
TEXT_MAX = 40
JSON_ESC = {"&": "\\u0026", "<": "\\u003c", ">": "\\u003e"}


def T(key, **params):
    import i18n_lib
    return i18n_lib.t("aib.power.page." + key, params, locale=i18n_lib.resolve_locale())


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def _when(epoch):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(int(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _txt(v, limit=TEXT_MAX):
    """給腳本顯示的名稱:不是字串 ⇒ None;去控制字元 / 隱形字元(方向控制、零寬),截到 limit 字。"""
    if not isinstance(v, str):
        return None
    try:
        from power_ask import invisible
    except Exception:  # noqa: BLE001
        def invisible(_c):
            return False
    s = "".join(c for c in v if not (ord(c) < 32 or 127 <= ord(c) < 0xa0 or invisible(c))).strip()
    return s[:limit] or None


def _json_block(obj):
    """放進 <script type="application/json">:< > & 轉跳脫碼,字串裡的 </script> 關不掉區塊。"""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return "".join(JSON_ESC.get(c, c) for c in s)


def _copy_btn(cmd, label=None):
    return '<button class="cp" data-cmd="%s">%s</button><code>%s</code>' % (_e(cmd), _e(label or T("copy")), _e(cmd))


def _light(a):
    return a.get("light") if a.get("light") in LIGHT_COLOR else "unknown"


def _note(a, light):
    note = T("light_" + light)
    if a.get("reason") in REASONS:
        note = T("reason_" + a["reason"])
    if a.get("reset_at"):
        note += " · " + T("reset_at", when=_when(a["reset_at"]))
    return note


# ---------- 01 動力核心 ----------

def ai_card(a):
    light = _light(a)
    key = a.get("key") if a.get("key") in NAMES else None
    if key is None:
        return ""
    rows = ['<div class="ver"><span>%s</span><b>%s</b><span>%s</span><b>%s</b></div>'
            % (_e(T("local")), _e(a.get("local") or "—"), _e(T("latest")), _e(a.get("latest") or "—"))]
    short = T("light_" + light) + (" · " + T("reset_at", when=_when(a["reset_at"])) if a.get("reset_at") else "")
    rows.append('<div class="note">%s</div>' % _e(short))          # 模組上只放短燈號;完整原因在點開的彈窗
    acts = []
    if light in ("yellow", "red") or (light == "unknown" and a.get("login_cmd")):
        acts.append(_copy_btn("aib power login %s" % key, T("login_btn")))
    if a.get("can_update"):
        acts.append(_copy_btn("aib power upgrade %s" % key, T("upgrade_btn")))
    if acts:
        rows.append('<div class="acts">%s</div>' % "".join('<div class="act">%s</div>' % x for x in acts))
    return ('<section class="mod %s" data-key="%s" data-light="%s" tabindex="0" role="button" style="--ring:%s">'
            '<div class="orb"><b class="ring"></b><span class="glyph">%s</span><i class="lamp" style="background:%s"></i></div>'
            '<h3>%s</h3>%s</section>'
            % (_e(light), key, _e(light), BRAND_COLOR.get(key, "#7a86a8"), _e(GLYPH.get(key, "?")),
               LIGHT_COLOR[light], _e(NAMES[key]), "".join(rows)))


# ---------- 03 任務板 / 04 孵化艙 / 05 維修艙 ----------

def _task_li(t):
    done = t.get("state") in ("done", "claimed")
    tid = str(t.get("id") or "").lower()
    if not re.fullmatch(r"[a-z]{1,3}[0-9]{1,2}", tid):
        return ""
    soon = "" if t.get("detectable", True) else ' <span class="soon">%s</span>' % _e(T("task_soon"))
    return ('<li class="%s"><details><summary><i class="tick">%s</i><span class="tid">%s</span>%s%s</summary>'
            '<div class="how">%s</div></details></li>'
            % ("ok" if done else "todo", "✓" if done else "", _e(tid.upper()), _e(T("task_" + tid)), soon,
               _e(T("task_%s_how" % tid))))


def task_board(ob):
    tasks = [t for t in ob.get("tasks") or [] if isinstance(t, dict)]
    tasks = [t for t in tasks if t.get("where", "power") == "power"]       # 艦長室那幾條只在艦長室出現(SPEC §14)
    tiers = ob.get("tiers") if isinstance(ob.get("tiers"), dict) else {}
    done = sum(1 for t in tasks if t.get("state") in ("done", "claimed"))
    tabs, panes = [], []
    first = True
    for k in TIERS:
        v = tiers.get(k) if isinstance(tiers.get(k), dict) else None
        mine = [t for t in tasks if t.get("tier", "R") == k]
        if v is None and not mine:
            continue
        v = v or {"done": sum(1 for t in mine if t.get("state") in ("done", "claimed")), "total": len(mine)}
        if v.get("locked"):                                               # SSR / UR 出發後才開(SPEC §15):虛線 + 鎖、不可點、沒有開關
            tabs.append('<button class="tab locked" disabled title="%s">%s 🔒 %s</button>'
                        % (_e(T("tier_lock_hint")), _e(k), _e(T("tier_locked"))))
            continue
        on = v.get("on") is not False
        tabs.append('<button class="tab%s" data-tier="%s">%s %s/%s</button>'
                    % (" on" if first else "", _e(k), _e(k), _e(v.get("done", 0)), _e(v.get("total", 0))))
        toggle = _copy_btn("aib power tier %s %s" % (k, "off" if on else "on"), T("tier_off_btn" if on else "tier_on_btn"))
        body = "".join(_task_li(t) for t in mine) if on else '<li class="muted">%s</li>' % _e(T("tier_hidden"))
        panes.append('<div class="pane%s" data-tier="%s"><div class="tierbar"><span>%s</span><span class="act">%s</span></div>'
                     '<ol class="tasks">%s</ol></div>'
                     % (" on" if first else "", _e(k), _e(T("tier_tracking" if on else "tier_hidden_short")), toggle, body))
        first = False
    return ('<div class="tabs">%s</div><div class="panes">%s</div>'
            '<div class="grand"><span>🎁 %s</span><b>%d/%d</b></div>'
            % ("".join(tabs), "".join(panes), _e(T("grand")), done, len(tasks)))


def _egg_svg(cracked):
    """孵化艙的像素蛋(12×14 格);裂了多畫一道裂縫。"""
    rows = ("....XXXX....", "...XXXXXX...", "..XXXXXXXX..", "..XXXXXXXX..", ".XXXXXXXXXX.", ".XXXXXXXXXX.",
            "XXXXXXXXXXXX", "XXXXXXXXXXXX", "XXXXXXXXXXXX", "XXXXXXXXXXXX", ".XXXXXXXXXX.", ".XXXXXXXXXX.",
            "..XXXXXXXX..", "....XXXX....")
    spots = {(4, 3), (5, 3), (8, 6), (9, 6), (8, 7), (3, 9), (4, 9), (7, 11)}
    crack = {(5, 5), (6, 6), (5, 7), (6, 8), (7, 7)} if cracked else set()
    rects = []
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c != "X":
                continue
            fill = "#3a2a1c" if (x, y) in crack else ("#7fd8ff" if (x, y) in spots else
                                                      ("#fff6e0" if x < 6 and y < 8 else "#e8d9b8"))
            rects.append('<rect x="%d" y="%d" width="1" height="1" fill="%s"/>' % (x, y, fill))
    return '<svg class="egg" viewBox="0 0 12 14" shape-rendering="crispEdges" aria-hidden="true">%s</svg>' % "".join(rects)


def hatch_bay(ob):
    tasks = {t.get("id"): t for t in ob.get("tasks") or [] if isinstance(t, dict)}
    stage1 = (tasks.get("R12") or {}).get("state") in ("done", "claimed")
    return ('<div class="hatch">%s<div><div class="big">%s</div><div class="dim">%s</div></div></div>'
            % (_egg_svg(stage1), _e(T("egg_stage1" if stage1 else "egg_stage0")), _e(T("egg_note"))))


def repair_bay(st):
    rows = [h for h in st.get("health") or [] if isinstance(h, dict)]
    health_li = "".join('<li class="%s">%s %s%s</li>' % (
        "ok" if h.get("ok") else ("na" if h.get("ok") is None else "bad"),
        "✓" if h.get("ok") else ("·" if h.get("ok") is None else "⚠"), _e(h.get("key")),
        (" " + _e(h.get("value")) + ("G" if h.get("key") == "disk" else "")) if h.get("value") is not None else "")
        for h in rows)
    hints = "".join('<div class="hint">⚠ %s:%s</div>' % (_e(h.get("key")), _e(h.get("hint")))
                    for h in rows if h.get("hint") and h.get("ok") is False)       # 小膠囊放不下的修法另起一行
    b = st.get("binding") if isinstance(st.get("binding"), dict) else {}
    return ('<div class="bind">%s · %s</div><ul class="health">%s</ul><div class="hints">%s</div>'
            % (_e(T("binding")), _e(T("binding_line", ok=b.get("ok", 0), seats=b.get("seats", 0))), health_li, hints))


# ---------- 給腳本的資料(先篩格式) ----------

def _page_data(st, now):
    ais = [a for a in st.get("ais") or [] if isinstance(a, dict) and a.get("key") in NAMES]
    lz = st.get("launch") if isinstance(st.get("launch"), dict) else {}
    choices = lz.get("model_choices") if isinstance(lz.get("model_choices"), dict) else {}
    models = {b: [m for m in (choices.get(b) if isinstance(choices.get(b), list) else [])
                  if isinstance(m, str) and MODEL_RE.fullmatch(m)][:12] for b in LAUNCH_BRANDS}
    personas = []
    for p in (lz.get("personas") if isinstance(lz.get("personas"), list) else []):
        if isinstance(p, dict) and isinstance(p.get("uid"), str) and UID_RE.fullmatch(p["uid"]):
            personas.append({"uid": p["uid"], "name": _txt(p.get("name")) or p["uid"], "fav": p.get("fav") is True})
    brains = []
    for b in (lz.get("brains") if isinstance(lz.get("brains"), list) else []):
        if isinstance(b, dict) and isinstance(b.get("route_key"), str) and ROUTE_RE.fullmatch(b["route_key"]):
            brains.append({"route_key": b["route_key"], "title": _txt(b.get("title")) or b["route_key"]})
    uids = {p["uid"] for p in personas}
    keys = {b["route_key"] for b in brains}
    recent = []
    for r in (lz.get("recent") if isinstance(lz.get("recent"), list) else []):
        if not isinstance(r, dict) or r.get("brand") not in LAUNCH_BRANDS:
            continue
        m = r.get("model") if isinstance(r.get("model"), str) and MODEL_RE.fullmatch(r["model"]) else None
        recent.append({"brand": r["brand"], "model": m, "persona": r.get("persona") if r.get("persona") in uids else None,
                       "brain": r.get("brain") if r.get("brain") in keys else None,
                       "when": _when(r["at"]) if isinstance(r.get("at"), int) and not isinstance(r.get("at"), bool) else ""})
        if len(recent) >= 20:
            break
    crew = st.get("crew") if isinstance(st.get("crew"), dict) else {}
    rank = [k for k in (crew.get("why_rank") if isinstance(crew.get("why_rank"), list) else []) if k in WHY][:WHY_MAX]
    return {
        "now": int(now), "ais": [{"key": a["key"], "light": _light(a), "name": NAMES[a["key"]],
                                  "note": _note(a, _light(a)), "can_update": bool(a.get("can_update"))} for a in ais],
        "models": models, "personas": personas[:200], "brains": brains[:200], "recent": recent,
        "why_rank": rank, "why_names": {k: T("why_" + k) for k in WHY},
        "L": {k: T(k) for k in ("copy", "copied", "pick_ai", "model_default", "persona_none", "brain_none",
                                "step_open", "step_bind", "seat_placeholder_note", "login_title",
                                "login_note", "quota_note", "off_title", "off_note", "aib_note",
                                "ask_need_login", "ask_cmd_note", "why_cmd_note", "why_other_note", "why_empty",
                                "recent_none", "up", "down", "remove", "light_off", "power_save_on", "power_save_off")},
    }


def _ready(ais):
    rows = [a for a in ais if isinstance(a, dict) and a.get("key") in NAMES]
    return sum(1 for a in rows if a.get("light") == "green"), len(rows)


def render(st, now=None):
    now = time.time() if now is None else now
    st = st if isinstance(st, dict) else {}
    ais_rows = st.get("ais") if isinstance(st.get("ais"), list) else []
    ais = "".join(ai_card(a) for a in ais_rows if isinstance(a, dict))
    ob = st.get("onboarding") if isinstance(st.get("onboarding"), dict) else {}
    ready, total = _ready(ais_rows)
    issues = sum(1 for h in st.get("health") or [] if isinstance(h, dict) and h.get("ok") is False)
    ver = st.get("version") if isinstance(st.get("version"), str) else None
    if ver is None:
        ver = next((a.get("local") for a in ais_rows if isinstance(a, dict) and a.get("key") == "aib"), None)
    ver = _txt(ver, 24) or "—"
    suggest = [q for q in st.get("suggest") or [] if isinstance(q, str) and QUESTION_RE.fullmatch(q)][:3]
    suggest = suggest or ["q_first_seat", "q_lights", "q_handoff"]
    why_grid = "".join('<button class="why" data-why="%s"><i class="rk"></i><span>%s</span></button>'
                       % (k, _e(T("why_" + k))) for k in WHY)
    q_li = "".join('<li><span>%s</span><button class="cp" data-cmd="%s">%s</button></li>'
                   % (_e(T(q)), _e(T(q)), _e(T("copy_q"))) for q in suggest)
    return PAGE % {
        "title": _e(T("title")), "snapshot": _e(T("snapshot", when=_when(now))), "ver": _e(ver),
        "ready": _e(T("hud_ready", n=ready, m=total)),
        "sys": _e(T("hud_sys_ok") if not issues else T("hud_sys_warn", n=issues)),
        "sys_cls": "ok" if not issues else "warn", "hud_time": _e(T("hud_time")), "hud_ver": _e(T("hud_ver")),
        "hud_sys": _e(T("hud_sys")), "power_save": _e(T("power_save_off")), "crew_btn": _e(T("crew_btn")),
        "core": _e(T("core")), "core_sub": _e(T("core_sub")), "ais": ais,
        "console": _e(T("console")), "pick_ai": _e(T("pick_ai")), "model": _e(T("model")), "persona": _e(T("persona")),
        "brain": _e(T("brain")), "go": _e(T("go")), "go_sub": _e(T("go_sub")), "adv": _e(T("adv")),
        "prev": _e(T("prev")), "next": _e(T("next")), "ask_btn": _e(T("ask_btn")), "recent": _e(T("recent")),
        "tasks_h": _e(T("tasks_board")), "board": task_board(ob), "hatch_h": _e(T("hatch")), "hatch": hatch_bay(ob),
        "repair_h": _e(T("repair")), "repair": repair_bay(st), "refresh": _e(T("refresh")),
        "why_title": _e(T("why_title")), "why_sub": _e(T("why_sub")), "why_grid": why_grid,
        "why_ranked": _e(T("why_ranked")), "why_skip": _e(T("why_skip")), "why_ok": _e(T("why_ok")),
        "ask_title": _e(T("ask_title")), "ask_rules": _e(T("ask_rules")), "ask_suggest": _e(T("ask_suggest")), "q_li": q_li,
        "close": _e(T("close")), "launch_title": _e(T("launch_title")), "launch_note": _e(T("launch_note")),
        "data": _json_block(_page_data(st, now)),
    }


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
:root{--bg:#04060f;--panel:#0e1529;--line:#22305a;--line2:#33467d;--fg:#e8eeff;--dim:#93a0c4;--acc:#5ec8ff;
--gold:#ffcf4a;--silver:#cfd8e6;--bronze:#d08a52;--cyan:#5ee6e0;--ok:#3ddc84;--warn:#ffd23f;--bad:#ff4d5e;
--mono:"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}html,body{margin:0}
body{background:radial-gradient(ellipse 120%% 70%% at 50%% 0,#0f1a3a 0,#070b1c 45%%,var(--bg) 80%%);color:var(--fg);
font:18px/1.5 -apple-system,"PingFang TC","Noto Sans TC","Hiragino Sans","Apple SD Gothic Neo",sans-serif;min-height:100vh;overflow-x:hidden}
button{font:inherit;color:inherit}
.hud{display:flex;align-items:center;gap:22px;padding:10px 24px;border-bottom:1px solid var(--line);background:rgba(6,10,24,.85);
position:relative;z-index:3;flex-wrap:wrap}
.hud h1{margin:0;font-size:22px;letter-spacing:.18em;font-weight:700}
.hud .rd{display:flex;gap:8px;align-items:baseline;color:var(--dim)}.hud .rd b{font-family:var(--mono);color:var(--fg);font-weight:600}
.hud .ok b{color:var(--ok)}.hud .warn b{color:var(--warn)}.hud .sp{flex:1}
.chip{border:1px solid var(--line2);background:#0d1630;border-radius:999px;padding:4px 14px;cursor:pointer;font-size:18px}
.chip:hover{border-color:var(--acc)}
.dome{position:relative;height:330px;margin:0 auto;max-width:1500px;overflow:hidden;border-radius:50%% 50%% 0 0/100%% 100%% 0 0;
border:2px solid var(--line2);border-bottom:0;background:linear-gradient(#01030a,#050b1f 70%%,#0a1430)}
.dome canvas#stars{position:absolute;inset:0;width:100%%;height:100%%}
.aurora{position:absolute;left:-10%%;right:-10%%;height:130px;filter:blur(18px);opacity:.55;mix-blend-mode:screen;
animation:drift 22s ease-in-out infinite alternate}
.aurora.a1{top:30px;background:linear-gradient(90deg,transparent,#20ffa8 20%%,#3be0ff 45%%,transparent 60%%,#7a5cff 80%%,transparent)}
.aurora.a2{top:70px;opacity:.35;background:linear-gradient(90deg,transparent 10%%,#7a5cff 30%%,transparent 50%%,#20ffa8 75%%,transparent);
animation-duration:31s;animation-direction:alternate-reverse}
@keyframes drift{from{transform:translateX(-6%%) skewX(-8deg)}to{transform:translateX(6%%) skewX(8deg)}}
.planet{position:absolute;left:50%%;bottom:-150px;width:400px;height:400px;transform:translateX(-50%%)}
.planet .glow{position:absolute;inset:3%%;border-radius:50%%;box-shadow:0 0 50px 14px rgba(90,170,255,.38)}
.planet canvas{position:absolute;inset:0;width:100%%;height:100%%;image-rendering:pixelated}
.strut{position:absolute;bottom:0;width:3px;height:120%%;background:linear-gradient(transparent,var(--line2));transform-origin:bottom}
.meteor{position:absolute;top:40px;left:70%%;width:120px;height:2px;background:linear-gradient(90deg,#fff,transparent);opacity:0;
transform:rotate(-24deg);animation:meteor 9s linear infinite 3s}
@keyframes meteor{0%%{opacity:0;transform:translate(0,0) rotate(-24deg)}3%%{opacity:.9}9%%{opacity:0;transform:translate(-320px,140px) rotate(-24deg)}100%%{opacity:0}}
.deck{max-width:1500px;margin:-18px auto 0;padding:0 20px 20px;display:grid;grid-template-columns:1fr 1.08fr 1fr;gap:26px;
position:relative;z-index:2;perspective:1600px;align-items:start}
.bay{background:linear-gradient(180deg,#111a33,var(--panel));border:1px solid var(--line2);border-radius:26px 26px 14px 14px;
padding:16px 18px;box-shadow:0 -6px 30px rgba(40,90,200,.15),inset 0 1px 0 rgba(255,255,255,.06);min-width:0}
.deck>.bay:first-child{transform:rotateY(9deg) translateY(26px);transform-origin:right}
.deck>.right{transform:rotateY(-9deg) translateY(26px);transform-origin:left;display:flex;flex-direction:column;gap:14px;min-width:0}
.bay h2{margin:0 0 4px;font-size:18px;letter-spacing:.16em;color:var(--acc);display:flex;align-items:center;gap:10px}
.bay h2 .no{font-family:var(--mono);color:var(--dim);font-size:16px}
.bay h2 .fold{margin-left:auto;cursor:pointer;background:none;border:0;color:var(--dim);font-size:18px}
.bay .sub,.dim{color:var(--dim);font-size:16px}
.bay.folded>*:not(h2){display:none}
.mods{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px}
.mod{border:1px solid var(--line);border-radius:14px;padding:10px 6px;background:#0a1020;cursor:pointer;text-align:center;outline:none}
.mod:hover,.mod:focus-visible,.mod.sel{border-color:var(--ring);box-shadow:0 0 0 1px var(--ring) inset}
.mod.off{opacity:.45}.mod h3{margin:4px 0 2px;font-size:18px}
.orb{position:relative;width:56px;height:56px;margin:0 auto;flex:none}
.orb .ring{position:absolute;inset:0;border-radius:50%%;border:4px solid var(--ring);box-shadow:0 0 16px var(--ring),inset 0 0 12px var(--ring);
animation:breathe 3.2s ease-in-out infinite}
.mod.off .orb .ring{box-shadow:none;animation:none;border-color:#39425f}
.orb .glyph{position:absolute;inset:0;display:grid;place-items:center;font:700 24px var(--mono);color:var(--ring)}
.orb .lamp{position:absolute;right:-2px;top:-2px;width:14px;height:14px;border-radius:50%%;border:2px solid #0a1020;animation:pulse 1.6s ease-in-out infinite}
.mod.green .lamp,.mod.off .lamp,.mod.unknown .lamp{animation:none}
@keyframes breathe{50%%{box-shadow:0 0 4px var(--ring),inset 0 0 4px var(--ring)}}@keyframes pulse{50%%{opacity:.35}}
.ver{display:grid;grid-template-columns:auto auto;justify-content:center;gap:0 6px;font-size:15px;color:var(--dim);text-align:left;margin-top:4px}
.ver b{font:500 14px var(--mono);color:var(--fg);white-space:nowrap}.note{font-size:15px;color:var(--dim);margin-top:4px;line-height:1.35}.mod.green .note{display:none}
.acts{margin-top:6px}.act{display:flex;gap:6px;align-items:center;justify-content:center;flex-wrap:wrap;margin-top:4px}
.acts code,.tierbar code{display:none}
code{font:15px var(--mono);color:var(--fg);background:#141c36;padding:2px 6px;border-radius:5px;overflow-wrap:anywhere}
.cp{font-size:16px;color:#04101c;background:var(--acc);border:0;border-radius:6px;padding:3px 12px;cursor:pointer;white-space:nowrap}
.cp:hover{filter:brightness(1.15)}
.sel-ai{display:flex;align-items:center;gap:14px;margin:12px 0;min-height:64px}
.sel-ai .orb{margin:0}.sel-ai .nm{font-size:22px;font-weight:700}
.field{display:grid;grid-template-columns:6em minmax(0,1fr);align-items:center;gap:10px;margin:8px 0}
.field .lb{color:var(--dim)}
.chips{display:flex;gap:6px;flex-wrap:wrap;min-width:0}
.chip{border:1px solid var(--line2);background:#0a1124;border-radius:999px;padding:3px 14px;cursor:pointer;font:500 16px var(--mono)}
.chip.on{background:#18306a;border-color:var(--acc);color:var(--fg)}
.step{display:flex;align-items:center;gap:6px;min-width:0;border:1px solid var(--line2);background:#0a1124;border-radius:10px;padding:2px}
.step .sv{flex:1;text-align:center;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sb{background:none;border:0;color:var(--acc);cursor:pointer;padding:4px 10px;font-size:16px}.sb:disabled{color:#39425f;cursor:default}
.adv{color:var(--dim);font:15px var(--mono);margin:4px 0 0 calc(6em + 10px)}
.bay h2 .cnt{margin-left:auto;font:600 16px var(--mono);color:var(--ok);letter-spacing:0}
.go{display:block;width:100%%;margin:16px 0 10px;padding:14px;border-radius:999px;border:0;cursor:pointer;font-size:22px;font-weight:800;
letter-spacing:.4em;color:#04101c;line-height:1.2;background:linear-gradient(90deg,#5ee6e0,#5ec8ff,#8f7bff);box-shadow:0 0 24px rgba(94,200,255,.55);
animation:goglow 2.4s ease-in-out infinite}
.go small{display:block;font-size:14px;font-weight:600;letter-spacing:.06em;opacity:.75;margin-top:2px}
.go:disabled{filter:grayscale(1) brightness(.6);animation:none;cursor:not-allowed;box-shadow:none}
@keyframes goglow{50%%{box-shadow:0 0 6px rgba(94,200,255,.3)}}
.row2{display:flex;gap:10px;align-items:center}
.recent{list-style:none;margin:8px 0 0;padding:0;max-height:200px;overflow:auto}
.recent li{display:flex;gap:8px;align-items:center;padding:6px 8px;border-radius:8px;cursor:pointer;font-size:16px}
.recent li:hover{background:#13204a}.recent li.dim{cursor:default}.recent .dot{width:10px;height:10px;border-radius:50%%;flex:none}
.recent .when{margin-left:auto;font-family:var(--mono);color:var(--dim)}
.tabs{display:flex;gap:5px;margin:10px 0 8px;flex-wrap:wrap}
.tab{border:1px solid var(--line2);background:#0a1124;border-radius:8px;padding:3px 8px;cursor:pointer;font:600 15px var(--mono)}
.tab.on{background:#18306a;border-color:var(--acc)}.tab.locked{border-style:dashed;color:var(--dim);cursor:not-allowed;background:none}
.pane{display:none}.pane.on{display:block}.tierbar{display:flex;justify-content:space-between;align-items:center;color:var(--dim);font-size:16px}
.tasks{list-style:none;margin:8px 0 0;padding:0 4px 0 0;max-height:260px;overflow:auto;
-webkit-mask-image:linear-gradient(#000 85%%,transparent);mask-image:linear-gradient(#000 85%%,transparent)}
#tasks.grow .tasks{max-height:460px}
.tasks li{padding:4px 0;border-bottom:1px solid rgba(51,70,125,.35)}.tasks li.muted{color:var(--dim)}
.tasks summary{cursor:pointer;list-style:none;display:flex;gap:8px;align-items:center}.tasks summary::-webkit-details-marker{display:none}
.tick{width:20px;height:20px;border-radius:5px;border:2px solid var(--line2);display:grid;place-items:center;font-style:normal;font-size:14px;flex:none}
.ok .tick{background:var(--ok);border-color:var(--ok);color:#04101c}.ok>details>summary{color:var(--dim)}
.tid{font:14px var(--mono);color:var(--dim);min-width:3.2em}.how{color:var(--dim);font-size:16px;padding:2px 0 4px 32px}
.soon{font-size:14px;color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-left:4px}
.grand{display:flex;justify-content:space-between;margin-top:10px;padding-top:8px;border-top:1px dashed var(--line2)}
.grand b{font-family:var(--mono)}
.hatch{display:flex;gap:16px;align-items:center;margin-top:8px}.egg{width:60px;height:70px;flex:none;animation:wobble 3s ease-in-out infinite}
@keyframes wobble{0%%,80%%,100%%{transform:rotate(0)}85%%{transform:rotate(-6deg)}90%%{transform:rotate(6deg)}95%%{transform:rotate(-3deg)}}
.bind{margin:8px 0 6px;font-size:16px}.health{list-style:none;margin:0;padding:0;font-size:15px;display:flex;flex-wrap:wrap;gap:6px}
.health li{border:1px solid var(--line2);border-radius:999px;padding:1px 10px;background:#0a1124}
.health li.bad{color:#ffb3ba;border-color:#7a3340}.health li.na{color:var(--dim)}.hints{margin-top:6px}.hint{color:var(--dim);font-size:15px}
.foot{max-width:1500px;margin:8px auto 24px;padding:0 20px;color:var(--dim);font-size:16px;display:flex;gap:16px;flex-wrap:wrap}
.veil{position:fixed;inset:0;background:rgba(3,6,16,.55);backdrop-filter:blur(9px) saturate(.6);-webkit-backdrop-filter:blur(9px) saturate(.6);
display:none;z-index:10;align-items:center;justify-content:center;padding:16px}
.veil.on{display:flex}
.dlg{background:linear-gradient(180deg,#121c3a,#0b1226);border:1px solid var(--line2);border-radius:18px;padding:22px 24px;max-width:920px;width:100%%;
box-shadow:0 20px 60px rgba(0,0,0,.6);max-height:calc(100vh - 32px);overflow:auto}
.dlg h2,.drawer h2{margin:0 0 6px;font-size:24px;letter-spacing:.08em}
.x{float:right;background:none;border:0;font-size:24px;cursor:pointer;color:var(--dim)}
.cmds{display:flex;flex-direction:column;gap:10px;margin:14px 0}.cmds .act{justify-content:flex-start}
.whyw{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:20px;margin-top:14px}
.whyg{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.why{position:relative;border:1px solid var(--line2);background:#0a1226;border-radius:12px;padding:16px 8px;cursor:pointer;text-align:center;font-size:18px}
.why:hover{border-color:var(--acc)}.why.on{background:#14275a;border-color:var(--acc)}
.why .rk{position:absolute;top:-10px;left:-10px;width:28px;height:28px;border-radius:50%%;display:none;place-items:center;
font:800 16px var(--mono);font-style:normal;color:#04101c}
.why.on .rk{display:grid}.r1{background:var(--gold)}.r2{background:var(--silver)}.r3{background:var(--bronze)}.r4,.r5{background:var(--cyan)}
.ranked{list-style:none;margin:0;padding:0}.ranked li{display:flex;gap:6px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.ranked li .rk{width:26px;height:26px;border-radius:50%%;display:grid;place-items:center;font:800 15px var(--mono);font-style:normal;color:#04101c;flex:none}
.ranked li span{flex:1}.ranked button{background:#0a1226;border:1px solid var(--line2);border-radius:6px;cursor:pointer;padding:0 8px}
.btns{display:flex;gap:12px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}
.btn{border:1px solid var(--line2);background:#0a1226;border-radius:999px;padding:8px 22px;cursor:pointer}
.btn.pri{background:var(--acc);color:#04101c;border:0;font-weight:700}.btn:disabled{opacity:.4;cursor:not-allowed}
.drawer{position:fixed;top:0;bottom:0;left:0;width:min(440px,92vw);background:#0b1226;border-right:1px solid var(--line2);z-index:11;
transform:translateX(-102%%);transition:transform .25s;padding:22px;overflow:auto;box-shadow:10px 0 40px rgba(0,0,0,.5)}
.drawer.on{transform:none}.drawer ul{list-style:none;padding:0}
.drawer li{display:flex;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}.drawer li span{flex:1}
body.still *,body.still *::before,body.still *::after{animation:none!important;transition:none!important}
body.still .aurora{filter:blur(24px);opacity:.3}body.still .meteor{display:none}
body.paused *{animation-play-state:paused!important}
@media (max-height:860px) and (min-width:1181px){.dome{height:240px}.planet{width:340px;height:340px;bottom:-160px}}
@media (max-width:1180px){.deck{grid-template-columns:minmax(0,1fr);margin-top:0}.deck>.bay:first-child,.deck>.right{transform:none}
.dome{height:200px;border-radius:24px 24px 0 0}.planet{width:300px;height:300px;bottom:-140px}
.whyw{grid-template-columns:minmax(0,1fr)}.mods{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.whyg{grid-template-columns:repeat(2,minmax(0,1fr))}.mods{grid-template-columns:repeat(2,minmax(0,1fr))}
.hud{gap:10px;padding:10px 16px}.deck{padding:0 16px 16px}.field{grid-template-columns:minmax(0,1fr)}.adv{margin-left:0}}
</style></head><body>
<header class="hud" id="hud"><h1>%(title)s</h1>
<span class="rd">%(hud_time)s <b id="clock">--:--:--</b></span><span class="rd">%(hud_ver)s <b>%(ver)s</b></span>
<span class="rd %(sys_cls)s">%(hud_sys)s <b>%(sys)s</b></span><span class="rd ok"><b>%(ready)s</b></span>
<span class="sp"></span><button class="chip" id="crew">%(crew_btn)s</button><button class="chip" id="save">%(power_save)s</button></header>
<div class="dome" id="dome"><canvas id="stars"></canvas><div class="aurora a1"></div><div class="aurora a2"></div><div class="meteor"></div>
<div class="planet"><div class="glow"></div><canvas id="earth" width="128" height="128"></canvas></div>
<i class="strut" style="left:18%%;transform:rotate(38deg)"></i><i class="strut" style="left:36%%;transform:rotate(16deg)"></i>
<i class="strut" style="right:36%%;transform:rotate(-16deg)"></i><i class="strut" style="right:18%%;transform:rotate(-38deg)"></i></div>
<main class="deck">
<section class="bay" id="core"><h2><span class="no">01</span>%(core)s<span class="cnt">%(ready)s</span></h2><div class="sub">%(core_sub)s</div><div class="mods">%(ais)s</div></section>
<section class="bay" id="console"><h2><span class="no">02</span>%(console)s</h2>
<div class="sel-ai" id="selai"><span class="dim">%(pick_ai)s</span></div>
<div class="field"><span class="lb">%(model)s</span><div class="chips" id="model" role="group" aria-label="%(model)s"></div></div>
<div class="field"><span class="lb">%(persona)s</span><div class="step" id="persona"><button class="sb" data-d="-1" aria-label="%(prev)s">◀</button><span class="sv"></span><button class="sb" data-d="1" aria-label="%(next)s">▶</button></div></div>
<div class="field"><span class="lb">%(brain)s</span><div class="step" id="brain"><button class="sb" data-d="-1" aria-label="%(prev)s">◀</button><span class="sv"></span><button class="sb" data-d="1" aria-label="%(next)s">▶</button></div></div>
<div class="adv">%(adv)s</div>
<button class="go" id="go" disabled>%(go)s<small>%(go_sub)s</small></button>
<div class="row2"><button class="chip" id="askbtn">%(ask_btn)s</button><span class="dim">%(recent)s</span></div>
<ul class="recent" id="recent"></ul></section>
<div class="right">
<section class="bay" id="tasks"><h2><span class="no">03</span>%(tasks_h)s</h2>%(board)s</section>
<section class="bay fold-me" id="hatch"><h2><span class="no">04</span>%(hatch_h)s<button class="fold">▾</button></h2>%(hatch)s</section>
<section class="bay fold-me" id="repair"><h2><span class="no">05</span>%(repair_h)s<button class="fold">▾</button></h2>%(repair)s</section>
</div></main>
<div class="foot"><span>%(snapshot)s</span><span>%(refresh)s</span></div>
<div class="veil" id="why"><div class="dlg"><h2>%(why_title)s</h2><div class="dim">%(why_sub)s</div>
<div class="whyw"><div class="whyg">%(why_grid)s</div><div><div class="dim">%(why_ranked)s</div><ol class="ranked" id="ranked"></ol>
<div class="cmds" id="whycmd"></div></div></div>
<div class="btns"><button class="btn" id="whyskip">%(why_skip)s</button><button class="btn pri" id="whyok" disabled>%(why_ok)s</button></div></div></div>
<div class="veil" id="modal"><div class="dlg" style="max-width:640px"><button class="x" data-close title="%(close)s">×</button><h2 id="mt"></h2>
<div class="dim" id="mn"></div><div class="cmds" id="mc"></div><div class="dim" id="mf"></div></div></div>
<div class="veil" id="launch"><div class="dlg" style="max-width:720px"><button class="x" data-close title="%(close)s">×</button><h2>%(launch_title)s</h2>
<div class="dim">%(launch_note)s</div><div class="cmds" id="lc"></div><div class="dim" id="lf"></div></div></div>
<aside class="drawer" id="ask"><button class="x" data-close title="%(close)s">×</button>
<h2>%(ask_title)s <span id="askai"></span></h2><div class="dim">%(ask_rules)s</div><div class="cmds" id="askcmd"></div>
<div class="dim">%(ask_suggest)s</div><ul>%(q_li)s</ul></aside>
<script type="application/json" id="data">%(data)s</script>
<script>
(function(){
'use strict';
var D=JSON.parse(document.getElementById('data').textContent),L=D.L;
var $=function(id){return document.getElementById(id)};
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function store(k,v){try{if(v===undefined)return localStorage.getItem('aib.power.'+k);localStorage.setItem('aib.power.'+k,v)}catch(_){return null}}
function copyRow(cmd,label){var d=el('div','act'),b=el('button','cp',label||L.copy);b.setAttribute('data-cmd',cmd);d.appendChild(b);
 d.appendChild(el('code',null,cmd));return d}
document.addEventListener('click',function(ev){var b=ev.target.closest('[data-cmd]');if(!b)return;ev.stopPropagation();
 var c=b.getAttribute('data-cmd'),t=b.textContent,ok=function(){b.textContent=L.copied;setTimeout(function(){b.textContent=t},1200)};
 if(navigator.clipboard)navigator.clipboard.writeText(c).then(ok,function(){})},true);
function tick(){var d=new Date(),p=function(n){return(n<10?'0':'')+n};$('clock').textContent=p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())}
tick();setInterval(function(){if(!document.hidden)tick()},1000);
var rm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches,saved=store('save');
var still=saved===null?!!rm:saved==='1';
document.addEventListener('visibilitychange',function(){document.body.classList.toggle('paused',document.hidden)});
function stars(){var c=$('stars'),r=c.getBoundingClientRect(),x=c.getContext('2d');c.width=Math.max(1,r.width|0);c.height=Math.max(1,r.height|0);
 var s=7,n=function(){s=(s*16807)%%2147483647;return s/2147483647};
 for(var i=0;i<260;i++){var a=n(),b=n(),m=n();x.fillStyle='rgba(255,255,255,'+(0.25+m*0.75).toFixed(2)+')';
  x.fillRect(Math.floor(a*c.width),Math.floor(b*c.height),m>0.92?2:1,m>0.92?2:1)}}
stars();window.addEventListener('resize',stars);
/* 像素地球:簡化海岸線(經度, 緯度)多邊形 ⇒ 256×128 地圖;光影 / 夜燈 / 雲由程式算,不用外部貼圖 */
var LAND=[[-166,68,-156,71,-140,70,-128,70,-115,68,-95,72,-82,73,-80,63,-90,57,-82,52,-78,58,-65,60,-60,55,-56,52,-66,45,-70,42,-76,38,-75,35,-81,31,-80,25,-82,27,-84,30,-90,29,-97,27,-97,22,-94,18,-88,21,-87,16,-83,10,-79,9,-80,7,-86,12,-92,14,-105,20,-110,24,-112,29,-115,30,-117,32,-121,35,-124,40,-124,47,-130,54,-138,59,-148,60,-152,58,-158,56,-165,54,-160,58,-165,61,-166,64],
[-73,78,-60,82,-30,83,-20,80,-20,72,-30,68,-42,60,-50,64,-55,70,-66,76],
[-80,9,-75,11,-72,12,-62,11,-52,5,-50,0,-44,-2,-35,-5,-35,-9,-39,-15,-40,-22,-48,-26,-53,-34,-58,-38,-62,-40,-65,-45,-68,-50,-70,-55,-74,-52,-75,-45,-73,-37,-71,-30,-70,-18,-76,-14,-81,-6,-80,-1,-78,2,-77,7],
[-10,36,-9,43,-2,44,-5,48,2,51,8,54,10,57,5,59,5,62,14,67,20,70,30,71,40,67,44,68,55,69,68,73,80,73,100,77,112,74,130,71,140,72,160,70,170,70,180,68,180,65,170,60,163,58,160,54,156,51,162,57,150,60,140,55,141,48,135,43,130,42,128,35,126,38,122,40,122,37,119,35,121,31,122,29,118,24,110,21,108,16,109,12,105,9,103,10,100,13,100,7,103,2,101,3,98,8,98,16,94,17,92,22,87,21,80,15,78,8,73,16,72,21,67,25,62,25,57,26,56,24,59,22,55,17,52,16,45,13,43,13,39,21,35,28,33,30,35,33,36,36,30,36,27,37,26,40,23,40,24,38,22,37,20,40,19,42,14,45,13,43,16,40,16,38,12,38,10,44,6,43,3,43,0,39,-2,37,-6,36],
[-17,21,-17,15,-15,11,-12,7,-8,4,-2,5,4,6,9,4,10,-1,13,-6,12,-15,15,-27,18,-34,20,-35,26,-34,32,-29,35,-22,35,-17,40,-15,40,-10,39,-5,43,0,48,4,51,11,44,11,43,13,39,16,36,22,34,28,32,31,25,32,20,31,15,32,11,33,10,37,3,37,-2,35,-6,36,-10,30,-13,27],
[114,-22,114,-26,115,-34,118,-35,123,-34,129,-32,132,-32,135,-35,138,-35,140,-38,144,-38,147,-39,150,-37,153,-32,153,-25,146,-19,142,-11,141,-17,136,-12,130,-12,126,-14,122,-18],
[44,-25,47,-25,50,-15,49,-12,44,-17],[130,31,134,34,140,36,142,40,141,45,144,44,140,41,136,36,132,34],
[-5,50,1,51,2,53,-2,56,-2,58,-5,58,-6,55,-3,54,-5,52],[109,1,111,-3,116,-4,119,1,117,7,113,3],[95,5,98,4,104,-2,106,-6,101,-3],
[131,-1,138,-2,147,-6,150,-10,143,-9,138,-8,132,-4],[120,22,120.6,25.3,122,25,121,21.9],[172,-34,178,-38,175,-41,171,-44,167,-46,170,-42],
[-24,64,-14,65,-14,66.5,-22,66.5],[120,18,122,18,126,7,122,7,120,14],[80,6,82,7,81,9.8,79.8,9]];
var WATER=[[-95,59,-88,56,-80,55,-78,62,-87,64,-94,61],[47,37,54,37,54,45,47,46],[28,41,41,41,41,46,30,46]];
function inPoly(p,x,y){var c=false;for(var i=0,j=p.length-2;i<p.length;j=i,i+=2){var xi=p[i],yi=p[i+1],xj=p[j],yj=p[j+1];
 if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)c=!c}return c}
var MW=256,MH=128,map=new Uint8Array(MW*MH),cloud=new Float32Array(MW*MH),seed=12345;
function rnd(){seed=(seed*16807)%%2147483647;return seed/2147483647}
var G=[];for(var gi=0;gi<33*17;gi++)G.push(rnd());
function vn(u,v){var x=u*32,y=v*16,x0=Math.floor(x),y0=Math.floor(y),fx=x-x0,fy=y-y0,g=function(a,b){return G[(b%%17)*33+(a%%32)]};
 fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy);return(g(x0,y0)*(1-fx)+g(x0+1,y0)*fx)*(1-fy)+(g(x0,y0+1)*(1-fx)+g(x0+1,y0+1)*fx)*fy}
for(var my0=0;my0<MH;my0++)for(var mx0=0;mx0<MW;mx0++){var lon0=mx0/MW*360-180+0.7,lat0=90-(my0+0.5)/MH*180,v=0,k;
 if(lat0<-64)v=2;else{for(k=0;k<LAND.length;k++)if(inPoly(LAND[k],lon0,lat0)){v=1;break}
  if(v)for(k=0;k<WATER.length;k++)if(inPoly(WATER[k],lon0,lat0)){v=0;break}}
 if(v===1&&Math.abs(lat0)>70)v=2;map[my0*MW+mx0]=v;
 cloud[my0*MW+mx0]=vn(mx0/MW,my0/MH)*0.65+vn((mx0*2.3%%MW)/MW,(my0*2.3%%MH)/MH)*0.35}
var EC=$('earth'),EX=EC.getContext('2d'),N=EC.width,R=N/2-2,img=EX.createImageData(N,N),rot=2.2,cl=0,tilt=0.41;
var SX=-0.62,SY=-0.28,SZ=0.73;
function landRGB(lat,x,y){var a=Math.abs(lat),n=cloud[y*MW+x]*30-15;
 if(a<12)return[44+n,120+n,52];if(a<32)return[176+n,150+n,96];if(a<55)return[70+n,128+n,64];return[90+n,110+n,80]}
function drawEarth(){var d=img.data,ct=Math.cos(tilt),stt=Math.sin(tilt),c=N/2;
 for(var py=0;py<N;py++)for(var px=0;px<N;px++){var o=(py*N+px)*4,nx=(px+0.5-c)/R,ny=(py+0.5-c)/R,q=nx*nx+ny*ny;
  if(q>1){d[o+3]=0;continue}var nz=Math.sqrt(1-q),yy=-ny*ct+nz*stt,zz=ny*stt+nz*ct,lat=Math.asin(Math.max(-1,Math.min(1,yy)))*57.2958,
  lon=Math.atan2(nx,zz)+rot,u=((lon/6.28318)%%1+1)%%1,mx=Math.floor(u*MW)%%MW,my=Math.min(MH-1,Math.max(0,Math.floor((90-lat)/180*MH))),
  m=map[my*MW+mx],l=nx*SX+ny*SY+nz*SZ,day=Math.max(0,Math.min(1,(l+0.12)*3)),r,g,b;
  if(m===2){r=225;g=235;b=245}else if(m===1){var t=landRGB(lat,mx,my);r=t[0];g=t[1];b=t[2]}else{r=18;g=58+(nz*30|0);b=128+(nz*40|0)}
  var sh=0.25+0.75*Math.max(0,l);r*=sh;g*=sh;b*=sh;
  var cv=cloud[my*MW+((mx+Math.floor(cl))%%MW)];if(cv>0.66){var ca=Math.min(1,(cv-0.66)*5)*0.8;r+=(240*sh-r)*ca;g+=(244*sh-g)*ca;b+=(250*sh-b)*ca}
  var nr=4,ng=8,nb=22;if(m===1&&cv<0.6&&Math.abs(lat)<62&&((mx*7+my*13)%%11===0||(mx*3+my*5)%%17===0)){nr=255;ng=196;nb=96}
  r=r*day+nr*(1-day);g=g*day+ng*(1-day);b=b*day+nb*(1-day);var rim=Math.pow(1-nz,3);
  d[o]=Math.min(255,r+rim*60);d[o+1]=Math.min(255,g+rim*110);d[o+2]=Math.min(255,b+rim*200);d[o+3]=255}
 EX.putImageData(img,0,0)}
setInterval(function(){if(still||document.hidden)return;rot+=0.0058;cl+=0.08;drawEarth()},80);
function applySave(){document.body.classList.toggle('still',still);$('save').textContent=still?L.power_save_on:L.power_save_off;drawEarth()}
$('save').addEventListener('click',function(){still=!still;store('save',still?'1':'0');applySave()});
applySave();
/* 01 ⇒ 02:點左邊 AI 才出現那家的模型 / 人格 / 第二大腦;黃 / 紅 / 灰燈走登入彈窗 */
var AI={};D.ais.forEach(function(a){AI[a.key]=a});var cur=null;
/* 模型 = 一排膠囊;人格 / 第二大腦 = ◀ 名稱 ▶(第一格是「不綁」) */
var S={model:'',persona:'',brain:''},IX={persona:0,brain:0};
var OPTS={persona:[{v:'',t:L.persona_none}].concat(D.personas.map(function(x){return{v:x.uid,t:(x.fav?'★ ':'')+x.name}})),
 brain:[{v:'',t:L.brain_none}].concat(D.brains.map(function(x){return{v:x.route_key,t:x.title}}))};
function drawStep(k){var o=OPTS[k][IX[k]];$(k).querySelector('.sv').textContent=cur?o.t:'—';S[k]=cur?o.v:'';
 $(k).querySelectorAll('.sb').forEach(function(b){b.disabled=!cur||OPTS[k].length<2})}
function setStep(k,v){for(var i=0;i<OPTS[k].length;i++)if(OPTS[k][i].v===v){IX[k]=i;break}drawStep(k)}
['persona','brain'].forEach(function(k){$(k).querySelectorAll('.sb').forEach(function(b){b.addEventListener('click',function(){
 var n=OPTS[k].length;IX[k]=(IX[k]+Number(b.getAttribute('data-d'))+n)%%n;drawStep(k)})})});
function setModel(v){S.model=v;$('model').querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.getAttribute('data-m')===v)})}
function fillSelects(){var m=$('model');m.textContent='';S.model='';
 if(cur){var ms=D.models[cur]||[];if(!ms.length)m.appendChild(el('span','dim',L.model_default));
  ms.forEach(function(x){var c=el('button','chip',x.indexOf(cur+'-')===0?x.slice(cur.length+1):x);c.setAttribute('data-m',x);c.title=x;
   c.addEventListener('click',function(){setModel(x)});m.appendChild(c)});if(ms.length)setModel(ms[0])}
 drawStep('persona');drawStep('brain');$('go').disabled=!cur}
function showSel(){var box=$('selai');box.textContent='';document.querySelectorAll('.mod').forEach(function(x){x.classList.toggle('sel',x.getAttribute('data-key')===cur)});
 if(!cur){box.appendChild(el('span','dim',L.pick_ai));return}
 var src=document.querySelector('.mod[data-key="'+cur+'"]');if(src){var orb=src.querySelector('.orb').cloneNode(true);orb.style.setProperty('--ring',src.style.getPropertyValue('--ring'));box.appendChild(orb)}
 var w=el('div');w.appendChild(el('div','nm',AI[cur].name));w.appendChild(el('div','dim',AI[cur].note));box.appendChild(w)}
function pick(k){cur=k;showSel();fillSelects()}
function openModal(title,note,cmds,foot){$('mt').textContent=title;$('mn').textContent=note||'';var c=$('mc');c.textContent='';
 cmds.forEach(function(x){c.appendChild(copyRow(x))});$('mf').textContent=foot||'';$('modal').classList.add('on')}
function clickMod(k){var a=AI[k];if(!a)return;
 if(k==='aib'){openModal(a.name,a.note,a.can_update?['aib power upgrade aib']:[],L.aib_note);return}
 if(a.light==='off'){openModal(a.name+' · '+L.light_off,L.off_title,[],L.off_note);return}
 if(a.light!=='green'){openModal(a.name+' · '+L.login_title,a.note,['aib power login '+k],L.login_note+(a.light==='red'?' '+L.quota_note:''));return}
 pick(k)}
document.querySelectorAll('.mod').forEach(function(m){var k=m.getAttribute('data-key');
 m.addEventListener('click',function(){clickMod(k)});m.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();clickMod(k)}})});
/* 出發:只組指令讓人自己貼(開席 ⇒ 綁人格 + 第二大腦) */
$('go').addEventListener('click',function(){if(!cur)return;var m=S.model,p=S.persona,b=S.brain;
 var a=['aib',cur,'safe','dev','new'];if(m)a.push('--model',m);var c=$('lc');c.textContent='';c.appendChild(el('div','dim',L.step_open));c.appendChild(copyRow(a.join(' ')));
 if(p||b){var s=['aib','newproj','--seat','SEAT'];if(p)s.push('--persona-uid',p);if(b)s.push('--folder',b);
  c.appendChild(el('div','dim',L.step_bind));c.appendChild(copyRow(s.join(' ')));$('lf').textContent=L.seat_placeholder_note}else $('lf').textContent='';
 $('launch').classList.add('on')});
/* 最近出發(最多 20 筆):點一下填回控制台 */
(function(){var ul=$('recent');if(!D.recent.length){ul.appendChild(el('li','dim',L.recent_none));return}
 var PN={},BN={};D.personas.forEach(function(x){PN[x.uid]=x.name});D.brains.forEach(function(x){BN[x.route_key]=x.title});
 D.recent.forEach(function(r){var li=el('li'),dot=el('i','dot'),src=document.querySelector('.mod[data-key="'+r.brand+'"]');
  dot.style.background=src?src.style.getPropertyValue('--ring'):'#7a86a8';li.appendChild(dot);
  li.appendChild(el('span',null,[(AI[r.brand]||{}).name||r.brand,r.model,PN[r.persona],BN[r.brain]].filter(Boolean).join(' · ')));
  li.appendChild(el('span','when',r.when));
  li.addEventListener('click',function(){clickMod(r.brand);if(cur!==r.brand)return;if(r.model)setModel(r.model);
   setStep('persona',r.persona||'');setStep('brain',r.brain||'')});ul.appendChild(li)})})();
var first=D.recent.length&&AI[D.recent[0].brand]&&AI[D.recent[0].brand].light==='green'?D.recent[0].brand:null;
if(!first)for(var bi=0;bi<D.ais.length;bi++)if(D.ais[bi].key!=='aib'&&D.ais[bi].light==='green'){first=D.ais[bi].key;break}
if(first){pick(first);var r0=D.recent[0];if(r0&&r0.brand===first){if(r0.model)setModel(r0.model);setStep('persona',r0.persona||'');setStep('brain',r0.brain||'')}}
else{showSel();fillSelects()}
/* 問 AI 抽屜:只回答、不動手;問題讓人自己在終端機打 */
$('askbtn').addEventListener('click',function(){var c=$('askcmd');c.textContent='';$('askai').textContent=cur?'· '+AI[cur].name:'';
 if(!cur){c.appendChild(el('div','dim',L.pick_ai))}else if(AI[cur].light!=='green'){c.appendChild(el('div','dim',L.ask_need_login));
  c.appendChild(copyRow('aib power login '+cur))}else{var m=S.model,a=['aib','power','ask',cur];if(m)a.push('--model',m);
  c.appendChild(copyRow(a.join(' ')));c.appendChild(el('div','dim',L.ask_cmd_note))}
 $('ask').classList.add('on')});
/* 任務板分頁 */
document.querySelectorAll('.tab[data-tier]').forEach(function(t){t.addEventListener('click',function(){var k=t.getAttribute('data-tier');
 document.querySelectorAll('.tab').forEach(function(x){x.classList.toggle('on',x===t)});
 document.querySelectorAll('.pane').forEach(function(p){p.classList.toggle('on',p.getAttribute('data-tier')===k)})})});
/* 04 / 05 收合(記住);收起後任務清單長高 */
function grow(){$('tasks').classList.toggle('grow',document.querySelectorAll('.fold-me.folded').length>0)}
document.querySelectorAll('.fold-me').forEach(function(s){var id=s.id,btn=s.querySelector('.fold');
 if(store('fold.'+id)==='1'){s.classList.add('folded');btn.textContent='▴'}
 btn.addEventListener('click',function(){var on=!s.classList.contains('folded');s.classList.toggle('folded',on);btn.textContent=on?'▴':'▾';store('fold.'+id,on?'1':'0');grow()})});
grow();
/* 為什麼用 AIB:點的順序 = 排名(最多 5);確定 ⇒ 給 aib power why set 指令 */
var picks=D.why_rank.slice();
function drawWhy(){document.querySelectorAll('.why').forEach(function(b){var k=b.getAttribute('data-why'),i=picks.indexOf(k),rk=b.querySelector('.rk');
  b.classList.toggle('on',i>=0);rk.className='rk'+(i>=0?' r'+(i+1):'');rk.textContent=i>=0?String(i+1):''});
 var ol=$('ranked');ol.textContent='';picks.forEach(function(k,i){var li=el('li');li.appendChild(el('i','rk r'+(i+1),String(i+1)));
  li.appendChild(el('span',null,D.why_names[k]));
  [['▲',L.up,-1],['▼',L.down,1],['×',L.remove,0]].forEach(function(x){var b=el('button',null,x[0]);b.title=x[1];b.addEventListener('click',function(){
   if(x[2]===0)picks.splice(i,1);else{var j=i+x[2];if(j<0||j>=picks.length)return;var t=picks[i];picks[i]=picks[j];picks[j]=t}drawWhy()});li.appendChild(b)});
  ol.appendChild(li)});
 if(!picks.length)ol.appendChild(el('li','dim',L.why_empty));$('whyok').disabled=!picks.length;$('whycmd').textContent=''}
document.querySelectorAll('.why').forEach(function(b){b.addEventListener('click',function(){var k=b.getAttribute('data-why'),i=picks.indexOf(k);
 if(i>=0)picks.splice(i,1);else if(picks.length<5)picks.push(k);drawWhy()})});
$('whyok').addEventListener('click',function(){var c=$('whycmd');c.textContent='';c.appendChild(el('div','dim',L.why_cmd_note));
 c.appendChild(copyRow('aib power why set '+picks.join(',')));if(picks.indexOf('other')>=0)c.appendChild(el('div','dim',L.why_other_note))});
$('whyskip').addEventListener('click',function(){$('why').classList.remove('on');try{sessionStorage.setItem('aib.power.whyskip','1')}catch(_){}});
$('crew').addEventListener('click',function(){drawWhy();$('why').classList.add('on')});
drawWhy();
var skipped=false;try{skipped=sessionStorage.getItem('aib.power.whyskip')==='1'}catch(_){}
if(!D.why_rank.length&&!skipped)setTimeout(function(){$('why').classList.add('on')},1000);
/* 關彈窗 / 抽屜 */
function closeAll(){['modal','launch','ask'].forEach(function(id){$(id).classList.remove('on')})}
document.querySelectorAll('[data-close]').forEach(function(b){b.addEventListener('click',closeAll)});
['modal','launch'].forEach(function(id){$(id).addEventListener('click',function(e){if(e.target===$(id))closeAll()})});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){closeAll();$('why').classList.remove('on')}});
})();
</script>
</body></html>
"""


def page_path(env=None):
    import aib_ui
    return aib_ui.state_root(env) / "power-room" / "index.html"


def _write_page(p, text):
    """暫存檔名帶隨機碼、O_EXCL + O_NOFOLLOW、0600 再換名:別人先放在旁邊的連結不會被跟著寫(#293 審查)。"""
    tmp = p.with_name(".%s.%d.%s.tmp" % (p.name, os.getpid(), os.urandom(6).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def extras(light=None):
    """控制台 / 艦員檔案 / 問 AI 建議題 / aib 版本;任何一塊讀不到就給空的,不拖垮整頁。"""
    out = {}
    try:
        import power_launch
        out["launch"] = power_launch.launch_list(light=light)
    except Exception:  # noqa: BLE001
        out["launch"] = {}
    try:
        import power_crew
        out["crew"] = power_crew.why()
    except Exception:  # noqa: BLE001
        out["crew"] = {}
    try:
        import power_ask
        out["suggest"] = power_ask.suggest((out["crew"] or {}).get("why_rank") or [])
    except Exception:  # noqa: BLE001
        out["suggest"] = []
    try:
        import power_tasks
        out["version"] = power_tasks._aib_version()
    except Exception:  # noqa: BLE001
        out["version"] = None
    return out


def open_power(out=None, env=None, status=None, opener=None, extra=None):
    """`aib ui power`:量一次、寫頁、開視窗。回 rc:0 開了;2 開不了(印檔案位置讓人自己開)。"""
    import aib_ui
    out = sys.stdout if out is None else out
    if status is None:
        import power_status
        status = power_status.status
    st = dict(status())
    lights = {a.get("key"): a.get("light") for a in st.get("ais") or [] if isinstance(a, dict)}
    try:
        st.update((extra or extras)(light=lambda b: lights.get(b, "unknown")))
    except Exception:  # noqa: BLE001
        pass
    p = page_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_page(p, render(st))
    how, _detail = (opener or aib_ui.open_app_window)(p.as_uri(), env)
    if how in ("app", "browser"):
        out.write(T("opened") + "\n")
        return 0
    out.write(T("open_failed", path=str(p)) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(open_power())
