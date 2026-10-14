#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib handoff —— 每一席收尾的固定三步交接(the maintainer 10-01:對話用量到 85% 或說「準備 clear」時必做,不可跳過)。

the maintainer 的三步:
  一、問自己「還有什麼沒想到 / 我建議什麼」 → 寫下來 → 更新人格記憶(席位記憶)與專案記憶(第二大腦)
  二、問自己「下一個 AI 會不知道什麼」       → 寫下來 → 再更新一次
  三、「下一輪怎麼從上面這些重新開始」       → 最後總結 + 可直接貼的交接文字 + 「我會如何開始」 → 可以 /clear

  aib handoff collect [--seat <艙位碼>/<席碼>] [--repo DIR] [--since-hours N] [--out FILE] [--json]
      只量機器事實(不做判斷),印一份草稿:三段待填的標題 + 機器事實段。
      collect 留戳記 state/handoff/<席碼>.collects.jsonl {ts, nonce};nonce 印在草稿頭(第二行)。
  aib handoff write --step 1|2|3 --file <填好的草稿> [--seat …]
      --step 必給(r17 M1:沒有「三步一次做完」)。驗:草稿頭的席 = 這一席、nonce = 這一輪最後一次 collect;
      機器事實段還在、而且就是 collect 印的那一份(collect 戳記記它的雜湊 facts_sha);這一步要的每一段至少
      MIN_SECTION_LINES 行、MIN_SECTION_CHARS 個看得見的字、MIN_DISTINCT_CHARS 種不同的字、最常見的那個字不超過
      MAX_TOP_CHAR_SHARE;刪掉草稿自己的提示字(四個語系的待填問題、段標題)之後還要剩 MIN_OWN_CHARS 個字(r19);
      已經填了的段兩兩相似度不到 MAX_PAIR_SIMILARITY(r19:第 1 步就比,不等第 2 步);三段和上一輪交接檔
      一字不差 ⇒ 拒(r19:不比相似度 —— 誠實的「這一輪沒變、還在等 the maintainer」本來就會像上一輪)。不合 ⇒ rc 2,什麼都不寫。步驟要照順序,而且是同一份草稿(同一個 nonce;
      前一步寫過的段,後一步一字不能改 —— steps.json 記每段的雜湊)。
      每一步都寫:第二大腦(aib memory write --kind handoff 同一支 Python)、席位記憶 memory/<席碼>/context.md。
      第三步再寫:pending/HANDOFF-<席碼>-<MMDD-HHMM>.md(開頭插 handoff-fields 六格圍欄,doctor 與右欄讀它)與收據
      state/handoff/<席碼>.json {seat, session_uuid, ts, file, sha, collect_ts, collect_nonce, writer_seat, seat_mismatch}。
      收據算數 = 在那一輪裡、而且用的是那一輪最後一次 collect(寫完又 collect 卻沒再寫 ⇒ 不算)。
  aib handoff status [--seat …] [--json]
      這一輪有沒有收據、上一輪有沒有留收據(或被自動補抓)。

「一輪」= 這一席從某個開始點(Claude 的 /clear、開席、載入舊席位)到下一個開始點。開始點記在
state/handoff/<席碼>.rounds.jsonl。下一輪開始時(round_start)檢查上一輪有沒有收據;沒有 ⇒ 記一個缺漏旗標
state/handoff/<席碼>.missed.json、在背景起 `collect --auto` 存一份 pending/HANDOFF-<席碼>-AUTO-<時刻>.md
(脫離呼叫端、不等它)、回一段提醒文字,呼叫端(Claude 的 SessionStart hook、seat_onboard、seat_load)把它放進新對話。
★ r17 M2:檢查本身只讀幾個小檔(毫秒級),不跑 git —— SessionStart hook 只有 10 秒,人格包與基因要先補。
  collect 有總時限 AIB_HANDOFF_COLLECT_BUDGET_S(預設 8 秒),每一支 git 都帶 GIT_OPTIONAL_LOCKS=0
  (它會在別席的工作樹跑 git status,不能去搶別人的 index.lock)。
  不算缺漏:上一輪在本機第一次跑交接檢查(state/handoff/enabled_at)之前就開始;上一輪真人一句話都沒講(連按兩次 /clear,
  只有 Claude 量得到:開始點記了對話檔路徑,而且那個檔在、裡面沒有真人訊息;檔不在 ⇒ 照查,r18 M2)。
  ★ r18 M2:Claude 的 /resume(SessionStart source=resume)不是新的一輪,但對話換成接回的那一份 ⇒
  rounds.jsonl 記一行 {"resume_at", "transcript"},併進目前這一輪的對話檔清單;下一次 /clear 每一份都看。
  ★ r19:接回的那一份只算接回之後的真人訊息(記下接回當下的檔案大小 offset,從那裡往後讀;有 timestamp 的
  訊息早於接回時刻也不算)—— 不然接回一份舊對話、什麼都沒講就 /clear,會被舊對話裡的話誤報成跳過交接。
  API 席與橋接 bot 自己不能跑指令 ⇒ 提醒換說法,請真人代跑。
★ 85% 自動觸發還沒接(v0.70.0 之後);接點 = 呼叫 `aib handoff collect` 或本檔 nudge_text()。

