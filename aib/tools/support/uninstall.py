#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib uninstall: remove the open-source aib from this computer. Lists first; removes only with --yes.

    aib uninstall              show what would be removed (nothing is touched)
    aib uninstall --yes        remove the program: this checkout, the ~/.local/bin/aib link to it, the boot menu,
                               the PATH line the installer added
                               (a checkout with work that exists nowhere else — uncommitted changes, untracked files,
                               stashes, commits not on any remote, other worktrees — is kept; add
                               --discard-unsaved-work to remove it anyway)
                               It also takes out what aib added to other programs' settings: its hooks in Claude
                               (settings.json), Codex, Grok, Copilot and Antigravity hook files, the backups it made of
                               those files, and the desktop shortcut to its records. Only entries aib made are touched.
    aib uninstall --all --yes  also remove aib's records (~/.ez2-aib: seats, personas, memory, handoffs, settings)
                               and its cache (~/.cache/ez2-aib); ~/.ez2-aib is first saved to ~/ez2-aib-backup-<time>.tar.gz

Never touched: another aib on this machine (a developer copy, or one installed some other way), its folder, and the
records it shares with this one (--all refuses while another aib is installed); your projects (`your-project/.aib`
is yours to delete); the AI tools and Homebrew.
"""
# 10-11(the maintainer「有完整移除舊 aib 或所有 aib 的方式嗎」→「照你的建議」):先列出會刪什麼、自動備份記憶、確認(--yes)才刪。
#   FishMac 實裝:同一個帳號可能同時有開發版 aib(~/.local/bin/aib 是它)和公開版(~/ez2-aib)⇒ 只刪公開版自己的東西;
#   兩者共用 ~/.ez2-aib ⇒ 還有別的 aib 時 --all 一律不刪。輸出走 aib/i18n 四語鍵(10-14 起;原本固定英文)。
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

PUBLIC_REPO = "github.com/ez2app/ez2-aib"
BOOT_LABEL = "com.ez2.aib-boot-restore"
PATH_MARK = "# aib-bootstrap: PATH"


def _t(key, **params):
    """aib/i18n 四語鍵(10-14 新用戶走查第 12 條:LANG=zh_TW 也整段英文);字典出事就印鍵名。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cockpit"))
        from i18n_lib import t
        return t(key, params)
    except Exception:
        return key


def _origin(checkout):
    try:
        return subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"], capture_output=True,
                              text=True, timeout=15, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_public_checkout(checkout):
    return (checkout / ".git").is_dir() and PUBLIC_REPO in _origin(checkout).replace("git@github.com:", "github.com/")


def _git_out(checkout, *args):
    """git 的 stdout;跑不起來或非 0 ⇒ None(呼叫端當成「查不了」)。"""
    try:
        r = subprocess.run(["git", "-C", str(checkout)] + list(args), capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def unsaved_work(checkout):
    """10-15 稽核 C4:這份 checkout 裡有沒有「別的地方都沒有」的東西 —— 有就不能整包刪(照 CONTRIBUTING clone 來開發的人,
    沒推的分支會全沒)。回原因清單;空 = 安全。查不了(git 失敗)也算一個原因:寧可留著不刪。"""
    why = []
    status = _git_out(checkout, "status", "--porcelain", "--untracked-files=normal")
    if status is None:
        return ["could not check it with git"]
    n = len([ln for ln in status.splitlines() if ln.strip()])
    if n:
        why.append("%d uncommitted change(s) or untracked file(s)" % n)
    stash = _git_out(checkout, "stash", "list")
    if stash is None:
        why.append("could not check its stashes")
    elif stash.strip():
        why.append("%d stash(es)" % len(stash.strip().splitlines()))
    ahead = _git_out(checkout, "rev-list", "--count", "--branches", "--not", "--remotes")
    if ahead is None:
        why.append("could not check for unpushed commits")
    elif ahead.strip() not in ("", "0"):
        why.append("%s commit(s) not on any remote" % ahead.strip())
    wts = _git_out(checkout, "worktree", "list", "--porcelain")
    if wts is None:
        why.append("could not list its worktrees")
    else:
        others = [ln[len("worktree "):] for ln in wts.splitlines() if ln.startswith("worktree ")][1:]
        if others:
            why.append("%d other worktree(s): %s" % (len(others), ", ".join(others)))
    return why


# 10-15 稽核 C5:aib 會在別的程式的設定裡加 hook(開席時自動裝)、改之前先備份、init 時在桌面放捷徑;移除要一起清。
#   只認 aib 自己的:hook 指令裡有 aib 那幾支程式的路徑(src/.../<檔名>.py)、具名鍵 / 檔名是 aib-*、
#   備份檔名是 aib 的寫法、桌面捷徑是連到 <狀態根>/user 的連結。別的東西一律不碰。
OUR_HOOK = re.compile(r"/src/(policy/hooks/aib-now-gate|identity/auto_distill_hook|cockpit/menu_pick|cockpit/claudebot_hook)\.py")
OUR_NAMED = ("aib-menu-pick", "aib-auto-distill")                    # ~/.gemini/config/hooks.json 的具名鍵
OUR_HOOK_FILES = ("aib-menu-pick.json", "aib-auto-distill.json")     # ~/.grok/hooks、~/.copilot/hooks 裡 aib 自己的檔
OUR_BACKUPS = ("bak-now-gate-", "bak-menu-pick-", "bak-claudebot-", "bak-auto-distill-", "bak-aib-hook-trust-",
               "bak-aib-ext-")


def _ours(obj):
    return bool(OUR_HOOK.search(json.dumps(obj, ensure_ascii=False)))


def _strip_hook_list(items):
    """一個事件底下的清單:Claude 格式是 [{matcher, hooks:[handler]}],Copilot 格式是 [handler]。拿掉 aib 的 handler。"""
    kept = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("hooks"), list):
            inner = [h for h in it["hooks"] if not _ours(h)]
            if inner:
                kept.append(dict(it, hooks=inner))
        elif not _ours(it):
            kept.append(it)
    return kept


