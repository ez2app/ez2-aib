#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""主人格(真人分身)登記:aib persona main [show | set <uid> | clear](維護者 10-16 13:1x 親裁;@abb89 派)。

寫 <狀態根>/identity/user-main-persona.json = {"persona_uid": …, "set_at": …, "by": "aib persona main"}。
讀的人:memory_tidy(冠位大變動保護、任何一席把真人親打的話靜默精煉進這張)、persona_pack(主人格席帶主人格檔)。
這個檔原本只有人讀、沒有程式寫(只能手寫 JSON);這裡補正式入口。
set 只收登記表(identity/cards.json)裡有的卡;clear 只刪這個檔。
rc:0 成功;1 卡不在登記表;2 用法錯。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "cockpit", HERE.parent / "memory"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def state_root():
    import memory_archive
    return memory_archive.state_root()


def main_path():
    return state_root() / "identity" / "user-main-persona.json"


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return default


def cards():
    """登記表 {uid: 顯示名}。"""
    rows = (_read_json(state_root() / "identity" / "cards.json", {}) or {}).get("cards") or {}
    return {u: (r.get("display_name") or u) for u, r in rows.items() if isinstance(r, dict)}


def get_main():
    """登記的主人格 uid;沒登記 ⇒ ""。"""
    d = _read_json(main_path(), {})
    uid = d.get("persona_uid") if isinstance(d, dict) else ""
    return uid if isinstance(uid, str) else ""


def set_main(uid):
    """→ (ok, 名稱)。卡不在登記表 ⇒ (False, "")。"""
    known = cards()
    if uid not in known:
        return False, ""
    p = main_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps({"persona_uid": uid, "set_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                               "by": "aib persona main"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return True, known[uid]


def clear_main():
    """→ 原本登記的 uid(沒有 ⇒ "")。"""
    uid = get_main()
    try:
        main_path().unlink()
    except FileNotFoundError:
        pass
    return uid


def _t(key, fallback, **params):
    try:
        import i18n_lib
        text = i18n_lib.t(key, params or None)
    except Exception:  # noqa: BLE001
        text = None
    if not isinstance(text, str) or not text or text == key or (text.startswith("!!") and text.endswith("!!")):
        text = fallback.format(**params) if params else fallback
    return text


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "show"
    if cmd in ("show", "看") and len(args) <= 1:
        uid = get_main()
        if not uid:
            print(_t("aib.persona_main.none", "主人格:沒登記(登記:aib persona main set <卡號>)"))
        else:
            print(_t("aib.persona_main.show", "主人格:{name}({uid})", name=cards().get(uid, "?"), uid=uid))
        return 0
    if cmd in ("set", "設") and len(args) == 2:
        ok, name = set_main(args[1])
        if not ok:
            print(_t("aib.persona_main.unknown", "卡號 {uid} 不在人格卡登記表,沒改", uid=args[1]), file=sys.stderr)
            return 1
        print(_t("aib.persona_main.set", "主人格登記為 {name}({uid})", name=name, uid=args[1]))
        return 0
    if cmd in ("clear", "清") and len(args) == 1:
        old = clear_main()
        print(_t("aib.persona_main.cleared", "主人格登記已清除(原本:{uid})", uid=old or "-"))
        return 0
    print(_t("aib.persona_main.usage", "用法:aib persona main [show | set <卡號> | clear]"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
