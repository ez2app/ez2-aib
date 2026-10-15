#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""每席一台電腦(S1):一支只讀的資料生產者,印一份 aib-seat-screen/v1 JSON。

設計稿:docs/DESIGN-seat-screen-86ff0-1013.md §4(4.1~4.4)、§10.1、§21。

    seat_screen.py [--seat <席碼> | --seat-id <seat_id>] [--cabin <艙>] [--fresh] [--brief] [--charts] [--dm] [--plan]
    seat_screen.py todo add --text <文字> [--group self|doing|human|others|now] [--seat …|--seat-id …]
    seat_screen.py todo done --id <t_…> [--seat …|--seat-id …]
    seat_screen.py todo list [--seat …|--seat-id …]

規矩:
- 鍵用 seat_id(身分),席碼只拿來查 dm / aib now(那兩處本來就以席碼為鍵)。
- 找席位只在本艙 instances/<艙>/seats(§10.1:死艙留著同席碼的舊席位檔);只有量不到是哪一艙時,才在整個狀態根的各艙找、取最新。
- 感測器各自一支小函式,只讀、各自逾時;量不到 ⇒ level=unknown + missing 記一筆機器鍵,不拋例外。
- 不連網、不 fetch;子程序一律帶逾時;不掃整個狀態根。
- 輸出只有機器鍵與數字;人看的字一律照抄現有資料(dm 內文、人格卡、aib now、專案卡、commit 標題),本檔不寫說明文。
- 快取:<狀態根>/instances/<艙>/state/seat-screen/<seat_id>.json(原子寫);沒帶 --fresh 且 15 秒內 ⇒ 直接回快取。
- 待辦帳本:<狀態根>/todo/<seat_id>.jsonl,只追加(done 也是一列);dm / 決定卡 / aib now 的正本在原處,不抄進帳本。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import secrets
import subprocess
import sys
import time

if __name__ == "__main__":
    # 直接跑本檔時,seat_screen_fleet 的 `import seat_screen` 要拿到同一份模組
    # (否則有兩份 _PENDING / _LIVE / git 名額:收尾等不到、殺不到艦長段的子程序,git 同時 6+6 支)
    sys.modules.setdefault("seat_screen", sys.modules["__main__"])

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atomic_json  # noqa: E402
import seat_address  # noqa: E402
import seats_dir_lib  # noqa: E402

SCHEMA = "aib-seat-screen/v1"
CACHE_TTL_S = 15          # §4.1:15 秒內重用
TREE_TTL_S = 60           # §4.4:同一棵樹 60 秒快取,HEAD 變了才重算
SENSOR_TIMEOUT_S = 2.0    # §4.4:感測器各自逾時
DEADLINE_S = 2.5          # 整輪最多等這麼久;慢的寫進 missing
TODO_MAX = 30
DETAIL_MAX = 5
TEXT_MAX = 160
TODO_GROUPS = ("now", "doing", "human", "others", "self")
SEAT_ID_RE = re.compile(r"^[0-9a-f]{4,8}$")    # 與 bind_one.SEAT_ID_RE / project_seats._SEAT_RE 同形
TODO_ID_RE = re.compile(r"^t_[0-9a-f]{8}$")

# §4.3 第 1 層:職位預設(v1 只有已實作的感測器;red_lines 每個角色都有,綁了專案就加 project_state)
ROLE_SENSORS = {
    "root": ("push_chain", "machine_lease", "inbox"),
    "integrator": ("push_chain", "machine_lease", "main_head"),
    "dev": ("my_trees", "machine_lease", "main_head", "inbox"),
    "pm": ("my_trees", "push_chain", "main_head", "inbox", "machine_lease"),   # §22.4 此刻:機器誰在用
    "qa": ("inbox",),
    "bot": ("inbox",),
}
ROLE_ALIASES = (("root", "root"), ("integ", "integrator"), ("dev", "dev"), ("pm", "pm"),
                ("qa", "qa"), ("accept", "qa"), ("bot", "bot"))
ALWAYS = ("red_lines",)


def role_family(role):
    r = str(role or "").lower()
    for prefix, fam in ROLE_ALIASES:
        if r.startswith(prefix):
            return fam
    return "dev"


def sensors_for(role, has_project):
    names = list(ROLE_SENSORS[role_family(role)]) + list(ALWAYS)
    if has_project:
        names.append("project_state")
    return names


def _first_line(ws):
    """專案卡 work_state.now 第一行;形狀不對一律 None,不拋例外。"""
    try:
        lines = str((ws or {}).get("now") or "").splitlines()
    except Exception:  # noqa: BLE001
        return None
    return _clip(lines[0]) if lines else None


def _clip(text, n=TEXT_MAX):
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 子程序與背景工作(收尾時有上限地等、殺掉剩下的子程序)
import threading  # noqa: E402

_LIVE = set()                      # 還在跑的子程序(收尾時殺掉,不留孤兒)
_LIVE_LOCK = threading.Lock()
_PENDING = []                      # 背景工作(會寫快取的):收尾時最多等 EXIT_WAIT_S
_GIT_SLOTS = threading.Semaphore(6)    # 全模組同時最多 6 支 git(別席也在用這台機器)
EXIT_WAIT_S = 3.0


_CLOSING = False                   # finish() 開始收尾後設成 True(在 _LIVE_LOCK 裡):之後不再起新的子程序


def _killpg(p):
    """子程序開在自己的 session(start_new_session):整組殺,連它再起的孫程序一起(host_lease / dm_queue / cabin_resolve.sh)。"""
    import signal
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def _reap(p):
    """殺完收屍:最多等 1 秒;管線若被孫程序握著還不放,就關掉管線、不再等。"""
    try:
        p.communicate(timeout=1)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        for fh in (p.stdout, p.stderr):
            try:
                if fh:
                    fh.close()
            except OSError:
                pass


def _run(argv, timeout=SENSOR_TIMEOUT_S, cwd=None):
    """子程序一律帶逾時、不吃 stdin;回 (rc, stdout)。逾時:在等待的這一端整組殺 + 收屍,再拋 TimeoutExpired
    (由呼叫端處理成 unknown)。行程登記在 _LIVE,收尾時還沒結束的整組殺掉;收尾開始後不再起新的。"""
    if _CLOSING:
        raise subprocess.TimeoutExpired(argv[:1], 0)
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         cwd=cwd, text=True, start_new_session=True)
    with _LIVE_LOCK:
        closing = _CLOSING
        if not closing:
            _LIVE.add(p)
    if closing:                        # Popen 回來到登記之間收尾已開始:立刻整組殺
        _killpg(p)
        _reap(p)
        raise subprocess.TimeoutExpired(argv[:1], 0)
    try:
        out, _err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _killpg(p)
        _reap(p)
        raise
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(p)
    return p.returncode, out


def _git_raw(repo, *args, timeout=SENSOR_TIMEOUT_S):
    """git 一律 --no-optional-locks(status 不去搶別席的 index.lock);全模組同時最多 6 支。"""
    t0 = time.monotonic()
    if not _GIT_SLOTS.acquire(timeout=timeout):      # 等名額也算在逾時裡:前面卡住的 git 不會連環拖垮後面的
        raise subprocess.TimeoutExpired(["git", *args[:1]], timeout)
    try:
        return _run(["git", "--no-optional-locks", "-C", repo, *args],
                    timeout=max(0.1, timeout - (time.monotonic() - t0)))
    finally:
        _GIT_SLOTS.release()


def _git(repo, *args, timeout=SENSOR_TIMEOUT_S):
    rc, out = _git_raw(repo, *args, timeout=timeout)
    return out.strip() if rc == 0 else None


class _Pool(cf.ThreadPoolExecutor):
    """submit_cache():會寫快取的工作(工作樹、影子帳本彙整)才登記,收尾 finish() 有上限地等它們寫完;
    一般感測器不登記 —— 逾時的感測器不該讓收尾多等。"""

    def submit_cache(self, fn, *args, **kwargs):
        fu = self.submit(fn, *args, **kwargs)
        _PENDING.append(fu)
        return fu


def finish(rc):
    """行程收尾:最多等 EXIT_WAIT_S 讓背景工作寫完快取 → 殺掉還活著的子程序 → flush → os._exit。
    逾時沒收完的工作執行緒不能拖住行程結束。"""
    try:
        try:
            sys.stdout.flush()           # 輸出先送出去,再等快取
        except Exception:  # noqa: BLE001
            pass
        pend = [f for f in _PENDING if not f.done()]
        if pend:
            cf.wait(pend, timeout=EXIT_WAIT_S)
        global _CLOSING
        with _LIVE_LOCK:
            _CLOSING = True              # 先關門再點名:之後任何工作執行緒都起不了新的子程序
            live = list(_LIVE)
        for p in live:
            _killpg(p)
    finally:
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(rc if isinstance(rc, int) else 1)


def repo_root():
    """這支檔所在的倉(worktree 與正本共用 refs);EZ2_AIB_ROOT 明給的優先(與 seat-info 同順位)。"""
    return os.environ.get("EZ2_AIB_ROOT") or os.path.abspath(os.path.join(HERE, "..", ".."))


# ---------------------------------------------------------------- 找席位
def _hub_socket(environ):
    # 艙名單裡的變數只在這支讀(test-cabin-single-source-names:同一函式不混讀名單外的變數)
    return environ.get("EZ2_AIHUB_SOCKET") or ""


def _tmux_socket(environ):
    tmux = environ.get("TMUX") or ""
    return tmux.split(",")[0].rsplit("/", 1)[-1] if tmux else ""


def cabin_from_env(environ=None):
    """本艙:EZ2_AIHUB_SOCKET 或 $TMUX 的 socket 名 aihub-<艙>(與 seat-info 同一條;不自己拼艙名)。"""
    environ = os.environ if environ is None else environ
    sock = os.path.basename(_hub_socket(environ) or _tmux_socket(environ))
    return sock[len("aihub-"):] if sock.startswith("aihub-") else None


def _seat_files(root, cabin):
    pat = os.path.join(root, "instances", cabin if cabin else "*", "seats", "w*.json")
    for f in glob.glob(pat):
        d = _load(f)
        if d and not d.get("retired") and not d.get("exited_at_epoch"):
            yield f, d


def find_seat(root, cabin=None, *, seat_id=None, code=None, session_uuid=None):
    """回 (席位檔路徑, 紀錄, 艙, 來源)。知道是哪一艙 ⇒ 只在本艙找(找不到就是找不到,不退到別艙);
    只有艙量不到時,才在整個狀態根的各艙找、取 started_epoch 最新的那份。"""
    def match(d):
        if seat_id:
            return d.get("seat_id") == seat_id
        if session_uuid:
            return str(d.get("session_uuid") or "").lower() == session_uuid.lower()
        return bool(code) and seat_address.code_of(d) == code

    scopes = [(cabin, "cabin")] if cabin else []
    if not cabin:
        scopes.append((None, "all_cabins"))
    for scope, src in scopes:
        best = None
        for f, d in _seat_files(root, scope):
            if match(d):
                k = d.get("started_epoch") or 0
                if best is None or k > best[0]:
                    best = (k, f, d)
        if best:
            return best[1], best[2], best[2].get("cabin_id") or scope, src
    return None, None, cabin, None


def resolve(args, root):
    cabin = getattr(args, "cabin", None) or cabin_from_env()
    if getattr(args, "seat_id", None):
        return find_seat(root, cabin, seat_id=args.seat_id)
    if getattr(args, "seat", None):
        code = seat_address.uuid_tail5(str(args.seat).lstrip("@"))
        return find_seat(root, cabin, code=code) if code else (None, None, cabin, None)
    try:
        import seat_now
        envs = seat_now.SESSION_ENVS
    except Exception:          # noqa: BLE001
        envs = ("AIB_RESUME_SESSION_UUID",)
    for k in envs:
        u = os.environ.get(k)
        if u:
            hit = find_seat(root, cabin, session_uuid=u)
            if hit[1]:
                return hit
    return None, None, cabin, None


