#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格抽查:問一席三題,把它的回答和它綁的人格卡並排給人看(the maintainer 09-30「如何確定綁定人格之後,就會用那個人格做事?」)。

綁定、畫面上的 🎭、「人格已載入 <八碼>」只證明人格卡送到了;照不照做只能問。這支把「怎麼問」與「拿什麼對照」固定下來。

  persona_check.py ask  --seat <艙位碼>/<席碼>     用 aib dm 寄三題(回信開頭要寫「人格抽查回覆」)
  persona_check.py show --seat <艙位碼>/<席碼>     左:人格卡的職責/紅線/語氣;右:那一席最近一封「人格抽查回覆」
                                                 (從我的收件佇列找,長訊會跟著讀落檔)
  共用:--me <席碼>  寄件/收件的是哪一席(預設:本窗格那一席)

不判分:紅線逐條對不對得上由人看;印出「卡上的紅線有幾條、回答裡逐字提到幾條」只是提示,不是結論。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MARK = "人格抽查回覆"
_TR = {}


def T(key, **params):
    """給人與 AI 看的字走 aib.pcheck.* 四語鍵(語系盤查第 17 條,10-14);每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名。
    MARK(人格抽查回覆)是回信的認法、卡片小節名稱(職責 / 紅線 / 語氣與口癖)是卡上的資料 ⇒ 不翻。"""
    try:
        _ck = str(HERE.parent / "cockpit")
        if _ck not in sys.path:   # 每次叫都插一次會讓 sys.path 一直長
            sys.path.insert(0, _ck)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def question(back: str) -> str:
    """抽查題目。回信指令在這裡組(<艙位碼>/<席碼>,test-dm-address-teaching 逐處看過的那一處),再代進四語句子。"""
    return T("aib.pcheck.question", cmd="aib dm %s" % back, mark=MARK)


def card_name(text: str) -> str:
    """人格包身分行裡的卡名。身分行照語系變(aib.ppack.identity),四語的寫法都認;都對不上退回舊的繁中寫法。"""
    try:
        _ck = str(HERE.parent / "cockpit")
        if _ck not in sys.path:   # 每次叫都插一次會讓 sys.path 一直長
            sys.path.insert(0, _ck)
        import i18n_lib
        for loc in i18n_lib.CANONICAL_LOCALES:
            tpl = i18n_lib.t("aib.ppack.identity", None, locale=loc)
            if "{name}" not in tpl or "{uid}" not in tpl:
                continue
            pre, rest = tpl.split("{name}", 1)
            pre = pre.split("{head}", 1)[-1].strip()
            post = rest.split("{uid}", 1)[0]
            m = re.search(re.escape(pre) + r"\s*(.+?)" + re.escape(post), text)
            if m:
                return m.group(1).strip()
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"你綁定的人格卡:([^(]+)\(", text)
    return m.group(1).strip() if m else ""
_SEAT_RE = re.compile(r"^([a-z0-9]{1,4})/@?([0-9a-z]{5})$")


def aib_home() -> Path:
    """狀態根:與認席那一支(persona_reinject_hook)同一個單源 seats_dir_lib.state_root
    (EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),不在這裡另寫一份順位。"""
    import persona_reinject_hook as H
    return H.aib_home()


def my_seat() -> tuple[str, str]:
    """(艙位碼, 席碼) of the pane this runs in; ("", "") when unknown."""
    import persona_reinject_hook as H
    home = H._instance_home()
    uuid = H.seat_session_uuid()
    if not home or not uuid:
        return "", ""
    return home.name.rsplit("-", 1)[-1][-4:], uuid[-5:]


def seat_uuid(cabin: str, code: str) -> str:
    """找那一席的 session_uuid:所有艙的席位檔裡,艙位碼與席碼都對得上的那一個。"""
    for f in sorted(glob.glob(str(aib_home() / "instances" / ("*-%s" % cabin) / "seats" / "w*.json"))):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        u = str((rec or {}).get("session_uuid") or "")
        if u.endswith(code):
            return u
    return ""


def card_sections(uuid: str) -> dict:
    import persona_pack
    pack = persona_pack.render_pack(uuid, aib_home())
    if pack.get("status") != "ok":
        return {"status": pack.get("status"), "name": "", "sha8": "", "parts": {}}
    text = pack["text"]
    name = card_name(text)
    parts, cur = {}, None
    for ln in text.split("\n"):
        m = re.match(r"^##\s*(.+)$", ln)
        if m:
            cur = m.group(1).strip()
            parts[cur] = []
        elif cur and ln.strip() and not ln.startswith(("──", "[AIB")):
            parts[cur].append(ln.rstrip())
    return {"status": "ok", "name": name, "sha8": pack.get("persona_sha8"),
            "parts": parts}


