#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""主人手機配對(開發文件第 33 節 v2.1:33.5 Andy 10-18 17:5x 裁、33.5a 18:0x、33.6 @abb89 17:59 審)。只用標準庫。

- 手機用一般 Nostr App(0xchat / Amethyst / Primal)傳 NIP-17 私訊給這台的公開席;手機私鑰只在手機上,
  電腦上的 AI 偽造不了手機簽的私訊 ⇒ 配對過的手機傳的話可以當主人本人的(但界線見 ALLOWED_NOTE)。
- 配對要兩道本人關:pair start 一窗(開始配對);手機傳回配對碼後**不自動配**,第二窗顯示手機 npub 頭 10 + 尾 6
  (手機 App 畫面上顯示的就是 npub,本人對得上)放最前面,本人按了才寫 paired.json。配對碼本身不是防線(艦長室顯示、AI 讀得到)。
- 最多 3 支;撤銷一支或全部(本人證明);撤銷後那支傳的一律拒收。
- 防重播:收件端記已處理的事件 id 7 天、rumor 時間差 > 10 分鐘拒收;指令同一個 id 不執行第二次。
- 分成指令 / 討論(@abb89 F4 收緊):開頭 `gm` 或 `GM`(不分大小寫)+ 空白或冒號、`指令` + 空白或冒號、
  `!` / `!` + 不是驚嘆號的字 ⇒ 指令;其他 ⇒ 討論。只打「gm」、「指令集…」、「!!! 太好了」⇒ 討論。
  ⚠ 照 Andy 裁,「gm 大家早」會被當指令(gm 後面接了空白與內容)。
- 信任錨(@abb89 F1):主人簽的機器清單裡 role=phone 的那幾支;paired.json 只是顯示用快取,工人收信不看它。
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from atomic_json import write_json  # noqa: E402

MAX_PHONES = 3
CODE_LEN = 10
PAIR_WINDOW_S = 600
SKEW_S = 600
SEEN_KEEP_S = 7 * 86400
_B32 = "abcdefghijkmnpqrstuvwxyz23456789"
_NPUB_RE = re.compile(r"^npub1[02-9ac-hj-np-z]{58}$")
_CMD_RE = re.compile(r"^\s*(?:gm[\s:\uff1a]+|指令[\s:\uff1a]+|[!\uff01](?![!\uff01])\s*)(\S.*)$", re.I | re.S)

# 手機指令只准這幾類(@abb89 33.6 第 5 點白名單)。⚠ 核心分不出一句話屬哪一類:這是寫給席位遵守的規矩,不是防線;
# 真正的防線是那些敏感指令本來就要在電腦上 Touch ID / 密語。
ALLOWED_NOTE = ("對話與問狀態;叫席位做它專案裡的事(寫程式、改文件、跑測試、開 PR);看進度。"
                "不准:配對 / 撤手機、機器清單、主人金鑰、叫醒允許、好友 / 封鎖、分享等級、Touch ID / PIN 設定、"
                "金鑰櫃(secret ask、claude-apikey)、裝 / 解外掛、授權檔 —— 這些要在電腦上按 Touch ID。")


class PairError(ValueError):
    """原因碼:too_many / not_paired / no_pending / expired / bad_npub。"""


def _chat_home(env):
    import chat_bridge as CB
    return CB.chat_home(env)


def paired_path(env=None):
    return Path(_chat_home(env)) / "paired.json"


def read_paired(env=None):
    try:
        rec = json.loads(paired_path(env).read_text(encoding="utf-8"))
        phones = rec.get("phones") if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return []
    return [p for p in phones or [] if isinstance(p, dict) and _NPUB_RE.match(str(p.get("npub")))]


def _save_paired(phones, env=None):
    p = paired_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(p), {"v": 1, "phones": phones})
    os.chmod(str(p), 0o600)


def is_paired(npub, env=None):
    return any(p["npub"] == npub for p in read_paired(env))