# ---------------------------------------------------------------- 取料(每支只讀、各自逾時)
def fetch_lease(repo):
    rc, out = _run([sys.executable, os.path.join(repo, "tools", "governance", "host_lease.py"), "status", "--json"])
    if not out.strip().startswith("{"):       # 被佔用時 rc=4 但 JSON 照印
        raise RuntimeError("rc=%d" % rc)
    return json.loads(out)


def fetch_dm(code):
    rows = []
    for tab in ("waiting", "read"):
        rc, out = _run([sys.executable, os.path.join(HERE, "dm_queue.py"), "list", "--seat", code, "--tab", tab])
        if rc != 0:
            raise RuntimeError("rc=%d" % rc)
        part = json.loads(out) if out.strip() else []
        for m in part if isinstance(part, list) else []:
            if isinstance(m, dict):
                rows.append(dict(m, _tab=tab))
    return [m for m in rows if not m.get("done_at") and not m.get("archived_at") and not m.get("superseded_by")]


def _etime(t):
    d, _, hms = t.rpartition("-")
    parts = [int(x) for x in hms.split(":")]
    sec = parts[-1] + (parts[-2] if len(parts) > 1 else 0) * 60 + (parts[-3] if len(parts) > 2 else 0) * 3600
    return sec + (int(d) * 86400 if d else 0)


def fetch_ship_procs():
    """真的 python ship.py 行程(排隊的殼不算;測試治具在暫存目錄起的不算)。macOS ps 沒有 etimes,只有 etime。"""
    rc, out = _run(["ps", "-axo", "pid=,etime=,command="])
    if rc != 0:
        raise RuntimeError("rc=%d" % rc)
    procs = []
    for ln in out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) != 3:
            continue
        cmd = parts[2]
        if "ship.py" not in cmd or not re.search(r"ship\.py\s+(package|release)\b", cmd):
            continue
        if cmd.startswith(("/bin/zsh", "/bin/bash", "zsh", "bash", "sh ")) or "/tmp" in cmd or "/var/folders" in cmd:
            continue
        try:
            pid, el = int(parts[0]), _etime(parts[1])
        except ValueError:
            continue
        mb = re.search(r"--branch[= ](\S+)", cmd)
        br = mb.group(1) if mb else None
        procs.append({"pid": pid, "elapsed_s": el, "branch": br})
    return procs


def fetch_main(repo):
    """主線 ref:URL 指向 github.com 的 remote 的 main(沒有就 origin/main,再沒有就本地 main);不 fetch。"""
    rem = None
    for ln in (_git(repo, "config", "--get-regexp", r"^remote\..*\.url$") or "").splitlines():
        key, _, url = ln.partition(" ")
        name = key[len("remote."):-len(".url")]
        if "github.com" in url and (rem is None or name == "origin"):
            rem = name
    for ref in ([rem + "/main"] if rem else []) + ["origin/main", "main"]:
        out = _git(repo, "log", "-1", "--format=%H%x1f%ct%x1f%s", ref)
        if out:
            sha, ct, subj = out.split("\x1f", 2)
            return {"ref": ref, "sha": sha, "ct": int(ct), "subject": subj}
    raise RuntimeError("no_main_ref")


def _tree_cache_path(cache_dir, seat_id):
    return os.path.join(cache_dir, seat_id + ".trees.json")


def fetch_trees(repo, code, main, cache_dir, seat_id, now):
    """席碼出現在分支名或路徑裡的工作樹 = 我的樹(seat-info --dev 同一條)。
    `worktree list --porcelain` 已帶每棵樹的 HEAD;(HEAD, 主線) 沒變且 60 秒內 ⇒ 用快取,不再逐樹跑 git。"""
    out = _git(repo, "worktree", "list", "--porcelain")
    if out is None:
        raise RuntimeError("worktree_list")
    cache = (_load(_tree_cache_path(cache_dir, seat_id)) or {}).get("trees") or {} if cache_dir else {}
    trees, fresh_cache, cands = [], {}, []
    for blk in out.split("\n\n"):
        lines = blk.splitlines()
        path = next((l[9:] for l in lines if l.startswith("worktree ")), None)
        head = next((l[5:] for l in lines if l.startswith("HEAD ")), None)
        br = next((l[7:] for l in lines if l.startswith("branch ")), None)
        br = br[len("refs/heads/"):] if br and br.startswith("refs/heads/") else br
        if not path or not head or (code not in (br or "") and code not in path):
            continue
        if not os.path.isdir(path):
            continue
        cands.append((path, head, br))

    lock, done = threading.Lock(), {}

    def measure(path, head):
        c = cache.get(path)
        if c and c.get("head") == head and c.get("main") == main["sha"] and now - (c.get("at") or 0) < TREE_TTL_S:
            return dict(c, _hit=True)
        lr = _git(path, "rev-list", "--left-right", "--count", "%s...HEAD" % main["sha"])
        st = _git(path, "status", "--porcelain")
        behind, ahead = (int(x) for x in lr.split()) if lr and len(lr.split()) == 2 else (None, None)
        rec = {"head": head, "main": main["sha"], "at": now, "ahead": ahead, "behind": behind,
               "dirty": None if st is None else bool(st.strip())}
        if cache_dir and lr is not None and st is not None:
            # 量完一棵就寫一次(機器忙時全部量完前可能被收尾砍掉;寫過的樹下一輪就是熱的)
            with lock:
                done[path] = rec
                try:
                    atomic_json.write_json(_tree_cache_path(cache_dir, seat_id), {"trees": dict(cache, **done)})
                except Exception:  # noqa: BLE001
                    pass
        return rec

    with cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(cands)))) as pool:     # 逐樹 git 並行(冷的時候最貴)
        recs = list(pool.map(lambda x: measure(x[0], x[1]), cands))
    for (path, head, br), rec in zip(cands, recs):
        hit = bool(rec.pop("_hit", False))
        fresh_cache[path] = rec
        if br is None and not rec.get("dirty") and not rec.get("ahead"):
            continue   # 對照用的乾淨 detached checkout,不是在改的樹(seat-info --dev 同一條)
        trees.append({"path": path, "branch": br, "head": head[:9], "ahead": rec.get("ahead"),
                      "behind": rec.get("behind"), "dirty": rec.get("dirty"), "cached": hit})
    if cache_dir:
        try:
            atomic_json.write_json(_tree_cache_path(cache_dir, seat_id), {"trees": fresh_cache})
        except Exception:      # noqa: BLE001 —— 快取寫不進去不影響這一輪
            pass
    trees.sort(key=lambda t: (-(t["ahead"] or 0), t["path"]))
    return trees


def _ship_refuse(root, repo, now):
    """推包被擋、還沒推成的最近一次(ship/last-refuse-<標籤>.json 比同標籤 last-push 新、3 天內;只認這個專案的標籤,
    同 project_sweep.ship_state)。回 {"branch","at","new":[測試路徑]} 或 None。"""
    d = os.path.join(root, "ship")
    if not os.path.isdir(d):
        return None
    try:
        from project_sweep import _project_ship_tags  # noqa: PLC0415 —— 只在有 ship/ 時才載
        tags = _project_ship_tags(repo, d)
    except Exception:  # noqa: BLE001 —— 認不出標籤就不報(不猜成別的倉的紅)
        return None
    best = None
    for t in tags:
        r = _load(os.path.join(d, "last-refuse-%s.json" % t)) or {}
        pushed = float((_load(os.path.join(d, "last-push-%s.json" % t)) or {}).get("at") or 0)
        at = float(r.get("at") or 0)
        if at > pushed and now - at < 3 * 86400 and (best is None or at > best["at"]):
            best = {"branch": r.get("branch"), "at": at, "new": [str(x) for x in (r.get("new") or [])]}
    return best


def fetch_pkgs(repo, root, trees, main_ref, now):
    """§22.4「我的包走到哪」:每棵樹的分支走到哪一關(同 chart_funnel 的標題比對:cherry-pick 換了 sha 標題不變)。
      stage 3 = 每一顆標題都在主線(或領先 0 顆);2 = 都在主線或任一個 int/* 合包;1 = 有一部分;0 = 都沒有。
      reds = 最近一次推包被擋的新紅裡,這棵樹動過的檔(合包含這棵樹的提交才算)。"""
    sm = set((_git(repo, "log", main_ref, "--format=%s", "-n", "3000") or "").splitlines())
    ints = (_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/int/") or "").split()
    sb = set((_git(repo, "log", *ints, "^" + main_ref, "--format=%s", "-n", "4000") or "").splitlines()) if ints else set()
    ref = _ship_refuse(root, repo, now)
    rsub = set()
    if ref and ref.get("branch") and _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + str(ref["branch"])):
        rsub = set((_git(repo, "log", "refs/heads/" + str(ref["branch"]), "^" + main_ref, "--format=%s", "-n", "4000") or "").splitlines())

    def one(t):
        br = t.get("branch")
        if not br:
            return None
        subs = [x for x in (_git(repo, "log", "%s..%s" % (main_ref, t["head"]), "--format=%s") or "").splitlines() if x]
        if not subs or all(x in sm for x in subs):
            st = 3
        elif all(x in sm or x in sb for x in subs):
            st = 2
        else:
            st = 1 if any(x in sm or x in sb for x in subs) else 0
        reds = []
        if ref and ref["new"] and st < 3 and any(x in rsub for x in subs):
            files = set((_git(repo, "diff", "--name-only", "%s...%s" % (main_ref, t["head"])) or "").splitlines())
            reds = [os.path.basename(x) for x in ref["new"] if x in files]
        return {"stage": st, "commits": len(subs), "reds": reds,
                "refused": ref["branch"] if reds else None}

    with cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(trees)))) as pool:
        res = list(pool.map(one, trees))
    for t, r in zip(trees, res):
        if r:
            t.update(r)
    return trees


def seat_logos(root, cabin):
    """席碼 → 引擎標誌(the maintainer 10-14 03:1x「所有的 mods 有帶到 aib 席位的,都要帶上他的 logo」)。
    標誌表用 render_lib.ENGINE_ICONS(右欄 AI 清單同一份);讀不到就回空,畫面退回只寫席碼。"""
    try:
        sys.path.insert(0, os.path.join(HERE, "render"))
        from render_lib import ENGINE_ICONS  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for _path, rec in _seat_files(root, cabin):
        code = seat_address.code_of(rec)
        icon = ENGINE_ICONS.get(str(rec.get("provider") or "").lower())
        if code and icon:
            out[code] = icon
    return out


def seat_logo_colors(root, cabin):
    """席碼 → 標誌的原廠色(the maintainer 10-14「仔細把 aib-boards 的所有 logo 補回原廠色」);跟 seat_logos 同一批席,
    顏色用 brand_color(右欄 AI 清單同一張表)。grok 黑白品牌沒有色 ⇒ 不放(畫面用預設字色)。"""
    out = {}
    for _path, rec in _seat_files(root, cabin):
        code = seat_address.code_of(rec)
        col = brand_color(rec.get("provider"))
        if code and col:
            out[code] = col
    return out


EVENT_WINDOW_S = 24 * 3600
EVENT_MAX = 40


