#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat tidy:開發機的兩種「沒人收的東西」—— 孤兒常駐、已合併的舊工作樹(10-25 Andy 准「先做第一組」)。

  aib room heat tidy [--json] [--seat <席碼>]                 列出孤兒常駐 + 工作樹(只讀)
  aib room heat tidy archive <編號>… | --all-mine --seat <席碼> [--dry-run] [--json]   封存自己席已合併的舊樹
  aib room heat tidy notify [--via dm|popup] [--dry-run] [--json]
      dm(預設):孤兒常駐通知主人席(aib dm notice,地址自動補艙位碼);popup:一則本機系統通知給坐在這台前面的人
      (艦長室自動開關用這種,不經 dm、沒有寄件人問題);兩種各自 6 小時內同一支不重發

孤兒常駐 daemon_orphan:自己 uid、父行程 1、直譯器跑的腳本 realpath 後在工作樹根(~/wt.noindex)底下、活過 1 小時。
  主人席 = 工作樹資料夾名裡的五碼席碼(int0155-abb89 ⇒ abb89);認不出 ⇒ unknown。
  只通知、不殺:常駐的腳本不在暫存根底下,不在 release 白名單;砍別席的 pid 要主人自己收(真艙不准寬 pkill)。
已合併的舊樹 worktree:工作樹根底下每一棵 git 工作樹。可封存(archivable)要五條全過,否則 state=kept + 原因代碼:
  not_merged  頭碼不在 origin/main 裡(squash 合併也算這種,往不收那邊錯)
  dirty       有改動或未追蹤檔(ignored 不算;只准有 node_modules 捷徑,而且真的是捷徑)
  stashed     stash 清單裡有在這條分支上存的
  busy        有行程的 cwd / 開著的檔在樹裡,或正在跑樹裡的檔案
  recent      2 小時內動過(index / HEAD / 資料夾)
  locked      git worktree lock 過
判「已合併」前每個倉 git fetch origin main 一次(只在 tidy 時,不進 5 秒快照;fetch 失敗照本機的 origin/main 判,舊的只會少判)。
封存 = 搬前先寫還原指令 <名>.RESTORE.txt,再整棵搬到 ~/Ez2-backup.noindex/wt-<席>-merged-<日期>/(不刪;跨磁碟搬不了 ⇒
  move_failed,不複製)再 git worktree prune。還原檔放在樹旁邊不放樹裡(放樹裡,搬回去那棵就多一個未追蹤檔)。
--seat 只是歸屬提示(樹名尾碼誰都能取),不是防線;真正擋的是上面五條 + 先 --dry-run 列給人看。
  只搬 --seat 那席的樹(not_mine);執行前重掃一次,編號對不上 ⇒ candidate_changed。
手動 = 免費;自動(排程)在艦長室外掛的付費開關,核心只提供這三個指令。
隱私:輸出不放指令列、不放全路徑;樹名 / 分支名 / 程式名都過 safe_name。detail 代碼是契約,人話放 hint。
錯誤代碼:bad_candidate candidate_changed not_mine move_failed scan_failed notify_failed log_failed(+ 上面五個 kept 原因)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import room_heat as RH            # noqa: E402
import room_heat_release as RR    # noqa: E402

WT_ROOT = os.path.expanduser("~/wt.noindex")
BACKUP_ROOT = os.path.expanduser("~/Ez2-backup.noindex")
ORPHAN_MIN_AGE_S = 3600
IDLE_MIN_S = 2 * 3600
NOTIFY_EVERY_S = 6 * 3600
SEAT_RE = re.compile(r"(?:^|-)([0-9a-f]{5})(?=-|$)")
ID_RE = re.compile(r"^[0-9a-f]{16}$")
ALLOWED_UNTRACKED = ("?? node_modules",)
STASH_RE = re.compile(r"^(?:WIP on|On) (.+?):")
KEPT = ("not_merged", "dirty", "stashed", "busy", "recent", "locked")


def owner_of_tree(name):
    m = SEAT_RE.search(name or "")
    return m.group(1) if m else "unknown"


