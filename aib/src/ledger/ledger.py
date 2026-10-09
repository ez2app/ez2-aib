# SPDX-License-Identifier: Apache-2.0
"""Ez2 AIB 事件帳本本體(E1.1 draft)。

單一事件脊椎(ADR-0001 原理 4):一切是事件進同一本 append-only JSONL 帳本。
本模組只負責脊椎本身 —— append(HLC 產生+sha256 鏈+schema fail-closed)與
verify_chain(全鏈重驗)。收集器/投影是別的 lane 的事。

真相邊界:
  - schemas/event.schema.json 是事件形狀唯一正本;本模組不複述形狀,
    只在寫入前用它驗證,驗不過就整筆拒收、一個 byte 都不落盤。
  - event_id / hlc / integrity 是帳本擁有的欄位,呼叫端不得自帶
    (自帶=自報,拒收)。
  - 帳本檔一行一事件,行內容 = canonical JSON(sorted keys、無空白、UTF-8),
    因此「前一行的 sha256」是位元組級可重算的事實,不是宣稱。

鏈的定義:
  integrity.prev_event_hash = sha256(前一事件的 canonical JSON);創世=null。
  integrity.body_sha256     = sha256(body 的 canonical JSON)。
  event_id                  = "ev_" + sha256(整筆事件去掉 event_id 後的 canonical JSON)。
  HLC 全序:同一本帳內 (wall_ms, counter, node_id) 必須嚴格遞增。

併發假設(draft):單寫者、O_APPEND 逐行落盤 + fsync。多寫者仲裁屬 E1.2+。
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

try:
    from .schema_validator import SchemaValidator, SchemaValidationError
except ImportError:  # 無套件化執行(sys.path 直指本目錄)時的載入路徑
    from schema_validator import SchemaValidator, SchemaValidationError

SCHEMA_VERSION = "0.1.0-draft"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "event.schema.json"

# 帳本擁有、呼叫端不得自帶的欄位
_LEDGER_OWNED_FIELDS = ("event_id", "hlc", "integrity", "schema_version")
_HLC_COUNTER_MAX = 65535
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000  # 允許 5 分鐘正向偏斜,再高=毒時鐘


class LedgerError(Exception):
    """append 側 fail-closed:任何一筆被 raise 的事件都保證未落盤。"""


def canonical_json(obj):
    """canonical 形式:sorted keys、最小分隔、UTF-8 不轉義。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_body_sha256(event):
    return sha256_hex(canonical_json(event["body"]))


def compute_event_id(event):
    stripped = {k: v for k, v in event.items() if k != "event_id"}
    return "ev_" + sha256_hex(canonical_json(stripped))


def _hlc_key(hlc):
    return (hlc["wall_ms"], hlc["counter"], hlc["node_id"])


def dedup_key_of(event):
    """Build the stable opt-in dedup key for an event.

    Event ids include the previous chain hash, so replaying the same source
    record at another chain position produces a different id.  The body hash
    plus correlation id is stable across that replay while still separating
    independent lanes.  Callers must opt in because periodic events with an
    unchanged body should normally remain distinct.
    """
    body_sha = event.get("integrity", {}).get("body_sha256") or \
        compute_body_sha256(event)
    corr = (event.get("causation") or {}).get("correlation_id") or ""
    return "%s|%s" % (body_sha, corr)


def _default_node_id():
    import platform
    raw = platform.node().strip().lower() or "node-local"
    return re.sub(r"[^a-z0-9._-]", "-", raw)[:64]


CLAMP_UNKNOWN = "unknown"
CLAMP_NOT = "not_clamped"
CLAMP_YES = "clamped"
CLAMP_NOT_AN_EVENT = "not_an_event"


