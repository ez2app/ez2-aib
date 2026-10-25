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
import collections
import datetime
import json
import re
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import nostr_sdk as N  # noqa: E402  只有 venv 裡有;核心沒有這一行
import chat_devices as DV  # noqa: E402
import chat_phone_forward as PF  # noqa: E402
import chat_power as CP  # noqa: E402
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


def _roster_signer():
    """名冊用哪一把簽(第 37.7 節 K2):主人私鑰在這台、不用密語 ⇒ 主人金鑰 + d=aib-roster;
    主人金鑰加密了 / 這台是 join 進來的 ⇒ 機器金鑰 + d=aib-roster:<機器碼>(主人那份只在本人輸密語時重發)。"""
    if NK.owner_local():
        return _keys("owner"), CR.ROSTER_D
    dev = NK.read_map().get("device") or {}
    return _keys("device"), DV.roster_d(dev["npub"])


def _sign_roster(keys, content, d=None):
    tag = N.Tag.identifier(d or CR.ROSTER_D)
    return N.EventBuilder(N.Kind(CR.KIND_ROSTER), json.dumps(content, ensure_ascii=False, sort_keys=True)).tags([tag]).finalize(keys)


def _sign_dm_relays(keys, relays):
    return N.EventBuilder(N.Kind(CR.KIND_DM_RELAYS), "").tags([N.Tag.parse(["relay", r]) for r in relays]).finalize(keys)


async def _client(relays):
    c = N.Client()
    for r in relays:
        try:
            await c.add_relay(N.RelayUrl.parse(r))
        except Exception:   # noqa: BLE001  一站壞網址不拖垮整批(#303 審 A-M3)
            continue
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
    owner, d = _roster_signer()
    roster_ev = _sign_roster(owner, content, d)
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
    owner, d = _roster_signer()
    roster_ev = _sign_roster(owner, content, d)
    seat_evs = [(s, _sign_dm_relays(_keys(s["key"]), [])) for s in (prev.get("dm_seats") or [])]
    acks = asyncio.run(_send_all(last_relays, [roster_ev] + [ev for _s, ev in seat_evs]))
    rec = {"v": 1, "hash": "empty", "kind": "publish-empty", "at": int(time.time()), "roster_id": roster_ev.id().to_hex(),
           "roster_ok": acks.get(roster_ev.id().to_hex()) or [], "dm_seats": []}
    write_json(str(CR.published_path(root)), rec)
    os.chmod(str(CR.published_path(root)), 0o600)
    return (0 if rec["roster_ok"] else 2), rec


def cmd_sign_owner(stdin):
    """本人輸過密語之後(chat_bridge 那一半解開主人私鑰、從管線交過來,不經參數 / 環境變數):
    用主人金鑰簽機器清單(d=aib-devices)與 / 或主人名冊(d=aib-roster),發出去。回各自收下的站。"""
    req = json.loads(stdin.readline() or "{}")
    keys = N.Keys.parse(req.pop("secret"))
    evs = {}
    if req.get("devices") is not None:
        evs["devices"] = N.EventBuilder(N.Kind(CR.KIND_ROSTER), json.dumps(req["devices"], ensure_ascii=False, sort_keys=True)) \
            .tags([N.Tag.identifier(DV.DEVICES_D)]).finalize(keys)
    if req.get("roster") is not None:
        evs["roster"] = _sign_roster(keys, req["roster"])
    keys = None
    acks = asyncio.run(_send_all(req.get("relays") or CR.relays(), list(evs.values())))
    out = {k + "_ok": acks.get(ev.id().to_hex()) or [] for k, ev in evs.items()}
    return (0 if evs and all(out.values()) else 2), out


INVITE_D = "aib-invite:"


def _invite_event(keys, h, body, exp):
    tags = [N.Tag.identifier(INVITE_D + h), N.Tag.parse(["expiration", str(int(exp))])]
    return N.EventBuilder(N.Kind(CR.KIND_ROSTER), json.dumps(body, ensure_ascii=False, sort_keys=True)).tags(tags).finalize(keys)


def cmd_invite_publish(stdin):
    """邀請碼(10-24 晚):用名冊那把金鑰簽 d=aib-invite:<雜湊>;內容 = 我的位址 + 到期,用碼推出的金鑰加密 + HMAC
    (綁簽章者,#299 第 1 輪審 H1)。金鑰只走 stdin,不進 argv / 檔。中繼站照 expiration 標籤自己丟。"""
    req = json.loads(stdin.readline() or "{}")
    if not GF.INVITE_HASH_RE.match(str(req.get("hash") or "")) or not GF.parse_address(str(req.get("addr") or "")) \
            or not re.fullmatch(r"[0-9a-f]{32}", str(req.get("key") or "")):
        return 2, {"ok": False, "why": "bad_request"}
    keys, _d = _roster_signer()
    body = GF.invite_seal(bytes.fromhex(req["key"]), keys.public_key().to_bech32(),
                          {"addr": req["addr"], "exp": int(req["exp"])})
    ev = _invite_event(keys, req["hash"], body, req["exp"])
    keys = None
    ok = asyncio.run(_send_all(CR.relays(), [ev])).get(ev.id().to_hex()) or []
    return (0 if ok else 2), {"ok": bool(ok), "relays_ok": ok}


async def invite_fetch(h, relays):
    """⇒ [{author, at, body}](簽章已驗);挑哪一筆、驗作者是不是位址的主人由 chat_bridge 做。"""
    if not GF.INVITE_HASH_RE.match(h or ""):
        return 2, {"ok": False, "why": "bad_hash"}
    out = []
    for e in await _fetch_all(relays, N.Filter().kind(N.Kind(CR.KIND_ROSTER)).identifier(INVITE_D + h)):
        try:
            body = json.loads(e.content())
        except ValueError:
            continue
        if isinstance(body, dict):
            out.append({"author": e.author().to_bech32(), "at": e.created_at().as_secs(), "body": body})
    return 0, {"ok": True, "hits": out}


def cmd_pair_send(stdin):
    """新機器(join):用機器金鑰簽配對請求(d=aib-pair:<指紋>,p = 主人)發到主人的收信站。nonce 只帶雜湊。"""
    import chat_owner as CO
    req = json.loads(stdin.readline() or "{}")
    dev = NK.read_map().get("device") or {}
    ev = N.EventBuilder(N.Kind(CR.KIND_ROSTER), json.dumps(req["content"], ensure_ascii=False, sort_keys=True)) \
        .tags([N.Tag.identifier(CO.pair_d(dev["npub"])), N.Tag.parse(["p", NK.npub_to_hex(req["content"]["owner"])])]) \
        .finalize(_keys("device"))
    ok = asyncio.run(_send_all(req.get("relays") or CR.relays(), [ev])).get(ev.id().to_hex()) or []
    return (0 if ok else 2), {"ok": bool(ok), "relays_ok": ok}


async def devices_fetch(owner_npub, relays):
    """改清單前(@abb89 10-18 H1):從中繼站抓這位主人現行的 d=aib-devices、驗簽(nostr-sdk)+ 作者 = 主人 + chat_devices 驗內容,
    當下一次簽章的底。過期的照樣回(底只看內容);沒有 ⇒ list None。"""
    flt = N.Filter().author(N.PublicKey.parse(owner_npub)).kind(N.Kind(CR.KIND_ROSTER)).identifier(DV.DEVICES_D)
    ev = await _fetch_latest(relays, flt)
    if ev is None:
        return 0, {"ok": True, "list": None}
    if ev.author().to_bech32() != owner_npub:
        return 2, {"ok": False, "why": "wrong_author"}
    try:
        rec = DV.parse_list(ev.content(), owner_npub, int(time.time()))
    except DV.DevicesError as err:
        return 2, {"ok": False, "why": "bad_list", "detail": str(err)}
    return 0, {"ok": True, "list": rec}


async def pair_fetch(owner_npub, relays):
    """主人那台:抓給我的配對請求 ⇒ [{signer, content}](簽章已驗;d 要是簽章者自己的 aib-pair:<指紋>)。"""
    import chat_owner as CO
    flt = N.Filter().kind(N.Kind(CR.KIND_ROSTER)).pubkey(N.PublicKey.parse(owner_npub))
    out = []
    for e in await _fetch_all(relays, flt):
        signer = e.author().to_bech32()
        vecs = [t.to_vec() for t in e.tags()]
        if next((v[1] for v in vecs if len(v) >= 2 and v[0] == "d"), None) == CO.pair_d(signer):
            out.append({"signer": signer, "content": e.content()})
    return 0, {"ok": True, "requests": out}


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


async def _fetch_all(relays, flt):
    """同 _fetch_latest,但回全部(驗過簽);機器名冊一次抓清單上每一台。"""
    c = await _client(relays)
    try:
        evs = await c.fetch_events(N.ReqTarget.auto([flt]), datetime.timedelta(seconds=TIMEOUT))
    finally:
        await c.shutdown()
    return [e for e in evs if e.verify()]


async def _owner_roster(owner_npub, relays, root):
    """主人的名冊(第 37.7 節 K1):主人金鑰簽的 d=aib-roster,再加機器清單上各台的機器名冊合併。
    ⇒ (rc, 合併後名冊或原因 dict, 名冊時間)。清單不可用(沒有 / 壞 / seq 比看過的小 / 過期)⇒ 只用 d=aib-roster。"""
    owner_pk = N.PublicKey.parse(owner_npub)
    flt = N.Filter().author(owner_pk).kind(N.Kind(CR.KIND_ROSTER)).identifier(CR.ROSTER_D)
    flt_dv = N.Filter().author(owner_pk).kind(N.Kind(CR.KIND_ROSTER)).identifier(DV.DEVICES_D)
    try:
        ev, ev_dv = await asyncio.gather(_fetch_latest(relays, flt), _fetch_latest(relays, flt_dv))
    except Exception as err:   # noqa: BLE001
        return 2, {"ok": False, "why": "relay_error", "err": type(err).__name__}, None
    base, at = None, None
    if ev is not None:
        if ev.author().to_bech32() != owner_npub:
            return 2, {"ok": False, "why": "wrong_author"}, None
        try:
            base = CR.parse(ev.content(), owner_npub)
        except CR.RosterError as err:
            return 2, {"ok": False, "why": "bad_roster", "detail": str(err)}, None
        at = ev.created_at().as_secs()
    dev_list, dv_why = DV.usable_list(root, owner_npub, ev_dv.content() if ev_dv else None,
                                      ev_dv.author().to_bech32() if ev_dv else None, int(time.time()))
    if ev_dv is not None and dv_why != "ok":
        audit(root, "devices", "-", owner_npub, "devices-list:" + dv_why)
    devs = []
    if dev_list and DV.machines(dev_list):
        listed = {d["npub"]: d for d in DV.machines(dev_list)}        # 主人手機不發機器名冊、不准被當機器合併
        flt_r = N.Filter().authors([N.PublicKey.parse(n) for n in listed]).kind(N.Kind(CR.KIND_ROSTER)) \
            .identifiers([DV.roster_d(n) for n in listed])
        try:
            evs = await _fetch_all(relays, flt_r)
        except Exception as err:   # noqa: BLE001
            return 2, {"ok": False, "why": "relay_error", "err": type(err).__name__}, None
        latest = {}
        for e in evs:
            n = e.author().to_bech32()
            vecs = [t.to_vec() for t in e.tags()]
            d_tag = next((v[1] for v in vecs if len(v) >= 2 and v[0] == "d"), None)
            if n not in listed or d_tag != DV.roster_d(n):
                continue
            if n not in latest or e.created_at().as_secs() > latest[n].created_at().as_secs():
                latest[n] = e
        for n, e in sorted(latest.items()):
            try:
                devs.append((n, DV.parse_device_roster(e.content(), n, owner_npub, dev_list)))
                at = max(at or 0, e.created_at().as_secs())
            except (CR.RosterError, DV.DevicesError) as err:
                audit(root, "devices", "-", n, "device-roster:%s" % err)
    if base is None and not devs:
        return 2, {"ok": False, "why": "no_roster"}, None             # 對方還沒開全球對談(第 13 節)
    host = next((d["npub"] for d in (dev_list or {}).get("devices") or [] if d.get("host") is True), None)
    merged, warns = DV.merge(owner_npub, base, devs, host=host)
    for w in warns:
        audit(root, "devices", "-", owner_npub, "%s:%s" % (w["kind"], w["cabin"]))
    return 0, merged, at


async def probe(url):
    """量一個中繼站(第 28.6 第三站實測):連線、回 OK、查詢回 EOSE 各花多久。只用**拋棄式金鑰**發一則
    ephemeral(kind 20001,站只轉送不存)、查一次 kind 10050(limit 1);不連發、不帶我們任何金鑰或名冊。"""
    out = {"ok": False, "url": url}
    t0 = time.time()
    c = N.Client()
    try:
        await c.add_relay(N.RelayUrl.parse(url))
        await c.try_connect(datetime.timedelta(seconds=TIMEOUT))
        rels = await c.relays()
        rel = next(iter(rels.values()), None)
        st = rel.status() if rel is not None else None
        if (getattr(st, "name", None) or str(st).split(".")[-1]) != "CONNECTED":
            out["why"] = "connect"
            return 2, out
        out["connect_ms"] = int((time.time() - t0) * 1000)
        ev = N.EventBuilder(N.Kind(20001), "aib-probe").finalize(N.Keys.generate())
        t1 = time.time()
        res = await c.send_event(ev)
        out["ok_ms"] = int((time.time() - t1) * 1000)
        out["accepted"] = bool(res.success)
        if not res.success:
            out["why"] = "rejected"
            out["reason"] = str(next(iter(res.failed.values()), ""))[:120] if getattr(res, "failed", None) else ""
        t2 = time.time()
        await c.fetch_events(N.ReqTarget.auto([N.Filter().kind(N.Kind(CR.KIND_DM_RELAYS)).limit(1)]),
                             datetime.timedelta(seconds=TIMEOUT))
        out["query_ms"] = int((time.time() - t2) * 1000)
        out["ok"] = out["accepted"]
        return (0 if out["ok"] else 2), out
    except Exception as err:   # noqa: BLE001
        out["why"] = out.get("why") or "error"
        out["err"] = type(err).__name__
        return 2, out
    finally:
        try:
            await c.shutdown()
        except Exception:   # noqa: BLE001
            pass


