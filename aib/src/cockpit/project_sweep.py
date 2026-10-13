#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""專案清查 `aib project sweep`(docs/DESIGN-project-sweep-1012.md;the maintainer 10-12 15:4x 准,a seat 審)。第一版**只讀**。

收齊這個專案的活席與離艙席手上的事 → 同一件只算一筆 → 照固定順序排 → 每筆附誰負責、下一步、你不回就怎樣。
不改任何檔、不派工、不刪東西。

排序(a seat 10-12 15:37 裁,寫死):
  0 會丟東西   離艙席手上:沒提交的工作樹、本機有提交但主線沒有、也沒推到 GitHub 的分支
  1 壞掉/擋路  機器租約過期沒人收(持有者死了);主線舊紅(ship/known-base-reds);推包被擋還沒推成(ship/last-refuse 比 last-push 新)
  2 等 the maintainer 裁 艦長席佇列 kind=decide 還沒處理、也看不出已裁的卡
  3 已派未做   活席佇列裡還沒讀的信;以及「已裁、執行中」的決定卡(卡已讀未結 / 有非決定信帶同一個 thread /
             <狀態根>/pending/RECEIPT-*.md 寫到那個 thread,任一成立 —— a seat 10-12 23:5x:the maintainer 口頭回了的卡別再算他欠)
  4 各席自記   活席狀態列的主項目、最新交接檔的「待辦」
  5 離艙遺留   離艙席的未讀(照信件 id 去重)、沒留交接檔
另有「待人確認」:cherry 說有東西不在主線,但每一顆的標題主線都有(衝突解過再疊上去的常見誤報),不算會丟。

  project_sweep.py [--project UID] [--json] [--limit N]
不給 --project ⇒ 用這一席(AIB_SEAT_CODE / 這個對話)綁的專案。
輸出裡的家目錄一律遮成 ~(會進艦長室與開工簡報 —— a seat 審)。
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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import project_seats as PS  # noqa: E402

REPO_DEFAULT = Path(HERE).resolve().parents[1]
CLASS_NAMES = {0: "會丟東西", 1: "壞掉/擋路", 2: "等 the maintainer 裁", 3: "已派未做", 4: "各席自記待辦", 5: "離艙遺留",
               9: "待人確認"}
STEP_TIMEOUT_S = 20
BRANCH_BUDGET_S = 8          # 分支比對總時限;到點沒比完的記進「沒算到」(審查)


def code_in(code, text):
    """五碼要整段出現,前後不是十六進位字元(20261 不能配到 …-20261011 —— 審查)。"""
    return bool(re.search(r"(?<![0-9a-f])%s(?![0-9a-f])" % re.escape(code), text or ""))


def mask(text):
    home = os.path.expanduser("~")
    return str(text).replace(home, "~") if home and home != "/" else str(text)


def _run(argv, cwd=None, timeout=STEP_TIMEOUT_S):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return p


def _git(repo, *args):
    p = _run(["git", "-C", str(repo), *args])
    return p.stdout if p is not None and p.returncode == 0 else ""


def _dm_list(code, root):
    """這一席佇列裡還沒處理(waiting)的信,list of dict;讀不到 ⇒ None。"""
    p = _run([sys.executable, os.path.join(HERE, "dm_queue.py"), "list", "--seat", code, "--tab", "waiting"])
    if p is None or p.returncode != 0:
        return None
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return None
    return d if isinstance(d, list) else None