def clamp_state(event):
    """這一筆的來源時戳有沒有被夾制。**讀取端用**,不住在測試裡。

    四個值,兩兩相異:

    ==================  ==========================================================
    ``unknown``         舊事件(沒有 ``source_wall_ms``)—— **「沒有答案」不是「沒被夾制」**
    ``not_clamped``     來源時戳等於記錄值:什麼都沒丟(含「來源恰好等於鏈尾」那一格)
    ``clamped``         來源時戳早於記錄值:**來源真的被丟掉了**,原值在 ``source_wall_ms``
    ``not_an_event``    根本不是一筆事件 —— 例如 ``dedup`` 的收據
                        (頂層只有 ``event_id`` 與 ``deduplicated``,沒有 ``hlc``)
    ==================  ==========================================================

    ★ **每一條路徑都顯式回值,沒有隱式 ``None``。**
      Python 的 ``if/elif`` 少一個 ``else`` 會回 ``None``,而 ``None`` 與四個值都不相等 ——
      於是「四值兩兩相異」那一格照樣綠,只有真實輸入落到那條路時才出事,而那時沒有人在看。
    """
    if not isinstance(event, dict):
        return CLAMP_NOT_AN_EVENT
    hlc = event.get("hlc")
    if not isinstance(hlc, dict):
        return CLAMP_NOT_AN_EVENT
    if "source_wall_ms" not in hlc:
        return CLAMP_UNKNOWN
    source = hlc.get("source_wall_ms")
    recorded = hlc.get("wall_ms")
    if not isinstance(source, int) or not isinstance(recorded, int):
        return CLAMP_UNKNOWN
    if source < recorded:
        return CLAMP_YES
    else:
        # ★ 顯式 else:`source > recorded` 在現行碼上不可達(:146 的上界擋在前面),
        #   但**不可達不等於可以沒有落點** —— 沒有這一行,那條路就回 None。
        return CLAMP_NOT