async def resolve(addr, relays, root=None):
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
        rc, rec, at = await _owner_roster(owner_npub, relays, Path(root) if root else GF.default_root())
        if rc != 0:
            return rc, rec
        s, cab = DV.find(rec, cabin, seat)          # 艙綁宣告它的機器(第 37.7 節第 4 點):撞名的艙已不在合併結果裡
        if s is None:
            out = {"ok": False, "why": "no_seat"}
            if "%s/%s" % (cabin, seat) in (rec.get("dropped_seats") or []):   # 對方名冊同一席列了兩次(chat_roster.parse 整席丟掉)
                out["hint"] = GF.DUPLICATE_SEAT_HINT                          # why 照回 no_seat(契約),人話另放 hint
            return 2, out
        env = CR.common_envelope({"envelope": cab.get("envelope") or rec.get("envelope")})
        if env is None:
            return 2, {"ok": False, "why": "no_common_envelope"}
        seat_npub, look_in = s["npub"], (cab.get("dm_relays") or rec.get("dm_relays") or relays)
        out.update(owner=owner_npub, display_name=rec.get("display_name"), country=rec.get("country"), cabin=cabin,
                   seat=s, envelope=env, roster_at=at, device=cab.get("device"),
                   socials=rec.get("socials") or [], nip05=rec.get("nip05"),
                   aib=cab.get("aib_version") or rec.get("aib_version"))   # 第 37.5 節:席所在那台的版本
        out["roster_seats"] = _roster_seats(rec)
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


def cmd_fetch(addr, relays, root=None):
    return asyncio.run(resolve(addr, relays, root=root))


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
    typ = "friend-request" if rec.get("op") == "request" else "dm"        # 第 29.4 節
    t = [["aib-v", "1"], ["aib-type", typ], ["aib-msgid", rec["msgid"]], ["aib-from", from_addr], ["aib-to", rec["to"]]]
    for key, tag in (("thread", "aib-thread"), ("kind", "aib-kind"), ("priority", "aib-priority"),
                     ("reply_to", "aib-reply-to"), ("lang", "aib-lang")):
        if rec.get(key):
            t.append([tag, str(rec[key])])
    if rec.get("expires"):
        t.append(["aib-expires", str(rec["expires"])])
    if rec.get("author") in GF.AUTHORS:                                   # 第 35 節第 2 點:誰寫的(本人 = 艦長室驗過 PIN)
        t.append(["aib-author", rec["author"]])
    if rec.get("echo"):
        t.append(["aib-echo", "1"])                  # 回音小幫手回的那一句:收件端只進艦長室、不交給 AI(#306 審 B-M1)
    tz = _local_tz()
    if tz:
        t.append(["aib-tz", tz])
    return [N.Tag.parse(x) for x in t]


def _receipt(root, msgid, stage, detail=None):
    r = {"v": 1, "at": int(time.time()), "stage": stage}
    if detail:
        r["detail"] = detail
    GF.append_receipt(root, msgid, r)


# ── 觀測(第 20.5 節;第 1 期 1.9):稽核行、時鐘、工人狀態 ──
CLOCK_EVERY = float(os.environ.get("AIB_CHAT_CLOCK_EVERY") or 600)   # 秒
CLOCK_WARN_S = float(os.environ.get("AIB_CHAT_CLOCK_WARN") or 3)     # 驗收第 10 項:慢 3 秒就警告
OFFLINE_AFTER = float(os.environ.get("AIB_CHAT_OFFLINE_AFTER") or 300)   # 第 20.1 節:全部站斷線超過 5 分鐘 ⇒ 離線
RECV_OK_S = float(os.environ.get("AIB_CHAT_RECV_OK_S") or 60)            # 10-20:收信 session 撐過這麼久才算正常,連續失敗歸零


def audit(root, direction, msgid, peer, result):
    """一則全球收發一行(第 20.5 節):時間、方向、msgid、對象 npub、結果。**不記內容。**"""
    d = Path(root) / "audit"
    try:
        d.mkdir(mode=0o700, exist_ok=True)
        p = d / (time.strftime("%Y%m%d") + ".jsonl")
        line = json.dumps({"v": 1, "at": int(time.time()), "dir": direction, "msgid": msgid, "peer": peer,
                           "result": result}, ensure_ascii=False) + "\n"
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass                      # 稽核寫不進去不擋收發


def today_counts(root):
    out = {"sent": 0, "received": 0, "failed": 0}
    try:
        lines = (Path(root) / "audit" / (time.strftime("%Y%m%d") + ".jsonl")).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if r.get("dir") == "out" and r.get("result") == "relayed":
            out["sent"] += 1
        elif r.get("dir") == "out" and r.get("result") in ("failed", "refused", "expired"):
            out["failed"] += 1
        elif r.get("dir") == "in" and r.get("result") == "inbox":
            out["received"] += 1
    return out


def _clock_url(relay):
    explicit = os.environ.get("AIB_CHAT_CLOCK_URL")
    if explicit:
        return explicit
    if relay.startswith("wss://"):
        return "https://" + relay[len("wss://"):].split("/", 1)[0] + "/"
    return None


