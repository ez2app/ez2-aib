#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""「長訊已落檔」指標信的格式(10-08 從 pending_edit 搬到核心:訊息待審做成收費外掛後,核心的 dm_text 仍要認得指標信)。"""
import os
import re
import stat

# 10-15 B9:背景分身寄的長訊指標是「來自 @五碼 的背景分身」(sender_origin.relay_pointer_line),原本認不出 ⇒ 指標信當普通短訊。
RELAY_RE = re.compile(
    r"^長訊已落檔\((\d+) 位元組(?:,來自 @[0-9a-z]+(?: 的背景分身)?)?\),請讀:(.+)\s*$"
)


def relay_path_of(msg):
    m = RELAY_RE.match(str(msg or "").strip())
    if not m:
        return None
    path = m.group(2).strip()
    if not path.startswith("/"):
        return None
    return path


_RELAY_NAME_RE = re.compile(r"^RELAY-dm-[A-Za-z0-9_-]+\.txt$")


def relay_dirs(env=None):
    """長訊檔只會落在這裡(dm-ctl:AIB_DM_RELAY_DIR,沒設就 <EZ2_AIB_HOME 或 ~/.ez2-aib>/pending)。"""
    e = os.environ if env is None else env
    home = e.get("EZ2_AIB_HOME") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".ez2-aib")
    out = []
    for d in (e.get("AIB_DM_RELAY_DIR"), os.path.join(home, "pending")):
        if d:
            try:
                out.append(os.path.realpath(d))
            except (OSError, ValueError):
                pass
    return out


def relay_file_ok(path, env=None):
    """指標信寫的路徑是不是真的長訊檔:realpath 後在 relay_dirs 底下、檔名是 RELAY-dm-*.txt。
    #370 r1 M2:不然短訊寫「長訊已落檔(…),請讀:<任意檔>」就能讓待審框 / 原話一行露出那檔開頭。"""
    try:
        rp = os.path.realpath(path)
    except (OSError, ValueError, TypeError):
        return False
    return bool(_RELAY_NAME_RE.match(os.path.basename(rp))) and os.path.dirname(rp) in relay_dirs(env)


def relay_head(path, limit=2048, env=None):
    """長訊檔開頭(只讀前 limit 位元組、一般檔、不阻塞)。不是長訊檔 / 讀不到 ⇒ None。
    待審框每幀畫面、原話一行(dm_text.original_line)共用這一支,FIFO / 很大的檔都不會卡住(#370 L-new4)。"""
    if not relay_file_ok(path, env):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, limit)
    except OSError:
        return None
    finally:
        os.close(fd)
    return raw.decode("utf-8", "ignore")