def short(npub):
    """給本人對的樣子:npub 頭 10 + 中段 8 + 尾 6(手機 App 畫面上看得到的就是 npub;頭 5 碼固定是 npub1,
    只比頭尾約 55 位元,加中段 8 碼才夠 ⇒ @abb89 F5)。"""
    return "%s…%s…%s" % (npub[:10], npub[29:37], npub[-6:])


def add_phone(npub, env=None, now=None):
    if not _NPUB_RE.match(npub or ""):
        raise PairError("bad_npub")
    phones = read_paired(env)
    if any(p["npub"] == npub for p in phones):
        return phones
    if len(phones) >= MAX_PHONES:
        raise PairError("too_many")
    phones.append({"npub": npub, "at": int(time.time() if now is None else now)})
    _save_paired(phones, env)
    return phones


def revoke(target, env=None):
    """target = npub、npub 頭幾碼(至少 10)或 all。回撤掉的 npub 清單;沒有符合的 ⇒ not_paired。"""
    phones = read_paired(env)
    if target == "all":
        hit = phones
    else:
        t = (target or "").strip()
        hit = [p for p in phones if p["npub"] == t or (len(t) >= 10 and p["npub"].startswith(t))]
    if not hit:
        raise PairError("not_paired")
    _save_paired([p for p in phones if p not in hit], env)
    return [p["npub"] for p in hit]


# ── 配對中(pair start 寫、工人收到配對碼時記候選、pair start 等到候選後第二窗)──

def pending_path(root):
    return Path(root) / "pair-phone-pending.json"


def new_code():
    return "".join(_B32[b % len(_B32)] for b in os.urandom(CODE_LEN))


def _code_hash(code):
    return hashlib.sha256(("aib-phone-pair/" + "".join(ch for ch in code.lower() if ch in _B32)).encode()).hexdigest()


def start_pending(root, code, seat, now=None):
    now = int(time.time() if now is None else now)
    write_json(str(pending_path(root)), {"v": 1, "code": _code_hash(code), "seat": seat, "until": now + PAIR_WINDOW_S,
                                         "candidates": []})
    os.chmod(str(pending_path(root)), 0o600)


def read_pending(root):
    try:
        rec = json.loads(pending_path(root).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def offer_code(root, from_npub, content, now=None):
    """工人:非 aib 寄件人傳來一則話 ⇒ 是不是配對中的那組碼。是 ⇒ 記成候選(**不配**),回 True;否則 False。"""
    now = time.time() if now is None else now
    rec = read_pending(root)
    if not rec or now > rec.get("until", 0) or not _NPUB_RE.match(from_npub or ""):
        return False
    text = (content or "").strip()
    if len(text) > 40 or _code_hash(text) != rec.get("code"):
        return False
    cands = [c for c in rec.get("candidates") or [] if isinstance(c, dict)]
    if from_npub not in {c.get("npub") for c in cands}:
        cands.append({"npub": from_npub, "at": int(now)})
    rec["candidates"] = cands[-5:]
    write_json(str(pending_path(root)), rec)
    return True


def clear_pending(root):
    try:
        pending_path(root).unlink()
    except FileNotFoundError:
        pass


# ── 收件:防重播 + 指令 / 討論 ──

def seen_path(root):
    return Path(root) / "paired-seen.json"


def accept_event(root, event_id, created_at, now=None):
    """手機傳來的一則 ⇒ 收不收(防重播):時間差 > SKEW_S ⇒ "skew";id 處理過 ⇒ "replay";收 ⇒ None 並記下 id。"""
    import fcntl
    now = time.time() if now is None else now
    if not isinstance(created_at, (int, float)) or abs(now - created_at) > SKEW_S:
        return "skew"
    p = seen_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            seen = json.loads(p.read_text(encoding="utf-8"))
            seen = seen if isinstance(seen, dict) else {}
        except (OSError, ValueError):
            seen = {}
        if event_id in seen:
            return "replay"
        seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and now - v < SEEN_KEEP_S}
        seen[event_id] = int(now)
        write_json(str(p), seen)
        os.chmod(str(p), 0o600)
        return None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def classify(content):
    """⇒ ("command", 去掉開頭字的內容) 或 ("chat", 原文)。"""
    m = _CMD_RE.match(content or "")
    if m and m.group(1).strip():
        return "command", m.group(1).strip()
    return "chat", content


