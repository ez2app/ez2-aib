#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""MuseBot 的對話綁定:形狀判準與「同一個 Muse 對話只准一隻 AIB 席」的鎖。

2026-10-05 a seat 複核 musebot 掛載時補。為什麼要有這一支:

- 交接包在 `sand_bridge.bound_agent("musebot")` 裡寫了「只驗非空」,而那個值最後會變成
  **鎖檔的檔名**(grokbot/claudebot 都是 `<id>.lock`)。不驗形狀 ⇒ 帶 `/` 或 `..` 的對話 id
  會把鎖寫到別的地方 —— 與同一輪抓到的 `musebot_mailbox.bridge_dir` 是同一類病,一起修。
- grokbot/claudebot 都有「同一個對話只准一隻席」的檔案鎖,musebot 原本沒有 ⇒ 兩隻席可以
  綁同一個 Muse 對話、同時回同一串。這一支把那把鎖補上,判法與兩個兄弟逐字同形。

★ 形狀判準是**安全下界,不是格式宣稱**:Muse 對話 id 的真正格式由 Muse 側定(SPEC §8 未答),
  這裡只保證「檔名安全、長度有界」。Muse 側給出正式格式後再收緊,收緊時 test-musebot-binding 會紅。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROVIDER = "musebot"
BINDINGS_NAME = "musebot-bindings"
# 檔名安全下界:開頭是英數,其餘英數與 . _ - :;長度 4~128;不含路徑分隔與 ..(下面另外擋)
CONVERSATION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:-]{3,127}$")


def _state_root(base=None) -> Path:
    if base:
        return Path(base)
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def valid_conversation_id(conv: str) -> bool:
    s = (conv or "").strip()
    return bool(s) and ".." not in s and bool(CONVERSATION_RE.match(s))


def lock_path(conv: str, base=None) -> Path:
    if not valid_conversation_id(conv):
        raise ValueError("Muse 對話 id 形狀不對")
    return _state_root(base) / BINDINGS_NAME / (conv.strip() + ".lock")


def held_by_names(conv: str, base=None) -> list[str]:
    """這個對話現在被哪幾隻活席占著(只拿來報名字;占不占用以鎖為準,判法同 grokbot/claudebot)。"""
    out = []
    for st in sorted((_state_root(base) / "bridge").glob("*/status.json")):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            if d.get("provider") != PROVIDER or d.get("agent_id") != conv or d.get("state") == "stopped":
                continue
            os.kill(int(d.get("pid")), 0)
        except (OSError, ValueError, TypeError):
            continue
        out.append("@%s" % (d.get("code") or "?"))
    return out


def lock_is_held(conv: str, base=None) -> bool:
    """那把對話鎖現在有沒有被活的橋拿著(判法 = 橋自己搶鎖的那一招,非阻塞試搶;試到就馬上放)。"""
    import fcntl
    p = lock_path(conv, base)
    if not p.exists():
        return False
    try:
        fh = p.open("a")
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        fh.close()
    return False


def last_bound(base=None) -> dict:
    """10-09 a seat(D8):右鍵開 musebot 沒帶對話 id 時的預設 ——「上一次綁過的那段 Muse 對話」。

    為什麼要有:沒帶 id 的 musebot 席開得起來、右欄是綠的,但沒綁 ⇒ Muse 側的看守找不到它,
    別席寄的信顯示已送達、永遠沒人讀(10-09 實量 a seat 堆 7 封、a seat 堆 9 封)。grokbot/claudebot
    有 30 秒選單與「沿用上次」,musebot 沒有。
    取法:bridge/*/status.json 裡 provider=musebot、agent_id 形狀合法者,取 status.json 最新的那一份。
    回 {"conv": id 或 "", "held_by": ["@席碼", …], "held": bool}。held 以鎖為準(同 grokbot/claudebot)。"""
    best = None
    for st in (_state_root(base) / "bridge").glob("*/status.json"):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            mt = st.stat().st_mtime
        except (OSError, ValueError):
            continue
        conv = d.get("agent_id") or ""
        if d.get("provider") != PROVIDER or not valid_conversation_id(conv):
            continue
        if best is None or mt > best[0]:
            best = (mt, conv.strip())
    if best is None:
        return {"conv": "", "held_by": [], "held": False}
    conv = best[1]
    return {"conv": conv, "held_by": held_by_names(conv, base), "held": lock_is_held(conv, base)}