class Ledger:
    def __init__(self, path, node_id=None, schema_path=None, now_ms=None):
        self._path = Path(path)
        self._node_id = node_id or _default_node_id()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

        schema_file = Path(schema_path) if schema_path else DEFAULT_SCHEMA_PATH
        if not schema_file.is_file():
            raise LedgerError("event schema not found (fail-closed): %s" % schema_file)
        try:
            with open(schema_file, "r", encoding="utf-8") as fh:
                self._validator = SchemaValidator(json.load(fh))
        except (OSError, ValueError) as err:
            raise LedgerError("event schema unreadable (fail-closed): %s" % err)

        # 開帳=全鏈驗證後才重建尾端狀態(codex 審 E1.1-高1:只解析 JSON
        # 會讓「已遭竄改但仍可解析」的帳本繼續被追加)。帳本檔以事件計小,
        # O(n) 開帳成本換「append 永遠接在可信鏈尾」的不變式。
        self._last_hash = None
        self._last_hlc = None
        self._count = 0
        self._dedup_index = {}
        self._scanned_bytes = 0
        self._scan_identity = None
        self._verified_events = []
        if self._path.exists():
            # 共享鎖下**單次**掃描:同時驗鏈與建索引(codex 審 2026-08-10
            # 高2 無鎖讀可讀到半行假故障;中4 雙掃 O(2n) 退化)。
            self._scan_locked()

    @property
    def count(self):
        return self._count

    # -- append ------------------------------------------------------------

    def _tick(self, wall_ms=None):
        """Issue a strictly increasing HLC, optionally from source time.

        ``wall_ms`` is used by backfill collectors.  A source timestamp older
        than the chain tail is clamped to the tail and advances the counter,
        so source truth is retained where possible without breaking ordering.
        """
        if wall_ms is None:
            now = self._now_ms()
        else:
            try:
                now = int(wall_ms)
            except (TypeError, ValueError):
                raise LedgerError("wall_ms must be an integer: %r" % (wall_ms,))
        # 超前時鐘上界(codex 審 E1.1-高2):單筆未來時間會永久抬高 HLC,
        # counter 溢位後整本帳停寫。允許小幅偏斜,超過即拒。
        ceiling = self._now_ms() + _MAX_CLOCK_SKEW_MS
        if now > ceiling:
            raise LedgerError(
                "wall_ms %d exceeds clock ceiling %d (max forward skew %dms)"
                % (now, ceiling, _MAX_CLOCK_SKEW_MS))
        if self._last_hlc is None or now > self._last_hlc[0]:
            cur = (now, 0)
        elif self._last_hlc[1] + 1 > _HLC_COUNTER_MAX:
            raise LedgerError("hlc counter overflow at wall_ms=%d" % self._last_hlc[0])
        else:
            cur = (self._last_hlc[0], self._last_hlc[1] + 1)
        # ★ T06/F3:來源時戳一起記下。沒有它的話,「本來就在鏈尾」與「比鏈尾早而被夾上來」
        #   事後**逐字同形**(hlc 除 counter 外完全相同,而 counter 只反映 append 順序)。
        #   `wall_ms` 仍是鏈尾值 —— 排序性質不動,留痕不是靠改排序換來的。
        return {"wall_ms": cur[0], "counter": cur[1], "node_id": self._node_id,
                "source_wall_ms": now}

    def _verify_entry(self, event, index, prev_hash, prev_hlc):
        """單筆完整驗證:鏈接續 + 內容雜湊 + id + HLC 遞增 + schema。

        codex 第三輪高2/高3:鎖內刷新原本只 json.loads,重開帳也漏 schema
        ——竄改過的事件(重算 id 但 kind 非法)照樣被接受並可續寫。
        兩條路徑改用同一支,不可能再各自寬鬆。"""
        # 形狀先於任何欄位解參照。否則缺 body/hlc 的惡意行會把 fail-closed
        # LedgerError 變成未捕捉的 KeyError，讓讀者只得到程序崩潰。
        try:
            self._validator.validate(event)
        except SchemaValidationError as err:
            raise LedgerError(
                "chain broken at %d: schema invalid (refusing to append): %s"
                % (index, err))
        integrity = event.get("integrity") or {}
        if integrity.get("prev_event_hash") != prev_hash:
            raise LedgerError(
                "chain broken at %d: prev_event_hash mismatch "
                "(refusing to append)" % index)
        if compute_body_sha256(event) != integrity.get("body_sha256"):
            raise LedgerError(
                "chain broken at %d: body hash mismatch (refusing to append)"
                % index)
        if compute_event_id(event) != event.get("event_id"):
            raise LedgerError(
                "chain broken at %d: event_id mismatch (refusing to append)"
                % index)
        hlc = tuple(_hlc_key(event["hlc"]))
        if prev_hlc is not None and hlc <= prev_hlc:
            raise LedgerError(
                "chain broken at %d: HLC not increasing (refusing to append)"
                % index)
        return sha256_hex(canonical_json(event)), hlc

    def _scan_locked(self):
        """LOCK_SH 下一次走完:逐筆驗鏈(hash/id/prev/HLC 遞增)並建 dedup 索引。

        壞鏈=拒開(fail-closed);讀取期間持共享鎖,writer 的 LOCK_EX 會等,
        因此不可能讀到半行。"""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self._path), flags)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LedgerError("ledger is not a single-link regular file")
            self._scan_identity = (info.st_dev, info.st_ino)
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                fd = -1
                prev_hash = None
                prev_hlc = None
                for index, raw in enumerate(fh):
                    line = raw.rstrip("\n")
                    if not line:
                        raise LedgerError(
                            "blank line at %d (refusing to append)" % index)
                    try:
                        event = json.loads(line)
                    except ValueError:
                        raise LedgerError(
                            "unparseable event at %d (refusing to append)" % index)
                    prev_hash, prev_hlc = self._verify_entry(
                        event, index, prev_hash, prev_hlc)
                    self._verified_events.append(event)
                    self._dedup_index.setdefault(
                        dedup_key_of(event), event["event_id"])
                    self._count += 1
                self._scanned_bytes = os.fstat(fh.fileno()).st_size
                self._last_hash = prev_hash
                self._last_hlc = prev_hlc
        finally:
            if fd >= 0:
                os.close(fd)

    def read_verified_events(self):
        """Parse and verify one locked inode, rejecting a path identity swap.

        Callers that need event data must not validate one path resolution and
        then reopen the pathname for parsing.  This method opens once, locks
        that descriptor, verifies every event on it, and returns those exact
        parsed events.  If the pathname changed since construction, it fails
        closed instead of silently accepting a different inode.
        """
        if not self._path.exists():
            if self._scan_identity is None:
                return []
            raise LedgerError("ledger disappeared after validation")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = -1
        try:
            fd = os.open(str(self._path), flags)
            fcntl.flock(fd, fcntl.LOCK_SH)
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LedgerError("ledger is not a single-link regular file")
            if self._scan_identity is not None and identity != self._scan_identity:
                raise LedgerError("ledger inode changed after validation")
            events = []
            prev_hash = None
            prev_hlc = None
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                for index, raw in enumerate(handle):
                    line = raw.rstrip("\n")
                    if not line:
                        raise LedgerError("blank line at %d" % index)
                    try:
                        event = json.loads(line)
                    except ValueError:
                        raise LedgerError("unparseable event at %d" % index)
                    prev_hash, prev_hlc = self._verify_entry(
                        event, index, prev_hash, prev_hlc)
                    events.append(event)
            return events
        except (OSError, UnicodeError) as err:
            raise LedgerError("ledger unreadable: %s" % err)
        finally:
            if fd >= 0:
                os.close(fd)

    def _refresh_index_under_lock(self, fd):
        """鎖內增量吸收「我開帳後別人新增的事件」,回傳當下真實尾端。

        codex 第三輪高2:原本只 json.loads 不驗,長存活實例可在帳本被竄改
        後繼續追加;中1:每次全掃使批次入帳退化為 O(n²)。改為從已掃過的
        位元組偏移續讀,並對新增段逐筆走 _verify_entry。"""
        size = os.lseek(fd, 0, os.SEEK_END)
        start = getattr(self, "_scanned_bytes", 0)
        if size < start:
            raise LedgerError(
                "ledger shrank under us (truncated?): refusing to append")
        if size == start:
            return (self._last_hash, self._last_hlc) if self._count else None
        os.lseek(fd, start, os.SEEK_SET)
        remaining = size - start
        buffer = b""
        pending_index = []
        prev_hash = self._last_hash
        prev_hlc = self._last_hlc
        index = self._count
        while remaining > 0:
            chunk = os.read(fd, min(1 << 16, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if not raw:
                    raise LedgerError(
                        "blank line at %d (refusing to append)" % index)
                try:
                    line = raw.decode("utf-8")
                    event = json.loads(line)
                except (UnicodeError, ValueError):
                    raise LedgerError(
                        "unparseable event at %d (refusing to append)" % index)
                prev_hash, prev_hlc = self._verify_entry(
                    event, index, prev_hash, prev_hlc)
                pending_index.append(
                    (dedup_key_of(event), event["event_id"]))
                index += 1
        if remaining != 0:
            raise LedgerError("ledger short read (refusing to append)")
        if buffer:
            raise LedgerError(
                "unterminated event at %d (refusing to append)" % index)
        # 只有整段驗完才發布記憶體狀態。中途壞行不應讓 count/index 先走，
        # 否則下一次從舊 offset 重讀會把同一筆再算一次。
        for key, event_id in pending_index:
            self._dedup_index.setdefault(key, event_id)
        self._count = index
        self._scanned_bytes = size
        self._last_hash = prev_hash
        self._last_hlc = prev_hlc
        return (prev_hash, prev_hlc) if self._count else None

    def find_by_dedup_key(self, key):
        """Return the current on-disk event id for a stable dedup key.

        A long-lived reader may coexist with another writer.  Refresh under a
        shared lock before answering so the public query cannot silently serve
        the construction-time snapshot forever.
        """
        if not self._path.exists():
            return self._dedup_index.get(key)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self._path), flags)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LedgerError("ledger is not a single-link regular file")
            if self._scan_identity is not None \
                    and identity != self._scan_identity:
                raise LedgerError("ledger inode changed after validation")
            if self._scan_identity is None:
                self._scan_identity = identity
            self._refresh_index_under_lock(fd)
            return self._dedup_index.get(key)
        finally:
            os.close(fd)

    def append(self, draft, wall_ms=None, dedup=False):
        """收一筆事件草稿(kind/actor/body/可選 causation/evidence_refs),
        由帳本補齊 HLC、鏈雜湊與 event_id,schema 驗過才落盤。

        ``wall_ms`` enables source-time backfill.  ``dedup`` is deliberately
        opt-in and returns a small receipt instead of appending when the same
        body hash and correlation id already exist.

        回傳完整入帳事件。任何錯誤 raise LedgerError 且保證未寫入。"""
        if not isinstance(draft, dict):
            raise LedgerError("draft must be a dict")
        for field in _LEDGER_OWNED_FIELDS:
            if field in draft:
                raise LedgerError(
                    "field %r is ledger-owned; drafts must not self-report it" % field)

        event = dict(draft)
        event["schema_version"] = SCHEMA_VERSION
        event.setdefault("causation", {"causation_id": None})
        if "body" not in event or not isinstance(event.get("body"), dict):
            raise LedgerError("draft.body must be an object")

        probe_key = None
        if dedup:
            probe_key = "%s|%s" % (
                compute_body_sha256(event),
                (event.get("causation") or {}).get("correlation_id") or "")

        def _seal(evt):
            evt["hlc"] = self._tick(wall_ms)
            evt["integrity"] = {
                "body_sha256": compute_body_sha256(evt),
                "prev_event_hash": self._last_hash,
                "signature": {
                    "alg": "unsigned_draft",
                    "key_id": self._node_id,
                    "value": "",
                },
            }
            evt["event_id"] = compute_event_id(evt)
            try:
                self._validator.validate(evt)
            except SchemaValidationError as err:
                raise LedgerError("schema rejected event (fail-closed): %s" % err)
            return evt

        # 先封一次:即使最後判定為重複,也要讓形狀過 schema(重複也是輸入)
        event = _seal(event)

        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND \
            | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self._path), flags, 0o644)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LedgerError("ledger is not a single-link regular file")
            identity = (info.st_dev, info.st_ino)
            if self._scan_identity is not None \
                    and identity != self._scan_identity:
                raise LedgerError("ledger inode changed after validation")
            if self._scan_identity is None:
                self._scan_identity = identity
            fcntl.flock(fd, fcntl.LOCK_EX)
            # 鎖內把別人新增的事件補進索引並取真實尾端(codex 審高1+E1.5高1):
            # dedup 判定與鏈接續都必須在鎖內用當下磁碟真相,否則兩程序同鍵
            # 各寫一筆(鏈不分叉但語意重複)、或各接舊尾端(鏈分叉)。
            before_hash = self._last_hash
            tail = self._refresh_index_under_lock(fd)
            if probe_key is not None:
                existing = self._dedup_index.get(probe_key)
                if existing:
                    return {"event_id": existing, "deduplicated": True}
            # refresh 會就地更新 self._last_hash,所以「尾端是否變動」必須跟
            # 進鎖前的快照比(拿更新後的值自己比自己恆等,事件就會帶著過期的
            # prev hash 寫下去——併發測試因此紅)。
            if self._last_hash != before_hash:
                event = _seal(event)
            line = canonical_json(event)
            payload = (line + "\n").encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            self._scanned_bytes = os.lseek(fd, 0, os.SEEK_END)
        finally:
            os.close(fd)

        self._last_hash = sha256_hex(line)
        self._last_hlc = _hlc_key(event["hlc"])
        self._dedup_index.setdefault(dedup_key_of(event), event["event_id"])
        self._count += 1
        return event

    # -- verify ------------------------------------------------------------

    def verify_chain(self):
        """全鏈重驗,回報告 dict(不 raise —— 驗證器是回饋器,原理 10:
        閘門可擋,回饋永不擋)。逐筆檢查:
          json 可解析 / schema 合法 / body_sha256 重算相符 /
          event_id 重算相符 / prev_event_hash 接前一筆 / HLC 嚴格遞增。
        第一個錯即停,報 {ok, count, error:{index, code, message}}。"""
        prev_hash = None
        prev_hlc = None
        index = 0
        if not self._path.exists():
            return {"ok": True, "count": 0, "error": None}
        with open(self._path, "r", encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                line = line.rstrip("\n")
                try:
                    event = json.loads(line)
                except ValueError as err:
                    return self._broken(index, "unparseable", str(err))
                try:
                    self._validator.validate(event)
                except SchemaValidationError as err:
                    return self._broken(index, "schema_invalid", str(err))

                want_body = compute_body_sha256(event)
                if event["integrity"]["body_sha256"] != want_body:
                    return self._broken(
                        index, "body_sha256_mismatch",
                        "stored %s != recomputed %s"
                        % (event["integrity"]["body_sha256"], want_body))

                want_id = compute_event_id(event)
                if event["event_id"] != want_id:
                    return self._broken(
                        index, "event_id_mismatch",
                        "stored %s != recomputed %s" % (event["event_id"], want_id))

                if event["integrity"]["prev_event_hash"] != prev_hash:
                    return self._broken(
                        index, "prev_hash_mismatch",
                        "stored %r != expected %r"
                        % (event["integrity"]["prev_event_hash"], prev_hash))

                hlc = _hlc_key(event["hlc"])
                if prev_hlc is not None and hlc <= prev_hlc:
                    return self._broken(
                        index, "hlc_not_monotonic",
                        "hlc %r does not advance past %r" % (hlc, prev_hlc))

                prev_hash = sha256_hex(canonical_json(event))
                prev_hlc = hlc
        return {"ok": True, "count": index + 1 if prev_hash else 0, "error": None}

    @staticmethod
    def _broken(index, code, message):
        return {"ok": False, "count": index,
                "error": {"index": index, "code": code, "message": message}}