def clock_offset(relays):
    """中繼站主機 HTTP Date 標頭 − 本機時間(秒;Date 只到秒)。量不到回 None。不連任何新的站:只問收信站本身。"""
    import email.utils
    import urllib.request
    got = []
    for r in relays:
        url = _clock_url(r)
        if not url:
            continue
        try:
            t0 = time.time()
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5) as resp:
                date = resp.headers.get("Date")
            t1 = time.time()
            if date:
                got.append(email.utils.parsedate_to_datetime(date).timestamp() - (t0 + t1) / 2)
        except Exception:   # noqa: BLE001  (含 HTTP 錯誤碼:4xx 也常帶 Date,但這裡寧可少量一站)
            continue
        if os.environ.get("AIB_CHAT_CLOCK_URL"):
            break
    if not got:
        return None
    got.sort()
    return round(got[len(got) // 2], 1)


class Sender(object):
    def __init__(self, root):
        self.root = Path(root)
        self.cache = {}
        self.echo_in = collections.deque(maxlen=2000)   # 回音小幫手剛收的陌生來信 msgid:outbox echo:true 只認回這些的

    async def _resolve(self, addr):
        hit = self.cache.get(addr)
        if hit and time.time() - hit[0] < RESOLVE_TTL:
            return hit[1]
        res = await resolve(addr, CR.relays(), root=self.root)
        if res[0] == 0:
            self.cache[addr] = (time.time(), res)
        return res

    def _rcpt(self, msgid, peer, stage, detail=None):
        _receipt(self.root, msgid, stage, detail)
        audit(self.root, "out", msgid, peer, stage)

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
            self._rcpt(msgid, None, "failed", why)
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
                self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "resolved", json.dumps({"npub": res["seat"]["npub"], "dm_relays": res["dm_relays"],
                                                                   "display_name": res.get("display_name")}, ensure_ascii=False))
            else:
                self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "failed", res.get("why"))
            self._done(msgid)
            return
        if rec.get("expires") and rec["expires"] < time.time():
            self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "expired")
            self._done(msgid)
            return
        frm = rec["from"]
        if rec.get("echo") and not (GF.echo_on(self.root) and rec.get("reply_to") in self.echo_in):
            if rec.get("content") in ECHO_TEXT.values():
                # #306 r3 B-L6:回音那句寫進 outbox 後轉接程式重啟(echo_in 只在記憶體)⇒ 丟掉,不當一般信寄給陌生人;
                #   那句是固定字、沒人等它的回條,丟了只是少回一句
                audit(self.root, "out", msgid, None, "echo:stale")
                self._done(msgid)
                return
            rec.pop("echo")                          # #306 r2:只有回音小幫手剛收的陌生來信才認 echo,其他照一般寄件
        if rec.get("echo") and frm.get("attested"):
            return await self._handle_echo(msgid, rec)
        if not frm.get("attested"):
            # 寄件人身分沒量到 ⇒ 不拿那一席的金鑰代簽(簽了就等於證明是那一席寄的)
            self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "refused", "unattested")
            self._done(msgid)
            return
        m = NK.read_map()
        entry = next((e for e in NK.sending_seats() if e["seat5"] == frm["seat"]), None)   # 跟名冊同一把(現在的人格)
        if entry is None or not m.get("owner"):
            self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "refused", "no_sender_key")
            self._done(msgid)
            return
        rc, res = await self._resolve(rec["to"])
        if rc != 0:
            if res.get("why") in TRANSIENT:
                self._retry_later(msgid, res.get("why"))
            else:
                self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "refused", res.get("why"))
                self._done(msgid)
            return
        await self._ensure_dm_relays(entry)
        keys = _keys(entry["key"])
        from_npub = keys.public_key().to_bech32()
        from_addr = "%s/%s/%s" % (m["owner"]["npub"], frm["cabin"], frm["seat"])
        tags = _rumor_tags(rec, from_addr)
        receiver = N.PublicKey.parse(res["seat"]["npub"])
        gw = N.nip17_make_private_msg(keys, receiver, rec.get("content") or "", rumor_extra_tags=tags)
        # NIP-17:另包一份給自己(手機端才看得到雙方完整對話),發到自己的收信站
        mine = N.nip17_make_private_msg(keys, keys.public_key(), rec.get("content") or "", rumor_extra_tags=tags)
        keys = None
        target, own = res["dm_relays"], CR.relays()
        # 寄出帳:對方回條回來時,只收「我寄給的那個 npub」簽的(防有人冒充對方亂回條)。
        # 要在寄之前寫:公開站回 OK 可能比對方的回條還慢(10-16 實機驗收 5 則掉 3 張 held 回條)
        (self.root / "sent").mkdir(exist_ok=True)
        sent = {"v": 1, "to": res["seat"]["npub"], "at": int(time.time()), "op": rec.get("op") or "send", "owner": res.get("owner"),
                "seat": frm["seat"]}                 # 寄件席:回信要回到同一席才放寬(#306 審 A-L11)
        if rec.get("author") in GF.AUTHORS:   # 10-19 Andy:回「主人本人寄的」那封的信只進艦長室,收件時要查得到
            sent["author"] = rec["author"]
        write_json(str(self.root / "sent" / (msgid + ".json")), sent)
        try:
            ok = await _send_to(target, own, gw, mine)
        except Exception as err:   # noqa: BLE001
            self._retry_later(msgid, type(err).__name__)
            return
        if ok:
            self._rcpt(msgid, rec.get("to") if isinstance(rec, dict) else None, "relayed", "%d" % len(ok))      # 第 7.1 節第 6 步:1 站成功就算
            GF.keep_for_resend(self.root, rec, res.get("owner"), res["seat"]["npub"], from_npub)   # 10-24:上線就補寄
            self._remember_peer(rec["to"], res, "request" if rec.get("op") == "request" else "sent")
            GF.note_first(self.root, "sent")
            self._done(msgid)
        else:
            self._retry_later(msgid, "relays")

    async def _handle_echo(self, msgid, rec):
        """回音小幫手回的那一句(#306 審 B-M3):只包一份送到對方收信站。不寫 sent/、不寫回條、不補寄、不記好友、
        不發給自己那份(上萬人也不長檔);送不出去 ⇒ 放掉,只寫稽核行。"""
        self._done(msgid)
        entry = next((e for e in NK.sending_seats() if e["seat5"] == rec["from"]["seat"]), None)
        m = NK.read_map()
        if entry is None or not m.get("owner"):
            audit(self.root, "out", msgid, "-", "echo:no_sender_key")
            return
        rc, res = await self._resolve(rec["to"])
        if rc != 0:
            audit(self.root, "out", msgid, "-", "echo:unresolved")
            return
        await self._ensure_dm_relays(entry)
        tags = _rumor_tags(rec, "%s/%s/%s" % (m["owner"]["npub"], rec["from"]["cabin"], rec["from"]["seat"]))
        keys = _keys(entry["key"])
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]), rec.get("content") or "",
                                      rumor_extra_tags=tags)
        keys = None
        try:
            ok = await _send_to(res["dm_relays"], [], gw, None)     # 回音:只送對方
        except Exception:   # noqa: BLE001
            ok = []
        audit(self.root, "out", msgid, res["seat"]["npub"], "echo" if ok else "echo:relays")

    async def resend(self, cur):
        """補寄一封(10-24;#303 審 B-H2):只重包、送到對方收信站。不寫回條、不改 sent/、不另發給自己那份(B-M6:
        手機 App 才不會看到 2–4 次);帶原寄出時間 aib-sent-at。對方換席 / 我換人格 / 寄件席不在 / 查不到 ⇒ 靜默放棄,
        只寫稽核行(之後過期自己清)。回成不成功。"""
        rec, owner = cur["rec"], cur["owner"]
        msgid = rec["msgid"]
        entry = next((e for e in NK.sending_seats() if e["seat5"] == (rec.get("from") or {}).get("seat")), None)
        m = NK.read_map()
        if entry is None or not m.get("owner") or entry.get("npub") != cur.get("from"):
            audit(self.root, "out", msgid, owner, "resend:sender_changed")
            return False
        rc, res = await self._resolve(rec["to"])
        if rc != 0 or res["seat"]["npub"] != cur.get("to"):
            audit(self.root, "out", msgid, owner, "resend:" + ("peer_changed" if rc == 0 else "unresolved"))
            return False
        frm = rec["from"]
        tags = _rumor_tags(rec, "%s/%s/%s" % (m["owner"]["npub"], frm["cabin"], frm["seat"]))
        tags.append(N.Tag.parse(["aib-sent-at", str(GF.int_or0(cur.get("at")))]))
        keys = _keys(entry["key"])
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(cur["to"]), rec.get("content") or "", rumor_extra_tags=tags)
        keys = None
        try:
            ok = await _send_to(res["dm_relays"], [], gw, None)
        except Exception:   # noqa: BLE001
            ok = []
        GF.note_resent(self.root, owner, msgid)
        audit(self.root, "out", msgid, owner, "resend" if ok else "resend:relays")
        return bool(ok)

    async def _first_seat_addr(self, owner, roster=False):
        """對方主人名冊上第一個公開席的位址(聯絡人快照有就用,沒有就抓名冊;roster=True ⇒ 不看快照、直接抓名冊)。"""
        p = {} if roster else (GF.read_peers(self.root).get(owner) or {})
        for x in p.get("seats") or []:
            if x.get("seat"):
                return "%s/%s/%s" % (owner, x.get("cabin") or "main", x["seat"])
        try:
            rc, rec, _at = await _owner_roster(owner, CR.relays(), self.root)
        except Exception:   # noqa: BLE001
            return None
        rec = rec if rc == 0 else None
        for cab in (rec or {}).get("cabins") or []:
            for st in cab.get("seats") or []:
                return "%s/%s/%s" % (owner, cab.get("cabin"), st.get("seat"))
        return None

    async def _first_seat(self, owner):
        """在線 / 名片 / 空間快照寄給誰:快照第一席 ⇒ (rc, resolve 結果)。
        10-24 一台會員機:快照記著對方 10-20 的舊席(對方換艙後名冊只剩新席)⇒ resolve 一直 no_seat、在線四天沒送到。
        快照那席查不到 ⇒ 改抓名冊第一席,查到就用名冊更新快照(note_peer 拿到不比快照舊的名冊會整份換掉 seats)。"""
        addr = await self._first_seat_addr(owner)
        rc, res = await self._resolve(addr) if addr else (2, {})
        if rc == 0 or (addr and res.get("why") not in ("no_seat", "no_dm_relays")):
            return rc, res                      # 只有「那席不在 / 沒收件站」才改抓名冊;網路錯等下一輪(@abb89 #297 r1)
        fresh = await self._first_seat_addr(owner, roster=True)
        if not fresh or fresh == addr:
            return rc, res
        rc, res = await self._resolve(fresh)
        if rc == 0:
            GF.note_peer(self.root, owner, res.get("display_name"), res.get("country"), _peer_seat(res),
                         res.get("roster_at"), claims=_claims(res))
        return rc, res

    async def _push_room(self, tgt, owner, frm, now):
        """空間快照(第 34 節 v2):island 等級送島的戶外 + 走動的人;room 等級再加建築內部。
        state 依在線等級(off ⇒ 不帶),📌 那一句只在在線等級 status 才帶。有變就送(最少 ROOM_GAP_S),沒變一天一次。"""
        import room_snapshot as RS
        sp, lvl = CR.space_level(tgt), CR.share_level(tgt)
        snap = RS.build(with_rooms=(sp == "room"), with_status=(lvl == "status"), with_state=(lvl != "off"))
        if not snap:
            return
        wire, full, dropped = RS.encode_info(snap)
        if wire is None:                                         # 第 36.3 節:不送、記原因(aib chat space --json 的 last_error)
            GF.write_space_status(self.root, {"at": int(now), "reason": "too_big", "wire_bytes": full, "limit": RS.WIRE_MAX})
            return
        rooms = getattr(self, "r_last", None)
        if rooms is None:
            rooms = self.r_last = {}
        prev = rooms.get(tgt)
        changed_level = prev is not None and (len(prev) < 3 or prev[2] != sp)
        if prev and not changed_level and ((prev[0] == wire and now - prev[1] < 86400) or now - prev[1] < ROOM_GAP_S):
            return                                               # 等級變了(例:island ⇒ room)不等間隔,立刻送
        rc, res = await self._first_seat(tgt)
        if rc != 0:
            return
        keys = _keys(frm["key"])
        tags = [N.Tag.parse(x) for x in (["aib-v", "1"], ["aib-type", "space"],
                                         ["aib-from", "%s/%s/%s" % (owner, frm.get("cabin") or "main", frm["seat5"])])]
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]), wire, rumor_extra_tags=tags)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
        except Exception:   # noqa: BLE001
            out = None
        finally:
            await c.shutdown()
        if out is not None and out.success:
            rooms[tgt] = (wire, now, sp)
            GF.write_space_status(self.root, {"at": int(now), "reason": None, "wire_bytes": len(wire), "dropped": dropped})
            audit(self.root, "space-out", "-", tgt, str(len(wire)))

    async def _push_revoke(self, tgt, owner, frm, kind, now):
        """撤回(10-24 跨客戶驗收 6.7 / 5.4):之前送過空間快照 / 在線,現在降到 none / off(或刪好友、封鎖)
        ⇒ 送一則帶 aib-revoke 的同類私訊,對方刪掉手上的快照 / 在線格。回 True = 中繼站收下。
        舊版收件端:space 解不開記一行 bad、presence 沒 seats 忽略 ⇒ 照舊,不會出錯。"""
        tries = getattr(self, "rv_try", None)
        if tries is None:
            tries = self.rv_try = {}
        n, at = tries.get((tgt, kind), (0, 0))
        if now - at < revoke_wait(n):                                   # 送不到 ⇒ 退避重試,不設上限
            return False
        tries[(tgt, kind)] = (min(n + 1, 32), now)
        addr = await self._first_seat_addr(tgt)
        rc, res = await self._resolve(addr) if addr else (2, {})
        if rc != 0:
            return False
        keys = _keys(frm["key"])
        typ = "space" if kind == "space" else "presence"
        tags = [N.Tag.parse(x) for x in (["aib-v", "1"], ["aib-type", typ], ["aib-revoke", "1"],
                                         ["aib-from", "%s/%s/%s" % (owner, frm.get("cabin") or "main", frm["seat5"])])]
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]),
                                      json.dumps({"v": 1, "revoke": True}), rumor_extra_tags=tags)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
        except Exception:   # noqa: BLE001
            out = None
        finally:
            await c.shutdown()
        if out is None or not out.success:
            return False
        tries.pop((tgt, kind), None)
        audit(self.root, "revoke-out", "-", tgt, kind)
        return True

    async def _send_owner_msg(self, tgt, typ, body):
        """主人層級的私訊(掛單,第 36.2 節):用我第一個公開席的金鑰寄到對方第一個席。回 True = 中繼站收下。"""
        m = NK.read_map()
        owner = (m.get("owner") or {}).get("npub")
        pub = set((CR.read_profile() or {}).get("public") or [])
        mine = [e for e in NK.sending_seats() if e["seat5"] in pub]
        if not owner or not mine:
            return False
        rc, res = await self._first_seat(tgt)
        if rc != 0:
            return False
        frm = mine[0]
        keys = _keys(frm["key"])
        tags = [N.Tag.parse(x) for x in (["aib-v", "1"], ["aib-type", typ],
                                         ["aib-from", "%s/%s/%s" % (owner, frm.get("cabin") or "main", frm["seat5"])])]
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]), body, rumor_extra_tags=tags)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
        except Exception:   # noqa: BLE001
            out = None
        finally:
            await c.shutdown()
        return out is not None and out.success

    async def push_offers(self, now=None):
        """offer-out 裡的買單送出去;送不出去留著下一輪再試,超過 EXPIRE_S 就丟(帳那邊會記 expired)。"""
        import chat_offers as CO
        now = time.time() if now is None else now
        for p, rec in CO.queued(self.root):
            if now - int(rec.get("at") or 0) >= CO.EXPIRE_S:
                p.unlink(missing_ok=True)
                continue
            if await self._send_owner_msg(rec.get("to"), "offer", json.dumps(rec["msg"], ensure_ascii=False)):
                p.unlink(missing_ok=True)
                audit(self.root, "offer-out", "-", rec.get("to"), "buy")

    async def push_visits(self, now=None):
        """visit-out 裡的參觀請求 / 開放 / 拒絕送出去;送不出去留著下一輪再試,超過 EXPIRE_S 就丟。"""
        import chat_visit as CVI
        now = time.time() if now is None else now
        for p, rec in CVI.queued(self.root):
            if now - int(rec.get("at") or 0) >= CVI.EXPIRE_S:
                p.unlink(missing_ok=True)
                continue
            if await self._send_owner_msg(rec.get("to"), "visit", json.dumps(rec["msg"], ensure_ascii=False)):
                p.unlink(missing_ok=True)
                audit(self.root, "visit-out", "-", rec.get("to"), rec["msg"].get("op"))

    async def _push_card(self, tgt, owner, frm, now):
        """名片(第 32 節,Andy 23:5x「社群只給好友看」):我宣告的社群帳號私下送給這位好友 / 我的機器;
        內容有變就送,沒變一天一次。分享等級 off 也照送(名片跟在線狀態是兩回事);陌生人不會在 targets 裡。"""
        socials = CR.clean_socials((CR.read_profile() or {}).get("socials"))
        card = {"v": 1, "socials": socials}
        gen = None
        if CR.share_level(tgt) != "off":                # 10-24 綠點:看得到我在線的好友才拿心跳鑰
            beat_npub, _sign = NK.beat_sign()
            gen, stem = NK.beat_share()
            card["beat"] = {"npub": beat_npub, "key": NK.load_secret(stem), "gen": gen, "relays": CR.relays()[:GF.BEAT_RELAYS_MAX]}
        body = json.dumps(card, ensure_ascii=False, sort_keys=True)
        card = None
        cards = getattr(self, "c_last", None)
        if cards is None:
            cards = self.c_last = {}
        prev = cards.get(tgt)
        due = tgt in getattr(self, "card_due", set())
        if not due and ((prev and prev[0] == body and now - prev[1] < 86400) or (not socials and not prev and gen is None)):
            return
        rc, res = await self._first_seat(tgt)
        if rc != 0:
            return
        keys = _keys(frm["key"])
        tags = [N.Tag.parse(x) for x in (["aib-v", "1"], ["aib-type", "card"],
                                         ["aib-from", "%s/%s/%s" % (owner, frm.get("cabin") or "main", frm["seat5"])])]
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]), body, rumor_extra_tags=tags)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
        except Exception:   # noqa: BLE001
            out = None
        finally:
            await c.shutdown()
        if out is not None and out.success:
            cards[tgt] = (body, now)
            getattr(self, "card_due", set()).discard(tgt)
            if gen:
                GF.note_beat_given(self.root, gen, [tgt])
            audit(self.root, "card-out", "-", tgt, str(len(socials)))
            return True
        return False

    async def push_presence(self, now=None):
        """在線狀態(第 30.3 節):每位好友 / 我的其他機器依分享等級送一份;有變就送(最少間隔 PRESENCE_GAP_S),
        沒變 PRESENCE_EVERY_S 送一次。陌生、封鎖、等級 off ⇒ 不送。回送了幾份。
        ask_due(本人刷新 / 工人剛起)⇒ 不看間隔、每位都送,並加 aib-ask=1 請對方立刻回一份(10-24 在不在)。"""
        import seat_presence as SP
        now = time.time() if now is None else now
        m = NK.read_map()
        owner = (m.get("owner") or {}).get("npub")
        pub = set((CR.read_profile() or {}).get("public") or [])
        mine_seats = [e for e in NK.sending_seats() if e["seat5"] in pub]
        if not owner or not mine_seats:
            if getattr(self, "ask_due", False):         # 沒有能代表我的席 ⇒ 問不了;記成問過(畫面不會一直轉)
                self.ask_due = False
                GF.note_asked(self.root, now)
            return 0
        peers = GF.read_peers(self.root)
        targets = [f["owner"] for f in CR.read_friends(None, "mine")] + [f["owner"] for f in CR.read_friends()]
        targets = list(dict.fromkeys(t for t in targets if t != owner))
        targets.sort(key=lambda o: -max(int((peers.get(o) or {}).get(k) or 0) for k in ("last_in", "last_out", "seen_at")))
        last = getattr(self, "p_last", None)
        if last is None:
            last = self.p_last = {}
        shared = GF.read_shared_sent(self.root)
        for tgt, row in shared.items():                                 # 撤回:之前分享過、現在降級 / 不再是好友 / 封鎖
            if row.get("space") and CR.space_level(tgt) not in ("island", "room"):
                if await self._push_revoke(tgt, owner, mine_seats[0], "space", now):
                    GF.note_shared_sent(self.root, tgt, "space", None)
                    getattr(self, "r_last", {}).pop(tgt, None)          # 之後再開分享 ⇒ 立刻送
            if row.get("share") and CR.share_level(tgt) == "off":
                if await self._push_revoke(tgt, owner, mine_seats[0], "share", now):
                    GF.note_shared_sent(self.root, tgt, "share", None)
                    last.pop(tgt, None)
        ask = getattr(self, "ask_due", False)
        ping = getattr(self, "ping_owner", None)
        if ask and not ping:
            self.cache.clear()                          # 刷新 = 重抓好友名冊(對方換艙 / 換鑰馬上看得到);點開對話框不清
        self._rotate_beat_if_needed(targets)
        gen, given = GF.read_beat_given(self.root)
        beaters = {o for o, _n, _k, _r in GF.read_beat_keys(self.root)}
        # 10-24 綠點:對方有心跳、也拿到我這一代的鑰 ⇒ 一般在線等級不再一位一封(看我的心跳就好);
        #   點開對話框(ping)只問那一位(#303 審 A-M5);整面刷新才問還沒有心跳的舊版(最多 ASK_OLD_MAX 位)
        if not ask:
            ask_to = set()
        elif ping:
            ask_to = {ping}
        else:
            ask_to = set([t for t in targets if t not in beaters][:ASK_OLD_MAX])
        answer = getattr(self, "answer_to", None) or set()     # 有人問我在不在 ⇒ 不管他有沒有心跳都回(#303 審 B-M3)
        self.answer_to = set()
        sent = 0
        for tgt in targets[:PRESENCE_MAX_TARGETS]:
            await self._push_card(tgt, owner, mine_seats[0], now)       # 名片跟在線等級無關(好友 / 我的機器才在 targets)
            sp = CR.space_level(tgt)
            if sp in ("island", "room"):                                # 空間分享是另一條(第 34 節 v2)
                if (shared.get(tgt) or {}).get("space") != sp:          # 先記意圖:升級後第一次也算,降級才知道要撤回
                    GF.note_shared_sent(self.root, tgt, "space", sp)
                getattr(self, "rv_try", {}).pop((tgt, "space"), None)   # 回到分享範圍 ⇒ 下次撤回從頭退避
                await self._push_room(tgt, owner, mine_seats[0], now)
            lvl = CR.share_level(tgt)
            if lvl == "off":
                continue
            if (shared.get(tgt) or {}).get("share") != lvl:
                GF.note_shared_sent(self.root, tgt, "share", lvl)
            getattr(self, "rv_try", {}).pop((tgt, "share"), None)
            asking = tgt in ask_to
            if lvl == "online" and tgt in beaters and tgt in given and not asking and tgt not in answer:
                continue
            rows = SP.presence([e["seat5"] for e in mine_seats], with_status=(lvl == "status"))
            for r, e in zip(rows, mine_seats):
                r["cabin"] = e.get("cabin") or "main"
            power = CP.presence_power(self.root, PRESENCE_EVERY_S)          # 10-18:讓好友判斷這台是不是在睡
            body = json.dumps({"v": 1, "seats": rows, "power": power}, ensure_ascii=False, sort_keys=True)
            prev = last.get(tgt)
            if prev and not asking and tgt not in answer \
                    and ((prev[0] == body and now - prev[1] < PRESENCE_EVERY_S) or now - prev[1] < PRESENCE_GAP_S):
                continue
            rc, res = await self._first_seat(tgt)
            if rc != 0:
                continue
            frm = mine_seats[0]
            keys = _keys(frm["key"])
            tags = [N.Tag.parse(x) for x in (["aib-v", "1"], ["aib-type", "presence"],
                                             ["aib-from", "%s/%s/%s" % (owner, frm.get("cabin") or "main", frm["seat5"])])
                    + ((["aib-ask", "1"],) if asking else ())]
            gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(res["seat"]["npub"]),
                                          json.dumps({"v": 1, "at": int(now), "seats": rows, "power": power}, ensure_ascii=False),
                                          rumor_extra_tags=tags)
            keys = None
            c = await _client(res["dm_relays"])
            try:
                out = await c.send_event(gw)
            except Exception:   # noqa: BLE001
                out = None
            finally:
                await c.shutdown()
            if out is not None and out.success:
                last[tgt] = (body, now)
                sent += 1
                audit(self.root, "presence-out", "-", tgt, lvl + (":ask" if asking else ""))
        extra = 0
        for tgt in targets[PRESENCE_MAX_TARGETS:]:      # 第 51 位起也要拿得到心跳鑰(#303 審 B-L8);一輪最多另送幾張
            if extra >= CARD_EXTRA_PER_ROUND:
                break
            if await self._push_card(tgt, owner, mine_seats[0], now):
                extra += 1
        if ask:
            self.ask_due = False
            self.ping_owner = None
            GF.note_asked(self.root, now)
        return sent

    def _rotate_beat_if_needed(self, targets):
        """這一代心跳鑰給過的人裡有誰不該再看(刪好友 / 封鎖 / 改 off)⇒ 換一代;名片內容跟著變,各位好友下一輪就收到新的。"""
        gen, given = GF.read_beat_given(self.root)
        if not gen or not _friends_readable():
            return                                         # 好友清單讀壞 ⇒ targets 是空的,不是大家都刪了(#303 審 B-L9)
        ok = {t for t in targets if CR.share_level(t) != "off"}
        if given - ok:
            new_gen, _stem = NK.beat_share(rotate=True)
            GF.note_beat_given(self.root, new_gen, [])     # 新一代從零算起(不然下一輪又換)
            audit(self.root, "beat", "-", "-", "rotate:%d" % len(given - ok))

    async def push_beat(self, now=None):
        """10-24 綠點:這台一筆心跳(kind 30078、d=aib-beat、中繼站只留最新)。內文 = 在線等級的在線狀態,
        用共用鑰 nip44 加密;好友不管幾位都只發這一筆。沒有公開席 / 還沒有共用鑰(沒給過任何好友)⇒ 不發。回成不成功。"""
        import seat_presence as SP
        now = time.time() if now is None else now
        pub = set((CR.read_profile() or {}).get("public") or [])
        mine_seats = [e for e in NK.sending_seats() if e["seat5"] in pub]
        gen, _given = GF.read_beat_given(self.root)
        if not mine_seats or not gen:
            return False
        rows = SP.presence([e["seat5"] for e in mine_seats], with_status=False)
        for r, e in zip(rows, mine_seats):
            r["cabin"] = e.get("cabin") or "main"
        owner = (NK.read_map().get("owner") or {}).get("npub")
        if not owner:
            return False
        # owner 放在加密內文裡:收的人解開後比對「宣告這把心跳鑰的主人」(#303 r2 A-M4:別人宣告我的心跳 npub 也冒不了)
        body = json.dumps({"v": 1, "at": int(now), "owner": owner, "seats": rows,
                           "power": CP.presence_power(self.root, BEAT_EVERY_S)}, ensure_ascii=False)
        share = _keys("beat-share-%d" % gen)
        ct = N.nip44_encrypt(share.secret_key(), share.public_key(), body, N.Nip44Version.V2)
        share = None
        _npub, sign = NK.beat_sign()
        ev = N.EventBuilder(N.Kind(CR.KIND_ROSTER), ct).tags(
            [N.Tag.identifier(BEAT_D), N.Tag.parse(["aib-gen", str(gen)])]).finalize(_keys(sign))
        c = await _client(CR.relays())
        try:
            out = await c.send_event(ev)
        except Exception:   # noqa: BLE001
            out = None
        finally:
            await c.shutdown()
        ok = out is not None and bool(out.success)
        audit(self.root, "beat-out", "-", "-", "ok" if ok else "fail")
        return ok

    async def poll_beats(self, now=None):
        """10-24 綠點:一次查全部好友(和我的其他機器)的最新心跳,每批 BEAT_BATCH 位;解得開的一輪寫一次 world.json。
        陌生 / 封鎖 / 不再是好友的不查(心跳鑰、補寄夾順手刪)。好友名片給的站只拿來查那位好友自己的心跳
        (#303 審 A-M2:不然好友塞自己的站就拿得到我的 IP 和全部好友的心跳 npub);內網 / 本機位址的站不連。回寫了幾位。"""
        now = time.time() if now is None else now
        keep = lambda o: not CR.is_blocked(o) and (CR.is_friend(o) or CR.is_mine(o))   # noqa: E731
        if _friends_readable():
            GF.drop_beat_keys(self.root, keep)
            GF.drop_resend(self.root, keep)
        rows = [(o, n, k, r) for o, n, k, r in GF.read_beat_keys(self.root) if keep(o)]
        by_npub = {}
        for o, n, k, _r in rows:
            by_npub.setdefault(n, []).append((o, k))      # 同一個心跳 npub 可能被好幾位宣告:解開後比內文 owner 才算數
        mine = CR.relays()
        plan = [(mine, sorted(by_npub))]                   # 我的站:查全部
        extra = {}
        for _o, n, _k, rl in rows:
            if len(by_npub[n]) > 1:
                continue        # #303 r3 A-Low:同一個 npub 不只一位宣告 ⇒ 誰的名片站都不拿來查它(M 不能藉此看到誰在查 V 的心跳);我的站照查
            for r in rl:
                if r not in mine and CR.public_relay_ok(r):
                    extra.setdefault(r, set()).add(n)
        plan += [([r], sorted(ns)) for r, ns in sorted(extra.items())[:BEAT_EXTRA_RELAYS]]
        latest = {}
        for relays, npubs in plan:
            for i in range(0, len(npubs), BEAT_BATCH):
                flt = N.Filter().authors([N.PublicKey.parse(n) for n in npubs[i:i + BEAT_BATCH]]) \
                    .kind(N.Kind(CR.KIND_ROSTER)).identifier(BEAT_D)
                try:
                    evs = await _fetch_all(relays, flt)
                except Exception:   # noqa: BLE001
                    continue
                for e in evs:
                    n = e.author().to_bech32()
                    if n in npubs and (n not in latest or e.created_at().as_secs() > latest[n].created_at().as_secs()):
                        latest[n] = e
        got, world = 0, []
        for n, e in latest.items():
            at = e.created_at().as_secs()
            if at > now + 300 or now - at > GF.PEERS_KEEP_S:
                continue
            owner, body = None, None
            for o, key in by_npub[n]:
                try:
                    k = N.Keys.parse(key)
                    b = json.loads(N.nip44_decrypt(k.secret_key(), k.public_key(), e.content()))
                except Exception:   # noqa: BLE001  換代前的舊心跳 / 壞內容 ⇒ 當沒看到
                    continue
                if isinstance(b, dict) and b.get("owner") == o:
                    owner, body = o, b
                    break
            if owner is None:
                continue
            cells = self._beat_cells(owner, body, at, now)
            if cells:
                world += cells
                got += 1
                if now - at < GF.BEAT_GREEN_S:          # 在線 ⇒ 補寄還沒到的信(跟收到在線狀態同一條)
                    due = getattr(self, "resend_owners", None)
                    if due is None:
                        due = self.resend_owners = set()
                    due.add(owner)
        GF.note_world_many(self.root, world)
        audit(self.root, "beat-in", "-", "-", str(got))
        return got

    def _beat_cells(self, owner, body, at, now):
        """一筆心跳 ⇒ world 要寫的格。presence_at 用本機收到時間(min(寄出, 現在);#303 審 B-M5:對方時鐘快就不會
        下線後還多亮),sent_at 記寄出原值(跟逐位在線狀態同一套比先後)。"""
        import seat_presence as SP
        rows = body.get("seats") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return []
        by_cab = {}
        for r in rows[:50]:
            if not isinstance(r, dict) or r.get("state") not in SP.STATES:
                continue
            seat, cab = str(r.get("seat") or ""), str(r.get("cabin") or "main")
            if NK.SEAT5_RE.match(seat) and NK.CABIN_RE.match(cab):
                by_cab.setdefault(cab, []).append({"seat": seat, "state": r["state"], "now": None, "step": None})
        cur = GF.read_world(self.root)
        out = []
        for cab, seats in by_cab.items():
            prev = cur.get("%s/%s" % (owner, cab)) or {}
            if GF.int_or0(prev.get("sent_at")) >= at and any(x.get("now") for x in prev.get("seats") or []):
                continue                                   # 已有一樣新、帶狀態列的(分享等級 status 那封),不蓋
            out.append((owner, cab, seats, int(min(at, now)), CP.clean_power(body.get("power")), at))
        return out

    def _remember_peer(self, to, res, source="sent"):
        """寄過信的人,回信照收(第 27.3 節):收件人的主人自動加進好友(source=sent)。
        寄到只有席 npub 的位址(沒有主人)⇒ 不加;收件人是自己或我的其他機器 ⇒ 不加。"""
        owner = (GF.parse_address(to) or {}).get("owner")
        me = (NK.read_map().get("owner") or {}).get("npub")
        if owner:
            GF.note_peer(self.root, owner, res.get("display_name"), res.get("country"), _peer_seat(res),
                         res.get("roster_at"), direction="out", claims=_claims(res))          # 第 29.6 節聯絡人快照
        if not owner or owner == me or CR.is_mine(owner) or CR.is_blocked(owner):
            return
        try:
            CR.add_friend(owner, res.get("display_name"), source=source)
        except CR.RosterError as err:
            sys.stderr.write("remember_peer:%s\n" % err)

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