def phone_log_path(root):
    return Path(root) / "phone-commands.jsonl"


def log_command(root, phone, event_id, text, now=None):
    """每一則手機指令留帳(@abb89 33.6 第 2 點):via=phone:<npub 頭尾>、原文雜湊、前 40 字(狀態列顯示用)。"""
    line = json.dumps({"v": 1, "at": int(time.time() if now is None else now), "via": "phone:" + short(phone),
                       "id": event_id, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "head": text[:40]},
                      ensure_ascii=False) + "\n"
    fd = os.open(str(phone_log_path(root)), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def last_command(root):
    try:
        lines = phone_log_path(root).read_text(encoding="utf-8").strip().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, IndexError):
        return None


# ── 指令(aib chat pair …)──

PROVE = None          # 只給測試換掉(行程內;不讀環境變數)
_TARGET_RE = re.compile(r"^npub1[02-9ac-hj-np-z]{5,58}$")      # 至少頭 10 碼(npub1 + 5)


def _prove(reason, env, root):
    import chat_owner as CO
    return (PROVE or CO.prove)(reason, env, root=root)


def _seat_npub(env, seat=None, live=None):
    """配對要掃的席:指定的、或這台第一個公開而且有金鑰的席。回 (席碼, npub) 或 (None, None)。
    跟名冊 / 寄件同一把(sending_seats:那一席現在綁的人格);換過人格的席不給舊人格那把(@abb89 #297 r1)。"""
    import chat_roster as CR
    import nostr_keys as NK
    pub = set((CR.read_profile(env) or {}).get("public") or [])
    for e in NK.sending_seats(env, live):
        if e["seat5"] in pub and (seat is None or e["seat5"] == seat):
            return e["seat5"], e["npub"]
    return None, None


def hold_queued_from(npub, env=None):
    """撤銷手機後(@abb89 F3):各席佇列裡這支手機傳來、還沒處理的,一律改待審(不讓已排隊的指令在撤銷後照跑)。回改了幾筆。"""
    import dm_queue as DQ
    n = 0
    for seat in DQ.all_seats(env):
        for item in DQ.list_items(seat, env):
            if item.get("sender_origin") == "nostr:" + npub and item.get("status") not in (DQ.HOLD_STATUS, "done", "deleted"):
                if DQ.hold_item(seat, item["id"], "phone_revoked", env=env):
                    n += 1
    return n


