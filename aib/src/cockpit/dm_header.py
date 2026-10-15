#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""dm 抬頭的單一來源:一個 build、一個 parse(包 I,the maintainer 09-26 18:5x「所有的 aib DM 都應如此,必要程式化」)。

## 新式(正本,the maintainer 18:4x 逐字樣本)
    IP:100.64.0.1/3c73/a seat >> IP:100.64.0.1/d6a8/a seat 09-26 18:41(台北時間)
  - 每一方三段 `IP:<位址>/<艙號>/<名>@<席碼>`;兩方以 ` >> ` 相接;最後 `MM-DD HH:MM(<時區地名>)`,不帶秒。
  - 「人格」是佔位:印席位綁定的 persona title;未綁 `未綁@席碼`;真人 `the maintainer@hub`;系統/開席通知 `系統@aib`。
    真人與系統寄件者前兩段照樣印本機的,只換第三段(a seat 18:48)。同機同艙也完整三段。
  - 不帶中括號;**抬頭與正文同一行,中間一個空格**(a seat 22:54 裁,收回 18:48「獨佔第一行」:
    多行貼上在 Claude Code 會折成 [Pasted text],收件人看不到字;長訊指標必須一行)。
  - 括號半形、緊貼時刻無空格。

## 舊式(相容一版,只解析不再產生)
  - `[@x(人格) > @y / 台北 MM-DD HH:MM]`(抬頭後同一行接正文)
  - 55f90 版在 token 前加「<位址>·<艙號>·」那一種(見 parse_legacy)
  三種解析結果是**同一個形狀的 dict**,讀端不必分辨是哪一式。

所有寫抬頭的地方呼叫 build,所有讀抬頭的地方呼叫 parse;**不得另寫第二份 regex**。
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Mapping, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

HUMAN_NAME, HUMAN_CODE = "the maintainer", "hub"
SYSTEM_NAME, SYSTEM_CODE = "系統", "aib"
UNBOUND_NAME = "未綁"
DEFAULT_TZ = "Asia/Taipei"
TZ_LABEL = {"Asia/Taipei": "台北時間", "Asia/Tokyo": "東京時間"}
ARROW = " >> "
LOOPBACK = "127.0.0.1"

# ── 新式 ──
_PARTY = r"IP:(?P<{p}addr>[^/\s]+)/(?P<{p}cabin>[^/\s]*)/(?P<{p}name>[^\s@][^@]*?)@(?P<{p}code>[^\s/@>]+)"
_V2_RE = re.compile(r"^" + _PARTY.format(p="s_") + re.escape(ARROW) + _PARTY.format(p="r_")
                    + r" (?P<when>\d\d-\d\d \d\d:\d\d)\((?P<tz>[^()\s]+)\)(?= |\n|$)")


def party(code: str, name: str = "", *, addr: str = "", cabin: str = "") -> dict:
    """一方的四欄(席碼欄叫 code,艦長 18:50 令:今晚新欄一律用 code)。name 空 ⇒ 未綁。"""
    return {"addr": addr or "", "cabin": cabin or "", "name": (name or "").strip() or UNBOUND_NAME,
            "code": (code or "").strip().lstrip("@")}


def tz_label(tz_name: str) -> str:
    return TZ_LABEL.get(tz_name, tz_name)


def format_when(when, tz_name: str = DEFAULT_TZ) -> str:
    """`MM-DD HH:MM(時區地名)`。when 可為 epoch 秒或 datetime(無時區的當 UTC)。"""
    if isinstance(when, (int, float)):
        dt = datetime.fromtimestamp(float(when), tz=timezone.utc)
    elif isinstance(when, datetime):
        dt = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    else:
        raise TypeError("when 必須是 epoch 秒或 datetime:%r" % (when,))
    if ZoneInfo is not None:
        dt = dt.astimezone(ZoneInfo(tz_name))
    return "%s(%s)" % (dt.strftime("%m-%d %H:%M"), tz_label(tz_name))


