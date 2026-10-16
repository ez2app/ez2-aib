#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""整理記憶(記憶整理第 3 包;docs/DESIGN-memory-tidy-1013.md §4.0 / §4.1 / §4.4)。

本席綁的專案卡「每席一列」、席位記憶精簡、第二大腦進度筆記(取代上一則)—— 機器量 → AI 改草稿 → 程式驗 → 寫。
第 4 包:本席綁的人格卡 —— 依本輪證據(the maintainer 原話)加 12 維規則、改身分欄;第一艦長卡遇大變動要 the maintainer 按。

  aib memory tidy collect [--seat 艙/席] [--out 檔]   量本席現況,產草稿(帶 nonce;機器事實段不准改)
  aib memory tidy apply --file 檔 [--seat 艙/席]      驗草稿 → 寫專案卡本席列 + 總覽、席位記憶、進度筆記、整理收據
  aib memory tidy status [--seat 艙/席]               印這一席最後一張整理收據

寫入:專案卡 鎖(<卡資料夾>/card.lock,和 aib newproj 同一把)→ 快照 → 寫 → 讀回;席位記憶 快照 → 寫(只有本席寫它,不上鎖)。
任何一個檔出錯(鎖、快照、磁碟)只記那個檔 failed,其餘照寫,收據一定寫。
快照拍不成 ⇒ 那個檔不寫(記 failed),其餘照寫。每筆記「哪個模型、哪一席、何時」;模型讀不到照實寫「讀不到」。
rc:0 寫了(收據 ok / partial);2 驗不過(什麼都沒寫,印哪一條);3 認不出席。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "cockpit", HERE.parent / "identity"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import memory_archive  # noqa: E402

ROW_MAX_LINES = 8
CONTEXT_MAX_LINES = 90
NOTE_MIN_CHARS = 20
PENDING_MAX = 10
FRESH_DAYS = 14          # 總覽只彙整 N 天內更新過的席列(換艙 / regroup 留下的舊列不再進總覽)
NOW_ROW_CHARS = 80
LOCK_WAIT_S = 10
SECTIONS = ("row", "context", "note", "persona", "fit", "facts")
OPTIONAL_SECTIONS = ("main",)          # 10-16 主人格(真人分身)那段:有登記、本席沒綁它、讀得到真人原話才出現
SEC_RE = re.compile(r"^## .*<!-- tidy:(row|context|note|persona|main|fit|facts) -->\s*$", re.M)
EVIDENCE_MIN = 8
HUMAN_MAX = 40
# 10-16 維護者裁:human_filter(src/persona-collect/)開源倉還沒有,等私倉匯出、不手搬;缺件時照實寫,不靜默回空
HUMAN_WORDS_UNAVAILABLE = "unavailable(缺 human_filter)"
CROWN_PERSONA = "71c97a"
PERSONA_LINE_RE = re.compile(r"^\s*-\s*(\S+?)\s*[::]\s*(.+?)\s*〔證據[::]「(.+?)」〕\s*$")
IDENT_LINE_RE = re.compile(r"^\s*身分\s*[::]\s*(性別|生日|嗜好)\s*=\s*(.+?)\s*〔證據[::]「(.+?)」〕\s*$")
HEAD_RE = re.compile(r"<!-- tidy:draft seat=([0-9a-z]{5}) nonce=([0-9a-f]{16}) -->")
ROW_FIELDS = (("現在", "now"), ("下一步", "next_step"), ("待裁", "pending"))


class TidyError(Exception):
    pass


def state_root():
    return memory_archive.state_root()


def tidy_dir():
    return state_root() / "state" / "tidy"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _when(text):
    """ISO 時刻字串 → 有時區的 datetime(比較用,不靠字串比);讀不懂 ⇒ 最舊。"""
    try:
        d = datetime.datetime.fromisoformat(str(text or ""))
        return d if d.tzinfo else d.astimezone()
    except ValueError:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p, doc):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 認席
def seat_info(seat=""):
    import aib_memory
    ctx = aib_memory.seat_context(seat)
    if not ctx.get("seat_uid"):
        raise TidyError("認不出是哪一席;在席裡跑,或加 --seat <艙位碼>/<席碼>")
    try:
        import seat_address
        ctx["code"] = seat_address.uuid_tail5(ctx["seat_uid"])
    except Exception:  # noqa: BLE001 —— 認碼工具不在就沿用 seat_context 的
        pass
    ctx["model"] = ctx.get("model") or "讀不到"
    return ctx


def card_path(uid):
    return state_root() / "projects" / uid / "card.json"


def context_path(code):
    return state_root() / "memory" / code / "context.md"


# ---------------------------------------------------------------- 機器事實
def _git(repo, *args):
    try:
        p = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def last_receipt(code):
    return _read_json(tidy_dir() / ("%s.json" % code), {})


def persona_since(code):
    """人格證據從哪個時刻算起:上一輪人格是「等 the maintainer 按」⇒ 沿用那一輪的起點(不然那批原話就丟了)。"""
    rec = last_receipt(code) or {}
    if ((rec.get("files") or {}).get("persona") or {}).get("status") != "waiting":
        return None                                  # None = 沒有待批的;"" = 待批那批從最開頭算(兩者不同)
    return rec.get("persona_since") or ""


def evidence_since(code):
    ps = persona_since(code)
    return ps if ps is not None else ((last_receipt(code) or {}).get("at") or "")


