#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""262(c):PAGE 助理憑證在 AIB 這一端的存放。

規格正本 `docs/spec/PAGE-WRITE-API-v1.md` §3.2:
  - 明碼**只落這一份**:`<狀態根>/auth/page/<tenant_id>.<page_id>.json`,權限 0600。
  - 憑證是 PAGE 發的,AIB 只存引用;PAGE 端撤銷即失效,這邊刪掉檔案也失效。

★ 狀態根不自己算:用 `seats_dir_lib.state_root()`(ED.1 順位 EZ2_AIB_STATE_ROOT >
  EZ2_AIB_HOME > ~/.ez2-aib)。本倉已經有三份 `state_root()`,不再新增第四份。
★ 寫入不自己 open:用 `atomic_json.write_json()`,它的臨時檔是 0o600 且 rename 過去,
  所以最終檔案就是 0600 —— 這是規格那把「權限鬆必紅」刀守的東西。
★ `tenant_id` / `page_id` 進檔名之前一定要過 `ID_RE`:規格的 ID_RE 不含 `/`,
  所以擋得住 `../` 逃逸。**不要**改成「先 join 再 realpath 比對」——那是事後補救,
  這裡是根本不讓它組出來。
"""
from __future__ import annotations

import json
import os
import re
import fcntl
import socket
import uuid

import atomic_json
from seats_dir_lib import state_root

# 規格 §一:三份契約同一條 ID_RE。
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")

TOKEN_DIR_PARTS = ("auth", "page")


class StoreError(Exception):
    """存取憑證檔失敗。一律拋出,不靜默 —— 呼叫端要決定是重新配對還是停手。"""


class MachineIdentityMissing(StoreError):
    """這台綁過(憑證帶 machine_id)但 machine.json 不見了:不自動生新身分(@74e8b 10-21 需求)。

    防手滑、不是防線:AI 刪得掉 machine.json 也刪得掉憑證。要重來 ⇒ 先 aib page unbind(--local-only)再綁。
    """
    code = "page_machine_identity_missing"


def _check_id(name, value):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise StoreError(
            "%s 不合規格的 ID_RE(收到 %r);不把它組進檔名" % (name, value))
    return value


def token_dir(environ=None):
    """(目錄, 狀態根來源)。來源字串照 ED.1 D2:每個入口要答得出根是哪來的。"""
    root, source = state_root(environ)
    return os.path.join(root, *TOKEN_DIR_PARTS), source


def token_path(tenant_id, page_id, environ=None):
    _check_id("tenant_id", tenant_id)
    _check_id("page_id", page_id)
    directory, source = token_dir(environ)
    return os.path.join(directory, "%s.%s.json" % (tenant_id, page_id)), source


def save_token(record, environ=None):
    """把 `/bind/poll` 回的 bound 記錄落檔(0600),回 (路徑, 來源)。

    只存規格 §3.3 回的那幾欄 + 綁定時刻;不加工、不改名。
    """
    for key in ("assistant_token", "tenant_id", "page_id"):
        if not record.get(key):
            raise StoreError("bound 記錄缺 %s,不落檔(半份憑證比沒有更難查)" % key)
    path, source = token_path(record["tenant_id"], record["page_id"], environ)
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise StoreError("憑證目錄不可用:%s" % exc)
    try:
        atomic_json.write_json(path, record)
    except atomic_json.AtomicWriteError as exc:
        raise StoreError("憑證寫入失敗:%s" % exc)
    # write_json 的臨時檔是 0o600;這一行是**第二道**,並且讓權限成為這支函式
    # 自己保證的事(刀對準的是結果不是實作)。
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise StoreError("憑證權限收不緊:%s" % exc)
    return path, source


def load_token(tenant_id, page_id, environ=None):
    """回憑證記錄;檔案不存在回 None(「還沒綁」不是錯誤)。"""
    path, _source = token_path(tenant_id, page_id, environ)
    return _read(path)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StoreError("憑證檔讀不動(%s):%s" % (path, exc))
    if not isinstance(record, dict):
        raise StoreError("憑證檔不是物件:%s" % path)
    return record


def list_tokens(environ=None):
    """回這台機器上所有已綁的頁(依檔名排序)。目錄不存在回空陣列。"""
    directory, _source = token_dir(environ)
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StoreError("憑證目錄列不出來:%s" % exc)
    out = []
    for name in names:
        if not name.endswith(".json") or name == "machine.json":
            continue
        record = _read(os.path.join(directory, name))
        if record is not None:
            out.append(record)
    return out


def machine_identity(environ=None):
    """One stable local UUID; the serial is never part of this record."""
    directory, _source = token_dir(environ)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    machine_path = os.path.join(directory, "machine.json")
    lock = os.open(os.path.join(directory, ".machine.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = _read(machine_path)
        if record is None:
            if any(r.get("machine_id") for r in list_tokens(environ)):
                raise MachineIdentityMissing(
                    "這台綁過但機器識別檔 machine.json 不見了,不自動換新身分;"
                    "先跑 aib page unbind(或 --local-only)清掉舊憑證再綁")
            record = {"id": str(uuid.uuid4()), "name": socket.gethostname()[:64] or "AIB"}
            atomic_json.write_json(machine_path, record)
        try:
            uuid.UUID(record["id"])
            if not isinstance(record.get("name"), str) or not 1 <= len(record["name"]) <= 64:
                raise ValueError("name")
        except (KeyError, ValueError, TypeError, AttributeError):
            raise StoreError("machine.json 格式不合規,保留原檔並停止綁定")
        os.chmod(machine_path, 0o600)
        return {"id": record["id"], "name": record["name"]}
    except (OSError, atomic_json.AtomicWriteError) as exc:
        raise StoreError("機器識別檔不可用:%s" % exc)
    finally:
        os.close(lock)


def only_token(environ=None):
    """剛好綁一頁時回那一份;零份回 None;多份拋錯要呼叫端指名。

    ★ 多份時**不挑一個**:規格 §3.2 一把 token 一頁,猜錯頁面會把改動寫到別人的頁上。
    """
    records = list_tokens(environ)
    if not records:
        return None
    if len(records) > 1:
        raise StoreError(
            "這台機器綁了 %d 頁,要用 --tenant/--page 指名(不猜):%s"
            % (len(records),
               ", ".join("%s.%s" % (r.get("tenant_id"), r.get("page_id"))
                         for r in records)))
    return records[0]


def forget(tenant_id, page_id, environ=None):
    """刪掉本機那一份(PAGE 端仍要另外解除;這只是本機失效)。回有沒有刪到。"""
    path, _source = token_path(tenant_id, page_id, environ)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StoreError("憑證檔刪不掉:%s" % exc)