def _hid(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _tree_of(path, root):
    """path 在 root 底下 ⇒ 第一層資料夾名;否則 None。都先 realpath(/x/../ 繞不過)。"""
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    if not rp.startswith(rr.rstrip("/") + "/"):
        return None
    return rp[len(rr.rstrip("/")) + 1:].split("/", 1)[0] or None


# ---------- 孤兒常駐 ----------

def orphans(rows=None, root=WT_ROOT, uid=None, ident=None, now=None, min_age_s=ORPHAN_MIN_AGE_S):
    """[{id, kind, owner, tree, program, age_s, cpu, children}];ps 量不到 ⇒ None。"""
    rows = RH.list_procs() if rows is None else rows
    if rows is None:
        return None
    uid = os.getuid() if uid is None else uid
    ident = ident or RR.identity
    now = time.time() if now is None else now
    kids = {}
    for r in rows:
        kids.setdefault(r[1], []).append(r)
    out = []
    for pid, ppid, u, cpu, _rss, _name, args in rows:
        if u != uid or ppid != 1:
            continue
        sc = RR._script(args)
        tree = _tree_of(sc, root) if sc else None
        if not tree:
            continue
        idt = ident(pid)
        if not idt or idt[0] != uid or idt[1] != 1:
            continue
        age = now - RR._start_s(idt)
        if age < min_age_s:
            continue
        desc, stack = [], list(kids.get(pid, ()))
        while stack and len(desc) < 500:
            k = stack.pop()
            desc.append(k)
            stack.extend(kids.get(k[0], ()))
        out.append({"id": _hid("daemon_orphan", pid, idt[2], uid), "kind": "daemon_orphan", "pid": pid,
                    "owner": owner_of_tree(tree), "tree": RH.safe_name(tree, 32),
                    "program": RH.safe_name(os.path.basename(sc), 32), "age_s": int(age),
                    "cpu": round(cpu + sum(k[3] for k in desc), 1), "children": len(desc)})
    return sorted(out, key=lambda o: -o["age_s"])


# ---------- 工作樹 ----------

def _git(path, *args, timeout=20):
    try:
        p = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, p.stdout


def _busy_cwds(run=None, rows=None):
    """自己 uid 每支行程的 cwd / 開著的檔 + 正在跑的絕對路徑引數(realpath);lsof / ps 跑不了 ⇒ None(全部當 busy,往不收那邊錯)。
    只看 cwd 不夠:常駐腳本 cwd 常在家目錄,樹裡的腳本還在跑就搬走,它下一次 import 會壞(10-25 int0155 孤兒)。"""
    try:
        out = (run or RH._run)(["lsof", "-n", "-P", "-u", str(os.getuid()), "-Fn"], timeout=30)
    except Exception:  # noqa: BLE001
        return None
    rows = RH.list_procs() if rows is None else rows
    if not out or rows is None:
        return None
    paths = {os.path.realpath(l[1:]) for l in out.splitlines() if l.startswith("n/")}
    uid = os.getuid()
    for r in rows:
        if r[2] == uid:
            paths.update(os.path.realpath(t) for t in RR._argv(r[6]) if t.startswith("/"))
    return paths


def _check(path, git, cwds, now):
    """一棵樹的狀態:(merged, 原因代碼或 None, 分支, 頭碼, 共用 git 目錄, idle 秒)。"""
    rc, head = git(path, "rev-parse", "HEAD")
    rc2, info = git(path, "rev-parse", "--abbrev-ref", "HEAD", "--git-dir", "--git-common-dir")
    if rc != 0 or rc2 != 0 or len(info.split("\n")) < 3:
        return None
    head = head.strip()
    branch, gitdir, common = info.split("\n")[:3]
    gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(path, gitdir)
    common = common if os.path.isabs(common) else os.path.join(path, common)
    merged = git(path, "merge-base", "--is-ancestor", head, "origin/main")[0] == 0
    rc3, st = git(path, "status", "--porcelain", "--untracked-files=normal")
    dirty = rc3 != 0 or any(l and (l not in ALLOWED_UNTRACKED or not os.path.islink(os.path.join(path, l[3:])))
                            for l in st.splitlines())
    rc4, stashes = git(path, "stash", "list", "--format=%gs")
    stashed = rc4 != 0 or any((STASH_RE.match(l) or [None, None])[1] == branch for l in stashes.splitlines())
    mt = 0.0
    for p in (os.path.join(gitdir, "index"), os.path.join(gitdir, "HEAD"), path):
        try:
            mt = max(mt, os.stat(p).st_mtime)
        except OSError:
            pass
    idle = now - mt
    rp = os.path.realpath(path).rstrip("/")
    busy = cwds is None or any(c == rp or c.startswith(rp + "/") for c in cwds)
    reason = ("not_merged" if not merged else "dirty" if dirty else "stashed" if stashed else "busy" if busy
              else "locked" if os.path.exists(os.path.join(gitdir, "locked")) else "recent" if idle < IDLE_MIN_S else None)
    return merged, reason, branch, head, common, idle


def worktrees(root=WT_ROOT, git=None, cwds=None, now=None, lsof=None, rows=None, fetch=True):
    """[{id, name, owner, branch, state, reason, idle_s}];工作樹根不在 ⇒ []。"""
    git = git or _git
    now = time.time() if now is None else now
    cwds = _busy_cwds(lsof, rows) if cwds is None else cwds
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out, fetched = [], set()
    for name in names:
        path = os.path.join(root, name)
        if os.path.islink(path) or not os.path.isfile(os.path.join(path, ".git")):
            continue                                   # 只認 git worktree(.git 是檔);主倉 / 捷徑 / 一般資料夾不碰
        if fetch:
            rc, common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            common = common.strip()
            if rc == 0 and common and common not in fetched:
                fetched.add(common)
                git(path, "fetch", "-q", "origin", "main", timeout=60)
        c = _check(path, git, cwds, now)
        if not c:
            continue
        merged, reason, branch, head, common, idle = c
        out.append({"id": _hid("worktree", os.path.realpath(path), head), "name": RH.safe_name(name, 40),
                    "owner": owner_of_tree(name), "branch": RH.safe_name(branch, 60), "merged": merged,
                    "state": "kept" if reason else "archivable", "reason": reason, "idle_s": int(idle),
                    "_path": path, "_common": common, "_head": head, "_branch": branch})
    return out


def public(w):
    return {k: v for k, v in w.items() if not k.startswith("_")}


def _log(rec, environ=None):
    d = RH.state_dir(environ)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(d, "heat-tidy.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _write_restore(path, w, dest):
    """搬之前先寫還原指令(搬到一半斷電也有得還原)。prune 後管理資料已清,所以用 worktree add 重建再補回 ignored 檔。"""
    repo = os.path.dirname(w["_common"].rstrip("/")) if os.path.basename(w["_common"].rstrip("/")) == ".git" else w["_common"]
    ref = w["_branch"] if w["_branch"] != "HEAD" else w["_head"]
    lines = ["# aib room heat tidy archive  %s" % time.strftime("%Y-%m-%d %H:%M"),
             "# 原位置 %s  分支 %s  頭碼 %s" % (w["_path"], w["_branch"], w["_head"]),
             "git -C %s worktree add %s%s %s" % (_q(repo), "" if w["_branch"] != "HEAD" else "--detach ", _q(w["_path"]), _q(ref)),
             "rsync -a --ignore-existing %s/ %s/   # 補回 ignored 檔(建置產物 / 捷徑)" % (_q(dest), _q(w["_path"])),
             "# 確認沒問題再刪這份備份:%s" % dest, ""]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines))


