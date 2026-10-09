#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""單席綁定執行器(L2-02 / 總綱 §二-D「單席綁定執行器」)。

    python3 src/identity/bind_one.py bind   --seat <seat_id> --card <uid|card_ref> [--brain <route_key>]
                                            --actor role@hex/Engine [--rebind-persona] [--dry-run]
    python3 src/identity/bind_one.py unbind --seat <seat_id> [--brain-only] --actor ... [--dry-run]
    python3 src/identity/bind_one.py status --seat <seat_id>
    共用旗標:--state-root DIR  --cards F  --contract F|git:...  [--no-reproject]

## 這支從哪裡來

`tools/bind-seats.py::bind_one`(08-15)把「綁一席」寫成三步(register → bind-persona →
bind-session)加冪等規則,但它是批次工具:一個 --card 掃全部席位、走 subprocess 叫
role_model CLI、綁完不重投影。D-3 的滑鼠選單、D-4 的重開續綁都需要「綁**一**席、給
uid、釘 sha、選大腦、綁完畫面自己更新」——那是同一套規則的單席形,所以抽到這裡;
bind-seats.py 的三步改呼叫本模組的 `bind_agent()`(規則只寫一份)。

## 規則(每條有測試釘;繼承 bind-seats 的血淚)

- **agent_uid = session_uuid 末五碼**(the maintainer 08-15 定)。席位檔沒有 session_uuid / 形狀不合
  → 不猜、不綁、rc 2 指名。
- **冪等=再跑不會壞也不會做錯事**:register 撞名要核 role;bind-session 撞名要核 agent_uid
  (不同=改綁,要走 request_rebind 真人核准,本工具不代勞);已綁別張卡預設**拒**,
  換卡要 `--rebind-persona` 明說。值沒變就不寫(帳本 append-only,每筆永久)。
- **人格身份=卡 uid**:`--card` 收 uid 或舊 card_ref(橋接查表),寫進身份層的一律是 uid;
  同時釘 `persona_sha`(cards.json v2 現值)。同卡再綁=**重釘到現在的版本**(這就是
  「卡已更新」提醒之後的確認動作),會入帳。
- **大腦=路由契約 routeKey**,必過 `check_against_contract`(aiWritable 且非敏感/機密),
  不過 rc 2;不給=用卡預設(投影決定),投影會再查一次契約。
- **綁完自動重跑投影**(bindings.json + seat-display.json,同一圈),右欄 5 秒輪詢
  自然更新;`--no-reproject` 給批次工具用。
- **席位延續檔** `<state>/identity/seat-continuity/<seat_id>.json`:記這席最後一次成功
  綁定(uid/sha/brain/agent_uid/session_uuid)。seat_id 同窗重開不變、session_uuid 會變,
  D-4 靠它把新 agent 接回既有人格。它是**執行器的產物**,不是第三個真相源:身份仍在
  agents/,它只回答「這席上一次綁的是什麼」。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("identity", "memory", "persona_route", "ledger"):
    sys.path.insert(0, str(_SRC / _p))

from role_model import (  # noqa: E402
    CARD_RE, REQUEST_ID_RE, ROLE_RE, ROUTE_KEY_RE, SESSION_RE,
    SHA256_RE as MODEL_SHA256_RE, TRUST_LEVELS, UID_RE, RoleModel,
    RoleModelError, _safe_name,
)
from binding_projection import load_card_registry, ProjectionError  # noqa: E402
from card_registry import resolve_card, UID_RE as CARD_UID_RE  # noqa: E402
from route_register import check_against_contract, default_contract_spec, load_contract  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACTOR_RE = re.compile(r"[a-z0-9-]{1,32}@[0-9a-f]{4,8}/[A-Za-z0-9._-]{1,32}")
SEAT_ID_RE = re.compile(r"[0-9a-f]{4,8}")
PROJECTION = _SRC / "memory" / "binding_projection.py"


class BindError(Exception):
    """來源不合/拒綁 —— 呼叫端回 rc 2,一個位元組都不寫。"""


def agent_uid_of(session_uuid):
    """末五碼(整串最後五個字元;uuid 最後一段 12 hex 所以等同去連字號後末五)。"""
    return session_uuid[-5:]


def state_root_of(explicit=None):
    """顯式 > 單源 seats_dir_lib.state_root(`EZ2_AIB_STATE_ROOT` > `EZ2_AIB_HOME` > `~/.ez2-aib`)。

    ★ 09-30:本檔 09-29 起自己跨艙找席位檔(`seat_file_paths`:seats/ 與 instances/*/seats/),
      就是 test-seats-dir-resolution 說的席位目錄解析器;跨艙掃描的根走單源(seat_who 同一條),
      不再自己只認 EZ2_AIB_HOME。與 cabin_resolve.sh / aib / binding_projection(本檔重投影用的那支)同一個順位;
      role_model 的**預設**仍只認 EZ2_AIB_HOME,但本檔一律把這裡算出的根明傳給它,不走它的預設。
      兩個變數都沒設(平常的席位)⇒ 與舊規則逐字相同。"""
    if explicit:
        return Path(explicit)
    cockpit = str(_SRC / "cockpit")
    if cockpit not in sys.path:
        sys.path.append(cockpit)
    import seats_dir_lib
    return Path(os.path.expanduser(seats_dir_lib.state_root()[0]))


def seat_file_paths(state_root):
    """席位檔只住兩個地方:`<狀態根>/seats/w*.json` 與 `<狀態根>/instances/<艙>/seats/w*.json`。"""
    root = str(state_root)
    return (glob.glob(os.path.join(root, "seats", "w*.json"))
            + glob.glob(os.path.join(root, "instances", "*", "seats", "w*.json")))