class Sweep:
    def __init__(self, uid, root=None, repo=None, captain=None):
        self.uid = uid
        self.root = PS.state_root(root)
        self.repo = Path(repo or REPO_DEFAULT)
        self.captain = captain
        self.items = {}
        self.missed = []
        self.seats = []

    # 同一件只算一筆:同 key 的出處併進 sources,分類取最前面的
    def add(self, cls, key, owner, what, nxt, default, source):
        it = self.items.get(key)
        if it is None:
            self.items[key] = {"class": cls, "key": key, "owner": owner, "what": mask(what), "next": nxt,
                               "default_if_no_reply": default, "sources": [mask(source)]}
            return
        it["class"] = min(it["class"], cls) if cls != 9 and it["class"] != 9 else min(it["class"], cls)
        if mask(source) not in it["sources"]:
            it["sources"].append(mask(source))

    def seats_of_project(self):
        live = set()
        try:
            # 真的活:窗格還活著的席(右欄「離艙」頁同一份判斷);死艙裡的席位檔不算活(審查:seat_now.live_codes 只看有沒有檔)
            import leftover_seats
            _rows, live = leftover_seats.scan()
            live = set(live)
        except Exception:  # noqa: BLE001
            self.missed.append("活席名單(leftover_seats.scan 讀不到)")
        reg = {s["seat"] for s in PS.seats_of(self.uid, root=self.root)}
        old = {s for s, u, _t, _src, _m in PS._old_bindings(self.root) if u == self.uid}
        for seat_id in sorted(reg | old):
            codes = PS.codes_of(seat_id, root=self.root)
            live_codes = [c for c in codes if c in live]
            self.seats.append({"seat_id": seat_id, "codes": codes, "live": live_codes,
                               "in_registry": seat_id in reg})
        return self.seats

    # ── 0 / 待人確認:分支與工作樹 ──
    def branches_and_worktrees(self):
        main = "github/main" if _git(self.repo, "rev-parse", "-q", "--verify", "refs/remotes/github/main").strip() else "origin/main"
        heads = [b for b in _git(self.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() if b]
        remote = set()
        p = _run(["git", "-C", str(self.repo), "for-each-ref", "--format=%(refname:short)", "refs/remotes"])
        if p is not None:
            remote = {r.split("/", 1)[1] for r in p.stdout.split() if "/" in r}
        main_subjects = None
        deadline = time.time() + BRANCH_BUDGET_S
        over = False
        wts, cur = [], {}
        for ln in _git(self.repo, "worktree", "list", "--porcelain").splitlines() + [""]:
            if not ln:
                if cur:
                    wts.append(cur)
                cur = {}
                continue
            k, _, v = ln.partition(" ")
            cur[k] = v
        for s in self.seats:
            if s["live"]:
                continue                       # 活席手上的是人家正在做的,不算會丟
            for code in s["codes"]:
                for b in heads:
                    if not code_in(code, b):
                        continue
                    if time.time() > deadline:
                        if not over:
                            self.missed.append("分支比對(超過 %d 秒,還有沒比的)" % BRANCH_BUDGET_S)
                        over = True
                        break        # 分支不比了,工作樹照樣看(便宜)—— 審查第二輪
                    # 遠端有同名分支、而且本機沒有比它多的 ⇒ 推過了,不會丟;本機比遠端多 ⇒ 照樣比(審查)
                    if b in remote and not any(
                            _git(self.repo, "rev-list", "--count", "%s..%s" % (r, b)).strip() not in ("", "0")
                            for r in _git(self.repo, "for-each-ref", "--format=%(refname:short)",
                                          "refs/remotes/*/" + b).split()):
                        continue
                    plus = [ln[2:] for ln in _git(self.repo, "cherry", main, b).splitlines() if ln.startswith("+ ")]
                    if not plus:
                        continue
                    if main_subjects is None:
                        main_subjects = set(_git(self.repo, "log", "--format=%s", "-n", "5000", main).splitlines())
                    subj = [_git(self.repo, "log", "-1", "--format=%s", c).strip() for c in plus]
                    if all(x in main_subjects for x in subj):
                        self.add(9, "branch:" + b, "@" + code, "分支 %s:%d 顆 cherry 說不在主線,但標題主線都有" % (b, len(plus)),
                                 "逐顆比內容,確認後刪分支", "不動", "branch")
                    else:
                        self.add(0, "branch:" + b, "@" + code, "分支 %s 有 %d 顆提交主線沒有、也沒推到遠端" % (b, len(plus)),
                                 "誰接手看內容 → 推上去或確認可丟", "不動,下次 sweep 再列", "branch")
                for w in wts:
                    path = w.get("worktree", "")
                    if not code_in(code, path) and not code_in(code, w.get("branch", "")):
                        continue
                    st = _run(["git", "-C", path, "status", "--porcelain"]) if os.path.isdir(path) else None
                    n = len([x for x in (st.stdout if st is not None else "").splitlines() if x.strip()])
                    if n:
                        self.add(0, "worktree:" + path, "@" + code, "工作樹 %s 有 %d 個未提交檔" % (path, n),
                                 "誰接手看內容 → 提交到分支或確認可丟", "不動,下次 sweep 再列", "worktree")

    # ── 1:機器租約持有者死了沒人收 ──
    def lease(self):
        tool = self.repo / "tools" / "governance" / "host_lease.py"
        p = _run([sys.executable, str(tool), "status", "--json"]) if tool.is_file() else None
        if p is None:
            self.missed.append("機器租約")
            return
        try:
            d = json.loads(p.stdout)
        except ValueError:
            self.missed.append("機器租約")
            return
        if d.get("state") == "expired":
            cur = d.get("lease") or {}
            self.add(1, "lease:" + str(cur.get("run_id")), cur.get("owner", "?"),
                     "機器租約過期沒人收(%s,%s)" % (cur.get("owner", "?"), cur.get("what", "?")),
                     "確認持有者真的停了,下一個 acquire 會接管(留痕)", "下一個要用機器的接管", "lease")

    def ship_state(self):
        """10-13 清查補強 2:主線紅 / 推包卡住(tools/release/ship.py 留在 <狀態根>/ship/ 的檔)。
          known-base-reds-*.json  上一次推包確認「主線上也紅」的測試 ⇒ 1 類(推包照放行舊紅,但主線是紅的,要有人修)
          last-refuse-*.json 比同標籤的 last-push-*.json 新 ⇒ 1 類「推包被擋」(列新紅前幾支)"""
        d = self.root / "ship"
        if not d.is_dir():
            return
        tags = _project_ship_tags(self.repo, d)
        # 只讀這個專案的(10-13 真跑:資料夾裡還有別的倉 / 治具留的 known-base-reds,讀全部會報假的主線紅);
        #   同專案的別的 clone(推包樹 ~/wt-ship-*,標籤不同)靠 ship 留的 tag-<標籤>.json 根提交對上(審查)
        for f in [d / ("known-base-reds-%s.json" % t) for t in tags]:
            if not f.is_file():
                continue
            try:
                tests = sorted(str(t) for t in (json.loads(f.read_text(encoding="utf-8")).get("tests") or []))
            except (OSError, ValueError, AttributeError):
                self.missed.append("主線舊紅 %s" % f.name)
                continue
            if tests:
                self.add(1, "main-red:" + f.name, "推包席", "主線上有 %d 支舊紅(推包照放行,但主線是紅的):%s" % (
                    len(tests), "、".join(os.path.basename(t) for t in tests[:4]) + ("…" if len(tests) > 4 else "")),
                    "派人修、修好推上去後這筆自動消失", "推包照放行舊紅、主線一直紅", "ship/" + f.name)
        for f in [d / ("last-refuse-%s.json" % t) for t in tags]:
            if not f.is_file():
                continue
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.missed.append("推包紀錄 %s" % f.name)
                continue
            pf = f.with_name(f.name.replace("last-refuse-", "last-push-"))
            try:
                pushed_at = float(json.loads(pf.read_text(encoding="utf-8")).get("at") or 0)
            except (OSError, ValueError):
                pushed_at = 0.0
            at = float(r.get("at") or 0)
            if at <= pushed_at:
                continue
            new = [os.path.basename(str(t)) for t in (r.get("new") or [])]
            when = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "?"
            self.add(1, "ship-refused:" + f.name, "推包席", "推包被擋(%s,%s,rc=%s):新紅 %d 支%s" % (
                r.get("branch") or "?", when, r.get("rc"), len(new), (":" + "、".join(new[:4]) + ("…" if len(new) > 4 else "")) if new else ""),
                "修好紅的、重推;推成之後這筆自動消失", "包一直卡著不上主線", "ship/" + f.name)

    # ── 2 / 3 / 5:佇列 ──
    def _receipt_text(self, limit=400, max_bytes=65536):
        """<狀態根>/pending/RECEIPT-*.md 最新 limit 份的內文(每份最多 max_bytes),拿來找「這個 thread 已經有人執行、留了收據」。"""
        files = sorted(glob.glob(str(self.root / "pending" / "RECEIPT-*.md")), key=lambda f: -os.path.getmtime(f))[:limit]
        out = []
        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    out.append((os.path.basename(f), fh.read(max_bytes)))
            except OSError:
                continue
        return out

    def queues(self):
        # 先讀各席佇列(下面兩段都要用):派工信帶的 thread 是「卡已裁、在執行」的訊號之一
        seat_rows = {}
        for s in self.seats:
            for code in (s["live"] or s["codes"][:3]):
                if code not in seat_rows:
                    seat_rows[code] = _dm_list(code, self.root)
        if self.captain:
            rows = _dm_list(self.captain, self.root)
            if rows is None:
                self.missed.append("艦長席 @%s 的決定卡" % self.captain)
            else:
                threads = {str(r.get("thread")) for rr in list(seat_rows.values()) + [rows] for r in (rr or [])
                           if r.get("thread") and r.get("kind") != "decide"}
                receipts = None
                for r in rows:
                    if r.get("kind") != "decide" or r.get("done_at"):
                        continue
                    first = next((x.strip() for x in (r.get("msg") or "").splitlines() if x.strip()), "")
                    th = str(r.get("thread") or "")
                    why = []
                    if r.get("read_at"):
                        why.append("卡已讀未結")
                    if th and th in threads:
                        why.append("有派工信帶同一個 thread")
                    if th and not why:
                        if receipts is None:
                            receipts = self._receipt_text()
                        hit = next((name for name, text in receipts if th in text), None)
                        if hit:
                            why.append("收據 " + hit)
                    if why:
                        self.add(3, "decided:" + str(r.get("id")), "執行席", "已裁、執行中:" + re.sub(r"^要你決定[::]?", "", first)[:120] + "(" + "、".join(why) + ")",
                                 "執行席做完回報,艦長把卡標完成", "照執行中,不再問 the maintainer", "decide@%s" % self.captain)
                    else:
                        self.add(2, "dm:" + str(r.get("id")), "the maintainer", first[:140],
                                 "the maintainer 在決定窗按", "照卡上寫的預設", "decide@%s" % self.captain)
        seen = set()
        for s in self.seats:
            for code in (s["live"] or s["codes"][:3]):
                rows = seat_rows.get(code)
                if rows is None:
                    self.missed.append("@%s 的佇列" % code)
                    continue
                fresh = []
                for r in rows:
                    rid = str(r.get("id"))
                    if rid in seen or r.get("read_at") or r.get("kind") == "decide":
                        continue
                    seen.add(rid)                 # 同一封在好幾艙 / 好幾個碼看到只算一次(照信件 id 去重)
                    fresh.append(r)
                if not fresh:
                    continue
                # 一席一筆(10-12 真跑:a seat 46 封逐封列出來太吵),列最舊那封與寄件者分布
                fresh.sort(key=lambda r: r.get("ts") or 0)
                who = {}
                for r in fresh:
                    w = r.get("sender_seat") or re.sub(r"^@", "", str(r.get("sender") or "?")).split("(")[0] or "?"
                    who[w] = who.get(w, 0) + 1
                senders = "、".join("@%s %d" % (w, n) for w, n in sorted(who.items(), key=lambda x: -x[1])[:4])
                oldest = fresh[0].get("when") or "?"
                first = next((x.strip() for x in (fresh[0].get("msg") or "").splitlines() if x.strip()), "")[:80]
                if s["live"]:
                    self.add(3, "unread:" + code, "@" + code, "%d 封沒讀(最舊 %s;%s);最舊那封:%s" % (len(fresh), oldest, senders, first),
                             "這一席讀了處理、標已讀", "留在待審框", "dm@%s" % code)
                else:
                    self.add(5, "unread:" + code, "@" + code, "離艙席 %d 封沒讀(最舊 %s;%s);最舊那封:%s" % (len(fresh), oldest, senders, first),
                             "看要不要轉給活席", "不動", "dm@%s" % code)

    # ── 4 / 5:狀態列與交接檔 ──
    def notes(self):
        for s in self.seats:
            if s["live"]:
                for code in s["live"]:
                    try:
                        rec = json.loads((self.root / "state" / "seat-now" / (code + ".json")).read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        rec = {}
                    if rec.get("item"):
                        state = {"reply": "等回應", "decide": "等 the maintainer"}.get(rec.get("state"), "忙碌中")
                        self.add(4, "now:" + code, "@" + code, "狀態列【%s】%s(%s/%s)" % (state, rec.get("item"),
                                                                                       rec.get("steps_done", "?"), rec.get("steps_total", "?")),
                                 "照狀態列做", "照狀態列做", "seat-now")
            hf = []
            for code in s["codes"]:
                hf += glob.glob(str(self.root / "pending" / ("HANDOFF-%s-*.md" % code)))
            if hf:
                latest = max(hf, key=os.path.getmtime)
                for line in _todo_lines(latest)[:8]:
                    self.add(4 if s["live"] else 5, "todo:%s:%s" % (s["seat_id"], line[:60]), "@" + (s["codes"] or ["?"])[0],
                             "交接待辦:" + line, "接手的席照做或銷項", "不動", os.path.basename(latest))
            elif not s["live"] and s["codes"]:
                self.add(5, "nohandoff:" + s["seat_id"], "@" + s["codes"][0], "離艙沒留交接檔",
                         "看第二大腦有沒有交接,沒有就記一筆", "不動", "pending/")

    def run(self):
        t0 = time.time()
        for step in (self.seats_of_project, self.branches_and_worktrees, self.lease, self.ship_state, self.queues, self.notes):
            try:
                step()
            except Exception as e:  # noqa: BLE001
                self.missed.append("%s(%s)" % (step.__name__, type(e).__name__))
        items = sorted(self.items.values(), key=lambda it: (it["class"] if it["class"] != 9 else 6, it["key"]))
        return {"schema": "aib-project-sweep/v1", "project": self.uid, "title": _title(self.uid, self.root),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "took_s": round(time.time() - t0, 1),
                "seats": self.seats, "items": items, "missed": self.missed}


def _ship_tag(repo):
    """tools/release/ship.py _known_base_reds_file 的同一套標籤:sha256("repo:" + reuse_index.repo_identity)[:16];
    取不到身分 ⇒ 依路徑。不 import ship.py(太重),只抄這一行規則 —— test-project-sweep W13 釘兩邊相同。"""
    import hashlib
    try:
        vd = str(Path(repo) / "tools" / "verify")
        if vd not in sys.path:
            sys.path.insert(0, vd)
        import reuse_index
        rid = reuse_index.repo_identity(str(repo))
    except Exception:  # noqa: BLE001
        rid = None
    key = ("repo:%s" % rid) if rid else str(Path(repo).resolve())
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _project_ship_tags(repo, d):
    """這個專案在 ship/ 底下的倉標籤:自己的(_ship_tag),加上 tag-<標籤>.json 的根提交跟自己有交集的。"""
    own = _ship_tag(repo)
    tags = [own]
    try:
        p = subprocess.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True,
                           timeout=STEP_TIMEOUT_S, stdin=subprocess.DEVNULL)
        mine = set(p.stdout.split()) if p.returncode == 0 else set()
    except (OSError, subprocess.SubprocessError):
        mine = set()
    for f in sorted(Path(d).glob("tag-*.json")):
        t = f.name[len("tag-"):-len(".json")]
        if t in tags or not mine:
            continue
        try:
            roots = set(json.loads(f.read_text(encoding="utf-8")).get("roots") or [])
        except (OSError, ValueError, AttributeError):
            continue
        if roots & mine:
            tags.append(t)
    return tags


def _todo_lines(path):
    """交接檔裡「待辦」那段的條目(- 開頭);沒有那段 ⇒ []。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    out, on = [], False
    for ln in text.splitlines():
        if re.match(r"^\s*(#+\s*)?待辦", ln):
            on = True
            rest = re.sub(r"^\s*(#+\s*)?待辦[::]?\s*", "", ln).strip()
            if rest and rest not in ("無",):
                out.append(rest)
            continue
        if on:
            if re.match(r"^\s*#", ln) or (not ln.strip() and out):
                break
            m = re.match(r"^\s*[-*]\s+(.*)", ln)
            if m:
                out.append(m.group(1).strip())
    return out


def _title(uid, root):
    proj = PS.read(root)["projects"].get(uid) or {}
    if proj.get("title"):
        return proj["title"]
    try:
        return json.loads((PS.state_root(root) / "projects" / uid / "card.json").read_text(encoding="utf-8")).get("title") or ""
    except (OSError, ValueError):
        return ""


def my_project(root=None):
    """這一席綁的專案:AIB_SEAT_CODE → 席位檔 → seat_id → 正本;正本沒有 ⇒ 席位檔上的 project_uid。"""
    code = os.environ.get("AIB_SEAT_CODE", "")
    if not code:
        try:
            import seat_now
            code = seat_now.self_code() or ""
        except Exception:  # noqa: BLE001
            code = ""
    if not code:
        return None
    import seat_address
    files = glob.glob(str(PS.state_root(root) / "instances" / "*" / "seats" / "*.json"))
    # 同一個席碼可能在好幾艙都有席位檔(舊艙留下的、沒綁專案):新的先看,第一個有答案的才算(10-12 實測 a seat 兩份)
    for f in sorted(files, key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and seat_address.code_of(rec) == code:
            uid = PS.project_of(str(rec.get("seat_id") or ""), root=root) or rec.get("project_uid")
            if uid:
                return uid
    return None


def captain_code(root=None):
    try:
        d = json.loads((PS.state_root(root) / "state" / "captain-room" / "config.json").read_text(encoding="utf-8"))
        return str(d.get("ship_seat") or "") or None
    except (OSError, ValueError):
        return None


def render(res, limit=None, brief=False):
    """brief(開工簡報用):沒有任何一筆 ⇒ 空字串;不放別席信件 / 決定卡的內文,只放誰、幾筆(審查:內文是別席的)。"""
    if brief:
        if not res["items"]:
            return ""
        cards = [it for it in res["items"] if it["class"] == 2]
        decided = [it for it in res["items"] if it["key"].startswith("decided:")]
        rest = [_brief_item(it) for it in res["items"] if it["class"] != 2 and not it["key"].startswith("decided:")]
        if decided:    # 已裁、執行中的卡:同樣不放卡的內文,合成一筆
            rest.append({"class": 3, "key": "decided-cards", "owner": "執行席", "what": "%d 張決定卡已裁、執行中" % len(decided),
                         "next": "執行席做完回報,艦長把卡標完成", "default_if_no_reply": "照執行中,不再問 the maintainer",
                         "sources": sorted({x for it in decided for x in it["sources"]})})
        if cards:      # 決定卡在簡報裡合成一筆(內容是別席寫給 the maintainer 的,看決定窗)
            rest.insert(0, {"class": 2, "key": "decide-cards", "owner": "the maintainer", "what": "%d 張決定卡等你按(內容看艦長室決定窗)" % len(cards),
                            "next": "the maintainer 在決定窗按", "default_if_no_reply": "照每張卡寫的預設",
                            "sources": sorted({x for it in cards for x in it["sources"]})})
        res = dict(res, items=sorted(rest, key=lambda it: (it["class"] if it["class"] != 9 else 6, it["key"])))
    out = ["專案 %s %s · 席 %d(活 %d)· %s · %.1f 秒" % (res["title"], res["project"], len(res["seats"]),
                                                     sum(1 for s in res["seats"] if s["live"]), res["at"], res["took_s"])]
    shown = 0
    for cls in (0, 1, 2, 3, 4, 5, 9):
        rows = [it for it in res["items"] if it["class"] == cls]
        if not rows or (limit is not None and shown >= limit):
            continue
        out.append("%s %s(%d)" % ("" if cls == 9 else cls, CLASS_NAMES[cls], len(rows)))
        for it in rows:
            if limit is not None and shown >= limit:
                break
            out.append("  %s %s" % (it["owner"], it["what"]))
            out.append("     下一步:%s · 你不回:%s · 出處:%s" % (it["next"], it["default_if_no_reply"], ",".join(it["sources"])))
            shown += 1
    if limit is not None and len(res["items"]) > shown:
        out.append("…還有 %d 筆(aib project sweep 看全部)" % (len(res["items"]) - shown))
    out.append("沒算到的:" + ("、".join(res["missed"]) if res["missed"] else "無"))
    return "\n".join(mask(x) for x in out)


def _brief_item(it):
    if it["key"].startswith("unread:"):
        return dict(it, what=re.sub(r";最舊那封:.*$", "", it["what"]))
    return it


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib project sweep")
    ap.add_argument("--project", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--brief", action="store_true", help="開工簡報:沒事就不印;不放別席信件內文")
    a = ap.parse_args(argv)
    uid = a.project or my_project()
    if not uid:
        print("找不到這一席綁的專案;用 --project proj:… 指定", file=sys.stderr)
        return 2
    res = Sweep(uid, repo=a.repo, captain=captain_code()).run()
    if a.json:
        print(mask(json.dumps(res, ensure_ascii=False)))
    else:
        text = render(res, a.limit, brief=a.brief)
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