def strip_aib_hooks(cfg):
    """回 (新設定, 有沒有變)。cfg["hooks"] 是 {事件: 清單} 的設定檔(Claude settings.json、Codex / Grok / Copilot hooks)。"""
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    if not isinstance(hooks, dict):
        return cfg, False
    new = dict(cfg)
    nh = {}
    for ev, items in hooks.items():
        if isinstance(items, list):
            items = _strip_hook_list(items)
            if not items:
                continue
        nh[ev] = items
    if nh:
        new["hooks"] = nh
    else:
        new.pop("hooks")
    return new, new != cfg


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _claude_settings(home):
    cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return (Path(cfg).expanduser() if cfg else Path(home) / ".claude") / "settings.json"


def _desktop(home):
    override = os.environ.get("EZ2_AIB_DESKTOP_DIR")
    return Path(os.path.expanduser(override)) if override else Path(home) / "Desktop"


def traces(home):
    """aib 在別的程式裡留下的東西 ⇒ (rewrite: [(path, 新內容 dict, what)], remove: [(path, what)])。"""
    home = Path(home)
    rewrite, remove = [], []
    touched = []
    for f, label in ((_claude_settings(home), "Claude"), (home / ".codex" / "hooks.json", "Codex")):
        cfg = _read_json(f)
        touched.append(f)
        if cfg is None:
            continue
        new, changed = strip_aib_hooks(cfg)
        if changed:
            rewrite.append((f, new, "take out the hooks aib added to %s" % label))
    for d, label in ((home / ".grok" / "hooks", "Grok"), (home / ".copilot" / "hooks", "Copilot")):
        for name in OUR_HOOK_FILES:
            f = d / name
            cfg = _read_json(f)
            touched.append(f)
            if cfg is None or not _ours(cfg):
                continue
            new, _changed = strip_aib_hooks(cfg)
            if [k for k in (new.get("hooks") or {})]:
                rewrite.append((f, new, "take out the hooks aib added to %s" % label))
            else:
                remove.append((f, "the %s hook file aib added" % label))
    f = home / ".gemini" / "config" / "hooks.json"
    cfg = _read_json(f)
    touched.append(f)
    if cfg is not None:
        new = {k: v for k, v in cfg.items() if not (k in OUR_NAMED and _ours(v))}
        if new != cfg:
            rewrite.append((f, new, "take out the hooks aib added to Antigravity"))
    touched.append(home / ".codex" / "config.toml")
    for f in touched:
        for b in sorted(glob.glob(glob.escape(str(f)) + ".*")):
            if any(Path(b).name[len(f.name) + 1:].startswith(m) for m in OUR_BACKUPS):
                remove.append((Path(b), "a backup aib made before changing %s" % f.name))
    desk = _desktop(home)
    user_hub = os.path.normpath(str(home / ".ez2-aib" / "user"))
    try:
        entries = sorted(desk.iterdir()) if desk.is_dir() else []
    except OSError:
        entries = []
    for p in entries:
        if p.is_symlink() and os.path.normpath(os.path.join(str(p.parent), os.readlink(str(p)))) == user_hub:
            remove.append((p, "the desktop shortcut aib made to its records; only the shortcut goes, the records stay "
                              "unless you add --all"))
    return rewrite, remove