def find_seat(state_root, seat_id):
    """seat_id → (席位檔路徑, 內容)。seats/ 與 instances/*/seats/ 都找;
    兩個檔同 seat_id = 狀態不一致,拒(不挑一個)。"""
    if not SEAT_ID_RE.fullmatch(seat_id or ""):
        raise BindError("seat_id 形狀不合:%r" % (seat_id,))
    hits = []
    # ★ 09-29 a seat:只找說明寫的那兩個地方。舊寫法 `**/w*.json` 把整個狀態根翻一遍
    #   (真機:用量快取、橋接快取…共開約 490 個檔、約 11 秒;席位檔其實只有 184 個),
    #   建一個人格要等十幾秒、建一個專案要等兩分多鐘(它會叫好幾次)。
    #   順帶:別的資料夾裡剛好叫 w*.json 又帶同一個 seat_id 的檔,以前會被算成「兩個席位檔」而拒綁。
    for p in sorted(set(seat_file_paths(state_root))):
        try:
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("seat_id") == seat_id:
            hits.append((p, rec))
    if not hits:
        raise BindError("找不到 seat_id=%s 的席位檔(狀態根 %s)" % (seat_id, state_root))
    if len(hits) > 1:
        raise BindError("seat_id=%s 有 %d 個席位檔,狀態不一致,不挑:%s"
                        % (seat_id, len(hits), ", ".join(p for p, _ in hits)))
    return hits[0]


def seat_binding_inputs(rec):
    """席位檔 → (role, session_uuid, agent_uid);缺什麼就說什麼(bind-seats 同款誠實不寫)。"""
    uuid = rec.get("session_uuid")
    if not uuid:
        raise BindError("席位檔沒有 session_uuid 欄位(seat-detect 尚未回填或候選不唯一)")
    if not UUID_RE.match(str(uuid)):
        raise BindError("session_uuid 形狀不合:%r" % (uuid,))
    role = rec.get("role")
    if not role:
        raise BindError("席位檔沒有 role")
    return role, uuid, agent_uid_of(uuid)


def actor_of(sig, kind="main"):
    if not ACTOR_RE.fullmatch(sig or ""):
        raise BindError("--actor 格式不合(需 role@四到八碼十六進位/引擎,例 root@00000/Claude):%r"
                        % (sig,))
    return {"ai_id": sig, "sig": sig, "kind": kind}


# ---- 核心:綁一個 agent(bind-seats 三步的規則正本) --------------------------------

class RoleModelReadView:
    """只讀身份視圖；constructor 與查詢都不得建立任何目錄或檔案。"""

    def __init__(self, state_root):
        self._root = Path(state_root)
        if os.path.islink(str(self._root)):
            raise RoleModelError(
                "state root is a symlink (fail-closed): %s" % self._root)

    def _read(self, path, kind):
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            raise RoleModelError("%s path escapes state root: %s" % (kind, path))
        cur = self._root
        for part in rel.parts:
            cur = cur / part
            if os.path.islink(str(cur)):
                raise RoleModelError("symlink in state path (fail-closed): %s" % cur)
        if path.exists() and not path.is_file():
            raise RoleModelError("%s unreadable: path is not a regular file" % kind)
        if not path.exists():
            raise RoleModelError("%s not found: %s" % (kind, path))
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError) as err:
            raise RoleModelError("%s unreadable: %s" % (kind, err))
        if not isinstance(record, dict):
            raise RoleModelError("%s unreadable: top level is not an object" % kind)
        return record

    def get_agent(self, agent_uid):
        return self._read(
            self._root / "identity" / "agents" / _safe_name(agent_uid), "agent")

    def get_session(self, session_uuid):
        return self._read(
            self._root / "identity" / "sessions" / _safe_name(session_uuid), "session")

def _optional_record(model, getter_name, kind, key):
    """唯讀取既有記錄；只有精確的 not-found 才等於不存在，壞檔要擋。"""
    try:
        return getattr(model, getter_name)(key), None
    except RoleModelError as err:
        detail = str(err)
        if detail.startswith("%s not found:" % kind):
            return None, None
        return None, "%s 狀態不可讀:%s" % (kind, detail[:200])


def _plain_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _agent_shape_error(agent, agent_uid):
    if agent.get("record") != "role-model-agent-v1":
        return "agent 記錄 schema 不合:%r" % agent.get("record")
    if agent.get("agent_uid") != agent_uid:
        return "agent 記錄內 uid 不合:%r" % agent.get("agent_uid")
    if not isinstance(agent.get("role"), str) \
            or not ROLE_RE.fullmatch(agent.get("role")):
        return "agent 記錄 role 形狀不合:%r" % agent.get("role")
    if not _plain_int(agent.get("created_wall_ms")) \
            or agent.get("created_wall_ms") < 0:
        return "agent 記錄 created_wall_ms 不合"
    if not isinstance(agent.get("last_event_id"), str) \
            or not agent.get("last_event_id"):
        return "agent 記錄 last_event_id 不合"
    return None