def archive(ids=(), all_mine=False, seat="", dry_run=False, scan_fn=None, now=None, backup_root=BACKUP_ROOT,
            git=None, environ=None):
    """{ok, detail?, results:[{id, name?, detail}]}。"""
    git = git or _git
    now = time.time() if now is None else now
    bad = [i for i in ids if not ID_RE.match(i or "")]
    if bad or not re.fullmatch(r"[0-9a-f]{5}", seat or ""):
        return {"ok": False, "detail": "bad_candidate", "results": [{"id": RH.safe_name(i, 16), "detail": "bad_candidate"} for i in bad]}
    trees = (scan_fn or worktrees)()
    by_id = {w["id"]: w for w in trees}
    pick = [w for w in trees if w["owner"] == seat and w["state"] == "archivable"] if all_mine else []
    results = []
    for i in ids:
        w = by_id.get(i)
        if not w:
            results.append({"id": i, "detail": "candidate_changed"})
        elif w["owner"] != seat:
            results.append({"id": i, "name": w["name"], "detail": "not_mine"})
        elif w["reason"]:
            results.append({"id": i, "name": w["name"], "detail": w["reason"]})
        else:
            pick.append(w)
    day = time.strftime("%Y%m%d", time.localtime(now))
    pruned = set()
    for w in pick:
        if dry_run:
            results.append({"id": w["id"], "name": w["name"], "detail": "would_archive"})
            continue
        dest_dir = os.path.join(backup_root, "wt-%s-merged-%s" % (seat, day))
        dest = os.path.join(dest_dir, os.path.basename(w["_path"]))
        n = 2
        while os.path.lexists(dest) or os.path.lexists(dest + ".RESTORE.txt"):
            dest = os.path.join(dest_dir, "%s-%d" % (os.path.basename(w["_path"]), n))
            n += 1
        try:
            os.makedirs(dest_dir, mode=0o700, exist_ok=True)
            _write_restore(dest + ".RESTORE.txt", w, dest)
            os.rename(w["_path"], dest)                # 同一顆磁碟才搬得動;EXDEV ⇒ move_failed,不退成複製 + 刪
        except OSError:
            if not os.path.lexists(dest):
                try:
                    os.unlink(dest + ".RESTORE.txt")
                except OSError:
                    pass
            results.append({"id": w["id"], "name": w["name"], "detail": "move_failed"})
            continue
        pruned.add(w["_common"])
        r = {"id": w["id"], "name": w["name"], "detail": "archived"}
        if not _log({"t": round(now, 3), "act": "archive", "owner": seat, "name": w["name"], "branch": w["branch"]}, environ):
            r["log"] = "log_failed"
        results.append(r)
    for common in sorted(pruned):                      # 全部搬完才 prune(搬一棵 prune 一次,後搬的那幾棵會留著 prunable)
        git(common, "worktree", "prune")
    return {"ok": all(r["detail"] in ("archived", "would_archive") for r in results), "results": results}


