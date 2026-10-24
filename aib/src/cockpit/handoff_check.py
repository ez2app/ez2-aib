#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接改嚴 + aib handoff verify(docs/DESIGN-memory-tidy-1013.md §8e;the maintainer 10-13 17:5x,a seat 17:55 審)。

the maintainer:「交接也要更新人格和專案卡沒錯,而且以下會過於馬虎」「一定要確定上面全部完成之後,請告訴我如何確認上面這些一定完成的判斷」。

- 三段改固定欄位(FIELDS);collect 先把機器能填的(工作樹、沒合分支、背景程式 pid、待審未讀 id)填進欄位,AI 只補判斷。
- 機器對帳(reconcile):本席背景程式 pid、本席工作樹 / 分支、待審未讀 id 都要在第二段交代;文中的 sha 要真的存在;
  寫到測試要寫 Ran N 綠 / 紅(哪幾支)/ 沒跑(原因)。
- a seat:缺欄位 / 對帳不齊**不硬擋**,收據標「不完整」+ 缺哪幾項,下一輪開頭照實顯示。
- verify:只讀,八項逐項 ok / 缺;全 ok ⇒ rc 0「全部完成」。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "memory", HERE.parent / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

FIELDS = {"s1": ("goal", "missed", "correction", "advice"),
          "s2": ("inflight", "waiting", "background", "temporary", "pitfalls"),
          "s3": ("summary", "paste", "next3", "todo")}
SHA_RE = re.compile(r"(?<![0-9A-Za-z_/.-])([0-9a-f]{7,40})(?![0-9A-Za-z_]|-[0-9a-f])")   # uuid 第一段不算(審查 7)
TEST_WORD_RE = re.compile(r"\btests?\b|測試", re.I)     # latest / pytest / 路徑裡的 test 不算(審查 6)
MACHINE = "[機器]"                                     # collect 預填的行;不算 AI 寫的字、不算對帳(審查 2、8)
TEST_OK_RE = re.compile(r"Ran\s*\d+|綠|紅|沒跑|green|red|not run|未跑", re.I)


def T(key, **kw):
    import aib_handoff
    return aib_handoff.T(key, **kw)


def field_title(sec, name):
    return T("aib.handoff.field.%s.%s" % (sec, name))


_ALL_TITLES = {}


def all_titles():
    """{各語系欄位標題: (段, 欄)} —— collect 與 write 語系不同也認得(審查 12)。"""
    if _ALL_TITLES:
        return _ALL_TITLES
    try:
        from i18n_lib import CANONICAL_LOCALES, load_dict
        for loc in CANONICAL_LOCALES:
            d = load_dict(loc)
            for sec, names in FIELDS.items():
                for n in names:
                    t = d.get("aib.handoff.field.%s.%s" % (sec, n))
                    if isinstance(t, str):
                        _ALL_TITLES[t.strip()] = (sec, n)
    except Exception:  # noqa: BLE001
        pass
    for sec, names in FIELDS.items():
        for n in names:
            _ALL_TITLES.setdefault(field_title(sec, n).strip(), (sec, n))
    return _ALL_TITLES


def _heading(ln):
    m = re.match(r"^#{2,4}\s*(.+?)\s*$", ln)
    return all_titles().get(m.group(1)) if m else None


def drop_machine(body):
    """拿掉 collect 預填的 [機器] 行(欄位標題留著)。"""
    return "\n".join(ln for ln in (body or "").splitlines() if not ln.strip().startswith("- " + MACHINE))


def strip_template(body):
    """拿掉欄位標題與 [機器] 行 ⇒ 只剩 AI 寫的字(舊的太短 / 湊字數 / 相似度 / 提示字閘照舊算這個;審查 2)。"""
    return "\n".join(ln for ln in drop_machine(body).splitlines() if not _heading(ln))


def field_keys():
    """給 aib_handoff.prompt_pieces:欄位標題也是提示字,不算 AI 自己寫的字。"""
    return ["aib.handoff.field.%s.%s" % (s, n) for s, names in FIELDS.items() for n in names]