def _session_shape_error(session, session_uuid):
    if session.get("record") != "role-model-session-v1" \
            or session.get("session_id") != session_uuid:
        return "session 記錄 schema/id 不合"
    active = session.get("active")
    if not isinstance(active, dict):
        return "session active 記錄不合"
    if not isinstance(active.get("agent_uid"), str) \
            or not UID_RE.fullmatch(active.get("agent_uid")):
        return "session active.agent_uid 不合"
    if active.get("trust") not in TRUST_LEVELS:
        return "session active.trust 記錄不合"
    if not _plain_int(active.get("bound_wall_ms")) \
            or active.get("bound_wall_ms") < 0:
        return "session active.bound_wall_ms 不合"
    if not isinstance(active.get("via"), str) or not active.get("via"):
        return "session active.via 不合"
    if not isinstance(active.get("event_id"), str) or not active.get("event_id"):
        return "session active.event_id 不合"
    history = session.get("history")
    if not isinstance(history, list):
        return "session history 記錄不合"
    for index, item in enumerate(history):
        if not isinstance(item, dict) \
                or not UID_RE.fullmatch(str(item.get("agent_uid") or "")) \
                or item.get("trust") not in TRUST_LEVELS \
                or not _plain_int(item.get("bound_wall_ms")) \
                or item.get("bound_wall_ms") < 0 \
                or not isinstance(item.get("via"), str) or not item.get("via") \
                or not isinstance(item.get("event_id"), str) or not item.get("event_id") \
                or item.get("state") != "superseded" \
                or not _plain_int(item.get("superseded_wall_ms")) \
                or item.get("superseded_wall_ms") < 0 \
                or not REQUEST_ID_RE.fullmatch(
                    str(item.get("superseded_by_request") or "")):
            return "session history[%d] 記錄不合" % index
    return None


def preflight_bind_agent(model, agent_uid, role, session_uuid, card_ref,
                         persona_sha=None, brain=None, rebind_persona=False,
                         equivalent_card_refs=()):
    """唯讀預演 `bind_agent` 的所有可知拒絕分支。

    回 `(state, why)`；state=`failed` 時保證本函式沒有寫任何東西。這不是
    磁碟交易保證，而是讓批次能先把所有席位的語意衝突找完，才寫第一筆帳。
    """
    if not isinstance(agent_uid, str) or not UID_RE.fullmatch(agent_uid):
        return "failed", "invalid agent_uid: %r" % (agent_uid,)
    if not isinstance(role, str) or not ROLE_RE.fullmatch(role):
        return "failed", "invalid role: %r" % (role,)
    if not isinstance(session_uuid, str) or not SESSION_RE.fullmatch(session_uuid):
        return "failed", "invalid session_id: %r" % (session_uuid,)
    if not isinstance(card_ref, str) or not CARD_RE.fullmatch(card_ref):
        return "failed", "invalid persona card ref: %r" % (card_ref,)
    if persona_sha is not None and not MODEL_SHA256_RE.fullmatch(persona_sha):
        return "failed", "invalid persona_sha (need sha256 hex): %r" % (persona_sha,)
    if brain is not None and not ROUTE_KEY_RE.fullmatch(brain):
        return "failed", "invalid brain route_key: %r" % (brain,)

    agent, error = _optional_record(model, "get_agent", "agent", agent_uid)
    if error:
        return "failed", error
    session, error = _optional_record(model, "get_session", "session", session_uuid)
    if error:
        return "failed", error
    if session is not None:
        shape_error = _session_shape_error(session, session_uuid)
        if shape_error:
            return "failed", shape_error

    if agent is None:
        if session is not None:
            bound = session["active"].get("agent_uid")
            if bound != agent_uid:
                return "failed", ("session 已綁給 %r,不是 %r——改綁要走 request_rebind 真人核准,"
                                  "本工具不代勞" % (bound, agent_uid))
            return "failed", ("session %s 指向 agent_uid %s,但該身份不存在(狀態不一致)"
                              % (session_uuid, agent_uid))
        return "new", ""

    shape_error = _agent_shape_error(agent, agent_uid)
    if shape_error:
        return "failed", shape_error
    if agent.get("role") != role:
        return "failed", ("agent_uid %s 已存在但 role=%r,席位檔是 %r——這不是重跑,是衝突"
                          % (agent_uid, agent.get("role"), role))

    state = "already"
    current_card = agent.get("persona_card")
    current_sha = agent.get("persona_sha")
    current_brain = agent.get("brain_route_key")
    if current_card is not None and (
            not isinstance(current_card, str) or not CARD_RE.fullmatch(current_card)):
        return "failed", "agent 狀態損壞:persona_card 形狀不合"
    if current_card is None and current_brain is not None:
        return "failed", "agent 狀態損壞:沒有人格但有 brain_route_key"
    if current_card is None and current_sha is not None:
        return "failed", "agent 狀態損壞:沒有人格但有 persona_sha"
    if current_sha is not None and (
            not isinstance(current_sha, str)
            or not MODEL_SHA256_RE.fullmatch(current_sha)):
        return "failed", "agent 狀態損壞:persona_sha 形狀不合"
    if current_brain is not None and (
            not isinstance(current_brain, str)
            or not ROUTE_KEY_RE.fullmatch(current_brain)):
        return "failed", "agent 狀態損壞:brain_route_key 形狀不合"
    same_card = (current_card == card_ref
                 or current_card in set(x for x in equivalent_card_refs if x))
    if not same_card:
        if current_card is not None and not rebind_persona:
            return "failed", ("已綁 %r,而本次要綁 %r——換卡要加 --rebind-persona 明說。"
                              "不加就不動,因為一個 --card 掃全部席位會把別人的卡蓋掉"
                              % (current_card, card_ref))
        state = "updated"
    elif current_card != card_ref or (
            persona_sha is not None and agent.get("persona_sha") != persona_sha):
        state = "updated"
    if brain is not None and agent.get("brain_route_key") != brain:
        state = "updated"

    if session is None:
        return "updated", ""
    bound = session["active"].get("agent_uid")
    if bound != agent_uid:
        return "failed", ("session 已綁給 %r,不是 %r——改綁要走 request_rebind 真人核准,"
                          "本工具不代勞" % (bound, agent_uid))
    return state, "身份與 session 綁定都已存在" if state == "already" else ""


