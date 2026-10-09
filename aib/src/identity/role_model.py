# SPDX-License-Identifier: Apache-2.0
"""Ez2 AIB 角色模型(E0.5 draft)。

大綱正文:uid=身份不變,role=動態指派(root/pm/dev/seo/design/view/monitor…
可隨時切換,切換=帳本事件);role 可綁 Ez2 C/P/F/G 的 P專家人格卡(綁定是
另一層映射,解綁不掉身份)。已綁 session 的身份重綁不是普通 role 切換:
AI 只能提 request,每次必須取得真人對精確 session/新舊身份/nonce/TTL 的
一次性 approval receipt;新 authority 通過且 trust=registry_verified 後,
AIB 才原子 supersede 舊綁定;失敗保留舊綁定,舊紀錄不刪除,重綁成功本身
也不得自動升 trust。

三層分離(本模組的骨架,層與層互不穿透):
  身份層  agents/    agent_uid 不變;檔案一經建立,agent_uid 欄位永不改寫。
  角色層  (身份檔內) role 動態可切,每次切換先過帳本(identity.bound)再落檔。
  綁定層  人格卡映射=身份檔內 persona_card 欄(+persona_sha 版本指紋、
          brain_route_key 第二大腦落點,L2-02;角色層附屬:解綁只清這些欄,
          身份與角色原樣);session↔身份綁定=sessions/ 檔,重綁動的是這一
          檔,身份層一個 byte 都不動。

真相邊界:
  - 事件種類是封閉 15 種(event.schema.json),新增種類必須走 ADR——本模組
    刻意不新增:身份/角色/綁定層的變異一律以 identity.bound 承載(subject
    指向身份,summary 說明變異),重綁申請走 decision.requested/resolved。
  - 帳本是脊椎,狀態檔是投影:每個變異**先入帳**(schema fail-closed,
    LedgerError 即中止、狀態檔不動),入帳成功才原子改寫狀態檔(tmp+rename)。
  - 狀態檔落在狀態根(EZ2_AIB_HOME,預設 ~/.ez2-aib)的 identity/ 子樹,
    不在 repo 內,故事件不掛 data_ref(schema 語意=repo 內相對路徑)。
  - 鏈上任何 symlink 即 fail-closed(對齊 ED.2)。
"""
import argparse
import hmac
import json
import os
import re
import secrets
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "ledger"))

from ledger import Ledger, LedgerError, sha256_hex  # noqa: E402

DEFAULT_STATE_ROOT = os.environ.get(
    "EZ2_AIB_HOME", os.path.join(os.path.expanduser("~"), ".ez2-aib"))
DEFAULT_LEDGER_REL = os.path.join("ledger", "aib-ledger.jsonl")
DEFAULT_REBIND_TTL_MS = 15 * 60 * 1000  # 真人核准窗;過期只能重新申請
MAX_REBIND_TTL_MS = 24 * 60 * 60 * 1000  # 審高5:申請者不得自定永久核准窗

# root/pm/dev/seo/design/view/monitor…開放集合,只約束字元形狀
# (對齊 provider-launch.sh 的 ROLE 驗證與 actor.sig 的 name 段)。
# ★一律 fullmatch:$ 會容忍尾端換行,帶換行的 uid 是「看似相同的另一個身份」(審低1)。
ROLE_RE = re.compile(r"[a-z0-9-]{1,32}")
UID_RE = re.compile(r"[A-Za-z0-9:._@-]{4,256}")
SESSION_RE = re.compile(r"[A-Za-z0-9:._@-]{1,128}")
CARD_RE = re.compile(r"\S{1,256}")
# L2-02:卡片版本指紋(cards.json v2 persona_sha=角色.md 全檔 sha256)與席位自選大腦
# (路由契約 routeKey 形狀;是否可寫/敏感由執行器 bind_one 對契約查,本層只約束形狀)。
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ROUTE_KEY_RE = re.compile(r"[a-z][a-z0-9._-]{0,127}")
REQUEST_ID_RE = re.compile(r"rbr_[0-9a-f]{32}")