狀態根走單源 seats_dir_lib.state_root(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),測試可整個搬走。
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
REPO_ROOT = SRC.parent
for _p in (HERE, SRC / "memory", SRC / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

SCHEMA = "aib-handoff/v1"
SECTIONS = ("s1", "s2", "s3")
FACTS = "facts"
MARK_RE = re.compile(r"<!--\s*handoff:(s1|s2|s3|facts)\s*-->")
TODO_PREFIX = "> TODO-HANDOFF"
CODE_RE = re.compile(r"^[0-9a-z]{5}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STEP_NEEDS = {1: ("s1",), 2: ("s1", "s2"), 3: SECTIONS}
HEADER_RE = re.compile(r"<!--\s*handoff:draft\s+v(\d+)\s+seat=(\S*)\s+collected=(\S*)(?:\s+nonce=([0-9a-f]+))?(\s+auto=1)?\s*-->")
# 每一段「有寫東西」的最低門檻(r17 M1;刻意放寬,只擋「一個字」「同一句貼三次」這種敷衍):
#   至少 2 行有字、至少 40 個看得見的字元(不算空白)。
MIN_SECTION_LINES = 2
MIN_SECTION_CHARS = 40
# r18 M1(一樣刻意放寬,只擋湊字數):
#   每一段至少 12 種不同的看得見的字(「aaaa…」湊到 40 個字擋掉);最常見的那一個字不能超過看得見的字的一半;
#   兩段之間相似度(difflib 比對空白壓平後的字)到 0.8 就算同一段改幾個字。
# r19:
#   ・拿掉「和上一輪交接檔相似 0.9 就擋」:誠實的「這一輪沒變、還在等 the maintainer」會被擋(改寫過也一樣)。
#     只留便宜的一條:三段和上一輪交接檔一字不差(空白壓平後)⇒ 拒。AB 輪交、倒序的舊稿接受(收據留全文給真人看)。
#   ・提示字不算內容:草稿的待填問題與段標題(四個語系)切成句子,從每一段刪掉之後,至少要剩 MIN_OWN_CHARS 個
#     字母或數字(標點不算)。拿掉「> TODO-HANDOFF」前綴把提示字貼回來 ⇒ 剩不到 ⇒ 拒。短於 MIN_PROMPT_PIECE 的句子不刪。
#   ・第 1 步就比已經填了的段(不只這一步要的段):三段幾乎一樣,第 1 步就擋,不等寫進記憶之後第 2 步才擋。
MIN_DISTINCT_CHARS = 12
MAX_TOP_CHAR_SHARE = 0.5
MAX_PAIR_SIMILARITY = 0.8
MIN_OWN_CHARS = 20
MIN_PROMPT_PIECE = 4
FACTS_SHA_LEN = 16
DEFAULT_SINCE_HOURS = 24
COLLECT_BUDGET_ENV = "AIB_HANDOFF_COLLECT_BUDGET_S"
DEFAULT_COLLECT_BUDGET_S = 8.0
MAX_WORKTREES = 40
MAX_BRANCHES = 30
RC_OK, RC_USAGE, RC_IO = 0, 2, 3
BG_RE = re.compile(r"reachability-scan\.py|\baib test\b")


class HandoffError(Exception):
    """rc 2:用法錯、草稿不合、步驟順序不對、認不出席。什麼都沒寫。"""


# ---------------------------------------------------------------- 字
_TR = {}


def T(key: str, **params) -> str:
    loc = os.environ.get("EZ2_AIB_LOCALE") or ""
    tr = _TR.get(loc)
    if tr is None:
        try:
            from i18n_lib import Translator
            tr = Translator()
        except Exception:  # noqa: BLE001 —— 字典讀不到就印鍵名(可見即可抓),不擋交接
            tr = False
        _TR[loc] = tr
    if not tr:
        return key
    try:
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def _out(text: str, err: bool = False) -> None:
    (sys.stderr if err else sys.stdout).write(text + "\n")


# ---------------------------------------------------------------- 路徑
def state_root() -> Path:
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def _home(home=None) -> Path:
    return Path(home) if home else state_root()


def handoff_dir(home=None) -> Path:
    return _home(home) / "state" / "handoff"


def receipt_path(code: str, home=None) -> Path:
    return handoff_dir(home) / ("%s.json" % code)


def receipts_log(code: str, home=None) -> Path:
    return handoff_dir(home) / ("%s.receipts.jsonl" % code)


def steps_path(code: str, home=None) -> Path:
    return handoff_dir(home) / ("%s.steps.json" % code)


def rounds_path(code: str, home=None) -> Path:
    return handoff_dir(home) / ("%s.rounds.jsonl" % code)


def collects_log(code: str, home=None) -> Path:
    """每一次 `aib handoff collect` 的戳記 {ts, nonce, round_ts}(r17 M1:收據要對得上這一輪最後一次 collect)。"""
    return handoff_dir(home) / ("%s.collects.jsonl" % code)


def collect_facts_path(code: str, home=None) -> Path:
    """最後一次 collect 的完整機器事實(write 第三步拿它填 handoff-fields 六格)。"""
    return handoff_dir(home) / ("%s.collect.json" % code)


def missed_path(code: str, home=None) -> Path:
    return handoff_dir(home) / ("%s.missed.json" % code)


def pending_dir(home=None) -> Path:
    return _home(home) / "pending"


def seat_memory_path(code: str, home=None) -> Path:
    """與人格包告訴那一席的同一條:~/.ez2-aib/memory/<席碼>/context.md(persona_pack 寫回段、
    create_and_bind_persona.memory_key_of 都用席碼)。"""
    return _home(home) / "memory" / code / "context.md"


def log_path(home=None) -> Path:
    return _home(home) / "logs" / "handoff.jsonl"


def _log(event: str, home=None, **fields) -> None:
    try:
        p = log_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(at=int(time.time()), event=event, **fields), ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _append_jsonl(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")


def _stamp(ts: float, fmt: str = "%m-%d %H:%M") -> str:
    return time.strftime(fmt, time.localtime(ts)) if ts else "-"


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 認席
def find_seat_record(code: str, home=None) -> dict:
    """席碼 ⇒ 席位檔(含 retired/);找不到回 {}。"""
    root = _home(home) / "instances"
    for pat in ("*/seats/w*.json", "*/seats/retired/*.json"):
        for f in sorted(root.glob(pat)):
            rec = _read_json(f)
            if isinstance(rec, dict) and str(rec.get("session_uuid") or "").endswith(code):
                rec = dict(rec)
                rec.setdefault("_cabin", f.parent.parent.name.rsplit("-", 1)[-1][-4:]
                               if f.parent.name == "seats" else f.parent.parent.parent.name.rsplit("-", 1)[-1][-4:])
                rec["_file"] = str(f)
                return rec
    return {}


def resolve_seat(seat: str = "") -> dict:
    """{"code","cabin","session_uuid","project_uid","persona_uid","record"}。認不出 ⇒ HandoffError。
    沒給 --seat 時與 aib memory 同一條(TMUX_PANE ⇒ 窗號 ⇒ 席位檔)。"""
    try:
        import aib_memory
        ctx = aib_memory.seat_context(seat)
    except Exception as exc:  # noqa: BLE001
        raise HandoffError(T("aib.handoff.err.seat", why=str(exc)))
    code = ctx.get("code") or ""
    if not CODE_RE.fullmatch(code):
        raise HandoffError(T("aib.handoff.err.seat", why=seat or "TMUX_PANE"))
    rec = find_seat_record(code)
    return {"code": code, "cabin": ctx.get("cabin") or rec.get("_cabin") or "",
            "session_uuid": ctx.get("seat_uid") or str(rec.get("session_uuid") or ""),
            "project_uid": ctx.get("project_uid") or "", "persona_uid": ctx.get("persona_uid") or "",
            "record": rec}


# ---------------------------------------------------------------- 一輪
def rounds(code: str, home=None) -> list:
    """每一輪的開始點。r18 M2:{"resume_at", "transcript"} 那種行(Claude /resume)不是新的一輪,
    它的對話檔併進前一筆的 "transcripts"(前面沒有開始點 ⇒ 丟掉:那一輪本來就量不到對話檔、照查)。"""
    out = []
    try:
        lines = rounds_path(code, home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if isinstance(d.get("ts"), (int, float)):
            out.append(dict(d))
        elif isinstance(d.get("resume_at"), (int, float)) and out and d.get("transcript"):
            out[-1].setdefault("transcripts", []).append(str(d["transcript"]))
            off = d.get("offset")
            out[-1].setdefault("resumes", []).append({
                "transcript": str(d["transcript"]), "at": float(d["resume_at"]),
                "offset": int(off) if isinstance(off, int) and off > 0 else 0})
    return out


def current_round_start(code: str, home=None) -> float:
    rs = rounds(code, home)
    return float(rs[-1]["ts"]) if rs else 0.0


def receipts(code: str, home=None) -> list:
    out = []
    try:
        lines = receipts_log(code, home).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)):
            out.append(d)
    last = _read_json(receipt_path(code, home))
    if isinstance(last, dict) and isinstance(last.get("ts"), (int, float)) and last not in out:
        out.append(last)
    return out


def collects(code: str, home=None) -> list:
    out = []
    try:
        lines = collects_log(code, home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)) and d.get("nonce"):
            out.append(d)
    return out


def last_collect_between(code: str, start: float, end: float | None = None, home=None) -> dict:
    hits = [c for c in collects(code, home) if c["ts"] >= start and (end is None or c["ts"] < end)]
    return max(hits, key=lambda c: c["ts"]) if hits else {}


def receipt_between(code: str, start: float, end: float | None = None, home=None) -> dict:
    """[start, end) 之間最新的一張「算數」的收據;沒有回 {}。
    算數(r17 M1):收據在這一輪裡、它用的那次 collect 也在這一輪裡,而且就是這一輪**最後一次** collect
    (先 collect、寫完收據、之後又 collect 卻沒再寫 ⇒ 不算:最後量到的事實沒有交接出去)。"""
    last = last_collect_between(code, start, end, home)
    if not last:
        return {}
    hits = [r for r in receipts(code, home)
            if r["ts"] >= start and (end is None or r["ts"] < end)
            and isinstance(r.get("collect_ts"), (int, float)) and start <= r["collect_ts"] <= r["ts"]
            and r.get("collect_nonce") == last["nonce"]]
    return max(hits, key=lambda r: r["ts"]) if hits else {}


def enabled_at(home=None) -> float:
    """這台機器第一次跑到交接檢查的時刻(state/handoff/enabled_at;沒有就現在寫)。
    在它之前就開始的一輪不算缺漏:那一輪的 AI 開席時還沒有這條規矩。狀態根跟著 EZ2_AIB_STATE_ROOT/HOME 走,沙盒各自一份。"""
    p = handoff_dir(home) / "enabled_at"
    try:
        return float(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    # 10-05:以前存 "%.3f" % now(四捨五入到毫秒)⇒ 進位時存下的時刻比真的晚,緊接著記下的那一輪開始點
    #   就會「早於啟用」被跳過(推前檢查裡 E1 偶發紅)。往下取到毫秒:存下的永遠不晚於真的時刻。
    now = math.floor(time.time() * 1000) / 1000
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
        tmp.write_text("%.3f\n" % now, encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        _log("enabled_at_failed", home, why=repr(exc)[:200])
    return now


TRANSCRIPT_SCAN_BYTES = 8 * 1024 * 1024
RESUME_SKEW_S = 5.0      # r19:timestamp 早於接回時刻這麼多秒以上才算舊對話搬過來的(時鐘、毫秒捨入的餘裕)
_NOT_ACTIVITY = ("<command-name>", "<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>")


def round_transcripts(entry: dict) -> list:
    """這一輪要量的對話檔 [(路徑, 從第幾個位元組讀, 只算這個時刻之後的訊息)]:
    開始點記的那一份(整份、不限時刻)+ 之後每一次 /resume 接回的那一份(r18 M2;r19:只算接回之後)。"""
    e = entry or {}
    out = [(str(e["transcript"]), 0, 0.0)] if e.get("transcript") else []
    marks = [m for m in e.get("resumes") or [] if isinstance(m, dict) and m.get("transcript")]
    if marks:
        return out + [(str(m["transcript"]), int(m.get("offset") or 0), float(m.get("at") or 0.0)) for m in marks]
    return out + [(str(t), 0, 0.0) for t in e.get("transcripts") or [] if t]


def round_was_empty(entry: dict) -> bool:
    """這一輪(rounds 紀錄)真人有沒有講過話。只有 Claude 的 /clear 記得到對話檔(transcript);量不到 ⇒ False(照查)。
    每一份對話檔(含 /resume 接回的)都要量得到、而且都沒有真人訊息,才算空的一輪(r18 M2)。
    r19:接回的那一份只看接回之後(舊對話裡本來就有的話不算這一輪講的)。"""
    tps = round_transcripts(entry)
    return bool(tps) and all(_transcript_empty(tp, off, since) for tp, off, since in tps)


def _msg_epoch(d: dict):
    """對話檔一行的 timestamp(ISO,Claude Code 寫的)換成 epoch 秒;沒有或看不懂 ⇒ None。"""
    ts = d.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _transcript_empty(tp: str, offset: int = 0, since: float = 0.0) -> bool:
    """這一份對話檔裡沒有真人訊息。找第一則:type=user、不是 isMeta、不是 /clear 這類本機指令的字。
    ★ r18 M2:檔不在 ⇒ 不是空的(照查)—— 接回別的對話、檔被搬走,都不能讓一整輪被放過。
    ★ r19:offset = 接回當下的檔案大小,從那裡往後讀(檔比 offset 小 = 被換過 ⇒ 從頭讀);since = 接回時刻,
    timestamp 早於它的訊息是舊對話搬過來的,不算(沒有 timestamp ⇒ 算,不冒險放過)。
    最多讀 8MB(讀到上限還沒找到 ⇒ 當作有人講過,不冒險放過)。"""
    try:
        fh = open(tp, "rb")
    except OSError:
        return False
    read = 0
    with fh:
        try:
            if offset > 0 and os.fstat(fh.fileno()).st_size >= offset:
                fh.seek(offset)
        except OSError:
            pass
        for raw in fh:
            read += len(raw)
            if read > TRANSCRIPT_SCAN_BYTES:
                return False
            if b'"user"' not in raw:       # 便宜的先篩(不管 JSON 有沒有空白),再解析
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "user" or d.get("isMeta"):
                continue
            at = _msg_epoch(d) if since else None
            if at is not None and at < since - RESUME_SKEW_S:
                continue
            c = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
            s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False) if c else ""
            if s.strip() and not s.lstrip().startswith(_NOT_ACTIVITY):
                return False
    return True


# 不能自己跑指令的席(API 席、橋接 bot):提醒要請真人代跑。會跑指令的 CLI 家 = 開席自取的家 + copilot。
CLI_PROVIDERS = frozenset(("claude", "codex", "agy", "grok", "copilot"))


def _needs_human(provider: str) -> bool:
    return bool(provider) and provider not in CLI_PROVIDERS


def notice_text(code: str, prev_start: float, auto_file: str, who: str = "", provider: str = "") -> str:
    """缺漏提醒(要過 R-30:不用框線、圈號、全形英數)。who 非空 ⇒ 說的是前身那一席。
    provider 是不能跑指令的席(API/橋接)⇒ 換一種說法:請真人代跑。"""
    key = "aib.handoff.notice.predecessor" if who else "aib.handoff.notice.self"
    if _needs_human(provider):
        key += "_api"
    return T(key, code=code, since=_stamp(prev_start), file=auto_file or T("aib.handoff.notice.no_file"))


def collect_budget() -> float:
    """collect 的總時限(秒):AIB_HANDOFF_COLLECT_BUDGET_S,預設 8。每一支 git/ps/租約呼叫的逾時都夾在剩下的時間內。"""
    try:
        v = float(os.environ.get(COLLECT_BUDGET_ENV) or DEFAULT_COLLECT_BUDGET_S)
    except ValueError:
        v = DEFAULT_COLLECT_BUDGET_S
    return v if v > 0 else DEFAULT_COLLECT_BUDGET_S


def _spawn_archive_sweep() -> None:
    """背景跑 memory_archive archive-sweep(DESIGN-memory-tidy-1013 §5.4);任何失敗都吞掉,不影響交接。"""
    try:
        import memory_archive
        memory_archive.spawn_background()
    except Exception:  # noqa: BLE001
        pass


def spawn_auto_collect(code: str, repo: str, out_file: str, home=None) -> int:
    """在背景(脫離呼叫端、不等它)跑 `aib_handoff.py collect --auto`,把機器事實存成 out_file。回 pid;起不來 ⇒ 0。
    stdin/stdout/stderr 都接 /dev/null、自己一個 session(呼叫端的 hook 結束、終端關掉都不影響它)。"""
    args = [sys.executable, str(HERE / "aib_handoff.py"), "collect", "--auto", "--seat", code, "--out", out_file]
    if repo:
        args += ["--repo", repo]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0")
    if home:
        env["EZ2_AIB_STATE_ROOT"] = str(home)
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, start_new_session=True, env=env, cwd="/")
    except OSError as exc:
        _log("auto_collect_spawn_failed", home, seat=code, why=repr(exc)[:300])
        return 0
    return p.pid


def check_previous(code: str, prev_start: float, *, cwd: str = "", source: str = "", home=None,
                   who: str = "", prev_entry: dict | None = None, provider: str = "") -> str:
    """上一輪(prev_start 起)有沒有收據;沒有 ⇒ 記缺漏旗標、背景補抓事實(不等)、回提醒。有 ⇒ ""。
    ★ 只讀幾個小檔、起一個背景行程:毫秒級(Claude 的 SessionStart hook 在等它;r17 M2)。
    不算缺漏:上一輪在本機第一次啟用交接檢查之前就開始(enabled_at);上一輪真人一句話都沒講(連按兩次 /clear)。
    同一個 prev_start 已經記過缺漏 ⇒ 沿用那份檔,不再補抓(seat_load 與 seat_onboard 會先後問同一席)。"""
    if receipt_between(code, prev_start, home=home):
        return ""
    if prev_start < enabled_at(home):
        _log("skip_before_enabled", home, seat=code, prev_round_ts=prev_start, source=source)
        return ""
    if prev_entry is not None and round_was_empty(prev_entry):
        _log("skip_empty_round", home, seat=code, prev_round_ts=prev_start, source=source)
        return ""
    flag = _read_json(missed_path(code, home))
    if isinstance(flag, dict) and flag.get("prev_round_ts") == prev_start and flag.get("auto_file"):
        return notice_text(code, prev_start, flag["auto_file"], who, provider)
    now = time.time()
    auto_file, pid = "", 0
    try:
        pd = pending_dir(home)
        pd.mkdir(parents=True, exist_ok=True)
        target = _unique(pd / ("HANDOFF-%s-AUTO-%s.md" % (code, time.strftime("%m%d-%H%M%S", time.localtime(now)))))
        auto_file = str(target)
        pid = spawn_auto_collect(code, cwd, auto_file, home)
        if not pid:
            auto_file = ""
    except Exception as exc:  # noqa: BLE001 —— 補抓起不來也要記旗標、回提醒,不擋開場
        _log("auto_collect_failed", home, seat=code, why=repr(exc)[:300])
        auto_file = ""
    try:
        _write_json(missed_path(code, home), {"schema": SCHEMA, "seat": code, "ts": now, "prev_round_ts": prev_start,
                                              "auto_file": auto_file, "auto_pid": pid, "source": source})
    except OSError as exc:
        _log("missed_flag_failed", home, seat=code, why=repr(exc)[:300])
    _log("missed", home, seat=code, prev_round_ts=prev_start, auto_file=auto_file, auto_pid=pid, source=source)
    return notice_text(code, prev_start, auto_file, who, provider)


def record_round(code: str, source: str, home=None, ts: float | None = None, transcript: str = "",
                 session_id: str = "") -> float:
    ts = time.time() if ts is None else ts
    doc = {"ts": ts, "source": source}
    if transcript:
        doc["transcript"] = transcript
    if session_id:
        doc["session_id"] = session_id
    _append_jsonl(rounds_path(code, home), doc)
    return ts


def round_resume(session_uuid: str, transcript: str, session_id: str = "", home=None) -> bool:
    """Claude 的 /resume(SessionStart source=resume):不是新的一輪,不查;把接回的那份對話檔併進目前這一輪
    (rounds.jsonl 記一行 {"resume_at", "transcript", "session_id"};rounds() 併進前一筆)。毫秒級(附加一行)。
    沒有對話檔路徑 ⇒ 不記(本來就量不到 ⇒ 照查)。回有沒有記。
    r19:記下接回當下那份檔的大小(offset):下一次 /clear 只量接回之後的真人訊息。"""
    code = str(session_uuid or "")[-5:]
    if not CODE_RE.fullmatch(code) or not transcript:
        return False
    try:
        offset = os.path.getsize(transcript)
    except OSError:
        offset = 0
    doc = {"resume_at": time.time(), "transcript": transcript, "offset": offset}
    if session_id:
        doc["session_id"] = session_id
    _append_jsonl(rounds_path(code, home), doc)
    return True


def _prev_round(code: str, home=None, rec: dict | None = None):
    """(上一輪開始點, 那一筆 rounds 紀錄或 None)。沒有 rounds 紀錄 ⇒ 席位檔的 started_epoch;再沒有 ⇒ 0。"""
    rs = rounds(code, home)
    if rs:
        return float(rs[-1]["ts"]), rs[-1]
    rec = find_seat_record(code, home) if rec is None else rec
    try:
        return float(rec.get("started_epoch") or 0), None
    except (TypeError, ValueError):
        return 0.0, None


def round_start(session_uuid: str, source: str, *, cwd: str = "", home=None, check: bool = True,
                transcript: str = "", session_id: str = "") -> str:
    """這一席開始新的一輪(Claude /clear)。先查上一輪,再記新的開始點。回提醒文字或 ""。毫秒級(不跑 git)。"""
    code = str(session_uuid or "")[-5:]
    if not CODE_RE.fullmatch(code):
        return ""
    rec = find_seat_record(code, home)
    prev, entry = _prev_round(code, home, rec)
    text = ""
    if check:
        text = check_previous(code, prev, cwd=cwd or str(rec.get("cwd") or ""), source=source, home=home,
                              prev_entry=entry, provider=str(rec.get("provider") or ""))
    record_round(code, source, home, transcript=transcript, session_id=session_id)
    return text


def predecessor_check(old_uuid: str, new_uuid: str = "", *, source: str = "", home=None) -> str:
    """新席接續舊席(載入舊席位、接回 = 分出新席):舊席最後一輪有沒有收據。回提醒文字或 ""。毫秒級(不跑 git)。
    沒有前身 ⇒ 什麼都不寫(新席的第一輪開始點 = 席位檔 started_epoch,不必另記)。"""
    old = str(old_uuid or "")[-5:]
    new = str(new_uuid or "")[-5:]
    if not CODE_RE.fullmatch(old) or old == new:
        return ""
    rec = find_seat_record(old, home)
    prev, entry = _prev_round(old, home, rec)
    return check_previous(old, prev, cwd=str(rec.get("cwd") or ""), source=source, home=home,
                          who=new or "new", prev_entry=entry, provider=str(rec.get("provider") or ""))


def nudge_text(pct: int | None = None) -> str:
    """85% 自動觸發的接點(v0.70.0 之後接):提醒要跑的字,單一來源。"""
    return T("aib.handoff.nudge", percent=str(pct if pct is not None else 85))


# ---------------------------------------------------------------- collect
# collect 的總時限:每一支子行程的逾時都夾在「離截止還剩多少」之內(r17 M2:一支卡住的 git 不能拖垮整份)。
_DEADLINE = [None]
# git 在別席的工作樹裡跑 status 會順手刷新 index(要拿 index.lock,撞到別席正在 commit)⇒ 一律關掉這種可有可無的鎖。
_SUB_ENV_EXTRA = {"GIT_OPTIONAL_LOCKS": "0"}


def _remaining(timeout: float) -> float:
    dl = _DEADLINE[0]
    return timeout if dl is None else min(timeout, dl - time.time())


def _run(args, cwd=None, timeout=5.0):
    t = _remaining(timeout)
    if t <= 0.05:
        return None, T("aib.handoff.why.budget")
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=t, env=dict(os.environ, **_SUB_ENV_EXTRA))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, repr(exc)[:200]
    if p.returncode != 0:
        return None, (p.stderr or p.stdout or "rc=%d" % p.returncode).strip()[:200]
    return p.stdout, ""