def _side(p: Mapping) -> str:
    addr = p.get("addr") or LOOPBACK
    name = (p.get("name") or "").strip() or UNBOUND_NAME
    code = (p.get("code") or "").strip().lstrip("@")
    if not code:
        raise ValueError("席碼不可空:%r" % (dict(p),))
    return "IP:%s/%s/%s@%s" % (addr, p.get("cabin") or "", name, code)


def build(sender: Mapping, receiver: Mapping, when, tz_name: str = DEFAULT_TZ) -> str:
    """新式抬頭一行(不含換行)。sender / receiver 用 party() 組,或 local_party() 取本機。"""
    return _side(sender) + ARROW + _side(receiver) + " " + format_when(when, tz_name)


def _from_groups(m, prefix: str) -> dict:
    return {"addr": m.group(prefix + "addr"), "cabin": m.group(prefix + "cabin"),
            "name": m.group(prefix + "name"), "code": m.group(prefix + "code")}


def _result(form, sender, receiver, when, tz, body):
    code = sender.get("code") or ""
    kind = ("system" if code == SYSTEM_CODE and sender.get("name") == SYSTEM_NAME else
            "human" if code == HUMAN_CODE else
            "seat" if re.fullmatch(r"[0-9a-z]{4,8}", code) and code not in ("grokbot", "unknown") and
            sender.get("name") not in ("未認證", "艙外") else "other")
    return {"form": form, "sender": sender, "receiver": receiver, "when": when, "tz": tz,
            "sender_kind": kind, "body": body}


def parse(text: str) -> Optional[dict]:
    """認新式與舊式兩種;認不出回 None。

    回傳(三式同形):
      form         "v2" | "legacy_at" | "legacy_ip"
      sender       {addr, cabin, name, code}(舊式缺的欄給 "")
      receiver     同上
      when         "MM-DD HH:MM"
      tz           "台北時間" 這類地名(舊式的「台北」補成「台北時間」)
      sender_kind  "system" | "human" | "seat" | "other"(未認證/艙外/bot)—— 系統判斷只看第三段 `系統@aib`
      body         抬頭之後的正文(抬頭後的一個空格或換行不算正文)
    """
    if not text:
        return None
    m = _V2_RE.match(text)
    if m:
        rest = text[m.end():]
        if rest[:1] in (" ", "\n"):
            rest = rest[1:]
        return _result("v2", _from_groups(m, "s_"), _from_groups(m, "r_"), m.group("when"), m.group("tz"), rest)
    return parse_legacy(text)


# ── 舊式(相容一版):[<寄件> > <收件> / 台北 MM-DD HH:MM] 正文 ──
#   寄件/收件 token:@五碼[(暱稱)] / human:名 / agent:名 / unattested:名 / 白話名(開席通知、GrokBot (…))。
#   55f90 那一版在 token 前加「<位址>·<艙號>·」(最多兩段,位址可為 IPv4 / IPv6 / 主機名 / ?),一併認。
# 「 / 台北 MM-DD HH:MM」段可省(畫面夾具與舊版簡寫 `[a seat > a seat] …` 也要認成 dm,不得畫成真人)。
_LEGACY_RE = re.compile(
    r"^\s*\[(?P<s>[^\]>]+?) > (?P<r>[^\]/]+?)(?: / (?P<tz>\S+) (?P<when>\d\d-\d\d \d\d:\d\d))?\] ?")
_PLACE_RE = re.compile(r"^(?P<segs>(?:[^\s·@\[\]>]+·){1,2})")
_CODE_TOKEN_RE = re.compile(r"^@(?P<code>[0-9a-z]{4,8})(?:[((](?P<nick>[^))]*)[))])?$")
# 不是席、不是真人的白話寄件者裡,哪些算「系統」:用 sender_origin 的機器名判(dm-ctl 知道),
#   舊式抬頭只有白話名時用這張字面表(與 codexbot_bridge 既有 SYSTEM_SENDER_LABELS 同集合)。
SYSTEM_LABELS = ("開席通知", "排序暫存", "系統", "巡查", "aib-system", "系統狀態通知", "自動降溫", "強制回報點",
                 "艦隊狀態", "指令訊息轉送", "開啟六席艦隊", "AI 啟動器", "CodexBot活檢")
