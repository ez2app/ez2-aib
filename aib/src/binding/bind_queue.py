# SPDX-License-Identifier: Apache-2.0
"""E2.6 綁定佇列:hub 新 AI 的 BIND 申請進決策收件匣。

大綱原文:hub 新 AI 一律 aib 啟動器開 + BIND 就地輸入 + 綁定佇列入決策收件匣。

為什麼要有佇列:綁定是把一個 AI 從「查無此人」升成「registry 認得的主AI」,
那是身份升權,不能由被綁的人自己說了算(原理 1)。所以流程是
  啟動器開窗 -> 就地收 BIND 資料 -> 進佇列 -> **人**核可 -> (EA.1 後)真綁
每一步都落一筆事件,佇列本身是磁碟上可 grep 的檔案(原理 3)。

誠實樁的邊界(EA.1 未完成):
  - 狀態機**沒有 bound 這個值**。核可後停在 approved_pending_ea1,
    因為真綁需要 ACB 側的 registry 收據,而那個收據現在拿不到。
    寧可讓狀態難看,也不要寫一個看起來已綁的假綠燈。
  - 申請者不得自帶任何驗證欄位;requested 內容只收機器觀測得到的事實。

收件匣落點:$EZ2_DECISION_INBOX(預設 ~/.ez2-aib/decision-inbox)。
E5.3 設計稿把決策收件匣定為 L7 人層共用基建,綁定佇列是它第一個租戶;
item 形狀刻意保持通用(kind/status/created_event_id/…),之後多幾種決策
只是多幾個 kind,不必另開一個匣。
"""

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ledger"))
sys.path.insert(0, os.path.join(_HERE, "..", "collectors"))
sys.path.insert(0, os.path.join(_HERE, "..", "cockpit"))   # ED.1 (b):狀態根走 seats_dir_lib 單源

from ledger import Ledger  # noqa: E402
from collector_common import clip  # noqa: E402

KIND = "bind_request"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved_pending_ea1"   # 刻意沒有 bound:真綁需 EA.1 的收據
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

PROVIDERS = ("claude", "codex", "gemini", "agy", "copilot", "grok", "deepseek", "nvidia", "openrouter", "typesafe", "openrouterjev", "sand", "grokbot", "codexbot", "claudebot", "musebot")
_ROLE_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
# 人類核可者一律 human: 開頭;AI 的 sig 形如 role@hex/Engine,擋在正則之外
_HUMAN_RE = re.compile(r"^human:[^\s]{1,64}$")


class BindError(ValueError):
    """申請或裁決不合規 —— fail-closed,不進佇列也不改狀態。"""


def _state_root():
    """ED.1 (b):狀態根單源 `seats_dir_lib.state_root()`(--state-root > EZ2_AIB_STATE_ROOT >
    EZ2_AIB_HOME > ~/.ez2-aib,root 08-22 17:53 採納),不在這裡再抄一份 env 鏈。延後匯入:
    本模組被 decision_inbox / bind_one 等多處匯入,匯入順序不該決定能不能找到它。"""
    from seats_dir_lib import state_root
    return state_root()[0]


def inbox_dir(base=None):
    """單路徑 env 仍優先(相容);沒設才落在狀態根下——只設根也要找得到(ED.1 (b))。"""
    return base or os.environ.get("EZ2_DECISION_INBOX") or \
        os.path.join(_state_root(), "decision-inbox")


def default_ledger_path():
    """帳本預設:EZ2_AIB_LEDGER 優先(相容),否則 <狀態根>/ledger/aib-ledger.jsonl。
    decision_inbox / route_register / seat_onboard 同一條帳本鏈共用這一支,不各抄一份。"""
    return os.environ.get("EZ2_AIB_LEDGER") or \
        os.path.join(_state_root(), "ledger", "aib-ledger.jsonl")


def _item_path(base, request_id):
    return os.path.join(inbox_dir(base), "%s.json" % request_id)


def make_request(provider, role, session_uuid, host, engine=None, pid=None):
    """組一筆申請(純資料)。只收機器觀測得到的事實,不收任何驗證宣稱。"""
    if provider not in PROVIDERS:
        raise BindError("未知 provider:%s" % provider)
    if not role or not _ROLE_RE.match(str(role)):
        raise BindError("role 必須是小寫英數與連字號:%r" % role)
    if not session_uuid or not _UUID_RE.match(str(session_uuid)):
        raise BindError("session_uuid 形狀不合:%r" % session_uuid)
    if not host:
        raise BindError("缺 host")
    return {
        "provider": provider,
        "role": str(role),
        "session_uuid": str(session_uuid),
        "host": str(host),
        "engine": str(engine or provider),
        "pid": int(pid) if pid else None,
    }


def request_id_of(request):
    """申請 id = provider:role:session_uuid。同一個 session 重跑啟動器不會
    長出第二張卡(冪等),因為那是同一次綁定意圖。"""
    return "bind-%s-%s-%s" % (request["provider"], request["role"],
                              request["session_uuid"])