def fetch_events(root, repo, code, now):
    """今天頁「紀錄」(the maintainer 10-14 02:59「這地方不夠」):近 24 小時 —— 收到的信(含已完成)、我寄出的信、
    我的提交(分支名帶席碼、訊息帶席碼)、推包推成 / 被擋。新的在前,最多 EVENT_MAX 筆;每種各自量不到就略過。"""
    ev, since = [], now - EVENT_WINDOW_S
    qroot = os.path.join(root, "state", "dm-queue")
    for path in glob.glob(os.path.join(qroot, "*", "q_*.json")):
        try:
            if os.path.getmtime(path) < since:
                continue
            m = _load(path) or {}
        except OSError:
            continue
        ts = m.get("sent_at") or m.get("ts") or 0
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if ts < since:
            continue
        to, frm = str(m.get("seat") or ""), str(m.get("sender_seat") or "")
        label = (m.get("sender_label") or m.get("sender") or ("@" + frm if frm else "?"))
        if to == code:
            ev.append({"at": ts, "kind": "dm_in", "who": label, "text": _clip(m.get("msg"), 120), "status": m.get("status")})
        elif frm == code:
            ev.append({"at": ts, "kind": "dm_out", "who": "→ @" + to, "text": _clip(m.get("msg"), 120), "status": m.get("status")})
    out = _git(repo, "log", "--branches=*%s*" % code, "--since=%d" % int(since), "--grep=" + code,
               "--format=%ct%x1f%h%x1f%s") or ""
    for ln in out.splitlines():
        parts = ln.split("\x1f")
        if len(parts) == 3 and parts[0].isdigit():
            ev.append({"at": int(parts[0]), "kind": "commit", "who": parts[1], "text": _clip(parts[2], 120)})
    d = os.path.join(root, "ship")
    for f in glob.glob(os.path.join(d, "last-push-*.json")) + glob.glob(os.path.join(d, "last-refuse-*.json")):
        r = _load(f) or {}
        try:
            at = float(r.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if at >= since:
            refuse = "last-refuse-" in f
            ev.append({"at": at, "kind": "ship_refuse" if refuse else "ship_push", "who": str(r.get("branch") or "?"),
                       "text": ("新紅 %d 支" % len(r.get("new") or [])) if refuse else str(r.get("head") or "")[:9]})
    ev.sort(key=lambda e: -e["at"])
    return ev[:EVENT_MAX]


FLOW_WINDOW_S = 24 * 3600
FLOW_ITEMS = 3
DELIVER_KINDS = ("deliver", "report")
# 驗收席「退回」:近 24 小時我寄出、內文像退件的信(推估 —— dm 沒有「退回」這種 kind,畫面標 guess)
RETURN_RE = re.compile(r"退回|退件|打回|紅燈|沒過|不過|reject|fail", re.I)


def _flow_item(m, who):
    return {"who": who, "text": _clip(m.get("msg"), 120), "at": m.get("sent_at") or m.get("ts")}


def fetch_flow(root, code, role, now):
    """§22.5 第 1 步「依角色換流程」(the maintainer 10-14 03:1x):role_family → 流程節點,mod 只照節點畫。
    dev 家族(dev / pm / integrator / root)沿用今天頁原本的包 → 推包 → 機器 / 主線 / 信箱 → 下一件,這裡回空節點;
    qa:待驗 → 驗過 → 退回;bot:來訊 → 回覆 → 結案。資料 = 這一席的信件佇列(收件匣資料夾)+ 我寄出的信(只有退回要)。"""
    fam = role_family(role)
    if fam not in ("qa", "bot"):
        return {"family": "dev", "nodes": []}
    qroot, since = os.path.join(root, "state", "dm-queue"), now - FLOW_WINDOW_S
    inbox = []
    for path in glob.glob(os.path.join(qroot, code, "q_*.json")):
        m = _load(path)
        if isinstance(m, dict) and not m.get("deleted_at") and not m.get("superseded_by"):
            inbox.append(m)
    num = lambda v: float(v) if isinstance(v, (int, float)) or str(v or "").replace(".", "", 1).isdigit() else 0.0
    who = lambda m: m.get("sender_label") or ("@" + str(m["sender_seat"]) if m.get("sender_seat") else None) or m.get("sender") or "?"
    newest = lambda ms: sorted(ms, key=lambda m: -num(m.get("sent_at") or m.get("ts")))

    def node(key, ms, level, guess=False, label=who):
        ms = newest(ms)
        return {"key": key, "level": level, "count": len(ms), "guess": guess, "items": [_flow_item(m, label(m)) for m in ms[:FLOW_ITEMS]]}
    if fam == "bot":
        open_ = [m for m in inbox if not m.get("done_at") and not m.get("archived_at")]
        unread = [m for m in open_ if not m.get("read_at")]
        reading = [m for m in open_ if m.get("read_at")]
        closed = [m for m in inbox if num(m.get("done_at")) >= since]
        return {"family": "bot", "nodes": [node("bot_in", unread, "error" if unread else "ok"),
                                           node("bot_reply", reading, "warning" if reading else "ok"),
                                           node("bot_closed", closed, "ok")]}
    deliv = [m for m in inbox if m.get("kind") in DELIVER_KINDS]
    pending = [m for m in deliv if not m.get("done_at") and not m.get("archived_at")]
    passed = [m for m in deliv if num(m.get("done_at")) >= since]
    returned = []
    for path in glob.glob(os.path.join(qroot, "*", "q_*.json")):
        try:
            if os.path.getmtime(path) < since:
                continue
        except OSError:
            continue
        m = _load(path)
        if isinstance(m, dict) and str(m.get("sender_seat") or "") == code and num(m.get("sent_at") or m.get("ts")) >= since \
                and RETURN_RE.search(str(m.get("msg") or "")):
            returned.append(m)
    lvl = "error" if any(not m.get("read_at") for m in pending) else "warning" if pending else "ok"
    return {"family": "qa", "nodes": [node("qa_pending", pending, lvl), node("qa_passed", passed, "ok"),
                                      node("qa_returned", returned, "warning" if returned else "ok", guess=True,
                                           label=lambda m: "→ @" + str(m.get("seat") or "?"))]}


# §22.5 第 2 步「依人格換主題」(the maintainer 10-14 03:1x):角色.md 開頭設定區可寫 theme_color / theme_particle / theme_speed / theme_border;
# 沒寫或寫錯的那一項不給 ⇒ 畫面用預設(主色 claude、光點 ●、速度 normal、框線 round)。只讀,不改人格卡。
THEME_SPEEDS = ("slow", "normal", "fast")
THEME_BORDERS = ("round", "single", "double", "bold")


def persona_theme(frontmatter):
    vals = {}
    for m in re.finditer(r"(?m)^theme_(color|particle|speed|border)\s*:\s*(.+?)\s*$", frontmatter or ""):
        vals[m.group(1)] = m.group(2).strip().strip("\"'")
    out = {}
    if re.fullmatch(r"#[0-9a-fA-F]{6}", vals.get("color", "")):
        out["color"] = vals["color"].lower()
    p = vals.get("particle", "")
    if p and len(p) <= 2 and not any(c.isspace() for c in p):
        out["particle"] = p
    if vals.get("speed") in THEME_SPEEDS:
        out["speed"] = vals["speed"]
    if vals.get("border") in THEME_BORDERS:
        out["border"] = vals["border"]
    return out


def fetch_persona(root, seat_id):
    """seat_id → 延續檔(bind_one.read_continuity)→ persona_uid → cards.json → 角色.md 的「職責」「紅線」兩段。
    路徑防護與切段沿用 persona_pack(inside / body_of / sections / clean),不另寫一套。"""
    ident = os.path.abspath(os.path.join(HERE, "..", "identity"))
    if ident not in sys.path:
        sys.path.insert(0, ident)
    import bind_one
    import persona_pack
    from pathlib import Path
    cont = bind_one.read_continuity(root, seat_id) or {}
    uid = cont.get("persona_uid")
    if not uid:
        return None
    doc = _load(os.path.join(root, "identity", "cards.json")) or {}
    card = (doc.get("cards") or {}).get(uid)
    if not isinstance(card, dict):
        return {"uid": uid, "name": None, "emoji": None, "category": None, "duties": [], "red_lines": [], "_card": "missing"}
    out = {"uid": uid, "name": card.get("display_name"), "emoji": card.get("emoji"), "category": card.get("category"),
           "duties": [], "red_lines": []}
    vroot = (doc.get("source") or {}).get("vault_root") or ""
    path = persona_pack.inside(Path(vroot), str(card.get("card_path_rel") or "")) if vroot else None
    if path is None or not path.is_file():
        out["_card"] = "unreadable"
        return out
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm = persona_pack._FRONTMATTER_RE.match(raw)
    theme = persona_theme(fm.group(0) if fm else "")
    if theme:
        out["theme"] = theme
    body = persona_pack.clean(persona_pack.body_of(raw))
    for name, text in persona_pack.sections(body):
        key = {"職責": "duties", "紅線": "red_lines"}.get(name)
        if key:
            out[key] = [_clip(m.group(1)) for m in re.finditer(r"(?m)^\s*[-*]\s+(.+?)\s*$", text)]
    return out


def fetch_project(root, seat_id, seat_rec):
    """project-seats.json(project_seats.project_of)→ <狀態根>/projects/<uid>/card.json;正本沒有 ⇒ 席位檔的 project_uid。"""
    import project_seats
    uid = project_seats.project_of(seat_id, root=root) or (seat_rec or {}).get("project_uid")
    if not uid:
        return None
    card = _load(os.path.join(root, "projects", uid, "card.json")) or {}
    ws = ((card.get("layers") or {}).get("work_state")) or card.get("work_state") or {}
    return {"uid": uid, "title": card.get("title") or (seat_rec or {}).get("project_title"),
            "work_state": {"now": ws.get("now") or "", "next_step": ws.get("next_step") or "",
                           "pending": [p for p in (ws.get("pending") or []) if isinstance(p, str)]}}


def fetch_now(root, code):
    import seat_now
    return seat_now.read(code, state_root=root)


# ---------------------------------------------------------------- 感測器(純函式:取料結果 → 一張卡)
def _card(sensor, level, line, detail, at, source, data=None):
    return {"sensor": sensor, "level": level, "line": _clip(line) if line else None,
            "detail": [_clip(x) for x in (detail or [])][:DETAIL_MAX], "measured_at": at, "source": source,
            "data": data or {}}


def s_machine_lease(f, ctx):
    d = f["lease"]
    lease = d.get("lease") or {}
    st = d.get("state")
    mine = bool(ctx["code"]) and ctx["code"] in str(lease.get("owner") or "")
    level = "unknown" if st == "unreadable" else ("ok" if st == "free" or mine else "warn")
    return _card("machine_lease", level, lease.get("what") if st != "free" else None, [],
                 ctx["at"], "host_lease.status",
                 {"state": st, "owner": lease.get("owner"), "pid": lease.get("pid"),
                  "acquired_epoch": lease.get("acquired_epoch"), "expiry_epoch": lease.get("expiry_epoch"),
                  "queue": len(d.get("queue") or [])})


def s_push_chain(f, ctx):
    procs = f["ship"]
    lease = (f.get("lease") or {}).get("lease") or {}
    queue = (f.get("lease") or {}).get("queue")
    line = lease.get("what") if "ship" in str(lease.get("owner") or "") else (procs[0]["branch"] if procs else None)
    return _card("push_chain", "ok", line, [p["branch"] for p in procs if p.get("branch")], ctx["at"],
                 "ps+host_lease", {"running": procs, "queued": None if queue is None else len(queue)})


def s_main_head(f, ctx):
    m = f["main"]
    return _card("main_head", "ok", m["subject"], [], ctx["at"], "git.log",
                 {"ref": m["ref"], "sha": m["sha"][:9], "committed_at": m["ct"]})


UNREAD_STATUSES = ("queued", "delivered", "claimed")


def is_unread(m):
    """未讀 = dm_queue tabs_payload 的 badges.unread 同一條:待處理頁裡 queued/delivered/claimed、沒過期(seat_screen_fleet 也讀 badges)。"""
    return (m.get("_tab", "waiting") == "waiting" and m.get("status") in UNREAD_STATUSES and not m.get("expired_now"))


def s_inbox(f, ctx):
    rows = f["dm"]
    unread = [m for m in rows if is_unread(m)]
    # line 留 null:畫法從 data 自己排字(本檔不寫說明文)
    return _card("inbox", "warn" if unread else "ok", None,
                 [m.get("sender") for m in unread], ctx["at"], "dm_queue.list",
                 {"unread": len(unread), "open": len(rows)})


def s_my_trees(f, ctx):
    trees = f["trees"]
    stale = [t for t in trees if (t.get("behind") or 0) >= 50]
    return _card("my_trees", "warn" if stale else "ok", None,
                 [t["branch"] or t["path"] for t in trees], ctx["at"], "git.worktree",
                 {"trees": trees, "dirty": sum(1 for t in trees if t.get("dirty")),
                  "ahead": sum(t.get("ahead") or 0 for t in trees)})


def s_red_lines(f, ctx):
    p = f.get("persona")
    lines = (p or {}).get("red_lines") or []
    if not lines:
        raise RuntimeError("no_red_lines" if p else "unbound")
    return _card("red_lines", "ok", lines[0], lines[:3], ctx["at"], "persona_card", {"count": len(lines)})


def s_project_state(f, ctx):
    p = f["project"]
    ws = p["work_state"]
    first = (ws.get("now") or "").splitlines()[0] if ws.get("now") else None
    return _card("project_state", "ok", first, ws.get("pending") or [], ctx["at"], "project_card",
                 {"uid": p["uid"], "next_step": _clip(ws.get("next_step"))})


SENSORS = {
    "machine_lease": (s_machine_lease, ("lease",)),
    "push_chain": (s_push_chain, ("ship", "lease")),
    "main_head": (s_main_head, ("main",)),
    "inbox": (s_inbox, ("dm",)),
    "my_trees": (s_my_trees, ("trees",)),
    "red_lines": (s_red_lines, ("persona",)),
    "project_state": (s_project_state, ("project",)),
}


# ---------------------------------------------------------------- 待辦
def todo_path(root, seat_id):
    if not SEAT_ID_RE.fullmatch(str(seat_id or "")):
        raise ValueError("seat_id")
    return os.path.join(root, "todo", seat_id + ".jsonl")


def ledger_open(root, seat_id):
    """帳本折疊:add 了、還沒 done 的(只追加檔;壞列略過)。"""
    items, done = {}, set()
    try:
        with open(todo_path(root, seat_id), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if not isinstance(r, dict) or not r.get("id"):
                    continue
                if r.get("op") == "add":
                    items[r["id"]] = r
                elif r.get("op") == "done":
                    done.add(r["id"])
    except OSError:
        return []
    return [r for k, r in items.items() if k not in done]


def ledger_append(root, seat_id, row):
    p = todo_path(root, seat_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)     # O_APPEND 一次寫一整列:多寫者不互蓋
    finally:
        os.close(fd)
    return row


NOW_GROUP = {"reply": "others", "decide": "human", "busy": "doing"}


def build_todo(f, ctx):
    """五個來源的 v1 三個:dm(未讀 ⇒ now、已讀未結 ⇒ doing)、aib now、帳本。同一個 id 只留一筆(先到的來源贏)。"""
    out, seen = [], set()

    def add(item):
        if item["id"] in seen:
            return
        seen.add(item["id"])
        out.append(item)

    for m in sorted(f.get("dm") or [], key=lambda m: m.get("sent_at") or m.get("ts") or 0):
        if not m.get("id"):
            continue
        add({"id": "dm:" + m["id"], "group": "now" if is_unread(m) else "doing", "text": _clip(m.get("msg")),
             "from": m.get("sender"), "since": m.get("sent_at") or m.get("ts"), "source": "dm"})
    rec = f.get("now")
    if isinstance(rec, dict) and not rec.get("idle") and NOW_GROUP.get(rec.get("state")):
        add({"id": "now:" + ctx["code"], "group": NOW_GROUP[rec["state"]], "text": _clip(rec.get("item")),
             "from": "@" + ctx["code"], "since": rec.get("started_at") or rec.get("at"), "source": "now"})
    for r in f.get("ledger") or []:
        ref = r.get("ref")
        if ref and ref in seen:
            continue
        add({"id": r["id"], "group": r.get("group") if r.get("group") in TODO_GROUPS else "self",
             "text": _clip(r.get("text")), "from": r.get("by"), "since": r.get("at"), "source": "ledger"})
    order = {g: i for i, g in enumerate(TODO_GROUPS)}
    out.sort(key=lambda t: (order.get(t["group"], 9), -(t["since"] or 0)))
    return out[:TODO_MAX]


# ---------------------------------------------------------------- 組裝
def cache_dir_of(root, cabin):
    return os.path.join(root, "instances", cabin, "state", "seat-screen") if cabin else None


def produce(root, seat_file, rec, cabin, *, repo=None, now=None):
    t0 = time.monotonic()
    at = int(now if now is not None else time.time())
    repo = repo or repo_root()
    seat_id = rec.get("seat_id")
    code = seat_address.code_of(rec)
    cdir = cache_dir_of(root, cabin)
    sweep_tmp(cdir)
    ctx = {"code": code, "at": at}
    missing = []

    jobs = {
        "lease": lambda: fetch_lease(repo),
        "ship": fetch_ship_procs,
        "main": lambda: fetch_main(repo),
        "persona": lambda: fetch_persona(root, seat_id),
        "project": lambda: fetch_project(root, seat_id, rec),
        "ledger": lambda: ledger_open(root, seat_id),
    }
    if code:
        jobs["events"] = lambda: fetch_events(root, repo, code, at)
    if code:
        jobs["dm"] = lambda: fetch_dm(code)
        jobs["now"] = lambda: fetch_now(root, code)
        jobs["flow"] = lambda: fetch_flow(root, code, rec.get("role"), at)
    else:
        missing.append("code:no_session_uuid")
    got, err = {}, {}
    pool = _Pool(max_workers=len(jobs) + 1)
    futs = {pool.submit(fn): k for k, fn in jobs.items()}
    if code and "my_trees" in ROLE_SENSORS[role_family(rec.get("role"))]:   # 逐樹 git 最貴:角色用不到就不量
        def _trees():
            m = futs_main.result(timeout=SENSOR_TIMEOUT_S)
            trees = fetch_trees(repo, code, m, cdir, seat_id, at)
            try:
                return fetch_pkgs(repo, root, trees, m["sha"], at)
            except Exception:  # noqa: BLE001 —— 量不到關卡照樣回樹(沒 stage 欄 = 畫面寫「量不到」)
                return trees
        futs_main = next(fu for fu, k in futs.items() if k == "main")
        futs[pool.submit_cache(_trees)] = "trees"
    deadline = time.monotonic() + DEADLINE_S
    for fu, k in futs.items():
        try:
            got[k] = fu.result(timeout=max(0.0, deadline - time.monotonic()))
        except cf.TimeoutError:
            err[k] = "timeout"
        except subprocess.TimeoutExpired:
            err[k] = "timeout"
        except Exception as e:  # noqa: BLE001 —— 量不到就是 unknown,不讓整支掛掉
            err[k] = (str(e) or e.__class__.__name__)[:60]
    pool.shutdown(wait=False, cancel_futures=True)

    persona = got.get("persona") or None
    if persona and persona.get("_card"):
        missing.append("persona:card_" + persona.pop("_card"))
    if "persona" in err:
        missing.append("persona:" + err["persona"])
    project = got.get("project")
    if "project" in err:
        missing.append("project:" + err["project"])

    know = []
    for name in sensors_for(rec.get("role"), bool(project)):
        fn, needs = SENSORS[name]
        why = next((err[k] for k in needs[:1] if k in err), None) or next((k + ":not_run" for k in needs[:1] if k not in got), None)
        if why is None:
            try:
                know.append(fn(got, ctx))
                continue
            except Exception as e:  # noqa: BLE001
                why = (str(e) or e.__class__.__name__)[:60]
        know.append(_card(name, "unknown", None, [], at, SENSORS[name][0].__name__[2:]))
        missing.append("%s:%s" % (name, why))
    if "flow" in err:
        missing.append("flow:" + err["flow"])
    for k in ("dm", "now", "ledger"):
        if k in err:
            missing.append("todo_%s:%s" % (k, err[k]))

    now_rec = got.get("now")
    subs = [c for c, _d in seat_subs(root, cabin, seat_id)]
    try:
        cap = captain_code(root, cabin)
    except Exception as e:  # noqa: BLE001
        cap = None
        missing.append("captain_seat:" + e.__class__.__name__)
    out = {
        "schema": SCHEMA, "seat_id": seat_id, "code": code, "cabin": cabin,
        "tier": tier_of(code, (persona or {}).get("category"), subs, cap), "subs": subs,
        "persona": persona, "role": rec.get("role"),
        "project": None if not project else {"uid": project["uid"], "title": project.get("title"),
                                              "work_state": _first_line(project.get("work_state"))},
        "now": None if not isinstance(now_rec, dict) else {
            "text": now_rec.get("item"), "state": "idle" if now_rec.get("idle") else now_rec.get("state"),
            "at": now_rec.get("at"), "steps_done": now_rec.get("steps_done"), "steps_total": now_rec.get("steps_total"),
            "started_at": now_rec.get("started_at")},
        "todo": build_todo(got, ctx) if code else [],
        "events": got.get("events") or [],
        "flow": got.get("flow") or {"family": role_family(rec.get("role")) if role_family(rec.get("role")) in ("qa", "bot") else "dev", "nodes": []},
        "logos": seat_logos(root, cabin),
        "logo_colors": seat_logo_colors(root, cabin),
        "know": know,
        "measured_at": at,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "missing": missing,
        "seat_file": seat_file,
    }
    if cdir and seat_id:
        try:
            atomic_json.write_json(os.path.join(cdir, seat_id + ".json"), out)
        except Exception as e:  # noqa: BLE001 —— 快取寫不進去不影響這一輪輸出
            out["missing"].append("cache_write:" + e.__class__.__name__)
    return out


def cached(root, cabin, seat_id, now=None):
    cdir = cache_dir_of(root, cabin)
    if not cdir or not seat_id:
        return None
    d = _load(os.path.join(cdir, seat_id + ".json"))
    now = time.time() if now is None else now
    if d and d.get("schema") == SCHEMA and 0 <= now - (d.get("measured_at") or 0) < CACHE_TTL_S:
        return dict(d, cached=True)
    return None


# ---------------------------------------------------------------- 圖表(--charts;重的,點了才算 §21.2,另存快取 60 秒)
CHARTS_TTL_S = 60
CHART_DAYS = 7
CHARTS_DEADLINE_S = 3.0
_REPO_KEY = b'"repo": "'      # 影子帳本列尾的頂層鍵;inputs 裡的同名字串一定是跳脫過的 \"repo\",對不上這個


def _days(now):
    return [time.strftime("%m-%d", time.localtime(now - i * 86400)) for i in range(CHART_DAYS - 1, -1, -1)]


def _iso_epoch(s):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def shadow_dir():
    """影子帳本目錄:沿用 shadow_tracer.shadow_dir_default(單一來源,seat-info --dev 同一支)。"""
    vdir = os.path.join(repo_root(), "tools", "verify")
    if vdir not in sys.path:
        sys.path.insert(0, vdir)
    import shadow_tracer
    return str(shadow_tracer.shadow_dir_default())


_KEY_TEST = re.compile(rb'^\{"test": "((?:[^"\\]|\\.)*)"')
_KEY_RC = re.compile(rb'"rc": (-?\d+)')
_TAIL_KEYS = (b'"time_utc": "', b'"duration_s": ')


def _tail_str(ln, key):
    i = ln.rfind(key)
    if i < 0:
        return None
    j = ln.find(b'"', i + len(key))
    return None if j < 0 else ln[i + len(key):j]


def _tail_num(ln, key):
    i = ln.rfind(key)
    if i < 0:
        return None
    m = re.match(rb"-?[\d.]+", ln[i + len(key):i + len(key) + 24])
    return float(m.group(0)) if m else None


def _light_row(ln):
    """影子帳本一列 → (t, test, rc, 秒, repo),不整列 json.loads(一列常 100KB 以上,大半是 inputs)。
    頂層鍵用沒跳脫的 `"鍵": ` 找:inputs / tail 裡的同名字串一定是 \\"鍵\\",對不上。缺 time_utc ⇒ None(畫不到日子)。"""
    m = _KEY_TEST.match(ln)
    tu = _tail_str(ln, b'"time_utc": "')
    repo = _tail_str(ln, _REPO_KEY)
    if not m or tu is None or repo is None:
        return None
    t = _iso_epoch(tu.decode("ascii", "replace"))
    rc = _KEY_RC.search(ln, 0, 4096)
    try:
        test = json.loads(b'"' + m.group(1) + b'"')
        path = json.loads(b'"' + repo + b'"')
    except ValueError:
        return None
    return (t, test, int(rc.group(1)) if rc else 0, _tail_num(ln, b'"duration_s": ') or 0.0, path)


ROWS_AGG_SCHEMA = "aib-seat-screen-rows/v2"     # v2 起每檔帶最後一列的指紋
ROWS_BUDGET_S = 1.0       # 一輪最多花這麼久解析帳本;超過就停在那一行,位置記進彙整檔,下一輪接著讀
ROWS_CHECK_EVERY = 32     # 一列常 100KB 以上:每 32 列看一次時間


def _load_agg(path):
    d = _load_any(path) if path else None
    return d if isinstance(d, dict) and d.get("schema") == ROWS_AGG_SCHEMA and isinstance(d.get("files"), dict) else None


STALE_TMP_S = 600


def sweep_tmp(cdir, now=None):
    """快取目錄裡 10 分鐘以上的暫存殘檔(本檔 mkstemp 的 .<名>.tmp.*、舊版 .rows-agg.*、atomic_json 的 .<名>.tmp.*)刪掉。
    只看這一個目錄、只刪點開頭且帶 .tmp. / .rows-agg. 的檔。"""
    if not cdir:
        return 0
    now = time.time() if now is None else now
    n = 0
    try:
        ents = list(os.scandir(cdir))
    except OSError:
        return 0
    for e in ents:
        if not e.name.startswith(".") or not (".tmp." in e.name or e.name.startswith(".rows-agg.")):
            continue
        try:
            if e.is_file(follow_symlinks=False) and now - e.stat(follow_symlinks=False).st_mtime > STALE_TMP_S:
                os.unlink(e.path)
                n += 1
        except OSError:
            continue
    return n


def _store_agg(path, agg):
    """大檔(彙整檔數 MB、艦長圖表段會超過 atomic_json 的 64KB 單筆上限):同目錄暫存檔 + rename,
    讀者只會看到完整的新或舊。"""
    import tempfile
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(agg, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fchmod(fh.fileno(), 0o644)     # mkstemp 開成 0600;快取給同機的畫法讀
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


FP_MAX = 8 << 20      # 一列再大也不會到 8MB;超過就當對不上(寧可重讀,不讀一大段來算指紋)


def _sha1(b):
    import hashlib
    return hashlib.sha1(b).hexdigest()


def scan_rows(now, sdir=None, days=CHART_DAYS, agg_path=None, budget_s=ROWS_BUDGET_S, stats=None):
    """7 天內全部的影子帳本列 (t, test, rc, 秒, repo),全艦共用。
    - 只開修改時間在 7 天內的 verify-*.rows.jsonl:帳本只追加;reuse_index.compact 改寫後把 mtime 還原成原值,
      所以 mtime 永遠 ≥ 檔內最新一列 ⇒ mtime 早於 7 天的檔裡不會有 7 天內的列。
    - 增量:agg_path(<狀態根>/instances/<艙>/state/seat-screen/rows-agg.json,各席共用)記每個檔的
      (inode, 大小, mtime, 讀到的位元組位置, 已解析的列);下一輪只讀新追加的位元組。
      inode 換了 / 檔變小 / mtime 往回 / 上一輪最後收下那一整列的指紋對不上 ⇒ 那個檔從頭重讀
      (reuse_index.compact 是原地改寫、同 inode、mtime 還原,之後再追加長回原位置以上,只有指紋認得出)。
      只收完整的行(結尾有換行)。超過時間預算也照樣把已讀到的位置存回去。
    - 時間預算:超過 budget_s 就停在當下那一行(位置照記,下一輪接著讀),stats["budget_hit"]=True。
    - `<檔>.archive`(compact 搬走的重複列,數 GB)不讀:趨勢會少算「同測試、同鑰匙、同結果」被壓掉的重跑。"""
    sdir = sdir or shadow_dir()
    cut = now - days * 86400
    stats = {} if stats is None else stats
    stats.update({"bytes": 0, "files": 0, "budget_hit": False, "reused_rows": 0})
    old = (_load_agg(agg_path) or {}).get("files") or {}
    new_files = {}
    t_start = time.monotonic()
    t_end = t_start + budget_s if budget_s else None
    for f in sorted(glob.glob(os.path.join(sdir, "verify-*.rows.jsonl"))):
        try:
            st = os.stat(f)
        except OSError:
            continue
        if st.st_mtime < cut:
            continue
        prev = old.get(f) or {}
        p_off, p_fs = int(prev.get("offset") or 0), int(prev.get("fp_start") or 0)
        cand = (prev.get("ino") == st.st_ino and st.st_size >= p_off and st.st_mtime >= (prev.get("mtime") or 0)
                and bool(prev.get("fp")) and 0 <= p_off - p_fs <= FP_MAX)
        try:
            fh = open(f, "rb")
        except OSError:
            continue
        with fh:
            # 同一個開檔:先驗「上一輪最後收下那一整列」的指紋,對上才從 offset 接著讀(不再另開檔,避開跟 compact 的競態);
            # compact 原地改寫(同 inode、mtime 還原)後又長回舊位置以上 ⇒ 指紋對不上 ⇒ 從頭重讀
            same = False
            if cand:
                fh.seek(p_fs)
                same = _sha1(fh.read(p_off - p_fs)) == prev.get("fp")
            start = p_off if same else 0
            rows = [r for r in (prev.get("rows") or []) if r and r[0] and r[0] > cut] if same else []
            stats["reused_rows"] += len(rows)
            pos, fp_start, fp = start, (p_fs if same else 0), (prev.get("fp") if same else _sha1(b""))
            if st.st_size > start and not stats["budget_hit"]:
                stats["files"] += 1
                fh.seek(start)
                try:
                    for i, ln in enumerate(fh):
                        if not ln.endswith(b"\n"):
                            break                      # 寫到一半的行:不收、不前進,下一輪再讀
                        fp_start, fp = pos, _sha1(ln)   # 指紋就用剛讀進來的這一列,不再回頭讀
                        pos += len(ln)
                        stats["bytes"] += len(ln)
                        r = _light_row(ln)
                        if r and r[0] and r[0] > cut:
                            rows.append(list(r))
                        if t_end and not (i + 1) % ROWS_CHECK_EVERY and time.monotonic() > t_end:
                            stats["budget_hit"] = True
                            break
                except OSError:
                    pass
        new_files[f] = {"ino": st.st_ino, "size": st.st_size, "mtime": st.st_mtime, "offset": pos,
                        "fp_start": fp_start, "fp": fp, "rows": rows}
    if agg_path:
        try:
            _store_agg(agg_path, {"schema": ROWS_AGG_SCHEMA, "at": int(now), "files": new_files})
        except Exception:  # noqa: BLE001 —— 彙整檔寫不進去:這一輪照樣有結果,下一輪重讀
            stats["store_failed"] = True
    out = [tuple(r) for v in new_files.values() for r in v["rows"]]
    out.sort(key=lambda r: r[0])
    stats["ms"] = int((time.monotonic() - t_start) * 1000)
    return out


def mine_rows(rows, paths, code=None):
    """我的列:repo 是我現在的樹,或樹資料夾名帶我的席碼(已刪掉的樹也算 —— 10-14 查:10-09~10-12 的列全在已刪的樹)。"""
    want = set(paths)
    return [r for r in rows if r[4] in want or (code and code in os.path.basename(r[4] or ""))]


def chart_rows(paths, now, sdir=None, code=None):
    return mine_rows(scan_rows(now, sdir), paths, code)


def chart_trend_slowest(rows, now):
    """extract3:每天綠/紅(每一列都算,不是每支最新)、每支測試耗時中位數前 8。"""
    import statistics
    days = _days(now)
    tr = {d: [0, 0] for d in days}
    slow = {}
    for t, test, rc, sec, _repo in rows:
        k = time.strftime("%m-%d", time.localtime(t))
        if k in tr:
            tr[k][0 if rc == 0 else 1] += 1
        slow.setdefault(os.path.basename(test or ""), []).append(sec)
    slowest = sorted(([k, round(statistics.median(v), 1), len(v)] for k, v in slow.items()), key=lambda x: -x[1])[:8]
    return [[d, *tr[d]] for d in days], slowest


def _batch_refs(repo):
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/int/")
    return [r for r in (out or "").splitlines() if r.startswith("int/batch")]


def chart_commits(repo, code, branches, main_ref, batches, now):
    """extract3:我自己的提交 = 分支 ^主線 ^每個合包 --since=7.days --grep=<席碼>,同一顆只算一次;
    檔案改動行數一起從 --numstat 拿(一條分支一次 git,不逐顆 git show)。"""
    days = _days(now)
    com = {d: 0 for d in days}
    files, seen = {}, set()
    excl = ["^" + main_ref] + ["^" + b for b in batches]

    def one(br):
        return _git(repo, "log", br, *excl, "--since=%d.days" % CHART_DAYS, "--grep=" + code,
                    "--format=@%H %ct", "--numstat") or ""

    with cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(branches)))) as pool:
        outs = list(pool.map(one, branches))
    for out in outs:
        skip = True
        for ln in out.splitlines():
            if ln.startswith("@"):
                h, _, ts = ln[1:].partition(" ")
                skip = h in seen
                if not skip:
                    seen.add(h)
                    k = time.strftime("%m-%d", time.localtime(int(ts or 0)))
                    if k in com:
                        com[k] += 1
                continue
            parts = ln.split("\t")
            if not skip and len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                files[parts[2]] = files.get(parts[2], 0) + int(parts[0]) + int(parts[1])
    top = sorted(files.items(), key=lambda kv: -kv[1])[:10]
    return [[d, com[d]] for d in days], [[os.path.basename(f) if len(f) > 48 else f, n] for f, n in top]