HUMAN_ORIGINS = ("mouse_ctl",)          # 待審區真人送出:是 the maintainer 按的
BOT_ORIGINS = {"grokbot": "GrokBot"}
# 艦長室網頁(src/cockpit/captain_room.py)寄的 dm:抬頭寫「艦長室@room」,不混進「系統@aib」——
#   那是艦長在網頁上按的指令,收件席要看得出是誰(the maintainer 10-10)。白話名與 i18n aib.sender.origin.captain_room 同一個字。
ROOM_ORIGIN, ROOM_CODE, ROOM_NAME = "captain-room", "room", "艦長室"


def _strip_place(token: str) -> tuple[str, str, str]:
    m = _PLACE_RE.match(token)
    if not m:
        return token, "", ""
    segs = m.group("segs").split("·")[:-1]
    addr = segs[0] if len(segs) == 2 else ""
    cabin = segs[-1] if len(segs) == 2 else (segs[0] if re.fullmatch(r"[0-9a-f]{4}", segs[0]) else "")
    if len(segs) == 1 and not cabin:
        addr = segs[0]
    return token[m.end():], addr, cabin


def party_from_label(label: str, origin: str = "") -> dict:
    """舊式 token(dm-ctl 內部的 S_LABEL / T_LABEL)⇒ 新式一方。origin = sender_origin 的機器名(有就用它判類)。"""
    token, addr, cabin = _strip_place((label or "").strip())
    m = _CODE_TOKEN_RE.match(token)
    if m:
        return party(m.group("code"), m.group("nick") or "", addr=addr, cabin=cabin)
    if token.startswith("human:"):
        hid = token[len("human:"):]
        name = HUMAN_NAME if hid.lower() in ("", "human", "user") else hid
        return party(HUMAN_CODE, name, addr=addr, cabin=cabin)
    if origin in HUMAN_ORIGINS:
        return party(HUMAN_CODE, HUMAN_NAME, addr=addr, cabin=cabin)
    if origin in BOT_ORIGINS or token.startswith("GrokBot"):
        return party("grokbot", BOT_ORIGINS.get("grokbot"), addr=addr, cabin=cabin)
    if origin == ROOM_ORIGIN or token.startswith("unattested:" + ROOM_ORIGIN):   # 只認來源,不認字面(10-11 審查:人格叫「艦長室」的席會被當成艦長室)
        return party(ROOM_CODE, ROOM_NAME, addr=addr, cabin=cabin)
    if token.startswith("seat_claimed:"):
        # Codex 席自稱的席碼(dm-ctl 從 codex 對話檔讀到、沒有 pane 鏈證明)。名字固定寫「自稱」,
        # 讓收件席看得出是弱態,但席碼要留著,它才回得了頭(09-30 搬 2ad9b2348 進 main 時補)。
        code = token[len("seat_claimed:"):].split("(")[0].split("（")[0]
        if re.fullmatch(r"[0-9a-f]{5}", code or ""):
            return party(code, "自稱", addr=addr, cabin=cabin)
    if token.startswith("unattested:"):
        return party(token[len("unattested:"):] or "unknown", "未認證", addr=addr, cabin=cabin)
    if token.startswith("agent:"):
        return party(token[len("agent:"):] or "agent", "艙外", addr=addr, cabin=cabin)
    if origin or any(token.startswith(lbl) for lbl in SYSTEM_LABELS):
        return party(SYSTEM_CODE, SYSTEM_NAME, addr=addr, cabin=cabin)
    return party("", token, addr=addr, cabin=cabin)   # 認不出:名字照抄、席碼空(build 會拒)