# ---------- 通知 ----------

def _notified_path(environ=None, via="dm"):
    return os.path.join(RH.state_dir(environ), "heat-tidy-notified.json" if via == "dm" else "heat-tidy-popped.json")


def _read_notified(environ=None, via="dm"):
    raw = RH.safe_read(_notified_path(environ, via), 256 * 1024)
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    return {k: v for k, v in d.items() if ID_RE.match(str(k)) and isinstance(v, (int, float))} if isinstance(d, dict) else {}


def _write_notified(d, environ=None, via="dm"):
    p = _notified_path(environ, via)
    try:
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        tmp = p + ".tmp%d" % os.getpid()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(d, fh)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def address_of(owner, environ=None):
    """席碼 ⇒ 「艙位碼/席碼」(這台機器上活著的那一艙);找不到 ⇒ None。
    不能只寫席碼:不在席位裡叫 aib dm(艦長室排程、launchd)時光席碼會被擋 invalid(10-25 實測)。"""
    try:
        import dm_address
        import seats_dir_lib
        root, _src = seats_dir_lib.state_root(environ)
        live = [sock for sock, alive in dm_address.lookup(owner, root) if alive]
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"-([0-9a-z]{4})$", live[0]) if live else None
    return "%s/%s" % (m.group(1), owner) if m else None