# ---------------------------------------------------------------- 草稿欄位(collect 預填)
def prefill(sec, name, facts):
    """機器能填的先填(a seat:AI 只補判斷,不要重打一遍)。"""
    code = ((facts.get("seat") or {}).get("code") or "")
    out = []
    if (sec, name) == ("s2", "inflight"):
        for w in (facts.get("worktrees") or {}).get("shown") or []:
            if w.get("mine"):
                out.append("- %s %s [%s] %s" % (MACHINE, w.get("path"), w.get("branch") or "detached",
                                                str(w.get("head") or "")[:12]))
        for b in (facts.get("unmerged") or {}).get("shown") or []:
            if code and code in b.get("branch", ""):
                out.append("- %s 分支 %s" % (MACHINE, b["branch"]))
    elif (sec, name) == ("s2", "waiting"):
        for u in facts.get("unread") or []:
            out.append("- %s dm %s(%s)%s" % (MACHINE, u.get("id"), u.get("from") or "?", (u.get("summary") or "")[:60]))
    elif (sec, name) == ("s2", "background"):
        for b in facts.get("background") or []:
            out.append("- %s pid %s %s" % (MACHINE, b.get("pid"), (b.get("cmd") or "")[:100]))
    return out


def section_template(sec, facts):
    lines = []
    for name in FIELDS[sec]:
        lines += ["### %s" % field_title(sec, name)] + prefill(sec, name, facts) + [""]
    return lines


def split_fields(sec, body):
    """一段 ⇒ {欄位: 內容};標題對不上的段落歸到前一個欄位。"""
    got, cur = {}, None
    for ln in drop_machine(body).splitlines():
        h = _heading(ln)
        if h and h[0] == sec:
            cur = h[1]
            got.setdefault(cur, [])
            continue
        if cur:
            got[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in got.items()}


def missing_fields(secs):
    """→ ["一段/欄位", ...]:標題不見、或底下沒字。"""
    out = []
    for sec, names in FIELDS.items():
        got = split_fields(sec, secs.get(sec, ""))
        for n in names:
            if not got.get(n):
                out.append("%s/%s" % (T("aib.handoff.section.%s" % sec), field_title(sec, n)))
    return out