def _good_relays(rl):
    out = []
    for r in rl if isinstance(rl, list) else ():
        if isinstance(r, str) and CR._relay_ok(r):
            try:
                N.RelayUrl.parse(r)
            except Exception:   # noqa: BLE001
                continue
            out.append(r)
    return out


def _friends_readable():
    """好友清單檔(好友 / 我的機器)沒有或讀得懂 ⇒ True;壞掉 ⇒ False(不拿空清單去換代 / 刪東西)。"""
    for kind in ("friends", "mine"):
        p = CR.friends_path(None, kind)
        if p.exists():
            try:
                if not isinstance(json.loads(p.read_text(encoding="utf-8")).get("friends"), list):
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


async def _send_to(target, own, gw, mine):
    """gw 發到對方收信站、mine 發到自己的;回收下 gw 的站。"""
    c = await _client(sorted(set(target) | set(own)))
    try:
        res = await c.send_event(gw, N.SendEventTarget.to([N.RelayUrl.parse(r) for r in target]))
        ok = sorted(str(u) for u in res.success)
        try:
            if mine is not None:                         # 補寄不另發給自己那份
                await c.send_event(mine, N.SendEventTarget.to([N.RelayUrl.parse(r) for r in own]))
        except Exception:   # noqa: BLE001  自己那份沒發到不影響寄出
            pass
    finally:
        await c.shutdown()
    return ok


# ── 收件(第 8.1 節)與回條(第 6.2、8.1 節第 9 步)──
BACKFILL = 2 * 86400 + 600       # gift wrap 時間戳往回亂數最多 2 天 ⇒ 補收要往回 2 天 10 分
KEEP = 7 * 86400                 # 去重紀錄保留 7 天
SEEN_KEY_LEN = 18                # 去重鍵只存「e:/m: + 前 16 個十六進位」(64 位元,撞號可略);全長 64 碼的 event id 一週就撐爆 64 KB
SEEN_MAX = 1500                  # 去重紀錄最多幾筆(短鍵每筆約 30 bytes ⇒ 約 45 KB,留在 atomic_json 64 KB 上限內);超過丟最舊的
RESUB_EVERY = GF.env_secs("AIB_CHAT_RESUB_EVERY", 30)   # 秒;席的金鑰有變(新席、封存)就重新訂閱(壞值退回預設、最少 1 秒)
RECEIPT_STAGES_OUT = ("queued", "delivered", "held", "refused", "expired", "still_waiting")   # read 另走一批一次(1.8)
RECEIPT_STAGES_IN = RECEIPT_STAGES_OUT + ("read",)
# 拒收原因碼(回條 aib-reason 標籤;寄件端 dm-ctl 照碼翻成一句話)。只放碼,不放內文
REFUSE_REASONS = ("not_friend", "unverified")
READ_BATCH_S = float(os.environ.get("AIB_CHAT_READ_BATCH") or 30)   # 秒;同一寄件人同一席這段時間內讀的合成一張(第 10 節)
REFUSE_EVERY_S = float(os.environ.get("AIB_CHAT_REFUSE_EVERY") or 86400)   # 秒;同一寄件 npub 這段時間最多回 1 張拒收(第 27.4 節)
PRESENCE_TICK_S = float(os.environ.get("AIB_CHAT_PRESENCE_TICK") or 60)     # 秒;多久看一次要不要送在線狀態(第 30.3 節)
PRESENCE_EVERY_S = float(os.environ.get("AIB_CHAT_PRESENCE_EVERY") or 900)  # 沒變也每 15 分鐘送一次
PRESENCE_GAP_S = float(os.environ.get("AIB_CHAT_PRESENCE_GAP") or 120)      # 同一位對方最少間隔
ASK_ANSWER_GAP_S = float(os.environ.get("AIB_CHAT_ASK_GAP") or 60)          # 回「在不在」:同一位最少間隔
REPLY_WINDOW_S = float(os.environ.get("AIB_CHAT_REPLY_WINDOW") or 600)     # 回我剛寄的信:寄出後這段時間內當好友收
ECHO_GAP_S = float(os.environ.get("AIB_CHAT_ECHO_GAP") or 10)              # 回音小幫手:同一位主人最少間隔
ECHO_PER_MIN = int(os.environ.get("AIB_CHAT_ECHO_PER_MIN") or 120)          # 回音小幫手:整台每分鐘最多回幾封
ECHO_TEXT = {"zh-TW": "收到了,線路是通的。", "en": "Got it, the line works.",
             "ja": "届きました。回線はつながっています。", "ko": "받았습니다. 연결이 잘 됩니다."}
