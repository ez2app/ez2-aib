#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""第 70 項 P5:每個 turn 開頭把「待審區未讀 N 則」注入上下文(UserPromptSubmit hook)。

規矩 3(每席每輪先 list 佇列)的機器版:第 149 項那晚 11 則裁定卡三小時,就是因為「要記得看」沒有機器在守。
形狀照 src/claimtruth/ctx-handoff-nudge.sh:stdin 是 hook 的 JSON(含 session_id),stdout 印的字進上下文;
沒信就一個字都不印(不轟炸);讀不到印「讀不到:<原因>」不印空(病 3)。

席碼 = session_id 尾五碼(aib dm 的目標單軌 B11;seat_id 不是它——09-13 本席就查錯了一整晚)。
只認 Claude 的 UserPromptSubmit;Codex 等引擎沒有同等接點,量不到就不裝、不假裝(接口 §3 P5)。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bootstrap"))

SID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
SEAT_RE = re.compile(r"^[0-9a-z]{4,8}$")


def seat_of(session_id):
    sid = session_id.strip() if isinstance(session_id, str) else ""
    if not SID_RE.fullmatch(sid):
        return None
    tail = sid[-5:].lower()
    return tail if SEAT_RE.fullmatch(tail) else None


def brief_line(seat, env=None, now=None):
    """一行給人看的摘要;沒信回空字串。"""
    import dm_queue as Q
    try:
        b = Q.brief_payload(seat, env=env, now=now)
    except Exception as exc:  # noqa: BLE001 — hook 不准炸,炸了整個 turn 會被吃掉
        return "📬 待審區讀不到:%s(席 %s)" % (exc.__class__.__name__, seat)
    parts = []
    if b.get("errors"):
        parts.append("佇列有 %d 個檔讀不到" % b["errors"])
    if b.get("unread"):
        head = "未讀 %d 則" % b["unread"]
        if b.get("oldest_when"):
            head += "(最早 %s)" % b["oldest_when"]
        parts.append(head)
    for key, label in (("decide", "要裁"), ("update", "有新版"), ("hold", "待人處理"), ("human", "真人"), ("expired", "已到期")):
        if b.get(key):
            parts.append("%s %d" % (label, b[key]))
    stale = ""
    if b.get("stale_wait"):
        # Andy 10-25 20:1x:進行中逾 4 小時沒動、沒寫等什麼 ⇒ 提醒(只提醒,不自動結)
        #   #380 r1 L6:跟 aib now 共用四語字串 aib.now.dm_stale_wait(不硬編中文);字典讀不到才退回中文
        try:
            import i18n_lib
            stale = i18n_lib.Translator(i18n_lib.resolve_locale()).t(
                "aib.now.dm_stale_wait", {"n": int(b["stale_wait"]), "code": seat})
        except Exception:  # noqa: BLE001 — hook 不准炸
            stale = ""
        if not stale or stale == "aib.now.dm_stale_wait":
            stale = "⏳ 進行中逾 4 小時沒寫等什麼 %d 封 —— dm_queue.py tabs --seat %s 看 stale_wait 那幾列" % (b["stale_wait"], seat)
    if not parts:
        return stale
    # 10-05 a seat:預設 list 只列還在投遞佇列裡的信,已送達未讀的看不到(照舊提示跑會得到 []);
    #   標讀過要 --by seat:<碼> 與 --if-rev,舊提示都沒教 ⇒ 兩條指令照抄就能用。
    line = ("📬 待審區 @%s:%s —— 先 `python3 %s/dm_queue.py list --seat %s --tab waiting`,"
            "讀完逐封 `python3 %s/dm_queue.py read --seat %s --id <id> --by seat:%s --if-rev <rev>`") % (
        seat, ";".join(parts), _HERE, seat, _HERE, seat, seat)
    try:
        import turn_order
        pos, rec, _why = turn_order.position(seat, env)
        if rec:
            line += ";順位 %s/%d(rev %d)" % (pos if pos else "-", len(rec["order"]), rec.get("rev") or 0)
    except Exception:  # noqa: BLE001 — 順位讀不到不影響未讀那一句
        pass
    tri = triage_line(seat, env=env, now=now)
    if tri:
        line += "\n" + tri
    if stale:
        line += "\n" + stale
    return line


def triage_line(seat, env=None, now=None):
    """the maintainer 09-30 17:2x:排空器替這一席做過程式歸納(有 triage.json)⇒ 多印一行分組封數;沒有就空字串。
    只算「現在還在待審」的封數(分組檔可能比佇列舊一拍)。讀不到不印(那一行只是輔助,未讀那一句才是主角)。"""
    import dm_queue as Q
    try:
        c = Q.triage_counts(seat, env=env, now=now)
    except Exception:  # noqa: BLE001 — hook 不准炸
        return ""
    if not c or not sum(int(v or 0) for v in c.values()):
        return ""
    import dm_triage as TR
    return "📬 分組(程式歸納):%s —— `python3 %s/dm_queue.py triage --seat %s` 看每組是哪幾封" % (
        TR.counts_text(c), _HERE, seat)


def main(argv=None, stdin=None, env=None):
    stdin = sys.stdin if stdin is None else stdin
    env = os.environ if env is None else env
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        return 0
    seat = seat_of(payload.get("session_id") if isinstance(payload, dict) else None)
    if not seat:
        return 0
    line = brief_line(seat, env=env)
    if line:
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
