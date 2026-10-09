#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""EH.4(1) 人格/第二大腦索引 —— 從「這一席是誰」查到兩個既有 key 的資料檔。

設計稿 `docs/reports/DESIGN-auto-persona-binding-0821.md` §8.1–8.3(the maintainer 08-21 裁「要有
key/快速索引」;a seat 08-22 14:27 裁本葉只做不碰 vault 的那一半,第一件=量+索引)。

★ 不發明第三種識別碼(§8.1):人格的 key = cards.json 的 uid,第二大腦的 key = route_key。
  本檔只補**索引**:`(project, role) → {persona_uid, route_key}`。

★ 索引的鍵是 `(project, role)` 不是 role(§8.2 候選 B):role 在第二個專案就會撞,且撞了
  不報錯。`project` = 席位檔的 `cwd`(寫入端 `src/cockpit/aib` `_mutate` 於開席那一刻寫
  `pwd -P`;既有席位由 `seat_cwd_backfill.py` 回填)。**沒有 cwd 的席位不入索引**——
  退回 role 當鍵會讓每一份舊席位檔被歸到同一個專案,失效值長得跟正確值一樣。

★ 索引落資料檔不落碼(§8.3):`<state_root>/identity/persona-index.json` + 同名
  `.provenance.json` 收據。它是**投影產物**(與 bindings.json / seat-display.json 同一
  族):只該被重生,不該被手改;來源 = cards.json + bindings.json + 席位檔,三者都是
  既有檔,本檔不寫它們任何一個、不碰 vault、不碰 ledger。

★ 判準(設計稿 §四,照抄不改):
  1. **唯一才入索引。** 同一個 `(project, role)` 在母體裡對應到兩個以上不同的
     `(persona_uid, route_key)` → 不入索引,整組列進 `conflicts` 候人裁。錯綁比空值更糟。
  4. **route_key 不猜。** 綁定裡 route_key 是什麼就記什麼(可為 null),不用卡的
     default_route_key 補位——route 決定第二大腦寫到 vault 哪個資料夾,猜錯是把一席的
     產出寫進別人的知識區。
  (§四-2 code 逐字相等、§四-3 母體下限,屬第 (2) 件「候選」的事,不在本檔。)

★ `source` 三態(human / auto / created,`DESIGN-SPEC-persona-source-three-states.md`):
  既有 bindings.json 的條目**沒有** source 欄,所以由它導出的索引條目 `source` 是 **None**
  (=不知道),**絕不寫成 "human"**——把「我不知道」寫成「我確定」比不寫更糟。日後由
  第 (2)/(3) 件產生的條目才會帶 auto / created。讀端不得把 None 當 human。

★ 查綁定用的鍵——**解析端雙認**(`seat_address` 的規矩:產生端單源、解析端雙認):
  先用 `code_of(rec)`(session_uuid 末五碼)查,再用 `seat_id` 查;兩個都查到而不同 → 當
  衝突處理(不挑)。為什麼要雙認:08-22 實測 bindings.json 32 個鍵**全是舊 seat_id**
  (08-21 18:23 的投影,早於 EX.17 3b-i 換鍵),而 22 個有 cwd 的席位用新碼查 0 命中、
  用 seat_id 查 3 命中——只認新碼會讓索引**安靜地空著**,而空索引長得跟「沒人綁過」一樣。
  **這裡刻意不印 LEGACY_MARK**:那個計數器數的是「還在**發**舊碼」(翻階段 4 的閘),
  查不該被數成發。條目裡記 `binding_key` 與 `binding_key_kind`(uuid_code / legacy_seat_id),
  讓「這一格是靠舊鍵對上的」事後查得出來。

用法:
    persona_index.py build [--state-root R] [--dry-run] [--json]   # 重生索引(預設落檔)
    persona_index.py show  --project <cwd> --role <role> [--state-root R]