def _write_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_item(request_id, base=None):
    try:
        with open(_item_path(base, request_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def list_items(base=None, status=None):
    d = inbox_dir(base)
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                item = json.load(fh)
        except (OSError, ValueError):
            continue          # 壞卡不讓整個列表爆掉(回饋器永不擋)
        if item.get("kind") != KIND:
            continue
        if status and item.get("status") != status:
            continue
        out.append(item)
    return out


def _actor(request):
    """申請者的 actor:身份未經驗證,trust 一律 t0_unbound —— 這正是要綁的原因。"""
    return {
        "ai_id": "%s:%s:%s" % (request["engine"], request["host"],
                               request["session_uuid"]),
        "sig": "%s@%s/%s" % (request["role"], request["session_uuid"][-5:],
                             request["engine"].capitalize()),
        "kind": "main",
        "engine": request["engine"],
        "trust": "t0_unbound",
    }


def enqueue(request, ledger_path, base=None, now_ms=None):
    """申請進佇列並落一筆 decision.requested。

    已有同 id 的卡就不再建(冪等),回 (item, created=False)。
    """
    rid = request_id_of(request)
    existing = load_item(rid, base)
    if existing:
        return existing, False

    led = Ledger(ledger_path, node_id="bind-queue")
    event = led.append({
        "kind": "decision.requested",
        "actor": _actor(request),
        "body": {
            "summary": {
                "zh-TW": clip("BIND 申請:%s/%s(session %s)候人核可" % (
                    request["provider"], request["role"],
                    request["session_uuid"][-5:])),
                "en": clip("bind request: %s/%s (session %s) awaiting human approval" % (
                    request["provider"], request["role"],
                    request["session_uuid"][-5:])),
            },
            "subject": {"kind": "decision", "id": rid},
        },
        "causation": {"causation_id": None, "correlation_id": "E2.6"},
    }, wall_ms=now_ms)

    item = {
        "schema_version": "0.1.0-draft",
        "kind": KIND,
        "request_id": rid,
        "status": STATUS_PENDING,
        "requested": request,
        "created_event_id": event["event_id"],
        "created_at_ms": event["hlc"]["wall_ms"],
        "resolution": None,
        "_honest_stub": "EA.1 未完成:核可只到 approved_pending_ea1,不代表已綁定",
    }
    _write_atomic(_item_path(base, rid), item)
    return item, True


def resolve(request_id, decision, approver, ledger_path, base=None,
            reason=None, now_ms=None):
    """人裁決一張卡。decision = approve | reject。

    approver 必須是 human:<名字> —— AI 不得核可自己或彼此的綁定申請。
    這不是禮貌問題:綁定是身份升權,由 AI 互批就等於系統自己給自己發證。
    """
    if decision not in ("approve", "reject"):
        raise BindError("decision 必須是 approve 或 reject")
    if not approver or not _HUMAN_RE.match(str(approver)):
        raise BindError("approver 必須是 human:<名字>(AI 不得核可綁定申請)")
    item = load_item(request_id, base)
    if item is None:
        raise BindError("查無此申請:%s" % request_id)
    if item["status"] != STATUS_PENDING:
        raise BindError("此申請已裁決過(%s),不重複裁決" % item["status"])

    new_status = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
    led = Ledger(ledger_path, node_id="bind-queue")
    event = led.append({
        "kind": "decision.resolved",
        "actor": {"ai_id": approver, "sig": "human@0000/Human", "kind": "human"},
        "body": {
            "summary": {
                "zh-TW": clip("BIND 申請 %s 由 %s %s%s" % (
                    request_id, approver,
                    "核可(仍待 EA.1 真綁)" if decision == "approve" else "駁回",
                    ("(%s)" % reason) if reason else "")),
                "en": clip("bind request %s %s by %s%s" % (
                    request_id,
                    "approved (real bind still pending EA.1)"
                    if decision == "approve" else "rejected",
                    approver, (" (%s)" % reason) if reason else "")),
            },
            "subject": {"kind": "decision", "id": request_id},
        },
        "causation": {"causation_id": item["created_event_id"],
                      "correlation_id": "E2.6"},
    }, wall_ms=now_ms)

    item["status"] = new_status
    item["resolution"] = {
        "decision": decision,
        "approver": approver,
        "reason": reason,
        "event_id": event["event_id"],
        "resolved_at_ms": event["hlc"]["wall_ms"],
    }
    _write_atomic(_item_path(base, request_id), item)
    return item


def _main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="bind_queue.py", add_help=True)
    p.add_argument("action", choices=["request", "list", "approve", "reject"])
    p.add_argument("--provider"); p.add_argument("--role")
    p.add_argument("--session-uuid"); p.add_argument("--host")
    p.add_argument("--engine"); p.add_argument("--pid", type=int)
    p.add_argument("--request-id"); p.add_argument("--approver")
    p.add_argument("--reason"); p.add_argument("--status")
    p.add_argument("--inbox"); p.add_argument("--ledger")
    p.add_argument("--now-ms", type=int)
    args = p.parse_args(argv)

    ledger = args.ledger or default_ledger_path()
    try:
        if args.action == "request":
            req = make_request(args.provider, args.role, args.session_uuid,
                               args.host or os.uname().nodename,
                               args.engine, args.pid)
            item, created = enqueue(req, ledger, args.inbox, args.now_ms)
            item["_created"] = created
            print(json.dumps(item, ensure_ascii=False))
            return 0
        if args.action == "list":
            print(json.dumps(list_items(args.inbox, args.status),
                             ensure_ascii=False))
            return 0
        item = resolve(args.request_id, "approve" if args.action == "approve"
                       else "reject", args.approver, ledger, args.inbox,
                       args.reason, args.now_ms)
        print(json.dumps(item, ensure_ascii=False))
        return 0
    except BindError as err:
        print(str(err), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
