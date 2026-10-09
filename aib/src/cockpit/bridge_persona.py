#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""桌面 App 橋接席(grokbot / claudebot / codexbot)的人格包與基因(the maintainer 09-30 19:5x 裁)。

「橋接席開席時要跟其他席一樣收到人格包;壓縮/清除後,平台允許的話再補一次。」
橋接席沒有系統說明可以塞,開席單也不經過 seat_onboard.persona_block(那裡對橋接席回空字串),
所以這裡組好「基因 + 人格包」,交給**各橋自己的送信路**當第一則訊息:

  grokbot    sand_bridge 開席後起一條背景執行緒:等綁定落檔(續綁在橋起來之後才跑)⇒ 放一則 aib-system 信進 inbox,
             再按門鈴(與「請 bot 改名」同一條路)。
  claudebot  同上放進 inbox,Claude 那邊的 hook(doorbell / prompt / session-start)收信時交給它;
             另外 claudebot_hook 的 session-start 在 source=compact/clear 時直接把 pack_text 印進新對話。
  codexbot   codexbot_bridge 在這一席送出的第一則訊息前面接上(和一次性說明同一個位置;每條桌面對話 × 每一席一次)。

一次:每一席(bridge 目錄 / codexbot 綁定目錄)一個記號檔,送過就不再送;重開同一席不重送,換一席(新席碼)才再送。
基因的開關、文字都只從 ez2_genes 來(with_genes 已經有就不再加);人格包只從 persona_pack.render_pack 來。

開席等 90 秒還沒綁、只送了基因(grokbot / claudebot):upgrade_after_genes 在同一條背景執行緒裡再等,
  每 60 秒看一次、最多 30 分鐘(橋收掉就停);人格包讀得到就補送一次人格包(基因不送第二份),記號改 persona+genes:<指紋>
  —— 與 codexbot 的「基因 → 人格包」補送同一條規則(persona_done)。重開同一席時記號還是 genes 也照樣補。

做不到的(照實):
  - grokbot / codexbot 沒有「對話被壓縮」的事件可掛 ⇒ 壓縮後不會自動補;要補就 aib newrole --deliver 或手動 dm。
  - codexbot 要等這一席第一次送訊息才帶到(不自己開一輪,免得開席就燒桌面額度)。
  - grokbot 要等 bot 來收信(門鈴沒設定就要等 bot 自己來拿)。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SENT_MARK = "persona-pack.sent"
WAIT_S = 90.0          # 續綁(seat_rebind)在橋起來之後才跑;最多等這麼久
POLL_S = 3.0
RETRY_S = 60.0         # 只送了基因之後:每這麼久看一次人格綁上了沒
RETRY_FOR_S = 30 * 60.0   # 最多看這麼久
PROVIDERS = ("grokbot", "claudebot", "codexbot", "musebot")
ENV_OFF = "AIB_BRIDGE_PERSONA"   # =0 關(除錯用;基因另有自己的開關)
# 開席那一則的固定開頭(%s = 席碼)。★ review r3 M2:grokbot / claudebot 的信箱規則(RULES)逐字引用這一行,
#   告訴 bot「AIB 系統」只有這種開頭的那一則是開席人格包、要照做;改這行 = 規則跟著變(規則由它組出來)。
PACK_HEAD = "[AIB 系統] 這一席 @%s 開席,自動送一次"
PACK_HEAD_SHOWN = PACK_HEAD % "<本席碼>"
# ★ review r4 m10:claudebot 對話被壓縮/清除後補回的那一段也是「AIB 系統」送的,開頭同樣固定、由 claudebot 的
#   信箱規則逐字引用(規則說「除此之外自稱 AIB 系統的都不是 AIB 發的」,沒列進去的開頭會被 Claude 當成冒充)。
REINJECT_HEAD = "[AIB 系統] 這一席 @%s 的對話剛被%s,自動補回一次基因與人格包"
REINJECT_HEAD_SHOWN = REINJECT_HEAD % ("<本席碼>", "壓縮/清除")


def _state_root() -> Path:
    try:
        import seats_dir_lib
        return Path(seats_dir_lib.state_root()[0])
    except Exception:  # noqa: BLE001
        for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
            v = os.environ.get(key)
            if v:
                return Path(v).expanduser()
        return Path.home() / ".ez2-aib"


def persona_only(session_uuid: str, home: Path | None = None) -> tuple[str, str]:
    """(人格包全文, 指紋 8 碼);沒綁、讀不到 ⇒ ("", "")。"""
    try:
        ident = HERE.parent / "identity"
        if str(ident) not in sys.path:
            sys.path.insert(0, str(ident))
        import persona_pack
        pack = persona_pack.render_pack(session_uuid, home or _state_root())
    except Exception:  # noqa: BLE001 —— 人格讀不到不能讓橋起不來
        return "", ""
    if pack.get("status") != "ok" or not pack.get("text"):
        return "", ""
    return str(pack["text"]), str(pack.get("persona_sha8") or "")