退出碼:build 0 完整、1 診斷不完整、2 無法判定;show 0 命中、1 查無、2 輸入不合。
"""
import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("memory", "cockpit"):
    if str(_SRC / _p) not in sys.path:
        sys.path.insert(0, str(_SRC / _p))

import binding_projection as bp   # noqa: E402 — resolve_state_root / discover_seat_files / load_card_registry
import seat_address               # noqa: E402 — code_of(單源末五碼)
from card_registry import CardRegistryError, write_atomic  # noqa: E402

SCHEMA_VERSION = "aib-persona-index/v2"
PROVENANCE_RECORD = "persona-index-provenance-v1"
INDEX_REL = ("identity", "persona-index.json")
BINDINGS_REL = ("identity", "bindings.json")
CARDS_REL = ("identity", "cards.json")

SOURCES = ("human", "auto", "created")

REASON_UNREADABLE = "seat_file_unreadable"
REASON_NO_ROLE = "seat_has_no_role"
REASON_NO_CWD = "seat_has_no_cwd"            # 舊席位檔;EH.3 回填前不入索引,不退回 role 當鍵
REASON_NO_KEY = "seat_has_no_lookup_key"     # 連 seat_id 都沒有
REASON_CARD_NOT_REGISTERED = "card_not_in_registry"
REASON_DUPLICATE_IDENTITY = "duplicate_seat_identity_conflict"
REASON_TUPLE_CONFLICT = "conflict_same_project_role"
REASON_STATE_ROOT_UNUSABLE = "state_root_unusable"
REASON_IDENTITY_ROOT_UNUSABLE = "identity_root_unusable"
REASON_IDENTITY_SOURCE_UNUSABLE = "identity_source_unusable"
REASON_SEAT_FILE_UNUSABLE = "seat_file_unusable"
REASON_INDEX_UNUSABLE = "index_target_unusable"
REASON_PROVENANCE_UNUSABLE = "provenance_target_unusable"


class IndexError_(Exception):
    """輸入不合(fail-closed,零寫入)。"""

    def __init__(self, message, reason="input_invalid", path=None):
        super().__init__(message)
        self.reason = reason
        self.path = path


# ---- 鍵 ---------------------------------------------------------------------

def index_key(project, role):
    """Return the reversible canonical JSON encoding of ``(project, role)``."""
    return json.dumps([project, role], ensure_ascii=False, separators=(",", ":"))


def lookup_key(rec):
    """席位檔 → 這一席在索引證據裡的名字;取不到回 None(新碼優先,沒有才 seat_id)。"""
    if not isinstance(rec, dict):
        return None
    return seat_address.code_of(rec) or rec.get("seat_id") or None


def bindings_for(rec, bindings):
    """席位檔 → [(binding, key, kind)](雙認:uuid 末五碼、舊 seat_id 各查一次;同值去重,
    不同值兩筆都回,由呼叫端當衝突)。見檔頭「查綁定用的鍵」。"""
    hits = []
    code = seat_address.code_of(rec)
    sid = rec.get("seat_id")
    for key, kind in ((code, "uuid_code"), (sid, "legacy_seat_id")):
        if key and key in bindings:
            b = bindings[key]
            if not any(h[0] == b for h in hits):
                hits.append((b, key, kind))
    return hits


# ---- 輸入 -------------------------------------------------------------------

def load_bindings(path):
    """bindings.json(投影產物)。形狀在這裡驗,不信任檔案:缺檔=錯誤不是空表。"""
    p = Path(path)
    if not p.is_file():
        raise IndexError_("bindings.json 不存在:%s(先跑 binding_projection)" % p)
    try:
        with open(str(p), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as err:
        raise IndexError_("bindings.json 讀不動:%s" % err)
    b = data.get("bindings") if isinstance(data, dict) else None
    if not isinstance(b, dict):
        raise IndexError_("bindings.json 形狀不合:缺 bindings 物件")
    out = {}
    for k, v in b.items():
        if not isinstance(v, dict) or not v.get("persona_uid"):
            raise IndexError_("bindings.json 條目 %r 缺 persona_uid" % (k,))
        rk = v.get("route_key")
        if rk is not None and not isinstance(rk, str):
            raise IndexError_("bindings.json 條目 %r route_key 不是字串" % (k,))
        out[str(k)] = {"persona_uid": str(v["persona_uid"]), "route_key": rk}
    return out


def load_cards(path):
    """cards.json → {uid: {display_name, route_key(default), source, ...}}(走投影那一支解析,不重造)。"""
    try:
        return bp.load_card_registry(str(path))
    except bp.ProjectionError as err:
        raise IndexError_(str(err))


# ---- 核心 -------------------------------------------------------------------

def _absolute_path(path):
    """Lexical absolute path; never resolve a link merely to report its source."""
    return os.path.abspath(str(path))


def _raise_unusable(path, reason, label, detail):
    absolute = _absolute_path(path)
    raise IndexError_("%s 不可用:%s:%s" % (label, absolute, detail),
                      reason=reason, path=absolute)


def _preflight_directory(path, reason, label, missing_ok=False):
    """lstat one directory node without accepting a symlink in its place."""
    p = Path(path)
    try:
        metadata = p.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        _raise_unusable(p, reason, label, "不存在")
    except OSError as err:
        _raise_unusable(p, reason, label, "lstat 失敗:%s" % err)
    if not stat.S_ISDIR(metadata.st_mode):
        _raise_unusable(p, reason, label, "不是不跟 link 的 directory")
    return metadata


def _preflight_identity_source(path, label):
    """Identity inputs must be exact single-link regular files before loaders run."""
    p = Path(path)
    try:
        metadata = p.lstat()
    except FileNotFoundError:
        _raise_unusable(p, REASON_IDENTITY_SOURCE_UNUSABLE, label, "不存在")
    except OSError as err:
        _raise_unusable(p, REASON_IDENTITY_SOURCE_UNUSABLE, label,
                        "lstat 失敗:%s" % err)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _raise_unusable(p, REASON_IDENTITY_SOURCE_UNUSABLE, label,
                        "必須是 single-link regular file")
    return metadata


def _seat_directories_no_follow(root):
    """Return the existing lexical seat directories without following directory links."""
    roots = [Path(root) / "seats"]
    instances = Path(root) / "instances"
    instances_meta = _preflight_directory(
        instances, REASON_SEAT_FILE_UNUSABLE, "instances source", missing_ok=True)
    if instances_meta is not None:
        try:
            with os.scandir(str(instances)) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as err:
            _raise_unusable(instances, REASON_SEAT_FILE_UNUSABLE,
                            "instances source", "scandir 失敗:%s" % err)
        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as err:
                _raise_unusable(entry.path, REASON_SEAT_FILE_UNUSABLE,
                                "instance source", "lstat 失敗:%s" % err)
            if stat.S_ISLNK(metadata.st_mode):
                _raise_unusable(entry.path, REASON_SEAT_FILE_UNUSABLE,
                                "instance source", "symlink 不可作為來源")
            if stat.S_ISDIR(metadata.st_mode):
                roots.append(Path(entry.path) / "seats")

    result = []
    seen = set()
    for seats_dir in roots:
        metadata = _preflight_directory(
            seats_dir, REASON_SEAT_FILE_UNUSABLE, "seat directory", missing_ok=True)
        if metadata is None:
            continue
        key = _absolute_path(seats_dir)
        if key not in seen:
            seen.add(key)
            result.append(Path(key))
    return result


def _preflight_seat_files(root):
    """Enumerate and validate every seat node before any seat target is read."""
    candidates = []
    seen = set()
    for seats_dir in _seat_directories_no_follow(root):
        try:
            with os.scandir(str(seats_dir)) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as err:
            _raise_unusable(seats_dir, REASON_SEAT_FILE_UNUSABLE,
                            "seat directory", "scandir 失敗:%s" % err)
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            path = Path(_absolute_path(entry.path))
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as err:
                _raise_unusable(path, REASON_SEAT_FILE_UNUSABLE,
                                "seat source", "lstat 失敗:%s" % err)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                _raise_unusable(path, REASON_SEAT_FILE_UNUSABLE, "seat source",
                                "必須是 single-link regular file")
            key = str(path)
            if key not in seen:
                seen.add(key)
                candidates.append((path, metadata))
    return sorted(candidates, key=lambda item: str(item[0]))


def _read_preflighted_seat(path, expected):
    """Read a preflighted regular seat without following its final path component."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(str(path), flags)
        actual = os.fstat(fd)
        before = (expected.st_dev, expected.st_ino, stat.S_IFMT(expected.st_mode),
                  expected.st_nlink)
        after = (actual.st_dev, actual.st_ino, stat.S_IFMT(actual.st_mode),
                 actual.st_nlink)
        if before != after or not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
            _raise_unusable(path, REASON_SEAT_FILE_UNUSABLE, "seat source",
                            "preflight 後節點已改變")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = None
            try:
                return json.load(fh)
            except ValueError:
                return None
    except IndexError_:
        raise
    except OSError:
        # A regular but unreadable/vanished seat remains the existing named
        # seat_file_unreadable diagnostic. O_NOFOLLOW still prevents target reads.
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _discover_seat_files_no_follow(root):
    preflighted = _preflight_seat_files(root)
    return [(path, _read_preflighted_seat(path, metadata))
            for path, metadata in preflighted]


