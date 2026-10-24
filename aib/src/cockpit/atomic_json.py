#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""JSON 記錄的原子寫入器 —— 多寫者共用一個目錄時的正確寫法。

## 由來(2026-08-16,the maintainer 直令「找出該程式中適合整個 aib 的地方,重構回來」)

舊載體的心跳寫入器是一支 1596 行的 registry 寫入器,其中 1503 行是內嵌
Python。逐段讀完之後,它的內容分成三類:

  舊身份模型專屬    binding_schema_version / authority anchor /
                    registry_verified / session-binding/v2
                    ★ **不搬**:那套 BIND 已判廢除,搬回來是把死掉的身份層復活,
                      而且直接牴觸 AIB 的紅線「直起路徑不得自稱 verified」。
  AIB 已經有        session uuid / role / engine / started / 狀態機
                    ★ **不搬**:席位檔就是這份資料,而且更新鮮。
                      搬 = 建第二真相源,那是本 repo 的紅線。
  **寫入健壯性**     原子寫、寫入鎖、大小上限、路徑防護、寫後驗證
                    ★★ **這一類值得搬,而且 AIB 現在真的缺。**

## AIB 現況與缺口(重構前的 `aib:write_seat_record`)

    tmp = path + ".tmp"        ← **固定檔名**:兩席同時寫會互相蓋
    open(tmp, "w")             ← 沒有 O_EXCL
    os.replace(tmp, path)      ← 這點是對的(rename 在同檔案系統上是原子的)
                               ← 沒有 fsync:當機時可能是空檔或半截
                               ← 沒有鎖:read-modify-write 之間有競態窗
                               ← 沒有寫後驗證

★ 這不是理論風險。席位檔是**多寫者**的:開席時寫、`captains-ctl --sync` 時寫,
  而 08-16 起 `--sync` 掛成**每 5 秒一次的心跳**。固定的 `.tmp` 檔名在兩席同時
  寫時會互相蓋掉 —— 而症狀是「席位檔內容變成另一席的」,不會有任何錯誤訊息。
★ 同型事故本 repo 已記過:08-16 早上「有人在臨時樹 merge 洗掉別人未提交的
  修正,靠測試數 192→191 才發現」。**後寫覆蓋不會叫,只會安靜地贏。**

## 這裡刻意**沒有**照抄的部分

  - `O_NOFOLLOW` / uid 檢查 / 目錄穩定化輪詢:那是舊載體的**多使用者威脅模型**
    (registry 可能被同機他人竄改)。AIB 的席位目錄在自己的 home 底下,
    加這一層會讓失敗模式變多而擋不到真實風險。**不是做不到,是不值得。**
  - `binding_schema_version` 那類欄位驗證:那是身份模型,見上。

★ 若哪天 AIB 的狀態檔要跨使用者共享,再把那一層加回來 —— **並且要在那時候
  寫下「為什麼現在需要」,不要因為舊載體有就抄。**
"""

import errno
import fcntl
import json
import os
import secrets
import time

# 單筆記錄的上限。超過幾乎一定是程式錯誤(例如把整份 log 塞進欄位),
# 而不是正常成長 —— 寧可寫入失敗並出聲,也不要把狀態檔撐爆。
MAX_RECORD_BYTES = 64 * 1024
LOCK_TIMEOUT_S = 10.0


class AtomicWriteError(Exception):
    """寫入失敗。★ 一律拋出、不靜默吞掉——呼叫端才知道要不要降級。"""


def _acquire(dir_fd, timeout_s=LOCK_TIMEOUT_S):
    """對目錄取獨佔鎖,忙碌時退避重試。

    鎖在**目錄**上而不是檔案上:read-modify-write 的競態窗涵蓋「讀舊值」到
    「rename 完成」整段,而那段期間目標檔的 inode 會換掉——鎖檔案鎖不住它。
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise AtomicWriteError("鎖定失敗:%s" % exc.__class__.__name__)
            if time.monotonic() >= deadline:
                raise AtomicWriteError("鎖定逾時(%.0f 秒):可能有寫者卡住"
                                       % timeout_s)
            time.sleep(0.01)


def write_json(path, value):
    """把 `value` 原子地寫進 `path`。失敗拋 AtomicWriteError。

    保證:**讀者永遠讀到完整的舊值或完整的新值**,不會讀到半截。
    """
    directory, name = os.path.split(os.path.abspath(path))
    if not name:
        raise AtomicWriteError("路徑沒有檔名:%r" % (path,))
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n") \
        .encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise AtomicWriteError("記錄 %d bytes 超過上限 %d"
                               % (len(payload), MAX_RECORD_BYTES))
    try:
        os.makedirs(directory, exist_ok=True)
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        raise AtomicWriteError("目錄不可用:%s" % exc.__class__.__name__)
    # ★ 臨時檔名帶 pid + 隨機碼:固定的 `.tmp` 會讓兩個寫者用同一個中繼檔,
    #   後到的把先到的內容蓋掉,而 rename 之後看起來完全正常。
    temp = ".%s.tmp.%d.%s" % (name, os.getpid(), secrets.token_hex(6))
    fd = None
    try:
        _acquire(dir_fd)
        # O_EXCL:臨時檔名撞到就失敗,不接手別人的檔。
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                     dir_fd=dir_fd)
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise AtomicWriteError("寫入中斷")
            offset += written
        os.fsync(fd)                    # 資料落地,才 rename
        os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        temp = None                     # rename 成功,沒有殘檔要清
        os.fsync(dir_fd)                # 目錄項也落地,當機後 rename 不會消失
    except AtomicWriteError:
        raise
    except OSError as exc:
        raise AtomicWriteError("原子寫入失敗:%s" % exc.__class__.__name__)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp is not None:            # 失敗路徑:清掉殘檔,不留垃圾
            try:
                os.unlink(temp, dir_fd=dir_fd)
            except OSError:
                pass
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(dir_fd)


def update_json(path, mutate, default=None):
    """read-modify-write,**全程持鎖**。`mutate(rec)` 回傳要寫入的字典。

    ★ 這是本模組存在的主要理由。`write_json` 只保證單次寫入是原子的,擋不住
      「A 讀舊值 → B 讀舊值 → A 寫 → B 寫」把 A 的修改吃掉。那個競態在席位檔
      上是真的:開席寫一次、每 5 秒的艦長列心跳寫一次。
    """
    directory, name = os.path.split(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        raise AtomicWriteError("目錄不可用:%s" % exc.__class__.__name__)
    try:
        _acquire(dir_fd)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = {} if default is None else dict(default)
        new = mutate(rec)
        if not isinstance(new, dict):
            raise AtomicWriteError("mutate 必須回傳 dict,得到 %r" % type(new))
        payload = (json.dumps(new, ensure_ascii=False, sort_keys=True) + "\n") \
            .encode("utf-8")
        if len(payload) > MAX_RECORD_BYTES:
            raise AtomicWriteError("記錄 %d bytes 超過上限 %d"
                                   % (len(payload), MAX_RECORD_BYTES))
        temp = ".%s.tmp.%d.%s" % (name, os.getpid(), secrets.token_hex(6))
        fd = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                         dir_fd=dir_fd)
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise AtomicWriteError("寫入中斷")
                offset += written
            os.fsync(fd)
            os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            temp = None
            os.fsync(dir_fd)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temp is not None:
                try:
                    os.unlink(temp, dir_fd=dir_fd)
                except OSError:
                    pass
        return new
    except AtomicWriteError:
        raise
    except OSError as exc:
        raise AtomicWriteError("原子更新失敗:%s" % exc.__class__.__name__)
    finally:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(dir_fd)
