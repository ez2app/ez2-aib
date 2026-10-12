#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長室通訊錄(分期 A,the maintainer 10-11 03:5x「每席 AI 都能選國別/IP/席位/主機/綁定社群找到在線的用戶或 AI」)。

四個來源合成一份「聯絡人」:本機席位、艦隊名冊探到的別台席位、廣場名片(aib plaza who)、廣場名片底下的席位報到。
同一個人(廣場名片的 dm_seat 指到某一席)折成一筆,不讓名單爆。

在線狀態不信自報:同艙/同艦隊的席位用量到的閒置秒數和主機探測;廣場上只有名片的,狀態一律「不明」❓,
只給「最後出現」時間(他的席位報到說 online 也只當自報,畫面標 ❓)。

好友 / 分組 / 封鎖 / 靜音 / 對話次數存本機 state/captain-room/contacts.json,不進 git。
純函式 + 一個小存檔;不碰 tmux、不寄信、不打網路。IP 只給本機這一頁(同擁有者)看:伺服器沒有任何一條路把通訊錄給別台;
以後若要給別台,那條路要自己把 ip 拿掉。
"""
import json
import os
import re
import tempfile
import threading
import time

_WRITE_LOCK = threading.RLock()   # 讀-改-寫一次一個(10-11 審查:寄信 90 秒期間舊資料會蓋回去;兩個同時存檔會把檔寫壞)

PRESENCE = ("online", "idle", "away", "busy", "decide", "handoff", "offline", "asleep", "unknown")
IDLE_S, AWAY_S = 300, 1800            # 閒置 5 分鐘 = 閒置;30 分鐘 = 離開
GROUPS_DEFAULT = ("friend", "fleet", "outside")   # 朋友 / 同艦隊 / 外部;畫面用 i18n 顯示
KEY_RE = re.compile(r"^(seat:[0-9a-z]{4}/[0-9a-z]{5}|plaza:@[A-Za-z0-9._/-]{1,65})$")
REGION_CC_RE = re.compile(r"^[A-Za-z]{2}$")


def presence_of_seat(s, host_ok=True, host_kind=None):
    """量到的狀態 → 一個字:主機探不到 ⇒ asleep/offline;交接完 ⇒ handoff;等決定/忙碌 ⇒ 那個;再看閒置秒數"""
    if not host_ok:
        return "asleep" if host_kind in ("unreachable", "timeout") else "offline"
    if s.get("handoff_done"):
        return "handoff"
    st = s.get("state") or ((s.get("now") or {}).get("state") if isinstance(s.get("now"), dict) else None)
    if st == "decide":
        return "decide"
    if st == "busy":
        return "busy"
    idle = s.get("idle_s")
    if idle is None:
        return "online"
    if idle < IDLE_S:
        return "online"
    if idle < AWAY_S:
        return "idle"
    return "away"


def _country(region):
    return region.lower() if isinstance(region, str) and REGION_CC_RE.match(region) else None


def _host_of_ssh(ssh):
    return str(ssh or "").split("@", 1)[-1].split(":", 1)[0] or None


def build_contacts(local_seats, cabin, hosts, plaza, my_ip=None, my_country=None, now=None):
    """回 {key: contact}。key = seat:<艙>/<席碼> 或 plaza:@名字。
    local_seats:/api/state 的 seats;hosts:/api/state 的 hosts[](本機第一);plaza:/api/state 的 plaza"""
    now = now or time.time()
    out = {}
    local_host = (hosts[0].get("name") if hosts else None) or "local"

    def seat_row(s, host, cab, ip, country, host_ok=True, host_kind=None, local=False):
        bot = s.get("bot") if isinstance(s.get("bot"), dict) else None
        persona = s.get("persona")
        return {"key": f"seat:{cab}/{s.get('code')}", "kind": "seat", "name": "@" + str(s.get("code")), "agent": None,
                "host": host, "cabin": cab, "seat": f"{cab}/{s.get('code')}", "ip": ip, "country": country,
                "provider": s.get("provider"), "role": s.get("role"),
                "persona": persona.get("name") if isinstance(persona, dict) else persona,
                "social": (bot or {}).get("bound_title") or None, "social_kind": s.get("provider") if bot else None,
                "presence": presence_of_seat(s, host_ok, host_kind), "measured": True,
                "idle_s": s.get("idle_s"), "ctx": s.get("ctx"), "unread": s.get("unread"),
                "item": (s.get("now") or {}).get("item") if isinstance(s.get("now"), dict) else s.get("item"),
                "local": local, "owner": "me", "skills": [], "region": None, "last_seen": None}

    for s in local_seats or []:
        if s.get("code"):
            r = seat_row(s, local_host, cabin, my_ip, my_country, local=True)
            out[r["key"]] = r
    for h in (hosts or [])[1:]:
        cab = h.get("cabin") or "????"
        for s in h.get("seats") or []:
            if not s.get("code"):
                continue
            r = seat_row(s, h.get("name"), cab, _host_of_ssh(h.get("ssh")), my_country, host_ok=bool(h.get("ok")), host_kind=h.get("kind"))
            # last_seen 一律 ISO 字串(廣場名片也是),頁面同一種畫法
            r["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - h["age_s"])) if isinstance(h.get("age_s"), (int, float)) else None
            out[r["key"]] = r
    for a in (plaza or {}).get("agents") or []:
        name = a.get("agent")
        if not name:
            continue
        dm_seat = a.get("dm_seat")
        linked = out.get(f"seat:{dm_seat}") if dm_seat else None
        if linked is not None:               # 同一個人:名片折進那一席
            linked.update(agent=name, skills=list(a.get("skills") or []), region=a.get("region"),
                          country=linked.get("country") or _country(a.get("region")), plaza_local=bool(a.get("local")))
            continue
        # 分期 B:廣場上的在線 = 他的席位報到(心跳)在 TTL 內;還是自報,但有時效,畫面標「自報 N 分鐘前」
        hb = a.get("hb_age_s")
        presence = "unknown"
        if isinstance(hb, (int, float)):
            presence = "online" if hb <= 900 else "idle" if hb <= 1800 else "away"
        out[f"plaza:{name}"] = {"key": f"plaza:{name}", "kind": "agent", "name": name, "agent": name, "host": None, "cabin": None,
                                "seat": dm_seat, "ip": None, "country": _country(a.get("region")), "provider": None, "role": None,
                                "persona": None, "social": None, "social_kind": None,
                                "presence": presence, "measured": False, "self_online": int(a.get("online") or 0), "hb_age_s": hb,
                                "idle_s": None, "ctx": None, "unread": None, "item": None, "local": bool(a.get("local")),
                                "owner": a.get("owner"), "skills": list(a.get("skills") or []), "region": a.get("region"),
                                "last_seen": a.get("at"), "fp": a.get("fp"), "dm_verified": bool(a.get("dm_verified"))}
    for c in out.values():
        if c["kind"] == "seat" and c.get("agent"):
            c.setdefault("fp", next((a.get("fp") for a in (plaza or {}).get("agents") or [] if a.get("agent") == c["agent"]), None))
    return out


def verify_state(contact, pinned_fp):
    """公鑰指紋互驗(TOFU):沒釘 = unverified;釘的跟現在一樣 = verified;不一樣 = changed(⚠ 可能被冒名或換了鑰匙)"""
    fp = contact.get("fp")
    if not fp:
        return None
    if not pinned_fp:
        return "unverified"
    return "verified" if pinned_fp == fp else "changed"


def _hay(c):
    return " ".join(str(v) for v in (c.get("name"), c.get("agent"), c.get("host"), c.get("cabin"), c.get("seat"), c.get("ip"),
                                     c.get("country"), c.get("provider"), c.get("role"), c.get("persona"), c.get("social"),
                                     c.get("region"), c.get("owner"), " ".join(c.get("skills") or [])) if v).lower()


ONLINE_SET = ("online", "busy", "decide", "idle")
SORT_ORDER = ("online", "busy", "decide", "idle", "away", "handoff", "unknown", "offline", "asleep")   # 名單排序:活的在前、不明在中、死的在後


def filter_contacts(contacts, q="", online_only=False, group=None, include_blocked=False):
    """搜尋:一個字串比對 名字/主機/艙/席位/IP/國別/供應商/角色/人格/綁的社群/技能;可只看在線、只看某一組"""
    words = [w for w in str(q or "").lower().split() if w]
    out = []
    for c in contacts.values():
        if c.get("blocked") and not include_blocked:
            continue
        if online_only and c.get("presence") not in ONLINE_SET:
            continue
        if group and c.get("group") != group:
            continue
        hay = _hay(c)
        if all(w in hay for w in words):
            out.append(c)
    order = {p: i for i, p in enumerate(SORT_ORDER)}
    out.sort(key=lambda c: (0 if c.get("friend") else 1, order.get(c.get("presence"), 9), -(c.get("talks") or 0), c.get("name") or ""))
    return out


class ContactStore:
    """好友 / 分組 / 封鎖 / 靜音 / 對話次數;一個 JSON,原子寫。
    檔在卻讀不了(壞 JSON、權限)⇒ broken:唯讀、不覆寫(10-11 審查:之前讀失敗會用空的把好友/封鎖/釘的指紋全蓋掉)"""

    def __init__(self, path):
        self.path = path
        self._load()

    def _load(self):
        self.broken = None
        self.data = {"friends": {}, "blocked": {}, "muted": {}, "talks": {}, "notes": {}, "groups": list(GROUPS_DEFAULT), "seeded": False,
                     "fingerprints": {}}   # key → 釘住的公鑰指紋(分期 B 互驗)
        path = self.path
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            if not isinstance(d, dict):
                raise ValueError("not a dict")
            for k in self.data:
                if k in d and isinstance(d[k], type(self.data[k])):   # 型別不對的欄位用預設值(C4 審查:dict 欄被寫成 list 會讓 entry() 炸到 /api/state)
                    self.data[k] = d[k]
        except FileNotFoundError:
            pass
        except Exception as e:
            self.broken = f"{type(e).__name__}: {str(e)[:80]}"

    def save(self):
        if self.broken:
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".contacts-", suffix=".tmp", dir=os.path.dirname(self.path))   # 每次不同的暫存檔名
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True

    def set(self, key, friend=None, group=None, block=None, mute=None, note=None, pin_fp=None):
        if not KEY_RE.match(key or ""):
            return {"ok": False, "err": "bad-key"}
        with _WRITE_LOCK:
            self._load()   # 以磁碟上最新的為準再改(別的請求可能剛改過)
            return self._set(key, friend, group, block, mute, note, pin_fp)

    def _set(self, key, friend, group, block, mute, note, pin_fp):
        if self.broken:
            return {"ok": False, "err": "store-unreadable", "detail": self.broken}
        d = self.data
        if pin_fp is not None:   # "" = 取消釘;否則釘這個指紋(艦長在別的管道比對過才按)
            if pin_fp:
                if not re.fullmatch(r"[0-9a-f]{16}", pin_fp):
                    return {"ok": False, "err": "bad-fp"}
                d.setdefault("fingerprints", {})[key] = pin_fp
            else:
                d.setdefault("fingerprints", {}).pop(key, None)
        if friend is True:
            d["friends"].setdefault(key, {"group": "friend", "added": int(time.time())})
        elif friend is False:
            d["friends"].pop(key, None)
        if group is not None:
            if group:
                if group not in d["groups"]:
                    if not re.fullmatch(r"[A-Za-z0-9_一-鿿-]{1,16}", group):
                        return {"ok": False, "err": "bad-group"}
                    d["groups"].append(group)
                d["friends"].setdefault(key, {"added": int(time.time())})["group"] = group
            elif key in d["friends"]:   # 「沒分組」:好友留著、只拿掉組;不是好友就什麼都不做(10-11 自審:之前會把他變成「朋友」組的好友)
                d["friends"][key]["group"] = None
        if block is True:
            d["blocked"][key] = int(time.time()); d["friends"].pop(key, None)
        elif block is False:
            d["blocked"].pop(key, None)
        if mute is True:
            d["muted"][key] = int(time.time())
        elif mute is False:
            d["muted"].pop(key, None)
        if note is not None:
            if note:
                d["notes"][key] = str(note)[:200]
            else:
                d["notes"].pop(key, None)
        self.save()
        return {"ok": True, "entry": self.entry(key)}

    def entry(self, key):
        d = self.data
        f = d["friends"].get(key)
        return {"friend": f is not None, "group": (f or {}).get("group"), "blocked": key in d["blocked"], "muted": key in d["muted"],
                "talks": int(d["talks"].get(key) or 0), "note": d["notes"].get(key), "pinned_fp": (d.get("fingerprints") or {}).get(key)}

    def members(self, group):
        """這一組的所有 key(群組發送用)"""
        return sorted(k for k, f in self.data["friends"].items() if (f or {}).get("group") == group and k not in self.data["blocked"])

    def is_blocked(self, key):
        return key in self.data["blocked"]

    def bump_talk(self, key, n=1):
        if KEY_RE.match(key or ""):
            with _WRITE_LOCK:
                self._load()
                if self.broken:
                    return
                self.data["talks"][key] = int(self.data["talks"].get(key) or 0) + n
                self.save()

    def seed_from_actions(self, actions_log):
        """第一次:從艦長室的動作紀錄(dm / plaza-dm)把過去的對話次數算進來;只做一次"""
        with _WRITE_LOCK:
            self._load()
            if self.data.get("seeded") or self.broken:
                return 0
            return self._seed(actions_log)

    def _seed(self, actions_log):
        n = 0
        try:
            with open(actions_log, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("kind") == "dm":
                        cab = e.get("cabin")
                        for c in e.get("to") or []:
                            key = f"seat:{cab}/{c}" if cab else None
                            if key and KEY_RE.match(key):
                                self.data["talks"][key] = int(self.data["talks"].get(key) or 0) + 1; n += 1
                    elif e.get("kind") == "plaza-dm" and e.get("to"):
                        key = f"plaza:{e['to']}"
                        if KEY_RE.match(key):
                            self.data["talks"][key] = int(self.data["talks"].get(key) or 0) + 1; n += 1
        except OSError:
            pass
        self.data["seeded"] = True
        self.save()
        return n

    def apply(self, contacts):
        for key, c in contacts.items():
            c.update(self.entry(key))
            c["verify"] = verify_state(c, c.get("pinned_fp"))
        return contacts

    def top(self, contacts, n=10):
        """聊過最多的前 n 名(有對話次數的才算),封鎖的不列"""
        rows = [c for c in contacts.values() if (c.get("talks") or 0) > 0 and not c.get("blocked")]
        rows.sort(key=lambda c: (-(c.get("talks") or 0), c.get("name") or ""))
        return rows[:n]


_store_cache = {}


def cached_store(path):
    """同一個檔同一個 mtime 只解析一次(/api/state 每 5 秒讀一次封鎖名單);改了就重讀。寫入請用新的 ContactStore,不要寫這份"""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    hit = _store_cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    store = ContactStore(path)
    _store_cache[path] = (sig, store)
    return store