def facts(ctx, repo=None):
    """本輪(上一張整理收據之後)的機器事實 → 一段純文字(收進草稿、算雜湊,apply 時驗沒被改)。"""
    code, uid = ctx["code"], ctx.get("project_uid") or ""
    since = (last_receipt(code) or {}).get("at") or ""
    lines = ["- 席:@%s(seat_id %s)· 模型:%s · 量測時刻:%s" % (code, ctx.get("seat_id") or "讀不到", ctx["model"], _now()),
             "- 上一張整理收據:%s" % (since or "沒有(這是第一次)")]
    repo = repo or os.getcwd()
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    log = _git(repo, "log", "--since", since or "24 hours ago", "--format=%h %s", "-n", "15")
    lines.append("- 倉:%s(分支 %s)" % (repo, branch or "讀不到"))
    lines.append("- 本輪提交(最多 15 筆):" + ("" if log else "沒有"))
    lines += ["  - " + ln[:140] for ln in log.splitlines()]
    card = _read_json(card_path(uid), None) if uid else None
    if not uid:
        lines.append("- 專案:這一席沒綁專案(先綁;第 2 包自動綁)")
    elif card is None:
        lines.append("- 專案:%s 的卡讀不到" % uid)
    else:
        ws = ((card.get("layers") or {}).get("work_state") or {})
        lines.append("- 專案:%s(%s)" % (card.get("title") or "?", uid))
        for sid, row in sorted((ws.get("seats") or {}).items()):
            lines.append("  - 席列 %s @%s(%s,%s):現在 %s" % (sid, row.get("code") or "?", row.get("model") or "?",
                                                         row.get("at") or "?", (row.get("now") or "")[:80]))
    pc = persona_card(ctx)
    if pc:
        import persona_card_edit as pce
        lines.append("- 人格卡:%s(%s)· 身分:%s" % (pc["name"], pc["uid"],
                                                 ";".join("%s=%s" % kv for kv in pce.identity(pc["text"]).items())))
        for d, items in pce.dims(pc["text"]).items():
            if items:
                lines.append("  - 現有 %s:%s" % (d, items[0][:100]))
    else:
        lines.append("- 人格卡:這一席沒綁人格,或卡讀不到(人格那段不會寫)")
    words = human_words(ctx, evidence_since(code))
    lines.append(human_words_line(words))
    lines += ["  - [%s] %s" % (t, w.replace("\n", " ")) for t, w in words]
    lines += fit_facts(ctx, repo)
    return "\n".join(lines)


FIT_HOURS = 4
FIT_LIST_MAX = 40


def fit_facts(ctx, repo):
    """§8c(the maintainer 10-13 16:4x「是否與最近4小時內的開發批配」):最近 4 小時做了什麼 + 可以換的人格 / 專案清單(只名稱)。"""
    out = ["- 綁定檢查(最近 %d 小時):" % FIT_HOURS]
    log = _git(repo, "log", "--since", "%d hours ago" % FIT_HOURS, "--format=%h %s", "-n", "20")
    out.append("  - 提交:" + ("" if log else "沒有"))
    out += ["    - " + ln[:120] for ln in log.splitlines()]
    since = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=FIT_HOURS)).isoformat(timespec="seconds")
    words = human_words(ctx, since)
    out.append("  - 真人說的(最近 %d 小時,最多 %d 則):" % (FIT_HOURS, HUMAN_MAX) + ("" if words else "沒有"))
    out += ["    - " + w.replace("\n", " ")[:120] for _t, w in words[-15:]]
    try:
        import bindme
        cards = bindme.persona_cards(str(state_root()))
        projects = bindme.project_cards(str(state_root()))
    except Exception:  # noqa: BLE001
        cards, projects = {}, []
    out.append("  - 現有人格卡(可換的):" + "、".join(n for _u, n in list(cards.items())[:FIT_LIST_MAX]) or "讀不到")
    out.append("  - 現有專案:" + "、".join(p.get("title") or p.get("uid") for p in projects[:FIT_LIST_MAX]) or "讀不到")
    return out