def _canonical_seat_path(path):
    """記錄 exact lexical absolute source；不得以 realpath 隱藏來源 link。"""
    return _absolute_path(path)


def _dedupe_seat_files(seat_files):
    """以 persisted ``seat_id`` 去重。同身份異內容是具名 incomplete。"""
    by_identity = {}
    rows = []
    errors = []
    seats_seen = 0
    for path, rec in sorted(seat_files, key=lambda item: str(item[0])):
        source = _canonical_seat_path(path)
        if not isinstance(rec, dict):
            errors.append({"seat_file": source, "reason": REASON_UNREADABLE})
            seats_seen += 1
            continue
        seat_id = rec.get("seat_id")
        if not isinstance(seat_id, str) or not seat_id:
            rows.append((source, rec))
            seats_seen += 1
            continue
        by_identity.setdefault(seat_id, []).append((source, rec))

    for seat_id in sorted(by_identity):
        copies = sorted(by_identity[seat_id], key=lambda item: item[0])
        seats_seen += 1
        first = copies[0][1]
        if any(rec != first for _source, rec in copies[1:]):
            errors.append({
                "reason": REASON_DUPLICATE_IDENTITY,
                "seat": seat_id,
                "seat_files": [source for source, _rec in copies],
            })
            continue
        rows.append(copies[0])
    return sorted(rows, key=lambda item: item[0]), errors, seats_seen


