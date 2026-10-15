#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""安裝時在桌面放「AIB 第二大腦」捷徑,打開就能管筆記與設定。

捷徑指向狀態根底下的 user/ 入口,不把整棵 ~/.ez2-aib 攤在桌面。
不寫 vault。桌面目錄不存在就跳過,不擋 init。
測試用 EZ2_AIB_DESKTOP_DIR 指向暫存桌面,不得寫真人 Desktop。

10-15(稽核 C5,Andy 裁):放桌面前先問(ask 回 True 才放);沒有人可問(安裝程式裡、非終端機)⇒ 不放,留一行說明。
捷徑名字跟使用者的語言(desktop.shortcut_name 四語);桌面上已經有連到入口的捷徑(不管叫什麼)⇒ 不再問、不改名。
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_COCKPIT = str(Path(__file__).resolve().parents[1] / "cockpit")
if _COCKPIT not in sys.path:
    sys.path.append(_COCKPIT)

DIR_MODE = 0o700
FILE_MODE = 0o600
SHORTCUT_NAME = "AIB 第二大腦"          # 舊名(zh-TW);新的照語言取 shortcut_name()
HUB_REL = "user"

HUB_README = (
    "這是 AIB 的管理入口。\n"
    "「第二大腦」是你的筆記與記憶;「設定」裡是金鑰、連線與人格。\n"
    "資料在本機 AIB 目錄,刪掉桌面上的捷徑不會刪資料。\n"
    "金鑰資料夾只有你能看。\n"
)

SETTINGS_README = (
    "金鑰與連線:各平台的 token,一行一隻。\n"
    "人格:角色卡與綁定。\n"
)


def T(key, params=None):
    """使用者看得到的字走 i18n;字典讀不到 ⇒ 英文退路。"""
    try:
        import i18n_lib
        out = i18n_lib.t(key, params or {})
        if out and out != key:
            return out
    except Exception:  # noqa: BLE001
        pass
    return {"desktop.shortcut_name": "AIB Second Brain"}.get(key, key)


def shortcut_name():
    return T("desktop.shortcut_name")


def _item(path, action, detail=""):
    return {"path": str(path), "action": action, "mode": None, "detail": detail,
            "remedy": ""}


def desktop_dir(env=None):
    env = os.environ if env is None else env
    override = env.get("EZ2_AIB_DESKTOP_DIR")
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / "Desktop"


def _mkdir(path, items):
    path = Path(path)
    if path.is_symlink():
        items.append(_item(path, "fail", "是 symlink,不跟隨"))
        items[-1]["remedy"] = "移走該捷徑後重跑 aib init"
        return False
    if path.exists():
        if not path.is_dir():
            items.append(_item(path, "fail", "已存在但不是目錄"))
            items[-1]["remedy"] = "移走該檔後重跑 aib init"
            return False
        mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
        if mode != DIR_MODE:
            os.chmod(str(path), DIR_MODE)
            items.append(_item(path, "fixed", "0700"))
        else:
            items.append(_item(path, "ok", "dir"))
        return True
    try:
        os.mkdir(str(path), DIR_MODE)
        os.chmod(str(path), DIR_MODE)
    except OSError as err:
        items.append(_item(path, "fail", str(err)[:120]))
        items[-1]["remedy"] = "確認上層目錄可寫後重跑 aib init"
        return False
    items.append(_item(path, "created", "0700"))
    return True


def _write_if_absent(path, text, items):
    path = Path(path)
    if path.exists():
        items.append(_item(path, "ok", "既有檔,不覆寫"))
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    items.append(_item(path, "created", "0600"))


def _rel_symlink(link, target, items):
    """link -> target,用相對路徑,既有正確捷徑不改。"""
    link = Path(link)
    target = Path(target)
    want = os.path.relpath(str(target), str(link.parent))
    if link.exists() or link.is_symlink():
        if link.is_symlink() and os.readlink(str(link)) == want:
            items.append(_item(link, "ok", "捷徑"))
            return True
        if link.is_symlink():
            os.remove(str(link))
        else:
            items.append(_item(link, "fail", "此處已有檔,不覆蓋"))
            items[-1]["remedy"] = "移走或改名後重跑 aib init"
            return False
    os.symlink(want, str(link))
    items.append(_item(link, "created", "捷徑 -> %s" % want))
    return True


def existing_shortcut(desk, hub):
    """桌面上已經連到入口的捷徑(任何名字;認目標)⇒ Path 或 None。"""
    want = os.path.normpath(str(hub))
    try:
        entries = sorted(Path(desk).iterdir())
    except OSError:
        return None
    for p in entries:
        if p.is_symlink() and os.path.normpath(os.path.join(str(p.parent), os.readlink(str(p)))) == want:
            return p
    return None


def ensure_desktop_hub(state_root, env=None, ask=None):
    """建 user/ 入口 + 桌面捷徑。回與 aib_init 同形的 items。
    ask(問題字串) → bool:放桌面捷徑前問人;None = 沒有人可問 ⇒ 不放,留一行說明。"""
    env = os.environ if env is None else env
    root = Path(state_root)
    items = []
    if not root.is_dir():
        items.append(_item(root, "ok", "狀態根不是目錄,捷徑略過"))
        return items
    hub = root / HUB_REL
    memory = root / "memory"
    secrets = root / "secrets"
    identity = root / "identity"
    settings = hub / "設定"
    for d in (memory, secrets, identity, hub, settings):
        if not _mkdir(d, items):
            return items
    _write_if_absent(hub / "請先讀我.txt", HUB_README, items)
    _write_if_absent(settings / "請先讀我.txt", SETTINGS_README, items)
    _rel_symlink(hub / "第二大腦", memory, items)
    _rel_symlink(settings / "金鑰與連線", secrets, items)
    _rel_symlink(settings / "人格", identity, items)

    desk = desktop_dir(env)
    if not desk.is_dir():
        items.append(_item(desk, "ok", "沒有桌面目錄,捷徑略過"))
        return items
    have = existing_shortcut(desk, hub)
    if have is not None:
        items.append(_item(have, "ok", "捷徑"))
        return items
    shortcut = desk / shortcut_name()
    if ask is None:
        items.append(_item(shortcut, "ok", T("desktop.skipped_noninteractive")))
        return items
    if not ask(T("desktop.ask", {"name": shortcut.name})):
        items.append(_item(shortcut, "ok", T("desktop.declined")))
        return items
    _rel_symlink(shortcut, hub, items)
    return items