# ---------------------------------------------------------------- 人格卡與 the maintainer 原話
def persona_card(ctx):
    """本席綁的人格卡 → {uid, name, path, text};沒綁或讀不到 ⇒ None。"""
    uid = ctx.get("persona_uid") or ""
    if not uid:
        return None
    cards = (_read_json(state_root() / "identity" / "cards.json", {}) or {}).get("cards") or {}
    row = cards.get(uid)
    if not isinstance(row, dict) or not row.get("card_path_rel"):
        return None
    try:
        import card_registry
        vault = Path(card_registry.resolve_vault())
    except Exception:  # noqa: BLE001
        return None
    path = vault / row["card_path_rel"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {"uid": uid, "name": row.get("display_name") or uid, "path": path, "text": text}


def _human_filter():
    """真人判準模組;開源倉還沒有(等私倉匯出)⇒ None。"""
    pc = HERE.parent / "persona-collect"
    if str(pc) not in sys.path:
        sys.path.append(str(pc))
    try:
        import human_filter
    except Exception:  # noqa: BLE001
        return None
    return human_filter


def human_words_status():
    """收據 / collect 記的真人原話來源狀態:ok,或 HUMAN_WORDS_UNAVAILABLE(缺 human_filter)。"""
    return "ok" if _human_filter() is not None else HUMAN_WORDS_UNAVAILABLE


def human_words_line(words):
    """機器事實裡那一行:缺 human_filter 時寫「讀不到」,不寫「沒有」(不讓模型以為真人沒說話)。"""
    head = "- 真人本輪原話(人格證據只認這裡的原文):"
    if words:
        return head
    if human_words_status() != "ok":
        return head + "讀不到(缺 human_filter,等私倉匯出;人格那段留空)"
    return head + "沒有"


def human_words(ctx, since="", span_rounds=False):
    """本輪(since 之後)the maintainer 親口說的話:這一席的 Claude 對話檔 → human_filter.is_human 篩
    (比對 dm 送件紀錄、dm 標頭、Stop hook 回饋等機器字樣;seat 一律當已綁真人,不然沒綁人格的席全被丟)。
    → [(時刻, 原文)](最多 HUMAN_MAX 則,每則截 300 字);讀不到回 []。"""
    paths = []
    try:                                           # 這一輪的對話檔(交接每輪記的;/clear 之後就是新對話那份)
        import aib_handoff
        rs = aib_handoff.rounds(ctx["code"])
        if rs and span_rounds and since:               # 遺漏回顧「最近 4 小時」可能跨過幾次 /clear(三輪審查 3)
            cut = _when(since)
            pick = [r for r in rs if float(r.get("ts") or 0) >= cut.timestamp()] or rs[-1:]
            pick = ([r for r in rs if float(r.get("ts") or 0) < cut.timestamp()][-1:] + pick)   # 起點前那一輪也算
            seen_ts, uniq = set(), []
            for r in pick:                             # 同一輪不要讀兩次(四輪審查 2)
                if r.get("ts") not in seen_ts:
                    seen_ts.add(r.get("ts"))
                    uniq.append(r)
            paths = []
            for r in uniq:
                for p, _off, _at in aib_handoff.round_transcripts(r):
                    if p and Path(p).is_file() and p not in paths:      # 接回的輪共用同一份對話檔
                        paths.append(p)
        elif rs:
            paths = [p for p, _off, _at in aib_handoff.round_transcripts(rs[-1]) if p and Path(p).is_file()]
    except Exception:  # noqa: BLE001
        paths = []
    if not paths:
        try:                                       # 退路:開席登記的那份
            import seat_fork
            p0 = seat_fork.find_transcript(ctx["seat_uid"])
            paths = [str(p0)] if p0 else []
        except Exception:  # noqa: BLE001
            paths = []
    if not paths:
        return []
    human_filter = _human_filter()
    if human_filter is None:                       # 缺件:collect / 收據 / 巡檢另外照實寫(human_words_status)
        return []
    seat = {"human_bound": True, "code": ctx["code"]}
    out = []
    for path in paths:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            out += _human_rows(fh, since, seat, human_filter)
    return out[-HUMAN_MAX:]


def _human_rows(fh, since, seat, human_filter):
    out = []
    if True:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "user":
                continue
            ts = str(row.get("timestamp") or "")
            if since and _when(ts) < _when(since):
                continue
            content = (row.get("message") or {}).get("content")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            text = str(content or "").strip()
            if not text:
                continue
            try:
                # skip_ledger:每則都重讀全部 RELAY 檔太慢(本機四千多檔);dm 標頭、Stop hook 回饋等機器字樣照擋
                ok, _meta = human_filter.is_human(seat, {"text": text, "raw_refs": row, "ts": ts}, skip_ledger=True)
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                out.append((ts[:16].replace("T", " "), text[:300]))
    return out


def _norm(s):
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------- 草稿
def render_draft(ctx, facts_text, nonce):
    code, uid = ctx["code"], ctx.get("project_uid") or ""
    card = _read_json(card_path(uid), {}) if uid else {}
    row = (((card.get("layers") or {}).get("work_state") or {}).get("seats") or {}).get(ctx.get("seat_id") or code) or {}
    try:
        context = context_path(code).read_text(encoding="utf-8")
    except OSError:
        context = ""
    row_text = "\n".join([
        "現在: %s" % (row.get("now") or ""),
        "下一步: %s" % (row.get("next_step") or ""),
        "待裁: %s" % ";".join(row.get("pending") or []),
    ])
    return "\n".join([
        "# 整理記憶草稿 @%s" % code,
        "<!-- tidy:draft seat=%s nonce=%s -->" % (code, nonce),
        "只改下面各段的內容;標記行與「機器事實」段不准改。",
        "",
        "## 本席這一列(專案卡,≤ %d 行;待裁多條用 ; 分)<!-- tidy:row -->" % ROW_MAX_LINES,
        row_text,
        "",
        "## 席位記憶精簡版(≤ %d 行;完成的、舊的刪掉,舊檔會自動備存)<!-- tidy:context -->" % CONTEXT_MAX_LINES,
        context.rstrip(),
        "",
        "## 進度筆記(寫進第二大腦,取代本席上一則進度)<!-- tidy:note -->",
        "",
        "",
        "## 人格卡新增(每行一條,新的寫上面;證據要是下面「真人本輪原話」裡 ≥ %d 字的原文;這輪沒觀察到就留空)<!-- tidy:persona -->"
        % EVIDENCE_MIN,
        "<!-- 格式:- 表達語氣:第一句講能不能用。好:「…」壞:「…」〔證據:「原文」〕 -->",
        "<!-- 身分只在真人說了才寫:身分:生日=1996-09-10〔證據:「原文」〕 -->",
        "",
        "",
    ] + main_section(ctx) + [
        "## 綁定檢查(這一席的人格、專案跟「最近 4 小時」做的事合不合;看機器事實最後那段)<!-- tidy:fit -->",
        "人格: 合",
        "人格建議: ",
        "專案: 合",
        "專案建議: ",
        "這4小時在做: ",
        "<!-- 不合 ⇒ 建議寫清單裡的名稱,或「新建:<名稱>」;「這4小時在做」一句話(新建時當人設 / 專案說明) -->",
        "",
        "## 機器事實(collect 量的,不要改)<!-- tidy:facts -->",
        facts_text,
        "",
    ])


def main_section(ctx):
    """草稿的主人格段(只在 main_persona_target 有卡時出現)。"""
    pc, _why = main_persona_target(ctx)
    if not pc:
        return []
    return [
        "## 主人格新增(真人分身「%s」;只寫真人親口說話看得出的習慣、偏好,只補不改、不寫身分;證據規則同上;沒觀察到就留空)"
        "<!-- tidy:main -->" % pc["name"],
        "<!-- 格式同人格卡新增:- 維度:規則〔證據:「原文」〕 -->",
        "",
        "",
    ]


def parse_draft(text):
    m = HEAD_RE.search(text or "")
    if not m:
        raise TidyError("草稿頭不見了(<!-- tidy:draft … -->);重新 collect")
    out = {"code": m.group(1), "nonce": m.group(2)}
    marks = list(SEC_RE.finditer(text))
    names = [mk.group(1) for mk in marks]
    if sorted(n for n in names if n not in OPTIONAL_SECTIONS) != sorted(SECTIONS) or len(names) != len(set(names)):
        raise TidyError("段落標記不對(要恰好 %s 各一個):%s" % (" / ".join(SECTIONS), names))
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[mk.group(1)] = text[mk.end():end].strip("\n")
    return out


def parse_persona(text):
    """→ (dims {維度: [條]}, identity {欄: 值}, 錯誤清單, 證據清單)。註解行(<!-- -->)與空行略過。"""
    import persona_card_edit as pce
    dims, ident, errs, evid = {}, {}, [], []
    for ln in (text or "").splitlines():
        if not ln.strip() or ln.strip().startswith("<!--"):
            continue
        m = IDENT_LINE_RE.match(ln)
        if m:
            ident[m.group(1)] = m.group(2).strip()
            evid.append(m.group(3))
            continue
        m = PERSONA_LINE_RE.match(ln)
        if not m:
            errs.append("看不懂這行(要「- 維度:規則〔證據:「原文」〕」):%s" % ln.strip()[:60])
            continue
        if m.group(1) not in pce.DIMS:
            errs.append("沒有這個維度:%s" % m.group(1))
            continue
        dims.setdefault(m.group(1), []).append((m.group(2).strip(), m.group(3)))
        evid.append(m.group(3))
    return dims, ident, errs, evid


def parse_row(text):
    """「現在 / 下一步 / 待裁」三欄;其他非空行收進 notes(不丟)。"""
    row = {"now": "", "next_step": "", "pending": [], "notes": []}
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        for zh, key in ROW_FIELDS:
            m = re.match(r"^\s*%s\s*[::]\s*(.*)$" % zh, ln)
            if m:
                v = m.group(1).strip()
                row[key] = [x.strip() for x in re.split(r"[;;]", v) if x.strip()][:PENDING_MAX] if key == "pending" else v
                break
        else:
            row["notes"].append(ln.strip())
    if not row["notes"]:
        row.pop("notes")
    return row


# ---------------------------------------------------------------- collect
def collect(seat="", out=None, repo=None):
    ctx = seat_info(seat)
    nonce = secrets.token_hex(8)
    ftext = facts(ctx, repo)
    draft = render_draft(ctx, ftext, nonce)
    pc = persona_card(ctx)
    mpc, _why = main_persona_target(ctx)
    since = evidence_since(ctx["code"])
    words = [w for _t, w in human_words(ctx, since)]
    _write_json(tidy_dir() / ("%s.collect.json" % ctx["code"]),
                {"nonce": nonce, "facts_sha": _sha(ftext.strip("\n")), "at": _now(), "project_uid": ctx.get("project_uid"),
                 "persona_sha": _sha(pc["text"]) if pc else "", "persona_since": since,
                 # 證據只認這一份(collect 當下抽的 the maintainer 原話),不從機器事實文字切 —— 文字裡的提交標題、各席列都可能被寫成假原話
                 "human_words": words, "human_words_status": human_words_status(),
                 "main_sha": _sha(mpc["text"]) if mpc else ""})
    if out:
        Path(out).write_text(draft, encoding="utf-8")
    return draft


# ---------------------------------------------------------------- apply
def validate(ctx, parts, skip_context=False):
    """回錯誤清單(空 = 過)。任何一條不過 ⇒ 什麼都不寫。"""
    errs = []
    col = _read_json(tidy_dir() / ("%s.collect.json" % ctx["code"]), {})
    if parts["code"] != ctx["code"]:
        errs.append("草稿是 @%s 的,這一席是 @%s" % (parts["code"], ctx["code"]))
    if parts["nonce"] != col.get("nonce"):
        errs.append("草稿不是這一席最後一次 collect 的那份(nonce 對不上);重新 collect")
    if _sha(parts["facts"].strip("\n")) != col.get("facts_sha"):
        errs.append("機器事實段被改過")
    if (col.get("project_uid") or "") != (ctx.get("project_uid") or ""):
        errs.append("collect 之後這一席換綁了專案(%s → %s);重新 collect" % (col.get("project_uid") or "-", ctx.get("project_uid") or "-"))
    row_lines = [ln for ln in parts["row"].splitlines() if ln.strip()]
    if len(row_lines) > ROW_MAX_LINES:
        errs.append("本席列 %d 行,超過 %d" % (len(row_lines), ROW_MAX_LINES))
    if ctx.get("project_uid") and not parse_row(parts["row"])["now"]:
        errs.append("本席列的「現在:」是空的")
    ctx_lines = parts["context"].splitlines()
    if len(ctx_lines) > CONTEXT_MAX_LINES:
        errs.append("席位記憶 %d 行,超過 %d" % (len(ctx_lines), CONTEXT_MAX_LINES))
    if not parts["context"].strip() and not skip_context:
        errs.append("席位記憶精簡版是空的")
    pdims, pident, perrs, evid = parse_persona(parts.get("persona", ""))
    errs += perrs
    if (pdims or pident) and not persona_card(ctx):
        errs.append("人格那段有寫,但這一席沒綁人格或卡讀不到")
    mdims, mident, merrs, mevid = parse_persona(parts.get("main", ""))
    errs += merrs
    if mident:
        errs.append("主人格那段不寫身分(只補 12 維)")
    if (mdims or mident) and not main_persona_target(ctx)[0]:
        errs.append("主人格那段有寫,但這輪不寫主人格:%s" % main_persona_target(ctx)[1])
    for d, items in mdims.items():
        if len(items) > 3:
            errs.append("主人格「%s」一次加了 %d 條(最多 3)" % (d, len(items)))
    evid = list(evid) + list(mevid)
    said = [_norm(w) for w in (col.get("human_words") or [])]
    for e in evid:
        if len(_norm(e)) < EVIDENCE_MIN:
            errs.append("證據太短(去空白後至少 %d 字):「%s」" % (EVIDENCE_MIN, e))
        elif not any(_norm(e) in w for w in said):
            errs.append("證據不在真人本輪原話裡(要原文,不能改寫):「%s」" % e[:40])
    for d, items in pdims.items():
        if len(items) > 3:
            errs.append("「%s」一次加了 %d 條(最多 3)" % (d, len(items)))
    note = parts["note"].strip()
    if note and len(note) < NOTE_MIN_CHARS:          # 空 = 這輪沒變(不寫筆記);有寫就要像樣
        errs.append("進度筆記太短(至少 %d 字;這輪沒變就整段留空)" % NOTE_MIN_CHARS)
    return errs


class _Lock:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a")
        deadline = datetime.datetime.now().timestamp() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if datetime.datetime.now().timestamp() > deadline:
                    self.f.close()
                    raise TidyError("鎖 %s 拿不到超過 10 秒" % self.path)
                import time
                time.sleep(0.1)

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def card_lock(uid):
    """專案卡的鎖:和 aib newproj 補記資料夾那段同一把(tools/project/new_project.py)。"""
    return _Lock(card_path(uid).parent / "card.lock")


def write_card_row(ctx, row):
    """本席列 + 總覽(從各席列彙整)。鎖內讀 → 快照 → 寫;快照拍不成就不寫。回 (status, 原因)。"""
    uid = ctx["project_uid"]
    path = card_path(uid)
    with card_lock(uid):
        card = _read_json(path, None)
        if card is None:
            return "failed", "專案卡讀不到"
        snap = memory_archive.snapshot(path, "project", uid)
        if snap["status"] == "failed":
            return "failed", "快照拍不成:%s" % snap.get("error")
        ws = card.setdefault("layers", {}).setdefault("work_state", {"now": "", "owners": [], "pending": [], "next_step": ""})
        first = "seats" not in ws
        if first:
            # 第一次整理:原本手寫的總覽不蓋掉 —— 收成一列「手寫總覽」併進彙整(待裁、負責人都保留)
            ws["seats"] = {"_manual": {"now": ws.get("now") or "", "next_step": ws.get("next_step") or "",
                                       "pending": list(ws.get("pending") or []), "code": "",
                                       "owners": list(ws.get("owners") or []), "model": "手寫", "at": _now()}}
        seats = ws["seats"]
        key = ctx.get("seat_id") or ctx["code"]
        seats.pop(key, None)                        # 先拿掉再放 ⇒ 字典順序 = 最後寫入的順序
        seats[key] = dict(row, code=ctx["code"], model=ctx["model"], at=_now())
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=FRESH_DAYS)
        # 新的在前;同一秒寫的照寫入先後(後寫的在前);手寫總覽那列永遠排最後、而且不受 14 天限制(the maintainer 手寫的不准悄悄消失)
        ranked = [(_when(r.get("at")), 0 if k == "_manual" else i + 1, k, r) for i, (k, r) in enumerate(seats.items())]
        live = [r for at, _rank, k, r in sorted(ranked, key=lambda t: (t[0], t[1]), reverse=True)
                if k == "_manual" or at >= cutoff]
        ws["now"] = ";".join(("@%s:" % r["code"] if r.get("code") else "") + r["now"][:NOW_ROW_CHARS]
                             for r in live if r.get("now"))
        ws["next_step"] = row.get("next_step") or ws.get("next_step") or ""
        owners = []
        for r in live:
            for o in ([r.get("code")] if r.get("code") else []) + list(r.get("owners") or []):
                if o and o not in owners:
                    owners.append(o)
        ws["owners"] = owners
        pend = []
        for r in live:
            for p in r.get("pending") or []:
                if p not in pend:
                    pend.append(p)
        ws["pending"] = pend[:PENDING_MAX]
        _write_json(path, card)
        back = _read_json(path, {})
        if ((back.get("layers") or {}).get("work_state") or {}).get("seats", {}).get(key, {}).get("now") != row["now"]:
            return "failed", "讀回不符"
    return "ok", ""


