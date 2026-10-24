#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ui power`:艦長動力室的極簡頁(核心免費;正式頁面 @74e8b 在艦長室外掛做)。

10-24 @abb89 / @d07f9 討論定案:頁面接上之前不改 `aib` 預設;核心先出極簡頁,隨時 `aib ui power` 可進。
- 靜態快照:跑一次 power_status.status() ⇒ 寫 <狀態根>/power-room/index.html ⇒ 用 aib_ui 的應用程式視窗開(file://)。
- **不起本機伺服器**:按鈕只複製 `aib power login / upgrade …` 指令,讓真人貼到終端機跑。本機 HTTP 端點會被任何網頁
  打(CSRF / DNS rebinding),極簡頁不值得冒這個險;正式頁要按鈕直接動作時,由外掛那端帶一次性權杖 + Host 檢查再做。
- 所有外來字(CLI 版本、hint)一律 html.escape;頁面 CSP 不准連任何網路。
"""
import html
import json
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

NAMES = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "copilot": "Copilot", "agy": "Antigravity", "aib": "aib"}
# 品牌色(Andy 10-24 01:5x 版型定案:動力核心圓圈光 = 品牌色;狀態改右上小燈)
BRAND_COLOR = {"claude": "#D97757", "codex": "#10A37F", "grok": "#E8E8E8", "copilot": "#8534F3", "agy": "#4285F4",
               "aib": "#7fd8ff"}
LIGHT_COLOR = {"green": "#3ddc84", "yellow": "#ffd23f", "red": "#ff4d5e", "unknown": "#7a86a8", "off": "#2a3047"}


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


def _copy_btn(cmd):
    return '<button class="cp" data-cmd="%s">%s</button><code>%s</code>' % (_e(cmd), _e(T("copy")), _e(cmd))


def ai_card(a):
    light = a.get("light") if a.get("light") in LIGHT_COLOR else "unknown"
    key = a.get("key")
    rows = ['<div class="ver">%s %s · %s %s</div>' % (_e(T("local")), _e(a.get("local") or "—"),
                                                        _e(T("latest")), _e(a.get("latest") or "—"))]
    note = T("light_" + light)
    if a.get("reason"):
        note = T("reason_" + a["reason"]) if a["reason"] in ("login_missing", "login_expired", "quota_reached",
                                                             "probe_failed") else note
    if a.get("reset_at"):
        note += " · " + T("reset_at", when=_when(a["reset_at"]))
    rows.append('<div class="note">%s</div>' % _e(note))
    acts = []
    if light in ("yellow", "red") or (light == "unknown" and a.get("login_cmd")):
        acts.append(_copy_btn("aib power login %s" % key))
    if a.get("can_update"):
        acts.append(_copy_btn("aib power upgrade %s" % key))
    if acts:
        rows.append('<div class="acts">%s</div>' % "".join('<div class="act">%s</div>' % x for x in acts))
    ring = BRAND_COLOR.get(key, "#7a86a8")
    return ('<section class="mod %s" style="--ring:%s"><header><b class="ring"></b><h3>%s</h3>'
            '<i class="lamp" style="background:%s"></i></header>%s</section>'
            % (_e(light), ring, _e(NAMES.get(key, key)), LIGHT_COLOR[light], "".join(rows)))


def render(st, now=None):
    now = time.time() if now is None else now
    ais = "".join(ai_card(a) for a in st.get("ais") or [] if isinstance(a, dict))
    ob = st.get("onboarding") if isinstance(st.get("onboarding"), dict) else {}
    tasks = [t for t in ob.get("tasks") or [] if isinstance(t, dict)]
    tasks = [t for t in tasks if t.get("where", "power") == "power"]       # 艦長室那幾條只在艦長室出現(SPEC §14)
    done = sum(1 for t in tasks if t.get("state") in ("done", "claimed"))
    tiers = ob.get("tiers") if isinstance(ob.get("tiers"), dict) else {}
    tier_line = " · ".join(("%s 🔒 %s" % (_e(k), _e(T("tier_locked"))) if v.get("locked") else
                            "%s %s/%s" % (_e(k), _e(v.get("done", 0)), _e(v.get("total", 0))))
                           for k, v in tiers.items() if isinstance(v, dict))     # SSR / UR 出發後才開(SPEC §15)
    # 極簡頁只列 R(新手)這一級;其他等級看數字,完整任務板在正式頁
    task_li = "".join('<li class="%s">%s %s%s</li>' % (
        "ok" if t.get("state") in ("done", "claimed") else "todo",
        "✅" if t.get("state") in ("done", "claimed") else "⬜",
        _e(T("task_" + str(t.get("id")).lower())),
        "" if t.get("detectable", True) else ' <span class="soon">%s</span>' % _e(T("task_soon")))
        for t in tasks if t.get("tier", "R") == "R" and t.get("where", "power") == "power")
    health_li = "".join('<li class="%s">%s %s%s</li>' % (
        "ok" if h.get("ok") else ("na" if h.get("ok") is None else "bad"),
        "✅" if h.get("ok") else ("·" if h.get("ok") is None else "⚠️"), _e(h.get("key")),
        (" — " + _e(h.get("value")) + (" GB" if h.get("key") == "disk" else "")) if h.get("value") is not None else "")
        + ('<div class="hint">%s</div>' % _e(h.get("hint")) if h.get("hint") and not h.get("ok") else "")
        for h in st.get("health") or [] if isinstance(h, dict))
    b = st.get("binding") if isinstance(st.get("binding"), dict) else {}
    return """<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
:root{--bg:#070a14;--panel:#0f1424;--line:#24304f;--fg:#e6ecff;--dim:#8a96b8;--acc:#5ec8ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#121a33 0,var(--bg) 60%%);color:var(--fg);
font:18px/1.5 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;padding:24px 16px}
h1{margin:0 0 4px;font-size:26px;letter-spacing:.08em}h2{font-size:18px;color:var(--acc);letter-spacing:.12em;margin:0 0 12px}
.sub{color:var(--dim);font-size:15px;margin-bottom:20px}.bay{border:1px solid var(--line);background:var(--panel);border-radius:10px;padding:16px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.mod{border:1px solid var(--line);border-radius:8px;padding:12px;background:#0b1020}.mod.off{opacity:.5}
.mod header{display:flex;align-items:center;gap:8px}.mod h3{margin:0;font-size:18px}
.mod .ring{width:18px;height:18px;border-radius:50%%;border:3px solid var(--ring);box-shadow:0 0 10px var(--ring);
animation:breathe 3s ease-in-out infinite}.mod .lamp{width:10px;height:10px;border-radius:50%%;margin-left:auto}
@keyframes breathe{50%%{box-shadow:0 0 2px var(--ring)}}@media (prefers-reduced-motion:reduce){.mod .ring{animation:none}}
.ver,.note,.hint{font-size:15px;color:var(--dim)}.act{display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap}
code{font-size:14px;color:var(--fg);background:#161d33;padding:2px 6px;border-radius:4px;word-break:break-all}
.cp{font:inherit;font-size:14px;color:var(--bg);background:var(--acc);border:0;border-radius:4px;padding:2px 10px;cursor:pointer}
ul{list-style:none;margin:0;padding:0}li{padding:4px 0}li.bad{color:#ffb3ba}li.todo{color:var(--dim)}
.foot{color:var(--dim);font-size:14px;margin-top:8px}.soon{font-size:13px;color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:0 6px}
</style></head><body>
<h1>%(title)s</h1><div class="sub">%(snapshot)s</div>
<div class="bay" id="core"><h2>%(core)s</h2><div class="grid">%(ais)s</div></div>
<div class="bay" id="tasks"><h2>%(tasks_h)s · %(done)d/%(total)d</h2><div class="sub">%(tier_line)s</div><ul>%(task_li)s</ul></div>
<div class="bay" id="binding"><h2>%(binding_h)s</h2><div>%(binding)s</div></div>
<div class="bay" id="health"><h2>%(health_h)s</h2><ul>%(health_li)s</ul></div>
<div class="foot">%(refresh)s</div>
<script>
document.querySelectorAll('.cp').forEach(function(b){b.addEventListener('click',function(){
 var c=b.getAttribute('data-cmd');var ok=function(){var t=b.textContent;b.textContent=%(copied)s;setTimeout(function(){b.textContent=t},1200)};
 if(navigator.clipboard){navigator.clipboard.writeText(c).then(ok,function(){})}});});
</script>
</body></html>
""" % {"title": _e(T("title")), "snapshot": _e(T("snapshot", when=_when(now))), "core": _e(T("core")), "ais": ais,
       "tasks_h": _e(T("tasks")), "done": done, "total": len(tasks), "task_li": task_li, "tier_line": tier_line,
       "binding_h": _e(T("binding")), "binding": _e(T("binding_line", ok=b.get("ok", 0), seats=b.get("seats", 0))),
       "health_h": _e(T("health")), "health_li": health_li, "refresh": _e(T("refresh")),
       "copied": json.dumps(T("copied"), ensure_ascii=False).replace("<", "\\u003c")}


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


def open_power(out=None, env=None, status=None, opener=None):
    """`aib ui power`:量一次、寫頁、開視窗。回 rc:0 開了;2 開不了(印檔案位置讓人自己開)。"""
    import aib_ui
    out = sys.stdout if out is None else out
    if status is None:
        import power_status
        status = power_status.status
    p = page_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_page(p, render(status()))
    how, _detail = (opener or aib_ui.open_app_window)(p.as_uri(), env)
    if how in ("app", "browser"):
        out.write(T("opened") + "\n")
        return 0
    out.write(T("open_failed", path=str(p)) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(open_power())
