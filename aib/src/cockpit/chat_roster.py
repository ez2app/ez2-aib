#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球對談的艙位名冊與收信中繼站清單——核心這一半(開發文件第 5.4、5.6、5.8 節;22.1 PR-7 = 第 1 期 1.3)。

只用標準庫:組名冊內容、驗別人的名冊內容、找席。**簽章與發佈不在這裡**:轉接程式的 venv 那一半(chat_nostr.py,
用 nostr-sdk)讀這裡組好的 outgoing.json,用主人金鑰簽 kind 30078(d = aib-roster)、用各席金鑰簽 kind 10050 發出去;
抓別人的名冊時先由 nostr-sdk 驗簽,再交給這裡的 parse() 驗內容。

主人資料 ~/.ez2-aib/chat/profile.json(0600):
  {"v":1, "display_name":"Andy", "country":"TW", "public":["abb89", …], "updated_at":…}
  - display_name、country:`aib chat on` 第一次一定要給(第 5.8、5.9 節);`aib chat name` 改名。
  - public:列進名冊的席碼。**預設一席都不列**(Andy 10-15 23:5x 裁):`aib chat publish <席碼>` 逐席打開、`aib chat hide` 關。
    列進去的席全世界都看得到它的 npub、人格名、角色,外人也才寄得到;沒列的席外人寄不到。
好友 ~/.ez2-aib/chat/friends.json(0600):只收本人與好友的信,其他一律拒收(維護者 10-16 12:1x);見下面「好友」一節。
名冊內容(第 5.4 節;第 1 期 owner 填主人 npub,NIP-05 網域到第 2 期):
  {"v":1, "owner":"npub1…", "display_name", "country", "cabins":[{"cabin","seats":[{"seat","npub","name","role",
   "accepts":["dm"],"langs":[…],"public":true}]}], "dm_relays":[…], "aib_version", "envelope":[1], "updated_at"}
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
import dm_global_files as GF  # noqa: E402  顯示名稱清理、國碼、npub 格式都用契約那一份
import nostr_keys as NK  # noqa: E402
from atomic_json import write_json  # noqa: E402

VERSION = 1
ROSTER_D = "aib-roster"
KIND_ROSTER = 30078
KIND_DM_RELAYS = 10050
# 第 19.1 節:第 1 期只用 primal、nos.lol 兩站(收信專用站費用與頻率上限查清楚前不放)
DEFAULT_RELAYS = ("wss://relay.primal.net", "wss://nos.lol")
ENVELOPES = [1]
_RELAY_RE = re.compile(r"^wss://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$")
_LOCAL_RELAY_RE = re.compile(r"^ws://127\.0\.0\.1:[0-9]{1,5}$")    # 只給測試(本機假中繼站)
_NPUB_RE = re.compile(r"^%s$" % GF.NPUB_RE)
_SEAT_RE = re.compile(r"^[0-9a-z]{4,8}$")
_CABIN_RE = re.compile(r"^[0-9a-z]{1,4}$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_ROLE_RE = re.compile(r"^[0-9A-Za-z._-]{1,32}$")
MAX_SEATS = 200


class RosterError(ValueError):
    """名冊不合格;reason 是給人看的一句話(不含名冊原文)。"""


# ── 主人資料 ──

def _chat_home(env=None):
    env = os.environ if env is None else env
    explicit = env.get("AIB_CHAT_HOME")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "chat"


def profile_path(env=None):
    return _chat_home(env) / "profile.json"


def read_profile(env=None):
    try:
        rec = json.loads(profile_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        return None
    rec.setdefault("public", [])
    return rec


def _save_profile(rec, env=None):
    p = profile_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p.parent), 0o700)
    rec["updated_at"] = int(time.time())
    write_json(str(p), rec)
    os.chmod(str(p), 0o600)


# ── 好友(維護者 10-16 12:1x:加入好友之後才收、才有已讀;不然拒收)──
# ~/.ez2-aib/chat/friends.json(0600):{"v":1, "friends":[{"owner":"npub1…", "name":"Bob", "added_at":…}]}
# 以「主人」為單位:加了 Bob 的主人 npub,Bob 名冊上列出的每一席寄來都收。只在本機,不公開、不發到中繼站。
# 第 1 期是單方面的通訊錄(好友申請與同意是第 2 期):對方也要把你加進他的好友,你寄的才進得去。

LISTS = ("friends", "mine")       # friends = 好友;mine = 我自己其他機器的主人號碼(第 27.2 節,收到當本人)