def collect(seat_files, bindings, cards):
    """(席位檔列表, bindings, cards) → 具完整診斷的索引文件主體。"""
    groups = {}      # key → {"project", "role", "bindings": {(uid, rk): [seat keys]}, "unbound_seats": [...]}
    unkeyed = []
    by_role = {}     # role → {(uid, rk): [seat keys]}:**母體**,含無 cwd 的席位(它們有 role 也有綁定)
    seat_rows, errors, seats_seen = _dedupe_seat_files(seat_files)
    for src, rec in seat_rows:
        role = rec.get("role")
        cwd = rec.get("cwd")
        key = lookup_key(rec)
        if not isinstance(key, str) or not key:
            unkeyed.append({"seat_file": src, "reason": REASON_NO_KEY})
            continue
        if not isinstance(role, str) or not role:
            unkeyed.append({"seat_file": src, "seat": key, "reason": REASON_NO_ROLE})
            continue
        hits = bindings_for(rec, bindings)
        for b, _bkey, _kind in hits:
            by_role.setdefault(role, {}).setdefault((b["persona_uid"], b["route_key"]), []).append(key)
        if not isinstance(cwd, str) or not cwd:
            unkeyed.append({"seat_file": src, "seat": key, "role": role, "reason": REASON_NO_CWD})
            continue
        g = groups.setdefault(index_key(cwd, role),
                              {"project": cwd, "role": role, "bindings": {}, "unbound_seats": []})
        if not hits:
            g["unbound_seats"].append(key)
        for b, bkey, kind in hits:
            g["bindings"].setdefault((b["persona_uid"], b["route_key"]), []).append(
                {"seat": key, "binding_key": bkey, "binding_key_kind": kind})

    index, conflicts, unbound = {}, {}, []
    for k in sorted(groups):
        g = groups[k]
        if not g["bindings"]:
            unbound.append({"project": g["project"], "role": g["role"],
                            "seats": sorted(set(g["unbound_seats"]))})
            continue
        if len(g["bindings"]) > 1:
            # §四-1:同一個 (project, role) 對到兩組以上不同綁定 → 不入索引,整組候人裁
            conflicts[k] = {"project": g["project"], "role": g["role"],
                            "reason": REASON_TUPLE_CONFLICT,
                            "candidates": [{"persona_uid": uid, "route_key": rk, "seats": _seat_names(seats)}
                                           for (uid, rk), seats in sorted(g["bindings"].items(),
                                                                          key=lambda t: (t[0][0], t[0][1] or ""))]}
            continue
        (uid, rk), hits = next(iter(g["bindings"].items()))
        seats = _seat_names(hits)
        card = cards.get(uid)
        entry = {
            "project": g["project"], "role": g["role"],
            "persona_uid": uid,
            "route_key": rk,                       # §四-4:綁定裡是什麼就是什麼,不用卡的 default 補位
            "source": None,                        # 既有綁定沒有 source 欄 → 不知道;絕不寫 human
            "source_note": "derived_from_bindings_json_without_source_field",
            "display_name": card.get("display_name") if card else None,
            "card_source": card.get("source") if card else None,
            "card_registered": card is not None,
            "evidence": {"seats": seats,
                         "binding_keys": sorted({h["binding_key"] for h in hits}),
                         "binding_key_kinds": sorted({h["binding_key_kind"] for h in hits}),
                         "unbound_seats_same_key": sorted(set(g["unbound_seats"]))},
        }
        if card is None:
            entry["warning"] = REASON_CARD_NOT_REGISTERED
        index[k] = entry
    population = {}
    for role, groups_ in sorted(by_role.items()):
        entries = []
        for (uid, rk), raw_seats in groups_.items():
            # ``_dedupe_seat_files`` 已將同 seat_id 收旂；此處一筆就是一個身份證據。
            seats = sorted(raw_seats)
            entries.append({"persona_uid": uid, "route_key": rk,
                            "count": len(seats), "seats": seats})
        population[role] = sorted(
            entries,
            key=lambda entry: (-entry["count"], entry["persona_uid"],
                               entry["route_key"] or ""),
        )
    return {
        "index": index,
        "conflicts": conflicts,
        "unbound": unbound,
        "unkeyed": unkeyed,
        "errors": errors,
        # ★ 母體不是索引:它按 role 聚合、跨專案、含無 cwd 的舊席位。只供第 (2) 件當「提示」
        #   (root 08-22 14:35:候選不得只靠 role 猜),不得被任何讀端拿來當綁定答案。
        "by_role": population,
        "counts": {"seats_seen": seats_seen, "indexed": len(index),
                   "conflicts": len(conflicts), "unbound": len(unbound), "unkeyed": len(unkeyed)},
    }