ASK_OLD_MAX = 20                                                             # 刷新時最多問幾位還沒有心跳的舊版好友
BEAT_D = "aib-beat"
BEAT_EVERY_S = float(os.environ.get("AIB_CHAT_BEAT_EVERY") or 300)          # 10-24 綠點:心跳多久發一筆
BEAT_POLL_S = float(os.environ.get("AIB_CHAT_BEAT_POLL") or 300)            # 沒人刷新時多久查一次好友心跳
BEAT_BATCH = 500                                                             # 一次查幾位作者
BEAT_EXTRA_RELAYS = 8                                                        # 好友名片給的站最多另查幾站(各只查自己那幾位)
PRESENCE_MAX_TARGETS = 50
CARD_EXTRA_PER_ROUND = 20                    # 前 50 位以外,一輪最多另送幾張名片
UNCONFIRMED_AFTER_S = float(os.environ.get("AIB_CHAT_UNCONFIRMED_AFTER") or 86400)  # 交出多久沒回條就標「未確認」
UNCONFIRMED_TICK_S = 600

ROOM_GAP_S = float(os.environ.get("AIB_CHAT_ROOM_GAP") or 600)          # 房間快照同一位最少間隔(第 34 節)
REVOKE_BACKOFF_MAX_S = 3600                                                 # 撤回送不到(對方名冊沒了)退避上限;一直試到送到為止


def revoke_wait(n):
    """撤回第 n 次沒送到後要等幾秒:GAP·2^(n-1),封頂 1 小時;指數先封頂,n 再大也不溢位。"""
    return 0 if n <= 0 else min(PRESENCE_GAP_S * 2 ** min(n - 1, 20), REVOKE_BACKOFF_MAX_S)


def sent_at(rumor):
    """rumor 的寄出時間原值(不亂數)。同一位寄件人的快照 / 在線 / 撤回之間比先後只用這個:同一個時鐘,快慢一致。
    不夾成收到時間 —— 夾了就變成比到達順序,亂序跨秒到時舊快照會復活(#318 r3,@abb89 定案)。
    寄件端時鐘快很多時,未來時間的撤回靠收到時間過期(GF.REVOKE_BACKFILL_S),不會永遠卡。"""
    return rumor.created_at().as_secs()
ORPHAN_KEEP_S = float(os.environ.get("AIB_CHAT_ORPHAN_KEEP") or 60)   # 秒;查不到寄出帳的回條暫存多久(第 27.8 節)
ORPHAN_MAX = 500
REFUSED_MAX = 200                           # refused.json 最多記幾位(第 27.5 節)
REFUSED_KEEP_S = 30 * 86400                 # 30 天沒再來就清


def _tagmap(rumor):
    out = {}
    for t in rumor.tags():
        v = t.to_vec()
        if len(v) >= 2 and v[0] not in out:
            out[v[0]] = v[1]
    return out


def _clean_note(text):
    """好友申請附的一句話:去掉控制字元、換行改空白、最多 REQUEST_NOTE_MAX 字。只給人看(艦長室),不進 AI。"""
    t = "".join(" " if ch in "\r\n\t" else ch for ch in str(text or "") if ch in "\r\n\t" or ord(ch) >= 32)
    return t.strip()[:GF.REQUEST_NOTE_MAX]


def _claims(res):
    """名冊上宣告的社群與 NIP-05(第 32 節);驗證由 chat_verify.verify_round 另做。"""
    out = {"nip05": (res or {}).get("nip05")}          # 社群不在名冊上(只給好友,走 card)
    if (res or {}).get("aib"):
        out["aib"] = res["aib"]                          # 第 37.5 節:對方版本(名冊上的 aib_version)
    if isinstance((res or {}).get("roster_seats"), list) and (res or {}).get("roster_at"):
        out["roster_seats"] = res["roster_seats"]         # 名冊目前公開的席(較新的名冊才會換掉快照的 seats)
    return out


VERIFY_TICK_S = float(os.environ.get("AIB_CHAT_VERIFY_TICK") or 300)    # 多久看一次有沒有要驗的證明(第 32 節)


def _roster_seats(rec):
    """名冊(合併後)目前公開的每一席 ⇒ 聯絡人快照的 seats(10-18 @74e8b:好友換艙後通訊錄還留著死席、排第一)。"""
    out = []
    for cab in rec.get("cabins") or []:
        for s in cab.get("seats") or []:
            if s.get("seat") and s.get("npub"):
                out.append({"cabin": cab.get("cabin"), "seat": s["seat"], "npub": s["npub"], "name": s.get("name"),
                            "role": s.get("role")})
    return out


async def roster_of(owner_npub, relays, root):
    """aib chat contacts --refresh:立刻重抓一位主人的名冊(含機器清單合併),回聯絡人快照要的欄位。"""
    rc, rec, at = await _owner_roster(owner_npub, relays, root)
    if rc != 0:
        return rc, rec
    return 0, {"ok": True, "owner": owner_npub, "display_name": rec.get("display_name"), "country": rec.get("country"),
               "roster_at": at, "aib": rec.get("aib_version"), "nip05": rec.get("nip05"),
               "roster_seats": _roster_seats(rec)}


def _peer_seat(res):
    """resolve 的結果 ⇒ 聯絡人快照裡的一席(只放名冊上的公開欄位)。"""
    s = (res or {}).get("seat") or {}
    if not s.get("npub"):
        return None
    return {"cabin": res.get("cabin"), "seat": s.get("seat") or s.get("code"), "npub": s.get("npub"),
            "name": s.get("name"), "role": s.get("role")}


