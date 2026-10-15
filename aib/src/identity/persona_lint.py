#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""回答語言檢查(DESIGN-memory-tidy-1013 §8.1 ③a;語系盤查第 5 條,a seat 10-14 BUGS-lang-survey,the maintainer 04:3x 派 a seat)。

the maintainer 10-13 13:38:「主要語言,應該比對他的使用語言,第二順位判斷 i18n,不要寫死一種語系」。
目標語言照順序取:
  1. 這一輪真人原話的語言(只看真人打的字:別席 dm、背景通知、hook 回饋、接續摘要、技能全文都不算;
     程式碼區塊、行內碼、網址、路徑不算;太短判不出 ⇒ 往前找最近一則判得出的真人原話)
  2. i18n_lib.resolve_locale()(這台 AIB 的語系設定;沒設就是它的預設)
回覆主體(同樣去掉程式碼)的語言要等於目標語言。zh-TW / zh-CN 用繁簡字表分;ja 看假名、ko 看諺文。
**只記錄、不擋**:結果寫 <狀態根>/state/persona-lint/<席碼>.last(只留最新一則)與 <狀態根>/logs/persona-lint.jsonl
(每一則,留 7 天)。只記語言與字數,不記對話內容。任何錯都吞掉(掛在 Stop hook 上,不能讓人送不出去)。

設計稿 ③ 只在「是回報」時檢查;語言這一條每一則回答都適用,而那支 Stop hook 認回報的條件很嚴(驗得過的抬頭),
所以 ③a 每一輪都跑;b/c/d(第一句、不回就走哪條、待辦行)要詞表,還沒做。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "cockpit") not in sys.path:
    sys.path.append(str(HERE.parent / "cockpit"))      # i18n_lib / seats_dir_lib / seat_address

LOG_KEEP_S = 7 * 86400
TAIL_CHUNK = 1 << 16
MAX_SCAN_BYTES = 8 << 20          # 往回最多讀這麼多找真人原話(長席的對話檔上百 MB)
MAX_HUMAN_ROWS = 20               # 往回最多看幾則真人原話

# 繁簡對照:常用、兩邊字形不同的字(左繁右簡)。判 zh-TW / zh-CN 只數這些字。
_PAIRS = ("這这 個个 們们 說说 時时 會会 來来 對对 國国 過过 還还 開开 關关 與与 為为 現现 實实 經经 "
          "機机 點点 體体 樣样 長长 東东 車车 門门 問问 間间 頭头 見见 進进 動动 電电 話话 讀读 寫写 測测 試试 "
          "檔档 錯错 線线 號号 級级 給给 從从 讓让 種种 將将 專专 業业 確确 認认 變变 傳传 單单 應应 該该 邊边 "
          "處处 條条 標标 題题 據据 歷历 紀纪 錄录 碼码 庫库 網网 頁页 視视 設设 擇择 選选 謝谢 請请 嗎吗 麼么 "
          "沒没 覺觉 學学 員员 帳账 遠远 備备 務务 隊队 發发 氣气 愛爱 聽听 書书 筆笔 韓韩 簡简")
# 日文新字體:繁體、簡體都不這樣寫 ⇒ 漢字多、假名少的短日文(設定変更完了。確認済み)靠它認
JA_KANJI = frozenset("変済図対応関続検証発経気様実価広売読転戦単総歩険駅転揃込払沢浜縦拡")
TRAD = frozenset(p[0] for p in _PAIRS.split())
SIMP = frozenset(p[1] for p in _PAIRS.split())

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
_PATH = re.compile(r"(?:~|\.{1,2})?/[\w.@%+~-]+(?:/[\w.@%+~-]+)+", re.A)   # re.A:中文斜線列舉(繁中/英文)不是路徑
_TAG = re.compile(r"<(system-reminder|command-[\w-]+|local-command-[\w-]+)>.*?</\1>", re.S)
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")

# 不是真人打的字:具名標籤開頭(同 claudebot_hook.INJECTED_PREFIXES)、別席 dm 的寄件頭、hook 回饋、接續摘要
_INJECTED = ("<task-notification", "<system-reminder", "<local-command", "<command-name", "<command-message",
             "[AIB ClaudeBot]", "Stop hook feedback:", "[SYSTEM NOTIFICATION",
             "This session is being continued from a previous conversation",
             "[Request interrupted by user", "規則(AIB 定的")
_DM_HEAD = re.compile(r"^(?:IP:)?[^\n>]{1,120}@[0-9a-z]{4,8} >> ")   # 寄件人名稱可能有空格(Bot a seat)


def _clean(text: str) -> str:
    for rx in (_TAG, _FENCE, _INLINE, _URL, _PATH):
        text = rx.sub(" ", text)
    return text