def _seat_names(hits):
    return sorted({h["seat"] for h in hits})


def lookup(doc, project, role):
    """索引文件 → 條目或 None(conflicts 裡的也回 None——那是「候裁」不是「有答案」)。"""
    return (doc.get("index") or {}).get(index_key(project, role))


# ---- 落檔 -------------------------------------------------------------------

def _sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def provenance_path(index_path):
    p = Path(index_path)
    return p.with_name(p.name + ".provenance.json")


def _status_for(body):
    hard_errors = [
        error for error in body.get("errors", [])
        if error.get("reason") != REASON_DUPLICATE_IDENTITY
    ]
    if hard_errors:
        return "unknown"
    if body.get("errors") or body.get("conflicts") or body.get("unkeyed"):
        return "incomplete"
    return "complete"


def _preflight_output_target(target, reason, label):
    """Allow an absent or exact single-link regular target; reject every alias."""
    target = Path(target)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return
    except OSError as err:
        _raise_unusable(target, reason, label, "lstat 失敗:%s" % err)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _raise_unusable(target, reason, label,
                        "必須不存在或為 single-link regular file")


def _preflight_output_targets(index_path):
    """Preflight both formal targets before the first index byte is written."""
    index_target = Path(index_path)
    provenance_target = provenance_path(index_target)
    _preflight_directory(index_target.parent, REASON_INDEX_UNUSABLE,
                         "index parent")
    _preflight_output_target(index_target, REASON_INDEX_UNUSABLE, "index target")
    _preflight_output_target(provenance_target, REASON_PROVENANCE_UNUSABLE,
                             "provenance target")


