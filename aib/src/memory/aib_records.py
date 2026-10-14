#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib records —— 用戶自己挑的人格卡與專案紀錄放進自己的 git 倉(最小版)。

設計:docs/DESIGN-records-repo-1013.md(the maintainer 10-13 00:2x 經 a seat:「讓用戶如果他有 git,他可以將他覺得重要的
人格與專案記錄更新到 git」⇒ 用戶自己挑、自己推;a seat 點頭)。

  aib records add <uid>                     挑一張人格卡(cards.json 的 uid)或一個專案(proj:…)
  aib records remove <uid>                  從清單拿掉(倉裡已提交的歷史不刪)
  aib records list                          列清單
  aib records remote [<網址>] [--private]   設 / 看遠端;非 GitHub 網址要 --private 自己確認是私人倉
  aib records push                          複製、提交;有遠端且確定是私人倉才推

不自動拍、不裝排程、不掛在 newrole / handoff / memory write 上;log / restore 不做(用戶直接用 git)。
有 git 才能用:沒有就印怎麼裝、rc 1、什麼都不寫。

rc:0 成功(含「沒有變動」);1 沒有 git;2 用法錯 / uid 查不到 / 疑似金鑰整次不推 / 拒推(提交仍留在本機)。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
for _p in (SRC / "cockpit", SRC / "identity", HERE):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import secret_patterns  # noqa: E402

MAX_BYTES = 1024 * 1024
TEXT_SUFFIXES = (".md", ".json")
# 用戶全域設定不准讓紀錄倉提交失敗(簽章、全域 hooks):本倉一律關掉
GIT_ID = ["-c", "user.name=aib", "-c", "user.email=aib@localhost", "-c", "commit.gpgsign=false",
          "-c", "core.hooksPath=/dev/null"]
GITHUB_RE = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                       r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

# ---------------------------------------------------------------- 字
_TR = {}


def T(key, **params):
    try:
        from i18n_lib import resolve_locale
        loc = resolve_locale()   # 語系來源只認 i18n_lib(明給 > env > 設定檔 > zh-TW)
    except Exception:  # noqa: BLE001 —— 讀不到 i18n_lib 時下面 Translator 也會失敗、印鍵名
        loc = ""
    tr = _TR.get(loc)
    if tr is None:
        try:
            from i18n_lib import Translator
            tr = Translator(loc or None)
        except Exception:  # noqa: BLE001 —— 字典讀不到就印鍵名(可見即可抓)
            tr = False
        _TR[loc] = tr
    if not tr:
        return key
    try:
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def _out(text, err=False):
    (sys.stderr if err else sys.stdout).write(text + "\n")


# ---------------------------------------------------------------- 路徑
def state_root():
    for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(k)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def repo_dir():
    return state_root() / "records"


def picked_path():
    return state_root() / "state" / "records" / "picked.json"


def remote_meta_path():
    return state_root() / "state" / "records" / "remote.json"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p, doc):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- git 在不在