def _git(repo, *args, timeout=5.0):
    return _run(["git", "-C", str(repo)] + list(args), timeout=timeout)


def _ahead_behind(repo, left, right):
    out, err = _git(repo, "rev-list", "--left-right", "--count", "%s...%s" % (left, right))
    if out is None:
        return None, err
    try:
        a, b = out.split()
        return (int(a), int(b)), ""
    except ValueError:
        return None, out.strip()[:80]


def _worktrees(repo):
    out, err = _git(repo, "worktree", "list", "--porcelain")
    if out is None:
        return None, err
    rows, cur = [], {}
    for ln in out.splitlines() + [""]:
        if not ln.strip():
            if cur.get("path"):
                rows.append(cur)
            cur = {}
            continue
        k, _, v = ln.partition(" ")
        if k == "worktree":
            cur["path"] = v
        elif k == "branch":
            cur["branch"] = v.replace("refs/heads/", "", 1)
        elif k == "HEAD":
            cur["head"] = v[:9]
        elif k in ("detached", "bare", "prunable", "locked"):
            cur[k] = True
    return rows, ""


def _wt_touched(path: str) -> float:
    """這個 worktree 最近被動的時刻(stat 就好,不跑 git):.git 檔指的 gitdir 裡 index/HEAD 的 mtime 與根目錄。"""
    best = 0.0
    try:
        best = os.stat(path).st_mtime
    except OSError:
        return 0.0
    gitfile = os.path.join(path, ".git")
    gitdir = gitfile
    try:
        if os.path.isfile(gitfile):
            with open(gitfile, encoding="utf-8") as fh:
                line = fh.read().strip()
            if line.startswith("gitdir:"):
                gitdir = line.split(":", 1)[1].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(path, gitdir)
    except OSError:
        pass
    for name in ("index", "HEAD", "logs/HEAD"):
        try:
            best = max(best, os.stat(os.path.join(gitdir, name)).st_mtime)
        except OSError:
            continue
    return best


def collect(seat: dict, repo: str = "", since_hours: float = DEFAULT_SINCE_HOURS,
            budget_s: float | None = None, home=None) -> dict:
    """機器事實(不判斷)。每一項量不到就進 unmeasured(與量到的一樣顯眼)。
    總時限 budget_s(沒給 ⇒ collect_budget(),預設 8 秒):到點還沒量的項目記成「時間預算用完」,不再等。"""
    t0 = time.time()
    deadline = t0 + (budget_s if budget_s and budget_s > 0 else collect_budget())
    prev_dl = _DEADLINE[0]
    _DEADLINE[0] = deadline
    try:
        return _collect(seat, repo, since_hours, home, t0, deadline)
    finally:
        _DEADLINE[0] = prev_dl


def _bindme():
    """aib bindme 的判斷(src/identity/bindme.py)。lazy:交接不能因為它載不起來而壞。"""
    ident = str(Path(__file__).resolve().parents[1] / "identity")
    if ident not in sys.path:
        sys.path.insert(0, ident)
    import bindme  # noqa: E402
    return bindme


def binding_state(code: str, home=None) -> dict:
    """10-11 the maintainer:交接一開始就要知道這一席有沒有人格/專案。回 {persona, project, problem, why}。
    problem ∈ ok / no_project / no_persona / none / crown_persona / bot / gone;量不到 ⇒ {"unmeasured": why}。"""
    try:
        bm = _bindme()
        root = str(_home(home))
        st = next((x for x in bm.status(root) if x["code"] == code), None)
    except Exception as exc:  # noqa: BLE001 —— 交接優先,綁定檢查壞了只記下來
        return {"unmeasured": repr(exc)[:160]}
    if not st:
        return {"problem": "gone", "persona": "", "project": "", "why": ""}
    return {"problem": st["problem"], "persona": st.get("persona_name") or "",
            "project": st.get("project_title") or "", "why": st.get("hint") or ""}


