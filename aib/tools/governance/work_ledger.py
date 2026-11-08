#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib ledger —— 艙級工作帳(自動推進 v2 B 項,11-08 f1fae 派;三家深審一致第一名「一本帳 + 權責不重疊 + 收據綁頭碼」)。

    aib ledger role set dispatch|integrate <seat> --by <seat> [--as dispatch]   誰是派工席 / 整合席
                    (帳是空的時只能先設派工席,誰都能設;之後只有派工席能改)
    aib ledger role list
    aib ledger add <id> --batch B --owner <seat> --title T [--next N] [--needs chrome|recorded] [--cmd CMD]
                    [--event-id E] --by <seat>
    aib ledger move <id> --from S --to S [--head SHA [--repo DIR]] [--evidence RECEIPT.json …] [--next N]
                    [--blocker WHY] [--event-id E] --by <seat>
    aib ledger show <id> [--json]
    aib ledger list [--state S] [--batch B] [--owner <seat>] [--json]
    aib ledger verify <id> [--json]
    aib ledger record [--kind K] [--tree DIR] [--out DIR] [--count REGEX] -- <cmd …>

為什麼:dm 只是通知、「已驗」只是一句話;帳才算數。每件工作一列(id、批、負責席、頭碼、證據路徑、下一步、阻塞原因),
每次狀態轉移一筆事件。醒來先讀帳,不靠記憶。

狀態:queued → running → review → accepted → integrating → deployed;旁支 blocked、blocked-human。
  轉移在一個 BEGIN IMMEDIATE 交易裡做:先比 --from(compare-and-set),對不上就整筆不寫(rc 4)。
