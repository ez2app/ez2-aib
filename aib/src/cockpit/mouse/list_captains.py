#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""列出這一艙活著的艦長,給另開 AI 第三層選「掛在誰底下」。

排序對齊右欄:受派優先,再 started。第一位=預設掛點。
讀不到席位目錄或沒有艦長=零行,選單就跳過這一層。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COCKPIT = HERE.parent
if str(COCKPIT) not in sys.path:
    sys.path.insert(0, str(COCKPIT))

import i18n_lib  # noqa: E402

ROOT_ICON = "\U0001F451"  # 👑
VICE_ICON = "⚓"
API_KEY_VICE_ICON = "\U0001F511"  # 🔑；與 pane-right-ai API_KEY_VICE_MARK 同裁
# aibot 橋接席(sand/grokbot（Cursor）與 codexbot（Desktop）):第三種段位圖 🤖,與右欄 pane-right 同裁
AIBOT_ICON = "\U0001F916"  # 🤖
AIBOT_PROVIDERS = frozenset(("sand", "grokbot", "codexbot", "claudebot", "musebot"))
# 與 providers.json auth=api_key / pane-right-ai API_KEY_PROVIDERS 對齊
API_KEY_PROVIDERS = frozenset(("deepseek", "nvidia", "openrouter", "typesafe", "openrouterjev"))


def role_family(role):
    if not isinstance(role, str) or not role:
        return role
    i = len(role)
    while i > 0 and role[i - 1].isascii() and role[i - 1].isdigit():
        i -= 1
    return role[:i] or role


def seats_dir():
    """席位登記簿。順位:顯式測試縫 EZ2_SEATS_DIR > **HUB 導出**(單一真相源 seats_dir_lib)> 主艙預設。
    09-11 test-seats-dir-resolution 紅:本檔原本只認 EZ2_AIB_INSTANCE_HOME,多艙環境會退回主艙
    拿到格式正確的錯答案(判準與前科見 seat_onboard.seats_dir docstring)。"""
    env = os.environ.get("EZ2_SEATS_DIR") or ""
    if env:
        return env
    hub = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if hub and hub.startswith("aihub-") and hub[len("aihub-"):]:
        from seats_dir_lib import seats_dir as _sd   # noqa: E402  COCKPIT 已在 sys.path
        return str(_sd())
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME") or ""
    return os.path.join(home, "seats") if home else ""


def is_captain(rec):
    if rec.get("advisor") is True:
        return False
    if rec.get("captain_assigned") is True:
        return True
    role = rec.get("role") or ""
    return role_family(role) == "root"


def label_of(rec):
    sid = rec.get("seat_id") if isinstance(rec.get("seat_id"), str) else ""
    uuid = rec.get("session_uuid") if isinstance(rec.get("session_uuid"), str) else ""
    code = uuid[-5:] if len(uuid) >= 5 else sid
    role = rec.get("role") or "root"
    fam = role_family(role) or "root"
    name = i18n_lib.t("aib.role.%s" % fam)
    if name.startswith("!!"):
        name = fam
    if code:
        return "%s @%s" % (name, code)
    return name


def list_captains(directory=None):
    d = directory if directory is not None else seats_dir()
    if not d or not os.path.isdir(d):
        return []
    rows = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in names:
        if not (name.startswith("w") and name.endswith(".json")):
            continue
        path = os.path.join(d, name)
        try:
            rec = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
            continue
        if not is_captain(rec):
            continue
        sid = rec.get("seat_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-z]{4,8}", sid):
            continue
        started = rec.get("started_epoch")
        try:
            started = int(started)
        except (TypeError, ValueError):
            started = 0
        rows.append((
            bool(rec.get("unassigned")),
            not (rec.get("captain_assigned") is True),
            started,
            sid,
            rec,
        ))
    rows.sort()
    out = []
    for i, (_u, _a, _s, sid, rec) in enumerate(rows):
        provider = (rec.get("provider") or "").strip().lower()
        if provider in AIBOT_PROVIDERS:
            icon = AIBOT_ICON
        elif i == 0:
            icon = ROOT_ICON
        elif provider in API_KEY_PROVIDERS:
            icon = API_KEY_VICE_ICON
        else:
            icon = VICE_ICON
        out.append((sid, "%s %s" % (icon, label_of(rec))))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = argv[0] if argv else None
    for sid, disp in list_captains(directory):
        sys.stdout.write("%s\t%s\n" % (sid, disp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
