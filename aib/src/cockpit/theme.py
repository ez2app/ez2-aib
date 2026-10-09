#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Strict loader and renderer helpers for AIB pane themes."""

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

from cockpit_lib import panel_contrast_sgr


SCHEMA_VERSION = "aib-theme/v1"
REGIONS = ("left", "center", "right", "bottom")
STATES = ("normal", "focused")

# 覆寫檔的保留鍵命名空間(the maintainer 08-14「無底色」)。
#
# 「無底色」是全艙一個開關而不是 per-region 覆寫,因為它要同時管四格
# window-style **和兩條 status 列**(內層 hub / 外層),而那兩條不是 region、
# 沒有 per-region 覆寫的位置。故在同一個覆寫檔裡放保留鍵當單一真相源。
#
# 守衛的理由:region 名將來若出現 "_cockpit",它會與這個保留鍵**靜默相撞**
# ——覆寫檔是扁平 dict,撞了不會報錯,只會有一邊被另一邊吃掉。所以 region
# 名永遠不得以 "_" 開頭,並在載入時就擋下(不是靠人記得)。
OVERRIDE_RESERVED_PREFIX = "_"
COCKPIT_OVERRIDE_KEY = "_cockpit"
COCKPIT_NO_BACKGROUND = "none"
# 未設定 / 新安裝 = 透明淡黑。要自定底色必須明示這個值,不能靠「沒有鍵」。
COCKPIT_PALETTE_MODE = "palette"

if any(r.startswith(OVERRIDE_RESERVED_PREFIX) for r in REGIONS):
    raise RuntimeError(
        "region 名不得以 %r 開頭:會與覆寫檔保留鍵相撞"
        % OVERRIDE_RESERVED_PREFIX)
ATTRIBUTES = {"bold", "dim", "italics", "underscore"}
TOP_LEVEL_KEYS = {
    "schema_version", "theme_id", "name_key", "default", "regions", "palette"
}
RESET = "\x1b[0m"
SGR_RE = re.compile(r"\x1b\[([0-9;:]*)m")


class ThemeError(RuntimeError):
    """Theme data is unsafe or violates the AIB theme contract."""


def _strict_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ThemeError("theme has duplicate JSON key: %s" % key)
        data[key] = value
    return data


def _open_safe(path):
    path = Path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ThemeError("theme loader requires O_NOFOLLOW support")
    # O_NONBLOCK matters before fstat: opening a caller-selected FIFO read-only
    # would otherwise wait forever for a writer instead of reaching the
    # regular-file gate. It has no effect on ordinary files.
    flags = (os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
             | os.O_NOFOLLOW)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as err:
        raise ThemeError("theme is unreadable: %s (%s)" % (path, err))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ThemeError("theme must be a regular non-symlink file: %s" % path)
        if info.st_uid != os.getuid():
            raise ThemeError("theme owner does not match the current user: %s" % path)
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise ThemeError("theme must not be group/world writable: %s" % path)
        if info.st_nlink != 1:
            raise ThemeError("theme must have exactly one filesystem link: %s" % path)
        return path, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _colour(value, where):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ThemeError("%s must be an xterm colour integer from 0 to 255" % where)
    return value


def _validate_state(value, where):
    if not isinstance(value, dict) or set(value) != {
            "background", "foreground", "attributes"}:
        raise ThemeError("%s has an invalid style shape" % where)
    _colour(value["background"], where + ".background")
    _colour(value["foreground"], where + ".foreground")
    attrs = value["attributes"]
    if (not isinstance(attrs, list) or len(attrs) != len(set(attrs))
            or any(attr not in ATTRIBUTES for attr in attrs)):
        raise ThemeError("%s has invalid or duplicate attributes" % where)


