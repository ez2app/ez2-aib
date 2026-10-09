#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""第 39 項:AI任務區頁簽的正文來源 —— 把 38 產生器(tools/dispatch/ai_task_zone.py)接進來。

只做一件事:`feature_zone_lines(seat, width, max_lines)` 回一串純文字列給
`render_lib.render_feature_zone_box` 去畫。資料怎麼算是 38 的事(四正本、來源、時刻、節流),
本檔不算、不快取、不寫任何東西;**沙盒任務卡在這裡沒有入口**(第 50 項)。

契約(開發 B a seat 09-12 03:15 一行):模組 tools/dispatch/ai_task_zone.py,
    render_lines(seat: str, width: int, max_lines: int, now=None) -> list[str]
回純文字列已含框線、每列裁到 width、超出 max_lines 時最後一列是「還有 N 列未顯示」、永不 raise、
內部依四正本 mtime 節流。本檔用 importlib 按路徑載它(不靠 sys.path 順序),
**任何一步失敗都回一列「量不到:為什麼」**,不炸畫圈、不留空白 —— 空白與「沒有事」同形,
而讀不到與沒有事的處置相反。38 說永不 raise,但那是它的承諾,這裡仍接住(承諾不是機制)。

換席必換內容靠參數:seat 是「中間那位」,逐幀由 pane-pending-review.sh 現查後傳進來,
本檔不記上一席。
"""
import importlib.util
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 38 在 fd6aa822 把「可 import 的正本」ai_task_zone.py 與「薄殼 CLI」ai-task-zone.py 分開:
# 正本才有 _RENDER_CACHE 與 sys.modules 快取。一律先找正本,沒有才退到舊名並在來源字面說出來。
GENERATOR_CANDIDATES = ("ai_task_zone.py", "ai-task-zone.py")
GENERATOR_REL = os.path.join("tools", "dispatch", GENERATOR_CANDIDATES[0])
ENTRY = "render_lines"
UNMEASURED = "量不到:%s"
# 38 的 render_lines 有 width=max(40, …) 地板:框比 40 窄時它照 40 排、列尾「來源 時刻」會被裁掉
# 而沒有任何一格紅(qa 39-1)。所以這裡先擋:框內寬 < 40 ⇒ 不叫 38、回一列具名。
MIN_WIDTH = 40
TOO_NARROW = "框太窄(需 %d 欄,現 %d)"
# 39-2:每幀重 exec 整支模組會讓 38 的節流快取恆冷;依 (path, mtime) 記住模組物件。
_MOD_CACHE = {}


def generator_path(root=None):
    """38 那支檔在本樹的位置(src/cockpit 往上兩層)。兩支都在 ⇒ 正本 ai_task_zone.py;
    只有舊名 ⇒ 舊名;都不在 ⇒ 回正本的路徑(給「不在:<路徑>」那一句用)。"""
    root = root or os.path.dirname(os.path.dirname(HERE))
    for name in GENERATOR_CANDIDATES:
        cand = os.path.join(root, "tools", "dispatch", name)
        if os.path.isfile(cand):
            return cand
    return os.path.join(root, GENERATOR_REL)


def load_generator(path=None):
    """載 38 的模組;回 (module, None) 或 (None, 理由字串)。

    同一支檔(路徑 + mtime 同)在同一個行程裡只 exec 一次(39-2):第二次直接回快取的模組物件,
    38 自己的模組層快取才活得過兩次呼叫。檔改了(mtime 變)就重載。
    也登記進 sys.modules["ai_task_zone"],讓 38 內部的相對引用與快取看到同一個物件。
    ★ pane-pending-review.sh 的 _paint 每幀是一個新 python 行程,所以跨幀的節流要靠 38 自己
      寫在磁碟上的投影;這裡的快取只保證「同一行程內不重 exec」。
    """
    path = path or generator_path()
    if not os.path.isfile(path):
        return None, "AI任務區產生器不在:%s" % path
    try:
        key = (os.path.realpath(path), os.stat(path).st_mtime_ns)
    except OSError as exc:
        return None, "AI任務區產生器讀不到:%r" % (exc,)
    mod = _MOD_CACHE.get(key)
    if mod is None:
        try:
            spec = importlib.util.spec_from_file_location("ai_task_zone", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["ai_task_zone"] = mod
            spec.loader.exec_module(mod)
        except Exception as exc:  # 載入失敗一律具名,不讓畫圈炸
            sys.modules.pop("ai_task_zone", None)
            return None, "AI任務區產生器載入失敗:%r" % (exc,)
        _MOD_CACHE.clear()          # 只留最新那一版,不讓舊 mtime 的模組物件堆著
        _MOD_CACHE[key] = mod
    if not callable(getattr(mod, ENTRY, None)):
        return None, "AI任務區產生器沒有 %s(seat, width, max_lines)" % ENTRY
    return mod, None


TASK_MODES = ("compact", "detail")
TASK_MODE_DEFAULT = "compact"


def task_mode_of(value):
    """精簡/詳細(第 68 項):只認這兩個字面,其餘一律預設 compact。"""
    v = str(value or "").strip().lower()
    return v if v in TASK_MODES else TASK_MODE_DEFAULT


def provider_accepts_mode(provider):
    """38 的 render_lines 有沒有**明示**宣告 mode 參數。★ 只認具名參數,不認 **kwargs——
    它的 **paths 是治具路徑覆蓋,把 mode 塞進去會被當成一條路徑。"""
    try:
        params = inspect.signature(provider).parameters
    except (TypeError, ValueError):
        return False
    p = params.get("mode")
    return p is not None and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)


def provider_accepts_color(provider):
    """同 provider_accepts_mode:只認具名的 color 參數(AI任務區進度條色,the maintainer 09-15 02:5x)。"""
    try:
        params = inspect.signature(provider).parameters
    except (TypeError, ValueError):
        return False
    p = params.get("color")
    return p is not None and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)


def feature_zone_lines(seat, width, max_lines, provider=None, path=None, mode=None, color=None):
    """回 (lines, source):lines 給框畫,source 是「這批列從哪來」的一句(留痕用)。

    provider:測試注入用;產品一律走 38 的 render_lines。
    max_lines ≤ 0 ⇒ 回空列(框自己會畫「沒有進行中的任務」或不畫)。
    mode(第 68 項):compact/detail;產生器明示宣告 mode 才傳,沒宣告就照舊呼叫、source 標 mode=unsupported。
    color:None / "256" / "truecolor"(呼叫端判好,NO_COLOR 傳 None);產生器明示宣告 color 才傳。
    """
    seat = str(seat or "").strip().lstrip("@")
    try:
        width = int(width)
        max_lines = int(max_lines)
    except (TypeError, ValueError):
        return [UNMEASURED % "寬度或列數不是整數"], "bad_args"
    if not seat:
        return [UNMEASURED % "沒有席碼(中間那位認不到)"], "no_seat"
    if max_lines <= 0:
        return [], "no_room"
    if width < MIN_WIDTH:
        # 39-1:比 38 的排版地板還窄 ⇒ 不叫它(它會照 40 排、尾巴被裁掉而不紅),具名說框太窄
        return [TOO_NARROW % (MIN_WIDTH, width)], "too_narrow"
    if provider is None:
        path_used = path or generator_path()
        mod, why = load_generator(path_used)
        if mod is None:
            return [UNMEASURED % why], "generator_missing"
        provider = getattr(mod, ENTRY)
        # 來源字面印真正載到的檔名(正本或舊名薄殼),不寫死
        source = "%s:%s" % (os.path.basename(path_used), ENTRY)
    else:
        source = "provider:%s" % getattr(provider, "__name__", "?")
    mode = task_mode_of(mode)
    kw = {}
    if provider_accepts_color(provider):
        kw["color"] = color if color in ("256", "truecolor") else None
    try:
        if provider_accepts_mode(provider):
            out = provider(seat, width, max_lines, mode=mode, **kw)
            source = "%s mode=%s" % (source, mode)
        else:
            out = provider(seat, width, max_lines, **kw)
            source = "%s mode=unsupported" % source
    except Exception as exc:
        return [UNMEASURED % ("AI任務區產生器出錯:%r" % (exc,))], "generator_error"
    if not isinstance(out, (list, tuple)):
        return [UNMEASURED % ("AI任務區產生器回的不是列表:%r" % type(out).__name__)], "bad_shape"
    lines = []
    for x in out:
        s = str(x).replace("\r", "")
        lines.extend(s.split("\n") if s else [""])
    return lines[:max_lines], source


def _main(argv):
    seat = argv[1] if len(argv) > 1 else ""
    width = int(argv[2]) if len(argv) > 2 else 100
    max_lines = int(argv[3]) if len(argv) > 3 else 40
    lines, source = feature_zone_lines(seat, width, max_lines)
    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))
    sys.stderr.write("source=%s\n" % source)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
