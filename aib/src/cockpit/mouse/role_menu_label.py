#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""另開 AI 第二層的顯示名:圖示 + 字典角色名。

圖示單源是 pane-right-ai.sh 的 ROLE_ICONS,不在這裡另抄一份。
root 家族不在那張表(那裡的 👑 是現任艦長主張);選單開的是艦長席,用同一枚 👑。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COCKPIT = HERE.parent
if str(COCKPIT) not in sys.path:
    sys.path.insert(0, str(COCKPIT))

import i18n_lib  # noqa: E402

PANE_RIGHT = COCKPIT / "pane-right-ai.sh"
ROOT_MENU_ICON = "\U0001F451"  # 👑,與右欄現任艦長同一枚


def role_family(role):
    if not isinstance(role, str) or not role:
        return role
    i = len(role)
    while i > 0 and role[i - 1].isascii() and role[i - 1].isdigit():
        i -= 1
    return role[:i] or role


def role_icons():
    src = PANE_RIGHT.read_text(encoding="utf-8")
    m = re.search(r"^ROLE_ICONS = (\{.*?\})$", src, re.S | re.M)
    if not m:
        return {}
    icons = ast.literal_eval(m.group(1))
    return icons if isinstance(icons, dict) else {}


def display_for(role, icons, locale=None):
    fam = role_family(role)
    if fam == "root":
        icon = ROOT_MENU_ICON
    else:
        icon = icons.get(role) or icons.get(fam) or ""
    label = i18n_lib.t("aib.role.%s" % fam, locale=locale)
    if label.startswith("!!"):
        label = role
    text = ("%s %s" % (icon, label)).strip()
    return text or role


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    icons = role_icons()
    for role in argv:
        sys.stdout.write("%s\t%s\n" % (role, display_for(role, icons)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
