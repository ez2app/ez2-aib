#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""AI任務區產生器(大綱第 38 項):一張**從四個正本算出來的唯讀畫面**,不是第二塊板。

    python3 tools/dispatch/ai_task_zone.py --seat <席碼五碼> [--json] [--strict] [--max-lines N] [--mode compact|detail]
    python3 tools/dispatch/ai_task_zone.py --release-gaps --version <版號> [--suite-results J] [--escape-log MD]   # 第 43 項:tag 訊息「已知缺口」節
    import ai_task_zone; ai_task_zone.render_lines(seat, width, max_lines, now=None) -> list[str]   # 第 39 項頁簽用

★ 為什麼是「算出來的」而不是「另存一份」(a seat 09-12 01:2x,三份草案共識):
  進度這件事每一刻都已經住在四個地方 —— 大綱派工表(這一包是什麼、序列到哪)、
  待決簿(等人裁)、交接單(接自誰)、收據與管線 log(官方有沒有)。
  任務區若另存一份 json,第一天就跟這四處分家 —— `sandbox-tasks` 那 79 張「開發一」
  就是分家後的樣子。所以本檔**沒有寫入路徑**:交件、收據、DONE 落地,畫面自己變。

★ 四條尺(rc 非 0 的理由,各自具名):
  rc=3  派工表把某項標 ✅,而那顆 sha **沒有收據**、或收據在但 sha **不在官方主線的祖先**
        ⇒ 印「無憑據」。「官方有」只認收據檔或本包 sha 的 DONE 行;**別包的 DONE 不算**。
  rc=4  狀態寫「等 the maintainer(待決簿 X)」而 X **不在待決簿** ⇒ 印「待登錄」。
        等人裁若沒有載體,就與「沒有東西在等」同形 —— 這正是待決簿存在的理由。
  rc=6  只在 --strict:任一正本讀不到。預設不紅,但每一格都印「量不到:<為什麼>」,
        不留空白 —— 空白與「沒有事」同形,而讀不到與沒有事的處置相反。
  rc=2  用法錯(席碼形狀、旗標)。

★ 每一格都帶**來源與量測時刻**:讀者要能回答「這個數字是哪一份檔、什麼時候的」。
  席碼不在派工表 ⇒ 三行全印「量不到」,**不借別席的資料**。

★ 「等誰・誰等我」直接讀 dm_queue 的佇列(queued / claimed),不另存傳輸狀態。
★ 機器狀態格(第 48 項):讀 `ps -axo pid,ppid,etime,command` 找 aib-release.sh / main-merge-witness.sh
  與它們子孫行程裡正在跑的 tests/test-*.py,配 LIVE log 哪條還沒 DONE/STOP、RELEASE 跑到 N/M;
  **不另建狀態檔**。「誰在排隊」從第 24 項每機租約的 ledger 讀(lease_view);租約讀不到 ⇒ 印量不到,不用「沒人排隊」頂替。
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
# ★★ 順序是承重的(33f17 2026-09-09 抓到):本樹 insert(0)、fallback append,
#   否則主庫那份 i18n 會贏本樹,本樹新加的鍵讀成 !!key!!。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
# ★ render_lib(顯示寬度、截斷、進度條色)同一條順序律:本樹 insert(0)、主庫 append。
#   產生器自己**不寫任何跳脫碼**;色值與 SGR 只住 render_lib(a seat 09-15 03:1x 裁 A)。
for _rdir, _front in ((_here / "render", True), (_fallback / "render", False)):
    if str(_rdir) not in sys.path:
        sys.path.insert(0, str(_rdir)) if _front else sys.path.append(str(_rdir))
import dm_text  # noqa: E402  原話一行的單一入口(與收件匣總覽共用)
import i18n_lib  # noqa: E402
import render_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