誰能寫哪個狀態(權責不重疊):
  queued、accepted、blocked-human = 派工席;integrating、deployed = 整合席;running、review = 負責席
  (running 派工席也能寫);blocked = 負責席 / 派工席 / 整合席。
  另外(ebb36 審 #527):離開 blocked-human 只有派工席能寫(負責席不能自己解開等真人,H2);
  負責席不能收自己的件(review → accepted 時 by = owner 就擋,M1);派工和整合同一席是 f1fae 定的設計,照 --as 分帽子。
  負責席能把 accepted / integrating 的件推到 blocked 再自己解回 running(把件從整合席手上拉回來;L2):這是刻意留的,
  返工要能發生;事件裡看得到。
返工換頭碼(H1):running → review 帶了不同的 --head,舊頭碼的收據移到 superseded(連同舊頭碼記下),不再算數;
  收件只看現在頭碼的收據。--repo DIR 帶了就用 git 查這個 commit 真的存在、記下它的 commit 時間(M4),
  收據的 started_at 不能早於它。事件 id 去重:--event-id 相同、內容相同 ⇒ 重送不重做(印原結果,rc 0);同 id 不同內容 ⇒ rc 6。
收件門(→ accepted):帳上的頭碼 + 收據驗證器全過才放行(見 verify)。

收據(--evidence 帶的 JSON 檔,schema "aib-receipt/1"):
  {"schema": "aib-receipt/1", "kind": "positive|knife|default|chrome|other", "sha": "<頭碼>", "cmd": "<跑的指令>",
   "host": "<機台>", "started_at": "<ISO 8601>", "ended_at": "<ISO 8601>", "rc": 0, "passed": 95, "total": 95,
   "output": "<原始輸出檔絕對路徑>", "output_sha256": "<必填>"}
  verify 逐張查:同一個 SHA(前綴相容、至少 7 碼)、指令 / 機台非空、起訖時間能讀且 起 ≤ 訖 ≤ 現在 + 5 分
  (知道 commit 時間時,起 ≥ commit 時間 − 5 分)、真實 rc = 0、通過支數 ≥ 1 且 = total、原始輸出檔存在且非空、
  output_sha256 對得上;原始輸出裡要出現頭碼前 7 碼,以及跟 passed / total 吻合的結果行(「P/T」或 unittest
  「Ran T tests」+ OK)(H3)。
  至少一張 positive 或 default 的收據(只有刀 / chrome 不算,L1);件標 --needs chrome ⇒ 還要一張同頭碼的 chrome 收據。
  **手寫收據只證明格式和輸出對得上,不證明真的跑過**:同帳號的 AI 照樣寫得出一份對得上的。真跑證明要靠包裝器產收據。
  收件那筆事件記下每張收據檔與原始輸出檔的 sha256(M3),事後檔被換掉查得到。

收據產生器 record(11-08 f1fae 定「防假完成最有用的一刀」):包裝器自己跑指令,rc、起訖、機台、原始輸出檔、它的 sha256、
  通過支數都由它寫,跑的人寫不了;頭碼取 --tree(預設現在資料夾)的 git HEAD,樹有沒提交的改動就不跑(收據證明不了那個頭碼)。
  收據檔帶 producer = "aib ledger record",收據本身的 sha256 登記在帳本 receipts 表。verify 碰到自稱 record 產的收據就去對登記:
  沒登記、或收據 / 原始輸出檔登記後被改過 ⇒ 不過。件標 --needs recorded ⇒ 每張收據都要是 record 產、登記過的。
  通過支數:--count 的正規式(具名群組 passed / total,最後一個符合的算);沒給就認 unittest 的 Ran N tests、再認最後一個 N/M。
  record 回傳指令自己的 rc(腳本照舊判),收據路徑印在最後一行 RECEIPT <path>。
  一樣是防誤不防冒用:同帳號的 AI 改得到帳本檔本身。
  record 只證明「這個指令在這個頭碼上 rc 0」;包一個假指令(sh -c 'printf "Ran 400 tests…OK"')也拿得到收據。
  所以派工時用 add --cmd 記下要跑的指令,positive / default 收據的 cmd 要一樣才收件(ebb36 r2 M1):
  shlex 拆成參數清單逐個比,引號邊界不同就算不同(#531 M1);拆不開的 --cmd 在 add 時就退(rc 2)。
  帶 --cmd ⇒ 自動加 --needs recorded(#531 M2,f1fae 定):每張收據都要是 record 產、登記過的,
  手寫收據把 cmd 欄抄成派工那句也不算。不帶 --cmd 維持原狀(手寫收據照舊可以,只證明格式對)。
  刀 / chrome 收據不比 cmd(它們本來就跑別的指令),但一樣要是 record 產的。show / verify 印每張收據的 cmd。
  自己印結果、又不是 N/M 的腳本要帶 --count(具名群組 passed、total 都要有),不然算不出支數、verify 擋下
  (假紅,不是假綠;#531 L3)。例:輸出「== 232 項,通過 232」⇒
    aib ledger record --count '== (?P<total>\\d+) 項,通過 (?P<passed>\\d+)' -- bash tests/e2e.sh
  只印「PASS N / FAIL M」、沒有總數的格式現在收不了件(通過數要 = total),要收就讓腳本多印一行 N/M。
  樹乾不乾淨也算沒追蹤的檔(忽略清單裡的照舊不算;r2 L1)。
身分:--by 是自報的席碼(跟 aib dm-queue --by 一樣):**防誤派,不防冒用**。帳只保證「誰說了什麼、照規則有沒有放行」;
  同一個帳號的 AI 都改得到本機檔,所以這是分工約定,不是安全閘(11-08 f1fae 定)。
同一席分角色(f1fae 定:派工 = f1fae、整合 = f1fae 正本,兩個 session):role set 可以把兩個角色設給同一席;
  一席同時掛兩個角色時,寫入一律要帶 --as dispatch|integrate|owner 說這次戴哪頂帽子,只照那個角色判;
  沒帶 ⇒ rc 3(不猜)。--as 也記進事件,回頭查得到哪個 session 做的。
帳檔:$AIB_LEDGER_DB,否則 $EZ2_AIB_INSTANCE_HOME/state/work-ledger.sqlite(艙級),否則 ~/.ez2-aib/state/work-ledger.sqlite。
rc:0 成功 / 2 用法錯 / 3 被規則擋(沒權限、不准的轉移、重複派、找不到)/ 4 舊狀態對不上 / 5 收據不過 / 6 事件 id 衝突。
只用標準函式庫。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

STATES = ("queued", "running", "review", "accepted", "integrating", "deployed", "blocked", "blocked-human")
SIDE = ("blocked", "blocked-human")
TRANSITIONS = {
    "queued": ("running",) + SIDE,
    "running": ("review", "queued") + SIDE,
    "review": ("accepted", "queued", "running") + SIDE,
    "accepted": ("integrating", "queued") + SIDE,
    "integrating": ("deployed", "accepted") + SIDE,
    "deployed": (),
    "blocked": ("queued", "running"),
    "blocked-human": ("queued", "running"),
}
# 寫入權:目標狀態 → 能寫的角色(owner = 這件的負責席)
WRITERS = {
    "queued": ("dispatch",),
    "running": ("owner", "dispatch"),
    "review": ("owner",),
    "accepted": ("dispatch",),
    "integrating": ("integrate",),
    "deployed": ("integrate",),
    "blocked": ("owner", "dispatch", "integrate"),
    "blocked-human": ("dispatch",),
}
ROLES = ("dispatch", "integrate")
RECEIPT_SCHEMA = "aib-receipt/1"
RECEIPT_KINDS = ("positive", "knife", "default", "chrome", "other")
RECORD_PRODUCER = "aib ledger record"
NEEDS = ("chrome", "recorded")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
FUTURE_SKEW = datetime.timedelta(minutes=5)

RC_OK, RC_USAGE, RC_RULE, RC_STALE, RC_RECEIPT, RC_EVENT = 0, 2, 3, 4, 5, 6


class Refused(Exception):
    def __init__(self, rc, msg, detail=None):
        super().__init__(msg)
        self.rc, self.msg, self.detail = rc, msg, detail


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def db_path(env=None):
    env = os.environ if env is None else env
    if env.get("AIB_LEDGER_DB"):
        return Path(env["AIB_LEDGER_DB"]).expanduser()
    if env.get("EZ2_AIB_INSTANCE_HOME"):
        return Path(env["EZ2_AIB_INSTANCE_HOME"]).expanduser() / "state" / "work-ledger.sqlite"
    return Path.home() / ".ez2-aib" / "state" / "work-ledger.sqlite"


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    if fresh:   # -wal / -shm 由 SQLite 照主檔權限建,主檔 600 它們就是 600(ebb36 L6;umask 022 實測)
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS items(
        id TEXT PRIMARY KEY, batch TEXT NOT NULL, title TEXT NOT NULL, owner TEXT NOT NULL, state TEXT NOT NULL,
        head TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]', next TEXT NOT NULL DEFAULT '',
        blocker TEXT NOT NULL DEFAULT '', needs TEXT NOT NULL DEFAULT '[]', rev INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS events(
        event_id TEXT PRIMARY KEY, item_id TEXT, op TEXT NOT NULL, from_state TEXT, to_state TEXT,
        by_seat TEXT NOT NULL, at TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS events_item ON events(item_id, at);
      CREATE TABLE IF NOT EXISTS receipts(
        path TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL, output_sha256 TEXT NOT NULL, sha TEXT NOT NULL,
        kind TEXT NOT NULL, rc INTEGER NOT NULL, recorded_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS roles(role TEXT PRIMARY KEY, seat TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL);
    """)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(items)")}
    for col in ("superseded TEXT NOT NULL DEFAULT '[]'", "head_time TEXT NOT NULL DEFAULT ''",
                "expect_cmd TEXT NOT NULL DEFAULT ''"):
        if col.split()[0] not in cols:
            db.execute("ALTER TABLE items ADD COLUMN " + col)
    return db


def seat_of(by):
    s = (by or "").strip()
    if s.startswith("seat:"):
        s = s[5:]
    s = s.lstrip("@")
    if not ID_RE.match(s):
        raise Refused(RC_USAGE, "--by must be a seat code (e.g. seat:2240e), got %r" % (by,))
    return s


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def item_dict(row):
    if row is None:
        return None
    d = dict(row)
    d["evidence"] = json.loads(d["evidence"])
    d["needs"] = json.loads(d["needs"])
    d["superseded"] = json.loads(d.get("superseded") or "[]")
    return d


def roles_of(db):
    return {r["role"]: r["seat"] for r in db.execute("SELECT role, seat FROM roles")}


# ---------- 收據驗證器 ----------

def _parse_time(v):
    if not isinstance(v, str) or not v.strip():
        raise ValueError("missing")
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = datetime.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError("no time zone")
    return t


def sha_match(a, b):
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not (SHA_RE.match(a) and SHA_RE.match(b)):
        return False
    return a.startswith(b) or b.startswith(a)


def file_sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_recorded(db, path, r, receipt_sha256):
    """自稱 record 產的收據:對帳本登記。回 problems。"""
    if db is None:
        return ["cannot check the record registry"]
    row = db.execute("SELECT receipt_sha256, output_sha256 FROM receipts WHERE path=?",
                     (str(Path(path).resolve()),)).fetchone()
    if row is None:
        return ["producer says %s, but this receipt is not in the ledger's record registry" % RECORD_PRODUCER]
    probs = []
    if receipt_sha256 != row["receipt_sha256"]:
        probs.append("receipt was changed after aib ledger record wrote it")
    out = Path(str(r.get("output") or ""))
    try:
        if file_sha256(out) != row["output_sha256"]:
            probs.append("raw output was changed after aib ledger record wrote it")
    except OSError:
        pass   # 檔不在由一般檢查報
    return probs


def result_line_ok(text, passed, total):
    """原始輸出裡有沒有跟 passed / total 吻合的結果行(H3)。"""
    if re.search(r"(?<!\d)%d\s*/\s*%d(?!\d)" % (passed, total), text):
        return True
    ran = list(re.finditer(r"^Ran (\d+) tests? in ", text, re.M))
    return bool(ran) and int(ran[-1].group(1)) == total and passed == total and \
        re.search(r"^OK\b", text[ran[-1].end():], re.M) is not None


def check_receipt(path, head, now=None, head_time=None, db=None):
    """一張收據 → (kind, problems, recorded, info)。problems 空 = 這張過。
    收據檔只讀一次:驗的內容跟記進事件的雜湊是同一份 bytes(ebb36 r2 L2)。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    probs = []
    try:
        data = Path(path).read_bytes()
        r = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as e:
        return None, ["receipt unreadable: %s" % (e,)], False, {}
    if not isinstance(r, dict):
        return None, ["receipt is not a JSON object"], False, {}
    info = {"receipt_sha256": hashlib.sha256(data).hexdigest(), "output_sha256": r.get("output_sha256"),
            "cmd": r.get("cmd")}
    recorded = r.get("producer") == RECORD_PRODUCER
    if recorded:
        rp = check_recorded(db, path, r, info["receipt_sha256"])
        probs.extend(rp)
        recorded = not rp
    if r.get("dirty"):
        probs.append("recorded on a tree with uncommitted changes")
    if r.get("schema") != RECEIPT_SCHEMA:
        probs.append("schema is not %s" % RECEIPT_SCHEMA)
    kind = r.get("kind")
    if kind not in RECEIPT_KINDS:
        probs.append("kind must be one of %s" % ", ".join(RECEIPT_KINDS))
    if not sha_match(r.get("sha"), head):
        probs.append("sha %r does not match head %r" % (r.get("sha"), head))
    for f in ("cmd", "host"):
        if not isinstance(r.get(f), str) or not r.get(f).strip():
            probs.append("%s missing" % f)
    try:
        t0, t1 = _parse_time(r.get("started_at")), _parse_time(r.get("ended_at"))
        if t1 < t0:
            probs.append("ended_at is before started_at")
        if t1 > now + FUTURE_SKEW:
            probs.append("ended_at is in the future")
        if head_time and t0 < _parse_time(head_time) - FUTURE_SKEW:
            probs.append("started_at is before the head commit (%s)" % head_time)
    except (ValueError, TypeError) as e:
        probs.append("started_at / ended_at unreadable (%s)" % (e,))
    rc = r.get("rc")
    if not isinstance(rc, int) or isinstance(rc, bool):
        probs.append("rc missing or not an integer")
    elif rc != 0:
        probs.append("rc is %d, not 0" % rc)
    passed, total = r.get("passed"), r.get("total")
    counts_ok = False
    if not isinstance(passed, int) or isinstance(passed, bool) or passed < 1:
        probs.append("passed missing or < 1")
    elif not isinstance(total, int) or isinstance(total, bool):
        probs.append("total missing")
    elif passed != total:
        probs.append("passed %r != total %r" % (passed, total))
    else:
        counts_ok = True
    out = r.get("output")
    if not isinstance(out, str) or not out.strip():
        probs.append("output missing")
    else:
        p = Path(out).expanduser()
        if not p.is_absolute():
            p = Path(path).resolve().parent / p
        if not p.is_file() or p.stat().st_size == 0:
            probs.append("raw output file missing or empty: %s" % p)
        elif not r.get("output_sha256"):
            probs.append("output_sha256 missing")
        else:
            raw = p.read_bytes()
            if hashlib.sha256(raw).hexdigest() != str(r["output_sha256"]).lower():
                probs.append("output_sha256 does not match the raw output file")
            else:
                text = raw.decode("utf-8", errors="replace")
                if head and head[:7].lower() not in text.lower():
                    probs.append("raw output does not mention the head %s" % head[:7])
                if counts_ok and not result_line_ok(text, passed, total):
                    probs.append("raw output has no result line matching %d/%d" % (passed, total))
    return kind, probs, recorded, info


def norm_cmd(c):
    """指令比對用:shlex 拆成參數清單再比(多餘空白、引號寫法不同算同一個指令)。
    ★ 比清單、不比接回的字串(ebb36 #531 M1):sh -c './run.sh --all' 與 sh -c ./run.sh --all 接回去一樣,
      參數卻不同(後者 --all 變成 $0)。拆不開(引號沒收)回 None,當成對不上,不退回只比空白。"""
    try:
        return shlex.split(c or "")
    except ValueError:
        return None


def same_cmd(a, b):
    na, nb = norm_cmd(a), norm_cmd(b)
    return na is not None and nb is not None and na == nb


def verify_item(item, now=None, db=None):
    """件 → {"ok": bool, "problems": [...], "receipts": [{path, kind, ok, problems}]}。"""
    probs, out = [], []
    head = item.get("head") or ""
    if not head:
        probs.append("no head on this item (move it to review with --head)")
    ev = item.get("evidence") or []
    if not ev:
        probs.append("no receipt attached (--evidence)")
    good_kinds = []
    needs = item.get("needs") or []
    for p in ev:
        kind, rp, recorded, info = check_receipt(p, head, now, item.get("head_time") or None, db)
        want = item.get("expect_cmd") or ""
        if "recorded" in needs and not recorded and not rp:   # 帶 --cmd 的件在 add 時已自動加 recorded(#531 M2)
            rp = ["needs recorded: this receipt was not written by aib ledger record"]
        if want and kind in ("positive", "default") and not rp and not same_cmd(info.get("cmd"), want):
            rp = ["cmd %r is not the dispatched command %r" % (info.get("cmd"), want)]
        out.append(dict(info, path=p, kind=kind, ok=not rp, problems=rp, recorded=recorded))
        if rp:
            probs.extend("%s: %s" % (Path(p).name, x) for x in rp)
        else:
            good_kinds.append(kind)
    if ev and not any(k in ("positive", "default") for k in good_kinds):
        probs.append("no passing positive or default receipt (knife / chrome alone do not count)")
    if "chrome" in (item.get("needs") or []) and "chrome" not in good_kinds:
        probs.append("needs chrome: no passing chrome receipt for this head")
    return {"ok": not probs, "problems": probs, "receipts": out}


# ---------- 寫入(交易 + 事件 id 去重) ----------

def _replay_or_claim(db, event_id, op, item_id, request):
    """事件 id 已用過:內容一樣 ⇒ 回原結果;不一樣 ⇒ 衝突。沒用過 ⇒ None。"""
    row = db.execute("SELECT op, item_id, request, result FROM events WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        return None
    if row["op"] == op and row["item_id"] == item_id and row["request"] == canonical(request):
        res = json.loads(row["result"])
        res["replayed"] = True
        return res
    raise Refused(RC_EVENT, "event id %s was already used for a different request" % event_id)


def _record(db, event_id, op, item_id, frm, to, by, request, result):
    db.execute("INSERT INTO events(event_id, item_id, op, from_state, to_state, by_seat, at, request, result)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (event_id, item_id, op, frm, to, by, now_iso(), canonical(request), canonical(result)))


def hats(roles, by, owner, as_role):
    """這次寫入戴的帽子集合。一席掛兩個角色又沒帶 --as ⇒ 擋(不猜是哪個 session)。"""
    held = {r for r in ROLES if roles.get(r) == by}
    if owner is not None and by == owner:
        held.add("owner")
    if as_role:
        if as_role not in held:
            raise Refused(RC_RULE, "%s is not %s%s" % (by, as_role, " of this item" if as_role == "owner" else ""))
        return {as_role}
    if len(held & set(ROLES)) > 1:
        raise Refused(RC_RULE, "%s holds %s; say which with --as" % (by, " and ".join(sorted(held & set(ROLES)))))
    return held


def _may_write(roles, by, target, owner, as_role=None):
    return bool(hats(roles, by, owner, as_role) & set(WRITERS[target]))


def tx(db, fn):
    db.execute("BEGIN IMMEDIATE")
    try:
        res = fn()
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")
    return res


def op_role_set(db, role, seat, by, event_id, as_role=None):
    request = {"role": role, "seat": seat, "by": by, "as": as_role}

    def run():
        rep = _replay_or_claim(db, event_id, "role", None, request)
        if rep is not None:
            return rep
        roles = roles_of(db)
        if not roles.get("dispatch") and role != "dispatch":
            raise Refused(RC_RULE, "set the dispatch seat first (aib ledger role set dispatch <seat>)")
        if roles.get("dispatch") and "dispatch" not in hats(roles, by, None, as_role):
            raise Refused(RC_RULE, "only the dispatch seat (%s) can change roles" % roles["dispatch"])
        db.execute("INSERT INTO roles(role, seat, set_by, set_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(role) DO UPDATE SET seat=excluded.seat, set_by=excluded.set_by, set_at=excluded.set_at",
                   (role, seat, by, now_iso()))
        res = {"ok": True, "role": role, "seat": seat}
        _record(db, event_id, "role", None, None, None, by, request, res)
        return res
    return tx(db, run)


def op_add(db, a, by, event_id):
    if a.expect_cmd is not None and not norm_cmd(a.expect_cmd):
        raise Refused(RC_USAGE, "--cmd %r cannot be split into arguments (unclosed quote?) or is empty" % a.expect_cmd)
    needs = set(a.needs or [])
    if a.expect_cmd:
        needs.add("recorded")   # 帶 --cmd ⇒ 只收 record 產的收據:手寫收據的 cmd 欄是自己填的(#531 M2,f1fae 定)
    request = {"id": a.id, "batch": a.batch, "owner": seat_of(a.owner), "title": a.title, "next": a.next or "",
               "needs": sorted(needs), "by": by, "as": a.as_role}
    if a.expect_cmd:
        request["cmd"] = a.expect_cmd   # 沒帶就不放這個鍵:升級前用過的 event id 原樣重送仍是 replay(#531 L1)

    def run():
        rep = _replay_or_claim(db, event_id, "add", a.id, request)
        if rep is not None:
            return rep
        roles = roles_of(db)
        if not roles.get("dispatch"):
            raise Refused(RC_RULE, "no dispatch seat yet; run: aib ledger role set dispatch <seat> --by <seat>")
        if not _may_write(roles, by, "queued", None, a.as_role):
            raise Refused(RC_RULE, "only the dispatch seat (%s) can queue work" % roles["dispatch"])
        old = db.execute("SELECT state FROM items WHERE id=?", (a.id,)).fetchone()
        if old is not None:
            raise Refused(RC_RULE, "%s is already dispatched (state %s); not queued twice" % (a.id, old["state"]))
        t = now_iso()
        db.execute("INSERT INTO items(id, batch, title, owner, state, next, needs, expect_cmd, rev, created_at, updated_at,"
                   " updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (a.id, a.batch, a.title, request["owner"], "queued", request["next"], json.dumps(request["needs"]),
                    request.get("cmd", ""), 1, t, t, by))
        res = {"ok": True, "id": a.id, "state": "queued", "rev": 1}
        _record(db, event_id, "add", a.id, None, "queued", by, request, res)
        return res
    return tx(db, run)


def git_commit(repo, sha):
    """(完整 sha, commit 時間 ISO)— 查不到就擋(M4)。"""
    import subprocess
    p = subprocess.run(["git", "-C", str(Path(repo).expanduser()), "show", "-s", "--format=%H %cI", sha + "^{commit}"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0 or not p.stdout.strip():
        raise Refused(RC_RULE, "%s is not a commit in %s" % (sha, repo))
    full, when = p.stdout.split()[:2]
    return full.lower(), when


def apply_move(item, a, request, by, roles):
    """規則全在這裡(交易內外共用);改 item、回 (blocker, next)。"""
    if item["state"] != a.from_state:
        raise Refused(RC_STALE, "%s is %s now, not %s; nothing written" % (a.id, item["state"], a.from_state))
    if a.to not in TRANSITIONS[a.from_state]:
        raise Refused(RC_RULE, "%s -> %s is not an allowed move" % (a.from_state, a.to))
    worn = hats(roles, by, item["owner"], a.as_role)
    if not worn & set(WRITERS[a.to]):
        raise Refused(RC_RULE, "%s cannot write state %s (writers: %s)" % (by, a.to, ", ".join(WRITERS[a.to])))
    if a.from_state == "blocked-human" and "dispatch" not in worn:
        raise Refused(RC_RULE, "only the dispatch seat can release blocked-human (it waits for a human)")
    if a.to == "accepted" and by == item["owner"]:
        raise Refused(RC_RULE, "%s owns %s and cannot accept its own work" % (by, a.id))
    if request["head"]:
        if not SHA_RE.match(request["head"]):
            raise Refused(RC_USAGE, "--head must be a hex commit sha (7+ chars)")
        if a.to != "review":
            raise Refused(RC_RULE, "the head is set only when moving to review")
        head, when = request["head"], ""
        if request.get("repo"):
            head, when = request["git"]
        if item["head"] and not sha_match(head, item["head"]):
            item["superseded"].append({"head": item["head"], "evidence": item["evidence"]})
            item["evidence"] = []
        item["head"], item["head_time"] = head, when
    if a.to == "review" and not item["head"]:
        raise Refused(RC_RULE, "moving to review needs --head")
    if a.to in SIDE and not (a.blocker or "").strip():
        raise Refused(RC_USAGE, "moving to %s needs --blocker" % a.to)
    for p in request["evidence"]:
        if p not in item["evidence"]:
            item["evidence"].append(p)
    return ((a.blocker or "").strip() if a.to in SIDE else ""), (item["next"] if a.next is None else a.next)


def op_move(db, a, by, event_id):
    evidence = [str(Path(p).expanduser().resolve()) for p in (a.evidence or [])]
    request = {"id": a.id, "from": a.from_state, "to": a.to, "head": (a.head or "").lower(), "evidence": evidence,
               "next": a.next, "blocker": a.blocker, "by": by, "as": a.as_role,
               "repo": str(Path(a.repo).expanduser().resolve()) if getattr(a, "repo", None) else None}
    if request["repo"] and request["head"] and SHA_RE.match(request["head"]):
        request["git"] = list(git_commit(request["repo"], request["head"]))
    # 收件要讀檔、算雜湊:先在交易外驗一次(L5);交易內只確認件沒被別人改過(rev 一樣),不在寫鎖裡讀大檔
    pre = None
    if a.to == "accepted":
        snap = item_dict(db.execute("SELECT * FROM items WHERE id=?", (a.id,)).fetchone())
        if snap is not None and snap["state"] == a.from_state:
            try:
                apply_move(snap, a, request, by, roles_of(db))
            except Refused:
                snap = None
            if snap is not None:
                pre = (snap["rev"], verify_item(snap, db=db))

    def run():
        rep = _replay_or_claim(db, event_id, "move", a.id, request)
        if rep is not None:
            return rep
        item = item_dict(db.execute("SELECT * FROM items WHERE id=?", (a.id,)).fetchone())
        if item is None:
            raise Refused(RC_RULE, "no such item: %s" % a.id)
        blocker, nxt = apply_move(item, a, request, by, roles_of(db))
        verdict = None
        if a.to == "accepted":
            if pre is None or pre[0] != item["rev"]:
                raise Refused(RC_STALE, "%s changed while its receipts were checked; nothing written" % a.id)
            verdict = pre[1]
            if not verdict["ok"]:
                raise Refused(RC_RECEIPT, "receipts do not prove %s at %s; not accepted" % (a.id, item["head"] or "-"),
                              verdict)
        rev = item["rev"] + 1
        db.execute("UPDATE items SET state=?, head=?, head_time=?, evidence=?, superseded=?, next=?, blocker=?, rev=?,"
                   " updated_at=?, updated_by=? WHERE id=? AND state=? AND rev=?",
                   (a.to, item["head"], item.get("head_time") or "", json.dumps(item["evidence"]),
                    json.dumps(item["superseded"]), nxt, blocker, rev, now_iso(), by, a.id, a.from_state, item["rev"]))
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise Refused(RC_STALE, "%s changed underneath; nothing written" % a.id)
        res = {"ok": True, "id": a.id, "from": a.from_state, "state": a.to, "rev": rev, "head": item["head"]}
        if verdict is not None:
            res["verified"] = [{"path": r["path"], "receipt_sha256": r.get("receipt_sha256"),
                                "output_sha256": r.get("output_sha256")} for r in verdict["receipts"] if r["ok"]]
        _record(db, event_id, "move", a.id, a.from_state, a.to, by, request, res)
        return res
    return tx(db, run)


# ---------- 讀 ----------

def get_item(db, item_id):
    return item_dict(db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def history(db, item_id):
    out = []
    for r in db.execute("SELECT event_id, op, from_state, to_state, by_seat, at, request, result FROM events "
                        "WHERE item_id=? ORDER BY rowid", (item_id,)):
        d = dict(r)
        d["as"] = json.loads(d.pop("request")).get("as")
        res = json.loads(d.pop("result"))
        if res.get("verified"):
            d["verified"] = res["verified"]
        out.append(d)
    return out


def print_item(it, hist=None):
    print("%s  [%s]  batch %s  owner %s  rev %d" % (it["id"], it["state"], it["batch"], it["owner"], it["rev"]))
    print("  title:    %s" % it["title"])
    print("  head:     %s" % (it["head"] or "-"))
    print("  next:     %s" % (it["next"] or "-"))
    if it["blocker"]:
        print("  blocker:  %s" % it["blocker"])
    if it["needs"]:
        print("  needs:    %s" % ", ".join(it["needs"]))
    if it.get("expect_cmd"):
        print("  cmd:      %s" % it["expect_cmd"])
    for p in it["evidence"]:
        print("  evidence: %s" % p)
    for old in it.get("superseded") or []:
        print("  superseded (old head %s): %d receipt(s)" % (old["head"][:12], len(old["evidence"])))
    print("  updated:  %s by %s" % (it["updated_at"], it["updated_by"]))
    for h in hist or []:
        print("  - %s %s %s -> %s by %s%s (%s)" % (h["at"], h["op"], h["from_state"] or "", h["to_state"] or "",
                                                    h["by_seat"], (" as " + h["as"]) if h.get("as") else "",
                                                    h["event_id"]))


def count_passed(text, rc, regex=None):
    """輸出 → (passed, total)。認不出 ⇒ (None, None)(verify 會擋)。"""
    if regex:
        ms = list(re.finditer(regex, text, re.M))
        if not ms:
            return None, None
        g = ms[-1].groupdict()
        p = g.get("passed")
        t = g.get("total")
        return (int(p) if p is not None else None), (int(t) if t is not None else None)
    ran = list(re.finditer(r"^Ran (\d+) tests? in ", text, re.M))
    if ran:
        total = int(ran[-1].group(1))
        tail = text[ran[-1].end():]
        bad = sum(int(x) for x in re.findall(r"(?:failures|errors)=(\d+)", tail))
        if re.search(r"^OK\b", tail, re.M) or re.search(r"^FAILED\b", tail, re.M):
            return total - bad, total
        return None, total
    # 退回:只認同一行有 PASS / OK / 綠 / passed / 通過 的 N/M(日期 11/08 不算;r2 L3)
    last = None
    for line in text.splitlines():
        if re.search(r"PASS|\bOK\b|綠|passed|通過", line, re.I):
            for m in re.finditer(r"(\d+)\s*/\s*(\d+)", line):
                last = m
    if last:
        return int(last.group(1)), int(last.group(2))
    return None, None


def git_out(tree, *args):
    p = subprocess.run(["git", "-C", str(tree)] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Refused(RC_USAGE, "git %s in %s failed: %s" % (" ".join(args), tree, (p.stderr or "").strip()[-200:]))
    return p.stdout


def cmd_record(db, a):
    cmd = list(a.command)
    if cmd[:1] == ["--"]:
        cmd = cmd[1:]
    if not cmd:
        raise Refused(RC_USAGE, "usage: aib ledger record [--kind K] [--tree DIR] -- <command …>")
    tree = Path(a.tree).expanduser().resolve()
    sha = git_out(tree, "rev-parse", "HEAD").strip().lower()
    if git_out(tree, "status", "--porcelain", "--untracked-files=normal").strip():   # 沒追蹤的檔也會改結果(r2 L1)
        raise Refused(RC_RULE, "%s has uncommitted changes; a receipt would not prove %s — commit first" % (tree, sha[:12]))
    out_dir = Path(a.out).expanduser().resolve() if a.out else db_path().parent / "receipts"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    raw = out_dir / ("%s-%s-%s.log" % (sha[:12], a.kind, stamp))
    rec_path = out_dir / ("%s-%s-%s.json" % (sha[:12], a.kind, stamp))
    started = now_iso()
    with open(str(raw), "wb") as f:
        try:
            p = subprocess.Popen(cmd, cwd=str(tree), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL)
        except OSError as e:
            raise Refused(RC_USAGE, "cannot run %s: %s" % (cmd[0], e))
        for chunk in iter(lambda: p.stdout.read(65536), b""):
            f.write(chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
        rc = p.wait()
    ended = now_iso()
    text = raw.read_text(encoding="utf-8", errors="replace")
    passed, total = count_passed(text, rc, a.count)
    # 包裝器自己的結尾行:頭碼 + 結果(verify 要原始輸出看得到頭碼和 passed/total;算完支數才加,不影響計數)
    with open(str(raw), "a", encoding="utf-8") as f:
        f.write("\n[aib ledger record] sha %s rc %d result %s/%s\n" % (sha, rc, passed, total))
    rec = {"schema": RECEIPT_SCHEMA, "producer": RECORD_PRODUCER, "kind": a.kind, "sha": sha, "tree": str(tree),
           "dirty": False, "cmd": shlex.join(cmd) if hasattr(shlex, "join") else " ".join(shlex.quote(c) for c in cmd),
           "host": socket.gethostname(), "started_at": started, "ended_at": ended, "rc": rc, "passed": passed,
           "total": total, "output": str(raw), "output_sha256": file_sha256(raw)}
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tx(db, lambda: db.execute("INSERT INTO receipts(path, receipt_sha256, output_sha256, sha, kind, rc, recorded_at)"
                              " VALUES (?,?,?,?,?,?,?)", (str(rec_path), file_sha256(rec_path), rec["output_sha256"],
                                                          sha, a.kind, rc, ended)))
    print("RECEIPT %s (rc %d, passed %s/%s, sha %s)" % (rec_path, rc, passed, total, sha[:12]))
    return rc


def build_parser():
    p = argparse.ArgumentParser(prog="aib ledger", description="Cabin work ledger: one row per piece of work, "
                                "every state change in a transaction, acceptance only with receipts bound to the head. "
                                "--by is a self-declared seat code: the ledger guards against mis-dispatch, not "
                                "impersonation (any AI on the same account can edit local files).")
    sub = p.add_subparsers(dest="cmd")

    def mut(sp):
        sp.add_argument("--by", required=True, help="your seat code, e.g. seat:2240e (self-declared: guards against "
                        "mis-dispatch, not impersonation)")
        sp.add_argument("--as", dest="as_role", choices=ROLES + ("owner",),
                        help="which role you act as; required when your seat holds both dispatch and integrate")
        sp.add_argument("--event-id", help="same id sent twice is applied once (default: a new random id)")
        sp.add_argument("--json", action="store_true")

    r = sub.add_parser("role", help="who dispatches / who integrates")
    rs = r.add_subparsers(dest="role_cmd")
    r1 = rs.add_parser("set")
    r1.add_argument("role", choices=ROLES)
    r1.add_argument("seat")
    mut(r1)
    r2 = rs.add_parser("list")
    r2.add_argument("--json", action="store_true")

    ad = sub.add_parser("add", help="queue a piece of work (dispatch seat)")
    ad.add_argument("id")
    ad.add_argument("--batch", required=True)
    ad.add_argument("--owner", required=True)
    ad.add_argument("--title", required=True)
    ad.add_argument("--next")
    ad.add_argument("--needs", action="append", choices=NEEDS)
    ad.add_argument("--cmd", dest="expect_cmd", help="the command positive / default receipts must have run (argument list compared at acceptance); "
                    "implies --needs recorded: only receipts written by aib ledger record count")
    mut(ad)

    mv = sub.add_parser("move", help="change state (compare-and-set on --from)")
    mv.add_argument("id")
    mv.add_argument("--from", dest="from_state", required=True, choices=STATES)
    mv.add_argument("--to", required=True, choices=STATES)
    mv.add_argument("--head")
    mv.add_argument("--repo", help="git tree to check --head against (records its commit time)")
    mv.add_argument("--evidence", action="append", help="receipt JSON (schema aib-receipt/1); repeatable")
    mv.add_argument("--next")
    mv.add_argument("--blocker")
    mut(mv)

    sh = sub.add_parser("show")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--state", choices=STATES)
    ls.add_argument("--batch")
    ls.add_argument("--owner")
    ls.add_argument("--json", action="store_true")
    rc_ = sub.add_parser("record", help="run a command and write its receipt yourself: rc, times, host, raw output, "
                         "sha256 and the passed count come from the wrapper, not from the person who ran it")
    rc_.add_argument("--kind", default="positive", choices=RECEIPT_KINDS)
    rc_.add_argument("--tree", default=".", help="git tree the command tests (its HEAD is the receipt's sha)")
    rc_.add_argument("--out", help="folder for the receipt and raw output (default: next to the ledger)")
    rc_.add_argument("--count", help="regex with named groups passed (and total); the last match counts")
    rc_.add_argument("command", nargs=argparse.REMAINDER)
    vf = sub.add_parser("verify", help="check the item's receipts against its head (writes nothing)")
    vf.add_argument("id")
    vf.add_argument("--json", action="store_true")
    return p


def emit(a, res, line):
    if getattr(a, "json", False):
        print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    else:
        print(line)


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd or (a.cmd == "role" and not a.role_cmd):
        p.print_help()
        return RC_USAGE
    if getattr(a, "id", None) is not None and not ID_RE.match(a.id):
        print("aib ledger: item id must match %s" % ID_RE.pattern, file=sys.stderr)
        return RC_USAGE
    db = connect(db_path())
    try:
        if a.cmd == "role" and a.role_cmd == "list":
            rows = [dict(r) for r in db.execute("SELECT role, seat, set_by, set_at FROM roles ORDER BY role")]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False))
            else:
                for r in rows:
                    print("%-10s %s (set by %s at %s)" % (r["role"], r["seat"], r["set_by"], r["set_at"]))
                if not rows:
                    print("no roles set")
            return RC_OK
        if a.cmd == "record":
            return cmd_record(db, a)
        if a.cmd in ("show", "verify"):
            it = get_item(db, a.id)
            if it is None:
                print("aib ledger: no such item: %s" % a.id, file=sys.stderr)
                return RC_RULE
            if a.cmd == "show":
                hist = history(db, a.id)
                if a.json:
                    print(json.dumps(dict(it, history=hist), ensure_ascii=False, sort_keys=True))
                else:
                    print_item(it, hist)
                return RC_OK
            v = verify_item(it, db=db)
            v["id"], v["head"] = it["id"], it["head"]
            if a.json:
                print(json.dumps(v, ensure_ascii=False, sort_keys=True))
            else:
                for r in v["receipts"]:
                    print("%s %-8s %s%s" % ("OK  " if r["ok"] else "FAIL", r["kind"] or "?", r["path"],
                                            "" if r["ok"] else "  (" + "; ".join(r["problems"]) + ")"))
                    if r.get("cmd"):
                        print("     cmd: %s" % r["cmd"])
                print("RESULT %s %s at %s%s" % ("OK" if v["ok"] else "FAIL", it["id"], it["head"] or "-",
                                                 "" if v["ok"] else ": " + "; ".join(v["problems"])))
            return RC_OK if v["ok"] else RC_RECEIPT
        if a.cmd == "list":
            q, args = "SELECT * FROM items WHERE 1=1", []
            for col, val in (("state", a.state), ("batch", a.batch), ("owner", seat_of(a.owner) if a.owner else None)):
                if val:
                    q += " AND %s=?" % col
                    args.append(val)
            rows = [item_dict(r) for r in db.execute(q + " ORDER BY batch, created_at", args)]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, sort_keys=True))
            else:
                for it in rows:
                    print("%-14s %-24s %-8s %-10s %s%s" % (it["state"], it["id"], it["owner"], it["head"][:10] or "-",
                                                         it["title"], ("  BLOCKED: " + it["blocker"]) if it["blocker"] else ""))
                if not rows:
                    print("no items")
            return RC_OK
        by = seat_of(a.by)
        event_id = a.event_id or ("ev-" + uuid.uuid4().hex)
        if not ID_RE.match(event_id):
            raise Refused(RC_USAGE, "--event-id must match %s" % ID_RE.pattern)
        if a.cmd == "role":
            res = op_role_set(db, a.role, seat_of(a.seat), by, event_id, a.as_role)
            emit(a, res, "%s%s is now %s" % ("(replayed) " if res.get("replayed") else "", res["seat"], res["role"]))
        elif a.cmd == "add":
            res = op_add(db, a, by, event_id)
            emit(a, res, "%squeued %s (rev %d)" % ("(replayed) " if res.get("replayed") else "", res["id"], res["rev"]))
        else:
            res = op_move(db, a, by, event_id)
            emit(a, res, "%s%s %s -> %s (rev %d)" % ("(replayed) " if res.get("replayed") else "", res["id"],
                                                     res["from"], res["state"], res["rev"]))
        return RC_OK
    except Refused as e:
        if getattr(a, "json", False):
            print(json.dumps({"ok": False, "rc": e.rc, "error": e.msg, "detail": e.detail}, ensure_ascii=False,
                             sort_keys=True))
        print("aib ledger: %s" % e.msg, file=sys.stderr)
        if e.detail and e.detail.get("problems"):
            for x in e.detail["problems"]:
                print("  - %s" % x, file=sys.stderr)
        return e.rc
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
