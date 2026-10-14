#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""三記憶閘(BE,2026-09-03):ACB 關閉後,這一支是它的正本。

the maintainer 09-03 直令「深度把 ACB 關閉、只開 aib」。舊閘 `~/.acb/bin/three-memory-gate.sh`(538 行)是
**唯一會擋收工**的 ACB hook,而且它此刻正在自動 commit the maintainer 的 Obsidian vault。搬過來時逐條記下
**故意改掉的四件**——每一件都是舊版量到過的缺陷,不是重寫時的偏好:

1. **量測範圍與執行範圍不相交**:舊版 cwd 白名單是 `~/ez2-ai*` 前綴(所以 `~/ez2-aib` 整棵在內),
   但它監看的三個 repo 沒有一個是 `ez2-aib`。⇒ 本艦隊每天 commit,閘一次都沒為它開火過。
   本版把 `~/ez2-aib` 放進 watch_repos(設定檔第一項)。
2. **記憶那一格量的是目錄**:舊版 `newest_mtime(~/.acb/memory)`,而那個目錄下每一席都在寫;
   任何一席的任何寫入都會讓「這一本記憶已回寫」為真。⇒ 恆綠。本版第一本改成**單檔**
   `~/.ez2-aib/memory/root/context.md`(ACB 那本的新家,內容已搬)。
3. **AI 記憶那一格看錯目錄**:舊設定 `~/.claude/projects/-Users-user/memory`,而本艦隊寫的是
   `-Users-user-ez2-aib`。本版兩個都收,任一新於 commit 即算。
4. **vault 自動 commit 失敗會擋住不相干的席**:舊版 `c80_autocommit || exit 2`,而它失敗最常見的
   原因是 `.git/index.lock`(Obsidian 的 git plugin、iCloud 同步、另一席同時收工)。
   ⇒ 你在改 `src/cockpit/` 卻被「the maintainer 的 Obsidian 資料夾正在被別的程式寫」擋住收工。
   本版 `block_on_failure=false`:失敗留痕+stderr 告警,**不擋**。要擋請改設定,不要改碼。

保留不動的三件:min/max age 視窗(600s/86400s)、Obsidian 最新條目必須含 `[sig::`、
`--status` 永遠 rc=0。

## 介面
    python3 aib-memory-gate.py            # Stop hook:欠回寫 ⇒ rc=2 並在 stderr 具名
    python3 aib-memory-gate.py --status   # 只印狀態,永遠 rc=0
    AIB_MEMORY_GATE_CONFIG=<path>         # 設定檔覆寫(治具用;預設 <repo>/policy/memory-gate.json)
    AIB_MEMORY_GATE_NOW=<epoch>           # 現在時刻覆寫(治具用)

## 失效方向
內部任何例外一律 **rc=0 + stderr 一行**(fail-open)。理由:這支掛在 Stop 上,它自己壞掉時
把每一席都鎖在回合裡,比漏掉一輪記憶治理貴得多。會擋人的只有一條路:**量到了**欠回寫。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "policy", "memory-gate.json")
LOG_PATH = os.path.expanduser("~/.ez2-aib/logs/memory-gate.log")


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def load_config(path=None):
    path = path or os.environ.get("AIB_MEMORY_GATE_CONFIG") or DEFAULT_CONFIG
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if cfg.get("schema") != "aib-memory-gate/v1":
        raise ValueError("設定檔 schema 不符:%r" % cfg.get("schema"))
    return cfg


def git_epoch(repo, ref):
    """repo 的 ref 的 committer 時間(epoch);查不到回 0。"""
    try:
        p = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", ref],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return 0
    out = (p.stdout or "").strip()
    return int(out) if p.returncode == 0 and out.isdigit() else 0


def latest_commit(cfg):
    """watch_repos × watch_refs 的最大 committer 時間;一個都沒有回 0。"""
    newest = 0
    for raw in cfg.get("watch_repos") or []:
        repo = expand(raw)
        if not os.path.exists(os.path.join(repo, ".git")):
            continue
        for ref in cfg.get("watch_refs") or ["HEAD"]:
            newest = max(newest, git_epoch(repo, ref))
    return newest


def newest_mtime(path):
    """檔=自己的 mtime;目錄=底下最新一個檔的 mtime;不存在回 0。"""
    path = expand(path)
    if os.path.isfile(path):
        try:
            return int(os.stat(path).st_mtime)
        except OSError:
            return 0
    if not os.path.isdir(path):
        return 0
    newest = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, int(os.stat(os.path.join(root, name)).st_mtime))
            except OSError:
                continue
    return newest


def find_ledger(mem):
    """Obsidian 那一本的「00 總綱」檔;找不到回 None。"""
    for raw in mem.get("paths") or []:
        base = expand(raw)
        prefix = mem.get("ledger_prefix")
        if os.path.isdir(base) and prefix:
            for name in sorted(os.listdir(base)):
                if name.startswith(prefix) and name.endswith(".md"):
                    return os.path.join(base, name)
        legacy = mem.get("ledger_legacy")
        if legacy and os.path.isfile(os.path.join(base, legacy)):
            return os.path.join(base, legacy)
    return None