_TRANSLATORS = {}


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。
    ★ 第 51 項量到的第一筆成本:`i18n_lib.t()` 每叫一次都重開一個 Translator(讀四份字典),一幀 44 次
      = 247 ms,佔整幀 303 ms 的八成。這裡改成每個 locale 只建一次;字典檔在程式生命期內不會變。"""
    tr = _TRANSLATORS.get(LOC)
    if tr is None:
        tr = _TRANSLATORS[LOC] = i18n_lib.Translator(locale=LOC)
    return tr.t(key, params)


RC_OK = 0
RC_USAGE = 2
RC_NO_EVIDENCE = 3
RC_DECISION_UNREGISTERED = 4
RC_STRICT_MISSING = 6

REPO = pathlib.Path(__file__).resolve().parents[2]
SEAT_RE = re.compile(r"^[0-9a-z]{4,8}$")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
# 第 75 項眼驗:LIVE log 當「在飛」的補充來源時的時效上限;超過就不算(昨天別艙的 log 永遠沒有尾行)。
LIVE_INFLIGHT_MAX_AGE_S = 7200
DECISION_REF_RE = re.compile(r"待決簿\s*[:：]?\s*`?([A-Za-z0-9][A-Za-z0-9_.-]*)`?")
# 派工序列一步一步用 → 分;**一步的開頭**是項目編號才算編號步驟(`33 合 10b(live17)` 是第 33 項;
# `commit 48–58 並推` 是文字步驟,48/58 不是這席要做的項目;`39(等 35)` 只算 39)。
STEP_SPLIT_RE = re.compile(r"\s*(?:→|->|⇒)\s*")
STEP_LEAD_RE = re.compile(r"^\**(\d{1,3}[a-z]?)\**(?![\w.])")
STRIKE_RE = re.compile(r"~~.*?~~")
# 殘句判斷用的符集合:**單源自 status-enum.json**(a seat 09-12 18:2x:寫死一份等於第二真相源,
# enum 加符時這裡不會跟上,而兩種失效都靜默——少剝一個符 = 狀態符印兩次;殘句沒丟 = 多一個文字步驟擋在序列最前面)。
# EXTRA_MARKS 是還沒進 enum 的符:第 66 項的 🟣 落地後 enum 自己會有它,那天這一行可以刪。
EXTRA_MARKS = frozenset("🟣")
# 三、四級標題都算節(`### 89.8` 與 `#### 88.8.1` 都放最近項目表)。
HEADING_RE = re.compile(r"^#{3,4}\s+(\d+(?:\.\d+)+)\s+(.*)$", re.M)
ITEM_ROW_RE = re.compile(r"^\|\s*(\d{1,3}[a-z]?)\s*\|(.*)$")
LIVE_HEAD_RE = re.compile(r"candidate\s+([0-9a-f]{7,40})")
LIVE_DONE_RE = re.compile(r"^\s*(?:\d{2}:\d{2}:\d{2}\s+)?DONE\b")
LIVE_STOP_RE = re.compile(r"^\s*(?:\d{2}:\d{2}:\d{2}\s+)?STOP\b")
RELEASE_DONE_MARK = "已原子推送"
RELEASE_FAIL_GLYPH = "✘"
RELEASE_RESULTS_RE = re.compile(r"逐支結果表[:：]\s*(\S+?\.jsonl)")
RELEASE_POP_RE = re.compile(r"母體\s*(\d+)")


# ── 小工具 ───────────────────────────────────────────────────────────────────
def stamp(epoch=None):
    """量測時刻:本機時區 ISO 秒。每一格都要帶,所以只有這一個寫法。"""
    dt = datetime.datetime.fromtimestamp(time.time() if epoch is None else epoch).astimezone()
    return dt.isoformat(timespec="seconds")


def mtime_stamp(path):
    try:
        return stamp(os.path.getmtime(path))
    except OSError:
        return None


def cell(key, value, source, measured_at, ok=True, why=None):
    """一格 = 值 + 來源 + 量測時刻。`ok=False` 就是「量不到」,`why` 必填。"""
    if not ok and not why:
        raise ValueError("unmeasured cell needs why")
    return {"key": key, "value": value, "source": source,
            "measured_at": measured_at, "ok": bool(ok), "why": why}


def unmeasured(key, why, source=None):
    return cell(key, T("aib.taskzone.unmeasured", {"why": why}), source, stamp(), ok=False, why=why)


def fmt_wait(seconds):
    seconds = int(max(0, seconds))
    if seconds < 3600:
        return T("aib.taskzone.wait_min", {"n": seconds // 60})
    return T("aib.taskzone.wait_hm", {"h": seconds // 3600, "m": (seconds % 3600) // 60})


def load_marks(enum_path=None):
    """符一律從正本讀;讀不到就紅,**不得有 fallback 預設值**(那個檔自己寫著理由)。"""
    cands = [enum_path] if enum_path else []
    cands += [REPO / "tools" / "report" / "status-enum.json",
              pathlib.Path.home() / "ez2-aib" / "tools" / "report" / "status-enum.json"]
    for c in cands:
        if c and pathlib.Path(c).exists():
            return json.loads(pathlib.Path(c).read_text(encoding="utf-8"))["marks"]
    raise SystemExit(T("aib.taskzone.err_no_enum"))


# ── 正本一:大綱(派工表 + 最近項目表)────────────────────────────────────────
def _secnum(num):
    """節號 → 可比較的整數 tuple(`89.9` > `88.9` > `59.5`)。"""
    return tuple(int(x) for x in num.split("."))


def _sections(md):
    """[(節號, 標題, 內文)],依出現順序。"""
    out = []
    heads = list(HEADING_RE.finditer(md))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        out.append((h.group(1), h.group(2).strip(), md[h.end():end]))
    return out


def _table_rows(body):
    """把一段 markdown 裡每一個 `| a | b |` 列拆成 cells;分隔列與表頭一起回,由呼叫端挑。"""
    rows = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            rows.append([c.strip() for c in s[1:-1].split("|")])
    return rows


def _has_words(text):
    """剝掉狀態符之後還剩不剩「字」(中日韓字或拉丁字母)。只剩時刻、數字、標點 ⇒ 那是完成註記的殘句不是步驟。"""
    return bool(re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]", text or ""))


def parse_items(md, marks):
    """最近項目表:所有標題含「最近項目」的節,合成一張(後面的節蓋前面的同號)。"""
    items = {}
    mark_chars = set(marks.values())
    # ★ 大綱把**新的節放在檔案前面**(§89 在 §88 上面),所以「最後一個」要用節號排,不用出現順序。
    for num, title, body in sorted(_sections(md), key=lambda s: _secnum(s[0])):
        if "最近項目" not in title:
            continue
        for line in body.splitlines():
            m = ITEM_ROW_RE.match(line.strip())
            if not m:
                continue
            cells = [c.strip() for c in m.group(2).rstrip("|").split("|")]
            if len(cells) < 2:
                continue
            status = cells[1]
            mark = next((ch for ch in status if ch in mark_chars), None)
            sha = SHA_RE.search(status)
            dec = DECISION_REF_RE.search(status)
            items[m.group(1)] = {
                "n": m.group(1), "title": cells[0], "status": status,
                # 第 75 項眼驗(a seat 15:22):mark 就是從 status 裡抽的第一個狀態符,兩個都印會連著出現兩次;
                # 這裡先存一份剝掉開頭狀態符與 markdown 的版本,detail 與 compact 都用它。
                "status_plain": plain_status(status, mark_chars),
                "mark": mark, "sha": sha.group(0) if sha else None,
                "decision_ref": dec.group(1) if dec else None,
                "writes": cells[2] if len(cells) > 2 else "",
                "section": num,
            }
    return items


def parse_dispatch(md, seat, mark_chars):
    """派工表:標題含「派工」而**節號最大**的那一節(新節在檔案前面,不能用出現順序);找 `@席碼` 那一列。
    回 (節號, 列 dict | None)。列 dict:line / seat_cell / capacity / sequence(項目編號依序)/ writes。"""
    secs = [s for s in _sections(md) if "派工" in s[1]]
    if not secs:
        return None, None
    num, _title, body = max(secs, key=lambda s: _secnum(s[0]))   # 節號最大的那一個 = 最新版派工表
    needle = "@" + seat
    for cells in _table_rows(body):
        if len(cells) < 4 or needle not in cells[1]:
            continue
        steps = []
        for raw in STEP_SPLIT_RE.split(STRIKE_RE.sub("", cells[3])):
            st = raw.strip(" ;;、")
            if not st:
                continue
            # ★ `~~步驟~~ ✅ 03:14` 劃掉之後剩「✅ 03:14」:那是完成註記的殘句,不是一個步驟(否則它會被當成
            #   「還沒做的文字步驟」擋在序列最前面,連 compact 的「現在」都被它吃掉)。以狀態符開頭的殘句一律丟。
            if st and st[0] in mark_chars and not _has_words(st[1:]):
                continue                     # 只丟「符 + 時刻/數字」這種殘句;「✅ 39 任務區頁簽」是合法步驟(a seat §5)
            # 編號要在**剝掉開頭狀態符之後**才抓,text 也用剝過的(a seat 19:46 B 的正向端露出來的第二層:
            # 「✅ 39 丙 頁簽」不會被當殘句丟掉了,但 STEP_LEAD_RE 從 ✅ 開始比對抓不到編號 ⇒ 它變成一個文字步驟,
            # 而文字步驟會擋在序列最前面、把 compact 的「現在」吃掉——修了一半比沒修更難看出來)。
            lead = st.lstrip()
            if lead and lead[0] in mark_chars:
                lead = lead[1:].lstrip()
            m = STEP_LEAD_RE.match(lead)
            steps.append({"n": m.group(1) if m else None, "text": lead or st})
        seq = []
        for st in steps:
            if st["n"] and st["n"] not in seq:
                seq.append(st["n"])
        return num, {"line": cells[0], "seat_cell": cells[1], "capacity": cells[2],
                     "sequence": seq, "steps": steps, "sequence_text": cells[3],
                     "writes": cells[4] if len(cells) > 4 else ""}
    return num, None


# ── 正本二:待決簿(與 pending-decisions.py 同一份檔)────────────────────────
def load_decisions(path):
    p = pathlib.Path(path)
    if not p.exists():
        return None, T("aib.taskzone.why_no_file", {"path": str(p)})
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, T("aib.taskzone.why_unreadable", {"path": str(p), "err": e})
    # ★ a seat:`{}` 不是空簿,是 schema 不合 —— 當成空簿會讓所有「等 the maintainer」同時誤紅。走跟壞 JSON 同一條。
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        return None, T("aib.taskzone.why_bad_schema", {"path": str(p)})
    return d["items"], None


# ── 正本三:交接單 ────────────────────────────────────────────────────────────
def load_handoff_parser(path):
    """第 41 項的解析器 `tools/dispatch/handoff_fields.py`(開發 C a seat):`analyze(path) -> (dict, rc)`。
    預設找本檔旁邊那一支;不在就回 None(退回用檔名推「接自誰」,並在格裡說明)。"""
    if not path or not pathlib.Path(path).exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("handoff_fields", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod if hasattr(mod, "analyze") else None
    except Exception:  # noqa: BLE001 —— 解析器壞了不該讓任務區整張畫不出來;格裡會寫退回檔名
        return None


def latest_handoff(handoff_dir, seat, parser=None):
    """本席最新一份交接單。「接自誰」優先吃第 41 項解析器的 from.seat/baton;沒有解析器就從檔名推。
    回 dict:path / shift / mtime / source(parser|filename)/ fields(解析器給的六格,沒有就 None)。"""
    d = pathlib.Path(handoff_dir)
    if not d.is_dir():
        return None
    cands = [p for p in d.glob("HANDOFF-*") if seat in p.name and p.suffix == ".md"]
    if not cands:
        return None
    p = max(cands, key=lambda x: x.stat().st_mtime)
    out = {"path": str(p), "shift": None, "mtime": mtime_stamp(p), "source": "filename", "fields": None,
           "from_seat": None, "parser_rc": None}
    if parser is not None:
        try:
            info, rc = parser.analyze(str(p))
        except Exception:  # noqa: BLE001
            info, rc = None, None
        if isinstance(info, dict):
            frm = info.get("from") or {}
            out["from_seat"] = frm.get("seat") or info.get("from_seat")
            baton = frm.get("baton") if frm.get("baton") is not None else info.get("baton")
            out["shift"] = str(baton) if baton is not None else None
            out["fields"] = info.get("fields")
            out["missing"] = info.get("missing")
            out["parser_rc"] = rc
            out["source"] = "parser"
    if out["shift"] is None:
        m = re.search(r"-p(\d+)", p.name)
        out["shift"] = m.group(1) if m else None
    return out


# ── 正本四:收據、管線 log、發版鎖 ───────────────────────────────────────────
def git_is_ancestor(repo, sha, ref):
    """(True/False/None, why)。None = 量不到(不是「不在」)。"""
    try:
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, ref],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if r.returncode == 0:
        return True, None
    if r.returncode == 1:
        return False, None
    return None, (r.stderr or "").strip()[:120]


def find_receipts(evidence_dir, sha):
    """本包 sha 的收據:`<sha40>-receipt-*.json` 或 docs-only 的 `-result-docs-only-*.json`。
    以前綴比對(表格裡通常只寫 8 碼)。"""
    d = pathlib.Path(evidence_dir)
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        if not p.name.endswith(".json"):
            continue
        if not p.name.startswith(sha):
            continue
        if not ("-receipt-" in p.name or "-result-docs-only-" in p.name):
            continue
        # ★ a seat 規則一:檔名對得上還不夠,內文 candidate_sha 也要是這一顆(檔名可以被改,內文是機器寫的)
        try:
            inner = json.loads(p.read_text(encoding="utf-8")).get("candidate_sha") or ""
        except (OSError, ValueError, AttributeError):
            continue
        if not (inner.startswith(sha) or sha.startswith(inner)):
            continue
        out.append(p)
    return sorted(out, key=lambda x: x.stat().st_mtime, reverse=True)


def receipts_for_shas(evidence_dir, shas, n=3):
    """本席的最近完成:只拿**派工序列裡的 sha** 去對收據(艦長 09-12 03:0x:不借全艙的)。
    回 None = 收據目錄不存在;回 [] = 序列裡沒有任何 sha 對得到收據。"""
    d = pathlib.Path(evidence_dir)
    if not d.is_dir():
        return None
    out = []
    for sha in shas:
        for f in find_receipts(evidence_dir, sha):
            out.append({"sha": sha, "path": str(f), "mtime": mtime_stamp(f), "mtime_epoch": f.stat().st_mtime})
            break
    out.sort(key=lambda r: r["mtime_epoch"], reverse=True)
    return out[:n]


# ── 機器狀態(第 48 項):誰佔機器、哪條 live 跑到哪、誰在排隊;讀 ps + log,不另建狀態檔 ──
PS_PATTERNS = (
    ("release", re.compile(r"\baib-release\.sh\b")),
    ("witness", re.compile(r"\bmain-merge-witness\.sh\b")),
    ("suite", re.compile(r"\baib-suite\b|\bcabin-safe-suite\.py\b")),
)
PS_TEST_RE = re.compile(r"(tests/test-[\w.-]+\.py)")
PS_ROW_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$")


def ps_snapshot():
    """`ps -axo pid,ppid,etime,command` 的原文;量不到回 None。治具用 --ps-file 餵假的。"""
    try:
        r = subprocess.run(["ps", "-axo", "pid,ppid,etime,command"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def machine_state(ps_text, lives, rel, now, env=None):
    """回 dict:occupants(誰佔機器,含它此刻在跑哪支考卷)/ live_inflight(哪條 live 還沒 DONE/STOP)/ queue(排隊)。
    ★ 排隊的載體是第 24 項每機租約(lease_view);租約讀不到 ⇒ 量不到,不用「沒人排隊」頂替。"""
    rows = []
    if ps_text is not None:
        for ln in ps_text.splitlines():
            m = PS_ROW_RE.match(ln)
            if m:
                rows.append({"pid": int(m.group(1)), "ppid": int(m.group(2)), "etime": m.group(3), "cmd": m.group(4)})
    occupants = []
    for r in rows:
        for kind, pat in PS_PATTERNS:
            if pat.search(r["cmd"]):
                # 它此刻在跑哪支考卷:找子孫行程裡的 tests/test-*.py
                running = None
                frontier = {r["pid"]}
                seen = set()
                while frontier and running is None:
                    nxt = set()
                    for c in rows:
                        if c["ppid"] in frontier and c["pid"] not in seen:
                            seen.add(c["pid"])
                            nxt.add(c["pid"])
                            mm = PS_TEST_RE.search(c["cmd"])
                            if mm:
                                running = mm.group(1)
                                break
                    frontier = nxt
                occupants.append({"kind": kind, "pid": r["pid"], "etime": r["etime"], "running_test": running})
                break
    lease = lease_view(now, env)
    # ★ 在飛先讀租約(第 75 項眼驗 §2-3:qa 在真艙看到「機器沒有驗證在跑」旁邊印著昨天別艙的 log 還在飛)。
    #   舊判準只問 LIVE log 有沒有 DONE/STOP 那一行,**沒有時效**:上一個艙留下的 log 永遠不會有尾行,於是永遠算在飛。
    #   租約是機器此刻的狀態(艦長 14:23 的三步判準第一步),它說話時 log 不得反駁;租約量不到才退回 log,且 log 要夠新。
    inflight = None
    state = (lease or {}).get("state")
    if state == "held":
        holder = lease.get("holder") or {}
        m = SHA_RE.search(str(holder.get("what") or ""))
        if m:
            sha8 = m.group(0)[:8]
            same = next((l for l in (lives or [])
                         if l.get("candidate") and l["candidate"].startswith(sha8)), None)
            inflight = {"path": (same or {}).get("path"), "candidate": sha8,
                        "mtime": (same or {}).get("mtime") or holder.get("acquired_at"), "source": "lease"}
    elif state in ("free", "expired"):
        pass            # ★ 租約是權威:機器空(free,租約檔不存在)或租約過期沒人續 ⇒ 沒有驗證在跑,**不看 log**
                        #   (a seat 眼驗那件:昨天別艙的 log 永遠沒有尾行,會與「機器沒有驗證在跑」印在一起互相矛盾)。
                        #   ★ free 是租約檔不存在時的值,不是 absent;absent 是「工具不在這棵樹」(a seat 18:2x 量到我原本漏了它)。
    else:               # None / unreadable / absent:租約量不到,才退回 LIVE log,而且 log 要夠新
        for l in (lives or []):
            if l["verdict"] is not None:
                continue
            age = None if l.get("mtime_epoch") is None else (float(now) - float(l["mtime_epoch"]))
            if age is None or age > LIVE_INFLIGHT_MAX_AGE_S:
                continue                     # 太舊(或量不到 mtime)的 log 不算在飛
            inflight = {"path": l["path"], "candidate": (l["candidate"] or "?")[:8],
                        "mtime": l["mtime"], "source": "live_log"}
            break
    return {"ps_ok": ps_text is not None, "occupants": occupants, "live_inflight": inflight,
            "release": ({"pid": rel.get("pid"), "done": rel.get("done"), "population": rel.get("population"),
                         "verdict": rel.get("verdict")} if rel else None),
            "lease": lease}


def lease_view(now, env=None):
    """第 24 項租約(tools/governance/host_lease.py)當「誰佔機器 / 誰在排隊」的載體。回 dict:
    state ∈ free/held/expired/unreadable/absent(工具不在這棵樹)、holder、queued(最近 30 分鐘被拒的)。
    ★ env 一路帶下去(艦長 09-12 20:2x 裁,開發 A 追使用端找到的最後一公尺):render_lines 已經把 dict(os.environ)
      傳給 build,host_lease.lease_path 也吃 env=,**只有這裡沒傳** ⇒ 隔離在最後一步掉下來,改讀真機那把租約。
      這是治具那一行(import aib_host_lease_fixture)之外的第一道:產品自己不從全域抓,測試就不必各自知道它碰得到租約。"""
    tool = pathlib.Path(__file__).resolve().parents[1] / "governance" / "host_lease.py"
    if not tool.exists():
        return {"state": "absent", "why": T("aib.taskzone.why_no_queue_carrier")}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("host_lease", str(tool))
        hl = importlib.util.module_from_spec(spec); spec.loader.exec_module(hl)
        st, cur, why = hl.status(hl.lease_path(env), now)
        queued = hl.recent_denied(hl.ledger_path(env), now)
        return {"state": st, "holder": cur, "why": why, "queued": queued or [], "path": str(hl.lease_path(env))}
    except Exception as e:  # noqa: BLE001 —— 租約讀不到不該讓任務區整張畫不出
        return {"state": "unreadable", "why": "%s: %s" % (type(e).__name__, e)}


def live_logs(live_dir):
    """LIVE*-PIPELINE-*.log,新到舊;每份給 candidate sha 與最後一條 DONE/STOP。"""
    d = pathlib.Path(live_dir)
    if not d.is_dir():
        return None
    logs = sorted(d.glob("LIVE*-PIPELINE-*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
    out = []
    for p in logs:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cand = LIVE_HEAD_RE.search(text)
        verdict, line = None, None
        for ln in text.splitlines():
            if LIVE_DONE_RE.match(ln):
                verdict, line = "DONE", ln.strip()
            elif LIVE_STOP_RE.match(ln):
                verdict, line = "STOP", ln.strip()
        try:
            mtime_epoch = p.stat().st_mtime
        except OSError:
            mtime_epoch = None
        out.append({"path": str(p), "candidate": cand.group(1) if cand else None,
                    "verdict": verdict, "line": line, "mtime": mtime_stamp(p), "mtime_epoch": mtime_epoch})
    return out


def read_lock(lock_path):
    p = pathlib.Path(lock_path)
    if not p.exists():
        return None
    d = {}
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
    except OSError:
        return None
    d["_path"] = str(p)
    d["_mtime"] = mtime_stamp(p)
    return d


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def release_state(live_dir, lock_path, now):
    """發版跑到哪:讀最新 RELEASE-*.log + 鎖檔 + 逐支結果表行數。回 dict 或 None(沒有發版 log)。"""
    d = pathlib.Path(live_dir)
    if not d.is_dir():
        return None
    logs = sorted(d.glob("RELEASE-*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not logs:
        return None
    p = logs[0]
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    st = {"path": str(p), "mtime": mtime_stamp(p), "verdict": "RUNNING", "line": None,
          "done": None, "population": None, "pid": None, "alive": None, "since": None,
          "max_sec": None, "version": None}
    lines = text.splitlines()
    for ln in lines:
        if RELEASE_DONE_MARK in ln:
            st["verdict"], st["line"] = "DONE", ln.strip()
        elif ln.lstrip().startswith(RELEASE_FAIL_GLYPH):
            st["verdict"], st["line"] = "STOP", ln.strip()
    m = RELEASE_POP_RE.search(text)
    if m:
        st["population"] = int(m.group(1))
    m = RELEASE_RESULTS_RE.search(text)
    if m and pathlib.Path(m.group(1)).exists():
        try:
            with open(m.group(1), "rb") as fh:
                st["done"] = sum(1 for _ in fh)
        except OSError:
            pass
    lock = read_lock(lock_path) if lock_path else None
    if lock:
        st["pid"] = lock.get("pid")
        st["alive"] = pid_alive(lock.get("pid") or "")
        st["since"] = lock.get("start")
        st["max_sec"] = lock.get("max_sec")
        st["version"] = lock.get("version")
        try:
            st["elapsed"] = int(now - int(lock.get("start_epoch") or 0))
        except ValueError:
            st["elapsed"] = None
    return st


# ── 佇列(等誰・誰等我)────────────────────────────────────────────────────
def queue_view(seat, env):
    """直接讀 dm_queue,不另存。回 (dict, None) 或 (None, why)。"""
    try:
        import dm_queue  # noqa: WPS433  (src/cockpit 已在 sys.path)
    except Exception as e:  # noqa: BLE001 —— 讀不到要說出口
        return None, T("aib.taskzone.why_import", {"mod": "dm_queue", "err": e})
    root = dm_queue.queue_root(env)
    if not root.is_dir():
        return None, T("aib.taskzone.why_no_dir", {"path": str(root)})
    by_seat = dm_queue.by_seat_for_render(env)
    mine = by_seat.get(seat, [])
    outbound = []
    for other, rows in by_seat.items():
        if other == seat:
            continue
        for r in rows:
            sender = (r.get("sender") or "")
            if seat in sender:
                outbound.append({"to": other, "when": r.get("when"), "ts": r.get("ts"), "id": r.get("id")})
    return {"root": str(root), "inbound": mine, "outbound": outbound}, None


# ── 組畫面 ───────────────────────────────────────────────────────────────────
def release_tag_shas(live_dir):
    """RELEASE-*.log 裡「已原子推送」那一行附近提到的 sha 前綴集合(tag 行)。沒有 log ⇒ 空集合。"""
    d = pathlib.Path(live_dir)
    out = set()
    if not d.is_dir():
        return out
    for p in d.glob("RELEASE-*.log"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            if RELEASE_DONE_MARK in ln or " tag " in ln or ln.lstrip().startswith("tag"):
                out.update(SHA_RE.findall(ln))
    return out


def _same_sha(a, b):
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def official_verdict(item, evidence_dir, lives, repo, main_ref, tag_shas=frozenset()):
    """「官方有」判定。回 (verdict, detail)。verdict ∈ official / no_receipt / not_on_main / done_unconfirmed / unmeasured。
    憑據三種(艦長 09-12 02:5x 補):收據檔 `-receipt-`、docs-only 的 `-result-docs-only-`、本包 sha 的 LIVE DONE 行。
    ★ 別包的 DONE 不算:LIVE log 的 DONE 只在 candidate 與本包 sha 同前綴時才算數。
    ★ DONE 行**單獨不算**:要連 RELEASE log 裡同一顆 sha 的 tag 行一起驗(艦長補件 2)。
    ★ 三種憑據都還要過 `merge-base --is-ancestor <sha> <main_ref>`:候選樹有收據 ≠ 官方有。"""
    sha = item.get("sha")
    if not sha:
        return "no_receipt", T("aib.taskzone.no_sha_in_status")
    receipts = find_receipts(evidence_dir, sha)
    own_done = [l for l in (lives or []) if l["verdict"] == "DONE" and _same_sha(l["candidate"], sha)]
    if not receipts and not own_done:
        foreign = [l for l in (lives or []) if l["verdict"] == "DONE"]
        if foreign:
            return "no_receipt", T("aib.taskzone.foreign_done_ignored",
                                   {"sha": sha, "other": (foreign[0]["candidate"] or "?")[:8]})
        return "no_receipt", T("aib.taskzone.no_receipt", {"sha": sha, "dir": str(evidence_dir)})
    if not receipts and not any(_same_sha(t, sha) for t in tag_shas):
        return "done_unconfirmed", T("aib.taskzone.done_unconfirmed",
                                     {"sha": sha, "log": pathlib.Path(own_done[0]["path"]).name})
    anc, why = git_is_ancestor(repo, sha, main_ref)
    src = receipts[0].name if receipts else pathlib.Path(own_done[0]["path"]).name
    when = (mtime_stamp(receipts[0]) if receipts else own_done[0]["mtime"])
    if anc is None:
        return "unmeasured", T("aib.taskzone.ancestor_unmeasured", {"sha": sha, "ref": main_ref, "why": why or "?"})
    if not anc:
        return "not_on_main", T("aib.taskzone.receipt_not_on_main", {"sha": sha, "ref": main_ref, "src": src, "when": when})
    return "official", T("aib.taskzone.official", {"sha": sha, "src": src, "when": when})


def build(args, env, now):
    marks = load_marks(args.status_enum)
    reds, missing = [], []
    out = {"seat": args.seat, "generated_at": stamp(now), "three": [], "blocks": {},
           "sources": {}, "reds": reds, "missing_sources": missing}

    # 正本一
    outline_path = pathlib.Path(args.outline)
    md, items, sec, row = None, {}, None, None
    if outline_path.exists():
        md = outline_path.read_text(encoding="utf-8", errors="replace")
        items = parse_items(md, marks)
        sec, row = parse_dispatch(md, args.seat, set(marks.values()) | EXTRA_MARKS)
        out["sources"]["outline"] = {"path": str(outline_path), "mtime": mtime_stamp(outline_path),
                                     "dispatch_section": sec, "items": len(items)}
    else:
        missing.append(T("aib.taskzone.why_no_file", {"path": str(outline_path)}))

    # 正本二
    decisions, why = load_decisions(args.decisions)
    if decisions is None:
        missing.append(why)
    else:
        out["sources"]["decisions"] = {"path": str(args.decisions), "mtime": mtime_stamp(args.decisions),
                                       "open": sum(1 for i in decisions if not i.get("resolved_at"))}
    open_dec = [i for i in (decisions or []) if not i.get("resolved_at")]
    by_id = {i["id"]: i for i in (decisions or []) if i.get("id")}

    # 正本三
    ho = latest_handoff(args.handoffs, args.seat, load_handoff_parser(args.handoff_parser))
    if not pathlib.Path(args.handoffs).is_dir():
        missing.append(T("aib.taskzone.why_no_dir", {"path": str(args.handoffs)}))
    out["sources"]["handoff"] = ho

    # 正本四
    lives = live_logs(args.live_dir)
    if lives is None:
        missing.append(T("aib.taskzone.why_no_dir", {"path": str(args.live_dir)}))
    if not pathlib.Path(args.evidence).is_dir():
        missing.append(T("aib.taskzone.why_no_dir", {"path": str(args.evidence)}))
    rel = release_state(args.live_dir, args.lock, now)
    tag_shas = release_tag_shas(args.live_dir)
    out["sources"]["evidence"] = {"dir": str(args.evidence), "live_logs": len(lives or []),
                                  "release": rel["path"] if rel else None}

    seq_items = []
    if row:
        for st in row["steps"]:
            if st["n"] and st["n"] in items:
                seq_items.append(items[st["n"]])
            elif st["n"] is None:
                # 文字步驟(例:`v0.59.2 tag 收口`):不是編號項目,狀態量不到;沒被 ~~劃掉~~ 就當還沒做。
                seq_items.append({"n": None, "title": st["text"][:48], "status": T("aib.taskzone.prose_step"),
                                  "mark": None, "sha": None, "decision_ref": None, "prose": True})
            else:
                # 有編號但最近項目表沒這一項:不丟掉(丟掉=序列少一步而看起來完整),印量不到。
                seq_items.append({"n": st["n"], "title": st["text"][:48],
                                  "status": T("aib.taskzone.item_not_in_table", {"n": st["n"]}),
                                  "mark": None, "sha": None, "decision_ref": None, "prose": True})

    # ① 這一包
    outline_src = (T("aib.taskzone.src_outline", {"sec": sec or "?"}) if md else None)
    outline_at = mtime_stamp(outline_path) if md else None
    if md is None:
        out["three"].append(unmeasured("this_package", T("aib.taskzone.why_no_file", {"path": str(outline_path)})))
    elif sec is None:
        out["three"].append(unmeasured("this_package", T("aib.taskzone.why_no_dispatch_table")))
    elif row is None:
        out["three"].append(unmeasured("this_package", T("aib.taskzone.why_seat_not_in_table", {"sec": sec, "seat": args.seat}), outline_src))
    else:
        cur = next((it for it in seq_items if it["mark"] != marks["done"]), None)
        if cur is None:
            val = T("aib.taskzone.sequence_all_done", {"line": row["line"], "n": len(seq_items)})
        else:
            # ① 行也吃 status_plain 與剝過 markdown 的標題:mark 就在同一句裡印,不剝會連著出現兩個符(a seat 眼驗 §2-2)
            val = T("aib.taskzone.this_package", {"sec": sec, "line": row["line"], "n": cur["n"] or "-",
                                                  "title": strip_md(cur["title"])[:48], "mark": cur["mark"] or "",
                                                  "status": (cur.get("status_plain") or cur["status"])[:60]})
        out["three"].append(cell("this_package", val, outline_src, outline_at))
        out["current"] = cur

    # ② 卡在誰
    stuck = []
    if rel:
        if rel["verdict"] == "RUNNING":
            prog = ("%s/%s" % (rel["done"] if rel["done"] is not None else "?",
                               rel["population"] if rel["population"] else "?"))
            stuck.append(T("aib.taskzone.wait_machine_release", {
                "version": rel.get("version") or "?", "prog": prog, "pid": rel.get("pid") or "?",
                "alive": T("aib.taskzone.alive") if rel.get("alive") else T("aib.taskzone.dead"),
                "since": rel.get("since") or "?", "max": rel.get("max_sec") or "?"}))
        else:
            stuck.append(T("aib.taskzone.release_ended", {"verdict": rel["verdict"], "line": (rel["line"] or "")[:80]}))
    cur = out.get("current")
    if cur and cur.get("decision_ref"):
        ref = cur["decision_ref"]
        d = by_id.get(ref)
        if decisions is None:
            stuck.append(T("aib.taskzone.decision_ledger_unreadable", {"id": ref}))
        elif d is None:
            stuck.append(T("aib.taskzone.decision_unregistered", {"id": ref}))
            reds.append({"rc": RC_DECISION_UNREGISTERED, "rule": "decision_unregistered", "item": cur["n"], "id": ref})
        elif d.get("resolved_at"):
            stuck.append(T("aib.taskzone.decision_resolved", {"id": ref, "decision": (d.get("decision") or "")[:60]}))
        else:
            stuck.append(T("aib.taskzone.wait_human", {"id": ref, "waited": fmt_wait(now - float(d.get("opened_epoch") or now))}))
    qv, qwhy = queue_view(args.seat, env)
    if qv and qv["outbound"]:
        oldest = min(qv["outbound"], key=lambda r: r.get("ts") or now)
        stuck.append(T("aib.taskzone.wait_delivery", {"n": len(qv["outbound"]), "to": oldest["to"], "when": oldest.get("when") or "?"}))
    if not stuck:
        stuck.append(T("aib.taskzone.stuck_none"))
    src2 = ", ".join(x for x in [
        (pathlib.Path(rel["path"]).name if rel else None),
        (pathlib.Path(args.decisions).name if decisions is not None else None),
        ("dm-queue" if qv else None)] if x)
    out["three"].append(cell("stuck_on", " ; ".join(stuck), src2 or None, stamp(now)))

    # ③ 上一件的下落
    if not row:
        out["three"].append(unmeasured("last_package", T("aib.taskzone.why_no_row")))
    else:
        done_items = [it for it in seq_items if it["mark"] == marks["done"]]
        if not done_items:
            out["three"].append(cell("last_package", T("aib.taskzone.no_done_yet"), outline_src, outline_at))
        else:
            last = done_items[-1]
            verdict, detail = official_verdict(last, args.evidence, lives, args.repo, args.main_ref, tag_shas)
            out["three"].append(cell("last_package", T("aib.taskzone.last_package", {
                "n": last["n"], "title": last["title"][:40], "detail": detail,
                "unchecked": T("aib.taskzone.unchecked_unmeasured")}),
                T("aib.taskzone.src_evidence"), stamp(now), ok=(verdict != "unmeasured"),
                why=(detail if verdict == "unmeasured" else None)))

    # 四塊
    # 現在在做:序列逐項;✅ 的每一項都過尺
    doing = []
    for it in seq_items:
        entry = {"n": it["n"], "title": it["title"], "mark": it["mark"], "status": it["status"],
                 "status_plain": it.get("status_plain") or it["status"],
                 "prose": bool(it.get("prose")), "source": outline_src, "measured_at": outline_at, "ok": True}
        if it["mark"] == marks["done"]:
            verdict, detail = official_verdict(it, args.evidence, lives, args.repo, args.main_ref, tag_shas)
            entry["official"] = verdict
            entry["evidence"] = detail
            if verdict == "unmeasured":
                entry["ok"] = False
            if verdict in ("no_receipt", "not_on_main", "done_unconfirmed"):
                reds.append({"rc": RC_NO_EVIDENCE, "rule": "done_without_evidence", "item": it["n"], "detail": detail})
        if it.get("decision_ref"):
            ref = it["decision_ref"]
            if decisions is None:
                # 列層也要印量不到(a seat §2 規則三那一列):讀的人眼睛在列上,空白像「沒什麼要說的」
                entry["decision"] = T("aib.taskzone.unmeasured", {"why": why})
                entry["ok"] = False
            elif ref not in by_id:
                entry["decision"] = T("aib.taskzone.decision_unregistered", {"id": ref})
                if not any(r["rule"] == "decision_unregistered" and r["item"] == it["n"] for r in reds):
                    reds.append({"rc": RC_DECISION_UNREGISTERED, "rule": "decision_unregistered", "item": it["n"], "id": ref})
            else:
                d = by_id[ref]
                entry["decision"] = (T("aib.taskzone.decision_resolved", {"id": ref, "decision": (d.get("decision") or "")[:60]})
                                     if d.get("resolved_at") else
                                     T("aib.taskzone.wait_human", {"id": ref, "waited": fmt_wait(now - float(d.get("opened_epoch") or now))}))
        doing.append(entry)
    out["blocks"]["doing"] = {"rows": doing, "source": outline_src, "measured_at": outline_at,
                              "ok": bool(row), "why": None if row else T("aib.taskzone.why_no_row")}

    # 等誰・誰等我
    waits = {"inbound": None, "hold": None, "outbound": None, "order": None, "human": None, "handoff": None, "machine": None}
    # 第 70 項 P6 後半:順位讀 state/turn-order.json 那一份正本,不讀信(九病第 9 病:順位只送一席、別席仍記舊的)。
    try:
        import turn_order  # noqa: WPS433
        pos, rec, owhy = turn_order.position(args.seat, env)
    except Exception as e:  # noqa: BLE001
        pos, rec, owhy = None, None, T("aib.taskzone.why_import", {"mod": "turn_order", "err": e})
    if rec:
        waits["order"] = cell("order", T("aib.taskzone.order", {
            "pos": (str(pos) if pos else "-"), "n": len(rec["order"]), "by": rec.get("set_by") or "?",
            "rev": rec.get("rev") or 0, "seq": " → ".join("@" + x for x in rec["order"])}),
            str(turn_order.path(env)), stamp(now))
    else:
        waits["order"] = unmeasured("order", T("aib.taskzone.why_no_order", {"why": owhy}))
    if qv is None:
        waits["inbound"] = unmeasured("inbound", qwhy)
        waits["hold"] = unmeasured("hold", qwhy)
        waits["outbound"] = unmeasured("outbound", qwhy)
    else:
        # 第 70 項 P4/任務區拆行(艦長 09-12 14:12 裁,qa 抓):hold_human 與「等你閒下來就送」混成一個數字而要人做的事相反,
        # 拆成兩行——「誰等我」只算排隊中的,「待人處理」另一行帶最早時刻與 rc(四語用 hold_rc 當鍵,不翻中文句)。
        held = [r for r in qv["inbound"] if r.get("status") == "hold_human"]
        inb = [r for r in qv["inbound"] if r.get("status") != "hold_human"]
        if held:
            oldest_h = min(held, key=lambda r: r.get("ts") or now)
            rc = oldest_h.get("hold_rc")
            waits["hold"] = cell("hold", T("aib.taskzone.hold_inbound", {
                "n": len(held), "when": oldest_h.get("when") or "?", "rc": ("rc%s" % rc) if rc is not None else "-"}),
                qv["root"], stamp(now))
        else:
            waits["hold"] = cell("hold", T("aib.taskzone.hold_none"), qv["root"], stamp(now))
        if inb:
            oldest = min(inb, key=lambda r: r.get("ts") or now)
            waits["inbound"] = cell("inbound", T("aib.taskzone.inbound", {"n": len(inb), "when": oldest.get("when") or "?",
                                                                          "sender": oldest.get("sender") or "?"}), qv["root"], stamp(now))
        else:
            waits["inbound"] = cell("inbound", T("aib.taskzone.inbound_none"), qv["root"], stamp(now))
        if qv["outbound"]:
            waits["outbound"] = cell("outbound", "; ".join(
                T("aib.taskzone.outbound_one", {"to": r["to"], "when": r.get("when") or "?"}) for r in qv["outbound"]),
                qv["root"], stamp(now))
        else:
            waits["outbound"] = cell("outbound", T("aib.taskzone.outbound_none"), qv["root"], stamp(now))
    if decisions is None:
        waits["human"] = unmeasured("human", why)
    else:
        if open_dec:
            oldest = min(open_dec, key=lambda i: float(i.get("opened_epoch") or now))
            waits["human"] = cell("human", T("aib.taskzone.human_open", {
                "n": len(open_dec), "oldest": oldest.get("id"), "waited": fmt_wait(now - float(oldest.get("opened_epoch") or now))}),
                str(args.decisions), mtime_stamp(args.decisions))
        else:
            waits["human"] = cell("human", T("aib.taskzone.human_none"), str(args.decisions), mtime_stamp(args.decisions))
    if ho:
        txt = T("aib.taskzone.handoff_from", {"file": pathlib.Path(ho["path"]).name, "shift": ho["shift"] or "?"})
        if ho["source"] == "parser":
            watch = (ho.get("fields") or {}).get("監視") or []
            txt += " " + T("aib.taskzone.handoff_parsed", {"seat": ho.get("from_seat") or "?", "rc": ho.get("parser_rc")})
            if watch:
                txt += " ; " + T("aib.taskzone.handoff_watch", {"value": str(watch[0].get("value", ""))[:80]})
        else:
            txt += " " + T("aib.taskzone.handoff_by_filename")
        waits["handoff"] = cell("handoff", txt, ho["path"], ho["mtime"])
    else:
        waits["handoff"] = unmeasured("handoff", T("aib.taskzone.why_no_handoff", {"seat": args.seat, "dir": str(args.handoffs)}))
    out["blocks"]["waiting"] = waits

    # 最近完成 3 件:本席序列裡的 sha 對收據;對不上就印量不到(不借全艙的)
    seq_shas = [it["sha"] for it in seq_items if it.get("sha")]
    rec = receipts_for_shas(args.evidence, seq_shas)
    if rec is None:
        out["blocks"]["recent"] = {"rows": [], "ok": False, "why": T("aib.taskzone.why_no_dir", {"path": str(args.evidence)}),
                                   "source": str(args.evidence), "measured_at": stamp(now)}
    elif not rec:
        why = (T("aib.taskzone.why_no_row") if not row else
               T("aib.taskzone.why_recent_unmatched", {"n": len(seq_shas)}))
        out["blocks"]["recent"] = {"rows": [], "ok": False, "why": why, "source": str(args.evidence), "measured_at": stamp(now)}
    else:
        rows = []
        for r in rec:
            anc, awhy = git_is_ancestor(args.repo, r["sha"], args.main_ref)
            rows.append({"sha": r["sha"][:8], "file": pathlib.Path(r["path"]).name, "mtime": r["mtime"],
                         "on_main": anc, "why": awhy, "source": r["path"], "measured_at": r["mtime"],
                         "ok": anc is not None})
        out["blocks"]["recent"] = {"rows": rows, "ok": True, "why": None, "source": str(args.evidence),
                                   "measured_at": stamp(now), "scope": T("aib.taskzone.recent_scope_seat", {"seat": args.seat})}

    # 機器狀態格(第 48 項)
    ps_text = (pathlib.Path(args.ps_file).read_text(encoding="utf-8", errors="replace")
               if args.ps_file and pathlib.Path(args.ps_file).exists() else (None if args.ps_file else ps_snapshot()))
    mach = machine_state(ps_text, lives, rel, now, env)
    out["machine"] = mach
    if not mach["ps_ok"]:
        waits["machine"] = unmeasured("machine", T("aib.taskzone.why_no_ps"))
    else:
        parts = []
        if mach["occupants"]:
            for o in mach["occupants"]:
                parts.append(T("aib.taskzone.machine_occupant", {
                    "kind": T("aib.taskzone.kind_" + o["kind"]), "pid": o["pid"], "etime": o["etime"],
                    "test": o["running_test"] or T("aib.taskzone.no_test_child")}))
        else:
            parts.append(T("aib.taskzone.machine_free"))
        if mach["live_inflight"]:
            parts.append(inflight_line(mach["live_inflight"]))
        else:
            parts.append(T("aib.taskzone.live_none"))
        lv = mach["lease"]
        if lv["state"] in ("absent", "unreadable"):
            parts.append(T("aib.taskzone.unmeasured", {"why": lv["why"]}))
        else:
            if lv["state"] == "held":
                h = lv["holder"]
                what = T("aib.lease.holder", {"name": h.get("owner", "?"), "what": h.get("what", "?"), "since": h.get("acquired_at", "?"),
                                              "at": h.get("expiry_at", "?"), "id": h.get("run_id", "?")})
            elif lv["state"] == "expired":
                h = lv["holder"]
                what = T("aib.lease.expired", {"name": h.get("owner", "?"), "what": h.get("what", "?"), "at": h.get("expiry_at", "?")})
            else:
                what = T("aib.lease.free")
            q = lv["queued"]
            qtxt = (T("aib.lease.queued", {"n": len(q), "name": q[0].get("owner", "?"), "what": q[0].get("what", "?"), "at": q[0].get("ts", "?")})
                    if q else T("aib.taskzone.lease_none_queued"))
            parts.append(T("aib.taskzone.lease_queue", {"what": what, "text": qtxt}))
        waits["machine"] = cell("machine", " ; ".join(parts), "ps + LIVE log", stamp(now))

    # 不准做
    forbid = []
    if rel and rel["verdict"] == "RUNNING" and rel.get("alive"):
        forbid.append(cell("freeze", T("aib.taskzone.forbid_release_freeze", {
            "pid": rel.get("pid") or "?", "since": rel.get("since") or "?", "max": rel.get("max_sec") or "?"}),
            rel["path"], rel["mtime"]))
    forbid.append(cell("no_write_path", T("aib.taskzone.forbid_no_write_path"), "ai-task-zone.py", stamp(now)))
    # ★ 規格(TASK-ZONE-COMPACT §7)要的是「盲區數字**併進**沒檢查燈」,現在**只做到一半**(a seat 19:16 §1):
    #   這一格恆為「量不到」⇒ 恆被 unmeasured_cells 收走 ⇒ 燈的數字裡確實有它一格,但那是一格「量不到」,
    #   不是第 42 項算出來的盲區**數量**。要真數字得呼叫 blindspots_42(),而它跑的是掃全庫的工具(以秒計),
    #   放進 build() 會毀掉第 51 項的每幀預算(重算 113 ms)——所以後半是**等產品**:等第 42 項給一份可快取的讀數,
    #   或把它接在 detail/發版那條路(--release-gaps 已經在用)。在那之前不要把格名寫成「盲區併進去了」。
    forbid.append(unmeasured("blind_spots", T("aib.taskzone.why_blind_spot_ruler_missing")))
    out["blocks"]["forbid"] = forbid

    out["footer"] = {"sec": sec, "handoff_shift": (ho or {}).get("shift"), "sandbox_note": T("aib.taskzone.sandbox_not_listed")}
    # 量不到的格(性質圈:走訪整份輸出,任何 ok=False 的 dict)—— 不逐塊點名,點名會漏
    out["unmeasured_cells"] = [path for path, _ in walk_cells(out) if _.get("ok") is False]
    # 第 68 項 compact 的資料(規則 1/2/3/5/6/7):進度分子分母、現在/卡在/下一步、最近完成(短標+sha8+時刻)、三燈
    # 「正在做」的人類交辦(the maintainer 09-15):讀 tabs_payload(見 human_task_view 註解:queue_view 看不到已送到/已讀的信)
    human, human_why = human_task_view(args.seat, env)
    out["compact"] = compact_data(items, seq_items, out.get("current"), rel, mach, open_dec if decisions is not None else None,
                                  out["blocks"]["recent"], out, marks, now, human=human, human_why=human_why)
    rc = RC_OK
    for r in reds:
        rc = max(rc, r["rc"])
    # ★ a seat 結論 3:--strict 的意思是「我要確定我看到的是全的」;正本在但算不出來(例:--repo 指錯)
    #   對讀的人與正本缺是同一件事,所以一起紅。預設仍不紅(量不到要印出來,不是擋住)。
    if args.strict and (missing or out["unmeasured_cells"]):
        rc = max(rc, RC_STRICT_MISSING)
    out["rc"] = rc
    return out


def live_label(path):
    """live log 檔名 → 「liveN」(LIVE30-PIPELINE-0912.log → live30);認不出編號就用去掉副檔名的檔名。"""
    if not path:
        return ""                            # 來源是租約、樹上沒有對應的 log:沒有 liveN 這個名字可印
    name = pathlib.Path(str(path)).name
    m = re.search(r"(?i)live\s*-?\s*(\d+)", name)
    return "live%s" % m.group(1) if m else (name.rsplit(".", 1)[0] or name)


def inflight_line(inflight):
    """在飛那一句。path 為 None(租約持有、沒有 LIVE log)不得 Path(None)。the maintainer 09-15 22:4x。"""
    if not inflight:
        return T("aib.taskzone.live_none")
    sha = inflight.get("candidate") or "?"
    path = inflight.get("path")
    if path:
        return T("aib.taskzone.live_inflight", {
            "file": pathlib.Path(str(path)).name, "sha": sha})
    return T("aib.taskzone.live_inflight_lease", {"sha": sha})


def strip_md(text):
    """產生器輸出不進 markdown:粗體記號原樣上畫面(第 75 項眼驗 §2-1 在 main 上實見)。"""
    return (text or "").replace("**", "")


def plain_status(status, mark_chars):
    """狀態句去掉**開頭**那個狀態符(它與項目自己的 mark 是同一個字元,印兩次讀起來像重複)與 markdown。
    只剝開頭:句子中間提到的符是內容不是標記。"""
    t = strip_md(status).strip()
    while t and t[0] in mark_chars:
        t = t[1:].lstrip()
    return t


def short_title(title, limit=12):
    """規則 3:短標題 = 項目那格第一個逗號/冒號/括號/破折號之前的字,去掉 markdown 粗體,≤ limit 字。"""
    t = (title or "").replace("**", "").strip()
    for sep in (",", ",", ":", ":", "(", "(", "——", "—", ";", ";"):
        i = t.find(sep)
        if i > 0:
            t = t[:i]
    t = t.strip()
    return t if len(t) <= limit else t[:limit - 1] + "…"      # 剛好截到 limit 字時看起來像完整標題(a seat §7)


# ── 「正在做」= 最早一則未完成的人類交辦(the maintainer 09-15 02:5x;a seat 03:0x–03:1x 裁)─────────────────
# ★ 來源是 dm_queue.tabs_payload,**不是** queue_view:queue_view 走 list_items → order.json,
#   而 order.json 只留 queued/claimed/hold ⇒ 已送到(delivered)、已讀(read)的人類信一封都讀不到——
#   正好是「正在做」最常見的情況;照舊接會恆退回派工序列,不紅不叫。
# ★ 頁:waiting + in_progress(第 164a 項把 read 改名 in_progress)。read 是過渡期:改名前它裝「已讀未完成」,
#   改名後這頁不存在、集合不變。164a 合入後拿掉 read(a seat 03:2x 同意)。done / archived / deleted 不看。
HUMAN_TASK_TABS = ("waiting", "in_progress", "read")


def human_task_view(seat, env):
    """回 ({"row": 最早一則人類交辦的列或 None, "more": 另有幾件}, None) 或 (None, why)。只讀,不改佇列。"""
    try:
        import dm_queue  # noqa: WPS433  (src/cockpit 已在 sys.path)
        tabs = dm_queue.tabs_payload(seat, env=env)
    except Exception as e:  # noqa: BLE001 —— 讀不到要說出口,不當成「沒有交辦」
        return None, "%s: %s" % (type(e).__name__, e)
    pages = tabs.get("tabs") or {}
    rows = [r for t in HUMAN_TASK_TABS for r in ((pages.get(t) or {}).get("rows") or [])
            if r.get("sender_kind") == "human"]
    if not rows:
        return {"row": None, "more": 0}, None
    rows.sort(key=lambda r: (int(r.get("sent_at") or r.get("ts") or 0), r.get("id") or ""))
    return {"row": rows[0], "more": len(rows) - 1}, None


def _changed_key(v):
    """changed_at 的可比較秒數:數字或 ISO 字串;讀不懂回 None(排在最舊,不猜成現在)。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v).timestamp()
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return None
    return None