def bind_agent_write_effects(model, agent_uid, session_uuid, card_ref,
                             persona_sha=None, brain=None,
                             equivalent_card_refs=()):
    """在語意 preflight 已綠後，精確列出 bind_agent 會寫哪兩個身份面。

    這仍是唯讀推導；caller 以其聯集決定哪些既有目錄/帳本需要寫權。
    第二次讀本身不可用就拒；讀後才發生的競態仍屬 preflight_only 邊界。
    """
    agent, error = _optional_record(model, "get_agent", "agent", agent_uid)
    if error:
        raise BindError(error)
    session, error = _optional_record(model, "get_session", "session", session_uuid)
    if error:
        raise BindError(error)

    write_agent = agent is None
    if agent is not None:
        current_card = agent.get("persona_card")
        same_card = (current_card == card_ref
                     or current_card in set(x for x in equivalent_card_refs if x))
        write_agent = current_card != card_ref
        if same_card and current_card == card_ref \
                and persona_sha is not None \
                and agent.get("persona_sha") != persona_sha:
            write_agent = True
        if brain is not None and agent.get("brain_route_key") != brain:
            write_agent = True
    write_session = session is None
    return {"write_agent": write_agent, "write_session": write_session,
            "write_ledger": write_agent or write_session}


def bind_agent(model, agent_uid, role, session_uuid, card_ref, actor,
               persona_sha=None, brain=None, rebind_persona=False,
               equivalent_card_refs=()):
    """回 (state, why):state ∈ new|already|updated|failed。
    規則與 bind-seats.py::bind_one 逐條相同(它現在呼叫這裡),多了 persona_sha 釘與大腦。"""
    preview, why = preflight_bind_agent(
        model, agent_uid, role, session_uuid, card_ref,
        persona_sha=persona_sha, brain=brain,
        rebind_persona=rebind_persona,
        equivalent_card_refs=equivalent_card_refs)
    if preview == "failed":
        return preview, why

    # 1. register(撞名核 role)
    try:
        model.register(agent_uid, role, actor)
        state = "new"
    except RoleModelError as err:
        if "already registered" not in str(err):
            return "failed", "register 失敗:%s" % str(err)[:200]
        try:
            agent = model.get_agent(agent_uid)
        except RoleModelError:
            return "failed", "register 說已註冊,但 show 查不出來(狀態不一致)"
        if agent.get("role") != role:
            return "failed", ("agent_uid %s 已存在但 role=%r,席位檔是 %r——這不是重跑,是衝突"
                              % (agent_uid, agent.get("role"), role))
        state = "already"

    # 2. bind-persona(換卡要具名;同卡不同 sha=重釘;都同=不寫)
    try:
        agent = model.get_agent(agent_uid)
    except RoleModelError:
        return "failed", "綁人格前查不到身份 %s(狀態不一致)" % agent_uid
    current_card = agent.get("persona_card")
    current_sha = agent.get("persona_sha")
    same_card = (current_card == card_ref
                 or current_card in set(x for x in equivalent_card_refs if x))
    if current_card != card_ref:
        if current_card is not None and not rebind_persona and not same_card:
            return "failed", ("已綁 %r,而本次要綁 %r——換卡要加 --rebind-persona 明說。"
                              "不加就不動,因為一個 --card 掃全部席位會把別人的卡蓋掉"
                              % (current_card, card_ref))
        try:
            model.bind_persona(agent_uid, card_ref, actor, persona_sha=persona_sha)
        except RoleModelError as err:
            return "failed", "bind-persona 失敗:%s" % str(err)[:200]
        state = "new" if state == "new" else "updated"
    elif persona_sha is not None and current_sha != persona_sha:
        try:
            model.bind_persona(agent_uid, card_ref, actor, persona_sha=persona_sha)
        except RoleModelError as err:
            return "failed", "bind-persona(重釘 sha)失敗:%s" % str(err)[:200]
        state = "new" if state == "new" else "updated"

    # 3. bind-brain(給了才動;值沒變不寫)
    if brain is not None and agent.get("brain_route_key") != brain:
        try:
            model.bind_brain(agent_uid, brain, actor)
        except RoleModelError as err:
            return "failed", "bind-brain 失敗:%s" % str(err)[:200]
        state = "new" if state == "new" else "updated"

    # 4. bind-session(撞名核 agent_uid;不同=改綁要真人)
    try:
        model.bind_session(session_uuid, agent_uid, "self_reported", actor)
    except RoleModelError as err:
        if "already bound" not in str(err):
            return "failed", "bind-session 失敗:%s" % str(err)[:200]
        try:
            session = model.get_session(session_uuid)
        except RoleModelError:
            return "failed", "bind-session 說已綁,但 show-session 查不出來"
        bound = (session.get("active") or {}).get("agent_uid")
        if bound != agent_uid:
            return "failed", ("session 已綁給 %r,不是 %r——改綁要走 request_rebind 真人核准,"
                              "本工具不代勞" % (bound, agent_uid))
        if state != "updated":
            state = "already"
    else:
        if state != "new":
            state = "updated"
    return state, "身份與 session 綁定都已存在" if state == "already" else ""


# ---- 席位層:找席位、解卡、查契約、寫延續檔、重投影 ------------------------------------

def default_cards_path(state_root):
    return Path(state_root) / "identity" / "cards.json"