def friends_path(env=None, kind="friends"):
    assert kind in LISTS
    return _chat_home(env) / (kind + ".json")


def read_friends(env=None, kind="friends"):
    try:
        rec = json.loads(friends_path(env, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rec, dict) or rec.get("v") != VERSION or not isinstance(rec.get("friends"), list):
        return []
    return [f for f in rec["friends"] if isinstance(f, dict) and _NPUB_RE.match(str(f.get("owner") or ""))]


def _save_friends(friends, env=None, kind="friends"):
    p = friends_path(env, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p.parent), 0o700)
    write_json(str(p), {"v": VERSION, "friends": friends, "updated_at": int(time.time())})
    os.chmod(str(p), 0o600)


def friend_owner(text):
    """`npub1…`(主人的)或全球位址 `<主人 npub>/<艙>/<席>` ⇒ 主人 npub;不合回 None。"""
    t = (text or "").strip()
    parsed = GF.parse_address(t)
    owner = (parsed or {}).get("owner") or (parsed or {}).get("seat_npub")
    if not owner:
        return None
    try:
        NK.npub_to_hex(owner)
    except ValueError:
        return None
    return owner


def add_friend(text, name=None, env=None, kind="friends", source=None):
    """加好友 / 加我的其他機器(同一位主人再加 = 改名字)。不合 ⇒ RosterError。回 (主人 npub, 是否新加)。
    source:"sent" = 寄過信自動加(第 27.3 節);手動加不帶。"""
    owner = friend_owner(text)
    if not owner:
        raise RosterError("bad_friend")
    me = (NK.read_map(env).get("owner") or {}).get("npub")
    if me and me == owner:
        raise RosterError("friend_self")
    clean = GF.clean_display_name(name) if name else None
    if name and not clean:
        raise RosterError("bad_name")
    fr = read_friends(env, kind)
    old = next((f for f in fr if f["owner"] == owner), None)
    if old:
        if clean:
            old["name"] = clean
        if source is None:
            old.pop("source", None)                 # 手動加過 ⇒ 不再算「自動加」
    else:
        rec = {"owner": owner, "name": clean, "added_at": int(time.time())}
        if source:
            rec["source"] = source
        fr.append(rec)
    _save_friends(fr, env, kind)
    return owner, old is None


def remove_friend(text, env=None, kind="friends"):
    owner = friend_owner(text)
    if not owner:
        raise RosterError("bad_friend")
    fr = read_friends(env, kind)
    left = [f for f in fr if f["owner"] != owner]
    if len(left) != len(fr):
        _save_friends(left, env, kind)
    return owner, len(left) != len(fr)


def is_friend(owner, env=None, kind="friends"):
    return bool(owner) and any(f["owner"] == owner for f in read_friends(env, kind))


def is_mine(owner, env=None):
    """寄件主人是我自己的另一台機器(第 27.2 節)⇒ 收件當本人(class own)。"""
    return is_friend(owner, env, "mine")


def clean_country(code):
    c = (code or "").strip().upper()
    return c if GF.COUNTRY_RE.match(c) else None


def set_profile(name=None, country=None, env=None):
    """改顯示名稱 / 國別。不合格 ⇒ RosterError(不寫)。回寫好的主人資料。"""
    rec = read_profile(env) or {"v": VERSION, "display_name": None, "country": None, "public": []}
    if name is not None:
        clean = GF.clean_display_name(name)
        if not clean:
            raise RosterError("bad_name")
        rec["display_name"] = clean
    if country is not None:
        c = clean_country(country)
        if not c:
            raise RosterError("bad_country")
        rec["country"] = c
    _save_profile(rec, env)
    return rec


def profile_ready(env=None):
    rec = read_profile(env)
    return bool(rec and rec.get("display_name") and rec.get("country"))


def set_public(seat5, on, env=None):
    if not _SEAT_RE.match(seat5 or ""):
        raise RosterError("bad_seat")
    rec = read_profile(env)
    if not rec:
        raise RosterError("no_profile")
    pub = [s for s in rec.get("public", []) if s != seat5]
    if on:
        pub.append(seat5)
    rec["public"] = sorted(set(pub))
    _save_profile(rec, env)
    return rec


MAX_RELAYS = 6     # 第 27.9 節:每多一站,每則信就多寄一份、多一個會限流或封人的對象


def _relay_ok(url):
    return bool(_RELAY_RE.match(url or "") or _LOCAL_RELAY_RE.match(url or ""))


