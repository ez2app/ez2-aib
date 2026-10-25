# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""R6 引擎徽章的**採集端**判定:三態 mode 與字型缺字退回(裁定 RULING-R6-engine-badge-icons-0830)。

★ 這一支住在採集端,可以讀 env 與字型檔;render_lib 不讀環境(黃金幀決定性),mode 由呼叫端注入。
★ 三條裁定:
  1. mode ∈ {both, icon, letters},預設 both;**只有即時 TTY 可以 both/icon**——落檔、貼給別席、進交接單一律 letters
     (930/292 事故:兩個人看到不同畫面用同一句話討論)。「即時 TTY」由 bash 包裝層 `[ -t 1 ]` 判後以
     EZ2_AIB_BADGE_TTY=1 注入;採集端 python 的 stdout 被重導到檔案,自己 isatty() 永遠是 False,不能拿來判。
  2. 字型缺字要退回 letters **並且說出來**:靜默退會讓「字型沒裝」與「這一席沒有引擎」同形。
     退回的理由由本函式回傳,呼叫端印到 stderr(.err 檔)。
  3. 字型探針讀 TTF 的 cmap(format 4/12),不用 fontTools(未安裝);字型檔由 EZ2_AIB_NERD_FONT 指定,
     預設掃 ~/Library/Fonts/*NerdFont*-Regular.ttf;找不到字型檔=缺字,不是綠。
  4. 10-25 Mac Pro 13(@ae6a1):字型檔裝了,Apple Terminal 設定檔卻還是 SF Mono ⇒ 畫成空白,而這裡只看字型檔就判「好了」。
     Apple Terminal 不能設備用字型 ⇒ 外層終端機是 Apple Terminal 時,再查「預設 / 開啟時」設定檔的字型畫不畫得出
     (JXA 讀 com.apple.Terminal 偏好 + NSFont.coveredCharacterSet,不叫起 Terminal);畫不出 ⇒ 退字母,理由 terminal_font。
     iTerm2 / Ghostty / WezTerm 有備用字型,維持只看字型檔。結果記在行程裡 + <狀態根>/state/badge-term-font.json,
     偏好檔 / 字型夾沒改就不重查(狀態列每次刷新是新行程)。
"""
import glob
import json
import os
import struct
import subprocess
import sys

BADGE_MODES = ("both", "icon", "letters")
MODE_ENV = "EZ2_AIB_ENGINE_BADGE"
TTY_ENV = "EZ2_AIB_BADGE_TTY"
FONT_ENV = "EZ2_AIB_NERD_FONT"
DEFAULT_FONT_GLOB = "~/Library/Fonts/*NerdFont*-Regular.ttf"


def ttf_codepoints(path):
    """回該 TTF cmap 裡所有字碼的集合(format 4 與 12);讀不懂回空集合(=缺字方向)。"""
    cps = set()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        num_tables = struct.unpack(">H", data[4:6])[0]
        cmap_off = None
        for i in range(num_tables):
            rec = 12 + 16 * i
            tag = data[rec:rec + 4]
            if tag == b"cmap":
                cmap_off = struct.unpack(">I", data[rec + 8:rec + 12])[0]
                break
        if cmap_off is None:
            return cps
        n_sub = struct.unpack(">H", data[cmap_off + 2:cmap_off + 4])[0]
        for i in range(n_sub):
            rec = cmap_off + 4 + 8 * i
            off = struct.unpack(">I", data[rec + 4:rec + 8])[0]
            sub = cmap_off + off
            fmt = struct.unpack(">H", data[sub:sub + 2])[0]
            if fmt == 4:
                segx2 = struct.unpack(">H", data[sub + 6:sub + 8])[0]
                seg = segx2 // 2
                ends = struct.unpack(">%dH" % seg, data[sub + 14:sub + 14 + segx2])
                starts_off = sub + 16 + segx2
                starts = struct.unpack(">%dH" % seg, data[starts_off:starts_off + segx2])
                for s, e in zip(starts, ends):
                    if s == 0xFFFF:
                        continue
                    cps.update(range(s, e + 1))
            elif fmt == 12:
                ngroups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
                for g in range(ngroups):
                    go = sub + 16 + 12 * g
                    s, e, _gid = struct.unpack(">III", data[go:go + 12])
                    cps.update(range(s, e + 1))
    except (OSError, struct.error, ValueError):
        return set()
    return cps


def font_paths(environ=None):
    e = os.environ if environ is None else environ
    forced = e.get(FONT_ENV)
    if forced:
        return [forced]
    return sorted(glob.glob(os.path.expanduser(DEFAULT_FONT_GLOB)))


def missing_glyphs(codepoints, paths):
    """回 (缺的字碼列表, 用了哪個字型檔);沒有字型檔 ⇒ 全缺、檔名 None。"""
    if not paths:
        return sorted(codepoints), None
    path = paths[0]
    have = ttf_codepoints(path)
    return sorted(cp for cp in codepoints if cp not in have), path


OUTER_TERM_ENV = "EZ2_AIB_OUTER_TERM"          # 測試 / 包裝層可直接指定外層終端機(Apple_Terminal …)
TERM_CHECK_ENV = "EZ2_AIB_TERM_FONT_CHECK"     # =0 ⇒ 不查終端機字型(維持只看字型檔)
TERM_PLIST = "~/Library/Preferences/com.apple.Terminal.plist"
USER_FONTS = "~/Library/Fonts"
TERM_FONT_JXA = r"""
ObjC.import('AppKit');
function run(argv) {
  var d = $.NSUserDefaults.alloc.initWithSuiteName('com.apple.Terminal');
  var ws = d.dictionaryForKey('Window Settings');
  var out = [];
  ['Default Window Settings', 'Startup Window Settings'].forEach(function (k) {
    var name = ObjC.unwrap(d.stringForKey(k));
    if (!name) return;
    var prof = (ws && !ws.isNil()) ? ws.objectForKey(name) : null;
    var fd = (prof && !prof.isNil()) ? prof.objectForKey('Font') : null;
    var font = (fd && !fd.isNil()) ? $.NSKeyedUnarchiver.unarchiveObjectWithData(fd) : null;
    if (!font || font.isNil()) font = $.NSFont.userFixedPitchFontOfSize(11);
    var cs = font.coveredCharacterSet;
    out.push({key: k, profile: name, font: ObjC.unwrap(font.fontName),
              covered: argv.map(function (cp) { return cs.longCharacterIsMember(parseInt(cp, 16)); })});
  });
  return JSON.stringify(out);
}
"""
_term_cache = {}
TERM_CACHE_FILE = "badge-term-font.json"       # <狀態根>/state/:狀態列每次刷新是新行程,行程內快取用不到


def _cache_path(environ):
    base = environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(base, "state", TERM_CACHE_FILE)


def _cache_read(environ, key):
    try:
        with open(_cache_path(environ), encoding="utf-8") as fh:
            rec = json.load(fh)
        if rec.get("key") == list(key) and isinstance(rec.get("res"), dict) and rec["res"].get("ok") in (True, False):
            return rec["res"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _cache_write(environ, key, res):
    p = _cache_path(environ)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": list(key), "res": res}, fh)
        os.replace(tmp, p)
    except OSError:
        pass


def _run(argv, timeout=5):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout


def outer_terminal(environ=None, runner=None):
    """外層終端機(TERM_PROGRAM)。駕駛艙在 tmux 裡,自己的 TERM_PROGRAM 是 tmux ⇒ 讀 tmux 全域環境。讀不到 ⇒ None。"""
    e = os.environ if environ is None else environ
    if e.get(OUTER_TERM_ENV):
        return e[OUTER_TERM_ENV]
    if e.get("TMUX"):
        try:
            rc, out = (runner or _run)(["tmux", "show-environment", "-g", "TERM_PROGRAM"])
        except (OSError, subprocess.SubprocessError):
            return None
        line = out.strip() if rc == 0 else ""
        return line.split("=", 1)[1] if line.startswith("TERM_PROGRAM=") else None
    tp = e.get("TERM_PROGRAM")
    return tp if tp and tp != "tmux" else None


def _mtime(path):
    try:
        return os.stat(os.path.expanduser(path)).st_mtime_ns
    except OSError:
        return None


def terminal_font_check(codepoints, environ=None, runner=None, platform=None):
    """外層是 Apple Terminal ⇒ {"ok": bool, "missing": [字碼], "profile", "font"};不是 / 查不到 / 關掉 ⇒ {"ok": None}。
    ok=False = 「預設」或「開啟時」設定檔的字型畫不出某個字碼。同一行程內偏好檔與字型夾沒變就用上次結果。"""
    e = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if e.get(TERM_CHECK_ENV) == "0" or platform != "darwin":
        return {"ok": None}
    if outer_terminal(e, runner) != "Apple_Terminal":
        return {"ok": None}
    cps = sorted(codepoints)
    key = (",".join("%x" % cp for cp in cps), _mtime(TERM_PLIST), _mtime(USER_FONTS))
    if runner is None:
        hit = _term_cache.get(key) or _cache_read(e, key)
        if hit is not None:
            _term_cache[key] = hit
            return hit
    try:
        rc, out = (runner or _run)(["osascript", "-l", "JavaScript", "-e", TERM_FONT_JXA] + ["%x" % cp for cp in cps])
        rows = json.loads(out) if rc == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        rows = None
    if not isinstance(rows, list) or not rows:
        return {"ok": None}
    missing, bad = set(), None
    for r in rows:
        cov = r.get("covered") if isinstance(r, dict) else None
        if not isinstance(cov, list) or len(cov) != len(cps):
            return {"ok": None}
        lost = {cp for cp, ok in zip(cps, cov) if ok is not True}
        if lost and bad is None:
            bad = r
        missing |= lost
    res = {"ok": not missing, "missing": sorted(missing),
           "profile": str((bad or rows[0]).get("profile") or "")[:60], "font": str((bad or rows[0]).get("font") or "")[:80]}
    if runner is None:
        _term_cache.clear()
        _term_cache[key] = res
        _cache_write(e, key, res)
    return res


def resolve_badge_mode(environ=None, *, icon_codepoints, tty=None, paths=None):
    """回 (mode, reason)。reason 非空 = 退回了 letters 且要說出來。

    - requested = EZ2_AIB_ENGINE_BADGE(預設 both;不認得的值當 both 並在 reason 說)
    - tty=False(或 EZ2_AIB_BADGE_TTY != "1")⇒ letters(會被引用的輸出鎖標準模式)
    - 字型檔缺 / 任一 icon 字碼不在 cmap ⇒ letters + 理由(含缺的字碼)
    """
    e = os.environ if environ is None else environ
    requested = (e.get(MODE_ENV) or "both").strip().lower()
    note = ""
    if requested not in BADGE_MODES:
        note = "%s=%r 不認得,當 both;" % (MODE_ENV, requested)
        requested = "both"
    if tty is None:
        tty = (e.get(TTY_ENV) == "1")
    if not tty:
        return "letters", ""                       # 非即時 TTY:鎖標準模式,不是退回,不用出聲
    if requested == "letters":
        return "letters", ""
    missing, path = missing_glyphs(set(icon_codepoints), font_paths(e) if paths is None else paths)
    if path is None:
        return "letters", note + "字型缺:找不到 Nerd Font 檔(%s 或 %s),徽章退回字母" % (FONT_ENV, DEFAULT_FONT_GLOB)
    if missing:
        return "letters", note + "字型缺字 %s(%s),徽章退回字母" % (
            ",".join("U+%04X" % cp for cp in missing), os.path.basename(path))
    return requested, note


def badge_inputs(environ=None, *, icons, tty=None, paths=None, runner=None, platform=None):
    """採集端一次算好要注入渲染器的三樣:{"mode","icon_available":{provider:bool},"reason"}。

    icons = render_lib.ENGINE_ICONS。mode=requested(非 TTY 鎖 letters);icon_available 逐 provider
    (字碼不在 cmap 或字型檔缺 ⇒ False);reason 只在有 provider 退回時非空(呼叫端印 stderr)。
    退回本身由渲染器 resolve_engine_badge 執行並在 rowmap detail 具名(font_missing;
    字型檔在、但 Apple Terminal 設定檔字型畫不出 ⇒ missing_reason=terminal_font,渲染器照這個名字記)。"""
    e = os.environ if environ is None else environ
    requested = (e.get(MODE_ENV) or "both").strip().lower()
    note = ""
    if requested not in BADGE_MODES:
        note = "%s=%r 不認得,當 both;" % (MODE_ENV, requested)
        requested = "both"
    if tty is None:
        tty = (e.get(TTY_ENV) == "1")
    if not tty:
        return {"mode": "letters", "icon_available": {p: True for p in icons}, "reason": "", "missing_reason": None}
    cps = {ord(ch) for ch in icons.values()}
    missing, path = missing_glyphs(cps, font_paths(e) if paths is None else paths)
    available = {p: (ord(ch) not in missing) for p, ch in icons.items()}
    if requested != "letters" and not all(available.values()):        # 字型檔缺 / 缺字(先講字型檔)
        lost = sorted(p for p, ok in available.items() if not ok)
        if path is None:
            note += "字型缺:找不到 Nerd Font 檔(%s 或 %s),%s 徽章退回字母" % (FONT_ENV, DEFAULT_FONT_GLOB, ",".join(lost))
        else:
            note += "字型缺字 %s(%s),%s 徽章退回字母" % (
                ",".join("U+%04X" % cp for cp in missing), os.path.basename(path), ",".join(lost))
    why = "font_missing"
    if requested != "letters" and any(available.values()):
        tf = terminal_font_check({ord(icons[p]) for p, ok in available.items() if ok}, e, runner, platform)
        if tf["ok"] is False:
            lost = sorted(p for p, ok in available.items() if ok and ord(icons[p]) in tf["missing"])
            for p in lost:
                available[p] = False
            why = "terminal_font"
            note += ";" if note and not note.endswith(";") else ""
            note += "終端機字型畫不出 %s(Apple Terminal 設定檔「%s」用 %s),%s 徽章退回字母;aib doctor 看怎麼換字型" % (
                ",".join("U+%04X" % cp for cp in tf["missing"]), tf["profile"], tf["font"], ",".join(lost))
    if all(available.values()):
        why = None
    elif why == "terminal_font" and missing:
        why = "font_missing"                       # 字型檔本身也缺 ⇒ 先講字型檔
    return {"mode": requested, "icon_available": available, "reason": note, "missing_reason": why}