def enabled_extensions(home):
    """aib ext 裝進各家 AI 的擴充(Claude / Codex / agy 的 plugin 指到 aib 資料夾)⇒ 名字清單。移除前要先撤。"""
    out = []
    for f in sorted(glob.glob(str(Path(home) / ".ez2-aib" / "ext-state" / "*.json"))):
        st = _read_json(f)
        if st and st.get("enabled"):
            out.append(Path(f).stem)
    return out


def _profile_without_path_line(text):
    """Drop the installer's `# aib-bootstrap: PATH` marker and the export line right after it; keep everything else."""
    lines = text.splitlines(keepends=True)
    out, skip = [], False
    for ln in lines:
        if skip:
            skip = False
            if ln.lstrip().startswith("export PATH="):
                continue
        if ln.strip() == PATH_MARK:
            skip = True
            if out and not out[-1].strip():
                out.pop()          # the installer wrote a blank line before the marker
            continue
        out.append(ln)
    return "".join(out)


def plan(home, package_dir, want_all, discard_unsaved=False, rewrite=None):
    """⇒ (remove: [(path, what)], profiles: [path], keep: [str], refuse: [str], hints: [str]).
    rewrite(給清單就填):[(path, 新設定, what)] —— 要拿掉 aib hook 的設定檔。"""
    home = Path(home)
    remove, profiles, keep, refuse, hints = [], [], [], [], []
    rewrite = [] if rewrite is None else rewrite
    checkout = Path(package_dir).resolve().parent
    link = home / ".local" / "bin" / "aib"
    ours_link = link.is_symlink() and Path(os.path.realpath(link)) == (checkout / "bin" / "aib").resolve()
    # 10-11(0.1.2 實裝抓到):pip / pipx 裝的 ⇒ 套件在 site-packages,不是 checkout;別說成「開發版」,說用哪個指令移除。
    #   pipx 會把 ~/.local/bin/aib 連到它自己 venv 裡的 aib ⇒ 那支是這份自己的,不算「另一個 aib」。
    in_pipx = "/pipx/venvs/" in str(checkout) or "/pipx/venvs/" in sys.prefix
    pip_installed = in_pipx or checkout.name in ("site-packages", "dist-packages")
    pipx_link = pip_installed and link.is_symlink() and \
        Path(os.path.realpath(link)).parent == (Path(sys.prefix) / "bin").resolve()
    other_aib = (link.exists() or link.is_symlink()) and not ours_link and not pipx_link
    if _is_public_checkout(checkout):
        unsaved = unsaved_work(checkout)
        if unsaved and not discard_unsaved:
            keep.append("%s: this aib folder has work that exists nowhere else (%s); it was not removed. Commit and push "
                        "what you want to keep, or add --discard-unsaved-work to remove it anyway"
                        % (checkout, "; ".join(unsaved)))
        else:
            remove.append((checkout, _t("aib.uninstall.what_checkout") +
                           (" — including work that exists nowhere else: %s" % "; ".join(unsaved) if unsaved else "")))
    elif in_pipx:
        hints.append(_t("aib.uninstall.hint_pipx"))
    elif pip_installed:
        hints.append(_t("aib.uninstall.hint_pip"))
    else:
        keep.append(_t("aib.uninstall.keep_devcopy", path=checkout))
    if ours_link:
        remove.append((link, _t("aib.uninstall.what_link")))
    elif other_aib:
        keep.append(_t("aib.uninstall.keep_other_link", path=link))
    plist = home / "Library" / "LaunchAgents" / (BOOT_LABEL + ".plist")
    if plist.exists() and not other_aib:
        remove.append((plist, _t("aib.uninstall.what_boot")))
    elif plist.exists():
        keep.append(_t("aib.uninstall.keep_boot", path=plist))
    if not other_aib:
        for name in (".zprofile", ".profile"):
            p = home / name
            try:
                if PATH_MARK in p.read_text(encoding="utf-8"):
                    profiles.append(p)
            except OSError:
                pass
    rw, rm = traces(home)
    if other_aib:
        if rw or rm:
            keep.append("hooks aib added to your AI tools and the desktop shortcut: they may belong to the other aib "
                        "(%s), left as is" % link)
    else:
        rewrite.extend(rw)
        remove.extend(rm)
    for name in enabled_extensions(home):
        hints.append("the extension `%s` is still turned on in your AI tools and lives in aib's folder: run "
                     "`aib ext remove %s` first, or it stays listed there after aib is gone" % (name, name))
    state, cache = home / ".ez2-aib", home / ".cache" / "ez2-aib"
    if want_all:
        if other_aib:
            refuse.append(_t("aib.uninstall.refuse_all_other", link=link, state=state))
        else:
            for p, what in ((state, _t("aib.uninstall.what_state")), (cache, _t("aib.uninstall.what_cache"))):
                if p.exists():
                    remove.append((p, what))
    elif state.exists():
        keep.append(_t("aib.uninstall.keep_state", path=state))
    return remove, profiles, keep, refuse, hints


