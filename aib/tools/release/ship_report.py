#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""推完一包自動產生七段回報的草稿(10-08 the maintainer:「不能只靠記憶,如何程式化」「把回報省多少格式也一併程式化」)。

只在 aib 自己的倉(有 tools/report/aib-report.py)做。數字全部從統計紀錄(ship-stats.jsonl)那一輪讀,不手打:
沒有 aib 要多久、這次實際多久、其中 aib 判斷省的 vs 同時跑省的(10-07 13:20 那輪:省 2.4 小時裡 aib 只省 13 分鐘)、
為什麼幾乎全部重跑(改到追蹤器)、主線舊紅放行與沒驗的(跟驗過的一樣顯眼)。時間一律台北。
「the maintainer 原話」「偏了沒」「下一步」工具不知道 ⇒ 寫「待補」,由人補完再送。產生失敗不影響推包。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TPE = datetime.timezone(datetime.timedelta(hours=8))


def taipei(utc):
    """"2026-10-07T05:20:00Z" ⇒ "10-07 13:20"(台北);讀不懂 ⇒ 原字串。"""
    try:
        t = datetime.datetime.strptime(str(utc), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return str(utc)
    return t.astimezone(TPE).strftime("%m-%d %H:%M")


def split(row):
    """一輪的 (沒有 aib, 實際, aib 判斷不用跑的測試一支一支跑的秒數, 同時跑省的) 秒數;草稿與 aib ship stats --at 共用。
    10-08 語意統一(跟推前檢查結尾那棵樹同一個拆法):少等 = aib 那塊(不用跑的秒數 ÷ 份數)+ 同時跑那塊(其餘)。"""
    full, wall = float(row.get("full_s") or 0), float(row.get("wall_s") or 0)
    aib = sum(float(v or 0) for v in (row.get("by_feature") or {}).values())
    less = max(0.0, full - wall)
    par = less - min(less, aib / max(1, int(row.get("shards") or 1)))
    return full, wall, aib, par


def at_command(row):
    """回報裡每個數字的查證指令:指到這一輪(10-08 §27.4),不是「aib ship stats」那種只給累計的。"""
    return 'aib ship stats --at "%s"' % taipei(row.get("utc"))


def find_rows(rows, at):
    """at = 「MM-DD HH:MM」(台北,草稿裡印的那種)或統計紀錄原本的 UTC 字串 ⇒ 對得上的那幾輪。"""
    at = " ".join(str(at).split())
    return [r for r in rows if at and (taipei(r.get("utc")) == at or str(r.get("utc")) == at)]


def row_lines(T, dur, row):
    """一輪的數字,跟草稿同一套算法(查得出草稿裡的數字是從哪裡來的)。"""
    full, wall, aib, par = split(row)
    bf = row.get("by_feature") or {}
    out = [T("stats_at_row", {"when": taipei(row.get("utc")), "total": dur(full), "actual": dur(wall),
                               "n": int(row.get("shards") or 1), "seconds": dur(aib),
                               "count": int(row.get("files_total") or 0)}),
           "  " + T("rp_aib_ev", {"total": dur(bf.get("select")), "actual": dur(bf.get("reuse")),
                                  "seconds": dur(bf.get("keyed"))}),
           "  " + T("rp_par", {"seconds": dur(par), "n": int(row.get("shards") or 1)})]
    if (row.get("causes") or {}).get("tracer"):
        out.append("  " + T("rp_cause_tracer"))
    return out


def draft(T, dur, row, commits, old_reds, unverified):
    """aib-report 的輸入(dict)。T = 翻譯函式(aib.ship.*),dur = 秒數 → 白話。"""
    when = taipei(row.get("utc"))
    src = T("rp_ev_src", {"when": when})
    cmd = at_command(row)
    full, wall, aib, par = split(row)
    bf = row.get("by_feature") or {}
    g1 = [{"id": "G1.1", "title": T("rp_pushed", {"n": len(commits)}), "status": "done",
           # 打勾的那一行一定要帶憑據(report-lint:只有 ✅ 沒有 sha / 數字 ⇒ 硬擋)⇒ 帶第一顆的 sha
           "evidence": T("rp_pushed_ev", {"what": ("%s %s" % (commits[0][0][:9], commits[0][1]) if commits else "-")[:70]}),
           "evidence_command": "git log --oneline origin/main -%d" % max(1, len(commits))}]
    g2 = [{"id": "G2.1", "title": T("rp_without", {"total": dur(full)}), "status": "done",
           "evidence": T("rp_without_ev", {"n": int(row.get("files_total") or 0) - int(row.get("full_unknown") or 0),
                                           "count": int(row.get("full_unknown") or 0)}), "evidence_command": cmd},
          {"id": "G2.2", "title": T("rp_actual", {"actual": dur(wall), "n": int(row.get("shards") or 1)}), "status": "done",
           "evidence": src, "evidence_command": cmd},
          {"id": "G2.3", "title": T("rp_aib", {"seconds": dur(aib), "n": int(row.get("shards") or 1),
                                               "total": dur(full - wall - par)}), "status": "done",
           "evidence": T("rp_aib_ev", {"total": dur(bf.get("select")), "actual": dur(bf.get("reuse")),
                                       "seconds": dur(bf.get("keyed"))}), "evidence_command": cmd},
          {"id": "G2.4", "title": T("rp_par", {"seconds": dur(par), "n": int(row.get("shards") or 1)}), "status": "done",
           "evidence": src, "evidence_command": cmd}]
    tracer = (row.get("causes") or {}).get("tracer")
    if tracer:
        g2.append({"id": "G2.5", "title": T("rp_cause_tracer"), "status": "done",
                   "evidence": ", ".join(os.path.basename(f) for f in tracer), "evidence_command": cmd})
    g3 = [{"id": "G3.1", "title": T("rp_old_red", {"n": len(old_reds), "total": int(row.get("files_total") or 0)}), "status": "partial" if old_reds else "done",
           "evidence": ", ".join(sorted(old_reds))[:200] or T("rp_none"), "evidence_command": "aib ship package"},
          {"id": "G3.2", "title": T("rp_unv", {"n": len(unverified), "total": int(row.get("files_total") or 0)}), "status": "waiting_prereq" if unverified else "done",
           "evidence": ", ".join(sorted(unverified))[:200] or T("rp_none"), "evidence_command": "aib ship package"}]
    return {
        "title": T("rp_title", {"when": when}),
        "tree": [{"id": "G1", "title": T("rp_g1"), "children": g1},
                 {"id": "G2", "title": T("rp_g2", {"when": when}), "children": g2},
                 {"id": "G3", "title": T("rp_g3"), "children": g3}],
        "progress_text": T("rp_progress", {"seconds": dur(aib), "total": dur(par), "when": when}),
        "stars": 3,
        "progress": None,
        "summary": T("rp_summary", {"n": len(commits), "total": dur(full), "actual": dur(wall)}),
        "reflection": T("rp_reflection"),
        "footer": {"dispatched": 0, "executed": T("rp_executed"), "human_decision": T("rp_todo"),
                   "waiting_update": T("rp_todo"), "next": T("rp_todo"), "patrol_minutes": 30, "patrol": T("rp_todo")},
    }


EARLY_RED = 5      # 推前檢查提早停(--fail-fast)的結束碼:不算一次推(同 pushgate.EARLY_RED)


def lifetime(rows):
    """使用 aib 至今(10-08 the maintainer:回報最下方告訴用戶用 aib 到現在省了多少)。跟推包畫面最後一行同一個算法:
    每一輪跑完的推前檢查(有「沒有 aib 要多久」、不是提早停的)加總。少等再拆成 aib 判斷的 / 同時跑的(split 同一個拆法)。
    回 {"n", "total", "actual", "less", "aib", "par", "unsplit"}(秒;unsplit = 沒記拆法的輪數);一輪都沒有 ⇒ None。"""
    done = [r for r in rows if r.get("full_s") and int(r.get("rc") or 0) != EARLY_RED]
    if not done:
        return None
    out = {"n": len(done), "total": 0.0, "actual": 0.0, "aib": 0.0, "par": 0.0,
           "unsplit": sum(1 for r in done if "by_feature" not in r)}   # 較早的輪沒記拆法 ⇒ 那幾輪少等全算在同時跑(aib 那塊偏少)
    for r in done:
        full, wall, _aib, par = split(r)
        out["total"] += full
        out["actual"] += wall
        out["par"] += par
        out["aib"] += max(0.0, full - wall) - par
    out["less"] = out["aib"] + out["par"]
    return out


def lifetime_lines(T, dur, rows):
    """回報整份最下方那一段(文字行);沒有紀錄 ⇒ []。"""
    lt = lifetime(rows)
    if lt is None:
        return []
    pct = "%.0f" % (100.0 * lt["less"] / lt["total"]) if lt["total"] > 0 else "0"
    return [T("rp_life_title", {"n": lt["n"]}),
            T("rp_life_total", {"total": dur(lt["total"]), "actual": dur(lt["actual"]), "seconds": dur(lt["less"]), "pct": pct}),
            T("rp_life_split", {"seconds": dur(lt["aib"]), "total": dur(lt["par"])})] + \
        ([T("rp_life_unsplit", {"n": lt["unsplit"]})] if lt["unsplit"] else []) + [T("rp_life_cmd")]


def write(repo, T, dur, row, commits, old_reds, unverified, out_dir=None, history=None, flaky=(), notes=()):
    """寫草稿 JSON,再用 aib-report 渲染(台北時區)。回 (txt 路徑或 None, json 路徑, aib-report 結束碼)。
    flaky(10-12 P1):這次放行的不穩定測試 ⇒ 回報最上面一行大字寫出來(JSON 草稿也帶 flaky_allowed)。
    notes(10-12 P9):推成後各台更新的結果(「Pro:已快轉到 …」),接在最上面那幾行(JSON 也帶 after_push)。"""
    tool = Path(repo) / "tools" / "report" / "aib-report.py"
    out_dir = Path(out_dir) if out_dir else Path.home() / ".ez2-aib" / "pending"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(TPE).strftime("%m%d-%H%M")
    jpath = out_dir / ("REPORT-DRAFT-ship-%s.json" % stamp)
    d = draft(T, dur, row, commits, old_reds, unverified)
    top = T("rp_flaky_top", {"what": ", ".join(sorted(flaky))}) if flaky else ""
    if flaky:
        d["flaky_allowed"] = sorted(flaky)
        d["summary"] = top + "\n" + d["summary"]
    if notes:
        d["after_push"] = list(notes)
        d["summary"] = "\n".join(list(notes) + [d["summary"]])
        top = "\n".join(([top] if top else []) + list(notes))
    jpath.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if not tool.exists():
        return None, jpath, None
    env = dict(os.environ, TZ="Asia/Taipei")
    p = subprocess.run([sys.executable, str(tool), str(jpath)], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=120, cwd=str(repo))
    if p.returncode != 0 or not p.stdout.strip():
        return None, jpath, p.returncode
    tpath = out_dir / ("REPORT-DRAFT-ship-%s.txt" % stamp)
    life = lifetime_lines(T, dur, history if history is not None else [row])
    tpath.write_text((top + "\n\n" if top else "") + p.stdout.rstrip("\n") + ("\n\n" + "\n".join(life) + "\n" if life else "\n"),
                     encoding="utf-8")
    return tpath, jpath, 0