def relays(env=None):
    """用哪些中繼站:AIB_CHAT_RELAYS(逗號分隔,測試用)優先;再來主人資料的 relays(aib chat relays add,
    第 27.9 節);都沒有 ⇒ 預設兩站。不合格的站略過,全不合格退回預設。"""
    env = os.environ if env is None else env
    raw = [x.strip() for x in (env.get("AIB_CHAT_RELAYS") or "").split(",") if x.strip()]
    if not raw:
        raw = list((read_profile(env) or {}).get("relays") or [])
    good = [r for r in raw if _relay_ok(r)][:MAX_RELAYS]
    return good or list(DEFAULT_RELAYS)


def set_relays(action, url=None, env=None):
    """aib chat relays add|remove|reset。自訂清單一開始 = 現在用的(預設兩站),加減都在它上面。
    不合格 ⇒ RosterError(bad_relay / too_many_relays / no_profile / last_relay)。回改好的清單。"""
    rec = read_profile(env)
    if not rec:
        raise RosterError("no_profile")
    cur = [r for r in (rec.get("relays") or list(DEFAULT_RELAYS)) if _relay_ok(r)]
    url = (url or "").strip().rstrip("/") if action != "reset" else None
    if action == "add":
        if not _relay_ok(url):
            raise RosterError("bad_relay")
        if url not in cur:
            if len(cur) >= MAX_RELAYS:
                raise RosterError("too_many_relays")
            cur.append(url)
    elif action == "remove":
        if url in cur and len(cur) == 1:
            raise RosterError("last_relay")
        cur = [r for r in cur if r != url]
    else:
        cur = list(DEFAULT_RELAYS)
    if cur == list(DEFAULT_RELAYS):
        rec.pop("relays", None)
    else:
        rec["relays"] = cur
    _save_profile(rec, env)
    return cur