def write_context(ctx, text):
    path = context_path(ctx["code"])
    snap = memory_archive.snapshot(path, "memory", ctx["code"])
    if snap["status"] == "failed":
        return "failed", "快照拍不成:%s" % snap.get("error")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(text.rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return "ok", ""


def _tidy_writes():
    out = set()
    try:
        for ln in (tidy_dir() / "writes.jsonl").read_text(encoding="utf-8").splitlines()[-500:]:
            try:
                out.add(json.loads(ln).get("sha"))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def main_persona_uid():
    """登記的主人格(真人分身)uid:identity/user-main-persona.json(aib persona main set 寫);沒登記 ⇒ ""。"""
    uid = (_read_json(state_root() / "identity" / "user-main-persona.json", {}) or {}).get("persona_uid")
    return uid if isinstance(uid, str) else ""


def is_crown(uid):
    """大變動要真人按的卡(真人分身):有登記主人格就認登記那張(10-16 維護者裁),沒登記才退回冠位常數。"""
    return uid == (main_persona_uid() or CROWN_PERSONA)


def main_persona_target(ctx):
    """這一席這輪要不要另外精煉主人格 → (卡 or None, 不做的原因)。
    維護者 10-16 13:1x:「真人分身就算沒有被綁定，也可以在判斷真人的情況下、靜默優化」。"""
    uid = main_persona_uid()
    if not uid:
        return None, "主人格沒登記(aib persona main set <卡號>)"
    if uid == (ctx.get("persona_uid") or ""):
        return None, "本席就綁主人格(寫在人格那段)"
    if human_words_status() != "ok":
        return None, "讀不到真人原話(缺 human_filter,等私倉匯出)"
    pc = persona_card({"persona_uid": uid})
    if not pc:
        return None, "主人格卡 %s 讀不到" % uid
    return pc, ""


def ask_big_change(pc, reasons, dialog=None):
    """第一艦長卡大變動:跳視窗等 the maintainer 按(不給 30 秒預設;逾時 = 不改)。回 True = 套用。"""
    try:
        import bindme
        dialog = dialog or bindme.mac_dialog
        btn, how = dialog("AIB 人格卡", "第一艦長人格卡「%s」這次是大變動:\n%s\n\n要套用嗎?(逾時 = 不改)"
                          % (pc["name"], "\n".join("- " + r for r in reasons)), ["不改", "套用"], "不改", "不改", 120)
    except Exception:  # noqa: BLE001
        return False
    return how == "picked" and btn == "套用"


def refingerprint(ctx, pc, runner=None):
    """改了卡 ⇒ card_registry scan 一次,再對每個綁這張卡的席 bind_one bind --card <uid> 釘新指紋
    (不走 aib newrole:那條會把預設範本接進席位記憶)。回 [(席碼, rc)]。"""
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                               timeout=300).returncode)
    src = HERE.parent
    try:
        import bindme
        import card_registry
        rows = [st for st in bindme.status(str(state_root())) if st.get("persona_uid") == pc["uid"]]
        actor = bindme._actor(str(state_root()), ctx["code"], None)
        vault = str(card_registry.resolve_vault())
    except BaseException as exc:  # noqa: BLE001 —— 認不出席也只記失敗,不擋其他
        return [("?", "failed: %s" % exc)]
    rc = run([sys.executable, str(src / "memory" / "card_registry.py"), "scan", "--vault", vault])
    if rc != 0:
        return [("scan", rc)]
    out = []
    for i, st in enumerate(rows):
        cmd = [sys.executable, str(src / "identity" / "bind_one.py"), "bind", "--seat", st["seat_id"], "--card", pc["uid"],
               "--actor", actor, "--state-root", str(state_root())]
        if i < len(rows) - 1:
            cmd.append("--no-reproject")                   # 最後一席才重投影一次
        out.append((st["code"], run(cmd)))
    return out


