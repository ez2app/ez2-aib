#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib page oneshot`:一份 intent JSON → v3.109 組版契約 → **只寫草稿** → 印預覽確認頁網址。

the maintainer 09-29 19:00:「AIB 裡一句話建整頁 ez2.page」。一句話 → intent 是席上 AI 做的(skill
`skills/ez2-page-oneshot/SKILL.md`);這支負責 intent → 草稿,且把四件事守死:

1. **只寫草稿。** 只准五條路(GET /draft、GET /blocks、POST /draft、PUT /profile、PUT /appearance),
   其他一律拒;寫之前先驗伺服器是 v3.109 草稿語意(GET /draft 頂層有 profile 與 billable_block_count;
   舊伺服器的 PUT 會直接改線上頁 ⇒ 一筆都不寫);每個 PUT 的回應都要有 `draft` 鍵,沒有就立刻停。
   這個套件裡沒有發布那條路;發布永遠是人在預覽確認頁按。
2. **憑證不外流。** 憑證只在本行程(PageApi 的 Authorization 標頭);組版核心(node)拿不到,
   argv/env 都沒有它;印到畫面的每一行(含伺服器回來的錯誤內容)先過 `redact`。
3. **clear_existing 嚴格。** 只收 JSON 的 true / false;沒寫 ⇒ 補 false(保留現有內容)。
   組版器本身收 "true"/"false" 字串、有內容時省略＝true(整份取代)——這裡比它嚴,因為席上 AI
   多寫一個引號就會把人手改的草稿全刪。true 還要再帶 `--confirm-clear` 才送。
4. **--dry-run 零寫入。** 讀照讀(GET 不扣額度),寫入改由本支回假的成功回應並記成計畫印出來。

組版與寫入順序不在這裡重寫:跑的是 vendor/ez2-page-oneshot/ 裡原封不動的 ez2-ai 原檔
(MANIFEST.json 記來源 commit),透過 oneshot_core.js 把每個請求交回本支代打。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from api import ApiError, TransportError

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir, os.pardir))
CORE = os.path.join(HERE, "oneshot_core.js")
MARK = "\x1eAIBONESHOT "
NODE_ENV = "AIB_NODE"
DRY_REVISION = "dryrun_not_written"

# 白名單:(方法, 路徑)。★ 刀對準這張表:多放一條,「只寫草稿」那一格必須紅。
ALLOWED_ROUTES = frozenset({
    ("GET", "/draft"),
    ("GET", "/blocks"),
    ("POST", "/draft"),
    ("PUT", "/profile"),
    ("PUT", "/appearance"),
})
WRITE_METHODS = frozenset({"POST", "PUT"})
TOKEN_SHAPE_RE = re.compile(r"ezp_[A-Za-z0-9_\-]{8,}")
BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


class OneshotError(Exception):
    """給人看的一句話(已遮憑證)。"""


def redact(text, token=None):
    """把憑證從任何要印出去的字串裡拿掉:本機那把的字面、任何 ezp_ 形狀、Bearer 後面那段。"""
    text = "" if text is None else str(text)
    if token:
        text = text.replace(token, "ezp_***")
    text = TOKEN_SHAPE_RE.sub("ezp_***", text)
    return BEARER_RE.sub(r"\1***", text)


# ---- intent ------------------------------------------------------------------

def load_intent(text, confirm_clear=False):
    """吃 intent JSON(物件)。回 (intent, clear_existing)。

    ★ clear_existing:只收 JSON 布林;沒寫補 False。刀對準下面那個 `is not bool`。
    """
    try:
        intent = json.loads(text)
    except ValueError as exc:
        raise OneshotError("intent 不是合法的 JSON:%s" % exc)
    if not isinstance(intent, dict):
        raise OneshotError("intent 要是一個 JSON 物件({...}),不是陣列或字串")
    if "clear_existing" in intent:
        value = intent["clear_existing"]
        if type(value) is not bool:
            raise OneshotError(
                "clear_existing 只收 JSON 的 true 或 false(沒加引號),收到 %s;什麼都沒送"
                % json.dumps(value, ensure_ascii=False))
    else:
        intent["clear_existing"] = False
    clear = intent["clear_existing"]
    if clear and not confirm_clear:
        raise OneshotError(
            "intent 寫了 clear_existing:true = 刪掉草稿現有的全部區塊再重建(人在編輯器手改、還沒發布的也會刪)。"
            "確定要清空就加 --confirm-clear 重跑;要保留現有內容請改成 false。什麼都沒送")
    return intent, clear


