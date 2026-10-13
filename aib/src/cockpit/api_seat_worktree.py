#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""API 家開發席的專屬工作目錄(the maintainer 09-28 00:4x:「右鍵新開席就能寫程式」)。

API 席(deepseek/nvidia/openrouter)的寫檔與 git 只准落在
AIB_API_WORKTREE 指的那棵樹(deepseek_cli.LocalWorkspace)。以前這棵樹要人手建、手設,
沒設 ⇒ 席只能讀,寫檔回「此席未在自己的 worktree 中」。本檔讓 aib 開席當下自動備好:

    api_seat_worktree.py ensure --repo <開席目錄> --uuid <這一席的 session uuid> --root <樹根>

  成功 rc=0,stdout 只印一行:工作目錄絕對路徑(aib 拿它當 --cwd 與 AIB_API_WORKTREE)。
  失敗 rc≠0,stdout 空、原因在 stderr;aib 照開席(只讀),不擋席起來。

命名沿用 09-28 三席手建的慣例:路徑 <root>/ds-<碼>、分支 ds/<碼>-<月日>(ds/* 在 API 席 git 白名單內)。
  碼 = uuid 尾五碼(dm 位址)。同一顆 uuid 重開(原地復活)⇒ 沿用同一棵,不重建、不動它的改動。
  路徑已存在但不是這個倉的 ds/<碼>-* 工作樹 ⇒ 拒絕,不蓋(那可能是別人的東西)。
  新樹的基底 = 開席目錄那個倉當下的 HEAD。
  歸屬用**完整 uuid** 認:建樹時記在倉設定 branch.<分支>.aibSeatUuid;沿用前比對,不同 ⇒ 拒絕
  (尾五碼相同的兩席不會共用一棵樹與它的未提交改動)。沒記過的舊樹(09-28 手建那幾棵)沿用並補記。
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import re
import subprocess
import sys
from pathlib import Path

RC_OK, RC_USAGE, RC_NOT_GIT, RC_CONFLICT, RC_GIT = 0, 2, 3, 4, 5
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _git(repo, *args, timeout=60):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def _worktrees(top: Path) -> dict:
    """{絕對路徑: 分支短名或 ""}。"""
    r = _git(top, "worktree", "list", "--porcelain")
    out, cur = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            cur = str(Path(line[9:]).resolve())
            out[cur] = ""
        elif line.startswith("branch ") and cur is not None:
            out[cur] = line[7:].removeprefix("refs/heads/")
    return out


def _owner(top: Path, branch: str):
    """(ok, 值)。git config --get:rc 0=有值、rc 1=沒這個鍵;其他=讀不到(鎖衝突等)⇒ ok=False,不當成「沒主人」。"""
    r = _git(top, "config", "--get", "branch.%s.aibSeatUuid" % branch)
    if r.returncode == 0:
        return True, r.stdout.strip()
    if r.returncode == 1:
        return True, ""
    return False, ""


def _claim(top: Path, branch: str, uuid: str) -> bool:
    """寫歸屬並讀回確認是自己。兩席同時搶同一分支時後寫的贏,輸的一方讀回不是自己 ⇒ False。"""
    if _git(top, "config", "branch.%s.aibSeatUuid" % branch, uuid).returncode != 0:
        return False
    ok, got = _owner(top, branch)
    return ok and got == uuid


def _unclaim(top: Path, branch: str) -> bool:
    """拿掉歸屬登記;成功 = 拿完之後讀回確實沒有這個鍵(git config --unset 的 rc=5 可能是「鍵有多個值」,不能只看 rc)。"""
    _git(top, "config", "--unset-all", "branch.%s.aibSeatUuid" % branch)
    ok, got = _owner(top, branch)
    return ok and got == ""


def ensure(repo: str, uuid: str, root: str, today: str = "") -> tuple[int, str]:
    """回 (rc, 路徑或原因)。「讀歸屬、登記、建樹、失敗還原」整段在同一把倉級檔案鎖裡
    (<git common dir>/aib-seat-worktree.lock):兩席同時開席時,失敗那一方的還原不會刪到成功那一方的登記。"""
    if not _UUID.match(uuid or ""):
        return RC_USAGE, "不是 uuid:%r" % uuid
    r = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if r.returncode != 0 or not r.stdout.strip():
        return RC_NOT_GIT, "開席目錄不是 git 倉(%s),這一席不建工作目錄、只能讀" % repo
    lock_path = Path(r.stdout.strip()) / "aib-seat-worktree.lock"
    try:
        lk = open(lock_path, "a")
    except OSError as exc:
        return RC_GIT, "開不了鎖檔 %s(%s);不建" % (lock_path, exc.strerror or exc)
    with lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
        except OSError as exc:
            return RC_GIT, "拿不到鎖 %s(%s);不建" % (lock_path, exc.strerror or exc)
        try:
            return _ensure_locked(repo, uuid, root, today)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _ensure_locked(repo: str, uuid: str, root: str, today: str = "") -> tuple[int, str]:
    if not _UUID.match(uuid or ""):
        return RC_USAGE, "不是 uuid:%r" % uuid
    # 五碼只有一個取法(seat_address.uuid_tail5;test-seat-address 的突變樁守這一條)
    import seat_address as _sa
    code = _sa.uuid_tail5(uuid)
    if not code:
        return RC_USAGE, "uuid 算不出五碼:%r" % uuid
    r = _git(repo, "rev-parse", "--show-toplevel")
    if r.returncode != 0 or not r.stdout.strip():
        return RC_NOT_GIT, "開席目錄不是 git 倉(%s),這一席不建工作目錄、只能讀" % repo
    top = Path(r.stdout.strip()).resolve()
    path = (Path(root).expanduser() / ("ds-" + code)).resolve()
    # 目錄被刪但還登記著的舊樹先清掉,否則會被誤認成「已存在可沿用」(--expire now:不吃 gc 的到期寬限)
    _git(top, "worktree", "prune", "--expire", "now")
    trees = _worktrees(top)
    if str(path) in trees:
        br = trees[str(path)]
        if not br.startswith("ds/%s-" % code):
            return RC_CONFLICT, "%s 已是這個倉的工作樹但分支是 %r,不是 ds/%s-*;不動它" % (path, br, code)
        if not path.is_dir():
            return RC_CONFLICT, "%s 還登記著但目錄不在;不沿用" % path
        ok, owner = _owner(top, br)
        if not ok:
            return RC_GIT, "讀不到 %s 的歸屬設定;不沿用" % br
        if owner and owner != uuid:
            return RC_CONFLICT, "%s 屬於另一席(%s…%s),尾五碼撞號;不共用" % (path, owner[:8], owner[-5:])
        # 沒記過主人的舊樹(09-28 手建那三棵)按尾五碼認領;aib 開席時已拒絕與已登記席撞尾五碼,
        # 所以會走到這裡的只有那棵樹原本的席。
        if not owner and not _claim(top, br, uuid):
            return RC_CONFLICT, "認領 %s 失敗(寫不進或被別席搶先);不沿用" % br
        return RC_OK, str(path)
    if path.exists():
        return RC_CONFLICT, "%s 已存在且不是這個倉的工作樹;不蓋" % path
    branch = "ds/%s-%s" % (code, today or datetime.date.today().strftime("%m%d"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if _git(top, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0:
        ok, owner = _owner(top, branch)
        if not ok:
            return RC_GIT, "讀不到 %s 的歸屬設定;不建" % branch
        if owner and owner != uuid:
            return RC_CONFLICT, "分支 %s 屬於另一席(%s…%s),尾五碼撞號;不共用" % (branch, owner[:8], owner[-5:])
        if not owner and not _claim(top, branch, uuid):
            return RC_CONFLICT, "認領 %s 失敗;不建" % branch
        r = _git(top, "worktree", "add", str(path), branch)
        if r.returncode != 0 and not owner:
            if not _unclaim(top, branch):
                return RC_GIT, "git worktree add 失敗,而且還原 %s 的歸屬登記也失敗(要人手 git config --unset)" % branch
    else:
        # 先登記主人再建樹(鍵可以先於分支存在):同尾五碼兩席同時開,輸的一方讀回不是自己就停手,
        # 贏的一方建樹;就算兩邊都走到 worktree add,路徑已存在那一方會失敗。
        ok, owner = _owner(top, branch)
        if not ok:
            return RC_GIT, "讀不到 %s 的歸屬設定;不建" % branch
        if owner and owner != uuid:
            return RC_CONFLICT, "分支 %s 已被另一席(%s…%s)登記,尾五碼撞號;不共用" % (branch, owner[:8], owner[-5:])
        if not _claim(top, branch, uuid):
            return RC_CONFLICT, "登記 %s 的歸屬失敗(寫不進或被別席搶先);不建" % branch
        r = _git(top, "worktree", "add", "-b", branch, str(path), "HEAD")
        if r.returncode != 0:
            # 建樹失敗要還原登記(原本沒主人 ⇒ 拿掉),否則真正的主人下次會被當成撞號、永久拿不回樹(a seat r2 中 2)
            if not _unclaim(top, branch):
                return RC_GIT, "git worktree add 失敗,而且還原 %s 的歸屬登記也失敗(要人手 git config --unset)" % branch
    if r.returncode != 0:
        return RC_GIT, "git worktree add 失敗:%s" % ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1]
    return RC_OK, str(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="api_seat_worktree.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure")
    e.add_argument("--repo", required=True)
    e.add_argument("--uuid", required=True)
    e.add_argument("--root", required=True)
    e.add_argument("--today", default="", help="月日 MMDD(測試用;預設今天)")
    a = ap.parse_args(argv)
    try:
        rc, msg = ensure(a.repo, a.uuid, a.root, a.today)
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, msg = RC_GIT, "%s: %s" % (type(exc).__name__, exc)
    if rc == RC_OK:
        sys.stdout.write(msg + "\n")
    else:
        sys.stderr.write("api_seat_worktree: " + msg + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
