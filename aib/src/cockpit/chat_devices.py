#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""同一位主人、多台機器(開發文件第 37.7 節 K1):機器清單 + 機器名冊 + 查人合併。

只有判斷邏輯,不連網、不 import nostr_sdk;抓事件與驗簽在 chat_nostr.resolve。

- 機器清單:kind 30078,d=aib-devices,**主人金鑰簽**。
  {"v":1, "owner":<主人 npub>, "devices":[{"npub","name","added_at"}…], "revoked":[{"npub","at"}…],
   "seq":<只增>, "at":<簽的時間>, "valid_until":<at + 至多 31 天>}
- 機器名冊:kind 30078,d=aib-roster:<機器碼>(機器 npub 去掉 npub1 後前 8 碼),機器金鑰簽,
  內容同名冊,owner 欄 = 主人(收件端回頭對清單,不信自報)。
- 收件端把「每位主人看過的最大 seq」存在 <root>/devices-seq.json(不隨 10 分鐘快取消失):
  比它小的清單一律不收;清單過了 valid_until ⇒ 只認主人金鑰簽的 d=aib-roster(防中繼站扣住新清單)。
- 合併:兩個來源宣告同一個艙碼 ⇒ 那個艙兩邊都不收、記警告(座位完全一樣的重複公告不算撞);
  合併後每個艙記下宣告它的機器,收信驗證時寄件席要在那台的艙裡。