def _aib_version():
    try:
        return (HERE.parent.parent / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _langs():
    try:
        import i18n_lib
        return [i18n_lib.resolve_locale()]
    except Exception:   # noqa: BLE001
        return ["zh-TW"]


# ── 組名冊 ──

def build(env=None, live=None, now=None):
    """組自己的名冊內容(還沒簽)。回 (內容 dict, 要發 10050 的席 [{seat5, key, npub}])。
    沒設主人資料 ⇒ RosterError("no_profile");沒有主人金鑰 ⇒ RosterError("no_owner")。
    只列:public 裡、有活席、而且有 active 金鑰的席。"""
    prof = read_profile(env)
    if not prof or not prof.get("display_name") or not prof.get("country"):
        raise RosterError("no_profile")
    m = NK.read_map(env)
    if not m.get("owner"):
        raise RosterError("no_owner")
    live = NK.live_seats(env=env) if live is None else live
    meta = {s["seat5"]: s for s in live}
    pub = set(prof.get("public") or [])
    cabins, dm_seats = {}, []
    for e in sorted(m["seats"], key=lambda x: (x.get("cabin") or "", x["seat5"])):
        if e.get("status") != "active" or e["seat5"] not in pub or e["seat5"] not in meta:
            continue
        s = meta[e["seat5"]]
        entry = {"seat": e["seat5"], "npub": e["npub"], "accepts": ["dm"], "langs": _langs(), "public": True}
        name = GF.clean_display_name(s.get("name") or "")
        if name:
            entry["name"] = name
        if isinstance(s.get("role"), str) and _ROLE_RE.match(s["role"]):
            entry["role"] = s["role"]
        cabins.setdefault(e.get("cabin") or "main", []).append(entry)
        dm_seats.append({"seat5": e["seat5"], "key": e["key"], "npub": e["npub"]})
    content = {"v": VERSION, "owner": m["owner"]["npub"], "display_name": prof["display_name"], "country": prof["country"],
               "cabins": [{"cabin": c, "seats": seats} for c, seats in sorted(cabins.items())],
               "dm_relays": relays(env), "aib_version": _aib_version(), "envelope": list(ENVELOPES),
               "updated_at": int(time.time() if now is None else now)}
    return content, dm_seats


def empty(owner_npub, env=None, now=None):
    """關閉線上對話時發的空名冊(第 20.4 節):沒有艙、沒有收信站。"""
    return {"v": VERSION, "owner": owner_npub, "display_name": None, "country": None, "cabins": [], "dm_relays": [],
            "aib_version": _aib_version(), "envelope": list(ENVELOPES), "updated_at": int(time.time() if now is None else now)}


def content_hash(content):
    """不含 updated_at 的雜湊:內容沒變就不重發。"""
    body = {k: v for k, v in content.items() if k != "updated_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def outgoing_path(root):
    return Path(root) / "roster" / "outgoing.json"


def published_path(root):
    return Path(root) / "roster" / "published.json"


def write_outgoing(root, content, dm_seats):
    """給 venv 那一半發的檔。內容沒變(雜湊同)就不重寫。回 (雜湊, 有沒有改)。"""
    d = Path(root) / "roster"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    h = content_hash(content)
    try:
        cur = json.loads(outgoing_path(root).read_text(encoding="utf-8"))
        if cur.get("hash") == h and cur.get("dm_seats") == dm_seats:
            return h, False
    except (OSError, ValueError, AttributeError):
        pass
    write_json(str(outgoing_path(root)), {"v": VERSION, "hash": h, "content": content, "dm_seats": dm_seats,
                                          "built_at": int(time.time())})
    os.chmod(str(outgoing_path(root)), 0o600)
    return h, True


def published_hash(root):
    try:
        return json.loads(published_path(root).read_text(encoding="utf-8")).get("hash")
    except (OSError, ValueError, AttributeError):
        return None


# ── 驗別人的名冊 ──

def parse(content, signer_npub):
    """別人的名冊內容(已由 nostr-sdk 驗過簽章)⇒ 驗過的 dict;任何一處不對 ⇒ RosterError(一個字都不寄)。
    signer_npub = 簽這份名冊的金鑰;第 1 期 owner 一定要等於它(名冊不能替別人的金鑰作主)。"""
    try:
        rec = json.loads(content) if isinstance(content, (str, bytes)) else content
    except ValueError:
        raise RosterError("not_json")
    if not isinstance(rec, dict) or rec.get("v") != VERSION:
        raise RosterError("bad_version")
    if rec.get("owner") != signer_npub or not _NPUB_RE.match(signer_npub or ""):
        raise RosterError("owner_mismatch")
    name = rec.get("display_name")
    if name is not None and GF.clean_display_name(name) != name:
        rec["display_name"] = None                                 # 第 5.8 節:不合的名字退回人格名,不整份拒
    if rec.get("country") is not None and not GF.COUNTRY_RE.match(str(rec["country"])):
        rec["country"] = None
    if not isinstance(rec.get("cabins"), list) or not isinstance(rec.get("dm_relays"), list):
        raise RosterError("bad_shape")
    if not all(isinstance(r, str) and (_RELAY_RE.match(r) or _LOCAL_RELAY_RE.match(r)) for r in rec["dm_relays"]):
        raise RosterError("bad_relay")
    env_ok = rec.get("envelope")
    if not isinstance(env_ok, list) or not all(isinstance(x, int) for x in env_ok):
        raise RosterError("bad_envelope")
    n, seen = 0, set()
    for cab in rec["cabins"]:
        if not isinstance(cab, dict) or not (_CABIN_RE.match(str(cab.get("cabin"))) or cab.get("cabin") == "main"):
            raise RosterError("bad_cabin")
        if not isinstance(cab.get("seats"), list):
            raise RosterError("bad_shape")
        for s in cab["seats"]:
            n += 1
            if n > MAX_SEATS or not isinstance(s, dict):
                raise RosterError("too_many_seats" if n > MAX_SEATS else "bad_seat")
            if not _SEAT_RE.match(str(s.get("seat"))) or not _NPUB_RE.match(str(s.get("npub"))):
                raise RosterError("bad_seat")
            key = (cab["cabin"], s["seat"])
            if key in seen:
                raise RosterError("duplicate_seat")
            seen.add(key)
            if s.get("name") is not None and GF.clean_display_name(s["name"]) != s["name"]:
                s["name"] = None
            if not isinstance(s.get("langs", []), list) or not all(isinstance(x, str) and _LANG_RE.match(x) for x in s.get("langs", [])):
                s["langs"] = []
    return rec


def find_seat(rec, cabin, seat):
    """驗過的名冊裡找 <艙>/<席>;找不到回 None(寄件端照 rc 2「對方沒有這一席」)。"""
    for cab in rec.get("cabins") or []:
        if cab.get("cabin") == cabin:
            for s in cab.get("seats") or []:
                if s.get("seat") == seat:
                    return s
    return None


def common_envelope(rec):
    """雙方都支援的最高信封版本;沒有共同版本回 None(第 13 節:rc 2)。"""
    both = sorted(set(ENVELOPES) & set(rec.get("envelope") or []))
    return both[-1] if both else None