def chart_in_batch(repo, trees, batches):
    """extract2 的 in_batch,擴成「任一個 int/batch* 含這棵樹的 HEAD」(一棵樹一次 for-each-ref --contains)。"""
    if not batches:
        return {t["path"]: False for t in trees}

    def one(t):
        if not t.get("branch"):
            return t["path"], False
        out = _git(repo, "for-each-ref", "--contains", t["branch"], "--format=%(refname:short)", "refs/heads/int/")
        return t["path"], any(r.startswith("int/batch") for r in (out or "").splitlines())

    with cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(trees)))) as pool:
        return dict(pool.map(one, trees))


def chart_trees(trees, rows, in_batch):
    """extract2:每棵樹每支測試取最新一列,算綠 / 紅。"""
    latest = {}
    for t, test, rc, sec, repo in rows:      # rows 已按時間排,後蓋前 = 最新
        latest.setdefault(repo, {})[test] = rc
    out = []
    for t in trees:
        rs = latest.get(t["path"]) or {}
        out.append({"branch": t.get("branch"), "path": t["path"], "ahead": t.get("ahead"),
                    "in_batch": bool(in_batch.get(t["path"])), "green": sum(1 for v in rs.values() if v == 0),
                    "red": sum(1 for v in rs.values() if v != 0)})
    return out


