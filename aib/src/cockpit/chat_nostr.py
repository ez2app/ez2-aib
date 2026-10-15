#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球對談轉接程式連 Nostr 的那一半(開發文件第 5.2–5.6、20.3 節;第 1 期 1.3)。

**只在 ~/.ez2-aib/chat/venv 的 python 底下跑**(import nostr_sdk==0.45.1);核心不 import 這一支。
chat_bridge 用子行程叫它:
  publish                  讀 <bridge root>/roster/outgoing.json,主人金鑰簽 kind 30078(d=aib-roster)、
                           各公開席用自己的金鑰簽 kind 10050(relay 標籤 = 名冊 dm_relays),發到 dm_relays;
                           寫 roster/published.json。名冊與每一則 10050 都至少 1 站收下才算成功(rc 0),否則 rc 2。
  publish-empty            關閉全球對談(第 20.4 節):空名冊 + 上次發過的各席 10050 換成沒有站(撤下)。
  fetch <npub>/<艙>/<席>   抓對方主人最新的名冊 → nostr-sdk 驗簽 → chat_roster.parse 驗內容 → 找席 → 再抓那席的 10050。
                           任何一步不對 ⇒ rc 2 + 原因代碼,印 JSON(不寄任何東西)。
私鑰:只在這個行程裡從 nostr_keys.load_secret 解開、交給 Keys.parse,不印、不落檔、例外只記種類。
"""
import argparse
import asyncio
import datetime
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import nostr_sdk as N  # noqa: E402  只有 venv 裡有;核心沒有這一行
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from atomic_json import write_json  # noqa: E402

TIMEOUT = float(os.environ.get("AIB_CHAT_NET_TIMEOUT") or 8)


def _keys(stem):
    secret = NK.load_secret(stem)
    try:
        return N.Keys.parse(secret)
    finally:
        secret = None


def _sign_roster(keys, content):
    tag = N.Tag.identifier(CR.ROSTER_D)
    return N.EventBuilder(N.Kind(CR.KIND_ROSTER), json.dumps(content, ensure_ascii=False, sort_keys=True)).tags([tag]).finalize(keys)


def _sign_dm_relays(keys, relays):
    return N.EventBuilder(N.Kind(CR.KIND_DM_RELAYS), "").tags([N.Tag.parse(["relay", r]) for r in relays]).finalize(keys)


async def _client(relays):
    c = N.Client()
    for r in relays:
        await c.add_relay(N.RelayUrl.parse(r))
    await c.connect()
    return c


async def _send_all(relays, events):
    """→ {事件 id: [收下的站]}。"""
    c = await _client(relays)
    out = {}
    try:
        for ev in events:
            try:
                res = await c.send_event(ev)
                out[ev.id().to_hex()] = sorted(str(u) for u in res.success)
            except Exception:   # noqa: BLE001  一則失敗不擋別則
                out[ev.id().to_hex()] = []
    finally:
        await c.shutdown()
    return out


def _publish(root, content, dm_seats, relays, tag):
    owner = _keys("owner")
    roster_ev = _sign_roster(owner, content)
    seat_evs = [(s, _sign_dm_relays(_keys(s["key"]), relays)) for s in dm_seats]
    acks = asyncio.run(_send_all(relays, [roster_ev] + [ev for _s, ev in seat_evs]))
    ok_roster = acks.get(roster_ev.id().to_hex()) or []
    ok_seats = {s["seat5"]: acks.get(ev.id().to_hex()) or [] for s, ev in seat_evs}
    rec = {"v": 1, "hash": CR.content_hash(content) if tag == "publish" else "empty", "kind": tag, "at": int(time.time()),
           "roster_id": roster_ev.id().to_hex(), "roster_ok": ok_roster, "dm_relays_ok": ok_seats,
           "dm_seats": dm_seats}
    ok = bool(ok_roster) and all(ok_seats.values())
    if not ok:
        rec["hash"] = None                     # 沒全發成功 ⇒ 不記雜湊,轉接程式下一輪再發
    Path(CR.published_path(root)).parent.mkdir(parents=True, exist_ok=True)
    write_json(str(CR.published_path(root)), rec)
    os.chmod(str(CR.published_path(root)), 0o600)
    return 0 if ok else 2, rec


def cmd_publish(root):
    out = json.loads(Path(CR.outgoing_path(root)).read_text(encoding="utf-8"))
    content = out["content"]
    return _publish(root, content, out.get("dm_seats") or [], content.get("dm_relays") or CR.relays(), "publish")


def cmd_publish_empty(root):
    try:
        prev = json.loads(Path(CR.published_path(root)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    try:
        last_relays = json.loads(Path(CR.outgoing_path(root)).read_text(encoding="utf-8"))["content"]["dm_relays"]
    except (OSError, ValueError, KeyError, TypeError):
        last_relays = CR.relays()
    owner_npub = NK.read_map().get("owner", {}).get("npub")
    content = CR.empty(owner_npub)
    # 撤下 = 各席 10050 換成沒有站;發到上次公告的那些站,看過舊清單的人才會收到新的
    owner = _keys("owner")
    roster_ev = _sign_roster(owner, content)
    seat_evs = [(s, _sign_dm_relays(_keys(s["key"]), [])) for s in (prev.get("dm_seats") or [])]
    acks = asyncio.run(_send_all(last_relays, [roster_ev] + [ev for _s, ev in seat_evs]))
    rec = {"v": 1, "hash": "empty", "kind": "publish-empty", "at": int(time.time()), "roster_id": roster_ev.id().to_hex(),
           "roster_ok": acks.get(roster_ev.id().to_hex()) or [], "dm_seats": []}
    write_json(str(CR.published_path(root)), rec)
    os.chmod(str(CR.published_path(root)), 0o600)
    return (0 if rec["roster_ok"] else 2), rec


async def _fetch_latest(relays, flt):
    c = await _client(relays)
    try:
        evs = await c.fetch_events(N.ReqTarget.auto([flt]), datetime.timedelta(seconds=TIMEOUT))
    finally:
        await c.shutdown()
    # 驗簽(含 id);驗不過的當沒看到。nostr-sdk 0.45.1 收事件時自己也會驗(10-16 實測簽章錯的根本到不了這裡),
    #   這一行是第二道:換 SDK 版本或換抓法時還在。
    evs = [e for e in evs if e.verify()]
    return max(evs, key=lambda e: e.created_at().as_secs()) if evs else None


async def resolve(addr, relays):
    """全球位址 → (rc, 結果)。<主人 npub>/<艙>/<席>:抓名冊 → 驗簽 → parse → 找席 → 抓那席 10050;
    npub1…(直接指定席金鑰):只抓那個 npub 的 10050。任何一步不對 ⇒ rc 2 + 原因代碼(不寄任何東西)。"""
    parsed = GF.parse_address(addr)
    if parsed is None:
        return 2, {"ok": False, "why": "bad_address"}
    out = {"ok": True}
    if "seat_npub" in parsed:
        seat_npub, look_in = parsed["seat_npub"], relays
        out.update(seat={"npub": seat_npub}, envelope=CR.ENVELOPES[-1])
    else:
        owner_npub, cabin, seat = parsed["owner"], parsed["cabin"], parsed["seat"]
        flt = N.Filter().author(N.PublicKey.parse(owner_npub)).kind(N.Kind(CR.KIND_ROSTER)).identifier(CR.ROSTER_D)
        try:
            ev = await _fetch_latest(relays, flt)
        except Exception as err:   # noqa: BLE001
            return 2, {"ok": False, "why": "relay_error", "err": type(err).__name__}
        if ev is None:
            return 2, {"ok": False, "why": "no_roster"}            # 對方還沒開全球對談(第 13 節)
        if ev.author().to_bech32() != owner_npub:
            return 2, {"ok": False, "why": "wrong_author"}
        try:
            rec = CR.parse(ev.content(), owner_npub)
        except CR.RosterError as err:
            return 2, {"ok": False, "why": "bad_roster", "detail": str(err)}
        s = CR.find_seat(rec, cabin, seat)
        if s is None:
            return 2, {"ok": False, "why": "no_seat"}
        if CR.common_envelope(rec) is None:
            return 2, {"ok": False, "why": "no_common_envelope"}
        seat_npub, look_in = s["npub"], (rec["dm_relays"] or relays)
        out.update(owner=owner_npub, display_name=rec.get("display_name"), country=rec.get("country"), cabin=cabin,
                   seat=s, envelope=CR.common_envelope(rec), roster_at=ev.created_at().as_secs())
    flt2 = N.Filter().author(N.PublicKey.parse(seat_npub)).kind(N.Kind(CR.KIND_DM_RELAYS))
    try:
        rel_ev = await _fetch_latest(look_in, flt2)
    except Exception as err:   # noqa: BLE001
        return 2, {"ok": False, "why": "relay_error", "err": type(err).__name__}
    vecs = [t.to_vec() for t in (rel_ev.tags() if rel_ev else [])]
    inbox = [v[1] for v in vecs if len(v) >= 2 and v[0] == "relay"]
    if not inbox:
        return 2, {"ok": False, "why": "no_dm_relays"}         # NIP-17:沒有 10050 就不該寄
    out["dm_relays"] = inbox
    return 0, out


def cmd_fetch(addr, relays):
    return asyncio.run(resolve(addr, relays))


# ── 常駐寄件(serve;第 7.1 節步驟 5–8)──
POLL = float(os.environ.get("AIB_CHAT_POLL") or 0.2)
HB_EVERY = 10                                               # 秒;dm_global_files.HEARTBEAT_MAX_AGE = 30
RETRY_GAP = float(os.environ.get("AIB_CHAT_RETRY_GAP") or 10)
MAX_TRIES = 3                                               # 第 7.1 節第 8 步:全部中繼站失敗 3 次 ⇒ 轉人工(failed)
RESOLVE_TTL = 600                                           # 第 5.2 節:名冊快取 10 分鐘
TRANSIENT = ("relay_error",)


def _local_tz():
    tz = os.environ.get("TZ") or ""
    if not tz:
        try:
            link = os.readlink("/etc/localtime")
            tz = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
        except OSError:
            tz = ""
    return tz if GF.TZ_RE.match(tz or "") else None


def _rumor_tags(rec, from_addr):
    t = [["aib-v", "1"], ["aib-type", "dm"], ["aib-msgid", rec["msgid"]], ["aib-from", from_addr], ["aib-to", rec["to"]]]
    for key, tag in (("thread", "aib-thread"), ("kind", "aib-kind"), ("priority", "aib-priority"),
                     ("reply_to", "aib-reply-to"), ("lang", "aib-lang")):
        if rec.get(key):
            t.append([tag, str(rec[key])])
    if rec.get("expires"):
        t.append(["aib-expires", str(rec["expires"])])
    tz = _local_tz()
    if tz:
        t.append(["aib-tz", tz])
    return [N.Tag.parse(x) for x in t]


def _receipt(root, msgid, stage, detail=None):
    r = {"v": 1, "at": int(time.time()), "stage": stage}
    if detail:
        r["detail"] = detail
    GF.append_receipt(root, msgid, r)


class Sender(object):
    def __init__(self, root):
        self.root = Path(root)
        self.cache = {}

    async def _resolve(self, addr):
        hit = self.cache.get(addr)
        if hit and time.time() - hit[0] < RESOLVE_TTL:
            return hit[1]
        res = await resolve(addr, CR.relays())
        if res[0] == 0:
            self.cache[addr] = (time.time(), res)
        return res

    def _done(self, msgid):
        for ext in (".json", ".try"):
            try:
                (self.root / "sending" / (msgid + ext)).unlink()
            except FileNotFoundError:
                pass

    def _retry_later(self, msgid, why):
        tp = self.root / "sending" / (msgid + ".try")
        try:
            n = json.loads(tp.read_text()).get("n", 0) + 1
        except (OSError, ValueError, AttributeError):
            n = 1
        if n >= MAX_TRIES:
            _receipt(self.root, msgid, "failed", why)
            self._done(msgid)
            return
        write_json(str(tp), {"n": n, "next": time.time() + RETRY_GAP, "why": why})

    async def handle(self, msgid):
        sp = self.root / "sending" / (msgid + ".json")
        try:
            rec = GF.check_outbox(json.loads(sp.read_text(encoding="utf-8")))
        except (OSError, ValueError, GF.ContractError):
            self._done(msgid)
            return
        if rec["op"] == "resolve":
            rc, res = await self._resolve(rec["to"])
            if rc == 0:
                _receipt(self.root, msgid, "resolved", json.dumps({"npub": res["seat"]["npub"], "dm_relays": res["dm_relays"],
                                                                   "display_name": res.get("display_name")}, ensure_ascii=False))
            else:
                _receipt(self.root, msgid, "failed", res.get("why"))
            self._done(msgid)
            return
        if rec.get("expires") and rec["expires"] < time.time():
            _receipt(self.root, msgid, "expired")
            self._done(msgid)
            return
        frm = rec["from"]
        if not frm.get("attested"):
            # 寄件人身分沒量到 ⇒ 不拿那一席的金鑰代簽(簽了就等於證明是那一席寄的)
            _receipt(self.root, msgid, "refused", "unattested")
            self._done(msgid)
            return
        m = NK.read_map()
        entry = next((e for e in m["seats"] if e.get("status") == "active" and e["seat5"] == frm["seat"]), None)
        if entry is None or not m.get("owner"):
            _receipt(self.root, msgid, "refused", "no_sender_key")
            self._done(msgid)
            return
        rc, res = await self._resolve(rec["to"])
        if rc != 0:
            if res.get("why") in TRANSIENT:
                self._retry_later(msgid, res.get("why"))
            else:
                _receipt(self.root, msgid, "refused", res.get("why"))
                self._done(msgid)
            return
        await self._ensure_dm_relays(entry)
        keys = _keys(entry["key"])
        from_addr = "%s/%s/%s" % (m["owner"]["npub"], frm["cabin"], frm["seat"])
        tags = _rumor_tags(rec, from_addr)
        receiver = N.PublicKey.parse(res["seat"]["npub"])
        gw = N.nip17_make_private_msg(keys, receiver, rec["content"], rumor_extra_tags=tags)
        # NIP-17:另包一份給自己(手機端才看得到雙方完整對話),發到自己的收信站
        mine = N.nip17_make_private_msg(keys, keys.public_key(), rec["content"], rumor_extra_tags=tags)
        keys = None
        target, own = res["dm_relays"], CR.relays()
        try:
            ok = await _send_to(target, own, gw, mine)
        except Exception as err:   # noqa: BLE001
            self._retry_later(msgid, type(err).__name__)
            return
        if ok:
            _receipt(self.root, msgid, "relayed", "%d" % len(ok))      # 第 7.1 節第 6 步:1 站成功就算
            # 寄出帳:對方回條回來時,只收「我寄給的那個 npub」簽的(防有人冒充對方亂回條)
            (self.root / "sent").mkdir(exist_ok=True)
            write_json(str(self.root / "sent" / (msgid + ".json")), {"v": 1, "to": res["seat"]["npub"], "at": int(time.time())})
            self._done(msgid)
        else:
            self._retry_later(msgid, "relays")

    async def _ensure_dm_relays(self, entry):
        """寄件席沒列名冊也要有 10050,對方才回得了回條與回信(NIP-17:沒有 10050 就不寄)。
        10050 只公開「這把公鑰在哪些站收信」,不進名冊、不帶名字角色;只有收過它信的人知道它的 npub。每席發一次。"""
        p = self.root / "dm-relays-published.json"
        try:
            done = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            done = {}
        own = CR.relays()
        if done.get(entry["npub"]) == own:
            return
        keys = _keys(entry["key"])
        ev = _sign_dm_relays(keys, own)
        keys = None
        try:
            c = await _client(own)
            try:
                res = await c.send_event(ev)
            finally:
                await c.shutdown()
        except Exception:   # noqa: BLE001  發不出去下次再試;不擋這一封
            return
        if res.success:
            done[entry["npub"]] = own
            write_json(str(p), done)

    def due_retries(self):
        out = []
        for tp in sorted((self.root / "sending").glob("*.try")):
            try:
                if json.loads(tp.read_text()).get("next", 0) <= time.time():
                    out.append(tp.stem)
            except (OSError, ValueError, AttributeError):
                continue
        return out


async def _send_to(target, own, gw, mine):
    """gw 發到對方收信站、mine 發到自己的;回收下 gw 的站。"""
    c = await _client(sorted(set(target) | set(own)))
    try:
        res = await c.send_event(gw, N.SendEventTarget.to([N.RelayUrl.parse(r) for r in target]))
        ok = sorted(str(u) for u in res.success)
        try:
            await c.send_event(mine, N.SendEventTarget.to([N.RelayUrl.parse(r) for r in own]))
        except Exception:   # noqa: BLE001  自己那份沒發到不影響寄出
            pass
    finally:
        await c.shutdown()
    return ok


# ── 收件(第 8.1 節)與回條(第 6.2、8.1 節第 9 步)──
BACKFILL = 2 * 86400 + 600       # gift wrap 時間戳往回亂數最多 2 天 ⇒ 補收要往回 2 天 10 分
KEEP = 7 * 86400                 # 去重紀錄保留 7 天
RESUB_EVERY = 30                 # 秒;席的金鑰有變(新席、封存)就重新訂閱
RECEIPT_STAGES_OUT = ("queued", "delivered", "held", "refused", "expired")   # read 是 1.8


def _tagmap(rumor):
    out = {}
    for t in rumor.tags():
        v = t.to_vec()
        if len(v) >= 2 and v[0] not in out:
            out[v[0]] = v[1]
    return out


class Ledger(object):
    """去重(event id 與 msgid)、收件帳(回條要寄給誰、用哪把金鑰)、補收起點。檔案都在 bridge root,0600。"""

    def __init__(self, root):
        self.root = Path(root)
        self.p = self.root / "recv-state.json"
        try:
            self.st = json.loads(self.p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.st = {}
        self.st.setdefault("seen", {})
        self.st.setdefault("last_at", 0)
        self.st.setdefault("delivery_sent", {})

    def save(self):
        cut = time.time() - KEEP
        self.st["seen"] = {k: v for k, v in self.st["seen"].items() if v >= cut}
        write_json(str(self.p), self.st)
        os.chmod(str(self.p), 0o600)

    def seen(self, key):
        return key in self.st["seen"]

    def mark(self, key):
        self.st["seen"][key] = int(time.time())

    def received(self, msgid, rec=None):
        p = self.root / "received" / (msgid + ".json")
        if rec is None:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        p.parent.mkdir(exist_ok=True)
        write_json(str(p), rec)
        os.chmod(str(p), 0o600)
        return rec


class Receiver(object):
    def __init__(self, root, sender):
        self.root = Path(root)
        self.sender = sender
        self.led = Ledger(root)

    def _our_seats(self):
        m = NK.read_map()
        return {NK.npub_to_hex(e["npub"]): e for e in m["seats"] if e.get("status") == "active"}, \
            (m.get("owner") or {}).get("npub")

    async def run(self, stop):
        while not stop.is_set():
            seats, _owner = self._our_seats()
            if not seats:
                await _sleep_or_stop(stop, 5)
                continue
            try:
                await self._session(stop, seats)
            except Exception as err:   # noqa: BLE001  斷線、站掛掉:5 秒後重來(下一輪會補收)
                sys.stderr.write("recv:%s\n" % type(err).__name__)
                await _sleep_or_stop(stop, 5)

    async def _session(self, stop, seats):
        c = await _client(CR.relays())
        try:
            since = int(self.led.st.get("last_at") or time.time()) - BACKFILL
            flt = N.Filter().kind(N.Kind(1059)).pubkeys([N.PublicKey.parse(h) for h in seats]) \
                .since(N.Timestamp.from_secs(max(0, since)))
            await c.subscribe(N.ReqTarget.auto([flt]))
            stream = c.notifications()
            until = time.time() + RESUB_EVERY
            while not stop.is_set():
                if time.time() >= until:
                    until = time.time() + RESUB_EVERY
                    if set(self._our_seats()[0]) != set(seats):
                        return                                   # 席變了 ⇒ 重新訂閱
                try:
                    n = await asyncio.wait_for(stream.next(), 1.0)
                except asyncio.TimeoutError:
                    continue
                if n is None:
                    return
                if n.is_NEW_EVENT():
                    await self.on_wrap(n.event, seats)
        finally:
            await c.shutdown()

    async def on_wrap(self, ev, seats):
        eid = ev.id().to_hex()
        if self.led.seen("e:" + eid) or ev.kind().as_u16() != 1059:
            return
        self.led.mark("e:" + eid)
        to_hex = next((v[1] for v in (t.to_vec() for t in ev.tags()) if len(v) >= 2 and v[0] == "p" and v[1] in seats), None)
        if to_hex is None:
            return self.led.save()
        entry = seats[to_hex]
        keys = _keys(entry["key"])
        try:
            un = N.UnwrappedGift.from_gift_wrap(keys, ev)   # nostr-sdk 自己擋 seal 簽章者 ≠ rumor 作者(sender mismatch)
        except Exception as err:   # noqa: BLE001
            sys.stderr.write("unwrap:%s\n" % type(err).__name__)
            return self.led.save()
        finally:
            keys = None
        rumor, sender = un.rumor(), un.sender()
        if rumor.author().to_hex() != sender.to_hex():      # NIP-17 硬性規定;SDK 之外自己再比一次(第 26 節)
            sys.stderr.write("sender_mismatch\n")
            return self.led.save()
        self.led.st["last_at"] = max(self.led.st.get("last_at") or 0, int(time.time()))
        if rumor.kind().as_u16() != 14 or sender.to_hex() == to_hex:
            return self.led.save()                           # 不是私訊;或是這一席寄給自己的那份副本(NIP-17)
        tags = _tagmap(rumor)
        from_npub = sender.to_bech32()
        if tags.get("aib-type") == "receipt":
            self.on_receipt(tags, from_npub)
            return self.led.save()
        await self.on_dm(rumor, tags, from_npub, entry)
        self.led.save()

    def on_receipt(self, tags, from_npub):
        ref, stage = tags.get("aib-ref", ""), tags.get("aib-receipt", "")
        if not GF.MSGID_RE.match(ref) or stage not in ("queued", "delivered", "held", "refused", "expired", "read"):
            return
        try:
            sent = json.loads((self.root / "sent" / (ref + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                                           # 不是我寄的信 ⇒ 不收
        if sent.get("to") != from_npub:
            sys.stderr.write("receipt_wrong_author\n")       # 不是我寄給的那席簽的 ⇒ 冒充,不收
            return
        r = {"v": 1, "at": int(time.time()), "stage": stage}
        pos = tags.get("aib-queue-pos", "")
        if stage == "queued" and pos.isdigit() and int(pos) > 0:
            r["pos"] = int(pos)
        GF.append_receipt(self.root, ref, r)

    async def on_dm(self, rumor, tags, from_npub, entry):
        msgid = tags.get("aib-msgid") or ""
        if not GF.MSGID_RE.match(msgid):
            msgid = rumor.id().to_hex()[:32] if rumor.id() else None   # 非 aib 寄件人:用 rumor id 當編號
        if not msgid or self.led.seen("m:" + msgid):
            return
        self.led.mark("m:" + msgid)
        is_aib = tags.get("aib-v") == "1"
        self.led.received(msgid, {"v": 1, "from": from_npub, "key": entry["key"], "seat": entry["seat5"],
                                  "aib": is_aib, "lang": tags.get("aib-lang"), "at": int(time.time())})
        content = rumor.content()
        if not content or len(content) > GF.CONTENT_MAX:
            return await self.send_receipt(msgid, "refused")
        exp = tags.get("aib-expires", "")
        expires = int(exp) if exp.isdigit() else None
        if expires and expires < time.time():
            return await self.send_receipt(msgid, "expired")          # 第 8.4 節:補收到時已過期 ⇒ 不投遞
        rec = {"v": 1, "msgid": msgid, "received_at": int(time.time()), "to_cabin": entry.get("cabin") or "main",
               "to_seat": entry["seat5"], "from_npub": from_npub, "verified": False, "class": "stranger",
               "content": content}
        from_addr = tags.get("aib-from")
        parsed = GF.parse_address(from_addr) if from_addr else None
        if parsed is not None:
            rec["from_addr"] = from_addr
        if parsed and "owner" in parsed:
            rc, res = await self.sender._resolve(from_addr)
            # 第 8.1 節第 4 步:rumor 作者要就是名冊上那一席 ⇒ 已驗證;否則一律未驗證、當陌生
            if rc == 0 and res["seat"]["npub"] == from_npub:
                rec["verified"] = True
                own = (NK.read_map().get("owner") or {}).get("npub")
                rec["class"] = "own" if parsed["owner"] == own else "stranger"   # 好友名單是第 2 期
                for k_in, k_out in (("display_name", "display_name"), ("country", "country")):
                    if res.get(k_in):
                        rec[k_out] = res[k_in]
                if res["seat"].get("name"):
                    rec["persona_name"] = res["seat"]["name"]
        for k_tag, k in (("aib-priority", "priority"), ("aib-thread", "thread"), ("aib-kind", "kind"),
                         ("aib-lang", "lang"), ("aib-tz", "tz")):
            if tags.get(k_tag):
                rec[k] = tags[k_tag]
        if rec.get("priority") not in GF.PRIORITIES:
            rec.pop("priority", None)
        if expires:
            rec["expires"] = expires
        if GF.MSGID_RE.match(tags.get("aib-reply-to", "")):
            rec["reply_to"] = tags["aib-reply-to"]
        try:
            GF.write_inbox(self.root, rec)
        except GF.ContractError as err:
            sys.stderr.write("inbox:%s\n" % err)
            await self.send_receipt(msgid, "refused")

    async def send_receipt(self, msgid, stage, pos=None):
        """回條寄回原寄件席(第 6.2 節)。只回 aib 寄件人(一般 Nostr App 收到一則看不懂的字沒用)。"""
        rec = self.led.received(msgid)
        if not rec or not rec.get("aib") or stage not in RECEIPT_STAGES_OUT:
            return False
        rc, res = await self.sender._resolve(rec["from"])
        if rc != 0:
            return False
        t = [["aib-v", "1"], ["aib-type", "receipt"], ["aib-receipt", stage], ["aib-ref", msgid]]
        if pos:
            t.append(["aib-queue-pos", str(pos)])
        keys = _keys(rec["key"])
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(rec["from"]), _receipt_text(stage, rec.get("lang")),
                                      rumor_extra_tags=[N.Tag.parse(x) for x in t])
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
            return bool(out.success)
        finally:
            await c.shutdown()

    async def pump_delivery(self):
        """核心寫的 delivery/<msgid>.jsonl(佇列狀態變化)⇒ 新的那幾行照寄回條(第 22.2 節)。"""
        d = self.root / "delivery"
        if not d.is_dir():
            return
        sent = self.led.st["delivery_sent"]
        changed = False
        for p in sorted(d.glob("*.jsonl")):
            msgid = p.stem
            try:
                lines = GF.read_delivery(self.root, msgid)
            except Exception:   # noqa: BLE001
                continue
            done = sent.get(msgid, 0)
            for r in lines[done:]:
                if r.get("state") in RECEIPT_STAGES_OUT:
                    await self.send_receipt(msgid, r["state"], r.get("pos"))
                done += 1
                changed = True
            sent[msgid] = done
        if changed:
            self.led.save()


_RECEIPT_TEXT = {"queued": "已排隊", "delivered": "已送達", "held": "待對方審核", "refused": "對方未收", "expired": "已過期"}


def _receipt_text(stage, lang):
    """回條內文:給一般 Nostr App 看的一句話(第 6.2 節:依回條讀者的語言)。"""
    try:
        import i18n_lib
        loc = lang if lang in ("zh-TW", "en", "ja", "ko") else "en"
        return i18n_lib.t("aib.chat.receipt." + stage, None, locale=loc)
    except Exception:   # noqa: BLE001
        return _RECEIPT_TEXT.get(stage, stage)


async def _sleep_or_stop(stop, secs):
    try:
        await asyncio.wait_for(stop.wait(), secs)
    except asyncio.TimeoutError:
        pass


def _switch_on():
    return CR.profile_path().parent.joinpath("enabled.json").exists()


async def serve(root):
    """常駐:每 POLL 秒領 outbox、寄;每 HB_EVERY 秒寫心跳。開關關掉、父行程(當班的轉接程式)不在、SIGTERM ⇒ 結束並刪心跳。"""
    import signal
    root = GF.ensure_dirs(root)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    parent = os.getppid()
    sender, last_hb = Sender(root), 0.0
    receiver = Receiver(root, sender)
    recv_task = asyncio.ensure_future(receiver.run(stop))
    for left in sorted((root / "sending").glob("*.json")):     # 上次掛掉時領了還沒寄完的:當重試處理
        if not (root / "sending" / (left.stem + ".try")).exists():
            write_json(str(root / "sending" / (left.stem + ".try")), {"n": 0, "next": 0, "why": "restart"})
    try:
        while not stop.is_set():
            if not _switch_on() or os.getppid() != parent:
                break
            if time.time() - last_hb >= HB_EVERY:
                GF.write_heartbeat(root)
                last_hb = time.time()
            for msgid, _rec, _p in GF.pending(root, "outbox"):
                if GF.claim(root, msgid) is not None:
                    await sender.handle(msgid)
            for msgid in sender.due_retries():
                await sender.handle(msgid)
            await receiver.pump_delivery()
            try:
                await asyncio.wait_for(stop.wait(), POLL)
            except asyncio.TimeoutError:
                pass
    finally:
        stop.set()
        try:
            await asyncio.wait_for(recv_task, 10)
        except Exception:   # noqa: BLE001
            recv_task.cancel()
        try:
            (Path(root) / "heartbeat.json").unlink()
        except FileNotFoundError:
            pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="chat_nostr")
    p.add_argument("cmd", choices=["publish", "publish-empty", "fetch", "serve"])
    p.add_argument("addr", nargs="?")
    p.add_argument("--root")
    a = p.parse_args(argv)
    root = Path(a.root) if a.root else GF.default_root()
    try:
        if a.cmd == "publish":
            rc, rec = cmd_publish(root)
        elif a.cmd == "publish-empty":
            rc, rec = cmd_publish_empty(root)
        elif a.cmd == "serve":
            return asyncio.run(serve(root))
        else:
            rc, rec = cmd_fetch(a.addr, CR.relays())
    except Exception as err:   # noqa: BLE001  例外只記種類:訊息裡可能帶路徑或金鑰相關字
        rc, rec = 2, {"ok": False, "why": "error", "err": type(err).__name__}
    print(json.dumps(rec, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