def _collect(seat, repo, since_hours, home, t0, deadline) -> dict:
    code = seat.get("code") or ""
    rec = seat.get("record") or {}
    facts = {"schema": SCHEMA, "ts": t0, "iso": _iso(t0), "seat": {
        "code": code, "cabin": seat.get("cabin") or "", "session_uuid": seat.get("session_uuid") or "",
        "provider": rec.get("provider") or "", "role": rec.get("role") or "",
        "project_uid": seat.get("project_uid") or rec.get("project_uid") or "",
        "persona_uid": seat.get("persona_uid") or ""},
        "repo": {}, "worktrees": {}, "unmerged": {}, "lease": {}, "pending_box": {}, "review": {},
        "background": [], "unmeasured": []}
    miss = facts["unmeasured"]
    facts["binding"] = binding_state(code, home)

    def over(item):
        if time.time() > deadline:
            miss.append({"item": item, "why": T("aib.handoff.why.budget")})
            return True
        return False

    # 倉
    top = ""
    if repo:
        out, err = _git(repo, "rev-parse", "--show-toplevel")
        if out is None:
            miss.append({"item": "repo", "why": err or repo})
        else:
            top = out.strip()
    else:
        miss.append({"item": "repo", "why": T("aib.handoff.why.no_repo")})
    since = t0 - since_hours * 3600
    if top:
        r = facts["repo"]
        r["path"] = top
        out, _ = _git(top, "rev-parse", "--abbrev-ref", "HEAD")
        r["branch"] = (out or "").strip()
        out, _ = _git(top, "log", "-1", "--format=%h %s")
        r["head"] = (out or "").strip()[:120]
        out, err = _git(top, "rev-parse", "--short=12", "main")
        if out is None:
            miss.append({"item": "main", "why": err})
        else:
            r["main_sha"] = out.strip()
        out, err = _git(top, "status", "--porcelain", timeout=8)
        if out is None:
            miss.append({"item": "repo.dirty", "why": err})
        else:
            r["dirty"] = len([ln for ln in out.splitlines() if ln.strip()])
        ab, err = _ahead_behind(top, "main", "origin/main")
        if ab is None:
            miss.append({"item": "main...origin/main", "why": err})
        else:
            r["main_vs_origin"] = {"ahead": ab[0], "behind": ab[1]}
        if r.get("branch") and r["branch"] not in ("main", "HEAD"):
            ab, err = _ahead_behind(top, "main", r["branch"])
            if ab is not None:
                r["branch_vs_main"] = {"ahead": ab[1], "behind": ab[0]}
        # worktrees
        if not over("worktrees"):
            rows, err = _worktrees(top)
            if rows is None:
                miss.append({"item": "worktrees", "why": err})
            else:
                picked, skipped = [], 0
                for w in rows:
                    w["touched"] = _wt_touched(w["path"])
                    mine = bool(code) and code in (w.get("branch") or "") + w["path"]
                    w["mine"] = mine                       # §8e 對帳只算本席工作樹(a seat 17:55)
                    if w["path"] == top or mine or w["touched"] >= since:
                        picked.append(w)
                    else:
                        skipped += 1
                picked.sort(key=lambda w: -w["touched"])
                if len(picked) > MAX_WORKTREES:
                    skipped += len(picked) - MAX_WORKTREES
                    picked = picked[:MAX_WORKTREES]
                for w in picked:
                    if over("worktrees.detail"):
                        w["unmeasured"] = True
                        continue
                    if w.get("branch") and w["branch"] != "main":
                        ab, _ = _ahead_behind(top, "main", w["branch"])
                        if ab is not None:
                            w["ahead"], w["behind"] = ab[1], ab[0]
                    out, err = _git(w["path"], "status", "--porcelain", timeout=4)
                    w["dirty"] = None if out is None else len([ln for ln in out.splitlines() if ln.strip()])
                facts["worktrees"] = {"total": len(rows), "shown": picked, "not_shown": skipped,
                                      "since_hours": since_hours}
        # 沒合進 main 的分支
        if not over("unmerged"):
            out, err = _git(top, "for-each-ref", "--no-merged=main", "--sort=-committerdate",
                            "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads", timeout=8)
            if out is None:
                miss.append({"item": "unmerged", "why": err})
            else:
                allb = []
                for ln in out.splitlines():
                    name, _, ts = ln.partition("\t")
                    try:
                        allb.append((name, float(ts or 0)))
                    except ValueError:
                        continue
                shown = [b for b in allb if b[1] >= since or (code and code in b[0])][:MAX_BRANCHES]
                facts["unmerged"] = {"total": len(allb), "shown": [{"branch": n, "ts": t} for n, t in shown]}
    # 租約(status 在機器被佔時 rc=4,stdout 一樣是 JSON ⇒ 不靠 rc 判,讀 stdout)
    lease_tool = REPO_ROOT / "tools" / "governance" / "host_lease.py"
    if not lease_tool.is_file():
        miss.append({"item": "lease", "why": str(lease_tool)})
    elif not over("lease"):
        try:
            t = _remaining(6)
            if t <= 0.05:
                raise subprocess.TimeoutExpired("host_lease", 0)
            p = subprocess.run([sys.executable, str(lease_tool), "status", "--json"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=t,
                               env=dict(os.environ, **_SUB_ENV_EXTRA))
            facts["lease"] = dict(json.loads(p.stdout), rc=p.returncode)
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
            miss.append({"item": "lease", "why": repr(exc)[:200]})
    # 待審框
    if code and not over("pending_box"):
        try:
            import dm_queue
            b = dm_queue.brief_payload(code)
            facts["pending_box"] = {k: b.get(k) for k in ("unread", "hold", "decide", "human", "expired",
                                                          "oldest_when", "errors")}
            rows = dm_queue.tabs_payload(code)["tabs"]["waiting"]["rows"]    # §8e:未讀逐封預填、對帳
            facts["unread"] = [{"id": r.get("id"), "from": r.get("sender"), "summary": str(r.get("msg") or "")[:80]}
                               for r in rows if r.get("status") in ("delivered", "queued") and not r.get("read_at")][:20]
        except Exception as exc:  # noqa: BLE001
            miss.append({"item": "pending_box", "why": repr(exc)[:200]})
    # review 帳
    if code:
        hits = sorted(glob.glob(str(pending_dir(home) / ("REVIEW-LOOP-*%s*.md" % code))),
                      key=lambda p: os.path.getmtime(p))
        if hits:
            try:
                lines = [ln for ln in Path(hits[-1]).read_text(encoding="utf-8").splitlines() if ln.strip()]
                facts["review"] = {"file": hits[-1], "tail": lines[-8:]}
            except OSError as exc:
                miss.append({"item": "review", "why": repr(exc)[:200]})
        else:
            facts["review"] = {"file": "", "tail": []}
    # 背景程式(從 worktree 起的掃描/測試 + 10-05 起:這一席自己的 AI 行程底下跑超過一分鐘的殼)
    out, err = (None, "") if over("background") else _run(["ps", "-axo", "pid=,ppid=,etime=,command="], timeout=4)
    if out is None:
        if err:                      # over() 已經自己記了「時間預算用完」
            miss.append({"item": "background", "why": err})
    else:
        me = str(os.getpid())
        procs = {}
        for ln in out.splitlines():
            parts = ln.split(None, 3)
            if len(parts) < 4:
                continue
            procs[parts[0]] = (parts[1], parts[2], parts[3])
        for pid, (_pp, etime, cmd) in procs.items():
            if pid == me:
                continue
            if cmd.split(" ", 1)[0].rsplit("/", 1)[-1] in ("zsh", "sh", "-zsh") or "shell-snapshots" in cmd:
                continue        # 殼的 -c 字串裡剛好有這幾個字,不是那支程式本身
            if BG_RE.search(cmd) and "worktrees/" in cmd:
                facts["background"].append({"pid": pid, "etime": etime, "cmd": cmd[:200]})
        _seat_children(facts, procs, rec.get("pid"), me)
    facts["sweep"] = run_sweep(code, facts["seat"]["project_uid"], home)
    facts["took_s"] = round(time.time() - t0, 2)
    return facts


SWEEP_TIMEOUT_S = 15


def run_sweep(code: str, project_uid: str = "", home=None) -> dict:
    """專案清查(docs/DESIGN-project-sweep-1012.md §3 第一版:交接前必跑)。跑了就算數:失敗 / 逾時只記狀態,
    **絕不擋交接**(a seat 審:對話快滿時被 sweep 自己的毛病擋住最危險)。回 {state, at, counts, top}。
    state ∈ ok / failed / timeout / no_project。"""
    at = _iso(time.time())
    args = [sys.executable, str(HERE / "project_sweep.py"), "--json"]
    if project_uid:
        args += ["--project", project_uid]
    env = dict(os.environ, AIB_SEAT_CODE=code) if code else None
    if home is not None:
        env = dict(env or os.environ, EZ2_AIB_STATE_ROOT=str(_home(home)))
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=SWEEP_TIMEOUT_S, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "at": at}
    except OSError as exc:
        return {"state": "failed", "at": at, "why": repr(exc)[:120]}
    if p.returncode == 2:
        return {"state": "no_project", "at": at}
    try:
        res = json.loads(p.stdout)
    except ValueError:
        return {"state": "failed", "at": at, "why": (p.stderr or "")[-120:]}
    counts = {}
    for it in res.get("items") or []:
        counts[str(it.get("class"))] = counts.get(str(it.get("class")), 0) + 1
    top = [{k: it.get(k) for k in ("class", "owner", "what")} for it in (res.get("items") or [])[:5]]
    return {"state": "ok", "at": at, "counts": counts, "top": top, "missed": res.get("missed") or []}


# 10-05 a seat:Claude 用背景殼起的長迴圈(盯標籤、排隊等租約)都是 `zsh -c 'source …shell-snapshots… && eval …'`,
#   上面那條規則刻意跳過殼、又只認 "worktrees/"(Pro 的資料夾形狀;Mini 的工作樹是 ~/wt-…)⇒ 兩支在跑的迴圈交接單寫 0 支。
#   改從席位檔記的 AI 行程往下找:它的子孫裡跑超過一分鐘的殼就是這一席的背景工作,clear 之後收不到它們的回報。
SEAT_CHILD_MIN_S = 60
SEAT_CHILD_SKIP = ("caffeinate",)        # Claude 自己的防睡眠,不是工作


def _etime_s(s):
    """ps etime([[dd-]hh:]mm:ss)→ 秒;讀不懂 ⇒ None。"""
    try:
        days, _, rest = s.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return (int(days) if days else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]
    except ValueError:
        return None


def _shell_payload(cmd):
    """Claude 背景殼的真正指令:取 eval '…' 那段;不是那種形狀就原樣。"""
    i = cmd.find("eval '")
    if i < 0:
        return cmd
    body = cmd[i + 6:]
    j = body.find("' < /dev/null")
    body = body[:j] if j >= 0 else body
    return body.replace("'\"'\"'", "'").replace("'\\''", "'")     # zsh 跳脫成 '"'"',bash 是 '\''


def _seat_children(facts, procs, root, me):
    if not root or str(root) not in procs:
        return
    root = str(root)
    mine = set()
    p = me
    for _ in range(64):                  # 跑 collect 的這條殼鏈不算背景工作
        mine.add(p)
        if p not in procs:
            break
        p = procs[p][0]
    kids = {}
    for pid, (pp, _e, _c) in procs.items():
        kids.setdefault(pp, []).append(pid)
    seen = {b["pid"] for b in facts["background"]}
    todo = list(kids.get(root, []))
    while todo:
        pid = todo.pop()
        todo += kids.get(pid, [])
        if pid in mine or pid in seen:
            continue
        _pp, etime, cmd = procs[pid]
        prog = cmd.split(" ", 1)[0].rsplit("/", 1)[-1]
        if prog in SEAT_CHILD_SKIP:
            continue
        secs = _etime_s(etime)
        if prog not in ("zsh", "bash", "sh") or " -c " not in cmd or secs is None or secs < SEAT_CHILD_MIN_S:
            continue
        facts["background"].append({"pid": pid, "etime": etime, "cmd": " ".join(_shell_payload(cmd).split())[:200],
                                    "kind": "seat-child"})


def _ab(d):
    return "+%s/-%s" % (d.get("ahead", "?"), d.get("behind", "?")) if d else "?"