def outline_nodes(items, path=()):
    """大綱樹深度優先展開:[(索引路徑 tuple, 節點 dict)];非 dict 的項略過。"""
    out = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        if isinstance(it, dict):
            out.append((path + (i,), it))
            out += outline_nodes(it.get("items"), path + (i,))
    return out


def outline_anchor(items):
    """錨點路徑:state=in_progress 取 changed_at 最新(一樣新取先走到的);沒有取深度優先第一個 pending;都沒有回 None。"""
    best, first_pending = None, None
    for path, it in outline_nodes(items):
        st = it.get("state")
        if st == "in_progress":
            k = _changed_key(it.get("changed_at"))
            k = float("-inf") if k is None else k
            if best is None or k > best[0]:
                best = (k, path)
        elif st == "pending" and first_pending is None:
            first_pending = path
    return best[1] if best else first_pending


def compact_data(items, seq_items, cur, rel, mach, open_dec, recent_block, out, marks, now, human=None, human_why=None):
    """compact 版面的資料層(渲染器不再自己算)。每一格算不出來就是 None,渲染時印「—」或量不到。
    human:human_task_view 的結果。有人類交辦 ⇒ d["task"] 放那一則(大綱樹的列由 render_compact 依寬高排);
    沒有 ⇒ d["task"] = None,「正在做」照舊用派工序列第一項,d["now_source"] = "dispatch";讀不到 ⇒ now_source = 原因。"""
    d = {"progress": None, "machine": None, "now": None, "stuck": [], "next": [], "recent": [], "lamps": {},
         "task": None, "now_source": None}
    row = (human or {}).get("row")
    if row:
        ol = row.get("outline") if isinstance(row.get("outline"), dict) else None
        started = row.get("started") if isinstance(row.get("started"), dict) else None
        tree = ol.get("items") if ol and isinstance(ol.get("items"), list) and ol.get("items") else None
        d["task"] = {"who": row.get("sender") or "?", "at": (row.get("when") or "")[6:11] or "?",
                     "title": ((ol or {}).get("title") or None) if tree else None,
                     "items": tree, "anchor": list(outline_anchor(tree) or []) if tree else None,
                     "first_step": (started or {}).get("first_step") or None,
                     "msg": row.get("msg") or "", "more": int((human or {}).get("more") or 0),
                     "marks": dict(marks)}          # 狀態符只從 status-enum 正本來(渲染端不另查)
    else:
        d["now_source"] = "dispatch" if human_why is None else human_why
    # 規則 1:分子 = 最新那張最近項目表裡標 ✅ 的項數,分母 = 該表項數
    if items:
        newest = max((it["section"] for it in items.values()), key=_secnum)
        pool = [it for it in items.values() if it["section"] == newest]
        d["progress"] = {"section": newest, "done": sum(1 for it in pool if it["mark"] == marks["done"]), "total": len(pool)}
    # 機器格:租約 > 發版 > 在飛 live > 空;ps 量不到就量不到
    if mach and mach.get("ps_ok"):
        lv = mach.get("lease") or {}
        if lv.get("state") == "held":
            h = lv.get("holder") or {}
            d["machine"] = T("aib.taskzone.c_machine", {"what": "%s %s" % (h.get("owner", "?"), h.get("what", "?"))})
        elif mach.get("release") and mach["release"].get("verdict") == "RUNNING":
            r = mach["release"]
            d["machine"] = T("aib.taskzone.c_machine", {"what": T("aib.taskzone.c_release_prog", {
                "n": r.get("done") if r.get("done") is not None else "?", "total": r.get("population") or "?"})})
        elif mach.get("live_inflight"):
            d["machine"] = T("aib.taskzone.c_machine", {"what": T("aib.taskzone.c_live_prog", {"sha": mach["live_inflight"]["candidate"]})})
        elif mach.get("occupants"):
            o = mach["occupants"][0]
            d["machine"] = T("aib.taskzone.c_machine", {"what": "%s %s" % (T("aib.taskzone.kind_" + o["kind"]), o.get("running_test") or "")})
        else:
            d["machine"] = T("aib.taskzone.c_machine_free")
    else:
        d["machine"] = T("aib.taskzone.unmeasured", {"why": T("aib.taskzone.why_no_ps")})
    # 規則 2/3:現在 = 編號 + 短標題 + 符 + 狀態短句;文字步驟不列(規則 3)
    numbered = [it for it in seq_items if it.get("n") and not it.get("prose")]
    pending = [it for it in numbered if it["mark"] != marks["done"]]
    if pending:
        it = pending[0]
        d["now"] = "%s %s  %s %s" % (it["n"], short_title(it["title"]), it.get("mark") or "",
                                     short_title(it.get("status_plain") or it.get("status") or "", 24))
    elif numbered:
        # 艦長 12:20 裁(a801):編號項目全完成時,有在飛候選就把「現在」印成「live N 驗證中 <sha8>」(從 machine.live_inflight 取),
        # 沒有在飛才印「序列全部 ✅」——艦長那一席的「現在」是它正在驗的那條 live,不是一句空的全 ✅。文字步驟仍不印。
        li = (mach or {}).get("live_inflight") if mach else None
        if li:
            lbl = live_label(li.get("path") or "")
            d["now"] = (T("aib.taskzone.c_now_live", {"file": lbl, "sha": li.get("candidate") or "?"}) if lbl
                        else T("aib.taskzone.c_live_prog", {"sha": li.get("candidate") or "?"}))
        else:
            d["now"] = T("aib.taskzone.c_all_done")
    # 卡在(規則 6:等誰併進來;佇列裡的信不列)
    if rel and rel.get("verdict") == "RUNNING":
        d["stuck"].append(T("aib.taskzone.c_wait_machine", {"n": rel.get("done") if rel.get("done") is not None else "?", "total": rel.get("population") or "?"}))
    if cur and cur.get("decision_ref") and open_dec is not None:
        dd = next((x for x in open_dec if x.get("id") == cur["decision_ref"]), None)
        if dd:
            d["stuck"].append(T("aib.taskzone.c_wait_human", {"id": dd["id"], "waited": fmt_wait(now - float(dd.get("opened_epoch") or now))}))
    # 下一步:cur 之後的編號項目,最多三項
    d["next"] = ["%s %s" % (it["n"], short_title(it["title"])) for it in pending[1:4]]
    # 規則 5:最近完成 ≤3、同 sha 一次;短標 + sha8 + HH:MM;不印檔名
    seen = set()
    for r in (recent_block.get("rows") or []):
        if r["sha"] in seen:
            continue
        seen.add(r["sha"])
        title = next((short_title(it["title"]) for it in seq_items if it.get("sha") and (it["sha"].startswith(r["sha"]) or r["sha"].startswith(it["sha"][:8]))), "")
        # 三態不得同形(a seat §4):在 main ✅ / 不在 main 印「未」/ 量不到印 ?——原本後兩者都是空白。
        mark = marks["done"] if r.get("on_main") else (T("aib.taskzone.c_unmeasured_mark") if r.get("on_main") is None
                                                       else T("aib.taskzone.c_not_on_main_mark"))
        d["recent"].append({"n": next((it["n"] for it in seq_items if it.get("sha") and it["sha"].startswith(r["sha"])), ""),
                            "title": title, "sha": r["sha"], "at": r.get("mtime"), "official": bool(r.get("on_main")), "mark": mark})
    d["recent"] = d["recent"][:3]
    # 三燈(規則 6/7):等人裁 = 全艙未裁;偏了 = 第七符 🟣 的項;沒檢查 = 量不到的格數 + 盲區併入。
    # ★ 量不到就是 None,不是 0(第 49 項只讀數字;開發 A a seat 09-12 13:45、艦長 13:50 裁「底列的字與 dict 同源,不要兩份算法」):
    #   drifted 在第 66 項落地前 build 沒有 out["drifted"] 這個鍵 ⇒ None(舊寫法 `or []` 印 0,那個 0 是假的)。
    #   measured_at(開發 A 14:04 定四鍵):投影那一刻的 epoch 秒整數 —— 與底列「投影 HH:MM」同一個 now;停住的 0 與剛量的 0 才分得開。
    # ★ drifted_enabled(the maintainer 09-15 03:0x「偏了 ? 長期問號」;a seat 03:09):第 66 項沒落地(build 沒有 drifted 鍵)與
    #   「讀不到」原本同為 drifted=None,畫面同印 ?。分開:False = 功能未啟用(印「未啟用」)、True = 有在量、
    #   None = 連這件都不知道(lamps() 整個失敗時)。drifted 本身的語意不動(整數或 None)。
    d["lamps"] = {"human": (len(open_dec) if open_dec is not None else None),
                  "drifted": (len(out["drifted"]) if out.get("drifted") is not None else None),
                  "unchecked": (len(out["unmeasured_cells"]) if out.get("unmeasured_cells") is not None else None),
                  "measured_at": int(now),
                  "drifted_enabled": "drifted" in out}
    return d