def continuity_path(state_root, seat_id):
    # 這是獨立的最後寫入防線，不能假定每個 caller 都先走過 find_seat。
    # 只容許 canonical seat id，路徑分隔符與 `..` 因而不可能成為檔名內容。
    if not isinstance(seat_id, str) or not SEAT_ID_RE.fullmatch(seat_id):
        raise BindError("seat_id 形狀不合:%r" % (seat_id,))
    parent = Path(state_root) / "identity" / "seat-continuity"
    path = parent / ("%s.json" % seat_id)
    if path.parent != parent:
        raise BindError("延續檔路徑越界:%r" % (seat_id,))
    return path


def preflight_continuity_path(state_root, seat_id, require_write=True):
    """在第一筆身份寫入前驗證可知的延續檔路徑問題，且不建立任何東西。"""
    root = Path(state_root)
    path = continuity_path(root, seat_id)
    checks = ((root, "狀態根", True),
              (root / "identity", "identity 目錄", False),
              (path.parent, "seat-continuity 目錄", False))
    for current, label, required in checks:
        if current.is_symlink():
            raise BindError("%s 是 symlink,拒絕延續檔路徑:%s" % (label, current))
        if current.exists() and not current.is_dir():
            raise BindError("%s 不是目錄,拒絕延續檔路徑:%s" % (label, current))
        if required and not current.is_dir():
            raise BindError("%s 不存在或不可讀:%s" % (label, current))
    if require_write:
        identity = root / "identity"
        if path.parent.is_dir():
            write_parent = path.parent
        elif identity.is_dir():
            write_parent = identity
        else:
            write_parent = root
        access_kwargs = ({"effective_ids": True}
                         if os.access in os.supports_effective_ids else {})
        if not os.access(str(write_parent), os.W_OK | os.X_OK, **access_kwargs):
            raise BindError("延續檔父目錄不可寫:%s" % write_parent)
    # os.access 只回答權限位；真正落檔以 O_RDONLY|O_DIRECTORY 逐層釘 fd，
    # 所以預檢也用同一條 open 路徑。否則 0300(可寫可穿越、不可讀)會晚到
    # identity/ledger 已寫之後才失敗。
    dir_fd = _open_continuity_dir(root, create=False)
    if dir_fd is not None:
        os.close(dir_fd)
    if path.is_symlink():
        raise BindError("延續檔路徑是 symlink,拒寫:%s" % path)
    if path.exists() and not path.is_file():
        raise BindError("延續檔路徑不是普通檔案,拒寫:%s" % path)
    return path


def _open_child_dir(parent_fd, name, create):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)


def _open_continuity_dir(state_root, create):
    """逐層以 openat + O_NOFOLLOW 開啟，回傳釘住的 seat-continuity fd。"""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise BindError("平台缺 O_NOFOLLOW/O_DIRECTORY,拒絕退化為可穿越 symlink 的寫法")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(str(state_root), flags)
    except OSError as err:
        raise BindError("狀態根不可安全開啟:%s" % err)
    try:
        try:
            identity_fd = _open_child_dir(root_fd, "identity", create)
        except OSError as err:
            raise BindError("identity 目錄不可安全開啟:%s" % err)
    finally:
        os.close(root_fd)
    if identity_fd is None:
        return None
    try:
        try:
            return _open_child_dir(identity_fd, "seat-continuity", create)
        except OSError as err:
            raise BindError("seat-continuity 目錄不可安全開啟:%s" % err)
    finally:
        os.close(identity_fd)


def _write_atomic_at(dir_fd, name, obj, shown):
    """只用釘住的父目錄 fd 寫 tmp 再 rename，不再重新解析父路徑字串。"""
    try:
        current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    except OSError as err:
        raise BindError("延續檔目標不可檢查:%s(%s)" % (shown, err))
    if current is not None and (stat.S_ISLNK(current.st_mode)
                                or not stat.S_ISREG(current.st_mode)):
        raise BindError("延續檔目標不是普通檔案,拒寫:%s" % shown)

    tmp_name = "%s.tmp.%d.%d" % (name, os.getpid(), time.time_ns())
    tmp_created = False
    try:
        fd = os.open(tmp_name,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dir_fd)
        tmp_created = True
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, sort_keys=True, indent=1)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_created = False
        os.fsync(dir_fd)
    except OSError as err:
        raise BindError("延續檔寫入失敗:%s(%s)" % (shown, err))
    finally:
        if tmp_created:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass


def _preflight_output_dir(path, label, require_write):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise BindError("狀態寫入面不可用:%s 不是安全目錄:%s" % (label, path))
    access_kwargs = ({"effective_ids": True}
                     if os.access in os.supports_effective_ids else {})
    mode = os.R_OK | os.X_OK | (os.W_OK if require_write else 0)
    if not os.access(str(path), mode, **access_kwargs):
        raise BindError("狀態寫入面不可用:%s 權限不足:%s" % (label, path))
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise BindError("狀態寫入面不可用:平台缺 O_NOFOLLOW/O_DIRECTORY")
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as err:
        raise BindError("狀態寫入面不可用:%s 無法安全開啟:%s" % (label, err))
    else:
        os.close(fd)