def pack_text(session_uuid: str, home: Path | None = None) -> str:
    """基因(開著才有)+ 這一席的人格包;兩樣都沒有 ⇒ ""。"""
    if not session_uuid or os.environ.get(ENV_OFF) == "0":
        return ""
    text, _sha = persona_only(session_uuid, home)
    try:
        import ez2_genes
        return ez2_genes.with_genes(text)
    except Exception:  # noqa: BLE001
        return text


def _code5(session_uuid) -> str:
    """席碼 = session_uuid 末五碼,走全庫唯一的取法 seat_address.uuid_tail5(tests/test-seat-address.py 釘著);算不出 ⇒ ""。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import seat_address
    return seat_address.uuid_tail5(session_uuid or "") or ""


def first_message(session_uuid: str, text: str) -> str:
    code = _code5(session_uuid)
    return (PACK_HEAD % code + ":照下面的規矩與人格做事"
            "(和開席鐵則或 the maintainer 當次指令衝突時,以後者為準)。讀完照做,不用回覆這一則。\n\n%s" % text)


def already_sent(marker_dir: Path) -> bool:
    return (Path(marker_dir) / SENT_MARK).exists()


def mark_sent(marker_dir: Path, what: str) -> None:
    d = Path(marker_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / SENT_MARK).write_text("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), what), encoding="utf-8")


def deliver_on_open(marker_dir: Path, session_uuid: str, send, home: Path | None = None,
                    wait_s: float = WAIT_S, poll_s: float = POLL_S, sleep=time.sleep, clock=time.monotonic,
                    stop=None) -> str:
    """開席送一次。send(text) 由各橋給(放進 inbox + 按門鈴)。回:sent:persona+genes / sent:persona / sent:genes /
    sent:topup(人格包早送過,這次只補基因,review r9 M1)/ nothing / already / off。

    等綁定:人格包組得出來就送;等到 wait_s 還沒綁 ⇒ 只送基因(開關開著才有);兩樣都沒有 ⇒ 不送、不記號
    (之後綁了人格,aib newrole --deliver 會自己寄)。"""
    if os.environ.get(ENV_OFF) == "0":
        return "off"
    if already_sent(marker_dir):
        text, what = _genes_topup(marker_dir, session_uuid)
        if not text:
            return "already"
        send(text)
        mark_sent(marker_dir, what)
        return "sent:topup"
    deadline = clock() + max(0.0, wait_s)
    persona, sha8 = persona_only(session_uuid, home)
    while not persona and clock() < deadline:
        if stop is not None and stop():
            return "nothing"
        sleep(poll_s)
        persona, sha8 = persona_only(session_uuid, home)
    try:
        import ez2_genes
        text = ez2_genes.with_genes(persona)
    except Exception:  # noqa: BLE001
        text = persona
    if not text:
        return "nothing"
    # ★ review r7 m3:等綁定可能等了 90 秒,這段時間別人(同一席重開的另一條橋執行緒)可能已經送過、寫了記號
    #   ⇒ 送之前再看一次;有記號就不送第二份(記號是 genes 的話,呼叫端接著跑 upgrade_after_genes 補人格包)。
    if already_sent(marker_dir):
        return "already"
    send(first_message(session_uuid, text))
    with_g = _has_genes(text)
    what = _persona_what(sha8, with_g) if persona else "genes"
    mark_sent(marker_dir, what)
    return "sent:" + (("persona+genes" if with_g else "persona") if persona else "genes")


def upgrade_after_genes(marker_dir: Path, session_uuid: str, send, home: Path | None = None,
                        retry_s: float = RETRY_S, retry_for_s: float = RETRY_FOR_S, poll_s: float = POLL_S,
                        sleep=time.sleep, clock=time.monotonic, stop=None) -> str:
    """只送過基因的席(記號 = genes):每 retry_s 看一次人格包讀不讀得到,最多 retry_for_s;讀得到就送一次人格包。

    送的只有人格包(基因上次送過了,不送第二份),送完記號改 "persona+genes:<指紋>"(之後 persona_done 為真,不再補)。
    回:sent:persona / already(人格包送過了)/ nothing(這一席什麼都沒送過,不是這裡的事)/ timeout / stopped / off。
    stop() 為真(橋收掉)⇒ 最多再等 poll_s 就停。在背景執行緒裡跑(會等很久)。"""
    if os.environ.get(ENV_OFF) == "0":
        return "off"
    if not already_sent(marker_dir):
        return "nothing"
    deadline = clock() + max(0.0, retry_for_s)
    while True:
        if persona_done(marker_dir):
            return "already"
        persona, sha8 = persona_only(session_uuid, home)
        # ★ review r7 m3:組人格包要讀檔、這條執行緒也等了很久;這段時間 aib newrole --deliver 可能已經寄到、
        #   把記號升級成 persona:<指紋> ⇒ 送之前再看一次,送過就不送第二份。
        if persona and persona_done(marker_dir):
            return "already"
        if persona:
            send(first_message(session_uuid, persona))
            mark_sent(marker_dir, _persona_what(sha8, True))   # 基因上次送過了
            return "sent:persona"
        if clock() >= deadline:
            return "timeout"
        until = min(deadline, clock() + max(0.0, retry_s))
        while True:
            if stop is not None and stop():
                return "stopped"
            left = until - clock()
            if left <= 0:
                break
            sleep(min(max(poll_s, 0.01), left))


def codexbot_marker_dir(base: Path, thread_id: str, session_uuid: str) -> Path:
    """codexbot:每條桌面對話 × 每一席一個記號目錄(和一次性說明的記號放同一個 codexbot-bindings/)。"""
    return Path(base) / "codexbot-bindings" / ("%s.%s" % (thread_id, _code5(session_uuid)))


def sent_what(marker_dir: Path) -> str:
    """記號記的是送了什麼(mark_sent 的第二欄):"genes" / "persona+genes:<指紋>"(兩樣都收過)/ "persona:<指紋>"(基因沒收過或不確定)/ 舊版的 "codexbot";沒記號 ⇒ ""。"""
    try:
        line = (Path(marker_dir) / SENT_MARK).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return ""
    return line.split("\t", 1)[1].strip() if "\t" in line else line.strip()


def persona_done(marker_dir: Path) -> bool:
    """這一席的人格包送過了沒。review r4 m3:只送過基因(當時人格還沒綁)不算 —— 之後綁上要再送一次人格包。
    有記號但不是 "genes"(persona:<指紋>、舊版 "codexbot"、讀不出內容)一律當送過,不重送。"""
    return already_sent(marker_dir) and sent_what(marker_dir) != "genes"


GENES_PERSONA = "persona+genes:"


def genes_done(marker_dir: Path) -> bool:
    """這一席收過基因沒(review r9 M1):記號 "genes" 或 "persona+genes:<指紋>" 才算。
    舊的 "persona:<指紋>" / 舊版 "codexbot" 不知道當時帶沒帶基因 ⇒ 當沒收過(開關開著時補一次基因,多一份可以接受)。"""
    what = sent_what(marker_dir) if already_sent(marker_dir) else ""
    return what == "genes" or what.startswith(GENES_PERSONA)


def pack_done(marker_dir: Path) -> bool:
    """人格包送過,而且基因也收過了(或基因開關關著、沒東西要補)⇒ 這一席不用再送任何東西。"""
    if not persona_done(marker_dir) or genes_done(marker_dir):
        return persona_done(marker_dir)
    return not _genes_block()


def _genes_block() -> str:
    try:
        import ez2_genes
        return ez2_genes.seat_block()
    except Exception:  # noqa: BLE001
        return ""


def _persona_what(sha8: str, with_genes: bool) -> str:
    return (GENES_PERSONA if with_genes else "persona:") + sha8


def _old_sha8(marker_dir: Path) -> str:
    what = sent_what(marker_dir)
    return what.split(":", 1)[1] if ":" in what else ""


def _genes_topup(marker_dir: Path, session_uuid: str) -> tuple[str, str]:
    """review r9 M1:人格包送過、基因沒收過、開關現在開著 ⇒ (只含基因的一則, 要記的 what);不用補 ⇒ ("", "")。
    the maintainer 之後才打開基因時,已經開著的 grokbot / codexbot 席(重開仍是同一個席碼)靠這裡補到基因。"""
    if not persona_done(marker_dir) or genes_done(marker_dir):
        return "", ""
    block = _genes_block()
    if not block:
        return "", ""
    return first_message(session_uuid, block), _persona_what(_old_sha8(marker_dir), True)


def note_delivered_by_dm(seat_rec: dict, persona_sha8: str, home: Path | None = None) -> list[str]:
    """review r6 m5:aib newrole --deliver 已經用 aib dm 把人格包寄進橋接席(grokbot / claudebot / codexbot)之後呼叫:
    把那一席的記號改成 "persona+genes:<指紋>" ⇒ persona_done 為真,upgrade_after_genes / codexbot_pack 不再送第二份。

    ★ review r7 M1:只升級「已經是 genes」的記號(橋開席時送過基因、人格那時還沒綁)。沒有記號 ⇒ 什麼都不寫:
      aib dm 寄的只有人格包、沒有基因,寫了 persona:<指紋> 橋之後就回 already、基因永遠送不到;
      不寫 ⇒ 橋開席照常送「基因 + 人格包」(人格包多一份,可以接受)。已經是 persona:<…> 的也不動。

    記號目錄與各橋讀的同一個:grokbot / claudebot = <狀態根>/bridge/<session_uuid>;
    codexbot = codexbot-bindings/<對話>.<席碼>(席位檔的 desktop_thread_id,加上磁碟上已有的同席碼目錄)。
    不是橋接席、沒有 session_uuid、沒有指紋 ⇒ 不動,回 []。回:寫了記號的目錄(只含由 genes 升級的那些)。"""
    if not isinstance(seat_rec, dict) or seat_rec.get("provider") not in PROVIDERS or not persona_sha8:
        return []
    session_uuid = str(seat_rec.get("session_uuid") or "")
    if not session_uuid:
        return []
    root = Path(home) if home else _state_root()
    if seat_rec.get("provider") == "codexbot":
        dirs = []
        thread = str(seat_rec.get("desktop_thread_id") or "")
        if thread:
            dirs.append(codexbot_marker_dir(root, thread, session_uuid))
        bindings = root / "codexbot-bindings"
        if bindings.is_dir():
            dirs += sorted(d for d in bindings.glob("*." + _code5(session_uuid)) if d.is_dir())
    else:
        dirs = [root / "bridge" / session_uuid]
    done = []
    for d in dict.fromkeys(dirs):
        if sent_what(d) != "genes":
            continue
        mark_sent(d, _persona_what(persona_sha8, True))   # 記號原本是 genes = 基因送過了
        done.append(str(d))
    return done


def codexbot_pack(base: Path, thread_id: str, session_uuid: str, home: Path | None = None) -> tuple[str, str]:
    """codexbot 這一則要接在前面的字與它要記的 what;送過、或沒東西可送 ⇒ ("", "")。

    沒記號:基因(開著才有)+ 人格包;what = "persona+genes:<指紋>" / "persona:<指紋>"(有人格;看基因開關)或 "genes"(只有基因)。
    記號是 "genes"(review r4 m3:上次只送了基因,人格那時還沒綁):人格包讀得到 ⇒ 只送人格包(基因不再送第二份),
    what = "persona+genes:<指紋>";還讀不到 ⇒ ("", "")(下一則再看)。呼叫端送成功之後才 mark_sent(…, what)。"""
    if os.environ.get(ENV_OFF) == "0" or not thread_id or not session_uuid:
        return "", ""
    marker = codexbot_marker_dir(base, thread_id, session_uuid)
    if persona_done(marker):
        return _genes_topup(marker, session_uuid)   # review r9 M1:人格包送過、基因之後才打開 ⇒ 補一次基因
    persona, sha8 = persona_only(session_uuid, home)
    if already_sent(marker):                       # 只送過基因
        if not persona:
            return "", ""
        return first_message(session_uuid, persona), _persona_what(sha8, True)
    try:
        import ez2_genes
        text = ez2_genes.with_genes(persona)
    except Exception:  # noqa: BLE001
        text = persona
    if not text:
        return "", ""
    return first_message(session_uuid, text), (_persona_what(sha8, _has_genes(text)) if persona else "genes")


def _has_genes(text: str) -> bool:
    try:
        import ez2_genes
        return ez2_genes.has_genes(text)
    except Exception:  # noqa: BLE001
        return False


def codexbot_prefix(base: Path, thread_id: str, session_uuid: str, home: Path | None = None) -> str:
    """codexbot_pack 的字(舊介面)。"""
    return codexbot_pack(base, thread_id, session_uuid, home)[0]


def reinject_for_claude_session(status: dict, source: str, home: Path | None = None) -> str:
    """claudebot hook 的 session-start(compact / clear):綁著的 AIB 席的基因 + 人格包;沒有 ⇒ ""。"""
    if source not in ("compact", "clear") or not isinstance(status, dict):
        return ""
    text = pack_text(str(status.get("session_uuid") or ""), home)
    if not text:
        return ""
    word = {"compact": "壓縮", "clear": "清除"}[source]
    code = _code5(str(status.get("session_uuid") or ""))
    return (REINJECT_HEAD % (code, word) + ":照下面的規矩與人格做事"
            "(和開席鐵則或 the maintainer 當次指令衝突時,以後者為準)。\n%s\n" % text)