def detect(text: str):
    """回 'zh-TW' / 'zh-CN' / 'zh'(中文但繁簡分不出)/ 'ja' / 'ko' / 'en',太短或認不出回 None。"""
    if not isinstance(text, str):
        return None
    t = _clean(text)
    kana, hangul, han = len(_KANA.findall(t)), len(_HANGUL.findall(t)), len(_HAN.findall(t))
    latin = len(_LATIN.findall(t))
    if kana >= 3 and kana * 5 >= han:            # 日文假名通常占漢字的一半以上;中文偶爾夾一個「の」不算
        return "ja"
    if hangul >= 3 and hangul >= han:
        return "ko"
    ja_kanji = sum(1 for c in t if c in JA_KANJI)
    if ja_kanji >= 2 or (ja_kanji >= 1 and kana >= 1):   # 繁簡中文都不會出現這些字形
        return "ja"
    # 中文 vs 英文看比例:一個漢字約抵一個英文字(4 個字母);英文句子夾幾個漢字不算中文,中文夾英文名詞仍是中文
    if han >= 6 and han * 4 >= latin:
        trad = sum(1 for c in t if c in TRAD)
        simp = sum(1 for c in t if c in SIMP)
        if trad > simp:
            return "zh-TW"
        if simp > trad:
            return "zh-CN"
        return "zh"
    if latin >= 20:
        return "en"
    return None


def same(target, got) -> bool:
    if target == got:
        return True
    return target.startswith("zh") and got.startswith("zh") and "zh" in (target, got)


def _rows_backwards(path, budget=MAX_SCAN_BYTES):
    """從檔尾往回一列一列吐(已解析的 dict);解析不了的列跳過;讀超過 budget 就停(同 aib-now-gate 的讀法)。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        rest = b""
        read = 0
        while pos > 0 and read < budget:
            n = min(TAIL_CHUNK, pos)
            pos -= n
            read += n
            f.seek(pos)
            buf = f.read(n) + rest
            lines = buf.split(b"\n")
            rest = lines[0]
            for ln in reversed(lines[1:]):
                if ln.strip():
                    try:
                        yield json.loads(ln)
                    except ValueError:
                        continue
        if pos == 0 and rest.strip():
            try:
                yield json.loads(rest)
            except ValueError:
                pass


def human_text(row):
    """這一列若是真人打的字,回那段文字;不是回 None。
    列的判準沿用 aib-now-gate._is_turn_start(isMeta / isCompactSummary / isSidechain / task-notification / tool_result),
    再多排除別席 dm(那是 AI 寫的,語言不代表真人)與注入的具名標籤。"""
    if row.get("type") != "user" or row.get("isCompactSummary") or row.get("isSidechain") or row.get("isMeta"):
        return None
    origin = row.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        return None
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    # 先清掉掛在前面的 <system-reminder> 等標籤區塊再判開頭(不然真人的話前面掛著提醒就整則被丟掉)
    head = _TAG.sub("", text).lstrip()
    if not head or head.startswith(_INJECTED) or _DM_HEAD.match(head):
        return None
    return head


def target_language(transcript_path, locale_fn=None):
    """回 (目標語言, 來源 'human' / 'i18n')。"""
    try:
        seen = 0
        for row in _rows_backwards(transcript_path):
            text = human_text(row)
            if text is None:
                continue
            got = detect(text)
            if got:
                return got, "human"
            seen += 1
            if seen >= MAX_HUMAN_ROWS:
                break
    except OSError:
        pass
    if locale_fn is None:
        import i18n_lib
        locale_fn = i18n_lib.resolve_locale
    return locale_fn(), "i18n"


def check(reply_text, transcript_path, locale_fn=None):
    target, src = target_language(transcript_path, locale_fn)
    got = detect(reply_text)
    return {"target": target, "target_from": src, "got": got,
            "ok": None if got is None else same(target, got),
            "chars": len(reply_text or "")}


def _state_root() -> Path:
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def _append_log(path: Path, rec: dict, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 7 天輪替:最舊那一則過期才整檔重寫(平常只多讀第一行)
    with open(path, encoding="utf-8") as fh:
        first = fh.readline()
    try:
        old = json.loads(first).get("ts", now) < now - LOG_KEEP_S
    except (ValueError, AttributeError):
        old = True
    if not old:
        return
    keep = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            try:
                if json.loads(ln).get("ts", 0) >= now - LOG_KEEP_S:
                    keep.append(ln)
            except (ValueError, AttributeError):
                continue
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text("".join(keep), encoding="utf-8")
    os.replace(tmp, path)


def record_turn(reply_text, transcript_path, session_id=None, root=None, now=None, locale_fn=None):
    """Stop hook 每一輪叫一次。回寫下的那筆;任何錯回 None(不擋)。"""
    try:
        if not (reply_text or "").strip() or not transcript_path:
            return None
        now = time.time() if now is None else now
        rec = check(reply_text, transcript_path, locale_fn)
        import seat_address
        code = seat_address.uuid_tail5(session_id) if session_id else None
        rec.update({"ts": round(now, 3), "seat": code})
        base = Path(root) if root else _state_root()
        _append_log(base / "logs" / "persona-lint.jsonl", rec, now)
        if code:
            last = base / "state" / "persona-lint" / ("%s.last" % code)
            last.parent.mkdir(parents=True, exist_ok=True)
            tmp = last.with_name(last.name + ".%d.tmp" % os.getpid())
            tmp.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, last)
        return rec
    except Exception:  # noqa: BLE001 —— 只記錄的檢查,壞了也不能擋人送出
        return None