def parse_legacy(text: str) -> Optional[dict]:
    m = _LEGACY_RE.match(text or "")
    if not m:
        return None
    s_tok, r_tok = m.group("s").strip(), m.group("r").strip()
    sender, receiver = party_from_label(s_tok), party_from_label(r_tok)
    if not m.group("when") and not (sender["code"] and receiver["code"]):
        return None     # 沒有時間段的簡寫:兩端都要是認得出的 token,人打的 `[a > b]` 不算 dm
    placed = bool(_PLACE_RE.match(s_tok) or _PLACE_RE.match(r_tok))
    tz = m.group("tz") or ""
    tz = tz if (not tz or tz.endswith("時間")) else tz + "時間"
    return _result("legacy_ip" if placed else "legacy_at", sender, receiver,
                   m.group("when") or "", tz, text[m.end():])


# ── 本機這一方 ──
def local_addr(env: Optional[Mapping] = None) -> str:
    """位址:AIB_DM_ADDR(治具)> Tailscale IPv4 > 區網 IPv4 > 127.0.0.1(挑法在 host_ip,與 dm_address.local_ips 同一套;
    10-14:tailscale 不在 PATH 時也找 App 固定安裝路徑,ssh 進來的殼才不會退回區網 IP)。"""
    env = os.environ if env is None else env
    if env.get("AIB_DM_ADDR"):
        return env["AIB_DM_ADDR"]
    return _host_ip().preferred_ipv4(env)


def _host_ip():
    """同目錄的 host_ip(抬頭與跨主機判斷同一套挑 IP 法);從別處 import 本檔時目錄不一定在 sys.path。"""
    try:
        import host_ip
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import host_ip
    return host_ip


def local_cabin(env: Optional[Mapping] = None) -> str:
    """艙號(艙位碼)= EZ2_AIB_INSTANCE 最後一個 `-` 之後的四碼(沒有就空;the maintainer 09-28 與 dm_address.py 同式)。"""
    env = os.environ if env is None else env
    return cabin_tail((env.get("EZ2_AIB_INSTANCE") or "").strip())


def cabin_tail(instance: str) -> str:
    """艙 id ⇒ 艙位碼:最後一個 `-` 之後、最多四碼。舊流水號 13 ⇒ 13;main ⇒ main。"""
    return instance.rsplit("-", 1)[-1][-4:] if instance else ""


def local_party(code: str, name: str = "", env: Optional[Mapping] = None) -> dict:
    return party(code, name, addr=local_addr(env), cabin=local_cabin(env))


def cabin_of_socket(socket: str, env: Optional[Mapping] = None) -> str:
    """aihub-<instance> ⇒ instance 尾四碼;不是 aihub- 形(治具 dmtest 之類)⇒ 本機艙號。"""
    s = (socket or "").strip()
    if s.startswith("aihub-") and len(s) > len("aihub-"):
        return cabin_tail(s[len("aihub-"):])
    return local_cabin(env)


def build_from_labels(s_label: str, t_label: str, *, s_origin: str = "", s_socket: str = "",
                      t_socket: str = "", when=None, env: Optional[Mapping] = None,
                      s_addr: str = "", s_note: str = "") -> str:
    """dm-ctl 用:它內部的寄件/收件 token ⇒ 新式抬頭。位址一律本機(同機投遞);艙號看各自的 socket。

    跨主機(dm_xhost.py):s_addr = sshd 量到的對方 IP,s_note 掛在寄件方名字後面(半形括號),
    讀的人一眼分得出這封是隔一台機器、經 SSH 進來的;席碼欄不動,抄地址回信照樣回得到。"""
    import time
    addr = local_addr(env)
    s = party_from_label(s_label, s_origin)
    r = party_from_label(t_label)
    s["addr"], s["cabin"] = s_addr or addr, cabin_of_socket(s_socket, env)
    if s_addr:
        # 跨主機:寄件方的艙不能拿收件機自己的艙號來補(cabin_of_socket 的退路);主艙寫 main,認不出就空著。
        sock = (s_socket or "").strip()
        s["cabin"] = "main" if sock == "aihub" else (cabin_tail(sock[len("aihub-"):]) if sock.startswith("aihub-") else "")
    r["addr"], r["cabin"] = addr, cabin_of_socket(t_socket or s_socket, env)
    if s_note:
        s["name"] = "%s(%s)" % (s["name"], s_note)
    return build(s, r, time.time() if when is None else when)