# ---- node 組版核心 -------------------------------------------------------------

def find_node(environ):
    candidate = environ.get(NODE_ENV) or shutil.which("node", path=environ.get("PATH"))
    if not candidate:
        for guess in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"):
            if os.path.exists(guess):
                candidate = guess
                break
    if not candidate:
        raise OneshotError("找不到 node(組版器是 JS):先裝 Node.js 18 以上,或用 %s 指定路徑" % NODE_ENV)
    return candidate


def vendor_dir(root=None):
    return os.path.join(root or PRODUCT_ROOT, "vendor", "ez2-page-oneshot")


def _core_env(environ, token, root=None):
    """給 node 的環境:不含任何帶憑證字面的變數(本來就不該有,這裡再濾一次)。"""
    env = {k: v for k, v in environ.items() if not (token and token in str(v))}
    env["AIB_ONESHOT_VENDOR"] = vendor_dir(root)
    return env


def run_core(request, environ, broker=None, token=None, root=None):
    """跑一次 oneshot_core.js。apply 時每個請求交給 broker(method, route, body) -> (status, json)。回 result dict。"""
    node = find_node(environ)
    if not os.path.isdir(vendor_dir(root)):
        raise OneshotError("找不到組版器抄本 %s(產品樹不完整)" % vendor_dir(root))
    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8")   # 不用管線:stderr 塞滿 64KB 會跟讀 stdout 互鎖
    proc = subprocess.Popen([node, CORE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=errfile, env=_core_env(environ, token, root),
                            text=True, encoding="utf-8", bufsize=1)
    result = None
    try:
        proc.stdin.write(MARK + json.dumps(request, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            if not line.startswith(MARK):
                continue
            msg = json.loads(line[len(MARK):])
            if msg.get("type") == "request":
                if broker is None:
                    status, payload = 599, {"error": "aib_oneshot_no_broker"}
                else:
                    status, payload = broker(msg.get("method"), msg.get("route"), msg.get("body"))
                proc.stdin.write(MARK + json.dumps({"type": "response", "id": msg.get("id"),
                                                    "status": status, "json": payload},
                                                   ensure_ascii=False) + "\n")
                proc.stdin.flush()
            elif msg.get("type") == "result":
                result = msg
                break
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        errfile.seek(0)
        err = errfile.read()
        errfile.close()
    if result is None:
        raise OneshotError("組版器沒有回結果(node rc=%s):%s" % (proc.returncode, redact(err, token)[-300:]))
    return result


# ---- 代打:白名單 + 只寫草稿 ------------------------------------------------------

class Broker:
    """組版核心的 api(method, route, body)。記下每一筆;違規一次就再也不寫。"""

    def __init__(self, api, dry_run=False):
        self.api = api
        self.dry_run = dry_run
        self.preflight_ok = False
        self.state = None            # 第一次 GET /draft 的回應
        self.violation = None        # 第一個違規(人話)
        self.write_disabled = False  # 允許 AIB 改頁=關(dry-run 只警告;真寫在 _preflight 就停)
        self.writes = []             # [(method, route, body, status)]
        self.last_write = None
        self._dry_seq = 0

    def __call__(self, method, route, body):
        method = str(method or "").upper()
        route = str(route or "")
        if (method, route) not in ALLOWED_ROUTES:
            self._violate("組版器要打 %s %s,不在只寫草稿的白名單裡" % (method, route))
            return 599, {"error": "aib_oneshot_route_blocked"}
        if method in WRITE_METHODS:
            return self._write(method, route, body)
        try:
            status, payload = self.api.oneshot_call(method, route)
        except TransportError as exc:
            return 599, {"error": "aib_oneshot_transport", "details": [str(exc)[:200]]}
        if route == "/draft" and self.state is None:
            self.state = payload if status == 200 else None
            self._preflight(status, payload)
        return status, payload

    def _violate(self, why):
        if self.violation is None:
            self.violation = why

    def _preflight(self, status, payload):
        if status != 200 or not isinstance(payload, dict):
            self._violate("讀不到草稿狀態(HTTP %s),一筆都沒寫" % status)
            return
        # ★ 刀對準這個判斷:拿掉的話,舊伺服器(PUT 直接改線上頁)那一格必須紅。
        if not isinstance(payload.get("profile"), dict) or "billable_block_count" not in payload:
            self._violate("這台 PAGE 伺服器還不是 v3.109 草稿語意(GET /draft 沒有 profile / billable_block_count;"
                          "舊版的 PUT 會直接改線上頁),一筆都沒寫")
            return
        if payload.get("aib_write_enabled") is False:
            self.write_disabled = True
            if not self.dry_run:
                self._violate("頁面主人把「允許 AIB 改頁」關掉了,一筆都沒寫;到 ez2.page 後台打開再重跑")
                return
        self.preflight_ok = True

    def _write(self, method, route, body):
        if self.violation or not self.preflight_ok:
            self._violate(self.violation or "還沒確認草稿語意就要寫,停手")
            return 599, {"error": "aib_oneshot_write_blocked"}
        if self.dry_run:
            self._dry_seq += 1
            self.writes.append((method, route, body, "dry-run"))
            fake = {"ok": True, "dry_run": True,
                    "draft": {"draft_revision_id": "%s_%d" % (DRY_REVISION, self._dry_seq)},
                    "preview": {"review_url": None}}
            return 200, fake
        try:
            status, payload = self.api.oneshot_call(method, route, body)
        except TransportError as exc:
            # 送出去了但沒拿到回應:可能已寫進草稿,也可能沒有 ⇒ 記成「結果不明」,後面不再寫
            self.writes.append((method, route, body, "unknown"))
            self._violate("%s %s 送出後斷線(%s),這一筆可能已寫進草稿也可能沒有;後面一筆都不寫" % (method, route, str(exc)[:120]))
            return 599, {"error": "aib_oneshot_transport"}
        self.writes.append((method, route, body, status))
        if status == 200:
            self.last_write = payload
            # ★ 刀對準這個判斷:PUT 回應沒有 draft 鍵 = 伺服器沒寫在草稿上 ⇒ 立刻停。
            if method == "PUT" and "draft" not in payload:
                self._violate("PUT %s 的回應沒有 draft 鍵(伺服器沒有把它寫在草稿上),後面一筆都不寫" % route)
                return 599, {"error": "aib_oneshot_not_draft"}
        return status, payload


# ---- 印 -----------------------------------------------------------------------

def _op_line(op):
    if not isinstance(op, dict):
        return str(op)
    kind = op.get("op")
    if kind == "create":
        payload = op.get("payload") or {}
        label = payload.get("label") or payload.get("title") or payload.get("text") or ""
        demo = "(示範)" if payload.get("demo") or op.get("demo") else ""
        return "新增 %s%s %s" % (op.get("block_type"), demo, str(label)[:40])
    if kind == "delete":
        return "刪除 %s" % op.get("block_id")
    if kind in ("move", "reorder"):
        return "%s %s" % (kind, op.get("block_id") or "(整頁順序)")
    return str(kind)


def _write_lines(writes):
    lines = []
    for i, (method, route, body, status) in enumerate(writes, 1):
        body = body or {}
        head = "  %d. %s %s" % (i, method, route)
        if status != "dry-run":
            head += " → %s" % status
        lines.append(head)
        if route == "/appearance":
            keys = [k for k in ("template_id", "theme_id", "shape_id", "avatar_size", "avatar_shape") if body.get(k)]
            lines.append("     外觀:" + "、".join("%s=%s" % (k, body[k]) for k in keys))
        elif route == "/profile":
            keys = [k for k in body if k not in ("operation_key", "expected_draft_revision")]
            lines.append("     個人檔案欄:" + ("、".join(keys) or "(無)"))
        elif route == "/draft":
            for op in (body.get("ops") or [])[:60]:
                lines.append("     - " + _op_line(op))
    return lines


def _appearance_warning(state, writes):
    """外觀會被換掉時先講(已有內容的頁沒帶主題 ⇒ 套回卡片預設,這是最常見的誤改)。"""
    current = (state or {}).get("appearance") or {}
    for method, route, body, _ in writes:
        if route != "/appearance":
            continue
        body = body or {}
        changes = []
        for key in ("theme_id", "shape_id", "avatar_size", "avatar_shape"):
            if body.get(key) and current.get(key) and body[key] != current[key]:
                changes.append("%s %s → %s" % (key, current[key], body[key]))
        if body.get("template_id") and current.get("template_id") and body["template_id"] != current["template_id"]:
            changes.append("套版 %s → %s" % (current["template_id"], body["template_id"]))
        if changes:
            return "注意:這次會改外觀(%s);不想換外觀就在 intent 照抄目前的 theme_id / shape_id" % "、".join(changes)
    return None


def _state_lines(state):
    state = state or {}
    profile = state.get("profile") or {}
    look = state.get("appearance") or {}
    lines = ["目前頁面(讀的,不扣額度):",
             "  顯示名:%s" % (profile.get("display_name") or "(空)"),
             "  外觀:theme_id=%s shape_id=%s avatar_size=%s avatar_shape=%s" % (
                 look.get("theme_id", "?"), look.get("shape_id", "?"),
                 look.get("avatar_size", ""), look.get("avatar_shape", "")),
             "  有草稿:%s" % ("是" if state.get("has_draft") else "否"),
             "  區塊:自己的 %s / 上限 %s / 還能加 %s(示範顆不算,剩 %s 顆示範)" % (
                 state.get("billable_block_count", "?"), state.get("block_limit", "?"),
                 state.get("blocks_remaining", "?"), state.get("demo_remaining", "?"))]
    if state.get("aib_write_enabled") is False:
        lines.append("  允許 AIB 改頁:關(寫不進去)")
    return lines


# ---- 三個動作 -------------------------------------------------------------------

def checklist(api, intent, out, environ, token=None, lang="zh", root=None):
    """S2 開場清單:逐字印模組的 ASK 句,已綁就附目前頁面狀態,有 intent 就附缺哪幾項。零寫入。"""
    say = lambda line: out(redact(line, token))
    view = run_core({"cmd": "checklist", "intent": intent}, environ, token=token, root=root)
    say(view.get("ask_zh") if lang == "zh" else view.get("ask_en"))
    say("")
    if api is not None:
        try:
            state = api.get_draft()
        except ApiError as exc:
            say("(讀不到目前頁面:HTTP %s %s)" % (exc.http_status, exc.code))
        except TransportError as exc:
            say("(讀不到目前頁面:%s)" % exc)
        else:
            for line in _state_lines(state):
                say(line)
    summary = view.get("summary") or {}
    if not view.get("ok", True):
        say("intent 的 opening_checklist 格式不對:%s" % ", ".join(map(str, view.get("errors") or [])))
    if intent is not None:
        say("這份 intent 對清單:已有 %s;待補必填 %s;請幫我找 %s" % (
            "、".join(summary.get("have") or []) or "無",
            "、".join(summary.get("pending_required") or []) or "無",
            "、".join(summary.get("find_for_me") or []) or "無"))
    return view


def compose_only(intent, out, environ, token=None, root=None):
    say = lambda line: out(redact(line, token))
    result = run_core({"cmd": "compose", "intent": intent}, environ, token=token, root=root)
    if not result.get("ok"):
        raise OneshotError("intent 組不成頁(什麼都沒送):%s" % ", ".join(map(str, result.get("errors") or [])))
    bp = result.get("blueprint") or {}
    meta = bp.get("meta") or {}
    say("[dry-run] 還沒綁頁面:只組版、沒讀頁面狀態、沒寫任何東西")
    say("  套版卡:%s%s" % (meta.get("format_id"), "(空 intent ⇒ 示範骨架)" if meta.get("scaffolded") else ""))
    say("  外觀:%s" % json.dumps(bp.get("appearance") or {}, ensure_ascii=False))
    say("  個人檔案欄:%s" % ("、".join((bp.get("profile") or {}).keys()) or "(無)"))
    for op in (bp.get("ops") or []):
        say("  - " + _op_line(op))
    say("  intent 待補必填:%s" % ("、".join(meta.get("checklist_pending_required") or []) or "無"))
    return result


def apply(api, intent, clear, out, environ, key_prefix, dry_run=False, token=None, root=None):
    """跑組版契約。回 (result, broker)。寫入只落草稿;dry_run 零寫入。"""
    say = lambda line: out(redact(line, token))
    broker = Broker(api, dry_run=dry_run)
    result = run_core({"cmd": "apply", "intent": intent,
                       "options": {"clear_existing": clear, "operation_key_prefix": key_prefix}},
                      environ, broker=broker, token=token, root=root)
    if broker.state is not None:
        for line in _state_lines(broker.state):
            say(line)
    meta = result.get("blueprint_meta") or {}
    title = "[dry-run] 組版計畫(沒有寫任何東西):" if dry_run else "已送出的寫入(全部只寫草稿):"
    say(title)
    say("  套版卡:%s%s;清空既有區塊:%s" % (
        meta.get("format_id"), "(空 intent ⇒ 示範骨架)" if meta.get("scaffolded") else "",
        "是" if clear else "否"))
    for line in _write_lines(broker.writes) or ["  (沒有需要寫的東西)"]:
        say(line)
    warn = _appearance_warning(broker.state, broker.writes)
    if warn:
        say(warn)
    blocks = result.get("scaffold_blocks")
    if blocks and blocks.get("skipped_reason") == "page_has_blocks":
        say("頁上已經有區塊,示範骨架一顆都沒加(只套外觀、補空的個人檔案欄)")
    if broker.write_disabled and dry_run:
        say("注意:頁面主人把「允許 AIB 改頁」關掉了;計畫照印,但真的寫會被擋,先到 ez2.page 後台打開")
    pending = meta.get("checklist_pending_required") or []
    say("intent 待補必填(依這份 intent 算,不是頁面現況):%s" % ("、".join(pending) or "無"))
    fill = {} if dry_run else (result.get("fill_gap") or {})   # dry-run 讀到的是還沒寫的頁,補洞建議會講錯
    for ask in (fill.get("asks") or [])[:3]:
        say("  建議追問(先對照頁面實際內容再問):%s" % ask)
    if broker.violation:
        raise OneshotError(redact(broker.violation + _done_note(broker), token))
    if not result.get("ok"):
        raise OneshotError(redact("組版器停在:%s%s%s" % (
            ", ".join(map(str, result.get("errors") or [])),
            (";伺服器說:%s" % json.dumps(result.get("detail"), ensure_ascii=False)[:300]) if result.get("detail") else "",
            _done_note(broker)), token))
    return result, broker


def _done_note(broker):
    done = [w for w in broker.writes if w[3] == 200]
    unknown = [w for w in broker.writes if w[3] == "unknown"]
    if broker.dry_run or not (done or unknown):
        return "(沒有寫入任何東西)"
    note = "(已寫進草稿的 %d 筆不會退回:%s" % (len(done), "、".join("%s %s" % (m, r) for m, r, _, _ in done) or "無")
    if unknown:
        note += ";結果不明 %d 筆:%s" % (len(unknown), "、".join("%s %s" % (m, r) for m, r, _, _ in unknown))
    return note + ";線上頁沒變,先 `aib page chat` 看草稿再決定)"
