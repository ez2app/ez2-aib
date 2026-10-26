#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""推上主線之前的檢查:只跑「這次改動影響到的測試」,分幾份同時跑(10-04)。

為什麼:雲端 10-04 推紅主線兩次,都因為推前檢查的清單是人手挑的,剛好漏了會壞的那支
(tmp-path 棘輪、annotate-i2-guard 佔位名計數)。挑測試這件事倉裡已經有現成的:
`select_changed`(規則①~⑦ ∪ 一律跑清單 ∪ 影子續跑量到的「誰讀過改動檔」)。本檔只多兩件事:
  1. 挑出來的依上次秒數分成 --jobs 份,同一台同時跑(10-04 起刀都落在複本、樹防線認得多批同樹,才做得了);
  2. 各份結果合成一個結束碼。
每一份都走 aib test(能沿用就沿用、紅永遠重跑),所以改動小時很快,改到大家都讀的檔(字典)時會跑很多支 —— 那是對的。

★ 本檔不是倉裡的推送閘門(那個不動);它是推之前自己先跑的檢查。
★ 挑不出來(select_changed rc 非 0)⇒ 全套,不拿比較窄的集合去跑(窄集合與「都跑過了」在收據上同形)。
★ 結束碼:任一份有紅(1)或跑壞(其他非 0/4)⇒ 1;有「沒驗」(4)而沒加 --allow-unverified ⇒ 4;都綠 ⇒ 0;
  加 --fail-fast 而一出紅就停(其他沒跑完)⇒ 5(EARLY_RED)—— 只代表「有紅、其餘沒驗」,永遠不能當通過。
★ 10-06(開發檔 §5 第 4、7 項):每份裡「上次紅的」排最前、「測試原始碼寫到改動檔」其次 —— 會紅的先跑,
  配 --fail-fast 時幾分鐘就知道,不用等整輪。排序只改先後,不改跑哪些。

★ 10-06(開發檔 §5 接續):每份都帶 `aib test --resume pushgate`。機器重開、推前檢查被殺,同一行再下,
  同一棵樹上已跑綠的不再跑(日誌綁樹的內容,樹一變就全部重跑;紅的、沒跑完的照跑)。
★ 10-06(開發檔 §12 第 5 條,itsdangerous 實測抓到):通用模式開跑前,先問測試用的 python「這個專案的套件從哪裡載入」;
  不在要推的這棵樹(共用 venv 指向另一個 clone、pip install -e 指到別處)⇒ 測到的不是要推的程式 ⇒ 不跑、結束碼 4(沒驗)。
★ 10-06(開發檔 §5.1):結尾印「實跑最慢的 10 支、各佔這一輪實跑總秒數幾 %、比上次慢多少」。只報告,不改測試。
★ 10-06(開發檔 §5.7):結尾印「實跑幾支幾秒、沿用幾支、估省幾分鐘」,並記一行到 <池目錄>/ship-stats.jsonl(aib ship stats 看累計)。
  省下的只算「有上次實跑秒數」的沿用;數字是估的,畫面寫「估」。
★ 10-06 --mode generic(別的專案,aib ship 用):不用 aib 的挑法(規則①~⑦ 是 aib 自己的檔),
  清單 = 專案裡全部測試(ship_profile.discover_tests);沒受影響的由 aib test 沿用 —— 挑窄只是省時間,不挑不會漏。

用法:pushgate.py [--repo DIR] [--base origin/main] [--jobs 3] [--allow-unverified] [--dry-run] [--fail-fast]
                 [--mode aib|generic]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import signal
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_COCKPIT = Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()
DEFAULT_S = 10.0
EARLY_RED = 5
RESUME_NAME = "pushgate"


def T(key, params=None):
    return i18n_lib.t("aib.pushgate." + key, params, locale=LOC)


_DIRECT = {}   # repo → 正本挑選標成「直接用到改動檔」的測試(select 填、tiers 讀;排序用)