def _reader_lang(env: Optional[Mapping] = None) -> str:
    """外框說明與全球標記用讀者(收件機主人)的介面語言(開發文件第 16 節);認不出就用預設。"""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale(env=env) if env is not None else i18n_lib.resolve_locale()
    except Exception:  # noqa: BLE001 —— 語言判不出不擋投遞,退預設
        loc = ""
    return loc if loc in ("zh-TW", "en", "ja", "ko") else "zh-TW"


def build_from_global(seat: str, item_id: str, t_label: str, *, t_socket: str = "", when=None,
                      env: Optional[Mapping] = None) -> tuple:
    """全球來信(佇列裡 sender_origin=nostr:*)⇒ (抬頭一行, 外框說明行)。開發文件第 5.8、8.1、8.3 節。

    寄件方只有顯示名稱加標記(dm_global_header.sender_side),收件方與時間照本機格式。
    佇列記錄不是全球種類、或 global 欄驗不過 ⇒ ValueError(不組出半個全球抬頭)。"""
    import time
    import dm_global_header as GH
    import dm_queue as Q
    item = Q._load_item(seat, item_id, env) or {}
    g = Q._global_or_none(item.get("global"))
    if item.get("sender_kind") not in Q.GLOBAL_SENDER_KINDS or g is None or not g.get("reply_code") \
            or not Q.NOSTR_ORIGIN_RE.fullmatch(str(item.get("sender_origin") or "")):
        raise ValueError("不是可用的全球來信:%s/%s" % (seat, item_id))
    lang = _reader_lang(env)
    r = party_from_label(t_label)
    r["addr"], r["cabin"] = local_addr(env), cabin_of_socket(t_socket, env)
    sender = GH.sender_side(g["shown"], g["verified"], same_name=g["same_name"], lang=lang,
                            country=g["country"], country_status=g["country_status"])
    head = GH.build(sender, _side(r), format_when(time.time() if when is None else when))
    frame = GH.frame(g["reply_code"], lang=lang, paired=item.get("sender_kind") == "owner_paired",
                     country=g["country"], country_status=g["country_status"], tz_country=g["tz_country"])
    return head, frame


def main(argv=None) -> int:
    """殼腳本用:dm_header.py build <寄件席> <寄件名> <收件席> <收件名> [epoch]
                 dm_header.py from-labels <S_LABEL> <T_LABEL> [S_ORIGIN] [S_SOCKET] [T_SOCKET] [S_ADDR] [S_NOTE]
                 dm_header.py from-global <佇列席> <佇列 id> <T_LABEL> [T_SOCKET]  ⇒ 抬頭\x1f外框說明(不是全球來信 rc=2)
                 dm_header.py parse < 文字   ⇒ JSON(認不出 rc=1)"""
    import json
    import sys
    import time
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["build"] and len(args) >= 5:
        when = float(args[5]) if len(args) > 5 else time.time()
        print(build(local_party(args[1], args[2]), local_party(args[3], args[4]), when))
        return 0
    if args[:1] == ["from-labels"] and len(args) >= 3:
        # from-labels <S_LABEL> <T_LABEL> [S_ORIGIN] [S_SOCKET] [T_SOCKET] [S_ADDR] [S_NOTE]
        rest = args[3:] + [""] * 5
        print(build_from_labels(args[1], args[2], s_origin=rest[0], s_socket=rest[1], t_socket=rest[2],
                                s_addr=rest[3], s_note=rest[4]))
        return 0
    if args[:1] == ["from-global"] and len(args) >= 4:
        try:
            head, frame = build_from_global(args[1], args[2], args[3], t_socket=(args[4] if len(args) > 4 else ""))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print("%s\x1f%s" % (head, frame))
        return 0
    if args[:1] == ["parse"]:
        got = parse(sys.stdin.read())
        if got is None:
            return 1
        print(json.dumps(got, ensure_ascii=False))
        return 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