def write_persona(ctx, parts, dialog=None, runner=None, defer_big=False, add_only=False, main=None):
    """加 12 維、改身分 → 快照 → 寫 → 重新登記指紋。回 (status, 細節)。
    defer_big:依用量自動精煉(10~70%)時第一艦長卡大變動不跳窗,存成待批,85% 交接那次再跳(DESIGN §8b.2)。
    main:主人格卡(main_persona_target 給的)⇒ 讀「主人格新增」段寫進那張;靜默(10-16 維護者裁):只補不改、
         不改身分、不跳窗;快照 + writes.jsonl + 收據照寫,可查可退。"""
    import persona_card_edit as pce
    pdims, pident, _e, _v = parse_persona(parts.get("main" if main else "persona", ""))
    if main:
        pident, add_only = {}, True
    if not pdims and not pident:
        return "skipped", "這輪沒觀察到"
    pc = main or persona_card(ctx)
    stamp = "%s · @%s · %s" % (_now()[:16].replace("T", " "), ctx["code"], ctx["model"])
    new = {d: ["%s〔AIB · %s · 證據:「%s」〕" % (rule, stamp, ev) for rule, ev in items] for d, items in pdims.items()}
    if add_only:                                     # 只補不改:那一維已滿 3 條就不加(不擠掉舊的)
        import persona_card_edit as _pce
        have = _pce.dims(pc["text"])
        new = {d: v[:max(0, _pce.PER_DIM - len(have.get(d) or []))] for d, v in new.items()}
        new = {d: v for d, v in new.items() if v}
        if not new and not pident:
            return "skipped", "只補不改:這幾維都滿 3 條,沒有新增"
    text, dropped = pce.set_dims(pc["text"], new)
    if pident:
        text = pce.set_identity(text, pident)
    if not main and is_crown(pc["uid"]):
        big, reasons = pce.big_change(pc["text"], text)
        if big and defer_big:
            return "waiting", "第一艦長卡大變動,存到 85%% 交接時給真人按:%s" % ";".join(reasons)
        if big and not ask_big_change(pc, reasons, dialog):
            return "waiting", "第一艦長卡大變動,真人沒有按套用:%s" % ";".join(reasons)
    with _Lock(tidy_dir() / ("persona-%s.lock" % pc["uid"])):
        # collect 之後卡變了:是別席 tidy 寫的(sha 在 writes.jsonl)⇒ 照新內容合併(上面就是拿現在的卡算的);
        # 不是 ⇒ 有人手改(例如 the maintainer 在 Obsidian 改)⇒ 不蓋,重新 collect(DESIGN §4.0b)
        col = _read_json(tidy_dir() / ("%s.collect.json" % ctx["code"]), {})
        try:
            now_text = pc["path"].read_text(encoding="utf-8")
        except OSError as exc:
            return "failed", str(exc)
        if now_text != pc["text"]:
            return "failed", "人格卡在套用途中又被改過,重新 collect"
        sha_key = "main_sha" if main else "persona_sha"
        if col.get(sha_key) and _sha(now_text) != col[sha_key] and _sha(now_text) not in _tidy_writes():
            return "failed", "人格卡在 collect 之後被人手改過(不是 tidy 寫的),不蓋;重新 collect"
        snap = memory_archive.snapshot(pc["path"], "persona", pc["uid"])
        if snap["status"] == "failed":
            return "failed", "快照拍不成:%s" % snap.get("error")
        tmp = pc["path"].with_name(pc["path"].name + ".tmp-%d" % os.getpid())
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, pc["path"])
        with open(tidy_dir() / "writes.jsonl", "a", encoding="utf-8") as f:   # 別席 collect 後看到這個 sha = tidy 寫的,可合併
            f.write(json.dumps({"sha": _sha(text), "uid": pc["uid"], "seat": ctx["code"], "at": _now()}) + "\n")
    rebinds = refingerprint(ctx, pc, runner)
    bad = [c for c, rc in rebinds if rc != 0]
    detail = "加 %d 條、擠掉 %d 條(在快照裡)、重釘 %d 席" % (sum(len(v) for v in new.values()), len(dropped), len(rebinds))
    return ("ok" if not bad else "partial"), detail + ("" if not bad else ";重釘失敗:%s" % bad)