def render_draft(facts: dict, auto: bool = False) -> str:
    s = facts.get("seat") or {}
    head = "<!-- handoff:draft v2 seat=%s collected=%s" % (s.get("code") or "", facts.get("iso") or "")
    if facts.get("nonce"):
        head += " nonce=%s" % facts["nonce"]
    lines = ["# " + T("aib.handoff.draft.title", code=s.get("code") or "?", when=_stamp(facts.get("ts") or 0)),
             head + (" auto=1" if auto else "") + " -->"]
    if auto:
        lines += ["", "> " + T("aib.handoff.draft.auto_note"), ""] + fields_block(facts, auto=True)
    import handoff_check
    for key in SECTIONS:
        lines += ["", "## %s <!-- handoff:%s -->" % (T("aib.handoff.section.%s" % key), key),
                  "%s: %s" % (TODO_PREFIX, T("aib.handoff.todo.%s" % key))]
        if not auto:                                    # §8e 固定欄位 + 機器預填(the maintainer 10-13 17:5x「過於馬虎」)
            lines += handoff_check.section_template(key, facts)
    lines += ["", "## %s <!-- handoff:facts -->" % T("aib.handoff.section.facts"), ""]
    lines.append("- %s: %s" % (T("aib.handoff.fact.time"), facts.get("iso") or ""))
    lines.append("- %s: %s/%s (%s %s) session_uuid=%s" % (
        T("aib.handoff.fact.seat"), s.get("cabin") or "?", s.get("code") or "?", s.get("provider") or "?",
        s.get("role") or "?", s.get("session_uuid") or "?"))
    bd = facts.get("binding") or {}
    if bd.get("problem") or s.get("project_uid") or s.get("persona_uid"):
        lines.append("- %s: %s %s" % (T("aib.handoff.fact.binding"),
                                      bd.get("project") or s.get("project_uid") or "-",
                                      bd.get("persona") or s.get("persona_uid") or "-"))
    if bd.get("problem") and bd["problem"] not in ("ok", "bot", "gone"):
        # 10-11 the maintainer:交接一開始就要確認有沒有人格/專案;缺了在第三步寫完前擬提案(不擋交接、不自動綁)
        lines.append("  " + T("aib.handoff.fact.binding_todo", why=bd.get("why") or bd["problem"]))
    r = facts.get("repo") or {}
    if r:
        lines.append("- %s: %s" % (T("aib.handoff.fact.repo"), r.get("path")))
        lines.append("- %s: %s @ %s%s" % (T("aib.handoff.fact.branch"), r.get("branch"), r.get("head"),
                                          (" (%s %s)" % (T("aib.handoff.fact.vs_main"), _ab(r["branch_vs_main"]))
                                           if r.get("branch_vs_main") else "")))
        if "dirty" in r:
            lines.append("- %s: %s" % (T("aib.handoff.fact.dirty"), r["dirty"]))
        if r.get("main_vs_origin"):
            lines.append("- main vs origin/main: %s" % _ab(r["main_vs_origin"]))
    wt = facts.get("worktrees") or {}
    if wt:
        lines += ["", "### " + T("aib.handoff.fact.worktrees", count=str(len(wt.get("shown") or [])),
                                 total=str(wt.get("total")), hours=str(wt.get("since_hours")))]
        for w in wt.get("shown") or []:
            dirty = w.get("dirty")
            lines.append("- %s [%s] %s %s %s" % (
                w.get("path"), w.get("branch") or ("detached " + str(w.get("head") or "")),
                T("aib.handoff.fact.vs_main"), _ab(w) if "ahead" in w else "-",
                T("aib.handoff.fact.dirty_n", n="?" if dirty is None else str(dirty))))
        if wt.get("not_shown"):
            lines.append("- " + T("aib.handoff.fact.not_shown", n=str(wt["not_shown"])))
    um = facts.get("unmerged") or {}
    if um:
        lines += ["", "### " + T("aib.handoff.fact.unmerged", count=str(len(um.get("shown") or [])),
                                 total=str(um.get("total")))]
        lines += ["- %s (%s)" % (b["branch"], _stamp(b["ts"])) for b in um.get("shown") or []]
    lease = facts.get("lease") or {}
    if lease:
        held = lease.get("lease") if isinstance(lease.get("lease"), dict) else {}
        brief = {"state": lease.get("state")}
        brief.update({k: held.get(k) for k in ("owner", "what", "run_id", "expiry_at") if held.get(k)})
        lines += ["", "- %s: %s" % (T("aib.handoff.fact.lease"), json.dumps(brief, ensure_ascii=False))]
    pb = facts.get("pending_box") or {}
    if pb:
        lines.append("- %s: %s" % (T("aib.handoff.fact.pending_box"), json.dumps(pb, ensure_ascii=False)))
    rv = facts.get("review") or {}
    if rv.get("file"):
        lines += ["", "### " + T("aib.handoff.fact.review", file=rv["file"])] + ["    " + ln for ln in rv["tail"]]
    elif "file" in rv:
        lines.append("- " + T("aib.handoff.fact.review_none"))
    bg = facts.get("background") or []
    lines += ["", "### " + T("aib.handoff.fact.background", n=str(len(bg)))]
    lines += ["- pid %s %s %s" % (b["pid"], b["etime"], b["cmd"]) for b in bg]
    lines += ["- " + T("aib.handoff.fact.subagents")]
    miss = facts.get("unmeasured") or []
    lines += ["", "### " + T("aib.handoff.fact.unmeasured", n=str(len(miss)))]
    lines += ["- %s: %s" % (m["item"], m["why"]) for m in miss] or ["- " + T("aib.handoff.fact.none")]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- handoff-fields 六格(doctor 與右欄任務區讀的)
FIELD_SLUGS = ("mainline_sha", "inflight", "waiting_on", "removable_trees", "three_memories", "monitor")
MAX_FIELD_ITEMS = 8
ROLE_RE = re.compile(r"^[a-z]+$")


def _field_key(slug: str) -> str:
    """圍欄鍵是規格逐字(docs/handbook/HANDOFF-FIELDS.md),固定取 zh-TW,與 tools/dispatch/handoff_fields.py 同一份字典。"""
    try:
        import i18n_lib
        return i18n_lib.t("aib.handoff_fields.key." + slug, None, locale="zh-TW", i18n_dir=str(REPO_ROOT / "i18n"))
    except Exception:  # noqa: BLE001 —— 字典讀不到就印鍵名(doctor 會紅,看得到)
        return slug


def _join(items: list, more: int = 0) -> str:
    s = " ; ".join(items[:MAX_FIELD_ITEMS])
    extra = more + max(0, len(items) - MAX_FIELD_ITEMS)
    return s + (" ; " + T("aib.handoff.fields.more", n=str(extra)) if extra else "")


def fields_block(facts: dict, auto: bool = False, home=None) -> list:
    """```handoff-fields 圍欄(六格 + 接自),從機器事實填得出的就填;填不出的寫「量不到:為什麼」(規格:算已答)。
    auto(上一棒跳過交接、機器補抓)⇒ 只有人知道的幾格寫「未填(上一棒跳過交接)」。"""
    s = facts.get("seat") or {}
    code = s.get("code") or "?"
    r = facts.get("repo") or {}
    top = r.get("path") or ""
    shown = (facts.get("worktrees") or {}).get("shown") or []
    skipped = T("aib.handoff.fields.auto_missing")
    role = next((x for x in (s.get("role"), s.get("provider")) if x and ROLE_RE.match(str(x))), "seat")
    vals = {}
    if r.get("main_sha"):
        vals["mainline_sha"] = T("aib.handoff.fields.mainline", sha=r["main_sha"], cur_branch=r.get("branch") or "?",
                                 head=(r.get("head") or "?").split(" ", 1)[0])
    else:
        why = next((m["why"] for m in facts.get("unmeasured") or [] if m.get("item") in ("main", "repo")), "")
        vals["mainline_sha"] = T("aib.handoff.fields.unmeasured", why=why or T("aib.handoff.why.no_repo"))
    # 在飛候選只列這一席名下的(目前這棵 + 分支或路徑帶席碼的),有領先或沒提交;別席的樹不替它說話。
    fly = ["%s %s %s" % (w.get("head") or "?", w.get("path"), w.get("branch") or "detached") for w in shown
           if (w.get("path") == top or code in (w.get("branch") or "") + (w.get("path") or ""))
           and w.get("branch") != "main" and ((w.get("ahead") or 0) > 0 or (w.get("dirty") or 0) > 0)]
    vals["inflight"] = _join(fly) if fly else (T("aib.handoff.fields.none") if shown else
                                               T("aib.handoff.fields.unmeasured", why=T("aib.handoff.why.no_repo")))
    box = json.dumps(facts.get("pending_box") or {}, ensure_ascii=False, sort_keys=True)
    vals["waiting_on"] = T("aib.handoff.fields.waiting", value=box,
                           detail=skipped if auto else T("aib.handoff.fields.see_s2"))
    mine = ["%s(%s)" % (w.get("path"), T("aib.handoff.fields.removable_cond")) for w in shown
            if w.get("path") != top and code in (w.get("branch") or "") + (w.get("path") or "")
            and w.get("ahead") == 0 and w.get("dirty") == 0]
    vals["removable_trees"] = _join(mine) if mine else T("aib.handoff.fields.removable_none")
    vals["three_memories"] = T("aib.handoff.fields.memories", path=str(seat_memory_path(code, home)),
                               target=s.get("persona_uid") or T("aib.handoff.fields.unmeasured",
                                                                  why=T("aib.handoff.fields.no_persona")))
    bg = facts.get("background") or []
    pids = _join(["pid %s" % b.get("pid") for b in bg]) if bg else T("aib.handoff.fields.no_background")
    vals["monitor"] = T("aib.handoff.fields.monitor", items=pids,
                        detail=skipped if auto else T("aib.handoff.fields.see_s3"))
    out = ["```handoff-fields", "%s: %s@%s → %s" % (_field_key("from"), role, code, T("aib.handoff.fields.next"))]
    out += ["%s: %s" % (_field_key(k), " ".join(str(vals[k]).split())) for k in FIELD_SLUGS]
    return out + ["```", ""]


def with_fields(text: str, facts: dict, home=None) -> str:
    """交接檔 = 草稿,第二行(草稿頭)後面插入六格圍欄(解析器只看前 40 行)。"""
    lines = text.splitlines()
    at = next((i + 1 for i, ln in enumerate(lines[:10]) if HEADER_RE.search(ln)), min(1, len(lines)))
    return "\n".join(lines[:at] + [""] + fields_block(facts, home=home) + lines[at:]) + "\n"


def facts_sha(raw: str) -> str:
    """機器事實段(空白壓平後)的短雜湊(r18 M1):collect 記進戳記,write 驗草稿裡那一段沒被換掉。"""
    return hashlib.sha256(_norm(section_body(raw)).encode("utf-8")).hexdigest()[:FACTS_SHA_LEN]


def record_collect(code: str, facts: dict, home=None) -> dict:
    """collect 留戳記(r17 M1):一次一個 nonce,寫進草稿頭;write 要對得上這一輪最後一次的 nonce。"""
    nonce = os.urandom(6).hex()
    facts["nonce"] = nonce
    stamp = {"ts": facts.get("ts") or time.time(), "nonce": nonce, "round_ts": current_round_start(code, home),
             "facts_sha": facts_sha(parse_draft(render_draft(facts)).get(FACTS, ""))}
    _append_jsonl(collects_log(code, home), stamp)
    _write_json(collect_facts_path(code, home), facts)
    return stamp


# ---------------------------------------------------------------- write
def parse_draft(text: str) -> dict:
    """{"s1": 內文, "s2": …, "s3": …, "facts": …};找不到標記的段不在結果裡。"""
    out, cur, buf = {}, None, []
    for ln in text.splitlines():
        m = MARK_RE.search(ln) if ln.lstrip().startswith("#") else None
        if m:
            if cur:
                out[cur] = "\n".join(buf)
            cur, buf = m.group(1), []
            continue
        if cur:
            buf.append(ln)
    if cur:
        out[cur] = "\n".join(buf)
    return out