_SH_COUNT = {"kept": re.compile(r"沿用\s*(\d+)\s*支"), "skipped": re.compile(r"挑掉不跑\s*(\d+)\s*支"),
             "ran": re.compile(r"實跑\s*(\d+)\s*支")}
_SH_WITHOUT = re.compile(r"約\s*([\d.]+)\s*(分鐘|秒)")
_SH_ACTUAL = re.compile(r"實際\s*[::]?\s*約?\s*([\d.]+)\s*(分鐘|秒)")
_SH_REASON = re.compile(r"([^、,,::]+?)\s*[((](\d+)[))]")


def _secs(m):
    return None if not m else round(float(m.group(1)) * (60 if m.group(2) == "分鐘" else 1), 1)


def chart_shadow(root, code):
    """<狀態根>/state/shadow-explain/<席碼>.json 的 savings / required 兩段:只用正則拿數字,原文照留在 lines。"""
    d = _load(os.path.join(root, "state", "shadow-explain", code + ".json"))
    if not d:
        return None
    lines = [str(x) for x in (d.get("savings") or []) + (d.get("required") or []) if isinstance(x, str)]
    out = {"at": d.get("at"), "kept": None, "skipped": None, "ran": None, "without_s": None, "actual_s": None,
           "reasons": [], "lines": list(dict.fromkeys(lines))}
    for ln in lines:
        for k, rx in _SH_COUNT.items():
            m = rx.search(ln)
            if m and out[k] is None:
                out[k] = int(m.group(1))
        m = _SH_ACTUAL.search(ln)
        if m:
            if out["actual_s"] is None:
                out["actual_s"] = _secs(m)
        elif out["without_s"] is None:
            out["without_s"] = _secs(_SH_WITHOUT.search(ln))
        if "原因" in ln and not out["reasons"]:
            tail = re.split(r"[::]", ln, maxsplit=1)[-1]
            out["reasons"] = [[m.group(1).strip(), int(m.group(2))] for m in _SH_REASON.finditer(tail)]
    return out