def cmd_pair(a, env, out):
    import chat_bridge as CB
    import chat_owner as CO
    import approve_gate as AG
    import chat_owner_cli as OC
    T = CB.T
    root = CB.bridge_root(env)
    if a.action in (None, "list"):
        phones = read_paired(env)
        if a.json:
            out.write(json.dumps({"phones": [dict(p, short=short(p["npub"])) for p in phones], "max": MAX_PHONES,
                                  "last_command": last_command(root)}, ensure_ascii=False) + "\n")
            return 0
        if not phones:
            out.write(T("pair_none") + "\n")
        for p in phones:
            out.write(T("pair_list_row", phone=short(p["npub"]), at=time.strftime("%m-%d %H:%M", time.localtime(p["at"]))) + "\n")
        out.write(T("pair_lost_hint") + "\n")
        return 0
    if a.action == "revoke":
        target = (a.target or "").strip()
        if target != "all" and not _TARGET_RE.match(target):           # @abb89 F7:只收 npub 格式或 all
            out.write(T("pair_err_not_paired") + "\n")
            return 2
        hit = [p["npub"] for p in read_paired(env) if target == "all" or p["npub"].startswith(target)]

        def mutate(cur, now):
            phones_signed = [d["npub"] for d in cur.get("devices") or [] if d.get("role") == "phone"
                             and (target == "all" or d["npub"].startswith(target))]
            if not phones_signed:
                raise CO.OwnerError("not_listed")
            nxt = cur
            for n in phones_signed:
                nxt = CO.next_list(nxt, now, revoke=n)
                hit.append(n)
            return nxt
        rc, _nxt = OC.sign_list_change(env, out, AG.reason("chat_pair.revoke", {"target": target}), mutate)
        if rc != 0:
            return 2
        gone = set(hit)
        for n in gone:
            try:
                revoke(n, env)
            except PairError:
                pass
            hold_queued_from(n, env)
        out.write(T("pair_revoked", n=len(gone)) + "\n")
        return 0
    if a.action == "start":
        if len(read_paired(env)) >= MAX_PHONES:
            out.write(T("pair_err_too_many", n=MAX_PHONES) + "\n")
            return 2
        if OC.owner_status(env)["role"] != "sealed":                 # F1:手機要簽進主人清單 ⇒ 主人金鑰要先加密
            out.write(T("pair_need_protect") + "\n")
            return 2
        seat, npub = _seat_npub(env, a.seat)
        if not seat:
            out.write(T("pair_no_seat") + "\n")
            return 2
        why = _prove(AG.reason("chat_pair.start", {"seat": seat}), env, root)
        if why:
            out.write(T("pair_proof_failed", why=why) + "\n")
            return 2
        code = new_code()
        start_pending(root, code, seat)
        if a.json:
            out.write(json.dumps({"seat": seat, "npub": npub, "qr": "nostr:" + npub, "code": code,
                                  "minutes": PAIR_WINDOW_S // 60}) + "\n")
        else:
            out.write(T("pair_show", npub=npub, code=code, minutes=PAIR_WINDOW_S // 60) + "\n")
        if a.no_wait:
            return 0
    return _confirm(a, env, out, root)


def _confirm(a, env, out, root):
    """等手機傳回配對碼。同一窗期一個以上候選 ⇒ 整批作廢 + 警告(@abb89 F5:讀到碼的 AI 可能搶先送);
    只有一個 ⇒ 簽進主人清單(第二窗字「把手機 <頭…中…尾> 配成主人手機」+ 差異;要主人密語)。"""
    import approve_gate as AG      # R1 從 cmd_pair 拆出來時漏帶 ⇒ pair confirm NameError(10-29 補)
    import chat_bridge as CB
    import chat_owner as CO
    import chat_owner_cli as OC
    T = CB.T
    if not read_pending(root):
        out.write(T("pair_err_no_pending") + "\n")
        return 2
    if not a.json:
        out.write(T("pair_waiting") + "\n")
    while True:
        rec = read_pending(root)
        if not rec or time.time() > rec.get("until", 0):
            clear_pending(root)
            out.write((json.dumps({"ok": False, "why": "timeout"}) if a.json else T("pair_timeout")) + "\n")
            return 2
        if rec.get("candidates"):
            time.sleep(float(env.get("AIB_PAIR_SETTLE") or 2))     # 再等一下:第二個(搶先或跟在後面的)也算整批作廢
            rec = read_pending(root) or {}
            cands = [c.get("npub") for c in rec.get("candidates") or [] if _NPUB_RE.match(str(c.get("npub")))]
            if len(cands) > 1:
                clear_pending(root)
                out.write((json.dumps({"ok": False, "why": "multiple"}) if a.json else T("pair_multi", n=len(cands))) + "\n")
                return 2
            if not cands:
                continue
            phone = cands[0]
            clear_pending(root)
            rc, _nxt = OC.sign_list_change(
                env, out, AG.reason("chat_pair.confirm", {"phone": short(phone)}),
                lambda cur, now: CO.next_list(cur, now, add={"npub": phone, "name": "phone", "role": "phone"}))
            if rc != 0:
                out.write((json.dumps({"ok": False, "why": "not_signed"}) if a.json else T("pair_rejected", phone=short(phone),
                                                                                        why="not_signed")) + "\n")
                return 2
            try:
                n = len(add_phone(phone, env))
            except PairError:
                n = MAX_PHONES
            out.write((json.dumps({"ok": True, "phone": phone, "short": short(phone), "n": n}) if a.json
                       else T("pair_done", phone=short(phone), n=n)) + "\n")
            return 0
        time.sleep(float(env.get("AIB_PAIR_POLL") or 1))