def sig_ok(ledger):
    """總綱最新條目(第一個 `## ` 段)要含 `[sig::`。讀不到內容=unknown ⇒ 放行留痕
    (mtime 已證明有新寫入;權限事故不該變成紅燈)。"""
    if ledger is None or not os.path.isfile(ledger):
        return False
    try:
        with open(ledger, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        log("sig unknown: ledger unreadable (TCC?), pass-with-log")
        return True
    seen = 0
    block = []
    for line in text.splitlines():
        if line.startswith("## "):
            seen += 1
            if seen == 2:
                break
        if seen == 1:
            block.append(line)
    return "[sig::" in "\n".join(block)


_TR = {}


def T(key, **params):
    """人和 AI 會讀到的字走四語鍵(語系盤查第 16 條);每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名。"""
    try:
        cockpit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cockpit"))
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def check_missing(cfg, commit_epoch):
    """回缺的那幾本的名字(list);全部有回寫回 []。"""
    missing = []
    for mem in cfg.get("memories") or []:
        name = mem.get("name") or "?"
        fresh = any(newest_mtime(p) >= commit_epoch for p in (mem.get("paths") or []))
        if not fresh:
            missing.append("[%s]" % name)
            continue
        if mem.get("sig_required") and not sig_ok(find_ledger(mem)):
            missing.append(T("aib.memgate.missing_sig", name=name))
    return missing


def autocommit(cfg, sig):
    """vault 快照,作者=本 session 署名(git blame 可稽核)。
    回 (ok, note)。ok=False 時是否擋人由設定的 block_on_failure 決定,不由本函式決定。"""
    ac = cfg.get("autocommit") or {}
    if not ac.get("enabled"):
        return True, "autocommit disabled"
    repo = expand(ac.get("repo") or "")
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return True, "no vault repo"
    try:
        st = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=60)
        if st.returncode != 0:
            return False, "status rc=%d %s" % (st.returncode, (st.stderr or "").strip()[:120])
        if not (st.stdout or "").strip():
            return True, "vault clean"
        add = subprocess.run(["git", "-C", repo, "add", "-A"],
                             capture_output=True, text=True, timeout=120)
        if add.returncode != 0:
            return False, "add rc=%d %s" % (add.returncode, (add.stderr or "").strip()[:120])
        head_before = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=30).stdout.strip()
        msg = "memory: 三記憶回寫快照(%s)" % sig
        cm = subprocess.run(["git", "-C", repo, "commit", "-m", msg],
                            capture_output=True, text=True, timeout=120)
        head_after = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                    capture_output=True, text=True, timeout=30).stdout.strip()
        if cm.returncode != 0:
            still = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                                   capture_output=True, text=True, timeout=60).stdout.strip()
            if not still:
                return True, "concurrent commit by another session"
            return False, "commit rc=%d %s" % (cm.returncode, (cm.stderr or "").strip()[:120])
        if head_after == head_before:
            return False, "commit rc=0 but HEAD unchanged"
        return True, "committed %s" % head_after[:8]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "autocommit exception: %s" % exc


def in_scope(cfg, cwd):
    """cwd 落在任一 scope_prefix 底下。

    ★ 不讀 `$PWD`(第一版讀了,臂當場抓到):子行程繼承的是**父行程**的 PWD,
      `subprocess.run(cwd=...)` 不會改它 ⇒ 整個閘對每一次呼叫都判「不在範圍」而靜默放行,
      而那與「範圍內且沒有欠回寫」在畫面上逐字相同。兩邊都 realpath:
      /tmp 與 /private/tmp 在 macOS 是同一個目錄的兩個名字。"""
    cwd = os.path.realpath(cwd)
    for raw in cfg.get("scope_prefixes") or []:
        prefix = os.path.realpath(expand(raw))
        if cwd == prefix or cwd.startswith(prefix + os.sep):
            return True
    return False


def run(argv):
    status_mode = "--status" in argv
    cfg = load_config()
    now = int(os.environ.get("AIB_MEMORY_GATE_NOW") or time.time())
    cwd = os.getcwd()
    if not status_mode and not in_scope(cfg, cwd):
        return 0, None
    commit_epoch = latest_commit(cfg)
    sig = (os.environ.get("EZ2_SESSION_SIG")
           or os.environ.get("CLAUDE_SESSION_ID", "")[:5] or "unbound")
    if commit_epoch == 0:
        return 0, T("aib.memgate.no_commit")
    stamp = time.strftime("%m-%d %H:%M", time.localtime(commit_epoch))
    missing = check_missing(cfg, commit_epoch)
    if status_mode:
        if missing:
            return 0, T("aib.memgate.status_red", when=stamp, detail=" ".join(missing))
        return 0, T("aib.memgate.status_green", when=stamp)
    ok, note = autocommit(cfg, sig)
    if not ok:
        log("autocommit failed: %s" % note)
        block = bool((cfg.get("autocommit") or {}).get("block_on_failure"))
        if block:
            return 2, T("aib.memgate.snap_block", detail=note)
        print(T("aib.memgate.snap_warn", detail=note), file=sys.stderr)
    age = now - commit_epoch
    if age < int(cfg.get("min_age_s", 600)):
        return 0, None                      # 緩衝窗:剛 commit 完,連續作業不打斷
    if age > int(cfg.get("max_age_s", 86400)):
        return 0, None                      # 非本輪,不追溯
    if missing:
        return 2, T("aib.memgate.block", when=stamp, detail=" ".join(missing))
    return 0, None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        rc, msg = run(argv)
    except Exception as exc:                                   # noqa: BLE001
        # fail-open:這支掛在 Stop 上,它自己壞掉不得把每一席鎖在回合裡。
        print(T("aib.memgate.self_fault", err=exc), file=sys.stderr)
        log("gate exception: %s" % exc)
        return 0
    if msg:
        print(msg, file=sys.stderr if rc else sys.stdout)
    return rc


if __name__ == "__main__":
    sys.exit(main())