def write_index(path, doc, sources):
    """原子落檔 + 收據(與 binding_projection 同一條紀律:投影檔只該被重生,收據讓
    「有人手改」與「上游變了」分得開)。輸出目錄不存在=拒(狀態樹是 aib init 的事)。"""
    target = Path(path)
    provenance = provenance_path(target)
    _preflight_output_targets(target)
    blob = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    try:
        write_atomic(target, blob)
    except (CardRegistryError, OSError) as err:
        _raise_unusable(target, REASON_INDEX_UNUSABLE, "index target",
                        "write 失敗:%s" % err)
    receipt = {"record": PROVENANCE_RECORD,
               "index_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
               "counts": doc.get("counts"), "sources": sources}
    try:
        write_atomic(provenance,
                     json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    except (CardRegistryError, OSError) as err:
        _raise_unusable(provenance, REASON_PROVENANCE_UNUSABLE,
                        "provenance target", "write 失敗:%s" % err)
    return receipt


def build(state_root=None, cards_path=None, bindings_path=None, out_path=None,
          now_ms=None, write=True):
    """重生索引。回 (doc, out_path, receipt|None)。write=False 只算不落檔。"""
    root, how = bp.resolve_state_root(state_root)
    cards_p = Path(cards_path) if cards_path else root.joinpath(*CARDS_REL)
    bind_p = Path(bindings_path) if bindings_path else root.joinpath(*BINDINGS_REL)
    out_p = Path(out_path) if out_path else root.joinpath(*INDEX_REL)
    _preflight_directory(root, REASON_STATE_ROOT_UNUSABLE, "state root")
    _preflight_directory(root / "identity", REASON_IDENTITY_ROOT_UNUSABLE,
                         "identity root")
    _preflight_identity_source(cards_p, "cards source")
    _preflight_identity_source(bind_p, "bindings source")
    cards = load_cards(cards_p)
    bindings = load_bindings(bind_p)
    seat_files = _discover_seat_files_no_follow(root)
    body = collect(seat_files, bindings, cards)
    status = _status_for(body)
    doc = {"schema_version": SCHEMA_VERSION,
           "status": status, "written": False,
           "generated_wall_ms": int(now_ms) if now_ms is not None else None,
           "state_root": str(root), "state_root_resolved_by": how}
    doc.update(body)
    sources = {"cards": {"path": str(cards_p), "sha256": _sha256_file(cards_p)},
               "bindings": {"path": str(bind_p), "sha256": _sha256_file(bind_p)},
               "seat_files": sorted(_canonical_seat_path(p) for p, _ in seat_files)}
    receipt = None
    if write and status == "complete":
        write_doc = dict(doc)
        write_doc["written"] = True
        receipt = write_index(out_p, write_doc, sources)
        doc["written"] = True
    return doc, out_p, receipt


# ---- CLI --------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="persona_index.py", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["build", "show"])
    ap.add_argument("--state-root")
    ap.add_argument("--cards"); ap.add_argument("--bindings"); ap.add_argument("--out")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--dry-run", action="store_true", help="只算不落檔")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--project"); ap.add_argument("--role")
    args = ap.parse_args(argv)
    try:
        if args.action == "build":
            doc, out_p, receipt = build(args.state_root, args.cards, args.bindings, args.out,
                                        now_ms=args.now_ms, write=not args.dry_run)
            if args.json:
                print(json.dumps(dict(doc, out=str(out_p)), ensure_ascii=False,
                                 sort_keys=True))
            else:
                c = doc["counts"]
                print("persona-index %s/%s: seats=%d indexed=%d conflicts=%d unbound=%d unkeyed=%d -> %s"
                      % (doc["status"], "written" if receipt is not None else "not-written",
                         c["seats_seen"], c["indexed"], c["conflicts"], c["unbound"],
                         c["unkeyed"], out_p))
            return {"complete": 0, "incomplete": 1, "unknown": 2}[doc["status"]]
        if not args.project or not args.role:
            ap.error("show 需要 --project 與 --role")
        root, _ = bp.resolve_state_root(args.state_root)
        p = Path(args.out) if args.out else root.joinpath(*INDEX_REL)
        if not p.is_file():
            raise IndexError_("索引不存在:%s(先 build)" % p)
        try:
            with open(str(p), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as err:
            raise IndexError_("索引讀不動:%s" % err)
        if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
            actual = doc.get("schema_version") if isinstance(doc, dict) else None
            raise IndexError_("索引 schema_version 不合:%r" % actual,
                              reason="schema_version_mismatch")
        hit = lookup(doc, args.project, args.role)
        if hit is None:
            print(json.dumps({"hit": None, "conflict": index_key(args.project, args.role) in (doc.get("conflicts") or {})},
                             ensure_ascii=False))
            return 1
        print(json.dumps(hit, ensure_ascii=False, sort_keys=True))
        return 0
    except IndexError_ as err:
        if args.action == "build" and args.json:
            envelope = {"status": "unknown", "written": False,
                        "reason": err.reason, "error": str(err)}
            if err.path is not None:
                envelope["path"] = err.path
            print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
        sys.stderr.write("persona_index: %s\n" % err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