def previous_progress(ctx):
    """本席在**同一個專案**的上一則進度筆記(這次要取代它);換綁前別的專案那則不取代。"""
    import aib_memory
    mine = [r for r in aib_memory.entries() if r.get("kind") == "progress" and r.get("seat_uid") == ctx["seat_uid"]
            and (r.get("project_uid") or "") == (ctx.get("project_uid") or "")]
    mine.sort(key=lambda r: r.get("created_ms") or 0)
    return mine[-1]["uid"] if mine else ""


def write_note(ctx, body, seat="", supersede=True):
    import aib_memory
    prev = previous_progress(ctx) if supersede else ""
    stamp = "\n\n— %s · @%s · %s(記憶整理)" % (_now()[:16].replace("T", " "), ctx["code"], ctx["model"])
    rec = aib_memory.write("progress", "@%s 進度 %s" % (ctx["code"], _now()[:16].replace("T", " ")), body.strip() + stamp,
                           seat=seat, project=ctx.get("project_uid") or "", supersedes=prev, model=ctx["model"],
                           tags=["aib/tidy"])
    return "ok", rec.get("uid", "")


FIT_MISS_TO_ACT = 2         # 連兩次判「不合」才換(防一次誤判來回切)


def parse_fit(text):
    """綁定檢查段 → {"persona": (ok, 建議), "project": (ok, 建議), "summary": 這4小時在做}。ok:True / False / None(沒填)。"""
    vals = {}
    for ln in (text or "").splitlines():
        if ln.strip().startswith("<!--") or ":" not in ln.replace(":", ":"):
            continue
        k, v = ln.replace(":", ":", 1).split(":", 1)
        vals[k.strip()] = v.strip()

    def ok(v):
        v = (v or "").strip()
        return True if v.startswith("合") else False if v.startswith("不合") else None
    return {"persona": (ok(vals.get("人格")), vals.get("人格建議") or ""),
            "project": (ok(vals.get("專案")), vals.get("專案建議") or ""),
            "summary": vals.get("這4小時在做") or ""}