def _backup(state, home):
    dest = Path(home) / ("ez2-aib-backup-%s.tar.gz" % time.strftime("%Y%m%d-%H%M%S"))
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(str(state), arcname=".ez2-aib")
    return dest


def main(argv=None, home=None, package_dir=None):
    ap = argparse.ArgumentParser(prog="aib uninstall", description=_t("aib.uninstall.desc"))
    ap.add_argument("--yes", action="store_true", help=_t("aib.uninstall.opt_yes"))
    ap.add_argument("--all", action="store_true", help=_t("aib.uninstall.opt_all"))
    ap.add_argument("--discard-unsaved-work", action="store_true",
                    help="also remove this aib folder when it has uncommitted changes, untracked files, stashes, "
                         "commits not on any remote or other worktrees (they are lost)")
    a = ap.parse_args(argv)
    home = Path(home or Path.home())
    package_dir = package_dir or os.environ.get("AIB_PACKAGE_DIR") or str(Path(__file__).resolve().parents[2])
    rewrite = []
    remove, profiles, keep, refuse, hints = plan(home, package_dir, a.all, a.discard_unsaved_work, rewrite)
    for r in refuse:
        print(_t("aib.uninstall.refused", reason=r))
    if refuse:
        return 3
    if not remove and not profiles and not rewrite:
        print(_t("aib.uninstall.nothing"))
    for p, what in remove:
        print(_t("aib.uninstall.row_remove", path=p, what=what))
    for p in profiles:
        print(_t("aib.uninstall.row_edit", path=p))
    for p, _cfg, what in rewrite:
        print("  edit    %s  (%s)" % (p, what))
    for k in keep:
        print(_t("aib.uninstall.row_keep", text=k))
    for h in hints:
        print(_t("aib.uninstall.row_note", text=h))
    if not a.yes:
        print(_t("aib.uninstall.dry_done", cmd="aib uninstall%s%s --yes"
                 % (" --all" if a.all else "", " --discard-unsaved-work" if a.discard_unsaved_work else "")))
        return 0
    if not remove and not profiles and not rewrite:
        return 0
    state = home / ".ez2-aib"
    if any(p == state for p, _ in remove):
        print(_t("aib.uninstall.backed_up", path=_backup(state, home)))
    if sys.platform == "darwin" and any(p.name == BOOT_LABEL + ".plist" for p, _ in remove):
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), BOOT_LABEL)], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    for p in profiles:
        p.write_text(_profile_without_path_line(p.read_text(encoding="utf-8")), encoding="utf-8")
    for p, cfg, _what in rewrite:
        tmp = p.with_name(p.name + ".tmp-aib-uninstall-%d" % os.getpid())
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            shutil.copymode(str(p), str(tmp))
        except OSError:
            pass
        os.replace(str(tmp), str(p))
    # 先翻好再刪:刪的通常就是這份 aib 本身(連字典一起),刪完再查只剩鍵名。
    done = _t("aib.uninstall.done")
    for p, _ in remove:
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, onerror=_rm_gone_ok)
    print(done)
    return 0



def _rm_gone_ok(func, path, exc_info):
    """刪的時候檔案自己先不見了(例:git 背景打包的暫存檔 bitmap-ref-tips_*)⇒ 本來就要刪,算成功;其他錯照舊丟。
    onerror 參數 Python 3.9–3.13 都有(3.12 起另有 onexc,這裡不用,保 3.9 相容)。"""
    if isinstance(exc_info[1], FileNotFoundError):
        return
    raise exc_info[1]

if __name__ == "__main__":
    sys.exit(main())