def held_by(conv: str, base=None) -> list[str]:
    """這段 Muse 對話是不是被一隻活 musebot 席占著。空 list = 沒被占。判準 = 鎖(同 grokbot/claudebot 的 held_by)。"""
    if not lock_is_held(conv, base):
        return []
    return held_by_names(conv, base) or ["(占用者名字查不到)"]


def history(base=None) -> list[tuple[str, float]]:
    """綁過的 Muse 對話,新的在前:[(id, 最後一次出現的 status.json 時間)]。
    來源 = bridge/*/status.json 裡 provider=musebot、形狀合法的 agent_id(每段取最新那份)。
    pick_default(右鍵開席沿用)與席上 /bind 清單(10-11 a seat)共用這一份,不各掃一次。"""
    newest: dict[str, float] = {}
    for st in (_state_root(base) / "bridge").glob("*/status.json"):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            mt = st.stat().st_mtime
        except (OSError, ValueError):
            continue
        conv = (d.get("agent_id") or "") if isinstance(d, dict) else ""
        if not isinstance(d, dict) or d.get("provider") != PROVIDER or not valid_conversation_id(conv):
            continue
        conv = conv.strip()
        if mt > newest.get(conv, -1.0):
            newest[conv] = mt
    return [(c, newest[c]) for c in sorted(newest, key=lambda c: (-newest[c], c))]


def pick_default(base=None) -> dict:
    """10-09 a seat(the maintainer 採「四家同一套」):右鍵開 musebot 沒帶 id 時要綁哪一段。
    歷史 = bridge/*/status.json 裡 provider=musebot、形狀合法的 agent_id,每段取最新那份,新的在前。
    從新到舊挑第一段**沒被占**的;以前只看最新那一段,它被占就整席不綁(信沒人收)。
    回 {"conv": 選到的 id 或 "", "history": 歷史段數, "held": [(id, [占用者…]), …](跳過的,新的在前)}。
      conv 非空 = 綁它;conv 空且 history>0 = 每一段都被占(呼叫端不開席);history==0 = 從沒綁過(呼叫端照開不綁)。"""
    order = [c for c, _mt in history(base)]
    held = []
    for conv in order:
        who = held_by(conv, base)
        if not who:
            return {"conv": conv, "history": len(order), "held": held}
        held.append((conv, who))
    return {"conv": "", "history": len(order), "held": held}


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["pick-default"]:
        r = pick_default()
        # 一行三欄給殼讀(\x1f 分隔,理由同 last-bound):選到的 id、歷史段數、跳過的「id 占用者」以「;」相接
        skipped = ";".join("%s %s" % (c, " ".join(w)) for c, w in r["held"])
        print("%s\x1f%d\x1f%s" % (r["conv"], r["history"], skipped))
        sys.exit(0)
    if sys.argv[1:2] == ["last-bound"]:
        r = last_bound()
        # 一行三欄給殼讀,分隔用 \x1f 不用 Tab:Tab 是 IFS 空白,第一欄空的時候 read 會把它吞掉、
        #   後面的 held 被讀進 conv(10-09 寫測試時實踩:沒有歷史 ⇒ conv 讀成 "0")。同 bot-thread-menu.sh P6 的教訓。
        print("%s\x1f%d\x1f%s" % (r["conv"], 1 if r["held"] else 0, " ".join(r["held_by"])))
        sys.exit(0)
    print("usage: musebot_threads.py pick-default | last-bound", file=sys.stderr)
    sys.exit(2)
