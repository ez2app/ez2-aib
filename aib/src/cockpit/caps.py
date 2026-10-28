#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib seat caps [--json] [--write-vault]:這份核心有哪些能力(PROPOSAL-gaps v3 第 5 件;@74e8b @977e3 @9f808 @ee4f8)。

外掛 / 頁面照**穩定的 key** 決定某個熱點顯不顯示,不看版本號(@74e8b:版本號比對一改就壞)。
  {"v": 1, "aib_version": "0.1.36", "caps": ["chat.events", "seat.input", …]}
判法:每個 key 對一個「程式真的在」的檢查(檔在、函式在、常數裡有那個值),不 import 任何模組(不會有副作用),
⇒ 後面的 PR 合進來,key 自己就亮,不用另外記得改這張表(但新功能要在 CAPS 加一列)。
規矩(@ee4f8):
  - caps 只給本機頁面,**不經 wire 回遠端**(防掃版本找洞);
  - 只決定「顯不顯示」,授權 / Touch ID 照舊另查 —— 有 key 不代表可以做。
--write-vault:另寫一份靜態檔 <EZ2_VAULT_DIR>/.ez2/caps.json 給 Obsidian 外掛(含手機,@977e3);沒設 EZ2_VAULT_DIR ⇒ rc 2 不猜。
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent           # aib/
V = 1


def _has_file(rel):
    return lambda: (HERE / rel).is_file()


def _has_def(rel, name):
    pat = re.compile(r"^(?:async\s+)?def %s\(" % re.escape(name), re.M)

    def chk():
        try:
            return bool(pat.search((HERE / rel).read_text(encoding="utf-8")))
        except OSError:
            return False
    return chk


def _has_text(rel, needle):
    def chk():
        try:
            return needle in (HERE / rel).read_text(encoding="utf-8")
        except OSError:
            return False
    return chk


# key ⇒ 檢查。key 一旦發出去就不改名(頁面靠它);功能拿掉 ⇒ 檢查自然變 False,key 就不出現。
CAPS = {
    "seat.transcript": _has_def("seat_panel.py", "transcript"),
    "seat.pending": _has_def("seat_panel.py", "pending"),
    "seat.input": _has_def("seat_act.py", "do_input"),
    "seat.answer": _has_def("seat_act.py", "do_answer"),
    "seat.stop": _has_def("seat_act.py", "do_stop"),
    "seat.unlock": _has_def("seat_act.py", "do_unlock"),
    "seat.remote": _has_def("seat_act.py", "do_remote"),
    "seat.attach": _has_file("seat_attach.py"),
    "seat.caps": _has_file("caps.py"),
    "chat.notice.usage_reset": _has_text("seat_panel.py", '("usage_reset"'),
    "chat.notice.login_ok": _has_text("seat_panel.py", '("login_ok"'),
    "chat.notice.login_needed": _has_text("seat_panel.py", '("login_needed"'),
    "chat.events": _has_file("room_events.py"),
    "chat.open": _has_file("chat_visit.py"),
    "chat.visit": _has_file("chat_visit.py"),
    "chat.cast": _has_file("chat_cast.py"),
    "chat.open.touchid": _has_def("chat_bridge.py", "human_gate"),
    "chat.address": _has_def("chat_bridge.py", "address_payload"),
    "chat.boot": _has_def("chat_bridge.py", "install_boot"),
    "chat.recent": _has_def("chat_bridge.py", "recent_payload"),
    "chat.recent.receipt": _has_def("chat_bridge.py", "last_receipt"),
    "chat.receipt.still_waiting": _has_text("dm_global_files.py", '"still_waiting"'),
    "chat.receipt.read_by_human": _has_text("chat_nostr.py", '"aib-read-by"'),
    "chat.telegram": _has_file("chat_telegram.py"),
    "page.bind.bg": _has_file("page/bind_bg.py"),
    "room.reload": _has_file("room_reload.py"),
    "chrome.orphans": _has_file("chrome_orphans.py"),
}


def aib_version(root=None):
    """aib/VERSION 是唯一版本來源(aib 入口註明;PyPI 裝的樹沒有 pyproject);pyproject 只當退路。"""
    root = Path(root) if root else ROOT
    try:
        v = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        v = ""
    if v:
        return v
    for p in (root.parent / "pyproject.toml", root / "pyproject.toml"):
        try:
            m = re.search(r'^version\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"), re.M)
        except OSError:
            continue
        if m:
            return m.group(1)
    return None


def payload():
    return {"v": V, "aib_version": aib_version(), "caps": sorted(k for k, chk in CAPS.items() if chk())}


def vault_path(env=None):
    e = os.environ if env is None else env
    v = e.get("EZ2_VAULT_DIR")
    return Path(v).expanduser() / ".ez2" / "caps.json" if v else None


def write_vault(pl, env=None):
    import atomic_json
    p = vault_path(env)
    if p is None:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_json.write_json(str(p), pl)
    return p


def main(argv=None, out=None, env=None):
    out = out or sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        out.write("usage: aib seat caps [--json] [--write-vault]\n")
        return 0
    bad = [a for a in argv if a not in ("--json", "--write-vault")]
    if bad:
        out.write("aib seat caps: unknown argument %s\n" % " ".join(bad))
        return 2
    pl = payload()
    if "--write-vault" in argv:
        if HERE not in map(Path, sys.path):
            sys.path.insert(0, str(HERE))
        p = write_vault(pl, env)
        if p is None:
            out.write(json.dumps({"v": V, "ok": False, "why": "no-vault"}) + "\n")
            return 2
    if "--json" in argv:
        out.write(json.dumps(pl, ensure_ascii=False) + "\n")
    else:
        out.write("aib %s\n" % (pl["aib_version"] or "?"))
        for k in pl["caps"]:
            out.write("  " + k + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