def _send_dm(owner, text):
    addr = address_of(owner)
    if not addr:
        return False
    try:
        p = subprocess.run(["aib", "dm", "--to", addr, "--kind", "notice", text], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _popup(title, text, platform=None, run=None):
    """本機系統通知(10-25 Andy:「這個功能是在本機上，直接彈出警示也可以」)。字串走 argv,不拼進 AppleScript;
    macOS osascript、Linux notify-send;都沒有 ⇒ False。"""
    plat = platform or sys.platform
    if plat == "darwin":
        cmd = ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
               "-e", "end run", title, text]
    elif plat.startswith("linux") and shutil.which("notify-send"):
        cmd = ["notify-send", "--", title, text]
    else:
        return False
    try:
        return (run or subprocess.run)(cmd, capture_output=True, timeout=15, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _line(o):
    return "pid %d %s (%s) age %dh cpu %.1f%% children %d" % (
        o["pid"], o["program"], o["tree"], o["age_s"] // 3600, o["cpu"], o["children"])


def notify(dry_run=False, scan_fn=None, send=None, now=None, environ=None, via="dm", popup=None):
    """via=dm:孤兒按主人席合成一封 aib dm notice;unknown 主人不發(不知道寄給誰),照樣列出。
    via=popup:全部(含 unknown)合成一則本機系統通知,給坐在這台前面的人看。兩種各自 6 小時去重。"""
    now = time.time() if now is None else now
    found = (scan_fn or orphans)()
    if found is None:
        return {"ok": False, "detail": "scan_failed", "results": []}
    seen = {k: v for k, v in _read_notified(environ, via).items() if now - v < NOTIFY_EVERY_S}
    by_owner = {}
    results = []
    for o in found:
        if o["id"] in seen:
            results.append({"id": o["id"], "owner": o["owner"], "detail": "already_notified"})
        elif o["owner"] == "unknown" and via == "dm":
            results.append({"id": o["id"], "owner": "unknown", "detail": "owner_unknown"})
        else:
            by_owner.setdefault("*" if via == "popup" else o["owner"], []).append(o)
    for owner, items in sorted(by_owner.items()):
        if via == "popup":
            body = "\n".join(["@%s %s" % (o["owner"], _line(o)) for o in items[:5]] + [RH.T("tidy.popup_hint")])
            ok = True if dry_run else (popup or _popup)(RH.T("tidy.popup_title", n=len(items)), body)
        else:
            lines = ["[aib room heat tidy] daemon_orphan ×%d" % len(items)] + [_line(o) for o in items] + [RH.T("tidy.notify_hint")]
            ok = True if dry_run else (send or _send_dm)(owner, "\n".join(lines))
        for o in items:
            results.append({"id": o["id"], "owner": o["owner"], "detail": ("would_notify" if dry_run else "notified") if ok else "notify_failed"})
            if ok and not dry_run:
                seen[o["id"]] = now
    if not dry_run:
        _write_notified(seen, environ, via)
    return {"ok": all(r["detail"] != "notify_failed" for r in results), "results": results}


def snapshot(seat="", orphan_fn=None, tree_fn=None):
    o = (orphan_fn or orphans)()
    trees = [public(w) for w in (tree_fn or worktrees)()]
    if seat:
        trees = [w for w in trees if w["owner"] == seat]
    return {"schema": "aib-heat-tidy/v1", "state": "ok" if o is not None else "unmeasured",
            "orphans": o or [], "worktrees": trees,
            "archivable": sum(1 for w in trees if w["state"] == "archivable")}


def main(argv, out=None):
    import argparse
    out = out or sys.stdout
    argv = list(argv)
    sub = argv.pop(0) if argv and argv[0] in ("archive", "notify") else "list"
    ap = argparse.ArgumentParser(prog="aib room heat tidy" + ("" if sub == "list" else " " + sub))
    ap.add_argument("--json", action="store_true")
    if sub in ("list", "archive"):
        ap.add_argument("--seat", default="")
    if sub == "archive":
        ap.add_argument("ids", nargs="*")
        ap.add_argument("--all-mine", action="store_true")
    if sub in ("archive", "notify"):
        ap.add_argument("--dry-run", action="store_true")
    if sub == "notify":
        ap.add_argument("--via", choices=("dm", "popup"), default="dm")
    a = ap.parse_args(argv)
    if sub == "archive":
        if bool(a.ids) == bool(a.all_mine) or not a.seat:
            out.write(RH.T("tidy.usage") + "\n")
            return 2
        r = archive(ids=a.ids, all_mine=a.all_mine, seat=a.seat, dry_run=a.dry_run)
    elif sub == "notify":
        r = notify(dry_run=a.dry_run, via=a.via)
    else:
        r = snapshot(seat=a.seat)
    if a.json:
        json.dump(r, out, ensure_ascii=False)
        out.write("\n")
    elif sub == "list":
        if r["state"] != "ok":
            out.write(RH.T("tidy.unmeasured") + "\n")
        out.write(RH.T("tidy.orphans", n=len(r["orphans"])) + "\n")
        for o in r["orphans"]:
            out.write("  %s @%-7s %-24s %-22s %4dh %5.1f%%\n" % (o["id"], o["owner"], o["program"], o["tree"], o["age_s"] // 3600, o["cpu"]))
        out.write(RH.T("tidy.worktrees", n=len(r["worktrees"]), a=r["archivable"]) + "\n")
        for w in r["worktrees"]:
            out.write("  %s @%-7s %-34s %s\n" % (w["id"], w["owner"], w["name"], RH.T("tidy." + (w["reason"] or "archivable"))))
    else:
        for x in r["results"]:
            out.write("  %s %s %s\n" % (x["id"], x.get("name") or x.get("owner", ""), RH.T("tidy." + x["detail"])))
        if not r["results"]:
            out.write(RH.T("tidy." + r["detail"] if r.get("detail") else "tidy.none") + "\n")
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