def git_available():
    """安靜探:macOS 先 xcode-select -p(/usr/bin/git 是殼,沒裝工具時一叫就跳安裝視窗);再 git --version 1 秒。"""
    git = shutil.which("git")
    if not git:
        return False
    if sys.platform == "darwin" and os.path.realpath(git) == "/usr/bin/git":
        try:
            r = subprocess.run(["xcode-select", "-p"], capture_output=True, timeout=1)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode != 0:
            return False
    try:
        r = subprocess.run([git, "--version"], capture_output=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


_SSH_CFG = []


def _ssh_configured():
    """用戶有沒有設 core.sshCommand(全域或本倉);查一次記住。"""
    if not _SSH_CFG:
        try:
            r = subprocess.run(["git", "-C", str(repo_dir()) if repo_dir().is_dir() else ".", "config", "--get",
                                "core.sshCommand"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
            _SSH_CFG.append(bool(r.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            _SSH_CFG.append(False)
    return _SSH_CFG[0]


def _git(*args, check=False):
    # 不准跳出輸入帳密 / 金鑰密碼的提示(會卡到逾時):沒有帳密小幫手就直接失敗,說清楚
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if not (os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH") or _ssh_configured()):
        env["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes"   # 用戶自己有設 ssh 指令(-i 指定金鑰等)就不蓋;加固兩項照 test-ssh-hardening-1011(不借金鑰代理、不開轉發)
    r = subprocess.run(["git", "-C", str(repo_dir())] + list(args), capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL, env=env)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r


# ---------------------------------------------------------------- 認 uid
def _vault_root():
    try:
        import card_registry
        return Path(card_registry.resolve_vault())
    except Exception:  # noqa: BLE001
        v = os.environ.get("EZ2_VAULT_DIR")
        return Path(v).expanduser() if v else None


def resolve(uid):
    """回 {uid, kind, name, src, dest} 或 None(查不到不猜)。"""
    uid = (uid or "").strip()
    if not uid:
        return None
    if uid.startswith("proj:"):
        card = state_root() / "projects" / uid / "card.json"
        if not card.is_file():
            return None
        doc = _read_json(card, {})
        return {"uid": uid, "kind": "project", "name": doc.get("title") or uid,
                "src": card, "dest": Path("projects") / (uid.replace(":", "_") + ".json")}
    cards = _read_json(state_root() / "identity" / "cards.json", {}).get("cards") or {}
    row = cards.get(uid)
    if not isinstance(row, dict) or not row.get("card_path_rel"):
        return None
    vault = _vault_root()
    if vault is None:
        return None
    return {"uid": uid, "kind": "persona", "name": row.get("display_name") or uid,
            "src": vault / row["card_path_rel"], "dest": Path("personas") / (uid + ".md")}


# ---------------------------------------------------------------- 清單
def load_picked():
    doc = _read_json(picked_path(), {})
    items = doc.get("items") if isinstance(doc, dict) else None
    return items if isinstance(items, list) else []


def save_picked(items):
    _write_json(picked_path(), {"schema": "aib-records-picked/v1", "items": items})


def cmd_add(uid):
    info = resolve(uid)
    if not info:
        _out(T("aib.records.err.unknown_uid", uid=uid), err=True)
        return 2
    items = load_picked()
    if any(i.get("uid") == uid for i in items):
        _out(T("aib.records.add.already", name=info["name"], uid=uid))
        return 0
    items.append({"uid": uid, "kind": info["kind"], "name": info["name"], "added": _now(), "last_push": None})
    save_picked(items)
    _out(T("aib.records.add.ok", name=info["name"], uid=uid))
    return 0


def cmd_remove(uid):
    items = load_picked()
    left = [i for i in items if i.get("uid") != uid]
    if len(left) == len(items):
        _out(T("aib.records.remove.missing", uid=uid), err=True)
        return 2
    save_picked(left)
    _out(T("aib.records.remove.ok", uid=uid))
    return 0


def cmd_list():
    items = load_picked()
    if not items:
        _out(T("aib.records.list.empty"))
        return 0
    for i in items:
        _out(T("aib.records.list.row", name=i.get("name") or "", uid=i.get("uid") or "",
               when=i.get("last_push") or T("aib.records.list.never")))
    return 0


# ---------------------------------------------------------------- 倉
def ensure_repo():
    d = repo_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    if not (d / ".git").exists():
        _git("init", "-q", check=True)
    return d


def current_remote():
    if not (repo_dir() / ".git").exists():
        return ""
    r = _git("remote", "get-url", "origin")
    return r.stdout.strip() if r.returncode == 0 else ""


def _mask(url):
    """網址裡帶的帳密 / token 不印出來:https://user:tok@host/… ⇒ https://***@host/…"""
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1***@", url or "", flags=re.I)


def cmd_remote(url, private):
    if not url:
        cur = current_remote()
        _out(_mask(cur) if cur else T("aib.records.remote.none"))
        return 0
    ensure_repo()
    if current_remote():
        _git("remote", "set-url", "origin", url, check=True)
    else:
        _git("remote", "add", "origin", url, check=True)
    _write_json(remote_meta_path(), {"url": url, "private_confirmed": bool(private), "at": _now()})
    _out(T("aib.records.remote.set", url=_mask(url)))
    if not GITHUB_RE.match(url) and not private:
        _out(T("aib.records.remote.need_private"))
    return 0


# ---------------------------------------------------------------- 公開倉判斷
def _gh_private(owner, repo):
    """有 gh ⇒ 問 gh;回 True(私人)/ False(公開)/ None(gh 沒有或問不到)。"""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "api", "repos/%s/%s" % (owner, repo), "--jq", ".private"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    v = r.stdout.strip()
    return True if v == "true" else False if v == "false" else None


def _api_status(owner, repo):
    """不帶登入打 GitHub API;回 HTTP 狀態碼,查詢失敗回 None。"""
    req = urllib.request.Request("https://api.github.com/repos/%s/%s" % (owner, repo),
                                 headers={"User-Agent": "aib-records", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        try:
            return e.code
        finally:
            e.close()
    except (OSError, ValueError):
        return None


def push_allowed(url):
    """回 (可以推?, 理由鍵)。GitHub:gh 看 private;沒 gh 打 API 200 公開拒、404 放行、失敗拒。
    非 GitHub:要 remote --private 確認過(記在 remote.json,且網址相同)。"""
    m = GITHUB_RE.match(url)
    if m:
        owner, repo = m.group(1), m.group(2)
        priv = _gh_private(owner, repo)
        if priv is True:
            return True, ""
        if priv is False:
            return False, "aib.records.push.refuse_public"
        status = _api_status(owner, repo)
        if status == 404:
            return True, ""
        if status == 200:
            return False, "aib.records.push.refuse_public"
        return False, "aib.records.push.refuse_unknown"
    meta = _read_json(remote_meta_path(), {})
    if meta.get("url") == url and meta.get("private_confirmed") is True:
        return True, ""
    return False, "aib.records.push.refuse_unconfirmed"


# ---------------------------------------------------------------- push
def _is_text(data):
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def cmd_push():
    items = load_picked()
    if not items:
        _out(T("aib.records.list.empty"))
        return 0
    staged, skipped, secret = [], [], []
    for i in items:
        info = resolve(i.get("uid"))
        if not info or not info["src"].is_file():
            skipped.append((i.get("uid"), "aib.records.skip.missing"))
            continue
        src = info["src"]
        if src.suffix.lower() not in TEXT_SUFFIXES:
            skipped.append((info["uid"], "aib.records.skip.not_text"))
            continue
        if src.stat().st_size > MAX_BYTES:
            skipped.append((info["uid"], "aib.records.skip.too_big"))
            continue
        data = src.read_bytes()
        if not _is_text(data):
            skipped.append((info["uid"], "aib.records.skip.not_text"))
            continue
        found = secret_patterns.hits(data.decode("utf-8"))
        if found:
            secret.append((info["uid"], found))
            continue
        staged.append((info, data))
    for uid, key in skipped:
        _out(T(key, uid=uid), err=True)
    if secret:
        for uid, names in secret:
            _out(T("aib.records.push.secret", uid=uid, rules=", ".join(names)), err=True)
        _out(T("aib.records.push.secret_abort"), err=True)
        return 2
    if not staged:
        _out(T("aib.records.push.nothing"))
        return 0
    d = ensure_repo()
    for info, data in staged:
        dest = d / info["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        _git("add", "--", str(info["dest"]), check=True)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        _out(T("aib.records.push.no_change"))
    else:
        msg = "aib records push: " + ", ".join("%s(%s)" % (info["name"], info["uid"]) for info, _ in staged)
        _git(*GIT_ID, "commit", "-q", "-m", msg, check=True)
        _out(T("aib.records.push.committed", n=len(staged)))
    done = {info["uid"] for info, _ in staged}
    url = current_remote()
    if not url:
        _out(T("aib.records.push.local_only"))
        return 0
    ok, why = push_allowed(url)
    if not ok:
        _out(T(why, url=_mask(url)), err=True)
        if why == "aib.records.push.refuse_unconfirmed" and _mask(url) != url:
            _out(T("aib.records.push.refuse_unconfirmed_masked"), err=True)
        return 2
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    r = _git("push", "-q", "origin", branch)
    if r.returncode != 0:
        _out(T("aib.records.push.failed", err=_mask((r.stderr or r.stdout).strip())[-300:]), err=True)
        return 2
    now = _now()
    for i in items:
        if i.get("uid") in done:
            i["last_push"] = now          # 只有真的推上去才記(拒推、推失敗、只本機都不算)
    save_picked(items)
    _out(T("aib.records.push.pushed", url=_mask(url)))
    return 0


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_RECORDS_PROG") or "aib records",
                                 description=T("aib.records.help.desc"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "remove"):
        p = sub.add_parser(name, help=T("aib.records.help.%s" % name))
        p.add_argument("uid")
    sub.add_parser("list", help=T("aib.records.help.list"))
    p = sub.add_parser("remote", help=T("aib.records.help.remote"))
    p.add_argument("url", nargs="?", default="")
    p.add_argument("--private", action="store_true", help=T("aib.records.help.private"))
    sub.add_parser("push", help=T("aib.records.help.push"))
    a = ap.parse_args(argv)
    if not git_available():
        _out(T("aib.records.err.no_git"), err=True)
        return 1
    try:
        if a.cmd == "add":
            return cmd_add(a.uid)
        if a.cmd == "remove":
            return cmd_remove(a.uid)
        if a.cmd == "list":
            return cmd_list()
        if a.cmd == "remote":
            return cmd_remote(a.url, a.private)
        return cmd_push()
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        _out(T("aib.records.err.git", err=_mask(str(e))[-300:]), err=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