TRUST_LEVELS = ("self_reported", "registry_verified")


class RoleModelError(Exception):
    """fail-closed:raise 即保證本次呼叫未改寫任何狀態檔。"""


def _safe_name(raw):
    """uid/session id 可含 ':' 等字元,檔名=可讀前綴+內容雜湊(防撞防注入)。"""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:64]
    return "%s-%s.json" % (stem, sha256_hex(raw)[:12])


class RoleModel:
    def __init__(self, state_root=None, ledger_path=None, now_ms=None):
        self._root = Path(state_root or DEFAULT_STATE_ROOT)
        self._ledger_path = Path(
            ledger_path or (self._root / DEFAULT_LEDGER_REL))
        if not self._ledger_path.parent.is_dir():
            raise RoleModelError(
                "ledger directory missing: %s(先跑 aib init 建狀態樹)"
                % self._ledger_path.parent)
        if now_ms is None:
            import time
            now_ms = lambda: int(time.time() * 1000)  # noqa: E731
        self._now_ms = now_ms
        if os.path.islink(str(self._root)):
            raise RoleModelError(
                "state root is a symlink (fail-closed): %s" % self._root)
        self._agents_dir = self._root / "identity" / "agents"
        self._sessions_dir = self._root / "identity" / "sessions"
        self._requests_dir = self._root / "identity" / "rebind-requests"
        for d in (self._agents_dir, self._sessions_dir, self._requests_dir):
            self._guard_chain(d)
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self._guard_chain(self._ledger_path)
        except ValueError:
            pass  # 明示指定的帳本可在狀態根之外,鏈檢查只管根下路徑
        self._ledger = None

    def _guard_chain(self, path):
        """審高6:symlink fail-closed 要查整條鏈,不是只查末端。

        state_root 以下(含目標)任何一級是 symlink 即拒;根之上的層級
        (如 macOS 的 /var→/private/var)不在本模組管轄。路徑不在根下=
        ValueError 由呼叫端決定(建構子對外部帳本容忍,狀態檔一律必在根下)。"""
        rel = Path(path).relative_to(self._root)  # 不在根下 → ValueError
        cur = self._root
        for part in rel.parts:
            cur = cur / part
            if os.path.islink(str(cur)):
                raise RoleModelError(
                    "symlink in state path (fail-closed): %s" % cur)

    # -- 內部件 -----------------------------------------------------------

    def _append_event(self, kind, summary_zh, summary_en, subject_id, actor,
                      causation_id=None, correlation_id=None):
        """先入帳。任何失敗以 RoleModelError 爆出,呼叫端保證尚未動狀態檔。"""
        draft = {
            "kind": kind,
            "actor": actor,
            "body": {
                "summary": {"zh-TW": summary_zh, "en": summary_en},
                "subject": {"kind": "identity", "id": subject_id},
            },
        }
        if kind in ("decision.requested", "decision.resolved"):
            draft["body"]["subject"]["kind"] = "decision"
        causation = {"causation_id": causation_id}
        if correlation_id:
            causation["correlation_id"] = correlation_id
        draft["causation"] = causation
        try:
            if self._ledger is None:
                self._ledger = Ledger(str(self._ledger_path))
            return self._ledger.append(draft)
        except LedgerError as err:
            raise RoleModelError("ledger rejected event (state untouched): %s" % err)

    def _load(self, path, kind):
        self._guard_chain(path)
        if not path.is_file():
            raise RoleModelError("%s not found: %s" % (kind, path))
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as err:
            raise RoleModelError("%s unreadable: %s" % (kind, err))

    def _write_atomic(self, path, record):
        """tmp+rename 原子落檔;rename 前任何失敗=舊內容完好。"""
        self._guard_chain(path)
        blob = json.dumps(record, ensure_ascii=False, sort_keys=True,
                          indent=1) + "\n"
        tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(path))
        finally:
            if tmp.exists():
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass

    def _agent_path(self, agent_uid):
        return self._agents_dir / _safe_name(agent_uid)

    def _session_path(self, session_id):
        return self._sessions_dir / _safe_name(session_id)

    # -- 身份層+角色層 ----------------------------------------------------

    def register(self, agent_uid, role, actor):
        """建身份。uid 撞既有=拒收且不碰既有檔(uid 不變的第一道閘)。"""
        if not UID_RE.fullmatch(agent_uid or ""):
            raise RoleModelError("invalid agent_uid: %r" % (agent_uid,))
        if not ROLE_RE.fullmatch(role or ""):
            raise RoleModelError("invalid role: %r" % (role,))
        path = self._agent_path(agent_uid)
        self._guard_chain(path)
        if path.exists():
            raise RoleModelError(
                "agent_uid already registered (uid is immutable): %s" % agent_uid)
        event = self._append_event(
            "identity.bound",
            "身份註冊:%s 初始角色=%s" % (agent_uid, role),
            "identity registered: %s initial role=%s" % (agent_uid, role),
            agent_uid, actor)
        record = {
            "record": "role-model-agent-v1",
            "agent_uid": agent_uid,
            "role": role,
            "persona_card": None,
            "created_wall_ms": self._now_ms(),
            "last_event_id": event["event_id"],
        }
        self._write_atomic(path, record)
        return record

    def get_agent(self, agent_uid):
        return self._load(self._agent_path(agent_uid), "agent")

    def switch_role(self, agent_uid, new_role, actor):
        """角色切換=帳本事件。動 role 一欄;agent_uid 逐字保留(斷言看守)。"""
        if not ROLE_RE.fullmatch(new_role or ""):
            raise RoleModelError("invalid role: %r" % (new_role,))
        path = self._agent_path(agent_uid)
        record = self._load(path, "agent")
        if record.get("agent_uid") != agent_uid:
            raise RoleModelError(
                "state corrupt: stored agent_uid mismatch (refusing to write)")
        old_role = record.get("role")
        event = self._append_event(
            "identity.bound",
            "角色切換:%s %s→%s" % (agent_uid, old_role, new_role),
            "role switch: %s %s->%s" % (agent_uid, old_role, new_role),
            agent_uid, actor)
        record["role"] = new_role
        record["last_event_id"] = event["event_id"]
        self._write_atomic(path, record)
        return record

    # -- 綁定層:人格卡(另一層映射;解綁不掉身份) -----------------------

    def bind_persona(self, agent_uid, card_ref, actor, persona_sha=None):
        """role 綁 Ez2 C/P/F/G 的 P專家人格卡。只動 persona_card(+persona_sha)欄。

        persona_sha(L2-02,可選):綁定當下那張卡的版本指紋(cards.json v2 的
        `persona_sha`)。投影拿它與登記表現值比,不同=「卡已更新」漂移提醒。
        不給=不釘(舊路徑),投影不會誤報漂移(不知道≠漂移)。
        """
        if not CARD_RE.fullmatch(card_ref or ""):
            raise RoleModelError("invalid persona card ref: %r" % (card_ref,))
        if persona_sha is not None and not SHA256_RE.fullmatch(persona_sha):
            raise RoleModelError("invalid persona_sha (need sha256 hex): %r" % (persona_sha,))
        path = self._agent_path(agent_uid)
        record = self._load(path, "agent")
        event = self._append_event(
            "identity.bound",
            "人格卡綁定:%s 卡=%s%s(映射層,不動身份)"
            % (agent_uid, card_ref, " sha=%s" % persona_sha[:12] if persona_sha else ""),
            "persona card bound: %s card=%s%s (mapping layer)"
            % (agent_uid, card_ref, " sha=%s" % persona_sha[:12] if persona_sha else ""),
            agent_uid, actor)
        record["persona_card"] = card_ref
        record["persona_sha"] = persona_sha
        record["last_event_id"] = event["event_id"]
        self._write_atomic(path, record)
        return record

    def unbind_persona(self, agent_uid, actor):
        """解綁人格卡。身份與角色一欄不動——解綁不掉身份。"""
        path = self._agent_path(agent_uid)
        record = self._load(path, "agent")
        if record.get("persona_card") is None:
            raise RoleModelError("no persona card bound: %s" % agent_uid)
        event = self._append_event(
            "identity.bound",
            "人格卡解綁:%s(身份與角色保留)" % agent_uid,
            "persona card unbound: %s (identity and role retained)" % agent_uid,
            agent_uid, actor)
        record["persona_card"] = None
        record["persona_sha"] = None
        record["brain_route_key"] = None   # 大腦是人格的落點,人格解了落點一併清
        record["last_event_id"] = event["event_id"]
        self._write_atomic(path, record)
        return record

    # -- 綁定層:第二大腦(L2-02;席位自選的記憶落點,投影優先於卡預設) ------

    def bind_brain(self, agent_uid, route_key, actor):
        """綁第二大腦=路由契約 routeKey。只動 brain_route_key 欄;要先綁人格
        (大腦是人格的落點,沒有人格的大腦沒有意義)。
        ★ 本層只驗形狀;「可寫且非敏感」由執行器對契約查(bind_one),投影再查一次。"""
        if not ROUTE_KEY_RE.fullmatch(route_key or ""):
            raise RoleModelError("invalid brain route_key: %r" % (route_key,))
        path = self._agent_path(agent_uid)
        record = self._load(path, "agent")
        if record.get("persona_card") is None:
            raise RoleModelError("bind persona before brain: %s has no persona card" % agent_uid)
        event = self._append_event(
            "identity.bound",
            "第二大腦綁定:%s route=%s(映射層,不動身份)" % (agent_uid, route_key),
            "second brain bound: %s route=%s (mapping layer)" % (agent_uid, route_key),
            agent_uid, actor)
        record["brain_route_key"] = route_key
        record["last_event_id"] = event["event_id"]
        self._write_atomic(path, record)
        return record

    def unbind_brain(self, agent_uid, actor):
        path = self._agent_path(agent_uid)
        record = self._load(path, "agent")
        if record.get("brain_route_key") is None:
            raise RoleModelError("no brain bound: %s" % agent_uid)
        event = self._append_event(
            "identity.bound",
            "第二大腦解綁:%s(人格與身份保留)" % agent_uid,
            "second brain unbound: %s (persona and identity retained)" % agent_uid,
            agent_uid, actor)
        record["brain_route_key"] = None
        record["last_event_id"] = event["event_id"]
        self._write_atomic(path, record)
        return record

    # -- 綁定層:session 綁定與重綁 ----------------------------------------

    def bind_session(self, session_id, agent_uid, trust, actor):
        """session↔身份初次綁定。身份必須已註冊;trust 只收封閉字彙。"""
        if not SESSION_RE.fullmatch(session_id or ""):
            raise RoleModelError("invalid session_id: %r" % (session_id,))
        if trust not in TRUST_LEVELS:
            raise RoleModelError("invalid trust: %r" % (trust,))
        self.get_agent(agent_uid)  # 綁定標的必須存在
        path = self._session_path(session_id)
        if path.exists():
            raise RoleModelError(
                "session already bound; rebind must go through "
                "request_rebind/approve_rebind: %s" % session_id)
        event = self._append_event(
            "identity.bound",
            "session 綁定:%s→%s trust=%s" % (session_id, agent_uid, trust),
            "session bound: %s->%s trust=%s" % (session_id, agent_uid, trust),
            agent_uid, actor)
        record = {
            "record": "role-model-session-v1",
            "session_id": session_id,
            "active": {
                "agent_uid": agent_uid,
                "trust": trust,
                "bound_wall_ms": self._now_ms(),
                "via": "bind_session",
                "event_id": event["event_id"],
            },
            "history": [],
        }
        self._write_atomic(path, record)
        return record

    def get_session(self, session_id):
        return self._load(self._session_path(session_id), "session")

    def request_rebind(self, session_id, old_agent_uid, new_agent_uid, actor,
                       ttl_ms=DEFAULT_REBIND_TTL_MS):
        """身份重綁申請。AI 走到這裡為止:不改任何綁定,只發 request。

        nonce 由本模組產生(申請者不得自帶——自帶=可預先偽造 receipt),
        真人 approval receipt 必須逐欄複誦 request_id/session/舊/新/nonce 才算數。
        TTL 有上限(審高5):申請者不得自定永久核准窗。
        """
        session = self.get_session(session_id)
        if session["active"]["agent_uid"] != old_agent_uid:
            raise RoleModelError(
                "old_agent_uid mismatch: active binding is %s"
                % session["active"]["agent_uid"])
        if old_agent_uid == new_agent_uid:
            # 三輪審中1:同身份「重綁自己」唯一效果是把 active trust 抬升,
            # 正撞「重綁成功本身不得自動升 trust」;再驗證走 EH 顯式流程。
            raise RoleModelError(
                "rebind to the same agent_uid is not a rebind "
                "(trust escalation path; use explicit re-attestation)")
        self.get_agent(new_agent_uid)  # 新 authority 必須是已註冊身份
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms <= 0:
            raise RoleModelError("ttl_ms must be a positive integer (not bool)")
        if ttl_ms > MAX_REBIND_TTL_MS:
            raise RoleModelError(
                "ttl_ms %d exceeds cap %d (no self-granted permanent windows)"
                % (ttl_ms, MAX_REBIND_TTL_MS))
        nonce = secrets.token_hex(16)
        request_id = "rbr_" + sha256_hex(
            "%s|%s|%s|%s" % (session_id, old_agent_uid, new_agent_uid, nonce))[:32]
        event = self._append_event(
            "decision.requested",
            "身份重綁申請:%s %s→%s(候真人一次性核准,TTL=%dms)"
            % (session_id, old_agent_uid, new_agent_uid, ttl_ms),
            "identity rebind requested: %s %s->%s (one-time human approval, "
            "TTL=%dms)" % (session_id, old_agent_uid, new_agent_uid, ttl_ms),
            request_id, actor, correlation_id=request_id)
        record = {
            "record": "role-model-rebind-request-v1",
            "request_id": request_id,
            "session_id": session_id,
            "old_agent_uid": old_agent_uid,
            "new_agent_uid": new_agent_uid,
            "nonce": nonce,
            "ttl_ms": ttl_ms,
            "requested_wall_ms": self._now_ms(),
            "state": "pending",
            "request_event_id": event["event_id"],
        }
        self._write_atomic(self._requests_dir / (request_id + ".json"), record)
        return record

    def approve_rebind(self, request_id, receipt, actor):
        """真人核准後的原子 supersede。全部檢查先於任何寫入(失敗=舊綁定完好):

          1. actor.kind 必須是 human(重綁核准是人類裁決入帳,原理 13)
          2. request_id 先驗格式再拼路徑(審高2:否則 ../ 或絕對路徑可逃出
             rebind-requests/ 讀寫外部檔)
          3. request 存在、pending、未過 TTL——過期是**讀時判定**不落檔
             (審中1:過期不是本模組的變異,寫檔會違反「raise 即狀態不變」;
             requested_wall_ms+ttl_ms 恆定,wall clock 前行下過期即失效;
             時鐘回撥可短暫復活 pending,屬具名殘餘風險 中3,本模組不防)
          4. receipt 逐欄精確複誦 request_id/session/舊身份/新身份+nonce
             常數時間比對(request_id 複誦=審高5,receipt 綁死精確申請)
          5. receipt.trust 必須=registry_verified(新 authority 未過驗=不 supersede)
          6. 成功即消耗 request(一次性;第二次核准同一 request 必拒)

        supersede 本身:舊綁定整筆進 history(不刪除),active 換新;
        新綁定 trust=receipt 所載(重綁不外溢、不升任何其他紀錄的 trust);
        agent_uid 身份層一個 byte 不動。
        """
        if not isinstance(actor, dict) or actor.get("kind") != "human":
            raise RoleModelError(
                "approve_rebind requires a human actor (AI can only request)")
        if not REQUEST_ID_RE.fullmatch(str(request_id)):
            raise RoleModelError(
                "invalid request_id shape (refusing path join): %r" % (request_id,))
        req_path = self._requests_dir / (str(request_id) + ".json")
        request = self._load(req_path, "rebind request")
        if request.get("state") != "pending":
            raise RoleModelError(
                "request %s is %s (approval receipts are one-time)"
                % (request_id, request.get("state")))
        if request.get("old_agent_uid") == request.get("new_agent_uid"):
            # 防手寫 request 檔繞過 request_rebind 的同 uid 閘(三輪審中1)
            raise RoleModelError(
                "self-rebind request rejected (trust escalation path)")
        now = self._now_ms()
        # 有效窗=[requested, requested+ttl):恰好到期毫秒即失效(三輪審低2)
        if now >= request["requested_wall_ms"] + request["ttl_ms"]:
            raise RoleModelError(
                "request %s expired (TTL %dms); old binding retained"
                % (request_id, request["ttl_ms"]))
        if not isinstance(receipt, dict):
            raise RoleModelError("receipt must be a dict")
        for field in ("request_id", "session_id", "old_agent_uid",
                      "new_agent_uid"):
            if receipt.get(field) != request[field]:
                raise RoleModelError(
                    "receipt %s mismatch; old binding retained" % field)
        if not hmac.compare_digest(
                str(receipt.get("nonce", "")), request["nonce"]):
            raise RoleModelError("receipt nonce mismatch; old binding retained")
        if not receipt.get("approved_by"):
            raise RoleModelError("receipt missing approved_by (human)")
        if receipt.get("trust") != "registry_verified":
            raise RoleModelError(
                "new authority trust must be registry_verified; got %r "
                "(old binding retained)" % (receipt.get("trust"),))
        session_path = self._session_path(request["session_id"])
        session = self._load(session_path, "session")
        if session["active"]["agent_uid"] != request["old_agent_uid"]:
            raise RoleModelError(
                "active binding changed since request; old binding retained")
        self.get_agent(request["new_agent_uid"])  # 新身份仍須在冊

        resolved = self._append_event(
            "decision.resolved",
            "身份重綁核准:%s by %s" % (request_id, receipt["approved_by"]),
            "identity rebind approved: %s by %s"
            % (request_id, receipt["approved_by"]),
            request_id, actor,
            causation_id=request["request_event_id"],
            correlation_id=request_id)
        bound = self._append_event(
            "identity.bound",
            "session 重綁:%s %s→%s(原子 supersede,舊紀錄保留)"
            % (request["session_id"], request["old_agent_uid"],
               request["new_agent_uid"]),
            "session rebound: %s %s->%s (atomic supersede, old record retained)"
            % (request["session_id"], request["old_agent_uid"],
               request["new_agent_uid"]),
            request["new_agent_uid"], actor,
            causation_id=resolved["event_id"],
            correlation_id=request_id)

        old_active = dict(session["active"])
        old_active["state"] = "superseded"
        old_active["superseded_wall_ms"] = now
        old_active["superseded_by_request"] = request_id
        session["history"].append(old_active)
        session["active"] = {
            "agent_uid": request["new_agent_uid"],
            "trust": receipt["trust"],
            "bound_wall_ms": now,
            "via": "approve_rebind",
            "request_id": request_id,
            "event_id": bound["event_id"],
        }
        self._write_atomic(session_path, session)

        request["state"] = "approved"
        request["approved_wall_ms"] = now
        request["approved_by"] = receipt["approved_by"]
        self._write_atomic(req_path, request)
        return session