def chart_dm(root, code, now):
    """extract4:collab = 7 天內我寄出的信寄給幾個不同的席;等待 = 寄給我的信,我自己標讀花幾分鐘(reads())。
    只看 7 天內改過的 q_*.json;先比位元組有沒有我的席碼才 json.loads。"""
    import statistics
    qroot = os.path.join(root, "state", "dm-queue")      # 與 dm_queue.queue_root 同一處
    cut = now - CHART_DAYS * 86400
    me = code.encode("utf-8")
    to_seats, mine = set(), []
    try:
        dirs = list(os.scandir(qroot))
    except OSError:
        dirs = []
    for de in dirs:
        if not de.is_dir():
            continue
        try:
            ents = list(os.scandir(de.path))
        except OSError:
            continue
        for e in ents:
            if not (e.name.startswith("q_") and e.name.endswith(".json")):
                continue
            try:
                if e.stat().st_mtime < cut:
                    continue
                with open(e.path, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            if me not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or (d.get("ts") or 0) <= cut:
                continue
            if d.get("sender_seat") == code and de.name != code:
                to_seats.add(de.name)
            if de.name == code:
                mine.append(d)
    reads = {}
    try:
        with open(os.path.join(qroot, code, "audit.jsonl"), encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("to") == "read" and e.get("actor") == "seat:" + code and e.get("id"):
                    reads.setdefault(e["id"], e.get("ts"))
    except OSError:
        pass
    waits = []
    for d in mine:
        base = d.get("delivered_at") or d.get("ts")
        r = reads.get(d.get("id"))
        if base and r:
            m = (r - base) / 60
            if m >= 0:
                waits.append(round(m, 1))
    speed = {"median_min": round(statistics.median(waits), 1), "n": len(waits)} if waits else None
    return len(to_seats), speed, waits


def chart_handoff(root, code, now):
    """交接:7 天內**已結束**的輪數 vs 有算數收據的輪數。判準全用 aib_handoff 自己的(rounds / receipt_between),
    跟 aib handoff status 同一條;量不到就 None,不編。"""
    import aib_handoff
    rs = [float(r["ts"]) for r in aib_handoff.rounds(code, home=root)]
    cut = now - CHART_DAYS * 86400
    done = [(s, e) for s, e in zip(rs, rs[1:]) if s >= cut]
    if not done:
        return None
    got = sum(1 for s, e in done if aib_handoff.receipt_between(code, s, e, home=root))
    return {"rounds": len(done), "receipts": got}


# ---------------------------------------------------------------- 分級與手下(頂層 tier / subs;便宜,每次都算)
# §12.2 第 3 條:人格卡還沒有 tier 欄 ⇒ 用 category 暫時對照(設計稿那張表,逐字);對不到一律 eng。
#   「專家 = 主管」在畫面上同一級 ⇒ 都算 lead;「QA = 監看」⇒ 都算 qa。
TIER_BY_CATEGORY = {
    "lead": ("客製/專家", "EZ2智囊團", "品味/審查", "策略/教練", "歷史/軍師",
             "開發/總管", "管理/總管", "開發/指揮", "治理/GodView", "開發/規劃"),
    "qa": ("開發/品質", "測試", "品質/複盤", "審查/公堂",
           "維運/值機", "維運/SRE", "營運/主機", "開發/維運", "工程/維運", "開發/足跡"),
    "design": ("開發/畫面", "行銷/內容"),
}


def captain_code(root, cabin, seats=None):
    """艦長:該艙右欄自己算好的 instances/<艙>/cockpit/ai-rowmap.json 裡畫 👑 的那列(cooldown.captain_seat 讀同一個檔;
    直接讀檔,--cabin 指別艙也對,不改環境變數);沒有 ⇒ 本艙唯一一席 root 家族;再沒有 ⇒ None(不猜)。
    seat_screen_fleet.captain_code 也走這支。"""
    if not cabin:
        return None
    if seats is None:
        seats = [(seat_address.code_of(d), d) for _f, d in _seat_files(root, cabin) if d.get("session_uuid")]
    codes = {c for c, _ in seats if c}
    rows = _load_any(os.path.join(root, "instances", cabin, "cockpit", "ai-rowmap.json"))
    for row in rows if isinstance(rows, list) else []:
        d = (row or {}).get("detail") or {} if isinstance(row, dict) else {}
        if d.get("mark") == "\U0001F451":
            label = str(d.get("label") or "").lstrip("@").rstrip("*")
            if label in codes:
                return label
    roots = [c for c, d in seats if c and str(d.get("role") or "").startswith("root")]
    return roots[0] if len(roots) == 1 else None


def _load_any(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def seat_subs(root, cabin, seat_id):
    """手下 = 本艙 reports_to 是我 seat_id 的席(只限本艙;退席 / 收工的不算)。回 [(席碼, 紀錄)]。"""
    if not cabin or not seat_id:
        return []
    out = []
    for _f, d in _seat_files(root, cabin):
        c = seat_address.code_of(d)
        if c and d.get("reports_to") == seat_id:
            out.append((c, d))
    return sorted(out, key=lambda x: x[0])


def tier_of(code, category, subs, captain):
    if code and captain and code == captain:
        return "captain"
    if subs:
        return "lead"
    for tier, cats in TIER_BY_CATEGORY.items():
        if category in cats:
            return tier
    return "eng"


# ---------------------------------------------------------------- --dm(15 秒快取)
DM_TTL_S = 15
DM_MAX = 40
DM_TEXT_MAX = 240


def _relay_summary():
    """長信落檔的展開:沿用 seat-info 的 relay_line.relay_summary(同一支,不另寫);公開版沒有那支 ⇒ 原文照回。"""
    path = os.path.join(repo_root(), "tools", "claude-mods", "aib-boards", "bin", "relay_line.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("seat_screen_relay_line", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.relay_summary
    except Exception:  # noqa: BLE001
        return lambda msg, home: msg


def dm_section(root, code, now):
    import dm_queue
    payload = dm_queue.tabs_payload(code)
    tabs = payload["tabs"]
    relay = _relay_summary()
    items, counts = [], {"unread": (payload.get("badges") or {}).get("unread"), "open": 0, "done_7d": 0}
    cut = now - CHART_DAYS * 86400
    for tab, status in (("waiting", "waiting"), ("read", "read"), ("done", "done")):
        for m in (tabs.get(tab) or {}).get("rows") or []:
            if tab != "done":
                counts["open"] += 1
            elif (m.get("done_at") or 0) >= cut:
                counts["done_7d"] += 1
            items.append({"id": m.get("id"), "sender": m.get("sender"), "sender_seat": m.get("sender_seat"),
                          "kind": m.get("kind"), "status": status, "ts": m.get("ts") or m.get("sent_at"),
                          "when": m.get("when"), "text": _clip(relay(m.get("msg") or "", root), DM_TEXT_MAX)})
    items.sort(key=lambda x: -(x["ts"] or 0))
    return {"items": items[:DM_MAX], "counts": counts, "chat": chat_rows(root, code, now, relay), "seats": seat_cards(root)}


CHAT_DAYS = 7
CHAT_MAX = 200


def chat_rows(root, code, now, relay=None):
    """「對話」頁左邊(the maintainer 10-14 03:3x「左邊改成像 telegram 這樣的對話」):近 7 天這一席收到與寄出的信,舊的在前。"""
    rows, since = [], now - CHAT_DAYS * 86400
    for path in glob.glob(os.path.join(root, "state", "dm-queue", "*", "q_*.json")):
        try:
            if os.path.getmtime(path) < since:
                continue
        except OSError:
            continue
        m = _load(path) or {}
        to, frm = str(m.get("seat") or ""), str(m.get("sender_seat") or "")
        if code not in (to, frm):
            continue
        try:
            ts = float(m.get("sent_at") or m.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < since:
            continue
        text = m.get("msg") or ""
        if relay:
            text = relay(text, root)
        # 真實狀態(the maintainer 03:4x「把訊息真實的狀態也更新上去」):完成 > 封存 / 作廢 > 卡住 > 已讀 > 未讀
        state = ("done" if m.get("done_at") else "archived" if (m.get("archived_at") or m.get("superseded_by") or m.get("deleted_at"))
                 else "held" if m.get("held_at") else "read" if m.get("read_at") else "unread")
        rows.append({"id": m.get("id"), "at": ts, "from": frm or None, "to": to, "out": frm == code, "state": state,
                     "label": m.get("sender_label") or m.get("sender"), "status": m.get("status"),
                     "kind": m.get("kind"), "text": _clip(text, 1200)})
    rows.sort(key=lambda r: r["at"])
    return rows[-CHAT_MAX:]


def brand_color(provider):
    """原廠顏色(the maintainer 10-14 03:4x「icon 與顏色一定要是原廠的,不要亂改」):render_lib.ENGINE_LETTER_COLORS 第一個真彩色,
    右欄 AI 清單與艦長室網頁(captain_room.brand_table)同一份;grok 黑白品牌沒有色 ⇒ None(畫面用預設字色)。
    在 tmux 裡(Claude Code 會降成 256 色)給 "ansi256(色表的 256 號)",不然給 #hex。"""
    try:
        sys.path.insert(0, os.path.join(HERE, "render"))
        from render_lib import ENGINE_LETTER_COLORS  # noqa: PLC0415
        cols = ENGINE_LETTER_COLORS.get(str(provider or "").lower()) or ()
        if not cols:
            return None
        import seat_now  # noqa: PLC0415
        if seat_now.claude_clamps_256():      # tmux 裡 Claude Code 自己降 256 色會降歪(橘變粉)⇒ 直接給色表指定的 256 色號
            return "ansi256(%d)" % cols[0][1]
        return "#%02x%02x%02x" % tuple(cols[0][0])
    except Exception:  # noqa: BLE001
        return None


def seat_cards(root):
    """席碼 → {標誌, 人格名, 人格圖示, 引擎};「對話」頁每則訊息頭用。人格讀不到就只給標誌與引擎。"""
    logos = seat_logos(root, None)
    out = {}
    for _path, rec in _seat_files(root, None):
        code = seat_address.code_of(rec)
        if not code:
            continue
        card = {"logo": logos.get(code), "provider": rec.get("provider"), "role": rec.get("role"),
                "color": brand_color(rec.get("provider"))}
        try:
            p = fetch_persona(root, rec.get("seat_id")) or {}
            card.update({"name": p.get("name"), "emoji": p.get("emoji")})
        except Exception:  # noqa: BLE001
            pass
        out[code] = card
    return out


# ---------------------------------------------------------------- --plan(60 秒快取)
PLAN_TTL_S = 60
PLAN_LAYER = {"outline": "goal", "goal": "goal", "major": "major", "subitem": "sub", "sub": "sub", "task": "task"}


def plan_rows(root, uid):
    """<狀態根>/projects/<uid>/plan.jsonl:只追加,同一個 uid 取 version 最大、同版取最後一列。"""
    best = {}
    order = []
    try:
        fh = open(os.path.join(root, "projects", uid, "plan.jsonl"), encoding="utf-8")
    except OSError:
        return None
    with fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("uid"):
                continue
            k = r["uid"]
            if k not in best:
                order.append(k)
            if k not in best or (r.get("version") or 0) >= (best[k].get("version") or 0):
                best[k] = r
    return [best[k] for k in order]


def plan_section(root, project):
    if not project:
        return None
    rows = plan_rows(root, project["uid"])
    if rows is None:
        return {"project_uid": project["uid"], "title": project.get("title"), "nodes": []}
    kids = {}
    for r in rows:
        kids.setdefault(r.get("parent_uid"), []).append(r["uid"])
    by = {r["uid"]: r for r in rows}

    def tasks_under(u):
        ks = kids.get(u) or []
        if not ks:
            return [u] if PLAN_LAYER.get(by[u].get("layer")) == "task" else []
        return [t for k in ks for t in tasks_under(k)]

    nodes = []
    for r in rows:
        p = r.get("progress")
        if not isinstance(p, (int, float)):
            ts = tasks_under(r["uid"]) if kids.get(r["uid"]) else []
            p = round(sum(1 for t in ts if by[t].get("status") == "done") / len(ts), 2) if ts else None
        nodes.append({"id": r["uid"], "parent": r.get("parent_uid"), "layer": PLAN_LAYER.get(r.get("layer"), r.get("layer")),
                      "title": r.get("title"), "status": r.get("status"), "owner": r.get("owner"), "progress": p})
    return {"project_uid": project["uid"], "title": project.get("title"), "nodes": nodes}


def _section_cache(cdir, seat_id, name, ttl, now):
    if not cdir or not seat_id:
        return None
    d = _load(os.path.join(cdir, "%s.%s.json" % (seat_id, name)))
    if d and 0 <= now - (d.get("measured_at") or 0) < ttl:
        return d.get("data")
    return None


def _section_store(cdir, seat_id, name, data, now):
    if cdir and seat_id:
        try:
            atomic_json.write_json(os.path.join(cdir, "%s.%s.json" % (seat_id, name)), {"measured_at": now, "data": data})
        except Exception:  # noqa: BLE001 —— 快取寫不進去不影響輸出
            pass


# ---------------------------------------------------------------- 圖表:lead 段
def _cards_doc(root):
    return _load(os.path.join(root, "identity", "cards.json")) or {}


def _persona_name(root, seat_id, cards):
    ident = os.path.abspath(os.path.join(HERE, "..", "identity"))
    if ident not in sys.path:
        sys.path.insert(0, ident)
    import bind_one
    try:
        uid = (bind_one.read_continuity(root, seat_id) or {}).get("persona_uid")
    except Exception:  # noqa: BLE001
        return None
    return ((cards.get("cards") or {}).get(uid) or {}).get("display_name") if uid else None


def _tmux_bin():
    return os.environ.get("AIB_TMUX_BIN") or "tmux"


def window_activity(cabin):
    """窗號 → tmux 最後活動 epoch。艙的 hub socket 名向單源 cabin_resolve.sh --hub <艙> 要(R8:不在這裡拼);
    seat_screen_fleet._idle_map 也走這支。量不到 ⇒ 拋例外,由呼叫端記 missing。"""
    rc, hub = _run(["bash", os.path.join(HERE, "cabin_resolve.sh"), "--hub", cabin])
    hub = hub.strip()
    if rc != 0 or not re.fullmatch(r"[A-Za-z0-9._-]+", hub or ""):
        raise RuntimeError("hub_unresolved")
    rc, out = _run([_tmux_bin(), "-L", hub, "list-windows", "-a", "-F", "#{window_id} #{window_activity}"])
    if rc != 0:
        raise RuntimeError("tmux_rc_%d" % rc)
    return dict(ln.split(" ", 1) for ln in out.splitlines() if " " in ln)


def seat_row(root, code, rec, cabin, cards, act, now):
    """手下(或全艦)一席一列:CTX 用右欄同一把(ctx_by_seat + lifecycle.read_machine_ctx);未讀用 dm_queue.tabs_payload。"""
    import seat_now
    row = {"code": code, "prov": rec.get("provider"), "name": _persona_name(root, rec.get("seat_id"), cards),
           "ctx": None, "idle_s": None, "unread": None, "now_state": None, "stale": None}
    a = act.get(rec.get("window_id") or "")
    if a and a.isdigit():
        row["idle_s"] = int(now - int(a))
    try:
        for d in (os.path.join(os.path.dirname(HERE), "rotation"), os.path.dirname(HERE)):
            if d not in sys.path:
                sys.path.append(d)
        from ctx_by_seat import resolve_live_session
        from lifecycle import read_machine_ctx
        live, _rb, _n = resolve_live_session(rec.get("session_uuid"), instance=cabin, window=rec.get("window_id"),
                                             since_ms=(rec.get("started_epoch") or 0) * 1000 or None)
        c = read_machine_ctx(live)
        if c:
            row["ctx"] = c.get("pct")
    except Exception:  # noqa: BLE001
        pass
    try:
        import dm_queue
        row["unread"] = (dm_queue.tabs_payload(code).get("badges") or {}).get("unread")
    except Exception:  # noqa: BLE001
        pass
    nr = seat_now.read(code, state_root=root)
    if isinstance(nr, dict):
        row["now_state"] = "idle" if nr.get("idle") else (nr.get("state") or "busy")
        row["stale"] = seat_now.is_stale_busy(nr, now)
    return row


def chart_funnel(repo, codes, main_ref, batches):
    """extract4 漏斗:分支每一顆的標題都在主線 ⇒ 3;都在主線或合包 ⇒ 2;有一部分 ⇒ 1;都沒有 ⇒ 0。
    回 [delivered(≥1), in_batch(≥2), on_main(3), total]。分支 = 工作樹分支名帶這些席碼的。"""
    wl = _git(repo, "worktree", "list", "--porcelain") or ""
    brs = []
    for blk in wl.split("\n\n"):
        m = re.search(r"^branch refs/heads/(.+)$", blk, re.M)
        if m and any(c in m.group(1) for c in codes):
            brs.append(m.group(1))
    sm = set((_git(repo, "log", main_ref, "--format=%s", "-n", "3000") or "").splitlines())
    sb = set()
    for b in batches:
        sb |= set((_git(repo, "log", b, "--format=%s", "-n", "400") or "").splitlines())

    def stage(b):
        subs = [x for x in (_git(repo, "log", "%s..%s" % (main_ref, b), "--format=%s") or "").splitlines() if x]
        if not subs or all(x in sm for x in subs):
            return 3
        if all(x in sm or x in sb for x in subs):
            return 2
        return 1 if any(x in sm or x in sb for x in subs) else 0

    with cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(brs)))) as pool:
        st = list(pool.map(stage, brs))
    return [sum(1 for s in st if s >= 1), sum(1 for s in st if s >= 2), sum(1 for s in st if s >= 3), len(st)]


def chart_gantt(repo, root, project, main_ref):
    """extract4 甘特:計畫樹的 task,evidence 結尾「· <分支>」且分支存在 ⇒ 主線之後第一顆到最後一顆提交時刻。"""
    rows = plan_rows(root, project["uid"]) if project else None
    out = []
    for n in rows or []:
        if PLAN_LAYER.get(n.get("layer")) != "task":
            continue
        m = re.search(r"·\s*(\S+)$", " ".join(str(e) for e in n.get("evidence") or []))
        br = m.group(1) if m else None
        # 分支名來自計畫樹的文字(別席寫的):先過 git check-ref-format --branch,開頭是 - 的一律不收,才拿去當 ref
        if not br or br.startswith("-") or _git(repo, "check-ref-format", "--branch", br) is None \
                or not _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + br):
            continue
        ts = [int(x) for x in (_git(repo, "log", "%s..%s" % (main_ref, br), "--format=%ct") or "").split()]
        if not ts:
            ts = [int(x) for x in (_git(repo, "log", "-1", "--format=%ct", br) or "").split()]
        if ts:
            out.append({"title": n.get("title"), "owner": n.get("owner"), "status": n.get("status"), "s": min(ts), "e": max(ts)})
    return out


def chart_hot(rows, now, days=3, top=12):
    """extract2 熱點:3 天內最常跑的測試 [名, 紅, 次](全艦列)。"""
    cut = now - days * 86400
    c = {}
    for t, test, rc, _s, _r in rows:
        if t > cut:
            x = c.setdefault(os.path.basename(test or ""), [0, 0])
            x[0] += 0 if rc == 0 else 1
            x[1] += 1
    return [[k, v[0], v[1]] for k, v in sorted(c.items(), key=lambda kv: -kv[1][1])[:top]]


# ---------------------------------------------------------------- 圖表:qa 段
_GATE_REPO = re.compile(r"/wt-(ship|batch|int)|/int-|/ship-")
_CODE_AT = re.compile(r"@([0-9a-z]{5})")


def chart_fleet_trend(rows, now):
    days = _days(now)
    tr = {d: [0, 0] for d in days}
    for t, _test, rc, _s, _r in rows:
        k = time.strftime("%m-%d", time.localtime(t))
        if k in tr:
            tr[k][0 if rc == 0 else 1] += 1
    return [[d, *tr[d]] for d in days]


def chart_flaky(rows, top=20):
    """extract2:推包樹(/wt-ship…)上同一支測試 ≥3 次、有綠也有紅;依「紅的比例 × 次數」排。"""
    by = {}
    for _t, test, rc, _s, repo in rows:
        if _GATE_REPO.search(repo or ""):
            by.setdefault(test, []).append(rc)
    out = []
    for test, rcs in by.items():
        g = sum(1 for r in rcs if r == 0)
        bad = len(rcs) - g
        if g and bad and len(rcs) >= 3:
            out.append((bad / len(rcs) * len(rcs), [os.path.basename(test or ""), g, bad]))
    out.sort(key=lambda x: -x[0])
    return [x[1] for x in out[:top]]


def chart_pareto(sdir, now, days=3):
    """extract4:batch-pushgate.jsonl 3 天內 verdict=must-run 的 why 計數(先比位元組再 json.loads)。"""
    import datetime as dt
    cut = now - days * 86400
    why = {}
    try:
        fh = open(os.path.join(sdir, "batch-pushgate.jsonl"), "rb")
    except OSError:
        return None
    with fh:
        for ln in fh:
            if b'"must-run"' not in ln:
                continue
            try:
                r = json.loads(ln)
                t = dt.datetime.strptime(r.get("time", ""), "%Y-%m-%d %H:%M:%S").timestamp()
            except (ValueError, TypeError):
                continue
            if t >= cut and r.get("verdict") == "must-run" and r.get("why"):
                why[r["why"]] = why.get(r["why"], 0) + 1
    return [[k, n] for k, n in sorted(why.items(), key=lambda kv: -kv[1])]


def _logs_dir(root):
    return os.path.join(root, "logs")


def chart_gate_events(root, now, hours=48):
    """extract2:now-gate.log 的 BLOCK 列 ⇒ gate_blocks(全期,依席計)與 48 小時事件;report-lint-stop 擋下的也進事件。"""
    blocks, events = {}, []
    cut = now - hours * 3600
    try:
        with open(os.path.join(_logs_dir(root), "now-gate.log"), encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if "BLOCK" not in ln:
                    continue
                m = _CODE_AT.search(ln)
                if not m:
                    continue
                blocks[m.group(1)] = blocks.get(m.group(1), 0) + 1
                t = _iso_epoch(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", ln.split(" ", 1)[0]))
                if t and t > cut:
                    events.append({"t": int(t), "who": m.group(1), "kind": "now_gate"})
    except OSError:
        blocks = None
    try:
        with open(os.path.join(_logs_dir(root), "report-lint-stop.jsonl"), "rb") as fh:
            for ln in fh:
                if b'"verdict": "pass"' in ln:
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                t = _iso_epoch(str(e.get("ts")))
                if t and t > cut and e.get("verdict") not in ("pass", None):
                    events.append({"t": int(t), "who": e.get("session"), "kind": "report_lint"})
    except OSError:
        pass
    events.sort(key=lambda e: e["t"])
    gb = None if blocks is None else [[k, n] for k, n in sorted(blocks.items(), key=lambda kv: -kv[1])[:10]]
    return gb, events


def produce_charts(root, rec, cabin, *, repo=None, now=None, dm_unread=None, tier="eng", subs=(), project=None):
    t0 = time.monotonic()
    at = int(now if now is not None else time.time())
    repo = repo or repo_root()
    code = seat_address.code_of(rec)
    seat_id = rec.get("seat_id")
    cdir = cache_dir_of(root, cabin)
    missing = []
    if not code:
        return {"trees": [], "shadow": None, "trend": [], "slowest": [], "commits": [], "files": [], "hex": None,
                "measured_at": at, "took_ms": 0, "missing": ["code:no_session_uuid"]}
    took = {}
    pool = _Pool(max_workers=12)
    f_main = pool.submit(fetch_main, repo)
    f_batches = pool.submit(_batch_refs, repo)
    f_trees = pool.submit_cache(lambda: fetch_trees(repo, code, f_main.result(timeout=SENSOR_TIMEOUT_S), cdir, seat_id, at))
    f_sdir = pool.submit(shadow_dir)
    rstats = {}
    f_rows = pool.submit_cache(lambda: scan_rows(at, f_sdir.result(timeout=SENSOR_TIMEOUT_S),
                                           agg_path=os.path.join(cdir, "rows-agg.json") if cdir else None, stats=rstats))
    f_shadow = pool.submit(chart_shadow, root, code)
    f_dm = pool.submit(chart_dm, root, code, at)
    f_hand = pool.submit(chart_handoff, root, code, at)
    f_unread = pool.submit(fetch_dm, code) if dm_unread is None else None
    deadline = time.monotonic() + CHARTS_DEADLINE_S

    def get(name, fu, default=None):
        try:
            return fu.result(timeout=max(0.0, deadline - time.monotonic()))
        except (cf.TimeoutError, subprocess.TimeoutExpired):
            missing.append(name + ":timeout")
        except Exception as e:  # noqa: BLE001
            missing.append("%s:%s" % (name, (str(e) or e.__class__.__name__)[:60]))
        return default

    main = get("main_head", f_main)
    batches = get("batches", f_batches, [])
    is_lead = tier == "lead" or bool(subs)
    if main and is_lead:      # 重的 git 先丟出去,跟下面的掃描並行
        f_funnel = pool.submit(chart_funnel, repo, [code, *subs], main["ref"], batches)
        f_gantt = pool.submit(chart_gantt, repo, root, project, main["ref"])
    if is_lead:
        f_act = pool.submit(window_activity, cabin)
        f_cards = pool.submit(_cards_doc, root)
    if tier == "qa":
        f_pareto = pool.submit(lambda: chart_pareto(f_sdir.result(timeout=SENSOR_TIMEOUT_S), at))
        f_gate = pool.submit(chart_gate_events, root, at)
    trees = get("trees", f_trees, [])
    all_rows = get("rows", f_rows, [])
    took["rows_and_trees_wait"] = int((time.monotonic() - t0) * 1000)
    took["rows_scan"] = rstats.get("ms")
    took["rows_bytes"] = rstats.get("bytes")
    took["rows_reused"] = rstats.get("reused_rows")
    if rstats.get("budget_hit"):
        missing.append("rows:budget")
    rows = mine_rows(all_rows, [t["path"] for t in trees], code)
    trees_out, commits, files = [], [], []
    if main and trees:
        f_in = pool.submit(chart_in_batch, repo, trees, batches)
        f_com = pool.submit(chart_commits, repo, code, [t["branch"] for t in trees if t.get("branch")], main["ref"], batches, at)
        trees_out = chart_trees(trees, rows, get("in_batch", f_in, {}))
        commits, files = get("commits", f_com, ([], []))
    trend, slowest = chart_trend_slowest(rows, at)
    shadow = get("shadow", f_shadow)
    collab, speed, waits = get("dm_scan", f_dm, (None, None, []))
    handoff = get("handoff", f_hand)
    if dm_unread is None and f_unread is not None:
        rows_dm = get("inbox", f_unread)
        dm_unread = None if rows_dm is None else sum(1 for m in rows_dm if is_unread(m))
    green, red = sum(x[1] for x in trend), sum(x[2] for x in trend)
    out = {
        "trees": trees_out, "shadow": shadow, "trend": trend, "slowest": slowest, "commits": commits, "files": files,
        "hex": {"success": {"green": green, "red": red}, "speed": speed,
                "inbox": {"unread": dm_unread}, "handoff": handoff, "collab": {"seats": collab}},
    }
    took["eng"] = int((time.monotonic() - t0) * 1000)
    if is_lead:
        t1 = time.monotonic()
        act = get("lead_idle", f_act, {})
        cards = get("lead_cards", f_cards, {})
        sub_recs = seat_subs(root, cabin, seat_id)
        f_rows_sub = [pool.submit(seat_row, root, c, d, cabin, cards, act, at) for c, d in sub_recs]
        out["lead"] = {
            "subs": [r for r in (get("lead_sub", fu) for fu in f_rows_sub) if r],
            "funnel": get("funnel", f_funnel) if main else None,
            "wait": waits or [],
            "gantt": (get("gantt", f_gantt) or []) if main else [],
            "hot": chart_hot(all_rows, at),
        }
        took["lead"] = int((time.monotonic() - t1) * 1000)
    if tier == "qa":
        t1 = time.monotonic()
        gb, events = get("gate", f_gate, (None, []))
        out["qa"] = {"fleet_trend": chart_fleet_trend(all_rows, at), "flaky": chart_flaky(all_rows),
                     "pareto": get("pareto", f_pareto), "gate_blocks": gb, "events": events}
        took["qa"] = int((time.monotonic() - t1) * 1000)
    if tier == "captain":
        t1 = time.monotonic()
        try:
            from seat_screen_fleet import fleet_charts
        except ImportError:
            missing.append("captain:not-installed")
        else:
            try:
                out["captain"] = fleet_charts(root, cabin, at)
            except Exception as e:  # noqa: BLE001
                missing.append("captain:" + (str(e) or e.__class__.__name__)[:60])
        took["captain"] = int((time.monotonic() - t1) * 1000)
    pool.shutdown(wait=False, cancel_futures=True)
    out.update({"measured_at": at, "took_ms": int((time.monotonic() - t0) * 1000), "took_ms_by": took, "missing": missing})
    if cdir and seat_id:
        try:
            _store_agg(os.path.join(cdir, seat_id + ".charts.json"), out)
        except Exception as e:  # noqa: BLE001
            out["missing"].append("cache_write:" + e.__class__.__name__)
    return out


def cached_charts(root, cabin, seat_id, now=None):
    cdir = cache_dir_of(root, cabin)
    if not cdir or not seat_id:
        return None
    d = _load(os.path.join(cdir, seat_id + ".charts.json"))
    now = time.time() if now is None else now
    if d and 0 <= now - (d.get("measured_at") or 0) < CHARTS_TTL_S:
        return dict(d, cached=True)
    return None


def brief(d):
    """--brief:去掉 detail / data / 人格段落,一行看得完的那份。"""
    out = dict(d)
    if out.get("persona"):
        out["persona"] = {k: v for k, v in out["persona"].items() if k not in ("duties", "red_lines")}
    out["know"] = [{k: v for k, v in c.items() if k not in ("detail", "data")} for c in d.get("know") or []]
    return out


def _seat_args(p):
    p.add_argument("--seat")
    p.add_argument("--seat-id")
    p.add_argument("--cabin")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = seats_dir_lib.state_root()[0]
    if argv[:1] == ["todo"]:
        ap = argparse.ArgumentParser(prog="seat_screen.py todo")
        ap.add_argument("op", choices=("add", "done", "list"))
        ap.add_argument("--text")
        ap.add_argument("--group", choices=TODO_GROUPS, default="self")
        ap.add_argument("--ref")
        ap.add_argument("--id")
        _seat_args(ap)
        a = ap.parse_args(argv[1:])
        _f, rec, _c, _s = resolve(a, root)
        if not rec or not rec.get("seat_id"):
            print(json.dumps({"ok": False, "err": "seat_not_found"}))
            return 2
        sid, code = rec["seat_id"], seat_address.code_of(rec)
        if a.op == "list":
            print(json.dumps(ledger_open(root, sid), ensure_ascii=False))
            return 0
        if a.op == "add":
            if not (a.text or "").strip():
                print(json.dumps({"ok": False, "err": "text_required"}))
                return 2
            row = {"op": "add", "id": "t_" + secrets.token_hex(4), "text": a.text.strip(), "group": a.group,
                   "at": int(time.time()), "by": "@" + code if code else None}
            if a.ref:
                row["ref"] = a.ref
        else:
            if not TODO_ID_RE.fullmatch(a.id or "") or a.id not in {r["id"] for r in ledger_open(root, sid)}:
                print(json.dumps({"ok": False, "err": "id_not_open"}))
                return 2
            row = {"op": "done", "id": a.id, "at": int(time.time()), "by": "@" + code if code else None}
        ledger_append(root, sid, row)
        print(json.dumps({"ok": True, "id": row["id"], "op": row["op"]}, ensure_ascii=False))
        return 0

    ap = argparse.ArgumentParser(prog="seat_screen.py")
    _seat_args(ap)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--charts", action="store_true")
    ap.add_argument("--dm", action="store_true")
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args(argv)
    seat_file, rec, cabin, _src = resolve(a, root)
    if not rec or not rec.get("seat_id"):
        print(json.dumps({"schema": SCHEMA, "seat_id": None, "code": None, "cabin": cabin, "missing": ["seat:not_found"]}))
        return 2
    d = None if a.fresh else cached(root, cabin, rec.get("seat_id"))
    d = d or produce(root, seat_file, rec, cabin)
    if a.charts:   # 重的:只有帶 --charts 才算;自己的快取 60 秒
        unread = next((c["data"].get("unread") for c in d.get("know") or [] if c.get("sensor") == "inbox"
                       and c.get("level") != "unknown"), None)
        ch = None if a.fresh else cached_charts(root, cabin, rec.get("seat_id"))
        d = dict(d, charts=ch or produce_charts(root, rec, cabin, dm_unread=unread, tier=d.get("tier") or "eng",
                                                subs=d.get("subs") or [], project=d.get("project")))
    now = int(time.time())
    cdir, sid = cache_dir_of(root, cabin), rec.get("seat_id")
    for flag, name, ttl, fn in (("dm", "dm", DM_TTL_S, lambda: dm_section(root, d.get("code"), now) if d.get("code") else None),
                                ("plan", "plan", PLAN_TTL_S, lambda: plan_section(root, d.get("project")))):
        if not getattr(a, flag):
            continue
        sec = None if a.fresh else _section_cache(cdir, sid, name, ttl, now)
        if sec is None:
            t1 = time.monotonic()
            try:
                sec = fn()
            except Exception as e:  # noqa: BLE001
                d = dict(d, missing=list(d.get("missing") or []) + ["%s:%s" % (name, (str(e) or e.__class__.__name__)[:60])])
            else:
                _section_store(cdir, sid, name, sec, now)
                d = dict(d, took_ms_by=dict(d.get("took_ms_by") or {}, **{name: int((time.monotonic() - t1) * 1000)}))
        d = dict(d, **{name: sec})
    print(json.dumps(brief(d) if a.brief else d, ensure_ascii=False))
    return 0


def entry(fn, argv=None):
    """CLI 入口:fn 出例外也印一份合法 JSON(missing 寫例外類別),然後一律走 finish()。"""
    try:
        rc = fn(argv)
    except SystemExit as e:          # argparse 用法錯
        rc = e.code if isinstance(e.code, int) else 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"schema": SCHEMA, "missing": ["internal:" + e.__class__.__name__]}))
        rc = 1
    finish(rc or 0)


if __name__ == "__main__":
    entry(main)