def _preflight_rw_file(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BindError("狀態寫入面不可用:%s 不是普通檔案:%s" % (label, path))
    try:
        fd = os.open(str(path), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError as err:
        raise BindError("狀態寫入面不可用:%s 不可寫:%s" % (label, err))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BindError("狀態寫入面不可用:%s 不是單連結普通檔案" % label)
    finally:
        os.close(fd)


def preflight_bind_outputs(state_root, ledger_path=None, do_reproject=True,
                           write_agents=True, write_sessions=True,
                           write_ledger=True):
    """唯讀驗證本次 bind 必定會碰到的既有寫入面，不建立或修改檔案。

    這只攔執行前已存在的目錄/權限/檔型問題；開始 commit 後才發生的
    ENOSPC、EIO、權限改變仍依 batch 的 preflight_only 契約回報部分提交。
    """
    root = Path(state_root)
    identity = root / "identity"
    required_dirs = [(identity / "agents", write_agents)]
    # True=本輪會寫，False=只讀，None=只因 constructor 要求其形狀安全。
    required_dirs.append((identity / "sessions", write_sessions))
    # RoleModel constructor 目前會建立這個目錄；即使普通 bind 不寫它，
    # 缺目錄時仍需要 identity 父層可寫，既有 symlink/非目錄也要先拒。
    required_dirs.append((identity / "rebind-requests", None))

    identity_exists = identity.exists() or identity.is_symlink()
    _preflight_output_dir(root, "state_root", not identity_exists)
    if identity_exists:
        need_identity_write = do_reproject or any(
            not (path.exists() or path.is_symlink()) for path, _write in required_dirs)
        _preflight_output_dir(identity, "identity", need_identity_write)
        for directory, require_write in required_dirs:
            if directory.exists() or directory.is_symlink():
                if require_write is None:
                    if directory.is_symlink() or not directory.is_dir():
                        raise BindError(
                            "狀態寫入面不可用:identity/%s 不是安全目錄:%s"
                            % (directory.name, directory))
                else:
                    _preflight_output_dir(
                        directory, "identity/%s" % directory.name, require_write)

    ledger_path = Path(ledger_path) if ledger_path else root / "ledger" / "aib-ledger.jsonl"
    ledger_dir = ledger_path.parent
    ledger_exists = ledger_path.exists() or ledger_path.is_symlink()
    if write_ledger:
        _preflight_output_dir(ledger_dir, "ledger", not ledger_exists)
        if ledger_exists:
            _preflight_rw_file(ledger_path, "ledger")
    else:
        if ledger_dir.is_symlink() or not ledger_dir.is_dir():
            raise BindError("狀態寫入面不可用:ledger 不是安全目錄:%s" % ledger_dir)
        if ledger_exists and (ledger_path.is_symlink() or not ledger_path.is_file()):
            raise BindError("狀態寫入面不可用:ledger 不是普通檔案:%s" % ledger_path)

    # 投影器會在 identity 根以 tmp+replace 寫兩份文件及兩份 provenance。
    if do_reproject and identity_exists:
        for name in ("bindings.json", "seat-display.json"):
            target = identity / name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise BindError("狀態寫入面不可用:投影目標不是普通檔案:%s" % target)
            provenance = identity / (name + ".provenance.json")
            if provenance.exists() or provenance.is_symlink():
                _preflight_rw_file(provenance, "投影收據 %s" % provenance.name)


def write_continuity(state_root, seat_id, agent_uid, session_uuid, persona_uid,
                     persona_sha, brain_route_key, now_ms, bind_source=None):
    """``bind_source``(BA,the maintainer 09-02 23:1x 裁「bindings 記來源=auto(可拆)」):
    這一筆綁定是誰決定的。**只在有值時才寫入那個鍵**——人綁與批綁一個位元組都不變,
    所以「沒有這個鍵」的意思是「不是自動綁」,不是「不知道」。
    ★ 為什麼不無條件寫:延續檔的形狀有消費者在做整份等值比對
    (`tests/test-bind-seats.py` 的 `assertEqual`),無條件加欄會把一支綠的臂換成紅,
    而那支臂守的東西跟本件無關。可拆=把這個鍵拿掉,自動綁的痕跡就沒了。"""
    rec = {"record": "seat-continuity-v1", "seat_id": seat_id, "agent_uid": agent_uid,
           "session_uuid": session_uuid, "persona_uid": persona_uid,
           "persona_sha": persona_sha, "brain_route_key": brain_route_key,
           "updated_wall_ms": int(now_ms)}
    if bind_source is not None:
        rec["bind_source"] = str(bind_source)
    # 09-30:這一席綁的專案(aib newproj 寫進來的 project_uid / project_title)不是人格的一部分,
    #   以前整份重寫會把它洗掉(換人格 = 專案不見)。原本有才帶著,原本沒有就一個位元組都不加。
    try:
        prev = read_continuity(state_root, seat_id) or {}
    except Exception:  # noqa: BLE001 —— 讀不到舊的就照舊寫,不擋綁定
        prev = {}
    for key in ("project_uid", "project_title"):
        if isinstance(prev, dict) and prev.get(key):
            rec[key] = prev[key]
    path = preflight_continuity_path(state_root, seat_id)
    dir_fd = _open_continuity_dir(state_root, create=True)
    try:
        _write_atomic_at(dir_fd, path.name, rec, path)
    finally:
        os.close(dir_fd)
    return rec


def read_continuity(state_root, seat_id):
    p = preflight_continuity_path(state_root, seat_id, require_write=False)
    dir_fd = _open_continuity_dir(state_root, create=False)
    if dir_fd is None:
        return None
    try:
        try:
            fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        except OSError as err:
            raise BindError("延續檔不可安全開啟:%s(%s)" % (p, err))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise BindError("延續檔不是普通檔案:%s" % p)
            with os.fdopen(fd, encoding="utf-8") as fh:
                fd = None
                rec = json.load(fh)
        except (OSError, ValueError):
            return None
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        os.close(dir_fd)
    if not isinstance(rec, dict) or rec.get("record") != "seat-continuity-v1":
        return None
    return rec


def resolve_card_ref(cards, ref):
    """--card 值 → (要寫進身份層的 card 身份, persona_sha, entry)。
    v2 表:身份=uid(舊 card_ref 只用來查表);v1 表:身份=card_ref 原值、sha=None。"""
    entry, _legacy = resolve_card(cards, ref)
    if entry is None:
        raise BindError("卡片 %r 不在登記表裡(uid 或 card_ref 都對不上)" % (ref,))
    if entry.get("persona_sha"):
        return entry["persona_uid"], entry["persona_sha"], entry
    return ref, None, entry


def reproject(state_root, cards_path, contract_spec):
    """綁完重跑投影(同一支 CLI,收據照留)。回 (rc, stdout+stderr)。"""
    out = Path(state_root) / "identity" / "bindings.json"
    disp = Path(state_root) / "identity" / "seat-display.json"
    cmd = [sys.executable, str(PROJECTION), "project", "--state-root", str(state_root),
           "--cards", str(cards_path), "--out", str(out), "--display-out", str(disp)]
    if contract_spec:
        cmd += ["--contract", contract_spec]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


SHA256_RE = re.compile(r"[0-9a-f]{64}")


def bind_seat(state_root, seat_id, card, actor, brain=None, rebind_persona=False,
              cards_path=None, contract_spec=None, dry_run=False, do_reproject=True,
              ledger_path=None, now_ms=None, pin_sha=None, bind_source=None):
    """綁一席。回 dict(machine-readable);拒綁 raise BindError。

    pin_sha(D-4 重開續綁用):不釘登記表現值,改釘上一次綁定確認過的指紋——這樣卡在
    席位關著的期間升了版,重開後右欄會亮「卡已更新」,而不是被續綁悄悄蓋成新版。"""
    state_root = Path(state_root)
    cards_path = Path(cards_path) if cards_path else default_cards_path(state_root)
    if not (state_root / "ledger").is_dir():
        raise BindError("找不到帳本目錄 %s —— 先跑 src/bootstrap/aib_init.py" % (state_root / "ledger"))
    try:
        cards = load_card_registry(str(cards_path))
    except ProjectionError as err:
        raise BindError("卡片登記表讀不到:%s(先跑 card_registry.py scan)" % err)
    contract = load_contract(contract_spec)
    if contract is None:
        raise BindError("路由契約讀不到(--contract 或 EZ2_ROUTE_REGISTRY);拒絕在看不到規則的情況下綁")
    seat_path, rec = find_seat(state_root, seat_id)
    role, session_uuid, agent_uid = seat_binding_inputs(rec)
    try:
        read_view = RoleModelReadView(state_root)
    except RoleModelError as err:
        raise BindError("狀態不可讀:%s" % err)
    if card is None:
        # 只換大腦(D-3 [綁定大腦] 選單):卡=這席現在綁的那張;沒綁過人格就拒——
        # 大腦是人格的落點(role_model.bind_brain 同規則,這裡提早說清楚)。
        if brain is None:
            raise BindError("bind 需要 --card(或 --brain 只換大腦)")
        try:
            current = read_view.get_agent(agent_uid)
        except RoleModelError as err:
            if str(err).startswith("agent not found:"):
                current = None
            else:
                raise BindError("狀態不可讀:%s" % err)
        card = (current or {}).get("persona_card")
        if not card:
            raise BindError("這席還沒綁人格,不能先綁大腦(先 [綁定人格])")
    persona_uid, persona_sha, entry = resolve_card_ref(cards, card)
    if pin_sha is not None:
        if not SHA256_RE.fullmatch(pin_sha):
            raise BindError("pin_sha 形狀不合(需 sha256 hex)")
        persona_sha = pin_sha
    if brain is not None:
        ok, code, detail = check_against_contract(brain, contract)
        if not ok:
            raise BindError("大腦 %s 過不了契約(%s):%s" % (brain, code, detail))

    preview, why = preflight_bind_agent(
        read_view, agent_uid, role, session_uuid, persona_uid,
        persona_sha=persona_sha, brain=brain,
        rebind_persona=rebind_persona,
        equivalent_card_refs=(entry.get("card_ref"),))
    if preview == "failed":
        raise BindError("%s:%s" % (seat_id, why))

    plan = {"seat_id": seat_id, "seat_file": str(seat_path), "role": role,
            "session_uuid": session_uuid, "agent_uid": agent_uid,
            "persona_uid": persona_uid, "persona_sha": persona_sha,
            "display_name": entry.get("display_name"), "brain_route_key": brain}
    if dry_run:
        plan["dry_run"] = True
        return plan

    # 所有正常寫入面與延續檔都在第一筆 append-only 帳本前先驗。
    preflight_continuity_path(state_root, seat_id)
    effects = bind_agent_write_effects(
        read_view, agent_uid, session_uuid, persona_uid,
        persona_sha=persona_sha, brain=brain,
        equivalent_card_refs=(entry.get("card_ref"),))
    preflight_bind_outputs(
        state_root, ledger_path=ledger_path, do_reproject=do_reproject,
        write_agents=effects["write_agent"],
        write_sessions=effects["write_session"],
        write_ledger=effects["write_ledger"])
    model = RoleModel(state_root=str(state_root), ledger_path=ledger_path, now_ms=now_ms)
    state, why = bind_agent(model, agent_uid, role, session_uuid, persona_uid, actor,
                            persona_sha=persona_sha, brain=brain,
                            rebind_persona=rebind_persona,
                            equivalent_card_refs=(entry.get("card_ref"),))
    plan.update({"state": state, "why": why})
    if state == "failed":
        raise BindError("%s:%s" % (seat_id, why))
    agent = model.get_agent(agent_uid)
    plan["continuity"] = write_continuity(
        state_root, seat_id, agent_uid, session_uuid, agent.get("persona_card"),
        agent.get("persona_sha"), agent.get("brain_route_key"),
        now_ms() if callable(now_ms) else int(time.time() * 1000), bind_source)
    plan["bind_source"] = bind_source
    if do_reproject:
        rc, log = reproject(state_root, cards_path, contract_spec)
        plan["reproject"] = {"rc": rc, "log_tail": log[-400:]}
    return plan


def unbind_seat(state_root, seat_id, actor, brain_only=False, cards_path=None,
                contract_spec=None, dry_run=False, do_reproject=True, ledger_path=None,
                now_ms=None):
    state_root = Path(state_root)
    cards_path = Path(cards_path) if cards_path else default_cards_path(state_root)
    seat_path, rec = find_seat(state_root, seat_id)
    role, session_uuid, agent_uid = seat_binding_inputs(rec)
    plan = {"seat_id": seat_id, "agent_uid": agent_uid, "brain_only": brain_only}
    if dry_run:
        plan["dry_run"] = True
        return plan
    preflight_continuity_path(state_root, seat_id)
    preflight_bind_outputs(
        state_root, ledger_path=ledger_path, do_reproject=do_reproject,
        write_agents=True, write_sessions=False if do_reproject else None,
        write_ledger=True)
    model = RoleModel(state_root=str(state_root), ledger_path=ledger_path, now_ms=now_ms)
    try:
        if brain_only:
            model.unbind_brain(agent_uid, actor)
        else:
            model.unbind_persona(agent_uid, actor)
    except RoleModelError as err:
        raise BindError("解綁失敗:%s" % err)
    agent = model.get_agent(agent_uid)
    plan["continuity"] = write_continuity(
        state_root, seat_id, agent_uid, session_uuid, agent.get("persona_card"),
        agent.get("persona_sha"), agent.get("brain_route_key"),
        now_ms() if callable(now_ms) else int(time.time() * 1000))
    if do_reproject:
        rc, log = reproject(state_root, cards_path, contract_spec)
        plan["reproject"] = {"rc": rc, "log_tail": log[-400:]}
    return plan


def seat_status(state_root, seat_id):
    state_root = Path(state_root)
    seat_path, rec = find_seat(state_root, seat_id)
    out = {"seat_id": seat_id, "seat_file": str(seat_path),
           "continuity": read_continuity(state_root, seat_id)}
    try:
        role, session_uuid, agent_uid = seat_binding_inputs(rec)
    except BindError as err:
        out["agent"] = None
        out["why"] = str(err)
        return out
    out.update({"role": role, "session_uuid": session_uuid, "agent_uid": agent_uid})
    model = RoleModelReadView(state_root)
    try:
        out["agent"] = model.get_agent(agent_uid)
    except RoleModelError:
        out["agent"] = None
    return out


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bind-one", description="單席綁定執行器(預設會寫;--dry-run 只印計畫)")
    ap.add_argument("command", choices=["bind", "unbind", "status"])
    ap.add_argument("--seat", required=True, help="seat_id(席位檔 seat_id 欄,4-8 hex)")
    ap.add_argument("--card", help="bind 用:卡 uid(或舊 card_ref)")
    ap.add_argument("--brain", help="bind 用:第二大腦 routeKey(不給=用卡預設)")
    ap.add_argument("--actor", help="帳本署名 role@hex/Engine(bind/unbind 必填,無預設)")
    ap.add_argument("--actor-kind", default="main", choices=["human", "main", "sub_agent", "system"])
    ap.add_argument("--rebind-persona", action="store_true", help="允許把已綁別張卡的席位改綁")
    ap.add_argument("--brain-only", action="store_true", help="unbind 用:只解大腦、留人格")
    ap.add_argument("--state-root")
    ap.add_argument("--cards", help="cards.json(預設 <state>/identity/cards.json)")
    ap.add_argument("--contract", help="路由契約(檔案或 git:<repo>#<rev>:<rel>;預設 EZ2_ROUTE_REGISTRY)")
    ap.add_argument("--no-reproject", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = state_root_of(args.state_root)
    try:
        if args.command == "status":
            sys.stdout.write(_dump(seat_status(root, args.seat)))
            return 0
        actor = actor_of(args.actor, args.actor_kind)
        if args.command == "bind":
            if not args.card and not args.brain:
                raise BindError("bind 需要 --card(或 --brain 只換大腦)")
            out = bind_seat(root, args.seat, args.card or None, actor, brain=args.brain,
                            rebind_persona=args.rebind_persona, cards_path=args.cards,
                            contract_spec=args.contract, dry_run=args.dry_run,
                            do_reproject=not args.no_reproject)
        else:
            out = unbind_seat(root, args.seat, actor, brain_only=args.brain_only,
                              cards_path=args.cards, contract_spec=args.contract,
                              dry_run=args.dry_run, do_reproject=not args.no_reproject)
    except BindError as err:
        print("bind-one: %s" % err, file=sys.stderr)
        return 2
    sys.stdout.write(_dump(out))
    if out.get("reproject") and out["reproject"]["rc"] != 0:
        print("bind-one: ⚠ 綁定已寫入,但重投影 rc=%d(畫面可能沒更新):%s"
              % (out["reproject"]["rc"], out["reproject"]["log_tail"][-200:]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