# -- CLI -------------------------------------------------------------------

def _add_actor_args(parser):
    parser.add_argument("--sig", required=True, help="顯示簽名,如 pm@00000/Claude")
    parser.add_argument("--ai-id", required=True, help="完整身份 engine:host:uuid")
    parser.add_argument("--actor-kind", default="main",
                        choices=["human", "main", "sub_agent", "system"])
    parser.add_argument("--engine")
    parser.add_argument("--model")
    parser.add_argument("--lineage")


def _actor_of(args):
    actor = {"ai_id": args.ai_id, "sig": args.sig, "kind": args.actor_kind}
    for key in ("engine", "model", "lineage"):
        value = getattr(args, key, None)
        if value:
            actor[key] = value
    return actor


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="E0.5 角色模型:uid 不變/role 動態/綁定另層/重綁走真人 receipt")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--ledger", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register"); p.add_argument("agent_uid"); p.add_argument("role"); _add_actor_args(p)
    p = sub.add_parser("show"); p.add_argument("agent_uid")
    p = sub.add_parser("switch-role"); p.add_argument("agent_uid"); p.add_argument("new_role"); _add_actor_args(p)
    p = sub.add_parser("bind-persona"); p.add_argument("agent_uid"); p.add_argument("card_ref"); p.add_argument("--sha", default=None, help="卡片版本指紋 sha256(cards.json v2 persona_sha)"); _add_actor_args(p)
    p = sub.add_parser("unbind-persona"); p.add_argument("agent_uid"); _add_actor_args(p)
    p = sub.add_parser("bind-brain"); p.add_argument("agent_uid"); p.add_argument("route_key"); _add_actor_args(p)
    p = sub.add_parser("unbind-brain"); p.add_argument("agent_uid"); _add_actor_args(p)
    p = sub.add_parser("bind-session"); p.add_argument("session_id"); p.add_argument("agent_uid"); p.add_argument("trust", choices=list(TRUST_LEVELS)); _add_actor_args(p)
    p = sub.add_parser("show-session"); p.add_argument("session_id")
    p = sub.add_parser("request-rebind"); p.add_argument("session_id"); p.add_argument("old_agent_uid"); p.add_argument("new_agent_uid"); p.add_argument("--ttl-ms", type=int, default=DEFAULT_REBIND_TTL_MS); _add_actor_args(p)
    p = sub.add_parser("approve-rebind"); p.add_argument("request_id"); p.add_argument("--receipt-json", required=True, help="真人 receipt 的 JSON 字串"); _add_actor_args(p)

    args = parser.parse_args(argv)
    try:
        model = RoleModel(state_root=args.state_root, ledger_path=args.ledger)
        if args.cmd == "register":
            out = model.register(args.agent_uid, args.role, _actor_of(args))
        elif args.cmd == "show":
            out = model.get_agent(args.agent_uid)
        elif args.cmd == "switch-role":
            out = model.switch_role(args.agent_uid, args.new_role, _actor_of(args))
        elif args.cmd == "bind-persona":
            out = model.bind_persona(args.agent_uid, args.card_ref, _actor_of(args),
                                     persona_sha=args.sha)
        elif args.cmd == "unbind-persona":
            out = model.unbind_persona(args.agent_uid, _actor_of(args))
        elif args.cmd == "bind-brain":
            out = model.bind_brain(args.agent_uid, args.route_key, _actor_of(args))
        elif args.cmd == "unbind-brain":
            out = model.unbind_brain(args.agent_uid, _actor_of(args))
        elif args.cmd == "bind-session":
            out = model.bind_session(args.session_id, args.agent_uid, args.trust,
                                     _actor_of(args))
        elif args.cmd == "show-session":
            out = model.get_session(args.session_id)
        elif args.cmd == "request-rebind":
            out = model.request_rebind(args.session_id, args.old_agent_uid,
                                       args.new_agent_uid, _actor_of(args),
                                       ttl_ms=args.ttl_ms)
        else:
            try:
                receipt = json.loads(args.receipt_json)
            except ValueError as err:
                raise RoleModelError("receipt JSON unparseable: %s" % err)
            out = model.approve_rebind(args.request_id, receipt, _actor_of(args))
    except RoleModelError as err:
        print("role-model: %s" % err, file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