def section_body(raw: str) -> str:
    """去掉待填行、HTML 註解、頭尾空白之後的內文;空字串 = 沒填。"""
    keep = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if s.startswith(TODO_PREFIX) or (s.startswith("<!--") and s.endswith("-->")):
            continue
        keep.append(ln)
    try:                                                # §8e:欄位標題與 [機器] 預填行不算 AI 寫的字(審查 2)
        import handoff_check
        return handoff_check.strip_template("\n".join(keep)).strip()
    except Exception:  # noqa: BLE001
        return "\n".join(keep).strip()


def _thin(body: str) -> bool:
    """這一段只是敷衍(行數或字數不到門檻)。"""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return len(lines) < MIN_SECTION_LINES or len("".join(body.split())) < MIN_SECTION_CHARS


def _norm(body: str) -> str:
    return " ".join(body.split())


def _padded(body: str) -> bool:
    """湊字數(r18 M1):不同的字不到 MIN_DISTINCT_CHARS 種,或最常見的那個字超過看得見的字的 MAX_TOP_CHAR_SHARE。"""
    vis = "".join(body.split())
    if not vis:
        return True
    counts = {}
    for ch in vis:
        counts[ch] = counts.get(ch, 0) + 1
    return len(counts) < MIN_DISTINCT_CHARS or max(counts.values()) > MAX_TOP_CHAR_SHARE * len(vis)


def similarity(a: str, b: str) -> float:
    """兩段的相似度 0~1(difflib,空白壓平後比)。quick_ratio 是上限,不到門檻就不必細算。"""
    x, y = _norm(a), _norm(b)
    if x == y:
        return 1.0
    sm = difflib.SequenceMatcher(None, x, y, autojunk=False)
    q = sm.quick_ratio()
    return sm.ratio() if q >= MAX_PAIR_SIMILARITY else q


_PROMPT_PIECES: list = []


def _letters(text: str) -> str:
    """只留字母與數字(任何文字的),轉小寫:比對提示字時不管空白、標點、全形半形。"""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def prompt_pieces() -> list:
    """草稿自己的提示字(r19):四個語系的三段待填問題、三段標題、TODO-HANDOFF 標記,在標點處切成句子,
    只留字母數字;短於 MIN_PROMPT_PIECE 的不要(太短會刪到正常的字)。長的排前面先刪。字典讀不到 ⇒ 用目前語系的字。"""
    if _PROMPT_PIECES:
        return _PROMPT_PIECES
    keys = ["aib.handoff.todo.%s" % k for k in SECTIONS] + ["aib.handoff.section.%s" % k for k in SECTIONS]
    try:
        import handoff_check
        keys += handoff_check.field_keys()                # 欄位標題也是提示字,不算自己寫的字
    except Exception:  # noqa: BLE001
        pass
    texts = [TODO_PREFIX]
    try:
        from i18n_lib import CANONICAL_LOCALES, load_dict
        for loc in CANONICAL_LOCALES:
            d = load_dict(loc)
            texts += [str(d[k]) for k in keys if isinstance(d.get(k), str)]
    except Exception:  # noqa: BLE001 —— 字典讀不到就用 T() 的字(至少擋目前語系)
        texts += [T(k) for k in keys]
    pieces = set()
    for t in texts:
        for part in re.split(r"[?\uff1f()\uff08\uff09\[\]\u300c\u300d,\uff0c;\uff1b:\uff1a+\u3001\u3002!\uff01]", t):
            q = _letters(part)
            if len(q) >= MIN_PROMPT_PIECE:
                pieces.add(q)
    _PROMPT_PIECES[:] = sorted(pieces, key=lambda x: (-len(x), x))
    return _PROMPT_PIECES


def own_text(body: str) -> str:
    """這一段刪掉草稿提示字之後剩下的字母數字(r19)。"""
    rest = _letters(body)
    for piece in prompt_pieces():
        rest = rest.replace(piece, "")
    return rest


def _prompt_only(body: str) -> bool:
    """這一段主要是把提示字貼回來(r19):刪掉提示字之後剩不到 MIN_OWN_CHARS 個字母數字,或剩下的是湊字數。"""
    rest = own_text(body)
    return len(rest) < MIN_OWN_CHARS or _padded(rest)


def _sec_sha(body: str) -> str:
    return hashlib.sha256(_norm(body).encode("utf-8")).hexdigest()[:FACTS_SHA_LEN]


def validate(text: str, step: int) -> dict:
    parsed = parse_draft(text)
    bad = [k for k in STEP_NEEDS[step] if not section_body(parsed.get(k, ""))]
    if bad:
        raise HandoffError(T("aib.handoff.err.empty", items=", ".join(T("aib.handoff.section.%s" % k)
                                                                          for k in bad)))
    secs = {k: section_body(parsed.get(k, "")) for k in SECTIONS}
    thin = [k for k in STEP_NEEDS[step] if _thin(secs[k])]
    if thin:
        raise HandoffError(T("aib.handoff.err.thin", items=", ".join(T("aib.handoff.section.%s" % k) for k in thin),
                             lines=str(MIN_SECTION_LINES), count=str(MIN_SECTION_CHARS)))
    padded = [k for k in STEP_NEEDS[step] if _padded(secs[k])]
    if padded:
        raise HandoffError(T("aib.handoff.err.padded", items=", ".join(T("aib.handoff.section.%s" % k) for k in padded),
                             count=str(MIN_DISTINCT_CHARS), percent=str(int(MAX_TOP_CHAR_SHARE * 100))))
    prompt = [k for k in STEP_NEEDS[step] if _prompt_only(secs[k])]
    if prompt:
        raise HandoffError(T("aib.handoff.err.prompt", items=", ".join(T("aib.handoff.section.%s" % k) for k in prompt),
                             count=str(MIN_OWN_CHARS)))
    filled = [k for k in SECTIONS if k in STEP_NEEDS[step] or secs[k]]      # r19:第 1 步就比已經填了的段
    same = [(a, b) for i, a in enumerate(filled) for b in filled[i + 1:]
            if similarity(secs[a], secs[b]) >= MAX_PAIR_SIMILARITY]
    if same:
        raise HandoffError(T("aib.handoff.err.same", items=", ".join(
            "%s = %s" % (T("aib.handoff.section.%s" % a), T("aib.handoff.section.%s" % b)) for a, b in same)))
    if not section_body(parsed.get(FACTS, "")):
        raise HandoffError(T("aib.handoff.err.no_facts"))
    return secs


def check_header(text: str, code: str, home=None) -> dict:
    """草稿頭要是這一席、這一輪最後一次 collect 印的那一份(r17 M1)。回那次 collect 的戳記。"""
    m = next((HEADER_RE.search(ln) for ln in text.splitlines()[:10] if HEADER_RE.search(ln)), None)
    if not m:
        raise HandoffError(T("aib.handoff.err.header"))
    if m.group(2) != code:
        raise HandoffError(T("aib.handoff.err.header_seat", got=m.group(2) or "-", code=code))
    if m.group(5):
        raise HandoffError(T("aib.handoff.err.auto_draft"))
    last = last_collect_between(code, current_round_start(code, home), home=home)
    if not last:
        raise HandoffError(T("aib.handoff.err.no_collect", code=code))
    if m.group(4) != last["nonce"]:
        raise HandoffError(T("aib.handoff.err.stale", when=_stamp(last["ts"], "%m-%d %H:%M:%S")))
    return last


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    n = 2
    while True:
        q = p.with_name("%s-%d%s" % (p.stem, n, p.suffix))
        if not q.exists():
            return q
        n += 1


def _steps_doc(code: str, home=None) -> dict:
    """steps.json 的 "steps";檔壞掉(不是 JSON、形狀不對)⇒ 當作空的、記 log,不讓 write 當掉(r18 minor)。"""
    p = steps_path(code, home)
    if not p.exists():
        return {}
    doc = _read_json(p)
    steps = doc.get("steps") if isinstance(doc, dict) else None
    if not isinstance(steps, dict):
        _log("steps_malformed", home, seat=code, file=str(p))
        return {}
    return {str(k): v for k, v in steps.items() if isinstance(v, dict)}


def _steps_this_round(code: str, home=None) -> dict:
    start = current_round_start(code, home)
    return {k: v for k, v in _steps_doc(code, home).items()
            if isinstance(v.get("ts"), (int, float)) and v["ts"] >= start}


def _prev_receipt(code: str, home=None) -> dict:
    """這一席上一輪(這一輪開始之前)最新的一張收據;沒有回 {}。"""
    start = current_round_start(code, home)
    hits = [r for r in receipts(code, home) if r["ts"] < start and r.get("file")]
    return max(hits, key=lambda r: r["ts"]) if hits else {}


def _prev_receipt_file(code: str, home=None) -> str:
    return str(_prev_receipt(code, home).get("file") or "")


def reused_sections(code: str, secs: dict, home=None) -> list:
    """r19:三段都填了、而且和上一輪交接檔的三段一字不差(空白壓平後)⇒ 回三段(拿舊稿換 nonce 重交);否則 []。
    不比相似度(r19 拿掉):誠實的「這一輪沒變」本來就像上一輪,改寫過的一律放行。上一輪沒有收據、交接檔讀不到 ⇒ [](記 log,不擋)。"""
    if not all(secs.get(k) for k in SECTIONS):
        return []
    f = _prev_receipt_file(code, home)
    if not f:
        return []
    try:
        old = parse_draft(Path(f).read_text(encoding="utf-8"))
    except OSError as exc:
        _log("prev_handoff_unreadable", home, seat=code, file=f, why=repr(exc)[:200])
        return []
    hits = [k for k in SECTIONS if _norm(secs[k]) == _norm(section_body(old.get(k, "")))]
    return hits if len(hits) == len(SECTIONS) else []


def _flag_superseded(code: str, flag: dict, home=None) -> bool:
    """缺漏旗標記的那一輪之後,有一輪留了算數的收據 ⇒ 旗標過期(status 不再印、write 收掉)。"""
    prev = flag.get("prev_round_ts")
    if not isinstance(prev, (int, float)):
        return False
    starts = [float(r["ts"]) for r in rounds(code, home) if float(r["ts"]) > prev]
    for i, st in enumerate(starts):
        if receipt_between(code, st, starts[i + 1] if i + 1 < len(starts) else None, home=home):
            return True
    return False


def _brain_write(seat: dict, step_label: str, body: str, seat_arg: str) -> dict:
    try:
        import aib_memory
        rec = aib_memory.write("handoff", T("aib.handoff.brain_title", code=seat["code"], label=step_label), body,
                               tags=["aib/handoff"], seat=seat_arg)
        return {"ok": True, "uid": rec.get("uid"), "path": rec.get("path")}
    except Exception as exc:  # noqa: BLE001 —— 第二大腦寫不進去照實記在收據,不假裝寫了
        return {"ok": False, "why": str(exc)[:300]}


def _seat_memory_append(code: str, step_label: str, body: str, home=None) -> dict:
    p = seat_memory_path(code, home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n\n## %s\n\n%s\n" % (T("aib.handoff.memory_heading", label=step_label,
                                                 when=_stamp(time.time())), body.strip()))
        return {"ok": True, "path": str(p)}
    except OSError as exc:
        return {"ok": False, "why": str(exc)[:300], "path": str(p)}