def _validate(data):
    if not isinstance(data, dict) or set(data) != TOP_LEVEL_KEYS:
        raise ThemeError("theme top-level keys do not match %s" % SCHEMA_VERSION)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ThemeError("unsupported theme schema: %s" % data.get("schema_version"))
    if (not isinstance(data.get("theme_id"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", data["theme_id"])):
        raise ThemeError("theme_id must be a safe lowercase identifier")
    if not isinstance(data.get("name_key"), str) or not data["name_key"]:
        raise ThemeError("theme name_key must be a non-empty string")
    if not isinstance(data.get("default"), bool):
        raise ThemeError("theme default marker must be boolean")

    regions = data.get("regions")
    if not isinstance(regions, dict) or set(regions) != set(REGIONS):
        raise ThemeError("theme must define exactly: %s" % ",".join(REGIONS))
    for role in REGIONS:
        region = regions[role]
        if not isinstance(region, dict) or set(region) != {
                "name_key", "normal", "focused"}:
            raise ThemeError("theme region has an invalid shape: %s" % role)
        if not isinstance(region["name_key"], str) or not region["name_key"]:
            raise ThemeError("theme region name_key is invalid: %s" % role)
        for state_name in STATES:
            _validate_state(region[state_name], "%s.%s" % (role, state_name))

    palette = data.get("palette")
    if not isinstance(palette, list) or not palette:
        raise ThemeError("theme palette must be a non-empty list")
    families = set()
    codes = set()
    for index, item in enumerate(palette):
        where = "palette[%d]" % index
        if not isinstance(item, dict) or set(item) != {"id", "name_key", "codes"}:
            raise ThemeError("%s has an invalid shape" % where)
        family = item["id"]
        if not isinstance(family, str) or not family or family in families:
            raise ThemeError("%s id is empty or duplicated" % where)
        families.add(family)
        if not isinstance(item["name_key"], str) or not item["name_key"]:
            raise ThemeError("%s name_key is invalid" % where)
        if not isinstance(item["codes"], list) or not item["codes"]:
            raise ThemeError("%s codes must be a non-empty list" % where)
        for code in item["codes"]:
            _colour(code, where + ".codes")
            if code in codes:
                raise ThemeError("palette colour is duplicated: %s" % code)
            codes.add(code)
    normal_backgrounds = {
        regions[role]["normal"]["background"] for role in REGIONS
    }
    if not normal_backgrounds.issubset(codes):
        raise ThemeError("every normal region background must be in the palette")
    return data


def _pinned_rescue(path):
    """checkout 權限位假紅的活路:位元組==release 對釘才走 0600 快照。

    嚴格路徑完全不變;本函式只在 group-writable 被拒後才會被叫,
    world-write/自訂檔/位元組不符在 dataguard 內仍然 fail-closed。
    """
    import sys as _sys
    _src = str(Path(__file__).resolve().parents[1])
    if _src not in _sys.path:
        _sys.path.insert(0, _src)
    from dataguard.dataguard import load_verified_bytes
    repo_root = Path(__file__).resolve().parents[2]
    try:
        rel = str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        rel = "__custom__"
    data_bytes, _receipt = load_verified_bytes(
        path, pin_key=rel, error_type=ThemeError, label="theme", force_pin=True)
    try:
        return json.loads(data_bytes.decode("utf-8"),
                          object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError) as err:
        raise ThemeError("theme is not valid JSON: %s (%s)" % (path, err))


def load_theme(path):
    try:
        path, descriptor = _open_safe(path)
    except ThemeError as err:
        if "group/world writable" not in str(err):
            raise
        return _validate(_pinned_rescue(path))
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            data = json.load(handle, object_pairs_hook=_strict_object)
    except ThemeError:
        raise
    except (OSError, ValueError) as err:
        raise ThemeError("theme is not valid JSON: %s (%s)" % (path, err))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _validate(data)


def pane_style(theme, role, state):
    if role not in REGIONS:
        raise ThemeError("unknown theme region: %s" % role)
    if state not in STATES:
        raise ThemeError("unknown theme state: %s" % state)
    style = theme["regions"][role][state]
    parts = ["fg=colour%d" % style["foreground"],
             "bg=colour%d" % style["background"]]
    parts.extend(style["attributes"])
    return ",".join(parts)


def palette_entry(theme, palette_id):
    """palette 逐筆查表;找不到=錯誤不是降級(色彩來源只有這一個)。"""
    for item in theme["palette"]:
        if item["id"] == palette_id:
            return item
    raise ThemeError("unknown palette id: %s" % palette_id)


def palette_rows(theme):
    """供選單建構:id|name_key|normal_bg|focused_bg 每行一筆。"""
    rows = []
    for item in theme["palette"]:
        codes = item["codes"]
        rows.append("%s|%s|%d|%d" % (item["id"], item["name_key"],
                                     codes[0], codes[-1]))
    return "\n".join(rows)


def override_style(theme, role, state, palette_id):
    """使用者選色後的該格樣式:**只換 bg**,fg 與 attributes 仍取 region
    契約值——十色都是已裁定過對比的色,自由色值不開放(the maintainer 08-14 裁)。

    focused 取 codes[-1]、normal 取 codes[0]:多碼色家族(如 gold 94/130)
    原本就是暗/亮一對;單碼色兩態同底,焦點差異由 region 的 bold 與邊界
    亮度差(E2.16 終裁「選中只亮度差不帶色」)承擔,不在這裡自造第二套
    焦點色語彙。"""
    if role not in REGIONS:
        raise ThemeError("unknown theme region: %s" % role)
    if state not in STATES:
        raise ThemeError("unknown theme state: %s" % state)
    codes = palette_entry(theme, palette_id)["codes"]
    base = theme["regions"][role][state]
    parts = ["fg=colour%d" % base["foreground"],
             "bg=colour%d" % (codes[0] if state == "normal" else codes[-1])]
    parts.extend(base["attributes"])
    return ",".join(parts)


def no_bg_style(theme, role, state):
    """「無底色」:拿掉背景讓終端底色透出,**前景與 attributes 一律保留**。

    為什麼不連 fg 也 default:終端的預設前景可能是任何顏色,在淺色終端上
    fg=default+bg=default 會變成「無畫面」而不是「無底色」(0a5b3 裁,root3
    採)。所以只動 bg 這一個軸。

    與 override_style 的關係:兩者互斥——選了十色之一就是有底色,選了無底色
    就沒有 palette id 可言。呼叫端負責二選一,這裡不做混合。"""
    if role not in REGIONS:
        raise ThemeError("unknown theme region: %s" % role)
    if state not in STATES:
        raise ThemeError("unknown theme state: %s" % state)
    base = theme["regions"][role][state]
    parts = ["fg=colour%d" % base["foreground"], "bg=default"]
    parts.extend(base["attributes"])
    return ",".join(parts)


def strip_background(style):
    """把任意 tmux style 字串的 bg= 換成 default,其餘逐段原樣保留。

    給兩條 status 列用:它們的預設值寫死在 cockpit-full.sh 裡、不是 region,
    沒有 theme 契約可查,只能就地改。逐段處理而不是整串重組,是為了讓
    「沒設無底色時輸出逐位元組等同今天」這條約束成立。"""
    parts = []
    seen_bg = False
    for part in style.split(","):
        if part.strip().startswith("bg="):
            parts.append("bg=default")
            seen_bg = True
        else:
            parts.append(part)
    if not seen_bg:
        parts.append("bg=default")
    return ",".join(parts)


def style_snapshot(theme):
    lines = ["theme_id|%s" % theme["theme_id"]]
    for role in REGIONS:
        for state in STATES:
            lines.append("%s|%s|%s" % (role, state,
                                        pane_style(theme, role, state)))
    return "\n".join(lines)


def _contrast_sgr(match, base):
    """Keep non-colour SGR state while enforcing the panel foreground."""
    raw = match.group(1).split(";") if match.group(1) else ["0"]
    kept = []
    reset = False
    index = 0
    while index < len(raw):
        item = raw[index]
        # ISO colon-form extended colours (for example 38:5:196).
        if ":" in item:
            try:
                code = int(item.split(":", 1)[0] or 0)
            except ValueError:
                code = -1
            if code == 0:
                reset = True
                kept = []
            elif code not in (1, 2, 22, 38, 39) and not (
                    30 <= code <= 37 or 90 <= code <= 97):
                kept.append(item)
            index += 1
            continue

        code = int(item or 0)
        if code == 0:
            reset = True
            kept = []
        elif code in (1, 2, 22, 39) or 30 <= code <= 37 or 90 <= code <= 97:
            # The approved base owns intensity and foreground contrast.
            pass
        elif code == 38:
            # Drop the entire indexed/RGB foreground group.
            if index + 1 < len(raw) and raw[index + 1] == "5":
                index += min(2, len(raw) - index - 1)
            elif index + 1 < len(raw) and raw[index + 1] == "2":
                index += min(4, len(raw) - index - 1)
        else:
            kept.append(item or "0")
            # Preserve a complete extended background/underline-colour group.
            if code in (48, 58) and index + 1 < len(raw):
                span = 2 if raw[index + 1] == "5" else 4 if raw[index + 1] == "2" else 0
                for _ in range(min(span, len(raw) - index - 1)):
                    index += 1
                    kept.append(raw[index])
        index += 1

    out = RESET if reset else ""
    if kept:
        out += "\x1b[%sm" % ";".join(kept)
    return out + base


def apply_panel_contrast(text, theme, role, state="normal"):
    if role not in REGIONS or state not in STATES:
        raise ThemeError("invalid panel contrast target: %s/%s" % (role, state))
    background = theme["regions"][role][state]["background"]
    base = panel_contrast_sgr(background)
    # Renderers legitimately use brand colours and DIM/active-tab attributes.
    # A bright panel cannot inherit those foregrounds: rewrite every SGR, keep
    # layout attributes such as underline/reverse, and reassert the one
    # contrast foreground after each sequence.
    body = SGR_RE.sub(lambda match: _contrast_sgr(match, base), str(text))
    return base + body + RESET


def _default_theme_path():
    return Path(__file__).resolve().parents[2] / "themes" / "aib-default.json"


def _main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=os.environ.get("EZ2_AIB_THEME_FILE")
                        or str(_default_theme_path()))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("snapshot")
    sub.add_parser("palette")
    style = sub.add_parser("style")
    style.add_argument("role", choices=REGIONS)
    style.add_argument("state", choices=STATES)
    style.add_argument("--palette", default=None,
                       help="使用者選色覆寫(palette id);只換 bg")
    style.add_argument("--no-bg", action="store_true",
                       help="無底色:bg=default,前景與 attributes 保留")
    strip = sub.add_parser("strip-bg")
    strip.add_argument("style")
    panel = sub.add_parser("panel-filter")
    panel.add_argument("role", choices=REGIONS)
    panel.add_argument("--state", choices=STATES, default="normal")
    args = parser.parse_args(argv)
    try:
        theme = load_theme(args.file)
        if args.command == "validate":
            print(theme["theme_id"])
        elif args.command == "snapshot":
            print(style_snapshot(theme))
        elif args.command == "palette":
            print(palette_rows(theme))
        elif args.command == "strip-bg":
            print(strip_background(args.style))
        elif args.command == "style":
            # 二選一而不是疊加:同時給等於呼叫端有 bug,不猜意圖直接擋。
            if args.no_bg and args.palette:
                raise ThemeError("--no-bg 與 --palette 互斥")
            if args.no_bg:
                print(no_bg_style(theme, args.role, args.state))
            elif args.palette:
                print(override_style(theme, args.role, args.state, args.palette))
            else:
                print(pane_style(theme, args.role, args.state))
        else:
            sys.stdout.write(apply_panel_contrast(sys.stdin.read(), theme,
                                                  args.role, args.state))
    except ThemeError as err:
        print(str(err), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