def note_refused(root, rec, parsed, why):
    """誰被拒收過(第 27.5 節):<bridge root>/refused.json,每位寄件主人一筆(沒有主人就記席 npub)。
    **不存內文**;名字與國別只記驗得到的(名冊上的),自稱的不記。最多 REFUSED_MAX 筆,30 天沒再來就清。"""
    p = Path(root) / "refused.json"
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
        rows = cur.get("refused") if isinstance(cur, dict) else None
        rows = rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        rows = []
    now = int(time.time())
    who = (parsed or {}).get("owner") or rec["from_npub"]
    row = next((r for r in rows if isinstance(r, dict) and r.get("who") == who), None)
    if row is None:
        row = {"who": who, "count": 0, "first_at": now}
        rows.append(row)
    row.update(count=int(row.get("count") or 0) + 1, last_at=now, reason=why, from_addr=rec.get("from_addr"),
               owner=bool((parsed or {}).get("owner")))
    for k in ("display_name", "country"):
        if rec.get("verified") and rec.get(k):
            row[k] = rec[k]
    rows = [r for r in rows if isinstance(r, dict) and now - int(r.get("last_at") or 0) < REFUSED_KEEP_S]
    rows = sorted(rows, key=lambda r: -int(r.get("last_at") or 0))[:REFUSED_MAX]
    write_json(str(p), {"v": 1, "refused": rows})
    os.chmod(str(p), 0o600)


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
        if any(len(k) > SEEN_KEY_LEN for k in self.st["seen"]):   # 舊版全長鍵 ⇒ 換短鍵(同一短鍵取較新的時間)
            short = {}
            for k, v in self.st["seen"].items():
                sk = self._k(k)
                short[sk] = max(v, short.get(sk, 0))
            self.st["seen"] = short
        self.st.setdefault("last_at", 0)
        self.st.setdefault("delivery_sent", {})
        self.st.setdefault("read_pending", {})      # "<寄件人 npub>|<本席金鑰>" ⇒ {"refs": [...], "first": t}
        self.st.setdefault("refused_sent", {})      # 寄件 npub ⇒ 上次回拒收回條的時間(第 27.4 節)

    def save(self):
        cut = time.time() - KEEP
        seen = {k: v for k, v in self.st["seen"].items() if v >= cut}
        if len(seen) > SEEN_MAX:          # 收得太兇(測試、好友多):寧可丟最舊的去重紀錄,也不要存不進去讓收信整條斷掉
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-SEEN_MAX:])
        self.st["seen"] = seen
        rcut = time.time() - REFUSE_EVERY_S
        self.st["refused_sent"] = {k: v for k, v in self.st["refused_sent"].items() if v >= rcut}
        write_json(str(self.p), self.st)
        os.chmod(str(self.p), 0o600)

    @staticmethod
    def _k(key):
        return key[:SEEN_KEY_LEN]

    def seen(self, key):
        return self._k(key) in self.st["seen"]

    def mark(self, key):
        self.st["seen"][self._k(key)] = int(time.time())

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
        self.echo_last, self.echo_win = {}, []
        self.echo_ids = collections.deque(maxlen=2000)     # 我回過的回音 msgid:回這些的不再回(#306 審 B-M1)
        self.client = None
        self.orphans = []      # 查不到寄出帳的回條:[(到期, ref, stage, tags, from_npub)](第 27.8 節)
        # 10-20 @abb89(會員清單 E 段):收信連續失敗 ⇒ 警報。Pro 13 從 10-19 11:20 起每 5 秒重來、20 小時沒人發現。
        #   n = 連續幾次「一下就斷」;一段 session 撐過 RECV_OK_S 才算正常、歸零。寫進 worker-status.json,aib chat status 印 ⚠。
        self.fail = {"n": 0, "since": None, "why": None}

    def _recv_failed(self, why):
        f = self.fail
        f["n"] += 1
        f["why"] = why
        f["since"] = f["since"] or int(time.time())

    def _recv_ok(self):
        self.fail = {"n": 0, "since": None, "why": None}

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
            t0 = time.time()
            try:
                await self._session(stop, seats)
                if time.time() - t0 >= RECV_OK_S:
                    self._recv_ok()
            except Exception as err:   # noqa: BLE001  斷線、站掛掉:5 秒後重來(下一輪會補收)
                if time.time() - t0 >= RECV_OK_S:
                    self._recv_ok()                         # 撐了一陣子才斷 = 普通斷線,不是連續失敗
                self._recv_failed(type(err).__name__)
                sys.stderr.write("recv:%s\n" % type(err).__name__)
                await _sleep_or_stop(stop, 5)

    async def _session(self, stop, seats):
        c = await _client(CR.relays())
        self.client = c
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
            self.client = None
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
            refs = [v[1] for v in (t.to_vec() for t in rumor.tags()) if len(v) >= 2 and v[0] == "aib-ref"]
            self.on_receipt(tags, from_npub, refs)
            return self.led.save()
        await self.on_dm(rumor, tags, from_npub, entry)
        self.led.save()

    def on_receipt(self, tags, from_npub, refs=None):
        """回條:每一個 aib-ref 都要是「我寄給這個 npub 的信」才收(已讀一張可帶多個 ref,1.8)。
        查不到寄出帳的先暫存 ORPHAN_KEEP_S 秒再比(第 27.8 節:寄出帳可能比回條晚寫);冒充的直接丟。"""
        stage = tags.get("aib-receipt", "")
        if stage not in RECEIPT_STAGES_IN:     # 舊版收到不認得的(例 still_waiting)就略過,不報錯
            return
        for ref in (refs or [tags.get("aib-ref", "")])[:200]:
            if not GF.MSGID_RE.match(ref or ""):
                continue
            if self._apply_receipt(ref, stage, tags, from_npub) == "missing" and ORPHAN_KEEP_S > 0 \
                    and len(self.orphans) < ORPHAN_MAX:
                self.orphans.append((time.time() + ORPHAN_KEEP_S, ref, stage, tags, from_npub))

    def retry_orphans(self):
        """每輪重比一次暫存的回條;比到了照收,過期才丟(記稽核 receipt-orphan)。"""
        keep, now = [], time.time()
        for item in self.orphans:
            until, ref, stage, tags, from_npub = item
            got = self._apply_receipt(ref, stage, tags, from_npub)
            if got != "missing":
                continue
            if now < until:
                keep.append(item)
            else:
                audit(self.root, "receipt-orphan", ref, from_npub, stage)
        self.orphans = keep

    def _apply_receipt(self, ref, stage, tags, from_npub):
        """回 ok / missing(還沒有寄出帳)/ forged(寄出帳在,但簽的人不是我寄給的那席)。"""
        try:
            sent = json.loads((self.root / "sent" / (ref + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "missing"
        if sent.get("to") != from_npub:
            sys.stderr.write("receipt_wrong_author\n")   # 不是我寄給的那席簽的 ⇒ 冒充,不收
            return "forged"
        r = {"v": 1, "at": int(time.time()), "stage": stage}
        pos = tags.get("aib-queue-pos", "")
        if stage == "queued" and pos.isdigit() and int(pos) > 0:
            r["pos"] = int(pos)
        if stage == "refused" and tags.get("aib-reason") in REFUSE_REASONS:
            r["detail"] = tags["aib-reason"]
        # 10-21 回條語意:只收白名單裡的碼,只拿來顯示;未知值當沒帶
        if stage == "held" and tags.get("aib-reason") in GF.HELD_REASONS:
            r["detail"] = tags["aib-reason"]
        days = tags.get("aib-days", "")
        if stage == "still_waiting" and days.isdigit() and 1 <= int(days) <= GF.WAIT_DAYS_MAX:
            r["days"] = int(days)
        if stage == "read" and tags.get("aib-read-by") == "human":
            r["by"] = "human"
        GF.append_receipt(self.root, ref, r)
        audit(self.root, "receipt-in", ref, from_npub, stage)
        if stage in GF.PEER_OK_STAGES and sent.get("owner"):
            GF.note_accepted(self.root, sent["owner"])     # 回條 7 天會清,「收過」另記(#357 r1 High)
        if stage in ("queued", "delivered"):              # 對方收下了(進佇列 / 申請送到)
            GF.note_first(self.root, "delivered")
        req = {"held": "pending", "delivered": "accepted", "refused": "rejected"}.get(stage)
        if sent.get("op") == "request" and req and sent.get("owner"):
            GF.note_peer(self.root, sent["owner"], request=req)        # 第 29.4 節:通訊錄顯示申請推到哪
        return "ok"

    async def on_room(self, rumor, tags, from_npub):
        """房間快照(第 34 節):只收驗得到名冊的好友 / 我的機器;解壓、再清一次、存 rooms/<主人>.json。不進佇列、不回條。"""
        import room_snapshot as RS
        if tags.get("aib-revoke") == "1":
            return await self._on_revoke(rumor, tags, from_npub, "space")
        parsed = GF.parse_address(tags.get("aib-from") or "") or {}
        owner = parsed.get("owner")
        allowed = bool(owner) and not CR.is_blocked(owner) and (CR.is_mine(owner) or CR.is_friend(owner))
        if not allowed:
            return
        rc, res = await self.sender._resolve(tags.get("aib-from"))
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        sent, now = sent_at(rumor), int(time.time())            # 寄出時間原值(NIP-59 外層會亂數往回,rumor 這個不會)
        cur = GF.read_room(self.root, owner) or {}
        cur_sent = GF.int_or0(cur.get("sent_at")) if now - GF.int_or0(cur.get("at")) < GF.ROOM_FRESH_S else 0
        if sent <= GF.read_revoked(self.root, owner).get("space", 0) or sent < cur_sent:
            audit(self.root, "space-in", "-", from_npub, "stale")   # 撤回前寄的 / 比手上舊 ⇒ 補收的舊信,丟掉
            return
        snap = RS.decode(rumor.content())
        if snap is None:
            audit(self.root, "space-in", "-", from_npub, "bad")
            return
        GF.write_room(self.root, owner, snap, sent_at=sent)
        audit(self.root, "space-in", "-", from_npub, "room" if snap.get("rooms") else "island")

    async def _on_revoke(self, rumor, tags, from_npub, kind):
        """對方撤回空間 / 在線分享:驗得到名冊席(不要求還是好友:刪好友、封鎖後送來的也要收)
        ⇒ 刪掉他的快照 / 在線格。只動這位主人自己的資料。
        比寄出時間:只刪撤回之前寄的(舊撤回晚到不准刪重開的分享);記下撤回時間,之後補收的舊快照 / 在線丟掉。"""
        parsed = GF.parse_address(tags.get("aib-from") or "") or {}
        owner = parsed.get("owner")
        if not owner:
            return
        held = (GF.read_room(self.root, owner) if kind == "space" else
                any(v.get("owner") == owner for v in GF.read_world(self.root).values()))
        if not held and not (CR.is_friend(owner) or CR.is_mine(owner)):
            return                                              # 手上沒他的資料、也不是好友 ⇒ 不查名冊、不寫檔(陌生人灌不滿帳)
        rc, res = await self.sender._resolve(tags.get("aib-from"))
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        at = sent_at(rumor)
        if kind == "space":
            GF.drop_room(self.root, owner, upto=at)
        else:
            GF.drop_world_owner(self.root, owner, upto=at)
        try:                                                    # 先刪再記帳;記帳失敗(壞檔、太大)不往上丟、不斷線
            GF.note_revoked(self.root, owner, kind, at)
        except Exception:   # noqa: BLE001
            audit(self.root, "revoke-in", "-", from_npub, "ledger_failed")
        audit(self.root, "revoke-in", "-", from_npub, kind)

    async def on_offer(self, rumor, tags, from_npub):
        """掛單(第 36.2 節):只收驗得到名冊的好友 / 我的機器;買單 ⇒ 賣方核心決定並回覆;回覆 ⇒ 買方記帳。不進佇列。"""
        import chat_offers as CO
        parsed = GF.parse_address(tags.get("aib-from") or "") or {}
        owner = parsed.get("owner")
        if not owner or CR.is_blocked(owner) or not (CR.is_mine(owner) or CR.is_friend(owner)):
            return
        rc, res = await self.sender._resolve(tags.get("aib-from"))
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        msg = CO.parse(rumor.content())
        if msg is None:
            audit(self.root, "offer-in", "-", from_npub, "bad")
            return
        if msg["op"] == "buy":
            reply = CO.seller_decide(None, owner, msg)
            audit(self.root, "offer-in", "-", from_npub, "buy:" + (reply["op"] if reply else "dropped"))
            if reply:
                await self.sender._send_owner_msg(owner, "offer", json.dumps(reply, ensure_ascii=False))
            return
        got = CO.buyer_reply(None, owner, msg)
        audit(self.root, "offer-in", "-", from_npub, (got or {}).get("status") or "ignored")

    async def on_visit(self, rumor, tags, from_npub):
        """參觀請求 / 開放 / 拒絕(第二波 #1,chat_visit):只收驗得到名冊的好友 / 我的機器;記帳,不進佇列、不回條。"""
        import chat_visit as CVI
        parsed = GF.parse_address(tags.get("aib-from") or "") or {}
        owner = parsed.get("owner")
        if not owner or CR.is_blocked(owner) or not (CR.is_mine(owner) or CR.is_friend(owner)):
            return
        rc, res = await self.sender._resolve(tags.get("aib-from"))
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        msg = CVI.parse(rumor.content())
        if msg is None:
            audit(self.root, "visit-in", "-", from_npub, "bad")
            return
        got = CVI.on_incoming(None, owner, msg)
        audit(self.root, "visit-in", "-", from_npub, "%s:%s" % (msg["op"], got or "ignored"))

    async def on_card(self, rumor, tags, from_npub):
        """名片(第 32 節):好友 / 我的其他機器私下送來的社群帳號。陌生、封鎖 ⇒ 靜默丟;不進佇列、不回條。"""
        parsed = GF.parse_address(tags.get("aib-from") or "") or {}
        owner = parsed.get("owner")
        known = bool(owner) and (CR.is_friend(owner) or CR.is_mine(owner))
        if not known or CR.is_blocked(owner):          # 跟 presence 同一條規則(寫法不同:刀測錨點要唯一)
            return
        rc, res = await self.sender._resolve(tags.get("aib-from"))
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        try:
            body = json.loads(rumor.content())
        except ValueError:
            return
        socials = CR.clean_socials(body.get("socials") if isinstance(body, dict) else None)
        GF.note_peer(self.root, owner, claims={"socials": socials})
        b = body.get("beat") if isinstance(body, dict) else None
        if isinstance(b, dict):                          # 10-24 綠點:對方給的心跳鑰;站先驗(壞網址會讓整批查詢掛掉)
            GF.note_beat_key(self.root, owner, b.get("npub"), b.get("key"), b.get("gen"), _good_relays(b.get("relays")))
        audit(self.root, "card-in", "-", from_npub, str(len(socials)))

    async def on_presence(self, rumor, tags, from_npub):
        """在線狀態(第 30.4 節):只收驗得到名冊、而且是好友 / 我的其他機器送來的;其他靜默丟。
        不進佇列、不回條、不記收件帳;只寫 world.json。"""
        import seat_presence as SP
        if tags.get("aib-revoke") == "1":
            return await self._on_revoke(rumor, tags, from_npub, "share")
        from_addr = tags.get("aib-from") or ""
        parsed = GF.parse_address(from_addr) or {}
        owner = parsed.get("owner")
        if not owner or CR.is_blocked(owner) or not (CR.is_friend(owner) or CR.is_mine(owner)):
            return
        rc, res = await self.sender._resolve(from_addr)
        if rc != 0 or res["seat"]["npub"] != from_npub:
            return
        sent = sent_at(rumor)
        if sent <= GF.read_revoked(self.root, owner).get("share", 0):
            return                                              # 撤回前寄的舊在線,丟掉
        try:
            body = json.loads(rumor.content())
            rows = body.get("seats") if isinstance(body, dict) else None
        except ValueError:
            return
        if not isinstance(rows, list):
            return
        by_cab = {}
        for r in rows[:50]:
            if not isinstance(r, dict) or r.get("state") not in SP.STATES:
                continue
            seat, cab = str(r.get("seat") or ""), str(r.get("cabin") or "main")
            if not (NK.SEAT5_RE.match(seat) and NK.CABIN_RE.match(cab)):
                continue
            step = r.get("step") if isinstance(r.get("step"), str) and re.fullmatch(r"\d{1,3}/\d{1,3}", r["step"]) else None
            by_cab.setdefault(cab, []).append({"seat": seat, "state": r["state"], "now": SP.clean_now(r.get("now")),
                                                "step": step})
        power = CP.clean_power(body.get("power"))
        for cab, seats in by_cab.items():
            GF.note_world(self.root, owner, cab, seats, power=power, sent_at=sent)
        due = getattr(self.sender, "resend_owners", None)              # 10-24:他在線 ⇒ 補寄還沒到的信
        if due is None:
            due = self.sender.resend_owners = set()
        due.add(owner)
        # 對方送在線狀態來 = 他把我當好友了 ⇒ 這個工人第一次聽到他時,補送一次名片(先前送的可能在他加我好友前被丟掉)
        heard = getattr(self, "heard", None)
        if heard is None:
            heard = self.heard = set()
        if owner not in heard:
            heard.add(owner)
            due = getattr(self.sender, "card_due", None)
            if due is None:
                due = self.sender.card_due = set()
            due.add(owner)
        if tags.get("aib-ask") == "1":                  # 對方在問「在不在」⇒ 下一輪立刻回他一份(同一位 ASK_ANSWER_GAP_S 一次)
            asked = getattr(self, "asked", None)
            if asked is None:
                asked = self.asked = {}
            if time.time() - asked.get(owner, 0) >= ASK_ANSWER_GAP_S:
                asked[owner] = time.time()
                (getattr(self.sender, "p_last", None) or {}).pop(owner, None)
                due = getattr(self.sender, "answer_to", None)
                if due is None:
                    due = self.sender.answer_to = set()
                due.add(owner)
                self.sender.answer_now = True
        audit(self.root, "presence-in", "-", from_npub, str(len(rows)))

    async def on_request(self, rec, parsed, peer, msgid, from_npub):
        """好友申請(第 29.4 節):不進 AI 佇列。本人 / 好友 ⇒ 直接 delivered;驗不到 ⇒ 拒收(限量);
        驗得到的陌生人 ⇒ 記 requests.json(同一主人 24 小時 1 則,多的靜默)⇒ 回 held(等主人同意)。"""
        if rec["class"] in ("own", "friend"):
            audit(self.root, "in", msgid, from_npub, "request:known")
            return await self.send_receipt(msgid, "delivered")
        if not rec["verified"]:
            note_refused(self.root, rec, parsed, "unverified")
            last = self.led.st["refused_sent"].get(from_npub) or 0
            if time.time() - last < REFUSE_EVERY_S:            # 跟 dm 拒收同一個限量(寫法不同:刀測錨點要唯一)
                audit(self.root, "in", msgid, from_npub, "request:unverified:quiet")
                return
            self.led.st["refused_sent"][from_npub] = int(time.time())
            audit(self.root, "in", msgid, from_npub, "request:unverified")
            return await self.send_receipt(msgid, "refused", reason="unverified")
        m = GF.INVITE_NOTE_RE.match(rec.get("content") or "")
        inv = m and not CR.is_blocked(parsed["owner"]) and GF.take_invite(self.root, m.group(1))
        if inv:
            # 10-24 第 11 條:帶我發的邀請碼 ⇒ 直接加好友、回 delivered(對方那邊 join 時已把我加好)
            GF.note_peer(self.root, parsed["owner"], peer.get("display_name"), peer.get("country"), _peer_seat(peer),
                         peer.get("roster_at"), claims=_claims(peer))
            try:
                CR.add_friend(parsed["owner"], GF.clean_display_name(rec.get("display_name") or "") or None, source="invite")
            except CR.RosterError:
                GF.refund_invite(self.root, m.group(1))         # 沒加成 ⇒ 次數補回,落回一般申請(下面記帳 + 回 held)
                inv = None
            if inv:
                audit(self.root, "in", msgid, from_npub, "request:invite")
                keys = await asyncio.get_running_loop().run_in_executor(   # PBKDF2 100 萬次不卡事件迴圈
                    None, GF.invite_keys, m.group(1), GF.invite_day(inv["created_at"]))
                await self._spend_invite(*keys)
                return await self.send_receipt(msgid, "delivered")
        row = {"owner": parsed["owner"], "display_name": rec.get("display_name"), "country": rec.get("country"),
               "from_addr": rec.get("from_addr"), "note": rec["content"], "msgid": msgid}
        GF.note_peer(self.root, parsed["owner"], peer.get("display_name"), peer.get("country"), _peer_seat(peer),
                     peer.get("roster_at"), claims=_claims(peer))
        if not GF.add_request(self.root, row, every=REFUSE_EVERY_S):
            audit(self.root, "in", msgid, from_npub, "request:quiet")
            return
        audit(self.root, "in", msgid, from_npub, "request")
        return await self.send_receipt(msgid, "held")

    async def _spend_invite(self, h, key):
        """用掉的碼:中繼站上同一個 d 換成 revoked(可取代事件,只留新的;同樣加密 + HMAC,沒有碼的人冒不了);
        送不出去也沒關係,本機帳已扣掉次數。"""
        try:
            keys, _d = _roster_signer()
            ev = _invite_event(keys, h, GF.invite_seal(key, keys.public_key().to_bech32(), {"revoked": True}),
                               int(time.time()) + GF.INVITE_TTL_S)
            keys = None
            await _send_all(CR.relays(), [ev])
        except Exception:   # noqa: BLE001
            pass

    async def on_dm(self, rumor, tags, from_npub, entry):
        msgid = tags.get("aib-msgid") or ""
        if not GF.MSGID_RE.match(msgid):
            msgid = rumor.id().to_hex()[:32] if rumor.id() else None   # 非 aib 寄件人:用 rumor id 當編號
        kind = GF.inbound_kind(tags)
        if not msgid:
            return
        if kind not in ("presence", "card", "space"):      # 這三種重收無害(寄出時間 / 內容比過),不佔去重名額(#303 審 A-M6)
            if self.led.seen("m:" + msgid):
                return
            self.led.mark("m:" + msgid)
        if kind == "presence":
            return await self.on_presence(rumor, tags, from_npub)
        if kind == "card":
            return await self.on_card(rumor, tags, from_npub)
        if kind == "space":
            return await self.on_room(rumor, tags, from_npub)
        if kind == "offer":
            return await self.on_offer(rumor, tags, from_npub)
        if kind == "visit":
            return await self.on_visit(rumor, tags, from_npub)
        if kind == "unknown":
            # 對方版本比這台新:不進 AI 框、不回條;記一行好查(型別名截短,不記內容)
            audit(self.root, "in", msgid, from_npub, "unknown-type:" + str(tags.get("aib-type"))[:32])
            return
        is_aib = tags.get("aib-v") == "1"
        self.led.received(msgid, {"v": 1, "from": from_npub, "key": entry["key"], "seat": entry["seat5"],
                                  "aib": is_aib, "lang": tags.get("aib-lang"), "at": int(time.time())})
        content = rumor.content()
        is_request = tags.get("aib-type") == "friend-request"
        who = (GF.parse_address(tags.get("aib-from") or "") or {}).get("owner") or from_npub
        if CR.is_blocked(who) or CR.is_blocked(from_npub):
            audit(self.root, "in", msgid, from_npub, "blocked")        # 第 29.3 節:封鎖 ⇒ 靜默丟,不回條、不記拒收
            return
        if is_request:
            content = _clean_note(content)
        elif not content or len(content) > GF.CONTENT_MAX:
            return await self.send_receipt(msgid, "refused")
        exp = tags.get("aib-expires", "")
        expires = int(exp) if exp.isdigit() else None
        if expires and expires < time.time():
            return await self.send_receipt(msgid, "expired")          # 第 8.4 節:補收到時已過期 ⇒ 不投遞
        rec = {"v": 1, "msgid": msgid, "received_at": int(time.time()), "to_cabin": entry.get("cabin") or "main",
               "to_seat": entry["seat5"], "from_npub": from_npub, "verified": False, "class": "stranger",
               "content": content}
        sa = tags.get("aib-sent-at", "")
        # 補寄的 rumor 時間是補寄當下;原寄出時間在 aib-sent-at(#303 r2 B-L1)。只收不比 rumor 晚的,不然當沒帶
        rec["sent_at"] = int(sa) if sa.isdigit() and int(sa) <= sent_at(rumor) else sent_at(rumor)
        phone_cmd = None
        if not is_aib and not is_request:
            import chat_pair as CP                              # 第 33 節:主人手機(一般 Nostr App,沒有 aib 標籤)
            if CP.offer_code(self.root, from_npub, content):
                audit(self.root, "in", msgid, from_npub, "phone-pair-code")   # 只記成候選;本人在電腦上按 Touch ID 才配
                return
            if from_npub in await self._signed_phones():           # @abb89 F1:信任錨是主人簽的清單,不是 paired.json
                why = CP.accept_event(self.root, rumor.id().to_hex() if rumor.id() else msgid, rumor.created_at().as_secs())
                if why:
                    audit(self.root, "in", msgid, from_npub, "phone-refused:" + why)   # 防重播 / 時間差
                    return
                kind, text = CP.classify(content)
                rec.update(verified=True, display_name="主人手機", content=text, kind="order" if kind == "command" else "other")
                rec["class"] = "paired"
                if kind == "command":
                    phone_cmd = text
                    CP.log_command(self.root, from_npub, msgid, text)
        from_addr = tags.get("aib-from")
        parsed = GF.parse_address(from_addr) if from_addr else None
        if parsed is not None:
            rec["from_addr"] = from_addr
        peer = None
        if parsed and "owner" in parsed:
            rc, res = await self.sender._resolve(from_addr)
            # 第 8.1 節第 4 步:rumor 作者要就是名冊上那一席 ⇒ 已驗證;否則一律未驗證、當陌生
            if rc == 0 and res["seat"]["npub"] == from_npub:
                peer = res
                rec["verified"] = True
                own = (NK.read_map().get("owner") or {}).get("npub")
                if parsed["owner"] == own or CR.is_mine(parsed["owner"]):
                    rec["class"] = "own"                                   # 同一把主人金鑰,或綁過的我的其他機器(27.2)
                elif CR.is_friend(parsed["owner"]):
                    rec["class"] = "friend"
                for k_in, k_out in (("display_name", "display_name"), ("country", "country")):
                    if res.get(k_in):
                        rec[k_out] = res[k_in]
                if res["seat"].get("name"):
                    rec["persona_name"] = res["seat"]["name"]
        if is_request:
            return await self.on_request(rec, parsed, peer, msgid, from_npub)    # 好友申請不受回音影響(#306 審 A-M10)
        if rec["class"] == "stranger" and rec["verified"] \
                and self._replies_my_sent(tags.get("aib-reply-to"), from_npub, rec["to_seat"]):
            # 10-24 真站實測:對方秒回時,回信可能比我這邊「寄出成功 ⇒ 記成好友」還早到 ⇒ 回我剛寄的那封就當好友收
            rec["class"] = "friend"
        if rec["class"] == "stranger" and GF.echo_on(self.root):
            # 回音只對陌生人(好友照一般流程,#306 審 A-M10);對方也是回音 / 回我回音的那封 ⇒ 不回(B-M1:不無限來回)
            if tags.get("aib-echo") or tags.get("aib-reply-to") in self.echo_ids:
                audit(self.root, "in", msgid, from_npub, "echo:loop")
                return
            return await self.on_echo(rec, peer, msgid, from_npub, entry, tags.get("aib-lang"))
        if tags.get("aib-echo") == "1":
            rec["echo"] = True                       # 對方回音小幫手回的:只進艦長室、不交給 AI(#306 審 B-M1)
        if rec["class"] == "stranger":
            # 維護者 10-16 12:1x:加入好友之後才收、才有已讀;不然拒收(不進佇列、不進待審、AI 看不到)
            why = "not_friend" if rec["verified"] else "unverified"
            note_refused(self.root, rec, parsed, why)
            last = self.led.st["refused_sent"].get(from_npub)
            if last and time.time() - last < REFUSE_EVERY_S:
                # 第 27.4 節:同一寄件人限量回拒收,免得被人狂寄時跟著狂回、被公開站封
                audit(self.root, "in", msgid, from_npub, "refused:%s:quiet" % why)
                return
            self.led.st["refused_sent"][from_npub] = int(time.time())
            audit(self.root, "in", msgid, from_npub, "refused:" + why)
            return await self.send_receipt(msgid, "refused", reason=why)
        for k_tag, k in (("aib-priority", "priority"), ("aib-thread", "thread"), ("aib-kind", "kind"),
                         ("aib-lang", "lang"), ("aib-tz", "tz"), ("aib-author", "author")):
            if tags.get(k_tag):
                rec[k] = tags[k_tag]
        if rec.get("priority") not in GF.PRIORITIES:
            rec.pop("priority", None)
        if rec.get("author") not in GF.AUTHORS:          # 第 35 節第 2 點:本人 / AI 擬稿 / AI 自動;其他值不轉
            rec.pop("author", None)
        if expires:
            rec["expires"] = expires
        if GF.MSGID_RE.match(tags.get("aib-reply-to", "")):
            rec["reply_to"] = tags["aib-reply-to"]
        try:
            GF.write_inbox(self.root, rec)
            audit(self.root, "in", msgid, from_npub, "inbox")
            GF.note_first(self.root, "in")
            if phone_cmd is not None:                          # 第 33.6 節:回手機一句純文字(手機 App 看得懂)
                await self._reply_plain(entry, from_npub, "aib:收到指令,已交給 @%s:%s" % (entry["seat5"], phone_cmd[:40]))
            if peer:
                GF.note_peer(self.root, parsed["owner"], peer.get("display_name"), peer.get("country"),
                             _peer_seat(peer), peer.get("roster_at"), direction="in", claims=_claims(peer))
        except GF.ContractError as err:
            sys.stderr.write("inbox:%s\n" % err)
            audit(self.root, "in", msgid, from_npub, "refused")
            await self.send_receipt(msgid, "refused")

    def _replies_my_sent(self, ref, from_npub, to_seat):
        """ref 是我 REPLY_WINDOW_S 內寄出的一封、還在寄(寄出帳在寄之前就寫了,寄完才記好友)、當初寄給的就是簽這封的那一席、
        而且回到原寄件席 ⇒ True。已經寄完還是陌生人 = 寄完之後被刪 / 封鎖了 ⇒ 不放寬(#306 審 A-L11)。"""
        if not GF.MSGID_RE.match(ref or "") or not (self.root / "sending" / (ref + ".json")).exists():
            return False
        try:
            sent = json.loads((self.root / "sent" / (ref + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(sent, dict) and sent.get("to") == from_npub and sent.get("op") in ("send", "request") \
            and sent.get("seat") == to_seat and time.time() - int(sent.get("at") or 0) < REPLY_WINDOW_S

    async def on_echo(self, rec, peer, msgid, from_npub, entry, lang):
        """回音小幫手(10-24 Andy 選):名冊驗得到的陌生寄件人 ⇒ 回條 + 回一句「收到了」;不存內容、不進 AI、不加好友。
        驗不到(沒地址 / 簽的不是名冊那一席)⇒ 不回(不知道該回誰)。同一位 ECHO_GAP_S 內、整台每分鐘超過 ECHO_PER_MIN ⇒ 靜默。"""
        if not peer or not rec.get("from_addr"):
            audit(self.root, "in", msgid, from_npub, "echo:unverified")
            return
        now = time.time()
        owner = GF.parse_address(rec["from_addr"])["owner"]
        last = self.echo_last.get(owner)
        win = [t for t in self.echo_win if now - t < 60]
        if (last and now - last < ECHO_GAP_S) or len(win) >= ECHO_PER_MIN:
            self.echo_win = win
            audit(self.root, "in", msgid, from_npub, "echo:quiet")
            return
        if len(self.echo_last) > 10000:
            self.echo_last = {o: t for o, t in self.echo_last.items() if now - t < ECHO_GAP_S}
        self.echo_last[owner] = now
        self.echo_win = win + [now]
        await self.send_receipt(msgid, "queued")
        cabin = entry.get("cabin") if GF._CABIN.match(str(entry.get("cabin") or "")) else "main"
        lang = lang if lang in ECHO_TEXT else GF.default_lang(rec.get("country"))    # 沒帶語言 ⇒ 照對方國別
        reply = {"v": GF.VERSION, "msgid": GF.new_msgid(), "created_at": int(now), "op": "send", "echo": True,
                 "from": {"cabin": cabin, "seat": entry["seat5"], "attested": True}, "to": rec["from_addr"],
                 "content": ECHO_TEXT.get(lang, ECHO_TEXT["en"]), "reply_to": msgid, "lang": lang}
        self.sender.echo_in.append(msgid)
        try:
            GF.write_outbox(self.root, reply)
        except GF.ContractError as err:
            sys.stderr.write("echo:%s\n" % err)
            return
        self.echo_ids.append(reply["msgid"])
        audit(self.root, "in", msgid, from_npub, "echo")

    async def _signed_phones(self):
        """主人簽的機器清單裡 role=phone 的手機(中繼站抓、nostr-sdk 驗簽 + 作者 = 主人 + chat_devices 驗內容 + seq 不倒退、
        沒過期)。快取 60 秒;本機 paired.json 有變(配對 / 撤銷剛做完)就立刻重抓。抓不到 ⇒ 空(寧可不認)。"""
        import chat_pair as CP
        owner = (NK.read_map().get("owner") or {}).get("npub")
        try:
            mt = CP.paired_path(None).stat().st_mtime
        except OSError:
            mt = 0
        hit = getattr(self, "_phones", None)
        if hit and time.time() - hit[0] < 60 and hit[1] == mt:
            return hit[2]
        phones = set()
        if owner:
            try:
                rc, rec = await devices_fetch(owner, CR.relays())
                lst = rec.get("list") if rc == 0 else None
                if lst and not lst.get("expired") and DV.accept_seq(self.root, owner, lst["seq"]):
                    phones = DV.phones(lst)
            except Exception as err:   # noqa: BLE001
                sys.stderr.write("signed_phones:%s\n" % type(err).__name__)
        self._phones = (time.time(), mt, phones)
        return phones

    async def _reply_plain(self, entry, to_npub, text):
        """回一則不帶 aib 標籤的純文字私訊(給主人手機;一般 Nostr App 看得懂)。失敗只記 audit。"""
        rc, res = await self.sender._resolve(to_npub)
        if rc != 0:
            audit(self.root, "out", "-", to_npub, "phone-ack:" + str(res.get("why")))
            return False
        keys = _keys(entry["key"])
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(to_npub), text)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
            audit(self.root, "out", "-", to_npub, "phone-ack" if out.success else "phone-ack:failed")
            return bool(out.success)
        finally:
            await c.shutdown()

    async def _notify_phone(self, to_npub, text):
        """BB Call 轉手機(chat_phone_forward):用這台的機器金鑰送一行純文字到主人手機。沒有機器金鑰 / 失敗 ⇒ False,只記 audit。"""
        if not (NK.read_map().get("device") or {}).get("npub"):
            audit(self.root, "out", "-", to_npub, "bbcall:no-device")
            return False
        rc, res = await self.sender._resolve(to_npub)
        if rc != 0:
            audit(self.root, "out", "-", to_npub, "bbcall:" + str(res.get("why")))
            return False
        keys = _keys("device")
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(to_npub), text)
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
            audit(self.root, "out", "-", to_npub, "bbcall" if out.success else "bbcall:failed")
            return bool(out.success)
        finally:
            await c.shutdown()

    async def send_receipt(self, msgid, stage, pos=None, refs=None, reason=None, days=None, by=None):
        """回條寄回原寄件席(第 6.2 節)。只回 aib 寄件人(一般 Nostr App 收到一則看不懂的字沒用)。
        refs:已讀一批一次時,同一張回條帶的全部 msgid(第一個 = msgid)。
        reason:拒收的原因碼(REFUSE_REASONS),寄件端照碼顯示給人看;不帶任何內文。"""
        rec = self.led.received(msgid)
        if not rec or not rec.get("aib") or (stage not in RECEIPT_STAGES_OUT and stage != "read"):
            return False
        rc, res = await self.sender._resolve(rec["from"])
        if rc != 0:
            return False
        t = [["aib-v", "1"], ["aib-type", "receipt"], ["aib-receipt", stage]] + \
            [["aib-ref", r] for r in (refs or [msgid])]
        if pos:
            t.append(["aib-queue-pos", str(pos)])
        if (stage == "refused" and reason in REFUSE_REASONS) or (stage == "held" and reason in GF.HELD_REASONS):
            t.append(["aib-reason", reason])
        if stage == "still_waiting" and isinstance(days, int) and 1 <= days <= GF.WAIT_DAYS_MAX:
            t.append(["aib-days", str(days)])
        if stage == "read" and by == "human":
            t.append(["aib-read-by", "human"])        # 本人已讀:只帶「已讀 + 時間」,不帶本人做了什麼
        keys = _keys(rec["key"])
        gw = N.nip17_make_private_msg(keys, N.PublicKey.parse(rec["from"]), _receipt_text(stage, rec.get("lang")),
                                      rumor_extra_tags=[N.Tag.parse(x) for x in t])
        keys = None
        c = await _client(res["dm_relays"])
        try:
            out = await c.send_event(gw)
            audit(self.root, "receipt-out", msgid, rec["from"], stage if out.success else stage + ":failed")
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
                    await self.send_receipt(msgid, r["state"], r.get("pos"), reason=r.get("reason"), days=r.get("days"))
                elif r.get("state") == "read" and r.get("by") == "human":
                    await self.send_receipt(msgid, "read", by="human")    # 本人已讀不跟 AI 的已讀併一批(寄件人要分得出來)
                elif r.get("state") == "read":
                    self._queue_read(msgid)
                done += 1
                changed = True
            sent[msgid] = done
        if await self._flush_reads():
            changed = True
        if changed:
            self.led.save()

    def _queue_read(self, msgid):
        rec = self.led.received(msgid)
        if not rec or not rec.get("aib"):
            return
        key = "%s|%s" % (rec["from"], rec["key"])
        b = self.led.st["read_pending"].setdefault(key, {"refs": [], "first": int(time.time())})
        if msgid not in b["refs"]:
            b["refs"].append(msgid)

    async def _flush_reads(self, force=False):
        """同一寄件人同一席,READ_BATCH_S 秒內讀的合成一張「已讀」(第 10 節:一批一次、不經 AI)。"""
        pend = self.led.st["read_pending"]
        out = False
        for key in list(pend):
            b = pend[key]
            if not b.get("refs") or (not force and time.time() - b.get("first", 0) < READ_BATCH_S):
                continue
            ok = await self.send_receipt(b["refs"][0], "read", refs=b["refs"])
            if ok or time.time() - b.get("first", 0) > 3600:      # 寄不出去一小時後放棄(不無限堆)
                pend.pop(key, None)
                out = True
        return out


_RECEIPT_TEXT = {"queued": "已排隊", "delivered": "已送達", "held": "待對方審核", "refused": "對方未收", "expired": "已過期",
                 "read": "已讀", "still_waiting": "還在等對方本人看"}


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


async def _relay_status(receiver):
    c = receiver.client
    out = {r: "DISCONNECTED" for r in CR.relays()}
    if c is None:
        return out
    try:
        rel = await c.relays()
        for url, relay in rel.items():
            st = relay.status()
            out[str(url).rstrip("/")] = getattr(st, "name", None) or str(st).split(".")[-1]
    except Exception:   # noqa: BLE001
        pass
    return out


async def write_worker_status(root, receiver, state):
    """工人狀態(第 20.5 節):各站連線、今日收發、待寄、時鐘差、離線。aib chat status 讀它(四語由核心印)。"""
    rel = await _relay_status(receiver)
    up = sum(1 for v in rel.values() if v == "CONNECTED")
    now = time.time()
    if up:
        state["last_up"] = now
    offline = up == 0 and now - state.get("last_up", state["started"]) >= OFFLINE_AFTER
    pend = len(list((Path(root) / "outbox").glob("*.json"))) + len(list((Path(root) / "sending").glob("*.json")))
    rec = {"v": 1, "at": int(now), "pid": os.getpid(), "relays": rel, "up": up, "total": len(rel), "offline": offline,
           "today": today_counts(root), "pending": pend, "clock": state.get("clock"),
           "recv_fail": dict(getattr(receiver, "fail", None) or {"n": 0, "since": None, "why": None}),
           "self_check": state.get("self_check")}
    write_json(str(Path(root) / "worker-status.json"), rec)
    os.chmod(str(Path(root) / "worker-status.json"), 0o600)


SELF_CHECK_S = float(os.environ.get("AIB_CHAT_SELF_CHECK") or 1800)        # 10-24 第 5 條:多久查一次自己的名冊
SELF_CHECK_FIRST_S = float(os.environ.get("AIB_CHAT_SELF_CHECK_FIRST") or 120)   # 剛起先等名冊發出去


async def self_check(root, now=None):
    """我公開的席從中繼站查不查得到(跟好友查我走同一條路、不看快取)。10-24 Pro 名冊同席三把,
    別台整份拒收四天沒人發現 ⇒ 工人自己定期查、查不到就在 aib chat status 標紅。
    ⇒ {at, n(查了幾席), bad:[{seat, why, hint?}]}。"""
    m = NK.read_map()
    owner = (m.get("owner") or {}).get("npub")
    pub = set((CR.read_profile() or {}).get("public") or [])
    seats = [e for e in NK.sending_seats() if e["seat5"] in pub] if owner else []
    bad = []
    for e in seats:
        rc, res = await resolve("%s/%s/%s" % (owner, e.get("cabin") or "main", e["seat5"]), CR.relays(), root=root)
        if rc != 0 or (res.get("seat") or {}).get("npub") != e["npub"]:
            row = {"seat": e["seat5"], "why": res.get("why") or ("wrong_key" if rc == 0 else "error")}
            if res.get("hint"):
                row["hint"] = res["hint"]
            bad.append(row)
    return {"at": int(time.time() if now is None else now), "n": len(seats), "bad": bad}


async def _self_check_into(state, root):
    try:
        state["self_check"] = await self_check(root)
    except Exception as err:   # noqa: BLE001
        sys.stderr.write("self-check:%s\n" % type(err).__name__)


async def _check_clock(state):
    off = await asyncio.to_thread(clock_offset, CR.relays())
    state["clock"] = {"offset": off, "at": int(time.time()), "warn": off is not None and abs(off) >= CLOCK_WARN_S}


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
    sender.ask_due = True                                     # 10-24:轉接程式剛起 ⇒ 問一次好友在不在
    receiver = Receiver(root, sender)
    wstate = {"started": time.time(), "clock": None}
    last_clock = 0.0
    last_self = time.time() - SELF_CHECK_S + SELF_CHECK_FIRST_S   # 第一次在起來 SELF_CHECK_FIRST_S 秒後
    last_presence = 0.0
    last_beat = last_poll = 0.0
    last_verify = 0.0
    last_unconfirmed = 0.0
    last_bbcall = 0.0
    watch = CP.SleepWatch(root)
    recv_task = asyncio.ensure_future(receiver.run(stop))
    import chat_telegram as TG                                # 第 39 節:Telegram 互串(關著就只是每 10 秒看一次開關)
    tg_task = asyncio.ensure_future(TG.serve_loop(root, stop))
    for left in sorted((root / "sending").glob("*.json")):     # 上次掛掉時領了還沒寄完的:當重試處理
        if not (root / "sending" / (left.stem + ".try")).exists():
            write_json(str(root / "sending" / (left.stem + ".try")), {"n": 0, "next": 0, "why": "restart"})
    try:
        while not stop.is_set():
            if not _switch_on() or os.getppid() != parent:
                break
            if watch.tick():                                  # 剛睡醒:立刻補送在線狀態,好友很快就看到醒了
                sender.p_last = {}
                last_presence = last_beat = 0.0
            rf = GF.read_refresh(root)
            if rf.get("requested_at", 0) > rf.get("asked_at", 0) and not sender.ask_due:   # 本人刷新世界面板
                sender.ask_due = True
                sender.ping_owner = rf.get("ping")
                last_presence = last_poll = 0.0
            for o in list(getattr(sender, "resend_owners", None) or ()):
                sender.resend_owners.discard(o)
                if CR.is_blocked(o) or not (CR.is_friend(o) or CR.is_mine(o)):
                    continue
                for cur in GF.resend_due(root, o):
                    try:
                        await sender.resend(cur)
                    except Exception as err:   # noqa: BLE001
                        sys.stderr.write("resend:%s\n" % type(err).__name__)
            if getattr(sender, "answer_now", False):         # 有人問我在不在
                sender.answer_now = False
                last_presence = 0.0
            if time.time() - last_unconfirmed >= UNCONFIRMED_TICK_S:
                last_unconfirmed = time.time()
                GF.mark_unconfirmed(root, after=UNCONFIRMED_AFTER_S)
            if time.time() - last_hb >= HB_EVERY:
                GF.write_heartbeat(root)
                last_hb = time.time()
                if time.time() - last_clock >= CLOCK_EVERY:
                    last_clock = time.time()
                    asyncio.ensure_future(_check_clock(wstate))
                if time.time() - last_self >= SELF_CHECK_S:
                    last_self = time.time()
                    asyncio.ensure_future(_self_check_into(wstate, root))
                await write_worker_status(root, receiver, wstate)
            for msgid, _rec, _p in GF.pending(root, "outbox"):
                if GF.claim(root, msgid) is not None:
                    await sender.handle(msgid)
            for msgid in sender.due_retries():
                await sender.handle(msgid)
            await receiver.pump_delivery()
            receiver.retry_orphans()
            if time.time() - last_verify >= VERIFY_TICK_S:
                last_verify = time.time()
                try:
                    import chat_verify as CV
                    await asyncio.to_thread(CV.verify_round, root)
                except Exception as err:   # noqa: BLE001
                    sys.stderr.write("verify:%s\n" % type(err).__name__)
            try:
                await sender.push_offers()
            except Exception as err:   # noqa: BLE001
                sys.stderr.write("offers:%s\n" % type(err).__name__)
            try:
                await sender.push_visits()
            except Exception as err:   # noqa: BLE001
                sys.stderr.write("visits:%s\n" % type(err).__name__)
            if time.time() - last_presence >= PRESENCE_TICK_S:
                last_presence = time.time()
                try:
                    await sender.push_presence()
                except Exception as err:   # noqa: BLE001
                    sys.stderr.write("presence:%s\n" % type(err).__name__)
            if time.time() - last_beat >= BEAT_EVERY_S:
                last_beat = time.time()
                try:
                    await sender.push_beat()
                except Exception as err:   # noqa: BLE001
                    sys.stderr.write("beat:%s\n" % type(err).__name__)
            if time.time() - last_poll >= BEAT_POLL_S:
                last_poll = time.time()
                try:
                    await sender.poll_beats()
                except Exception as err:   # noqa: BLE001
                    sys.stderr.write("beat-poll:%s\n" % type(err).__name__)
            if time.time() - last_bbcall >= PF.TICK_S:              # BB Call 轉手機(預設關;關著只讀一次設定檔)
                last_bbcall = time.time()
                try:
                    await PF.tick(None, time.time(), receiver._signed_phones, receiver._notify_phone)
                except Exception as err:   # noqa: BLE001
                    sys.stderr.write("bbcall:%s\n" % type(err).__name__)
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
        tg_task.cancel()
        try:
            (Path(root) / "heartbeat.json").unlink()
        except FileNotFoundError:
            pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="chat_nostr")
    p.add_argument("cmd", choices=["publish", "publish-empty", "fetch", "serve", "probe", "roster", "sign-owner", "pair-send",
                                   "pair-fetch", "devices-fetch", "invite-publish", "invite-fetch"])
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
        elif a.cmd == "probe":
            rc, rec = asyncio.run(probe(a.addr))
        elif a.cmd == "roster":
            rc, rec = asyncio.run(roster_of(a.addr, CR.relays(), root))
        elif a.cmd == "sign-owner":
            rc, rec = cmd_sign_owner(sys.stdin)
        elif a.cmd == "pair-send":
            rc, rec = cmd_pair_send(sys.stdin)
        elif a.cmd == "pair-fetch":
            rc, rec = asyncio.run(pair_fetch(a.addr, CR.relays()))
        elif a.cmd == "devices-fetch":
            rc, rec = asyncio.run(devices_fetch(a.addr, CR.relays()))
        elif a.cmd == "invite-publish":
            rc, rec = cmd_invite_publish(sys.stdin)
        elif a.cmd == "invite-fetch":
            rc, rec = asyncio.run(invite_fetch(a.addr, CR.relays()))
        else:
            rc, rec = cmd_fetch(a.addr, CR.relays(), root=root)
    except Exception as err:   # noqa: BLE001  例外只記種類:訊息裡可能帶路徑或金鑰相關字
        rc, rec = 2, {"ok": False, "why": "error", "err": type(err).__name__}
    print(json.dumps(rec, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