def round_checks(code: str) -> dict:
    """本輪有沒有整理記憶(aib memory tidy)、有沒有給 the maintainer 總結(aib summary)。只記,不擋交接
    (DESIGN-memory-tidy-1013 §2:整理失敗不擋交接,下一輪開頭補;沒總結只提醒)。任何錯 ⇒ 記「量不到」。"""
    try:
        import aib_summary
        tidy = aib_summary.tidy_this_round(code)
        summ = aib_summary.summary_this_round(code)
    except Exception as exc:  # noqa: BLE001
        why = "量不到:%r" % (exc,)
        return {"tidy": why, "summary": why, "block": ""}
    t = (tidy.get("status") or "?") if tidy else "沒做"
    sm = "有(%s)" % (summ.get("file") or "?") if summ else "沒做"
    lines = [T("aib.handoff.round.tidy", status=t), T("aib.handoff.round.summary", status=sm)]
    if not tidy:
        lines.append(T("aib.handoff.round.tidy_next"))
    block = "\n\n## %s\n%s\n" % (T("aib.handoff.round.title"), "\n".join("- " + x for x in lines))
    return {"tidy": t, "summary": "沒做" if not summ else "有", "block": block}


def binding_proposal(seat: dict, text: str, now: float, home=None, menu=None, menu_apply=None) -> dict:
    """第三步寫交接檔之前:這一席人格/專案不齊 ⇒ 用交接內容擬綁定提案(與 aib bindme 調查同格式,
    `aib bindme apply <sid> --me <席碼> --accept` 就能套)。**不擋交接**;任何失敗回 {}。
    自動綁(the maintainer 10-13 01:5x 在 a seat 窗裡確認):真的狀態根 ⇒ 背景跑 `bindme auto`(找到就綁、沒有就 30 秒彈窗後建),
    交接檔與收據先寫、不等它;開關 bindme.auto_enabled(AIB_AUTO_BIND / config/auto-bind.json,預設開)。
    - 交接檔本身就是這一輪的總結 ⇒ 不另發問卷(10-11 the maintainer:交接時就該確認並綁)
    - 第三段若寫了「建議人格: / 建議專案:」就照它;沒寫 ⇒ 人格擬新卡草稿、專案標要人決定"""
    code = seat.get("code") or ""
    try:
        bm = _bindme()
        root = str(_home(home))
        st = next((x for x in bm.status(root) if x["code"] == code), None)
        if not st or st["problem"] in ("ok", "bot"):
            return {}
        f = bm.parse_reply(text)
        if not f.get("主項目"):
            body = section_body(parse_draft(text).get("s3", "") or "")
            f["主項目"] = next((ln.strip(" -*#") for ln in body.splitlines() if ln.strip(" -*#")), "")[:80]
        sid = "handoff-%s-%s" % (time.strftime("%m%d-%H%M", time.localtime(now)), code)
        cards = bm.persona_cards(root)
        per = bm.match_persona(f.get("建議人格"), st, cards, f.get("人格理由") or "")
        if per["how"] == "draft_new":
            per["draft_path"] = bm.write_persona_draft(root, sid, code, f)
        if st.get("project_uid"):
            prj = {"uid": st["project_uid"], "title": st.get("project_title") or "", "how": "keep"}
        else:
            u, t, h = bm.match_project(f.get("建議專案"), bm.project_cards(root))
            prj = {"uid": u, "title": t, "how": h}
        addr = "%s/%s" % (st["cabin4"], code)
        row = {"code": code, "seat_id": st["seat_id"], "addr": addr, "replied": True, "cabin": st.get("cabin") or "",
               "summary": bm.summarize(f), "fields": f, "persona": per, "project": prj}
        rec = {"sid": sid, "me": code, "hours": 0, "created": _iso(now), "source": "handoff",
               "token": "", "targets": [{"code": code, "addr": addr, "seat_id": st["seat_id"], "stage": "handoff"}],
               "proposals": [row]}
        os.makedirs(bm.survey_dir(root), exist_ok=True)
        Path(bm.survey_path(root, sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        what = "人格 %s〔%s〕/ 專案 %s〔%s〕" % (per.get("name") or per.get("draft_path") or "-",
                                             bm.HOW_TEXT.get(per["how"], per["how"]),
                                             prj.get("title") or "-", bm.HOW_TEXT.get(prj["how"], prj["how"]))
        cmd = "aib bindme apply %s --me %s --accept" % (sid, code)
        # the maintainer 10-13 01:5x 在 a seat 窗裡確認「同意自動綁」(記憶整理第 2 包):自己這一席走 bindme.auto_apply ——
        #   找到合適的既有卡直接綁 + 「已綁 X」通知;沒有合適的 Mac 彈窗 30 秒、沒選照預設建立並綁;
        #   沒畫面也等滿 30 秒照預設,另送決定卡。冠位人格永不自動綁、bot 席不跑。結果寫進交接檔 ⇒ 檔記實際發生的事。
        #   綁的是自己這一席 ⇒ 不寄接手包。測試可用 menu_apply 換掉整段。
        row["provider"] = st.get("provider") or ""
        auto_note, spawned = "", False
        if menu_apply:
            res = menu_apply(root, sid, bm._actor(root, code, None), ask=True, deliver=False,
                             **({"menu": menu} if menu else {}))
            r0 = res[0] if res else {}
        else:
            r0 = {}
            # 調查檔先寫(背景那支要讀),再起背景;交接不等它(視窗最多約 1~3 分鐘)
            rec["proposals"][0]["provider"] = row["provider"]
            Path(bm.survey_path(root, sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            if bm.auto_enabled(root):
                pid = bm.spawn_auto(root, sid, code)
                spawned = bool(pid)
                auto_note = T("aib.handoff.proposal.auto_bg", path=bm.survey_path(root, sid)) if pid else \
                    T("aib.handoff.proposal.auto_fail")
            else:
                auto_note = T("aib.handoff.proposal.auto_off")
        bound = [d["kind"] for d in r0.get("done", []) if d.get("rc") == 0]
        outcome = "、".join({"persona": "人格", "project": "專案"}.get(k, k) for k in bound) if bound else "-"
        notes = ";".join([x for x in [auto_note] + list(r0.get("skipped") or []) if x]) or "-"
        lines = [T("aib.handoff.proposal.now", why=bm.PROBLEM_TEXT.get(st["problem"], st["problem"])),
                 T("aib.handoff.proposal.what", what=what), T("aib.handoff.proposal.done", what=outcome, why=notes)]
        if spawned:
            # 背景已在綁 ⇒ 不再給「同意就跑」那行(手動再跑會重複綁);要換掉用 repick
            lines.append(T("aib.handoff.proposal.cmd", cmd="aib bindme repick --me %s" % code))
        elif not bound:                                 # 已經綁了就不再寫「同意就跑」(10-11 示範實見:會誤導)
            lines.append(T("aib.handoff.proposal.cmd", cmd=cmd))
        block = "\n\n## %s\n%s\n" % (T("aib.handoff.proposal.title"), "\n".join("- " + x for x in lines))
        return {"sid": sid, "what": what, "cmd": cmd, "block": block, "bound": bound, "notes": notes}
    except Exception as exc:  # noqa: BLE001
        _log("binding_proposal_failed", home, seat=code, why=repr(exc)[:200])
        return {}


def write(seat: dict, text: str, step: int | None, seat_arg: str = "", home=None, writer: str = "") -> dict:
    """驗 → 寫。回 {"step","brain","seat_memory","file","receipt"}。
    r17 M1:一定要給 step(沒有「三步一次做完」);草稿頭要是這一席、這一輪最後一次 collect 的那一份;
    每一段要有實際內容(MIN_SECTION_LINES/MIN_SECTION_CHARS、不能和別段一樣);步驟照順序、而且是同一份草稿。
    r18 M1:不能湊字數(_padded)、兩段不能近似(MAX_PAIR_SIMILARITY)、機器事實段要是 collect 印的那一份(facts_sha)、
    前一步寫過的段不能改(steps.json 每段雜湊)、不能拿上一輪交接檔一字不差換 nonce 重交(reused_sections)。
    r19:提示字不算內容(_prompt_only);已經填了的段第 1 步就比;不再比和上一輪的相似度。
    writer = 跑這個指令的那一席(認得出才有);和 seat 不同 ⇒ 收據記 seat_mismatch(API 席要真人代跑是正當的)。"""
    code = seat["code"]
    if step not in STEP_NEEDS:
        raise HandoffError(T("aib.handoff.err.step"))
    need = step
    stamp = check_header(text, code, home)
    secs = validate(text, need)
    if stamp.get("facts_sha") and facts_sha(parse_draft(text).get(FACTS, "")) != stamp["facts_sha"]:
        raise HandoffError(T("aib.handoff.err.facts_changed"))
    done = {k: v for k, v in _steps_this_round(code, home).items() if v.get("nonce") == stamp["nonce"]}
    missing = [str(n) for n in range(1, need) if str(n) not in done]
    if missing:
        raise HandoffError(T("aib.handoff.err.order", label=str(need), missing=",".join(missing)))
    changed = sorted({k for v in done.values() if isinstance(v.get("sections"), dict)
                      for k, h in v["sections"].items() if k in secs and h != _sec_sha(secs[k])})
    if changed:
        raise HandoffError(T("aib.handoff.err.changed", items=", ".join(T("aib.handoff.section.%s" % k)
                                                                            for k in changed)))
    reused = reused_sections(code, secs, home)
    if reused:
        raise HandoffError(T("aib.handoff.err.reused", items=", ".join(T("aib.handoff.section.%s" % k) for k in reused),
                             file=_prev_receipt_file(code, home)))
    now = time.time()
    label = str(need)
    k = SECTIONS[need - 1]
    try:                                                # 寫進記憶要留欄位標題(下一個 AI 才分得出「等誰」「踩到的坑」;二輪審查)
        import handoff_check
        keep = section_body(handoff_check.drop_machine(parse_draft(text).get(k, ""))) or secs[k]
        keep_lines = [ln for ln in handoff_check.drop_machine(parse_draft(text).get(k, "")).splitlines()
                      if not ln.strip().startswith(TODO_PREFIX)]
        keep = "\n".join(keep_lines).strip() or secs[k]
    except Exception:  # noqa: BLE001
        keep = secs[k]
    body = "## %s\n\n%s" % (T("aib.handoff.section.%s" % k), keep)
    out = {"step": label, "brain": _brain_write(seat, label, body, seat_arg),
           "seat_memory": _seat_memory_append(code, label, body, home)}
    steps = {k: v for k, v in _steps_doc(code, home).items() if k in done}
    steps[str(need)] = {"ts": now, "nonce": stamp["nonce"], "brain_ok": out["brain"]["ok"],
                        "seat_memory_ok": out["seat_memory"]["ok"],
                        "sections": {s: _sec_sha(secs[s]) for s in STEP_NEEDS[need]}}
    _write_json(steps_path(code, home), {"schema": SCHEMA, "seat": code, "steps": steps})
    if need == 3:
        pd = pending_dir(home)
        pd.mkdir(parents=True, exist_ok=True)
        facts = _read_json(collect_facts_path(code, home)) or {}
        if facts.get("nonce") != stamp["nonce"]:
            facts = {"seat": {"code": code}, "unmeasured": [{"item": "repo", "why": T("aib.handoff.why.facts_gone")}]}
        sweep = facts.get("sweep") if isinstance(facts.get("sweep"), dict) else None
        if not sweep:          # 收集那一步沒跑到(舊版收集 / 事實檔不見)⇒ 這裡補跑:交接一定有嘗試過 sweep
            sweep = run_sweep(code, str((facts.get("seat") or {}).get("project_uid") or seat.get("project_uid") or ""), home)
        target = _unique(pd / ("HANDOFF-%s-%s.md" % (code, time.strftime("%m%d-%H%M", time.localtime(now)))))
        checks = round_checks(code)                    # 第 5 包:本輪整理 / 總結有沒有做(只記,不擋交接)
        text = text + checks["block"]
        warns = []
        incomplete = _incomplete(parse_draft(text), facts, checks, warns)   # §8e:標不完整,不硬擋;只進收據(審查 3)
        prop = binding_proposal(seat, text, now, home)
        if prop.get("block"):
            out["binding_proposal"] = {k: prop[k] for k in ("sid", "what", "cmd", "bound", "notes")}
        text = text + prop.get("block", "")            # 提案接在本文後面;下一行一字不改(Z24 刀錨)
        target.write_text(with_fields(text, facts, home), encoding="utf-8")
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = {"schema": SCHEMA, "seat": code, "cabin": seat.get("cabin") or "",
                   "session_uuid": seat.get("session_uuid") or "", "ts": now, "iso": _iso(now),
                   "round_ts": current_round_start(code, home), "file": str(target), "sha": sha,
                   "collect_ts": stamp["ts"], "collect_nonce": stamp["nonce"],
                   "writer_seat": writer, "seat_mismatch": bool(writer) and writer != code,
                   "brain": out["brain"], "seat_memory": out["seat_memory"],
                   "steps": {k: v["ts"] for k, v in steps.items()},
                   "sweep": sweep.get("state"), "sweep_at": sweep.get("at")}
        receipt["tidy"], receipt["summary"] = checks["tidy"], checks["summary"]
        receipt["incomplete"] = incomplete
        receipt["warnings"] = warns
        if out.get("binding_proposal"):                 # 審查 5:verify 第 8 項要讀得到
            receipt["binding_proposal"] = out["binding_proposal"]
        _write_json(receipt_path(code, home), receipt)
        _append_jsonl(receipts_log(code, home), receipt)
        out["file"], out["receipt"] = str(target), str(receipt_path(code, home))
        flag = _read_json(missed_path(code, home))
        if isinstance(flag, dict):
            out["missed_before"] = flag.get("auto_file") or ""
            if _flag_superseded(code, flag, home):        # 缺的是更早的一輪,這一輪已照規矩交接 ⇒ 旗標收掉
                try:
                    missed_path(code, home).unlink()
                except OSError as exc:
                    _log("missed_clear_failed", home, seat=code, why=repr(exc)[:200])
    if need == 3:
        try:                                            # §8e.4:第 3 步寫完自動 verify 一次,結果進收據
            import handoff_check
            items = handoff_check.verify(code, home)
            out["verify"] = [{"item": n, "ok": ok, "detail": d} for n, ok, d in items]
            rec = _read_json(receipt_path(code, home)) or {}
            rec["verify"] = {"ok": all(ok for _n, ok, _d in items), "items": out["verify"], "at": _iso(time.time())}
            _write_json(receipt_path(code, home), rec)
        except Exception as exc:  # noqa: BLE001 —— verify 壞了不影響交接本身
            out["verify_error"] = repr(exc)[:200]
    _log("write", home, seat=code, step=label, brain_ok=out["brain"]["ok"], seat_memory_ok=out["seat_memory"]["ok"])
    return out


def _incomplete(raw, facts, checks, warns=None):
    """§8e:交接哪裡不完整(固定欄位缺、機器對帳不齊、本輪沒整理)。raw = parse_draft 的原段(要欄位標題)。量不到不算缺。"""
    out = []
    try:
        import handoff_check
        secs = {k: raw.get(k, "") for k in SECTIONS}
        out += [T("aib.handoff.incomplete.field", what=x) for x in handoff_check.missing_fields(secs)]
        out += handoff_check.reconcile(facts, secs, warnings=warns)
    except Exception as exc:  # noqa: BLE001
        _log("incomplete_check_failed", None, why=repr(exc)[:200])
    t = str(checks.get("tidy") or "")
    if t.startswith("沒做") or t.startswith("failed"):
        out.append(T("aib.handoff.incomplete.tidy", status=t))
    return out


# ---------------------------------------------------------------- status
def status(code: str, home=None) -> dict:
    rs = rounds(code, home)
    start = float(rs[-1]["ts"]) if rs else 0.0
    prev_start = float(rs[-2]["ts"]) if len(rs) >= 2 else None
    this = receipt_between(code, start, home=home)
    prev = receipt_between(code, prev_start, start, home=home) if prev_start is not None else {}
    flag = _read_json(missed_path(code, home)) or {}
    if not isinstance(flag, dict) or (flag and _flag_superseded(code, flag, home)):
        flag = {}
    return {"seat": code, "round_start": start, "steps": sorted(_steps_this_round(code, home)),
            "this_round_receipt": this, "previous_round_start": prev_start, "previous_round_receipt": prev,
            "missed": flag}


def render_status(st: dict) -> str:
    lines = [T("aib.handoff.status.head", code=st["seat"], since=_stamp(st["round_start"]))]
    lines.append(T("aib.handoff.status.steps", items=",".join(st["steps"]) or "-"))
    r = st["this_round_receipt"]
    lines.append(T("aib.handoff.status.this_yes", when=_stamp(r["ts"]), file=r.get("file") or "")
                 if r else T("aib.handoff.status.this_no"))
    if st["previous_round_start"] is None:
        lines.append(T("aib.handoff.status.prev_unknown"))
    elif st["previous_round_receipt"]:
        lines.append(T("aib.handoff.status.prev_yes", when=_stamp(st["previous_round_receipt"]["ts"])))
    else:
        lines.append(T("aib.handoff.status.prev_no"))
    if st["missed"]:
        lines.append(T("aib.handoff.status.missed", when=_stamp(st["missed"].get("ts") or 0),
                       file=st["missed"].get("auto_file") or "-"))
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def caller_code() -> str:
    """跑這個指令的那一席(TMUX_PANE ⇒ 窗號 ⇒ 席位檔);不在席裡(真人的終端機)⇒ ""。"""
    try:
        import aib_memory
        code = aib_memory.seat_context("").get("code") or ""
    except Exception:  # noqa: BLE001
        return ""
    return code if CODE_RE.fullmatch(code) else ""


def auto_collect_main(code: str, repo: str, out: str, since_hours: float = DEFAULT_SINCE_HOURS) -> int:
    """背景補抓(check_previous 起的,沒有人在等):席碼 ⇒ 席位檔 ⇒ collect(有總時限)⇒ 原子寫成 out。
    先寫暫存檔再改名:doctor/右欄挑最新一份交接單時不會撿到寫一半的檔。"""
    if not CODE_RE.fullmatch(code or "") or not out:
        return RC_USAGE
    try:
        rec = find_seat_record(code)
        seat = {"code": code, "cabin": rec.get("_cabin", ""), "session_uuid": str(rec.get("session_uuid") or ""),
                "project_uid": str(rec.get("project_uid") or ""), "persona_uid": "", "record": rec}
        facts = collect(seat, repo=repo or str(rec.get("cwd") or ""), since_hours=since_hours)
        text = render_draft(facts, auto=True)
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".%s.tmp-%d" % (target.name, os.getpid()))
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001 —— 背景行程:記 log,不丟到沒人看的 stderr
        _log("auto_collect_failed", None, seat=code, why=repr(exc)[:300])
        return RC_IO
    _log("auto_collected", None, seat=code, file=out, took_s=facts.get("took_s"))
    return RC_OK


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["review"]:                         # §8e.7 遺漏回顧(the maintainer 10-13 17:5x)
        import handoff_review
        return handoff_review.main(argv[1:])
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_HANDOFF_PROG") or "aib handoff",
                                 description=T("aib.handoff.help.desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help=T("aib.handoff.help.collect"))
    c.add_argument("--seat", default="")
    c.add_argument("--repo", default="")
    c.add_argument("--since-hours", type=float, default=DEFAULT_SINCE_HOURS)
    c.add_argument("--out", default="")
    c.add_argument("--json", action="store_true")
    c.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)   # 背景補抓(check_previous 起的)
    w = sub.add_parser("write", help=T("aib.handoff.help.write"))
    w.add_argument("--seat", default="")
    w.add_argument("--step", type=int, choices=(1, 2, 3), required=True)
    w.add_argument("--file", required=True)
    w.add_argument("--json", action="store_true")
    v = sub.add_parser("verify", help=T("aib.handoff.help.verify"))
    v.add_argument("--seat", default="")
    v.add_argument("--json", action="store_true")
    s = sub.add_parser("status", help=T("aib.handoff.help.status"))
    s.add_argument("--seat", default="")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "collect" and a.auto:
        return auto_collect_main(a.seat, a.repo, a.out, a.since_hours)
    try:
        seat = resolve_seat(a.seat)
        if a.cmd == "collect":
            _spawn_archive_sweep()   # 記憶整理第 1 包:背景清 90 天備存(一天一次、不等、不印)
            repo = a.repo or os.getcwd()
            facts = collect(seat, repo=repo, since_hours=a.since_hours)
            record_collect(seat["code"], facts)
            text = json.dumps(facts, ensure_ascii=False, indent=1) if a.json else render_draft(facts)
            if a.out:
                Path(a.out).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
                _out(T("aib.handoff.collect.saved", file=a.out))
            else:
                _out(text.rstrip("\n"))
            return RC_OK
        if a.cmd == "write":
            try:
                text = Path(a.file).read_text(encoding="utf-8")
            except OSError as exc:
                raise HandoffError(T("aib.handoff.err.file", file=a.file, why=str(exc)))
            writer = caller_code()
            if writer and writer != seat["code"]:
                _out(T("aib.handoff.warn.other_seat", who=writer, code=seat["code"]), err=True)
            res = write(seat, text, a.step, a.seat, writer=writer)
            if a.json:
                _out(json.dumps(res, ensure_ascii=False, indent=1))
                return RC_OK
            _out(T("aib.handoff.write.done", label=res["step"], code=seat["code"]))
            _out(T("aib.handoff.write.memory_ok" if res["seat_memory"]["ok"] else "aib.handoff.write.memory_fail",
                   path=res["seat_memory"].get("path") or "", why=res["seat_memory"].get("why") or ""))
            b = res["brain"]
            _out(T("aib.handoff.write.brain_ok", uid=b.get("uid") or "", path=b.get("path") or "") if b["ok"]
                 else T("aib.handoff.write.brain_fail", why=b.get("why") or ""))
            if res.get("binding_proposal"):
                bp = res["binding_proposal"]
                _out(T("aib.handoff.write.binding_proposal", what=bp["what"], cmd=bp["cmd"]))
                _out(T("aib.handoff.proposal.done", what="、".join(bp.get("bound") or []) or "-", why=bp.get("notes") or "-"))
            if res.get("receipt"):
                _out(T("aib.handoff.write.receipt", file=res["file"], path=res["receipt"]))
                if res.get("verify"):
                    import handoff_check
                    txt, _done = handoff_check.render([(x["item"], x["ok"], x["detail"]) for x in res["verify"]])
                    _out(txt)                             # 不完整照樣交得出來(a seat 17:55 不硬擋),✗ 那幾項列在上面
                _out(T("aib.handoff.write.ready"))
            else:
                _out(T("aib.handoff.write.next", label=str(int(res["step"]) + 1)))
            return RC_OK
        if a.cmd == "verify":
            import handoff_check
            items = handoff_check.verify(seat["code"])
            if a.json:
                _out(json.dumps([{"item": n, "ok": ok, "detail": d} for n, ok, d in items], ensure_ascii=False, indent=1))
                return RC_OK if all(ok for _n, ok, _d in items) else 1
            txt, done = handoff_check.render(items)
            _out(txt)
            return RC_OK if done else 1                 # 1 = 還缺(跟用法錯 2 分開;審查 12)
        st = status(seat["code"])
        _out(json.dumps(st, ensure_ascii=False, indent=1) if a.json else render_status(st))
        return RC_OK
    except HandoffError as exc:
        _out("aib handoff: %s" % exc, err=True)
        return RC_USAGE


if __name__ == "__main__":
    sys.exit(main())