def walk_cells(node, path="$"):
    """走訪輸出樹,回 [(路徑, dict)]:所有帶 ok 鍵的 dict(三行、四塊、每一列)。給 --strict 與臂共用。"""
    found = []
    if isinstance(node, dict):
        if "ok" in node:
            found.append((path, node))
        for k, v in node.items():
            found += walk_cells(v, "%s.%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += walk_cells(v, "%s[%d]" % (path, i))
    return found


# ── 印出(版面照 pending/TASK-ZONE-REFERENCE-0912.md;框的右緣與兩欄並排留給第 39 項用第 34 項接口做)──
def _hms(iso, now_iso):
    """量測時刻在畫面上印短的:同一天只印 HH:MM:SS,不同天印 MM-DD HH:MM。JSON 裡仍是完整 ISO。"""
    if not iso:
        return ""
    try:
        d = datetime.datetime.fromisoformat(iso)
        n = datetime.datetime.fromisoformat(now_iso)
    except ValueError:
        return iso
    return d.strftime("%H:%M:%S") if d.date() == n.date() else d.strftime("%m-%d %H:%M")


def _src(c, now_iso):
    """每格右側:「來源 <檔> <時刻>」;沒有來源的格(量不到)只印「量測 <時刻>」,不印空的來源。"""
    at = _hms(c.get("measured_at"), now_iso)
    if c.get("source"):
        return "   " + T("aib.taskzone.src_label") + " " + c["source"] + ((" " + at) if at else "")
    return ("   " + T("aib.taskzone.at_label") + " " + at) if at else ""


def _box(title, rows, width):
    top = "┌ " + title + " "
    top += "─" * max(1, width - _cells(top) - 1) + "┐"
    body = ["│ " + r for r in rows]
    return [top] + body + ["└" + "─" * max(1, width - 2) + "┘"]


def _cells(s):
    """顯示欄數(CJK/全形 2 格,跳脫碼 0 格)。正本在 render_lib:進度條上色之後,自己數會把 SGR 算成寬度,
    把右側「機器」欄推歪、也會裁到色碼中間。"""
    return render_lib.visible_width(s)


def render_text(out, width=100, mode="compact", color=None, max_lines=None):
    """mode=compact(預設,第 68 項;the maintainer 11:5x)/ detail(原畫法:每格帶來源與時刻)。
    color:None / "256" / "truecolor";只有 compact 的進度條與大綱用得到,detail 一律純文字。
    max_lines:compact「正在做」大綱依高度刪列用;None = 不限。"""
    if mode == "compact":
        return render_compact(out, width, color=color, max_lines=max_lines)
    return render_detail(out, width)


def _fit(text, width):
    """規則 2:一行,超寬截斷加「…」。"""
    return _clip(text, width) if _cells(text) > width else text


def _hm(iso, now_iso):
    """compact 用 HH:MM(規則 3);不同天印 MM-DD HH:MM。"""
    t = _hms(iso, now_iso)
    return t[:-3] if len(t) == 8 else t


def _drop_from_bottom(rows, budget, pred):
    """從底往上刪符合 pred 的列,刪到 len(rows) <= budget 為止(就地改)。"""
    i = len(rows) - 1
    while i >= 0 and len(rows) > budget:
        if pred(rows[i]):
            del rows[i]
        i -= 1


def task_rows(task, width, budget=None, pct=None, color=None):
    """「正在做」的列;第一列已帶標籤。budget = 標籤那一列之外還能用幾列(None = 不限)。
    畫法(a seat 09-15 03:0x,the maintainer「從正在忙的那件向上或向下樹狀展開,至少展到自己對應的大項目」):
      ◆ 大項目 → 祖先鏈一層不省 → 錨點同層:前面已完成收一行、其他非完成的兄弟逐項印(不藏紅燈)、▶ 錨點
      → 錨點子項展開一層(更深印 +N)→ 錨點後面的兄弟最多 2 項其餘 +N → 另有 N 件交辦排隊中。
    高度不夠的刪除順序:排隊中那列 → 子項裡已完成 → 後面的兄弟縮成 +N;◆、祖先鏈、錨點行永不刪。
      ★ 三步之後仍放不下(規格沒寫到):再從底往上砍非祖先非錨點的列 —— 交件單列為待確認。
    寬 <60:◆ → ▶ 錨點 → 第一個子項 → +N。
    狀態符只從 task["marks"](status-enum 正本)取;不認得的 state 印「?」不猜。色只經 render_lib.outline_paint。"""
    marks = task.get("marks") or {}
    label = T("aib.taskzone.c_now")
    pad = " " * (_cells(label) + 1)

    def sym(it):
        return marks.get(it.get("state")) or "?"

    def txt(it):
        return str(it.get("text") or "").replace("\n", " ").strip()

    # 原話與「未拆解」前 24 字同一個取法:dm_text.original_line(剝路由頭 / 回報抬頭、讀長訊指標、保留 the maintainer 令)。
    #   指標檔讀不到 ⇒ 印「原話量不到」,不印指標那行、不印路徑(a seat 09-15 04:04 裁)。
    q_state, q_text = dm_text.original_line(task.get("msg"))
    if q_state == dm_text.RELAY_UNREADABLE:
        q_text = T("aib.taskzone.c_task_quote_unreadable")
    msg24 = q_text[:24]
    items = task.get("items")
    anchor = task.get("anchor") or []
    def fit_head(key, field, text, extra):
        # 窄框(a seat 09-15 04:2x 裁):截前面的原話 / 標題,「(未拆解)」「(the maintainer HH:MM 交辦)」尾巴必須完整。
        #   先扣掉尾巴寬度再截文字;交給外框 _fit 截尾會把尾巴截掉。
        room = width - _cells(label) - 1 - _cells(T(key, dict(extra, **{field: ""})))
        if _cells(text) > room:
            text = render_lib.trunc(text, max(1, room))
        return T(key, dict(extra, **{field: text}))

    if not items:
        head = fit_head("aib.taskzone.c_task_unsplit", "text", task.get("first_step") or msg24, {})
    else:
        head = fit_head("aib.taskzone.c_task_head", "title", task.get("title") or msg24,
                        {"who": task.get("who") or "?", "at": task.get("at") or "?"})
    rows, after, ind = [], [], ""        # 每列 [種類, 文字, 狀態鍵]
    if items and anchor:
        nodes = items
        for depth, idx in enumerate(anchor[:-1]):
            node = nodes[idx]
            rows.append(["ancestor", "  " * depth + "├ %s %s" % (sym(node), txt(node)), node.get("state")])
            nodes = node.get("items") or []
        ind = "  " * (len(anchor) - 1)
        ai = anchor[-1]
        before = [it for it in nodes[:ai] if isinstance(it, dict)]
        done = [it for it in before if it.get("state") == "done"]
        if done:
            rows.append(["done_group", ind + "├ %s %s" % (marks.get("done") or "?", T("aib.taskzone.c_done_group", {
                "text": " · ".join(short_title(txt(it)) for it in done), "n": len(done)})), "done"])
        for it in before:
            if it.get("state") != "done":
                rows.append(["before", ind + "├ %s %s" % (sym(it), txt(it)), it.get("state")])
        a = nodes[ai]
        rows.append(["anchor", ind + "▶ %s %s" % (sym(a), txt(a)), "anchor"])
        for ch in [it for it in (a.get("items") or []) if isinstance(it, dict)]:
            deeper = len(outline_nodes(ch.get("items")))
            rows.append(["child", ind + "  ├ %s %s%s" % (sym(ch), txt(ch), (" " + T("aib.taskzone.c_more", {"n": deeper})) if deeper else ""),
                         ch.get("state")])
        after = [it for it in nodes[ai + 1:] if isinstance(it, dict)]
        for it in after[:2]:
            rows.append(["after", ind + "├ %s %s" % (sym(it), txt(it)), it.get("state")])
        if len(after) > 2:
            rows.append(["after_more", ind + "  " + T("aib.taskzone.c_more", {"n": len(after) - 2}), None])
    if task.get("more"):
        rows.append(["queue", T("aib.taskzone.c_queue_more", {"n": task["more"]}), None])
    # 「原話」(a seat 03:34,the maintainer 防跑偏關卡):大綱 title 是 AI 寫的簡化,◆ 下一行並列 the maintainer 原話(取法見上),
    #   讓他一眼比對有沒有做偏。永不刪、窄框也保留;沒大綱時不另加(那一行本身就是原信)。可摺:下一包。
    quote = ""
    if items:
        if q_state == dm_text.RELAY_UNREADABLE:
            quote = q_text
        elif q_text:
            quote = T("aib.taskzone.c_task_quote", {"text": q_text})
    room = None if budget is None else max(0, budget - (1 if quote else 0))
    if width < 60:
        keep = [r for r in rows if r[0] == "anchor"] + [r for r in rows if r[0] == "child"][:1]
        queue = [r for r in rows if r[0] == "queue"]      # 排隊列窄框也印完整字樣,不併進 +N(a seat 04:2x)
        hidden = len(rows) - len(keep) - len(queue)
        rows = [[k, t.lstrip(), s] for k, t, s in keep]
        if hidden:
            rows.append(["more", T("aib.taskzone.c_more", {"n": hidden}), None])
        rows += queue
    elif room is not None and len(rows) > room:
        _drop_from_bottom(rows, room, lambda r: r[0] == "queue")
        _drop_from_bottom(rows, room, lambda r: r[0] == "child" and r[2] == "done")
        if len(rows) > room and any(r[0] in ("after", "after_more") for r in rows):
            rows = [r for r in rows if r[0] not in ("after", "after_more")]
            rows.append(["after_more", ind + "  " + T("aib.taskzone.c_more", {"n": len(after)}), None])
        _drop_from_bottom(rows, room, lambda r: r[0] not in ("ancestor", "anchor"))
    for r in reversed(rows):                # 錨點之後的最後一個樹枝收成 ╰;錨點以上(祖先鏈)不收,它下面還有錨點
        if r[0] == "anchor":
            break
        if "├ " in r[1]:
            r[1] = r[1].replace("├ ", "╰ ", 1)
            break
    lines = [label + " " + render_lib.outline_paint(head, "head", color, pct)]
    if quote:
        lines.append(pad + quote)
    for kind, text, st in rows:
        lead = len(text) - len(text.lstrip(" "))      # 縮排不上色(粗體亮白只包字,不包空白)
        lines.append(pad + text[:lead] + render_lib.outline_paint(text[lead:], "anchor" if kind == "anchor" else st, color, pct))
    return lines


def render_compact(out, width=100, color=None, max_lines=None):
    """第 68 項精簡版面:進度條一行 → 正在做/正卡在/下一步各一行 → 最近完成 ≤3 列 → 底列三燈 + 投影時刻。
    來源與時刻不逐行印(規則 4;鐵律沒變,只是收進 detail);文字步驟、收據檔名、佇列裡的信、不准做都不印。
    進度條(the maintainer 09-15 02:5x):`▰▱▱▱▱▱▱▱▱▱ 10%`,格子與色全由 render_lib.progress_bar 給;color=None 零跳脫碼。
    窄框(<60)不畫條,百分比仍是純文字(規則 9 原樣)。
    「正在做」(the maintainer 09-15):有人類交辦 ⇒ task_rows 的大綱樹;沒有 ⇒ 派工序列第一項 +「(來源:派工序列)」。
    max_lines(頁簽給的高度):大綱放不下時先把「最近完成」縮到 1 列(a seat 03:1x 預設),再交給 task_rows 照刪除順序砍。"""
    now_iso = out["generated_at"]
    c = out.get("compact") or {}
    narrow = width < 60
    lines = []
    pr = c.get("progress")
    pct = None
    if pr and pr["total"]:
        pct = int(round(100.0 * pr["done"] / pr["total"]))
        if narrow:
            head = T("aib.taskzone.c_progress_narrow", {"sec": pr["section"], "n": pr["done"], "total": pr["total"], "count": pct})
        else:
            head = T("aib.taskzone.c_progress", {"sec": pr["section"], "n": pr["done"], "total": pr["total"],
                                                 "text": render_lib.progress_bar(pct, color)})
    else:
        head = T("aib.taskzone.c_progress_unmeasured")
    machine = c.get("machine") or T("aib.taskzone.c_machine_free")
    gap = max(1, width - _cells(head) - _cells(machine))
    lines.append(_fit(head + " " * gap + machine, width))
    lines.append("")
    rec = c.get("recent") or []
    if narrow:
        rec = rec[:1]
    task = c.get("task")
    if task:
        # 大綱之外的固定列:進度 1 + 空 1 + 正卡在 1 + 下一步 1 + 空 1 + 最近完成標題 1 + 最近完成列(沒有時 1)+ 空 1 + 底列 1
        tree = task_rows(task, width, None, pct=pct, color=color)
        fixed = 8 + max(1, len(rec))
        if max_lines and fixed + len(tree) > max_lines and len(rec) > 1:
            rec = rec[:1]
            fixed = 9
        if max_lines and fixed + len(tree) > max_lines:
            tree = task_rows(task, width, max(0, max_lines - fixed - 1), pct=pct, color=color)
        lines += [_fit(x, width) for x in tree]
    else:
        now_txt = c.get("now") or (out["three"][0]["value"] if out["three"] and not out["three"][0]["ok"] else T("aib.taskzone.c_none"))
        src = c.get("now_source")
        suffix = ""
        if c.get("now") and src == "dispatch":
            suffix = " " + T("aib.taskzone.c_src_dispatch")
        elif src and src != "dispatch":
            suffix = " " + T("aib.taskzone.unmeasured", {"why": src})      # 人類交辦讀不到:說出口,不當成沒有
        lines.append(_fit("%s %s%s" % (T("aib.taskzone.c_now"), now_txt, suffix), width))
    stuck = " · ".join(c.get("stuck") or []) or T("aib.taskzone.c_none")
    lines.append(_fit("%s %s" % (T("aib.taskzone.c_stuck"), stuck), width))
    nxt = " → ".join(c.get("next") or []) or T("aib.taskzone.c_none")
    lines.append(_fit("%s %s" % (T("aib.taskzone.c_next"), nxt), width))
    lines.append("")
    lines.append(T("aib.taskzone.c_recent"))
    if not rec:
        rb = out["blocks"].get("recent") or {}
        lines.append("  " + (T("aib.taskzone.unmeasured", {"why": rb.get("why")}) if rb.get("why") else T("aib.taskzone.c_none")))
    for r in rec:
        lines.append(_fit("  %s %s %s  %s  %s" % (r["mark"] or " ", r["n"], r["title"], r["sha"], _hm(r["at"], now_iso)), width))
    lines.append("")
    lp = c.get("lamps") or {}
    _q = lambda v: "?" if v is None else v          # 三燈同一條規則:None 印 ?(與 lamps() 回的 dict 同源)
    # the maintainer 09-15 03:0x 底列改字(待真人裁 / 目標偏離 / 取不到值);右欄共用的 c_lamps 字面暫不動(a seat 預設丙,右欄 27 格放不下新字)。
    # 目標偏離:第 66 項未啟用印「未啟用」,與「取不到值」的 ? 分開(drifted_enabled 是 False 才印,None 仍是 ?)。
    drifted = T("aib.taskzone.c_lamp_disabled") if lp.get("drifted_enabled") is False else _q(lp.get("drifted"))
    lamps = T("aib.taskzone.c_lamps_zone", {"n": _q(lp.get("human")), "count": drifted, "total": _q(lp.get("unchecked"))})
    proj = T("aib.taskzone.c_projection", {"at": _hm(now_iso, now_iso)})
    gap = max(1, width - _cells(lamps) - _cells(proj))
    lines.append(_fit(lamps + " " * gap + proj, width))
    return "\n".join(lines)


def render_detail(out, width=100):
    now_iso = out["generated_at"]
    lines = []
    labels = (T("aib.taskzone.l1"), T("aib.taskzone.l2"), T("aib.taskzone.l3"))
    for lab, c in zip(labels, out["three"]):
        lines.append("▌%s %s%s" % (lab, c["value"], _src(c, now_iso)))

    # 現在在做
    b = out["blocks"]["doing"]
    rows = []
    if not b["ok"]:
        rows.append(T("aib.taskzone.unmeasured", {"why": b["why"]}))
    n = len(b["rows"])
    has_blocked = any(c["key"] == "freeze" for c in out["blocks"]["forbid"])
    for i, r in enumerate(b["rows"]):
        branch = "└" if (i == n - 1 and not has_blocked) else "├"
        extra = r.get("evidence") or r.get("decision") or ""
        rows.append(" %s %s %s %s  %s  %s" % (branch, r["mark"] or " ", r["n"] or "-", strip_md(r["title"])[:40],
                                              (r.get("status_plain") or r["status"])[:48], extra))
    for c in out["blocks"]["forbid"]:
        if c["key"] == "freeze":
            rows.append(" " + T("aib.taskzone.blocked_row", {"what": c["value"]}) + _src(c, now_iso))
    lines += _box(T("aib.taskzone.b_doing"), rows, width)

    # 等誰・誰等我
    rows = []
    for k in ("order", "outbound", "inbound", "hold", "human", "handoff", "machine"):
        c = out["blocks"]["waiting"][k]
        rows.append(c["value"] + _src(c, now_iso))
    lines += _box(T("aib.taskzone.b_waiting"), rows, width)

    # 最近完成 3 件
    b = out["blocks"]["recent"]
    rows = []
    if not b["ok"]:
        rows.append(T("aib.taskzone.unmeasured", {"why": b["why"]}))
    else:
        rows.append(b["scope"])
        for r in b["rows"]:
            verdict = (T("aib.taskzone.on_main") if r["on_main"] else
                       (T("aib.taskzone.not_on_main") if r["on_main"] is False else
                        T("aib.taskzone.unmeasured", {"why": r["why"] or "?"})))
            rows.append("%s %s  %s" % (r["sha"], verdict, _hms(r["mtime"], now_iso)))   # 規則 3:不印收據檔名
    lines += _box(T("aib.taskzone.b_recent"), rows, width)

    # 不准做
    rows = [c["value"] + _src(c, now_iso) for c in out["blocks"]["forbid"]]
    lines += _box(T("aib.taskzone.b_forbid"), rows, width)

    f = out.get("footer") or {}
    lines.append("╰ " + T("aib.taskzone.footer", {
        "at": _hms(now_iso, now_iso), "sec": f.get("sec") or "?",
        "shift": ("p" + f["handoff_shift"]) if f.get("handoff_shift") else "-",
        "sandbox": f.get("sandbox_note") or ""}))
    if out["reds"]:
        lines.append(T("aib.taskzone.reds_head", {"n": len(out["reds"])}))
        for r in out["reds"]:
            lines.append("  rc=%s %s #%s %s" % (r["rc"], r["rule"], r.get("item"), r.get("detail") or r.get("id") or ""))
    if out["missing_sources"]:
        lines.append(T("aib.taskzone.missing_head", {"n": len(out["missing_sources"])}))
        for m in out["missing_sources"]:
            lines.append("  " + m)
    return "\n".join(lines)


# ── 發版誠實節(第 43 項):不准做 + 被擋/逃生 + 等人裁 + 盲區 → tag 訊息「已知缺口」節 ───────────
ESCAPE_ROW_RE = re.compile(r"^\|\s*v?([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*\|\s*$")


def escape_rows_for(escape_log, version):
    """docs/reports/RELEASE-ESCAPES.md 裡本版的逃生列:[(閘 rc, 理由)]。檔不在回 None(量不到,不是「沒逃生」)。"""
    p = pathlib.Path(escape_log)
    if not p.exists():
        return None
    want = version.lstrip("v")
    out = []
    try:
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ESCAPE_ROW_RE.match(ln.strip())
            if not m or m.group(1) in ("版本", "---"):
                continue
            if m.group(1).lstrip("v") == want:
                out.append((m.group(2), m.group(3)))
    except OSError:
        return None
    return out


def suite_gaps(suite_results):
    """全套彙總 JSON 的缺口數:(母體, 有讀數, 通過, 紅, 跳過, 被閘住)。讀不到回 None。"""
    p = pathlib.Path(suite_results) if suite_results else None
    if not p or not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or "population" not in d:
        return None
    return {"population": d.get("population"), "measured": d.get("measured"), "passed": d.get("passed"),
            "failed": d.get("failed"), "skipped": d.get("skipped_total"), "gated": d.get("gated")}


def blindspots_42(repo, timeout=120):
    """第 42 項 tools/governance/coverage-blindspots.py 的讀數:(state, dict)。
    state ∈ ok(dict 有 blind/unreadable)/ failed(dict 有 rc/why)/ absent(工具不在這棵樹)。
    ★ 不自己算盲區:那把尺的 0 是它的,鏡子過期它自己會說(rc=2),這裡照抄不重量。"""
    tool = pathlib.Path(repo) / "tools" / "governance" / "coverage-blindspots.py"
    if not tool.exists():
        return "absent", {"path": str(tool)}
    try:
        r = subprocess.run([sys.executable, "-B", str(tool), "--json"], cwd=str(repo), capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "failed", {"rc": "?", "why": str(e)[:120]}
    if r.returncode != 0:
        return "failed", {"rc": r.returncode, "why": (r.stderr.strip().splitlines() or ["?"])[0][:160]}
    try:
        d = json.loads(r.stdout)
        return "ok", {"blind": list(d.get("blind") or []), "unreadable": list(d.get("unreadable") or []),
                      "tests_in_scope": d.get("tests_in_scope"), "file": tool.name}
    except ValueError as e:
        return "failed", {"rc": r.returncode, "why": "JSON: %s" % e}


def release_gaps(version, suite_results, escape_log, outline, decisions, now=None, repo=None):
    """給 tools/aib-release.sh 的 tag 訊息用:四行「已知缺口」,每行帶來源與量測時刻;算不出印「量不到:為什麼」。
    **永不 raise、永遠回四行以上的文字**:誠實節的產生器自己壞了,不准讓發版打不了 tag(閘不准癱瘓它守的東西);
    那種情況回一行「量不到:產生器 …」,tag 上仍看得見「這一段沒算出來」。"""
    now = time.time() if now is None else float(now)
    lines = [T("aib.taskzone.gaps_head", {"version": version, "at": stamp(now)})]
    try:
        # 被擋/逃生
        rows = escape_rows_for(escape_log, version)
        if rows is None:
            lines.append(T("aib.taskzone.gaps_escape_unmeasured", {"path": str(escape_log)}))
        elif not rows:
            lines.append(T("aib.taskzone.gaps_escape_none", {"version": version, "file": pathlib.Path(escape_log).name}))
        else:
            for rc, reason in rows:
                lines.append(T("aib.taskzone.gaps_escape_row", {"rc": rc, "reason": reason[:160], "file": pathlib.Path(escape_log).name}))
        # 等人裁
        items, why = load_decisions(decisions)
        if items is None:
            lines.append(T("aib.taskzone.gaps_human_unmeasured", {"why": why}))
        else:
            open_dec = [i for i in items if not i.get("resolved_at")]
            if not open_dec:
                lines.append(T("aib.taskzone.gaps_human_none"))
            else:
                oldest = min(open_dec, key=lambda i: float(i.get("opened_epoch") or now))
                lines.append(T("aib.taskzone.gaps_human", {
                    "n": len(open_dec), "id": oldest.get("id"),
                    "waited": fmt_wait(now - float(oldest.get("opened_epoch") or now)),
                    "ids": ", ".join(str(i.get("id")) for i in open_dec)}))
        # 不准做(大綱標 ⛔)
        op = pathlib.Path(outline)
        if not op.exists():
            lines.append(T("aib.taskzone.gaps_notdoing_unmeasured", {"path": str(op)}))
        else:
            marks = load_marks(None)
            its = parse_items(op.read_text(encoding="utf-8", errors="replace"), marks)
            nd = [it for it in its.values() if it["mark"] == marks["not_doing"]]
            if not nd:
                lines.append(T("aib.taskzone.gaps_notdoing_none"))
            else:
                lines.append(T("aib.taskzone.gaps_notdoing", {
                    "n": len(nd), "items": " ; ".join("%s %s" % (it["n"], it["title"][:40]) for it in nd)}))
        # 盲區:接第 42 項的尺(a seat REVIEW-41-43 §4-2:不寫死「還沒做」——42 已在 main 上線)
        st, info = blindspots_42(repo or REPO)
        if st == "ok":
            blind = T("aib.taskzone.gaps_blind_ok", {"n": len(info["blind"]), "count": len(info["unreadable"]), "file": info["file"]})
        elif st == "failed":
            blind = T("aib.taskzone.gaps_blind_failed", {"rc": info["rc"], "why": info["why"]})
        else:
            blind = T("aib.taskzone.gaps_blind_not_wired", {"path": info["path"]})
        sg = suite_gaps(suite_results)
        if sg is None:
            suite_txt = T("aib.taskzone.gaps_suite_unmeasured", {"path": str(suite_results or "-")})
        else:
            unm = (sg["population"] or 0) - (sg["measured"] or 0)
            suite_txt = T("aib.taskzone.gaps_suite", {
                "n": unm, "total": sg["population"], "count": sg["failed"], "items": sg["skipped"], "ids": sg["gated"],
                "file": pathlib.Path(suite_results).name})
        lines.append(T("aib.taskzone.gaps_blind_line", {"text": blind, "items": suite_txt}))
    except BaseException as e:  # noqa: BLE001 —— 見 docstring
        if isinstance(e, KeyboardInterrupt):
            raise
        lines.append(T("aib.taskzone.gaps_failed", {"err": "%s: %s" % (type(e).__name__, e)}))
    return lines


def default_home():
    return pathlib.Path(os.environ.get("EZ2_AIB_HOME") or (pathlib.Path.home() / ".ez2-aib"))


_LOCK_DEFAULT = {}


def _lock_default(repo):
    """<git common dir>/aib-release.lock。★ 第 51 項第二筆成本:這個 `git rev-parse` 每幀 25 ms,而 common dir
    在程式生命期內不會變 ⇒ 每個 repo 只問一次(問不到也記住,不每幀重問)。"""
    if repo in _LOCK_DEFAULT:
        return _LOCK_DEFAULT[repo]
    _LOCK_DEFAULT[repo] = _lock_default_uncached(repo)
    return _LOCK_DEFAULT[repo]


def _lock_default_uncached(repo):
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        cd = common.stdout.strip()
        if common.returncode == 0 and cd:
            return str((pathlib.Path(repo) / cd if not os.path.isabs(cd) else pathlib.Path(cd)) / "aib-release.lock")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def resolve_paths(**over):
    """四正本與其餘路徑的**唯一**預設解法:CLI 與 render_lines() 都走這裡,不各存一份。
    over 裡給了(非 None)的就用給的;沒給的依 EZ2_AIB_HOME(或 ~/.ez2-aib)與本樹算。"""
    home = default_home()
    a = argparse.Namespace()
    a.outline = over.get("outline") or str(REPO / "docs" / "EZ2-AIB-DEV-OUTLINE.md")
    a.decisions = over.get("decisions") or str(home / "state" / "pending-human-decisions.json")
    a.handoffs = over.get("handoffs") or str(home / "pending")
    a.evidence = over.get("evidence") or str(home / "evidence" / "b8-main-push")
    a.live_dir = over.get("live_dir") or str(home / "pending")
    a.repo = over.get("repo") or str(REPO)
    a.main_ref = over.get("main_ref") or "origin/main"
    a.status_enum = over.get("status_enum")
    a.strict = bool(over.get("strict"))
    a.lock = over.get("lock") if over.get("lock") is not None else _lock_default(a.repo)
    a.ps_file = over.get("ps_file")            # None = 現跑 ps;給了但檔不在 = ps 量不到
    a.handoff_parser = (over.get("handoff_parser")
                        or str(pathlib.Path(__file__).resolve().parent / "handoff_fields.py"))
    return a


# ── 可 import 的接口(第 39 項頁簽用;艦長 09-12 03:1x 令)+ 第 51 項節流第一版 ─────────────
# ★ 節流的鑰匙是**四正本的 mtime**(大綱檔、待決簿檔、交接單目錄與最新一份、收據目錄、log 目錄與最新
#   RELEASE/LIVE log、鎖檔),不是時間:正本沒變就回同一份投影(頁尾「投影截至」也不變 —— 它是算出來
#   的時刻,不是畫的時刻)。再加一道最長壽命 RENDER_MAX_AGE_S,讓「正本沒變但機器狀態變了」的格
#   (鎖檔 pid 死活、ps 誰佔機器)最多陳舊這麼久 —— ps 刻意不進鑰匙,它每幀都變。
RENDER_MAX_AGE_S = 30
RENDER_MIN_WIDTH = 40   # 再窄就裁到「來源 時刻」,鐵律失效;窄於此回一列具名,不排版
_RENDER_CACHE = {}


def _mtime_or_none(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _newest_by_prefix(dirpath, prefixes_contains):
    """一次 scandir 同時取多組「最新 mtime」。★ 第 51 項第三筆成本:pending/ 有一萬多個檔,一次 glob 7 ms;
    鑰匙原本 glob 三次(交接單/RELEASE/LIVE)= 節流命中時每幀 33 ms,改成掃一次。
    prefixes_contains = [(前綴, 必含子字串或 None, 必含尾綴或 None), ...];回 [最新 mtime_ns 或 None, ...]。"""
    best = [None] * len(prefixes_contains)
    try:
        with os.scandir(dirpath) as it:
            for e in it:
                name = e.name
                for i, (pre, sub, suf) in enumerate(prefixes_contains):
                    if not name.startswith(pre):
                        continue
                    if sub and sub not in name:
                        continue
                    if suf and not name.endswith(suf):
                        continue
                    try:
                        m = e.stat().st_mtime_ns
                    except OSError:
                        continue
                    if best[i] is None or m > best[i]:
                        best[i] = m
    except OSError:
        return [None] * len(prefixes_contains)
    return best


def source_signature(a, seat):
    """四正本現在的樣子,壓成一個可比較的 tuple。任何一格 None 都代表「那一格沒有東西」(是狀態,不是量不到)。"""
    specs = [("HANDOFF-", seat, ".md"), ("RELEASE-", None, ".log"), ("LIVE", "-PIPELINE-", ".log")]
    if os.path.realpath(a.live_dir) == os.path.realpath(a.handoffs):
        newest_handoff, rel, live = _newest_by_prefix(a.handoffs, specs)          # 同一個目錄:掃一次
    else:
        newest_handoff = _newest_by_prefix(a.handoffs, specs[:1])[0]
        rel, live = _newest_by_prefix(a.live_dir, specs[1:])
    return (
        _mtime_or_none(a.outline),
        _mtime_or_none(a.decisions),
        _mtime_or_none(a.handoffs), newest_handoff,
        _mtime_or_none(a.evidence),
        _mtime_or_none(a.live_dir), rel, live,
        _mtime_or_none(a.lock) if a.lock else None,
    )


def _clip(line, width):
    """裁到 width 顯示欄(CJK 2 格),不折行:頁簽由第 39 項決定寬度,超寬的字面只截不換行。
    ★ 走 render_lib.trunc:跳脫碼 0 格、原樣保留,截斷後補 RESET(進度條上色後自己逐字切會切進色碼中間)。"""
    return render_lib.trunc(line, width)


def render_lines(seat, width, max_lines, now=None, mode="compact", color=None, **paths):
    """給第 39 項頁簽用的接口。回文字列(已含框線、已裁到 width),最多 max_lines 列;
    超出時最後一列印「還有 N 列未顯示」。**永不 raise**:席碼不對、正本讀不到、內部炸掉都回一列文字
    (閘自己壞掉不准癱瘓它守的頁簽)。paths 是治具用的路徑覆蓋(outline/decisions/handoffs/evidence/
    live_dir/repo/main_ref/lock/status_enum),產品呼叫不給。節流:四正本 mtime 沒變且未超過
    RENDER_MAX_AGE_S 秒 ⇒ 回上一次的投影(同一份列、同一個截至時刻)。
    color(the maintainer 09-15 02:5x):None / "256" / "truecolor",由呼叫端判(NO_COLOR、非終端傳 None);
    只有 compact 進度條上色,色碼全部來自 render_lib。color 進節流鑰匙:有色與無色不得互拿快取。"""
    color = color if color in render_lib.PROGRESS_COLOR_MODES else None
    now = time.time() if now is None else float(now)
    width = int(width)
    max_lines = max(1, int(max_lines))
    if width < RENDER_MIN_WIDTH:
        # ★ a seat(REVIEW-39-INJECT 第 1 點):窄框不排版。原本地板到 40 再裁列,會把列尾的「來源 時刻」裁掉 ——
        #   §89.7「每格帶來源與量測時刻」先失效而沒有任何格紅。改成回一列具名,頁簽看得出是框太窄,不是沒任務。
        return [T("aib.taskzone.too_narrow", {"n": width, "total": RENDER_MIN_WIDTH})]
    if not SEAT_RE.fullmatch(seat or ""):
        return [_clip(T("aib.taskzone.err_seat_shape", {"seat": seat}), width)]
    try:
        a = resolve_paths(**paths)
        sig = source_signature(a, seat)
        # max_lines 進鑰匙:「正在做」大綱依高度刪列,不同高度排出來的列不同,不得互拿快取。
        # ★ 佇列(人類交辦)不在 source_signature 裡:新交辦最多晚 RENDER_MAX_AGE_S 秒才出現在「正在做」。
        key = (seat, width, mode, color, max_lines, tuple(sorted((k, str(v)) for k, v in paths.items())))
        hit = _RENDER_CACHE.get(key)
        # ★ 鑰匙裡的 None(沒有鎖檔、沒有交接單)是**一個狀態**,不是量不到:它出現時鑰匙就會從 None 變成 mtime,
        #   所以拿它比對是安全的;把 None 當「一律重算」會讓沒有發版在跑的平常時段永遠不節流(臂抓到的)。
        if hit and hit["sig"] == sig and (now - hit["at"]) < RENDER_MAX_AGE_S:
            lines = hit["lines"]
        else:
            a.seat = seat
            out = build(a, dict(os.environ), now)
            lines = [_clip(l, width) for l in render_text(out, width=width, mode=mode, color=color,
                                                          max_lines=max_lines).splitlines()]
            _RENDER_CACHE[key] = {"sig": sig, "at": now, "lines": lines}
    except BaseException as e:  # noqa: BLE001 —— 含 SystemExit(符正本讀不到):回一列,不 raise
        if isinstance(e, KeyboardInterrupt):
            raise
        return [_clip(T("aib.taskzone.render_failed", {"err": "%s: %s" % (type(e).__name__, e)}), width)]
    shadow = shadow_explain_line(seat, now)
    if shadow:
        lines = [_clip(shadow, width)] + list(lines)
    if len(lines) > max_lines:
        hidden = len(lines) - (max_lines - 1)
        lines = lines[:max_lines - 1] + [_clip(T("aib.taskzone.more_lines", {"n": hidden}), width)]
    return list(lines)


SHADOW_LINE_MAX_AGE_S = 3600


def shadow_explain_line(seat, now=None):
    """10-13 the maintainer 20:0x「所有家直接印、寫進右欄一格」:這一席一小時內最近一次的影子續跑說明 ⇒ 任務區最上面一列
    (<狀態根>/state/shadow-explain/<席>.json,tools/verify/shadow_explain.py 寫的)。沒有 / 讀不到 ⇒ ""。不進節流快取。"""
    try:
        root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
        with open(os.path.join(root, "state", "shadow-explain", "%s.json" % seat), encoding="utf-8") as fh:
            d = json.load(fh)
        at = float(d.get("at") or 0)
        now = time.time() if now is None else float(now)
        if now - at > SHADOW_LINE_MAX_AGE_S:
            return ""
        parts = [str(x) for x in (d.get("savings") or [])[1:5]]
        if not parts:
            return ""
        return T("aib.taskzone.shadow_line", {"when": time.strftime("%H:%M", time.localtime(at)),
                                              "what": " · ".join(parts)})
    except Exception:  # noqa: BLE001 —— 輔助資訊,讀壞了不得讓頁簽變錯誤列
        return ""


def lamps(seat, now=None, **paths):
    """第 49 項右欄三燈讀的數字(開發 A a seat 09-12 13:45/14:04;艦長 13:50 裁):{"human","drifted","unchecked","measured_at"},
    每鍵整數或 None;None = 量不到,不是 0;measured_at = 投影那一刻的 epoch 秒(節流命中時是上一次投影的時刻,49 靠它說「幾分鐘前的數字」)。**永不 raise**:席碼形狀不對、正本讀不到、內部炸掉都回三個 None。
    ★ 單一來源:拿的是 build() 算好的 out["compact"]["lamps"],與 compact 底列印的那句同一個 dict,不另算。
    節流:與 render_lines 同一把鑰匙形狀(seat + paths;寬度與 mode 無關),四正本沒變且未超過 RENDER_MAX_AGE_S 秒就回上一次。"""
    # drifted_enabled(09-15):False = 第 66 項未啟用、True = 有在量、None = 連這件都不知道。右欄照舊只取前四鍵(a seat 預設丙)。
    none = {"human": None, "drifted": None, "unchecked": None, "measured_at": None, "drifted_enabled": None}
    now = time.time() if now is None else float(now)
    if not SEAT_RE.fullmatch(seat or ""):
        return dict(none)
    try:
        a = resolve_paths(**paths)
        sig = source_signature(a, seat)
        key = ("lamps", seat, tuple(sorted((k, str(v)) for k, v in paths.items())))
        hit = _RENDER_CACHE.get(key)
        if hit and hit["sig"] == sig and (now - hit["at"]) < RENDER_MAX_AGE_S:
            return dict(hit["lamps"])
        a.seat = seat
        out = build(a, dict(os.environ), now)
        lp = (out.get("compact") or {}).get("lamps") or {}
        res = {k: lp.get(k) for k in none}
        _RENDER_CACHE[key] = {"sig": sig, "at": now, "lamps": res}
        return dict(res)
    except BaseException as e:  # noqa: BLE001 —— 同 render_lines:含 SystemExit,回三個 None 不 raise
        if isinstance(e, KeyboardInterrupt):
            raise
        return dict(none)


def main(argv=None):
    global LOC
    ap = argparse.ArgumentParser(description=T("aib.taskzone.cli_desc"))
    ap.add_argument("--seat", default=None, help=T("aib.taskzone.cli_seat"))
    ap.add_argument("--release-gaps", action="store_true", help=T("aib.taskzone.cli_release_gaps"))
    ap.add_argument("--version", default=None)
    ap.add_argument("--suite-results", default=None)
    ap.add_argument("--escape-log", default=None)
    ap.add_argument("--outline", default=None)
    ap.add_argument("--decisions", default=None, help=T("aib.taskzone.cli_decisions"))
    ap.add_argument("--handoffs", default=None, help=T("aib.taskzone.cli_handoffs"))
    ap.add_argument("--evidence", default=None, help=T("aib.taskzone.cli_evidence"))
    ap.add_argument("--live-dir", default=None, help=T("aib.taskzone.cli_live"))
    ap.add_argument("--repo", default=None)
    ap.add_argument("--main-ref", default=None)
    ap.add_argument("--lock", default=None, help=T("aib.taskzone.cli_lock"))
    ap.add_argument("--status-enum", default=None)
    ap.add_argument("--now", type=float, default=None, help=T("aib.taskzone.cli_now"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--width", type=int, default=100, help=T("aib.taskzone.cli_width"))
    ap.add_argument("--mode", choices=("compact", "detail"), default="compact", help=T("aib.taskzone.cli_mode"))
    # 進度條色(the maintainer 09-15 02:5x):預設 none = 零跳脫碼(非終端、pipe、尺的預設臂);要色由呼叫端明說
    ap.add_argument("--color", choices=("none", "256", "truecolor"), default="none")
    ap.add_argument("--max-lines", type=int, default=None, help=T("aib.taskzone.cli_max_lines"))
    ap.add_argument("--ps-file", default=None, help=T("aib.taskzone.cli_ps_file"))
    ap.add_argument("--handoff-parser", default=None, help=T("aib.taskzone.cli_handoff_parser"))
    ap.add_argument("--strict", action="store_true", help=T("aib.taskzone.cli_strict"))
    ap.add_argument("--locale", default=None)
    args = ap.parse_args(argv)

    if args.locale:
        os.environ["EZ2_AIB_LOCALE"] = args.locale
    LOC = i18n_lib.resolve_locale(args.locale)
    if args.release_gaps:
        # 發版誠實節:不看席,看版本。永遠 rc=0(見 release_gaps docstring);缺 --version 是用法錯。
        if not args.version:
            sys.stderr.write(T("aib.taskzone.err_need_version") + "\n")
            return RC_USAGE
        a = resolve_paths(outline=args.outline, decisions=args.decisions, repo=args.repo)
        esc = args.escape_log or str(pathlib.Path(a.repo) / "docs" / "reports" / "RELEASE-ESCAPES.md")
        print("\n".join(release_gaps(args.version, args.suite_results, esc, a.outline, a.decisions,
                                     now=args.now, repo=a.repo)))
        return RC_OK
    if not SEAT_RE.fullmatch(args.seat or ""):
        sys.stderr.write(T("aib.taskzone.err_seat_shape", {"seat": args.seat}) + "\n")
        return RC_USAGE
    a = resolve_paths(outline=args.outline, decisions=args.decisions, handoffs=args.handoffs,
                      evidence=args.evidence, live_dir=args.live_dir, repo=args.repo, main_ref=args.main_ref,
                      lock=args.lock, status_enum=args.status_enum, strict=args.strict,
                      ps_file=args.ps_file, handoff_parser=args.handoff_parser)
    a.seat = args.seat
    now = args.now if args.now is not None else time.time()
    out = build(a, dict(os.environ), now)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    elif args.max_lines:
        # 與 render_lines 同一條路(頁簽看到的就是這個),不另走一份
        print("\n".join(render_lines(args.seat, args.width, args.max_lines, now=now, mode=args.mode,
                                     color=(None if args.color == "none" else args.color),
                                     outline=a.outline, decisions=a.decisions, handoffs=a.handoffs,
                                     evidence=a.evidence, live_dir=a.live_dir, repo=a.repo,
                                     main_ref=a.main_ref, lock=a.lock, status_enum=a.status_enum,
                                     ps_file=a.ps_file, handoff_parser=a.handoff_parser)))
    else:
        print(render_text(out, width=max(40, args.width), mode=args.mode,
                          color=(None if args.color == "none" else args.color)))
    return out["rc"]


if __name__ == "__main__":
    sys.exit(main())