def select(repo, base):
    """回 (測試清單, 是否全套)。順便記下正本的 direct 旗標給 tiers 排序(不再自己用子字串猜一次)。"""
    p = subprocess.run([sys.executable, str(HERE / "select_changed.py"), "--repo", str(repo), "--base", base,
                        "--head", "HEAD"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=1800)
    try:
        rows = json.loads(p.stdout).get("selected") if p.returncode == 0 else None
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        _DIRECT.pop(str(repo), None)
        return all_tests(repo), True
    _DIRECT[str(repo)] = {r["test"] for r in rows if any(w.get("direct") for w in r.get("why") or [])}
    have = set(all_tests(repo))
    return [r["test"] for r in rows if r.get("test") in have], False


REUSE_COST_S = 0.5


_INDEX = {}


def _index(repo):
    """紀錄池讀一次就好(一輪推前檢查開始前要用三次:秒數、上次紅、上次秒數;池子上百 MB)。"""
    if repo not in _INDEX:
        import reuse_index as ri
        _INDEX[repo] = ri.build_index([], repo=repo)
    return _INDEX[repo]


def durations(repo, tests=None, will_reuse=None):
    """每支「這次大概要花幾秒」:會沿用的算 REUSE_COST_S(幾乎不花時間),其餘照上次實跑秒數。
    10-04 全套第二輪實踩:只照上次秒數分,第 0 份分到大部分「量不到、必實跑」的大測試,另兩份早早跑完在等它。
    will_reuse(test) 給測試替換;預設用 aib test 同一套判斷(判斷失敗 ⇒ 當成要實跑,寬的只是分得不準)。"""
    try:
        idx = _index(str(repo))
    except Exception:  # noqa: BLE001 - 秒數只用來分份,拿不到就用預設
        return {}
    out = {}
    for t, r in idx.latest.items():
        d = r.get("duration_s")
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            out[t] = float(d)
    if tests and will_reuse is None:
        try:
            import runner
            import shutil as _sh
            home = Path(tempfile.mkdtemp(prefix="aib-pushgate-home-", dir=runner.TMP_ROOT))
            try:
                env = runner.isolated_test_env(home, repo)
                will_reuse = lambda t: idx.decide(str(repo), t, changed=[], environ=env)[0] == "reuse"  # noqa: E731
                for t in tests:
                    if will_reuse(t):
                        out[t] = REUSE_COST_S
            finally:
                _sh.rmtree(home, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
    elif tests and will_reuse is not None:
        for t in tests:
            if will_reuse(t):
                out[t] = REUSE_COST_S
    return out


def changed_files(repo, base):
    # --no-renames:同 select_changed.changed_from_git(10-11),改名的舊路徑也算改到
    p = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--no-renames", base + "...HEAD"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    return [x for x in p.stdout.splitlines() if x.strip()] if p.returncode == 0 else []


def last_reds(repo):
    """上次紀錄是紅的測試(拿不到紀錄 ⇒ 空;只影響先後)。"""
    try:
        idx = _index(str(repo))
    except Exception:  # noqa: BLE001
        return set()
    return {t for t, r in idx.latest.items() if r.get("rc") not in (0, None)}


# 10-08 §27.5:幾秒跑完、專抓「整包推不上去」那種錯的便宜守門 ⇒ 比上次紅的還先跑(一紅就停時最早停)。
#   刀錨掃描:第 23、25 包都被別支測試的刀錨擋;佔位名:第 20 包被 annotate-i2-guard 擋。
GUARD_FIRST = ("tests/test-knife-anchor-scan-1008.py", "tests/test-annotate-i2-guard.py")


# 10-08(the maintainer:「把你最新的做法寫成程式,變成必要」):擋過推包的測試,以後一律最先跑。
#   那天一包被擋 4 次,每次都是一支幾秒就跑完的規則測試(stdin、艙名、環境變數清除、socket 路徑長度),
#   卻排在後面、跑 40 分鐘才撞到。推包被擋時 ship 記下「只在這包紅」的測試(add_blockers),這裡讀回來排第一層。
BLOCKERS_KEEP = 40


def blockers_file(repo):
    import hashlib as _h
    try:
        import reuse_index
        rid = reuse_index.repo_identity(str(repo))
    except Exception:  # noqa: BLE001
        rid = None
    key = ("repo:%s" % rid) if rid else str(Path(repo).resolve())
    return Path.home() / ".ez2-aib" / "ship" / ("blockers-%s.json" % _h.sha256(key.encode("utf-8")).hexdigest()[:16])


def load_blockers(repo):
    """擋過推包的測試(新的在前)。讀不到 ⇒ []。"""
    try:
        d = json.loads(blockers_file(repo).read_text(encoding="utf-8"))
        return [str(t) for t in d.get("tests") or []]
    except (OSError, ValueError, AttributeError):
        return []


def add_blockers(repo, tests):
    """這一包被這幾支擋 ⇒ 放到名單最前(去重,最多 BLOCKERS_KEEP 支)。寫不了就算了(不影響推包)。"""
    keep = []
    for t in list(tests) + load_blockers(repo):
        if t not in keep:
            keep.append(t)
    f = blockers_file(repo)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"tests": keep[:BLOCKERS_KEEP]}, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def tiers(repo, tests, changed, reds, direct=None):
    """-1 = 便宜守門與擋過推包的測試;0 = 上次紅或這次改到的測試檔本身;1 = 正本挑選標 direct(直接用到改動檔);2 = 其他。
    10-14:第 1 層原本自己讀測試原始碼找改動檔的路徑 / 檔名(子字串),跟正本 select_changed 的直接三條重複又判法不同 ⇒ 改讀正本的 direct;
    沒有正本結果(全套 / 一般專案)就沒有第 1 層,只影響先後、不影響挑哪些。"""
    direct = _DIRECT.get(str(repo), set()) if direct is None else set(direct)
    blockers = set(load_blockers(repo))
    out = {}
    for t in tests:
        if t in GUARD_FIRST or t in blockers:
            out[t] = -1
            continue
        if t in reds or t in changed:      # 10-08:這次新增 / 改的測試檔本身最可能紅,跟上次紅的同一層
            out[t] = 0
            continue
        out[t] = 1 if t in direct else 2
    return out


def order(shards, tier, dur):
    """每份內:最可能紅的先跑(層級小的先),同層慢的先。只改先後。"""
    return [sorted(s, key=lambda t: (tier.get(t, 2), -dur.get(t, DEFAULT_S), t)) for s in shards]


def split(tests, jobs, dur):
    """依秒數由大到小,每次放進目前最輕的那份。聯集 = 原清單、不重複。"""
    jobs = max(1, int(jobs))
    shards, load = [[] for _ in range(jobs)], [0.0] * jobs
    for t in sorted(dict.fromkeys(tests), key=lambda x: (-dur.get(x, DEFAULT_S), x)):
        i = load.index(min(load))
        shards[i].append(t)
        load[i] += dur.get(t, DEFAULT_S)
    return [s for s in shards if s]


RC_EXCLUSIVE = 5     # aib test「沒跑:別人在獨佔量耗時」(tools/verify/test_entry.py 同名常數);不是紅,也不能當綠


def combine(rcs, allow_unverified=False):
    if any(rc not in (0, 4, RC_EXCLUSIVE) for rc in rcs):
        return 1
    if any(rc == RC_EXCLUSIVE for rc in rcs):     # 10-13 a seat:有一份沒跑 ⇒ 整輪說「沒跑(別人獨佔)」,不印成紅
        return RC_EXCLUSIVE
    if any(rc == 4 for rc in rcs) and not allow_unverified:
        return 4
    return 0


KEEP_LINES = 40


def _line_re(key):
    """aib test 的某一行(依語系)的樣子:模板裡的 {x} 換成任意字。"""
    raw = i18n_lib.t("aib.test." + key, None, locale=LOC)
    return re.compile(".*?".join(re.escape(x) for x in re.split(r"\{\w+\}", raw)))


def red_outputs(text, keep=KEEP_LINES):
    """從一份的輸出挑出紅燈測試的輸出區塊(aib test 只替紅燈印「--- X 的輸出 ---」)。
    10-05:原本只印總表,紅燈輸出隨暫存檔刪掉 —— 畫面寫「輸出在最後的總表下面」卻什麼都沒有。
    每塊只留最後 keep 行(錯誤通常在尾巴),砍掉的行數照實說,全文留在檔裡。"""
    banner = _line_re("output_banner")
    stops = [_line_re(k) for k in ("row_ran", "row_reused", "row_resumed", "row_unverified", "summary")]
    blocks, cur = [], None
    for ln in text.splitlines():
        if banner.fullmatch(ln):
            cur = [ln]
            blocks.append(cur)
        elif cur is not None:
            if any(r.match(ln) for r in stops):
                cur = None
            else:
                cur.append(ln)
    out = []
    for b in blocks:
        body = b[1:]
        out.append(b[0])
        if len(body) > keep:
            out.append(T("cut", {"n": len(body) - keep}))
            body = body[-keep:]
        out.extend(body)
    return out


def _sigint_default():
    """子行程的 SIGINT 回到預設(10-09 a seat):推包都從 nohup … & 起,背景殼把 SIGINT 設成忽略,子行程會繼承;
    那樣 _stop 送的 SIGINT 沒人理,每份都白等 grace 秒才整組 SIGKILL,aib test 的 finally 也來不及收暫存、還鎖
    (test-verify-pushgate-1004 p9 在每次推包的主線副本上恆紅 20 秒就是這個)。exec 前設成預設,python 子行程就會照常收 KeyboardInterrupt。"""
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def _stop(p, grace=10.0):
    """先送 SIGINT 給 aib test 本身(讓它的 finally 收暫存副本、還鎖),等不到再整組 SIGKILL。"""
    if p.poll() is not None:
        return
    try:
        p.send_signal(signal.SIGINT)
        p.wait(timeout=grace)
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait()


PROBE = ("import importlib.util, json, sys\n"
         "out = {}\n"
         "for n in sys.argv[1:]:\n"
         "    try:\n"
         "        s = importlib.util.find_spec(n)\n"
         "        out[n] = (s.origin or (list(s.submodule_search_locations or []) or [None])[0]) if s else None\n"
         "    except Exception:\n"
         "        out[n] = None\n"
         "print(json.dumps(out))\n")


def own_packages(repo):
    """這棵樹自己的頂層套件(<套件>/__init__.py 或 src/<套件>/__init__.py;略過測試、文件、虛擬環境)。"""
    sys.path.insert(0, str(HERE.parent / "release"))
    import ship_profile
    out = []
    for base in (Path(repo), Path(repo) / "src"):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if (d.is_dir() and (d / "__init__.py").is_file() and d.name not in ship_profile.SKIP_DIRS
                    and d.name not in ("tests", "test", "docs", "src") and d.name.isidentifier()):
                out.append(d.name)
    return out


def foreign_imports(repo, python, env):
    """[(套件, 實際載入的位置)]:測試用的 python 載入的不在這棵樹裡的那些。問不到 ⇒ 空(不擋,照常跑)。"""
    names = own_packages(repo)
    if not names:
        return []
    try:
        p = subprocess.run([python, "-c", PROBE, *names], cwd=str(repo), env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        got = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    root = os.path.realpath(str(repo))
    bad = []
    for n in names:
        where = got.get(n)
        if where and not (os.path.realpath(where) + os.sep).startswith(root + os.sep):
            bad.append((n, where))
    return bad


def probe_foreign(repo):
    try:
        import runner
        import shadow_tracer
        import shutil as _sh
        home = Path(tempfile.mkdtemp(prefix="aib-pushgate-home-", dir=runner.TMP_ROOT))
        try:
            env = runner.isolated_test_env(home, repo)
            return foreign_imports(repo, shadow_tracer.test_python(str(repo), env), env)
        finally:
            _sh.rmtree(home, ignore_errors=True)
    except Exception:  # noqa: BLE001 - 問不到就不擋(照常跑;這一條只會多擋,不會多放)
        return []


SLOW_TOP = 10


def _row_re(key):
    """aib test 總表某一種行(依語系)→ 抓出測試名(與秒數)。"""
    raw = i18n_lib.t("aib.test." + key, None, locale=LOC).strip()     # 即時行前面有縮排,比對前兩邊都去掉
    parts = re.split(r"(\{\w+\})", raw)
    groups = {"{test}": r"(?P<test>\S+?)", "{rc}": r"(?P<rc>-?\d+)", "{elapsed}": r"(?P<elapsed>\d+(?:\.\d+)?)",
              "{name}": r"(?P<name>\S+?)"}
    return re.compile("".join(groups.get(x, ".+?" if re.fullmatch(r"\{\w+\}", x) else re.escape(x)) for x in parts))


def _row_ran_re():
    return _row_re("row_ran")


def savings(texts, prev):
    """這一輪:實跑幾支/幾秒、沿用(含接續)幾支、沿用的照上次實跑秒數估省了幾秒、沿用裡沒有上次秒數的幾支(不算進省下的)。"""
    # 10-06:也認每支跑完當下印的那行(live_*)—— 提早停時總表還沒印,只看總表會說「實跑 0 支」
    ran_res = [_row_ran_re()] + [_row_re("live_" + k) for k in ("done_green", "done_red")]
    keep = [_row_re(k) for k in ("row_reused", "row_resumed", "live_reuse", "live_resumed")]
    resumed_res = {id(keep[1]), id(keep[3])}
    ran, reused, resumed = {}, set(), set()
    for text in texts:
        for ln in text.splitlines():
            ln = ln.strip()
            m = next((m for m in (r.fullmatch(ln) or r.match(ln) for r in ran_res) if m), None)
            if m:
                ran[m.group("test")] = float(m.group("elapsed"))
                continue
            for r in keep:
                m = r.fullmatch(ln) or r.match(ln)
                if m:
                    reused.add(m.group("test"))
                    if id(r) in resumed_res:
                        resumed.add(m.group("test"))
                    break
    known = [prev[t] for t in reused if isinstance(prev.get(t), (int, float)) and prev.get(t) > 0]
    return {"ran": len(ran), "ran_s": round(sum(ran.values()), 1), "reused": len(reused),
            "saved_s": round(sum(known), 1), "unknown": len(reused) - len(known), "_ran": ran, "_reused": reused,
            "_resumed": resumed}


# ---- 10-06 the maintainer:從第一次起的總支數、總格數當總計,每一次推照實記「沿用幾支幾格、全部跑要多久、實際花多久」────────────
#   「格」= 一支測試檔裡的測試函式(unittest 的 test_ 方法、pytest 的 test_ 函式);檔裡一個都沒有(腳本式)算 1 格。
_CELL_CACHE = {}


def count_cells(path):
    p = Path(path)
    try:
        st_ = p.stat()
    except OSError:
        return 0
    key = (str(p), st_.st_mtime_ns, st_.st_size)
    if key not in _CELL_CACHE:
        n = 1
        if p.suffix == ".py":
            import ast
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                n = 0
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                        n += 1
                    elif isinstance(node, ast.ClassDef):
                        n += sum(1 for b in node.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                                 and b.name.startswith("test"))
                n = max(n, 1)
            except (SyntaxError, ValueError):
                n = 1
        _CELL_CACHE[key] = n
    return _CELL_CACHE[key]


_HAS_TEST_RE = re.compile(r"^\s*(?:async\s+)?def\s+test|^\s*class\s+\w*Test|\bunittest\b|\bpytest\b", re.M)


def all_tests(repo):
    """這個專案全部的測試檔(總計的母體)。
    10-07:aib 自己的倉照推前檢查挑測試的同一個母體(tests/test-*.py);以前用通用找法,
      把 tools/verify/test_entry.py、src/cockpit/mouse/hit_test.py 這種「檔名像測試、其實不是」的也算進去。
    一般專案照通用找法,但不在 tests/ 底下、檔裡一個測試都沒有的檔不算(同樣的誤認)。"""
    sys.path.insert(0, str(HERE.parent / "release"))
    import ship_profile
    try:
        aib = ship_profile.load(repo).gate_mode == "aib"
    except Exception:  # noqa: BLE001 - 讀不到設定 ⇒ 當一般專案
        aib = False
    if aib:
        return sorted("tests/" + p.name for p in (Path(repo) / "tests").glob("test-*.py"))
    # 10-26(@abb89 派,0.1.59 現況):開源 ez2-aib 倉的 aib 套件住在 aib/ 子目錄,而 aib test 會先把 --repo 正規化到 git 倉根 ⇒
    #   倉根不是 aib 套件、aib/ 才是 ⇒ --all 以前退回通用找法,只撈到倉根 tests/ 的 23 支 test_*.py,還把 aib/tools/verify/test_entry.py
    #   當測試跑。這裡認得「倉根底下的 aib/ 是 aib 套件」就回它的全套(aib/tests/test-*.py,路徑帶 aib/ 前綴,倉根與 aib/ 兩種 --repo 母體相同)。
    sub = Path(repo) / "aib"
    try:
        sub_aib = sub.is_dir() and ship_profile.load(sub).gate_mode == "aib"
    except Exception:  # noqa: BLE001 - 讀不到設定 ⇒ 不是
        sub_aib = False
    if sub_aib:
        return sorted("aib/tests/" + p.name for p in (sub / "tests").glob("test-*.py"))
    if ship_profile.declared_tests(repo)[0] is not None:
        return ship_profile.discover_tests(repo)      # 10-07:設定檔宣告的就是測試,不再用 python 的寫法判
    out = []
    for rel in ship_profile.discover_tests(repo):
        if "/tools/verify/" in "/" + rel:            # aib 隨包帶進專案的工具(test_entry.py 這種檔名像測試的執行器)不是專案的測試(10-26)
            continue
        if rel.split("/")[0] in ("tests", "test") or rel.endswith(ship_profile.JS_TEST_SUFFIXES + ("_test.go", "Test.php")):
            out.append(rel)     # JS/TS:找法本身就靠 *.test.* / __tests__ 這種明確的命名(10-07),不再用 python 的寫法判
            continue
        try:
            text = (Path(repo) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _HAS_TEST_RE.search(text):
            out.append(rel)
    return out


def ledger_fields(repo, sv, prev, shards):
    """這一輪的總計欄位:總支數/總格數、實跑與沿用(含沒被挑到、不用跑的)各幾支幾格、全部跑要幾秒(每支上次實跑秒數相加)。"""
    everything = all_tests(repo)
    cells = {t: count_cells(Path(repo) / t) for t in everything}
    ran = set(sv.get("_ran") or {})
    full = 0.0
    unknown = 0
    for t in everything:
        d = (sv.get("_ran") or {}).get(t, prev.get(t))
        if isinstance(d, (int, float)) and d > 0:
            full += d
        else:
            unknown += 1
    return {"files_total": len(everything), "cells_total": sum(cells.values()),
            "files_ran": len(ran & set(everything)), "cells_ran": sum(cells[t] for t in ran if t in cells),
            "full_s": round(full, 1), "full_unknown": unknown, "shards": max(1, len(shards)),
            "slowest_s": round(max([float((sv.get("_ran") or {}).get(t, prev.get(t)) or 0) for t in everything] or [0]), 1),
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def dur_text(seconds):
    """秒數 → 「12 秒」「3.5 分鐘」「1.2 小時」(依語系),小專案不會變成「約 0 分鐘」。"""
    s = float(seconds or 0)
    if s < 60:
        return T("unit_s", {"n": "%.0f" % s})
    if s < 3600:
        return T("unit_min", {"n": ("%.1f" % (s / 60)).rstrip("0").rstrip(".")})
    return T("unit_h", {"n": ("%.1f" % (s / 3600)).rstrip("0").rstrip(".")})


def saved_pct(sv):
    """省下的佔「全部實跑」的幾 %:沿用的(照上次實跑秒數)/(實跑的 + 沿用的)。"""
    whole = (sv.get("ran_s") or 0) + (sv.get("saved_s") or 0)
    return 100.0 * (sv.get("saved_s") or 0) / whole if whole > 0 else 0.0


def _stats_path(path=None):
    import shadow_tracer
    return Path(path or (shadow_tracer.shadow_dir_default() / "ship-stats.jsonl"))


def read_stats(repo, path=None, since=""):
    """這個倉記過的每一輪(由舊到新);since 是 ISO 時間字串,只取那之後的。repo=None ⇒ 這台機器所有專案(aib ship stats --all)。"""
    import reuse_index
    rid = None if repo is None else (reuse_index.repo_identity(str(repo)) or "")
    rows = []
    try:
        lines = _stats_path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and (rid is None or r.get("repo") == rid) and str(r.get("time", "")) >= since:
            rows.append(r)
    return rows


HOT_SHARE = 0.25                      # 被四分之一以上(有紀錄的)測試讀的檔 = 熱門檔
HOT_MIN = 20


HOT_HISTORY = 200


def change_counts(repo, n=HOT_HISTORY):
    """最近 n 個提交裡,每個檔被改過幾次。"""
    import collections
    p = subprocess.run(["git", "-C", str(repo), "log", "-n", str(n), "--format=", "--name-only"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    return collections.Counter(x for x in p.stdout.splitlines() if x.strip()) if p.returncode == 0 else {}


def hot_files(repo, top=10):
    """[(檔, 幾支測試讀它, 最近 HOT_HISTORY 個提交改過幾次)],依「讀的支數 × 改的次數」排。
    10-06 click 實測:只看讀的支數,前三名是 .editorconfig、.gitignore 這種每支都讀、卻幾乎不改的檔 —— 不改就不會害人重跑。"""
    try:
        idx = _index(str(repo))
    except Exception:  # noqa: BLE001
        return []
    changes = change_counts(repo)
    # 10-08:只數「整份讀」的(跟 causes 同一個判斷 _reads_whole)—— 翻譯檔以字串為單位讀的不算,
    #   否則 i18n/*.json 永遠排第一,而它改了多半只害讀到那幾個字串的測試重跑
    rows = [(p, sum(1 for t in ts if _reads_whole(idx, t, p)), changes.get(p, 0)) for p, ts in idx.reverse.items()
            if changes.get(p, 0) and (Path(repo) / p).is_file()]
    rows = [r for r in rows if r[1]]
    return sorted(rows, key=lambda r: (-(r[1] * r[2]), -r[1], r[0]))[:top]


def is_hot(repo, n_readers):
    try:
        total = len(_index(str(repo)).latest)
    except Exception:  # noqa: BLE001
        return False
    return n_readers >= max(HOT_MIN, HOT_SHARE * total)


def _reads_whole(idx, test, path):
    """這支測試是不是「整份」讀這個檔(改了任何一處都要重跑)。只經翻譯函式以字串為單位讀的(i18nkeys)不算 ——
    那種只有讀到的字串變了才重跑(10-07:翻譯檔被列成主因 320 支,實際以字串為單位沒有那麼多)。沒有紀錄 ⇒ 算(保守)。"""
    inputs = (idx.latest.get(test) or {}).get("inputs")
    if not inputs:
        return True
    for it in inputs:
        kind, _, rest = str(it).partition(":")
        if kind != "i18nkeys" and rest.partition(":")[0].lstrip("./") == path:
            return True
    return not any(str(it).startswith("i18nkeys:") and str(it).partition(":")[2].partition(":")[0].lstrip("./") == path
                   for it in inputs)


def causes(repo, changed, must, top=3):
    """這次要實跑的主因:改到的檔各被幾支「要實跑的」測試**整份**讀,多的在前。回 (檔, 要實跑裡整份讀的, 全部整份讀的)。"""
    try:
        idx = _index(str(repo))
    except Exception:  # noqa: BLE001
        return []
    must = set(must)
    rows = []
    for f in changed:
        whole = {t for t in idx.reverse.get(f, ()) if _reads_whole(idx, t, f)}
        rows.append((f, len(whole & must), len(whole)))
    return sorted([r for r in rows if r[1]], key=lambda r: (-r[1], r[0]))[:top]


def tracer_hit(repo, changed):
    """改到 aib 追蹤器本身的檔(只有 aib 自己的倉會有)⇒ 所有紀錄作廢、全部重跑。"""
    try:
        import replay_savings
        hit = sorted(set(changed) & replay_savings.tracer_paths())
        return hit if hit and replay_savings.is_aib_repo(str(repo)) else []
    except Exception:  # noqa: BLE001
        return []


def saved_by_feature(repo, sv, prev, selected, changed):
    """§20 第 3 項:省下的(每支上次實跑秒數)是哪個功能省的。
    select = 沒受影響、沒被挑來跑(只有 aib 模式會挑);resume = 斷掉後接續、已跑綠的不再跑;
    keyed = 沿用的裡,讀的翻譯/字典檔這次有改、只因以字串為單位才沒重跑;reuse = 其餘沿用。背景預跑先跑完的算在 reuse 裡(分不出來)。"""
    reused, resumed = set(sv.get("_reused") or ()), set(sv.get("_resumed") or ())
    sec = lambda ts: round(sum(float(prev.get(t) or 0) for t in ts), 1)  # noqa: E731
    keyed = set()
    changed = set(changed or ())
    try:
        idx = _index(str(repo))
        for t in reused - resumed:
            for it in (idx.latest.get(t) or {}).get("inputs") or ():
                kind, _, rest = str(it).partition(":")
                if kind == "i18nkeys" and rest.partition(":")[0].lstrip("./") in changed:
                    keyed.add(t)
                    break
    except Exception:  # noqa: BLE001
        keyed = set()
    unselected = set(all_tests(repo)) - set(selected) - reused
    return {"select": sec(unselected), "resume": sec(resumed), "keyed": sec(keyed), "reuse": sec(reused - resumed - keyed)}


def _keys_unchanged(repo, idx, test, path):
    """這支測試以字串為單位讀 path 時用到的那些鍵,值跟上次實跑時一樣嗎(拿現在的檔重算摘要比)。
    算不出來 ⇒ False(不算進「少跑」;審查 10-12:不能把用到改過字串、照樣要重跑的也算成少跑)。"""
    import shadow_tracer
    found = False
    for it in (idx.latest.get(test) or {}).get("inputs") or ():
        kind, _, rest = str(it).partition(":")
        if kind != "i18nkeys" or rest.partition(":")[0].lstrip("./") != path:
            continue
        found = True
        item_path, _, tail = rest.partition(":")
        kj, _, old = tail.rpartition(":")
        try:
            now = shadow_tracer.i18n_keys_digest(str(repo), item_path, json.loads(kj))
        except (ValueError, TypeError):
            now = None
        if not now or now != old:
            return False
    return found


def keyed_notes(repo, changed):
    """10-12 a seat(a seat 派):改到的 JSON 字典 ——
    ("saved", 檔, M):以字串為單位在用(宣告或自動偵測),讀它的測試裡 M 支用到的字串這次都沒改 ⇒ 這些不因這個檔重跑;
    ("hint", 檔, 0):沒在用、但內容看起來是翻譯字典(鍵 → 字串)⇒ 教怎麼在 .aib/ship.toml 宣告。"""
    import fnmatch
    import runner
    out = []
    try:
        globs = runner._keyed_files(Path(repo))
        idx = _index(str(repo))
    except Exception:  # noqa: BLE001
        return out
    for f in sorted(set(changed or ())):
        if not f.endswith(".json") or not (Path(repo) / f).is_file():
            continue
        if any(fnmatch.fnmatch(f, g) for g in globs):
            readers = idx.reverse.get(f, ())
            m = sum(1 for t in readers if not _reads_whole(idx, t, f) and _keys_unchanged(repo, idx, t, f))
            if m:
                out.append(("saved", f, m))
        elif runner.is_translation_dict(Path(repo) / f):
            out.append(("hint", f, 0))
    return out


def rerun_causes(repo, changed, fc):
    """§20 第 4 項:這一輪為什麼很多要重跑 —— 寫進總計帳。只記會讓大批測試重跑的原因:
    first(第一次建紀錄)、tracer(改到 aib 追蹤器 ⇒ 所有紀錄作廢)、deps(套件鎖檔)、wide(改到很多測試都會讀的檔:[檔, 幾支讀])。"""
    out = {}
    if fc.get("first"):
        out["first"] = True
    hit = tracer_hit(repo, changed)
    if hit:
        out["tracer"] = hit[:3]
    try:
        import replay_savings
        deps = sorted(f for f in changed if os.path.basename(f) in replay_savings.LOCKFILES)
        if deps:
            out["deps"] = deps[:3]
    except Exception:  # noqa: BLE001
        pass
    wide = [[f, n_all] for f, _n, n_all in causes(repo, changed, fc.get("must") or []) if is_hot(repo, n_all)]
    if wide:
        out["wide"] = wide
    return out


def forecast(tests, shards, dur, prev):
    """一開始的預告:(第一次?, 沿用幾支, 實跑幾支, 預計牆鐘秒數, 預計省幾秒, 預計省 %)。"""
    reusable = [t for t in tests if dur.get(t) == REUSE_COST_S]
    must = [t for t in tests if dur.get(t) != REUSE_COST_S]
    wall = max((sum(dur.get(t, DEFAULT_S) for t in s) for s in shards), default=0.0)
    saved = sum(prev.get(t, 0) or 0 for t in reusable)
    spent = sum(prev.get(t, DEFAULT_S) or DEFAULT_S for t in must)
    pct = 100.0 * saved / (saved + spent) if saved + spent > 0 else 0.0
    return {"first": not prev, "reused": len(reusable), "ran": len(must), "wall_s": wall, "saved_s": saved, "pct": pct,
            "must": must}


def eta_times(wall_s, now=None):
    """10-06 the maintainer:「預計約 XX 分鐘」要寫從幾點開始、預計幾點完成。用這台機器的時區;跨日才帶日期。"""
    start = time.time() if now is None else now
    end = start + max(0.0, float(wall_s or 0))
    fmt = "%H:%M" if time.localtime(start)[:3] == time.localtime(end)[:3] else "%m-%d %H:%M"
    return {"at": time.strftime("%H:%M", time.localtime(start)), "when": time.strftime(fmt, time.localtime(end))}


def without_aib(repo, prev, ran=None):
    """10-07 the maintainer:客戶自己的「沒有 aib、一支一支跑」要多久 = 這個專案每支測試上次實跑秒數相加(這一輪實跑的用這一輪的)。
    回 (秒數, 有秒數的支數, 沒有秒數的支數, 最慢一支的秒數)。"""
    ran = ran or {}
    known, unknown, slowest = 0.0, 0, 0.0
    total = 0.0
    for t in all_tests(repo):
        d = ran.get(t, prev.get(t))
        if isinstance(d, (int, float)) and d > 0:
            total += d
            known += 1
            slowest = max(slowest, float(d))
        else:
            unknown += 1
    return total, int(known), unknown, slowest


def print_versus(repo, prev, wall_s):
    """預告最上面:沒有 aib 要多久、有 aib 這台預計多久(幾點開始、幾點完成)、加開機器推算(多台分工還沒做 ⇒ 標推算)。"""
    serial, known, unknown, slowest = without_aib(repo, prev)
    print(T("vs_head", {"n": known + unknown}))
    if known:
        print(T("vs_serial", {"total": dur_text(serial)}))
        if unknown:
            print(T("vs_unknown", {"count": unknown}))
    else:
        print(T("vs_serial_none"))
    print(T("vs_aib", dict(eta_times(wall_s), expected=dur_text(wall_s))))
    if known and wall_s > 0:
        print(T("vs_multi", {"total": dur_text(max(slowest, wall_s / 2)), "expected": dur_text(max(slowest, wall_s / 4))}))


def aib_skipped_s(sv, prev):
    """aib 判斷不用跑的測試,一支一支跑要幾秒(上次實跑秒數相加)。有 by_feature(沒挑 + 接續 + 字串為單位 + 沿用)用它;
    沒有 ⇒ 只算沿用的那幾支。"""
    bf = sv.get("by_feature")
    if isinstance(bf, dict) and bf:
        return sum(float(v or 0) for v in bf.values())
    return sum(float(prev.get(t) or 0) for t in sv.get("_reused") or ())


def print_versus_done(repo, sv, shards_n, prev):
    """跑完對帳:沒有 aib 要等多久、有 aib 實際花多久(少等多少)、其中同時跑省下 / 影子續跑再省、從第一次到現在。"""
    serial, known, _unknown, _slow = without_aib(repo, prev, sv.get("_ran"))
    wall = float(sv.get("wall_s") or 0)
    if not known or serial <= 0:
        return
    n = max(1, shards_n)
    print(T("vs_done_head"))
    print(T("vs_done_serial", {"total": dur_text(serial)}))
    less = max(0.0, serial - wall)
    print(T("vs_done_aib", {"actual": dur_text(wall), "seconds": dur_text(less), "pct": "%.0f" % (100.0 * less / serial)}))
    # 10-08 語意統一(the maintainer:推包畫面「aib 省的」同一件事出現 8.1 分鐘 / 5 秒 / 8.3 分鐘三個數):
    #   「少等」拆成兩塊、相加恰好等於少等:aib 判斷不用跑的(那些測試一支一支跑的秒數 ÷ 份數)+ 同時跑省下的(其餘)。
    #   aib 那塊跟回報草稿、aib ship stats --at 用同一個來源(by_feature:沒挑 + 沿用 + 字串為單位)。
    skip = aib_skipped_s(sv, prev)
    aib_wall = min(less, skip / n)
    print(T("vs_done_par", {"seconds": dur_text(less - aib_wall)}))
    print(T("vs_done_reuse", {"total": dur_text(skip), "n": n, "seconds": dur_text(aib_wall)}))
    rows = [r for r in read_stats(repo) if r.get("full_s") and int(r.get("rc") or 0) != EARLY_RED]
    n = len(rows) + 1
    tot_serial = sum(float(r["full_s"]) for r in rows) + serial
    tot_wall = sum(float(r.get("wall_s") or 0) for r in rows) + wall
    print(T("vs_done_total", {"n": n, "total": dur_text(tot_serial), "actual": dur_text(tot_wall),
                              "seconds": dur_text(max(0.0, tot_serial - tot_wall))}))


def cause_texts(causes):
    """sv["causes"](rerun_causes 的結果)⇒ 說明那一行的白話清單。"""
    import shadow_explain as SE
    out = []
    c = causes or {}
    if c.get("first"):
        out.append(SE.T("cause_first"))
    for key in ("tracer", "deps"):
        if c.get(key):
            out.append(SE.T("cause_" + key, {"what": SE.T("sep").join(os.path.basename(str(f)) for f in c[key][:3])}))
    if c.get("wide"):
        out.append(SE.T("cause_wide", {"what": SE.T("sep").join("%s(%s)" % (os.path.basename(str(f)), n)
                                                              for f, n in c["wide"][:3])}))
    return out


def shadow_explain_emit(repo, sv, prev):
    """推前檢查收尾的影子續跑說明(tools/verify/shadow_explain.py)。算不出來只印一行原因,不影響結束碼。"""
    try:
        import shadow_explain
        serial, _known, _unknown, _slow = without_aib(repo, prev, sv.get("_ran"))
        reused, ran = int(sv.get("reused") or 0), int(sv.get("ran") or 0)
        total = int(sv.get("files_total") or (reused + ran))
        shadow_explain.emit("aib ship", dur_text, reused=reused, skipped=max(0, total - reused - ran), ran=ran,
                            without_s=serial, actual_s=float(sv.get("wall_s") or 0), causes=cause_texts(sv.get("causes")))
    except Exception as e:  # noqa: BLE001
        print("shadow-explain: %s: %s" % (type(e).__name__, e))


def print_forecast(repo, base, tests, shards, dur, prev, changed):
    fc = forecast(tests, shards, dur, prev)
    try:
        print_versus(repo, prev, fc["wall_s"])
    except Exception:  # noqa: BLE001 - 比較那幾行算不出來不影響推前檢查
        print(T("eta", eta_times(fc["wall_s"])))
    if fc["first"]:
        print(T("forecast_first", {"n": len(tests), "expected": dur_text(fc["wall_s"])}))
    elif fc["saved_s"] >= 1:
        print(T("forecast", {"reused": fc["reused"], "ran": fc["ran"], "expected": dur_text(fc["wall_s"]),
                             "pct": "%.0f" % fc["pct"], "total": dur_text(fc["saved_s"])}))
    else:
        print(T("forecast_all", {"n": fc["ran"], "expected": dur_text(fc["wall_s"])}))
    rows = read_stats(repo)
    saved = sum(float(r.get("saved_s") or 0) for r in rows)
    if saved >= 1:                         # 還沒省到就不印累計(the maintainer:不寫「沒省到」之類)
        whole = saved + sum(float(r.get("ran_s") or 0) for r in rows)
        print(T("cumulative", {"hours": dur_text(saved), "count": len(rows),
                               "pct": "%.0f" % (100.0 * saved / whole if whole else 0)}))
    hit = tracer_hit(repo, changed)
    if hit and fc["ran"]:                  # 追蹤器改了 ⇒ 這是唯一主因;別的檔讀者再多也不是這次全部重跑的原因
        print(T("cause_head"))
        print(T("cause_tracer", {"files": "、".join(os.path.basename(f) for f in hit[:3])}))
        return fc
    cs = causes(repo, changed, fc["must"])
    if cs and fc["ran"]:
        print(T("cause_head"))
        for f, n, _all in cs:
            print(T("cause_row", {"file": f, "tests": n}))
            if is_hot(repo, _all):
                print(T("hot_warn", {"file": f, "tests": _all}))
    for kind, f, m in keyed_notes(repo, changed):      # 10-12:翻譯字典以字串為單位 —— 在用就說少跑幾支,沒用就教怎麼開
        print(T("keyed_saved" if kind == "saved" else "keyed_hint", {"file": f, "tests": m}))
    return fc


def project_label(repo):
    """專案名稱(多專案合計時給人看):origin 網址最後一段,沒有就資料夾名。"""
    try:
        url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        url = ""
    m = re.search(r"([^/:]+?)(?:\.git)?/?$", url) if url else None
    return m.group(1) if m else Path(repo).resolve().name


def record_stats(repo, sv, rc, path=None):
    """每一輪推前檢查記一行(aib ship stats 看累計);記不下來不影響推前檢查。"""
    try:
        import fcntl
        import reuse_index
        import shadow_tracer
        f = _stats_path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        row = {k: v for k, v in sv.items() if not k.startswith("_")}
        row.update(repo=reuse_index.repo_identity(str(repo)) or "", rc=rc, time=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   name=project_label(repo))
        if os.environ.get("AIB_GATE_RUN_ID"):          # 10-12 P13:交件檢查認得出「這一筆是我這輪的」(同倉別棵樹的不算)
            row["run_id"] = os.environ["AIB_GATE_RUN_ID"]
        with open(f, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001
        pass


def slowest(texts, prev, top=SLOW_TOP):
    """各份輸出 → [(測試, 這次秒數, 佔這一輪實跑總秒數 %, 上次秒數或 None)],慢的在前。沿用的不算(沒花時間)。"""
    rr = _row_ran_re()
    ran = {}
    for text in texts:
        for ln in text.splitlines():
            m = rr.fullmatch(ln.strip())
            if m:
                ran[m.group("test")] = float(m.group("elapsed"))
    total = sum(ran.values()) or 1.0
    rows = sorted(ran.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [(t, sec, 100.0 * sec / total, prev.get(t)) for t, sec in rows]


def slow_lines(rows):
    out = [T("slow_head", {"n": len(rows)})] if rows else []
    for t, sec, pct, before in rows:
        if before:
            delta = T("slow_vs", {"pct": "%+.0f" % (100.0 * (sec - before) / before), "base": "%.1f" % before})
        else:
            delta = T("slow_new")
        out.append(T("slow_row", {"test": t, "elapsed": "%.1f" % sec, "pct": "%.0f" % pct, "what": delta}))
    return out


def _red_test_re():
    """「✗ 紅 {test}(…」那一行,{test} 抓出來(依語系)。"""
    raw = i18n_lib.t("aib.test.live_done_red", None, locale=LOC)
    parts = re.split(r"(\{\w+\})", raw)
    pat = "".join("(?P<test>.+?)" if x == "{test}" else (".*?" if re.fullmatch(r"\{\w+\}", x) else re.escape(x)) for x in parts)
    return re.compile(pat)


def early_red(lines, known_red=()):
    """10-07:一紅就停要不要停 —— 有一行紅、而且那支不在「已知主線舊紅」裡。
    已知舊紅照樣跑完、照樣報紅(最後能不能推仍由 ship 在主線上重新確認);只是不為它提早停、再整套重跑一次。"""
    rx, known = _red_test_re(), set(known_red or ())
    for ln in lines:
        m = rx.match(ln)
        if m and m.group("test").strip() not in known:
            return True
    return False


def run_shards(repo, shards, fail_fast=False, poll_s=1.0, resume=True, known_red=()):
    """回 ([(rc, 輸出, 留下的紀錄檔或 None)], 是否因紅提早停)。
    fail_fast:任一份即時印出「✗ 紅」那一行 ⇒ 停掉所有份(停掉的份 rc 記 EARLY_RED,紀錄檔留著)。"""
    procs = []
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    for i, s in enumerate(shards):
        log = tempfile.NamedTemporaryFile("w+", prefix="aib-pushgate-%d-" % i, suffix=".log", delete=False,
                                          encoding="utf-8")
        p = subprocess.Popen([sys.executable, str(HERE / "test_entry.py"), *(["--resume", RESUME_NAME] if resume else []), *s],
                             cwd=str(repo), stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True,
                             preexec_fn=_sigint_default)
        procs.append((p, log))
    try:
        return _collect_shards(procs, fail_fast, poll_s, known_red)
    finally:
        # 10-14 Mini 審查 2:每份自成一組行程(start_new_session),推包整組停 pushgate 時停不到它們 ⇒
        # 不管怎麼離開(被 SIGTERM / Ctrl-C / 例外),還在跑的份逐一停掉,不留在同一棵樹上跟下一批互撞
        for p, _log in procs:
            if p.poll() is None:
                _stop(p)


def _stop_on_signals():
    """10-14 Mini 審查 2:SIGTERM / SIGHUP 照 Ctrl-C 走(丟 SystemExit)⇒ run_shards 的 finally 停掉每一份。
    本來就是忽略的訊號不接管(nohup … & / trap "" TERM)。"""
    def handler(signum, _frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            if signal.getsignal(sig) != signal.SIG_IGN:
                signal.signal(sig, handler)
        except (ValueError, OSError):          # 不在主執行緒 ⇒ 不裝
            pass


def _collect_shards(procs, fail_fast, poll_s, known_red):
    stopped = set()
    while fail_fast and any(p.poll() is None for p, _ in procs):
        hit = False
        for p, log in procs:
            with open(log.name, encoding="utf-8", errors="replace") as fh:
                if early_red(fh, known_red):
                    hit = True
                    break
        if hit:
            for i, (p, _log) in enumerate(procs):
                if p.poll() is None:
                    _stop(p)
                    stopped.add(i)
            break
        time.sleep(poll_s)
    out = []
    for i, (p, log) in enumerate(procs):
        rc = EARLY_RED if i in stopped else p.wait()
        log.seek(0)
        out.append((rc, log.read(), log.name if rc != 0 else None))
        log.close()
        if rc == 0:
            os.unlink(log.name)
    return out, bool(stopped)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pushgate")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--known-red", action="append", default=[],
                    help="test already confirmed red on the base: --fail-fast does not stop for it (still run and reported)")
    ap.add_argument("--mode", choices=("aib", "generic"), default="aib")
    ap.add_argument("--no-red-output", action="store_true", help="list red tests but not their output (aib ship init)")
    ap.add_argument("--no-resume", action="store_true", help="run every selected test (aib ship init builds records)")
    ap.add_argument("--farm", default=os.environ.get("AIB_SHIP_FARM", ""),
                    help="@me: hand all but the first shard to trusted idle machines (ship_farm worker); env AIB_SHIP_FARM")
    a = ap.parse_args(argv)
    _stop_on_signals()
    repo = Path(a.repo).resolve()
    if a.mode == "generic":
        sys.path.insert(0, str(HERE.parent / "release"))
        import ship_profile
        # Use the same validated population as `aib test --all` and the ledger.
        # Filename discovery also includes tools such as hit_test.py and misses
        # the packaged checkout's aib/tests/test-*.py population.
        tests, full = all_tests(repo), None
        foreign = probe_foreign(repo)
        if foreign:
            for name, where in foreign:
                print(T("foreign_import", {"what": name, "path": where}))
            print(T("red", {"reason": 4}))
            return 4
    else:
        tests, full = select(repo, a.base)
    prev = durations(repo)
    dur = durations(repo, tests)
    changed = changed_files(repo, a.base)
    shards = order(split(tests, a.jobs, dur), tiers(repo, tests, changed, last_reds(repo)), dur)
    what = T("generic") if full is None else (T("full") if full else a.base)
    print(T("plan", {"n": len(tests), "items": len(shards), "what": what}))
    fc = print_forecast(repo, a.base, tests, shards, dur, prev, changed) if tests else {"wall_s": 0}
    if a.dry_run or not tests:
        return 0
    t_start = time.monotonic()
    machines = 1
    farm = None
    if a.farm and len(shards) > 1:
        import aib_plugins
        farm, _name, why = aib_plugins.get(repo, "ship.dispatcher")      # §23:多台一起跑是外掛(可開關、可另外賣)
        if farm is None:
            print(T("farm_unavailable", {"reason": why}))
    if farm is not None:
        import pool_share
        results, early, machines = farm.dispatch(sys.modules[__name__], repo, shards, a.farm,
                                                 pool_share.parse_trust(os.environ.get(pool_share.ENV_TRUST, "")),
                                                 fail_fast=a.fail_fast, resume=not a.no_resume)
    else:
        results, early = run_shards(repo, shards, fail_fast=a.fail_fast, known_red=a.known_red,
                                    **({"resume": False} if a.no_resume else {}))
    wall_s = round(time.monotonic() - t_start, 1)
    for i, (rc, text, kept) in enumerate(results):
        tail = [ln for ln in text.splitlines() if ln.startswith(("合計", "Total", "⊘", "  └ ✗")) or "✗" in ln[:6]]
        print(T("shard", {"n": i + 1, "reason": rc}))
        for ln in tail:
            print("  " + ln)
        if kept:
            print("  " + T("log_kept", {"path": kept}))
    for _rc, text, kept in results:
        if kept and not a.no_red_output:
            for ln in red_outputs(text):
                print(ln)
    for ln in slow_lines(slowest([r[1] for r in results], prev)):
        print(ln)
    sv = savings([r[1] for r in results], prev)
    sv["wall_s"] = wall_s
    try:
        sv.update(ledger_fields(repo, sv, prev, shards))
        sv["causes"] = rerun_causes(repo, changed, fc)
        sv["by_feature"] = saved_by_feature(repo, sv, prev, tests, changed)
        sv["machines"] = machines
    except Exception:  # noqa: BLE001 - 總計欄位記不下來不影響推前檢查
        pass
    if early:
        pass                                 # 提早停那一輪只跑了幾支就停 ⇒ 不報節省、不說「都實跑並建好紀錄」(10-06 第 4 包實踩:印出「這次 1 支都實跑」)
    elif sv["saved_s"] >= 1:                   # 不到 1 秒不報「約 0 秒」,照實說支數
        print(T("saved", {"ran": sv["ran"], "seconds": dur_text(sv["ran_s"]), "reused": sv["reused"],
                          "total": dur_text(sv["saved_s"]), "pct": "%.0f" % saved_pct(sv)}))
    elif sv["reused"]:                       # 有沿用、但查不到它們上次的秒數(紀錄池剛清過之類)⇒ 照實說支數
        print(T("reused_count", {"ran": sv["ran"], "reused": sv["reused"]}))
    elif sv["ran"]:
        print(T("records_built", {"n": sv["ran"]}))
    print(T("wall", {"seconds": dur_text(wall_s), "items": len(shards), "expected": dur_text(fc["wall_s"])}))
    if not early:
        try:
            print_versus_done(repo, sv, len(shards), prev)
        except Exception:  # noqa: BLE001
            pass
    shadow_explain_emit(repo, sv, prev)                  # 10-13 the maintainer:影子續跑說明一定印、一定記(提早停那一輪也印實際數字)
    rc = combine([r[0] for r in results], a.allow_unverified)
    if early:
        rc = EARLY_RED
        print(T("early_red"))
    record_stats(repo, sv, rc)
    print(T("ok") if rc == 0 else T("exclusive_skipped") if rc == RC_EXCLUSIVE else T("red", {"reason": rc}))
    return rc


if __name__ == "__main__":
    sys.exit(main())