# ---------------------------------------------------------------- 機器對帳
def _sha_exists(repo, sha):
    try:
        r = subprocess.run(["git", "-C", repo, "cat-file", "-e", sha + "^{commit}"], capture_output=True,
                           timeout=5, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True                                   # 量不到不當缺(不誣賴)


def reconcile(facts, secs, repo="", sha_exists=None, warnings=None):
    """→ 缺的項目清單(空 = 對帳全過)。只算本席(a seat:pid 樹 / 本席工作樹)。
    只看 AI 自己寫的字([機器] 預填行不算:審查 8 —— 不然留著預填就過了)。sha 找不到只進 warnings(可能是別的倉,審查 7)。"""
    secs = {k: drop_machine(v) for k, v in secs.items()}
    s2 = secs.get("s2", "")
    alltext = "\n".join(secs.get(k, "") for k in ("s1", "s2", "s3"))
    code = ((facts.get("seat") or {}).get("code") or "")
    out = []
    for b in facts.get("background") or []:
        if str(b.get("pid")) not in s2:
            out.append(T("aib.handoff.check.bg", pid=str(b.get("pid")), cmd=(b.get("cmd") or "")[:40]))
    for w in (facts.get("worktrees") or {}).get("shown") or []:
        if w.get("mine") and os.path.basename(str(w.get("path") or "")) not in s2:
            out.append(T("aib.handoff.check.worktree", path=str(w.get("path"))))
    for b in (facts.get("unmerged") or {}).get("shown") or []:
        if code and code in b.get("branch", "") and b["branch"] not in s2:
            out.append(T("aib.handoff.check.branch", branch=b["branch"]))
    for u in facts.get("unread") or []:
        if str(u.get("id")) not in s2:
            out.append(T("aib.handoff.check.unread", id=str(u.get("id"))))
    repo = repo or ((facts.get("repo") or {}).get("path") or "")
    if repo:
        seen = set()
        for m in SHA_RE.finditer(alltext):
            sha = m.group(1)
            if sha in seen or not re.search(r"[a-f]", sha):
                continue
            seen.add(sha)
            if not (sha_exists or _sha_exists)(repo, sha) and warnings is not None:
                warnings.append(T("aib.handoff.check.sha", sha=sha))
    inflight = split_fields("s2", s2).get("inflight", "")
    for ln in inflight.splitlines():
        if TEST_WORD_RE.search(ln) and not TEST_OK_RE.search(ln):
            out.append(T("aib.handoff.check.test", line=ln.strip()[:60]))
    return out


# ---------------------------------------------------------------- verify(只讀、八項)
def _ts(s):
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0


def verify(code, home=None, review=True):
    """→ [(項目, ok, 說明)]。看檔案與收據,不看 AI 自己說。"""
    import aib_handoff
    start = aib_handoff.current_round_start(code, home)
    items = []

    def add(key, ok, detail=""):
        items.append((T("aib.handoff.verify.item.%s" % key), bool(ok), detail))

    try:                                                # 審查 1:用交接一輪的起點,不用 aib_summary 的(它被剛寫的收據推到「現在」)
        import memory_tidy
        import aib_summary
        tidy = memory_tidy.last_receipt(code) or {}
        tidy = tidy if _ts(tidy.get("at")) >= start else {}
        summ = aib_handoff._read_json(aib_summary.summary_dir() / ("%s.json" % code)) or {}
        summ = summ if _ts(summ.get("at") or summ.get("ts")) >= start else {}
    except Exception as exc:  # noqa: BLE001
        tidy, summ = {}, {}
        add("tidy", False, repr(exc)[:120])
    files = (tidy or {}).get("files") or {}
    if tidy:
        bad = [k for k, v in files.items() if (v or {}).get("status") == "failed"]
        add("tidy", not bad, "failed: %s" % ",".join(bad) if bad else tidy.get("at", ""))
    elif not items:
        add("tidy", False, T("aib.handoff.verify.none"))
    per = files.get("persona") or {}
    try:
        import memory_tidy
        rows = [json.loads(x) for x in (memory_tidy.tidy_dir() / "writes.jsonl").read_text(
            encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError, ImportError):
        rows = []
    puid = (tidy or {}).get("persona_uid") or ""
    wrote = any(r.get("seat") == code and _ts(r.get("at")) >= start and (not puid or r.get("uid") == puid)
                for r in rows)                                                       # 本輪本席真的寫過人格卡
    wrote_own = bool(start) and bool(puid) and any(                                  # 本席那張(主人格卡那筆不算;r2 A-M1)
        r.get("seat") == code and _ts(r.get("at")) >= start and r.get("uid") == puid for r in rows)
    if per.get("status") == "ok":
        add("persona", wrote, "writes.jsonl" if wrote else T("aib.handoff.verify.no_write"))
    elif wrote_own and per.get("status") == "skipped":                               # 起點 0 / 舊收據沒卡號 ⇒ 不放行(r2 A-S1)
        # 本輪稍早已寫進人格卡,之後回顧的 --add-only 沒東西可補(或那幾維已滿)蓋了收據 ⇒ 照算寫過(#312 審查 A2)
        add("persona", True, "writes.jsonl(%s)" % (per.get("why") or "skipped"))
    else:
        # 10-24 維護者:「必要不能跳過，一定要寫回對應的人格」⇒ skipped 只在「讀得到原話、本輪真人一句都沒講」時算過;
        #   有原話卻 skipped、或讀不到原話(舊收據 unavailable)一律紅。waiting(第一艦長卡大變動等真人按)照舊算有理由。
        try:
            import memory_tidy
            usable = memory_tidy.human_words_usable((tidy or {}).get("human_words") or "")
        except ImportError:
            usable = False
        quiet = usable and (tidy or {}).get("human_words_count") == 0
        ok = (per.get("status") == "waiting" and bool(per.get("why"))) or (per.get("status") == "skipped" and quiet)
        add("persona", ok, "%s %s" % (per.get("status") or "-", per.get("why") or "") if ok else
            "%s %s(%s)" % (per.get("status") or "-", per.get("why") or "", T("aib.handoff.verify.persona_must")))
    card = files.get("card") or {}
    if card.get("status") == "ok":
        uid = (tidy or {}).get("project_uid") or ""
        doc = {}
        try:
            import memory_tidy
            doc = json.loads(memory_tidy.card_path(uid).read_text(encoding="utf-8")) if uid else {}
        except (OSError, ValueError, ImportError):
            doc = {}
        seats = ((doc.get("layers") or {}).get("work_state") or {}).get("seats") or {}
        row = seats.get((tidy or {}).get("seat_id") or "") or seats.get(code) or {}
        add("card", _ts(row.get("at")) >= start, row.get("at") or T("aib.handoff.verify.no_row"))
    else:
        add("card", card.get("status") == "skipped" and bool(card.get("why")),
            "%s %s" % (card.get("status") or "-", card.get("why") or ""))
    try:
        mtime = aib_handoff.seat_memory_path(code, home).stat().st_mtime
    except OSError:
        mtime = 0
    cx = files.get("context") or {}                     # 審查 10:要整理那步真的寫過(交接自己追加不算)
    cx_ok = cx.get("status") == "ok" or (cx.get("status") == "skipped" and bool(cx.get("why")))
    add("context", cx_ok and mtime >= start, "%s %s" % (cx.get("status") or "-",
        time.strftime("%m-%d %H:%M", time.localtime(mtime)) if mtime else "-"))
    rec = aib_handoff._read_json(aib_handoff.receipt_path(code, home)) or {}
    this_round = bool(rec) and float(rec.get("ts") or 0) >= start
    brain_ok = this_round and (rec.get("brain") or {}).get("ok") and (files.get("note") or {}).get("status") in ("ok", "skipped")
    add("brain", brain_ok, (files.get("note") or {}).get("uid") or "")
    # 10-24 維護者:「總結與交接都不能跳過」⇒ 交接檔寫「沒做」不再算過;總結要本輪寫過、且第二大腦那則真的寫進去
    sbrain = (summ or {}).get("brain") or {}
    add("summary", bool(summ) and bool(sbrain.get("ok")),
        sbrain.get("uid") or ((summ or {}).get("file") and T("aib.handoff.verify.summary_no_brain")) or "-")
    steps_ok = this_round and all(str(n) in (rec.get("steps") or {}) for n in (1, 2, 3))
    sha_ok = False
    if this_round and rec.get("file"):
        try:
            import hashlib
            sha_ok = hashlib.sha256(Path(rec["file"]).read_bytes()).hexdigest() == rec.get("sha")
        except OSError:
            sha_ok = False
    inc = rec.get("incomplete") or [] if this_round else []
    add("handoff", steps_ok and sha_ok and not inc,
        "; ".join(inc[:5]) if inc else (rec.get("file") or T("aib.handoff.verify.none")))
    bd = aib_handoff.binding_state(code, home)
    add("binding", bd.get("problem") in ("ok", "bot") or bool(rec.get("binding_proposal")),
        bd.get("problem") or bd.get("unmeasured") or "")
    if review:                                          # §8e.7 第 9 項:遺漏回顧(只在前 8 項全過之後才算數)
        try:
            import handoff_review
            rv = handoff_review.done_this_round(code, start)
        except Exception:  # noqa: BLE001
            rv = {}
        # 10-24:回顧筆記沒寫進第二大腦、或 0 句但其實讀不到原話(不是真人沒講話)⇒ 紅
        note_ok = bool(rv) and bool(rv.get("note")) and not str(rv.get("note")).startswith("failed")
        quiet_ok = bool(rv) and (rv.get("lines") or memory_tidy_usable(rv.get("words_source")))
        add("review", note_ok and quiet_ok, "%s 句 %s %s" % (rv.get("lines"), rv.get("by_kind"), rv.get("note") or "")
            if rv else T("aib.handoff.verify.review_todo"))
    return items


def memory_tidy_usable(source):
    try:
        import memory_tidy
        return memory_tidy.human_words_usable(source or "")
    except ImportError:
        return False


def render(items):
    lines = []
    for name, ok, detail in items:
        lines.append("%s %s%s" % ("✓" if ok else "✗", name, ("  " + detail) if detail else ""))
    done = all(ok for _n, ok, _d in items)
    first8 = all(ok for _n, ok, _d in items[:8])
    lines.append(T("aib.handoff.verify.all_done") if done else
                 T("aib.handoff.verify.missing", n=str(sum(1 for _n, ok, _d in items if not ok))))
    if first8 and not done:                             # 前 8 項全過、只差回顧 ⇒ 接著做(the maintainer 17:5x)
        lines.append(T("aib.handoff.verify.review_next"))
    return "\n".join(lines), done