def fit_check(ctx, text, spawn=None):
    """§8c:連 FIT_MISS_TO_ACT 次「不合」⇒ 照第 2 包的路(寫綁定提案、背景 bindme auto:既有卡直接綁,
    新建跳 30 秒窗、不按照預設建)。回 {"status", "why"}。"""
    import bindme
    fit = parse_fit(text)
    p = tidy_dir() / ("%s.fit.json" % ctx["code"])
    st = _read_json(p, {})
    act = {}
    for kind in ("persona", "project"):
        good, sug = fit[kind]
        if good is not False:
            st[kind] = 0
            continue
        st[kind] = int(st.get(kind) or 0) + 1
        if st[kind] >= FIT_MISS_TO_ACT:
            act[kind] = sug
            st[kind] = 0
    _write_json(p, st)
    if not act:
        miss = [k for k in ("persona", "project") if st.get(k)]
        return {"status": "ok" if not miss else "watch", "why": "不合 1 次,再一次才換:%s" % "、".join(miss) if miss else ""}
    root = str(state_root())
    seat = next((x for x in bindme.status(root) if x["code"] == ctx["code"]), None)
    if not seat or seat.get("problem") == "bot":
        return {"status": "skipped", "why": "認不出這一席或是 bot 席(bot 席不自動綁)"}
    cards = bindme.persona_cards(root)
    f = {"主項目": fit["summary"], "做了": fit["summary"]}
    sid = "fit-%s-%s" % (datetime.datetime.now().strftime("%m%d-%H%M"), ctx["code"])
    per = {"uid": seat.get("persona_uid"), "name": seat.get("persona_name") or "", "how": "keep"}
    if "persona" in act:
        sug = act["persona"]
        hits = [u for u, n in cards.items() if n == sug and u != bindme.CROWN_PERSONA]
        if len(hits) == 1:
            per = {"uid": hits[0], "name": sug, "how": "ask_existing", "why": "綁定檢查連兩次不合"}
        else:
            per = {"uid": None, "name": "", "how": "draft_new", "why": "綁定檢查連兩次不合、清單沒有合適的"}
            if sug.startswith("新建"):
                f["建議人格"] = sug.split(":", 1)[-1].split(":", 1)[-1].strip()
            per["draft_path"] = bindme.write_persona_draft(root, sid, ctx["code"], f)
    prj = {"uid": seat.get("project_uid"), "title": seat.get("project_title") or "", "how": "keep"}
    if "project" in act:
        sug = act["project"]
        name = sug.split(":", 1)[-1].split(":", 1)[-1].strip() if sug.startswith("新建") else sug
        u, t, h = bindme.match_project(name or fit["summary"][:30], bindme.project_cards(root))
        prj = {"uid": u, "title": t or name or fit["summary"][:30], "how": h if h != "unknown" else "new"}
    addr = "%s/%s" % (seat["cabin4"], ctx["code"])
    row = {"code": ctx["code"], "seat_id": seat["seat_id"], "addr": addr, "replied": True, "cabin": seat.get("cabin") or "",
           "summary": fit["summary"], "fields": f, "persona": per, "project": prj, "provider": seat.get("provider") or "",
           "confirm": True,                                  # the maintainer 10-13 16:5x:換綁一律跳窗倒數 30 秒,沒選照預設
           "old_persona": seat.get("persona_name") or "", "old_project": seat.get("project_title") or ""}
    rec = {"sid": sid, "me": ctx["code"], "hours": FIT_HOURS, "created": _now(), "source": "tidy-fit", "token": "",
           "targets": [{"code": ctx["code"], "addr": addr, "seat_id": seat["seat_id"], "stage": "tidy-fit"}],
           "proposals": [row]}
    os.makedirs(bindme.survey_dir(root), exist_ok=True)
    Path(bindme.survey_path(root, sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    if not bindme.auto_enabled(root):
        return {"status": "proposed", "why": "自動綁關著;提案在 %s" % bindme.survey_path(root, sid)}
    pid = (spawn or bindme.spawn_auto)(root, sid, ctx["code"])
    return {"status": "rebinding" if pid else "failed", "why": "換綁:%s" % "、".join(
        {"persona": "人格", "project": "專案"}[k] for k in act) + ("" if pid else "(背景起不來)")}


def _persona_count(parts):
    """人格這次加 / 改幾條:12 維條數 + 身分欄數(自動精煉提醒「人格 +N、大改 M」讀這個)。"""
    dims, ident, _e, _v = parse_persona(parts.get("persona", ""))
    return sum(len(v) for v in dims.values()) + len(ident)


def merge_row_add_only(ctx, row):
    """只補不改:本席列原有的「現在 / 下一步」留著(新的只在原本空時用),待裁取聯集。"""
    uid = ctx.get("project_uid") or ""
    card = _read_json(card_path(uid), {}) if uid else {}
    old = (((card.get("layers") or {}).get("work_state") or {}).get("seats") or {}).get(ctx.get("seat_id") or ctx["code"]) or {}
    out = dict(row)
    for k in ("now", "next_step"):
        if old.get(k):
            out[k] = old[k]
    pend = list(old.get("pending") or [])
    for p in row.get("pending") or []:
        if p not in pend:
            pend.append(p)
    out["pending"] = pend[:PENDING_MAX]
    return out


def apply(text, seat="", defer_big=False, skip_context=False, add_only=False):
    """skip_context:API 席代跑(草稿不帶席位記憶,省 token;a seat 10-13 14:41)⇒ 席位記憶段可空、空就不寫。"""
    ctx = seat_info(seat)
    parts = parse_draft(text)
    if add_only:                                     # §8e.7 遺漏回顧「只補不改」:不重寫席位記憶
        skip_context = True
    errs = validate(ctx, parts, skip_context=skip_context)
    if errs:
        raise TidyError("草稿沒過,什麼都沒寫:\n- " + "\n- ".join(errs))
    files = {}
    if ctx.get("project_uid"):
        try:
            row = parse_row(parts["row"])
            if add_only:
                row = merge_row_add_only(ctx, row)
            files["card"] = dict(zip(("status", "why"), write_card_row(ctx, row)))
        except Exception as exc:  # noqa: BLE001 —— 鎖、磁碟、權限:這張卡記 failed,其餘照寫、收據照寫
            files["card"] = {"status": "failed", "why": str(exc)[:200]}
    else:
        files["card"] = {"status": "skipped", "why": "這一席沒綁專案"}
    try:
        if skip_context:                       # 審查:模型會把佔位說明照抄回來 ⇒ 不管填了什麼都不寫
            files["context"] = {"status": "skipped", "why": "API 席代跑,不改席位記憶"}
        else:
            files["context"] = dict(zip(("status", "why"), write_context(ctx, parts["context"])))
    except Exception as exc:  # noqa: BLE001
        files["context"] = {"status": "failed", "why": str(exc)[:200]}
    try:
        st, why = write_persona(ctx, parts, defer_big=defer_big, add_only=add_only)
        files["persona"] = {"status": st, "why": why,
                            "added": _persona_count(parts) if st in ("ok", "partial", "waiting") else 0}
    except Exception as exc:  # noqa: BLE001
        files["persona"] = {"status": "failed", "why": str(exc)[:200]}
    mpc, mwhy = main_persona_target(ctx)
    if not mpc:
        files["main_persona"] = {"status": "skipped", "why": mwhy}
    else:
        try:
            st, why = write_persona(ctx, parts, main=mpc)
            mdims = parse_persona(parts.get("main", ""))[0]
            files["main_persona"] = {"status": st, "why": why, "uid": mpc["uid"],
                                     "added": sum(len(v) for v in mdims.values()) if st in ("ok", "partial") else 0}
        except Exception as exc:  # noqa: BLE001 —— 主人格寫不進只記這一筆,其他照寫(不報錯)
            files["main_persona"] = {"status": "failed", "why": str(exc)[:200], "uid": mpc["uid"]}
    try:
        files["fit"] = fit_check(ctx, parts.get("fit", ""))
    except Exception as exc:  # noqa: BLE001 —— 綁定檢查壞了不擋其他
        files["fit"] = {"status": "failed", "why": str(exc)[:200]}
    if not parts["note"].strip():
        files["note"] = {"status": "skipped", "why": "這輪沒變(筆記留空)"}
    else:
        try:
            st, uid = write_note(ctx, parts["note"], seat, supersede=not add_only)
            files["note"] = {"status": st, "uid": uid}
        except Exception as exc:  # noqa: BLE001 —— 第二大腦寫不進(這台沒有 Vault 等)照實記,其他照寫
            files["note"] = {"status": "failed", "why": str(exc)[:200]}
    col = _read_json(tidy_dir() / ("%s.collect.json" % ctx["code"]), {})
    hw = col.get("human_words_status") or human_words_status()
    if hw != "ok" and files["persona"]["status"] == "skipped":
        files["persona"]["why"] = "讀不到真人原話(缺 human_filter,等私倉匯出)"
    status = "ok" if all(f["status"] in ("ok", "skipped") for f in files.values()) else "partial"   # waiting(等 the maintainer 按)也算 partial
    rec = {"status": status, "at": _now(), "seat": ctx["code"], "seat_id": ctx.get("seat_id") or "",
           "persona_since": col.get("persona_since") or "",
           "model": ctx["model"], "project_uid": ctx.get("project_uid") or "", "nonce": parts["nonce"], "files": files}
    rec["human_words"] = hw
    _write_json(tidy_dir() / ("%s.json" % ctx["code"]), rec)
    memory_archive.spawn_background()           # 一天一次的 90 天清理,背景、不等
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib memory tidy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--seat", default="")
    c.add_argument("--out", default="")
    c.add_argument("--repo", default="")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--seat", default="")
    a.add_argument("--add-only", action="store_true", help="只補不改(遺漏回顧用):不重寫席位記憶、本席列只補、人格滿了不擠、筆記不取代")
    a.add_argument("--defer-big", action="store_true",
                   help="第一艦長卡大變動不跳窗,存到 85%% 交接再按(依用量自動精煉用)")
    s = sub.add_parser("status")
    s.add_argument("--seat", default="")
    o = ap.parse_args(argv)
    try:
        if o.cmd == "collect":
            text = collect(o.seat, o.out or None, o.repo or None)
            print(("已寫草稿:%s" % o.out) if o.out else text)
            return 0
        if o.cmd == "apply":
            try:
                text = Path(o.file).read_text(encoding="utf-8")
            except OSError as exc:
                raise TidyError("讀不到草稿 %s:%s" % (o.file, exc))
            rec = apply(text, o.seat, defer_big=o.defer_big, add_only=o.add_only)
            print(json.dumps(rec, ensure_ascii=False, indent=1))
            return 0
        ctx = seat_info(o.seat)
        print(json.dumps(last_receipt(ctx["code"]) or {"status": "none"}, ensure_ascii=False, indent=1))
        return 0
    except TidyError as exc:
        print("aib memory tidy:%s" % exc, file=sys.stderr)
        return 3 if "認不出" in str(exc) else 2


if __name__ == "__main__":
    sys.exit(main())