def latest_reply(me_code: str, code: str, since: float = 0) -> tuple[float, str]:
    """我收件佇列裡,那一席寄來、開頭是 MARK 的最近一封(長訊讀落檔)。"""
    best = (0.0, "")
    for f in glob.glob(str(aib_home() / "state" / "dm-queue" / me_code / "*.json")):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or ("@%s" % code) not in str(d.get("sender_label") or ""):
            continue
        ts = float(d.get("ts") or 0)
        if ts < since or ts <= best[0]:
            continue
        msg = str(d.get("msg") or "")
        m = re.search(r"請讀:(\S+\.txt)", msg)
        if m and "長訊已落檔" in msg:
            try:
                msg = Path(m.group(1)).read_text(encoding="utf-8")
            except OSError:
                continue
        if msg.lstrip().startswith(MARK):
            best = (ts, msg.strip())
    return best


def cmd_ask(seat: str, me: str) -> int:
    m = _SEAT_RE.fullmatch(seat or "")
    if not m:
        print(T("aib.pcheck.bad_seat"), file=sys.stderr)
        return 2
    if "/" in (me or ""):
        back = me                              # 明給完整地址:不必在席位裡跑(背景腳本也能用)
    else:
        my_cab, my_code = my_seat()
        back = "%s/%s" % (my_cab, me or my_code) if (my_cab and (me or my_code)) else ""
    if not back:
        print(T("aib.pcheck.no_me"), file=sys.stderr)
        return 2
    p = subprocess.run(["aib", "dm", seat, question(back), "--verify"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    print(last)
    state = aib_home() / "state" / "persona-check"
    state.mkdir(parents=True, exist_ok=True)
    (state / ("%s.json" % m.group(2))).write_text(json.dumps({"asked_at": time.time(), "seat": seat, "reply_to": back}),
                                                  encoding="utf-8")
    return 0 if "stage=delivered" in last else 3


def cmd_show(seat: str, me: str) -> int:
    m = _SEAT_RE.fullmatch(seat or "")
    if not m:
        print(T("aib.pcheck.bad_seat"), file=sys.stderr)
        return 2
    cabin, code = m.groups()
    uuid = seat_uuid(cabin, code)
    card = card_sections(uuid) if uuid else {"status": "seat_not_found", "parts": {}}
    me_code = (me.split("/")[-1] if me else my_seat()[1]).lstrip("@")
    try:
        asked = json.loads((aib_home() / "state" / "persona-check" / ("%s.json" % code)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        asked = {}
    ts, reply = latest_reply(me_code, code, float(asked.get("asked_at") or 0)) if me_code else (0, "")
    print(T("aib.pcheck.card_head", name=card.get("name") or "?", sha=card.get("sha8") or "-",
            detail="" if card.get("status") == "ok" else T("aib.pcheck.card_unreadable", status=card.get("status"))))
    for key in ("職責", "紅線", "語氣與口癖"):
        lines = card["parts"].get(key) or []
        print("\n" + T("aib.pcheck.on_card", name=key))
        print("\n".join(lines) if lines else T("aib.pcheck.not_on_card"))
    print("\n" + T("aib.pcheck.answer_head", target=seat, when=time.strftime("(%m-%d %H:%M)", time.localtime(ts)) if ts else ""))
    print(reply or T("aib.pcheck.no_answer", mark=MARK))
    reds = [ln.lstrip("-• ").strip() for ln in card["parts"].get("紅線") or [] if ln.strip()]
    if reds and reply:
        hit = sum(1 for r in reds if any(w in reply for w in re.findall(r"[一-鿿]{4,}", r)[:2]))
        print("\n" + T("aib.pcheck.hint", total=len(reds), hit=hit))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=T("aib.pcheck.prog_desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "show"):
        p = sub.add_parser(name)
        p.add_argument("--seat", required=True)
        p.add_argument("--me", default="")
    a = ap.parse_args(argv)
    return cmd_ask(a.seat, a.me) if a.cmd == "ask" else cmd_show(a.seat, a.me)


if __name__ == "__main__":
    sys.exit(main())