"""
import fcntl
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
from atomic_json import write_json  # noqa: E402

VERSION = 1
DEVICES_D = "aib-devices"
ROSTER_PREFIX = CR.ROSTER_D + ":"
MAX_DEVICES = 16
MAX_REVOKED = 256
VALID_MAX_S = 31 * 86400                    # 簽時 + 30 天;多 1 天給時鐘誤差
CLOCK_SKEW_S = 600
_NPUB_RE = re.compile(r"^%s$" % GF.NPUB_RE)


class DevicesError(ValueError):
    """清單不合格;代碼在 str(err)。"""


def device_code(npub):
    """機器碼 = npub 去掉 npub1 後前 8 碼(只用來分 d 標籤,不拿來認人)。"""
    return npub[5:13]


def roster_d(npub):
    return ROSTER_PREFIX + device_code(npub)


def build_list(owner, devices, revoked=(), seq=1, at=None, valid_s=30 * 86400):
    """組一份清單(還沒簽;簽由持主人金鑰那台做)。"""
    import time
    at = int(time.time() if at is None else at)
    return {"v": VERSION, "owner": owner, "devices": [dict(d) for d in devices],
            "revoked": [dict(r) for r in revoked], "seq": int(seq), "at": at, "valid_until": at + int(valid_s)}


def parse_list(content, signer_npub, now):
    """主人簽的清單(簽章已由 nostr-sdk 驗)⇒ 驗過的 dict;任何一處不對 ⇒ DevicesError(整份不採)。"""
    try:
        rec = json.loads(content) if isinstance(content, (str, bytes)) else content
    except ValueError:
        raise DevicesError("not_json")
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise DevicesError("bad_version")
    if not _NPUB_RE.match(signer_npub or "") or rec.get("owner") != signer_npub:
        raise DevicesError("owner_mismatch")
    seq, at, until = rec.get("seq"), rec.get("at"), rec.get("valid_until")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (seq, at, until)) or seq < 1:
        raise DevicesError("bad_seq")
    if at > now + CLOCK_SKEW_S:
        raise DevicesError("from_future")
    if until <= at or until - at > VALID_MAX_S:
        raise DevicesError("bad_valid_until")
    devs, revs = rec.get("devices"), rec.get("revoked", [])
    if not isinstance(devs, list) or not isinstance(revs, list):
        raise DevicesError("bad_shape")
    if len(devs) > MAX_DEVICES or len(revs) > MAX_REVOKED:
        raise DevicesError("too_many")
    seen, codes = set(), set()
    for d in devs:
        if not isinstance(d, dict) or not _NPUB_RE.match(str(d.get("npub"))):
            raise DevicesError("bad_device")
        if d["npub"] == signer_npub:
            raise DevicesError("owner_in_devices")       # 持主人金鑰那台走 d=aib-roster,不列在清單裡
        if d["npub"] in seen or device_code(d["npub"]) in codes:
            raise DevicesError("duplicate_device")
        seen.add(d["npub"])
        codes.add(device_code(d["npub"]))
        if d.get("name") is not None and GF.clean_display_name(d["name"]) != d["name"]:
            d["name"] = None
    for r in revs:
        if not isinstance(r, dict) or not _NPUB_RE.match(str(r.get("npub"))):
            raise DevicesError("bad_revoked")
        if r["npub"] in seen:
            raise DevicesError("revoked_still_listed")
    rec["expired"] = now > until
    return rec


# ── 看過的最大 seq(持久)──

def seq_path(root):
    return Path(root) / "devices-seq.json"


def accept_seq(root, owner, seq):
    """這份清單的 seq 能不能收:比看過的最大值小 ⇒ False(舊清單重播);收下就把最大值記下。
    讀-判-寫整段在 fcntl 鎖底下(同機多個行程同時查人也不會把最大值寫回去變小)。"""
    p = seq_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            seen = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(seen, dict):
                seen = {}
        except (OSError, ValueError):
            seen = {}
        best = seen.get(owner)
        best = best if isinstance(best, int) and not isinstance(best, bool) else 0
        if seq < best:
            return False
        if seq > best:
            seen[owner] = seq
            write_json(str(p), seen)
            os.chmod(str(p), 0o600)
        return True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def seen_seq(root, owner):
    try:
        v = json.loads(seq_path(root).read_text(encoding="utf-8")).get(owner)
    except (OSError, ValueError, AttributeError):
        return 0
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


# ── 查人合併 ──

def usable_list(root, owner, ev_content, signer_npub, now):
    """抓到的清單 ⇒ (可用的清單或 None, 原因)。原因:none / bad:<代碼> / stale_seq / expired / ok。
    不可用時呼叫端只認 d=aib-roster(跟舊版一樣)。"""
    if ev_content is None:
        return None, "none"
    try:
        rec = parse_list(ev_content, signer_npub, now)
    except DevicesError as err:
        return None, "bad:%s" % err
    if signer_npub != owner:
        return None, "bad:owner_mismatch"
    if not accept_seq(root, owner, rec["seq"]):
        return None, "stale_seq"
    if rec["expired"]:
        return None, "expired"
    return rec, "ok"


def parse_device_roster(content, signer_npub, owner, dev_list):
    """機器名冊 ⇒ 驗過的 dict。簽章者要在清單 devices 裡、owner 欄要 = 這位主人;否則 DevicesError / RosterError。"""
    if signer_npub not in {d["npub"] for d in dev_list["devices"]}:
        raise DevicesError("signer_not_listed")
    return CR.parse(content, signer_npub, owner=owner)


def _seat_set(cab):
    return sorted((s.get("seat"), s.get("npub")) for s in cab.get("seats") or [])


def merge(owner, base, devices):
    """base = 主人金鑰簽的名冊(驗過,可為 None);devices = [(機器 npub, 驗過的機器名冊)…]。
    ⇒ (合併後的名冊, 警告 [{kind, cabin, devices}…])。每個艙多帶 device / dm_relays / envelope。"""
    sources = ([(owner, base)] if base else []) + [(npub, rec) for npub, rec in devices if rec]
    by_cabin = {}
    for npub, rec in sources:
        for cab in rec.get("cabins") or []:
            by_cabin.setdefault(cab["cabin"], []).append((npub, rec, cab))
    cabins, warns = [], []
    for name in sorted(by_cabin):
        decl = by_cabin[name]
        if len({n for n, _r, _c in decl}) > 1 and len({json.dumps(_seat_set(c)) for _n, _r, c in decl}) > 1:
            warns.append({"kind": "devices-cabin-clash", "cabin": name, "devices": sorted({n for n, _r, _c in decl})})
            continue
        npub, rec, cab = decl[0]
        out = dict(cab)
        out.update(device=npub, dm_relays=list(rec.get("dm_relays") or []), envelope=list(rec.get("envelope") or []))
        cabins.append(out)
    head = base or (max((r for _n, r in devices if r), key=lambda r: r.get("updated_at") or 0, default=None) or {})
    merged = {k: v for k, v in head.items() if k != "cabins"}
    merged.update(owner=owner, cabins=cabins, devices=[n for n, _r in sources if n != owner])
    if not base:
        merged["dm_relays"] = sorted({r for _n, rec in sources for r in rec.get("dm_relays") or []})
        merged["envelope"] = sorted({e for _n, rec in sources for e in rec.get("envelope") or []})
    return merged, warns


def find(merged, cabin, seat):
    """合併後找 <艙>/<席> ⇒ (席, 艙) 或 (None, None)。"""
    for cab in merged.get("cabins") or []:
        if cab.get("cabin") == cabin:
            for s in cab.get("seats") or []:
                if s.get("seat") == seat:
                    return s, cab
    return None, None
