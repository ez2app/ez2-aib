#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib uninstall: remove the open-source aib from this computer. Lists first; removes only with --yes.

    aib uninstall              show what would be removed (nothing is touched)
    aib uninstall --yes        remove the program: this checkout, the ~/.local/bin/aib link to it, the boot menu,
                               the PATH line the installer added
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
import os
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


def plan(home, package_dir, want_all):
    """⇒ (remove: [(path, what)], profiles: [path], keep: [str], refuse: [str], hints: [str])."""
    home = Path(home)
    remove, profiles, keep, refuse, hints = [], [], [], [], []
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
        remove.append((checkout, _t("aib.uninstall.what_checkout")))
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
    a = ap.parse_args(argv)
    home = Path(home or Path.home())
    package_dir = package_dir or os.environ.get("AIB_PACKAGE_DIR") or str(Path(__file__).resolve().parents[2])
    remove, profiles, keep, refuse, hints = plan(home, package_dir, a.all)
    for r in refuse:
        print(_t("aib.uninstall.refused", reason=r))
    if refuse:
        return 3
    if not remove and not profiles:
        print(_t("aib.uninstall.nothing"))
    for p, what in remove:
        print(_t("aib.uninstall.row_remove", path=p, what=what))
    for p in profiles:
        print(_t("aib.uninstall.row_edit", path=p))
    for k in keep:
        print(_t("aib.uninstall.row_keep", text=k))
    for h in hints:
        print(_t("aib.uninstall.row_note", text=h))
    if not a.yes:
        print(_t("aib.uninstall.dry_done", cmd="aib uninstall%s --yes" % (" --all" if a.all else "")))
        return 0
    if not remove and not profiles:
        return 0
    state = home / ".ez2-aib"
    if any(p == state for p, _ in remove):
        print(_t("aib.uninstall.backed_up", path=_backup(state, home)))
    if sys.platform == "darwin" and any(p.name == BOOT_LABEL + ".plist" for p, _ in remove):
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), BOOT_LABEL)], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    for p in profiles:
        p.write_text(_profile_without_path_line(p.read_text(encoding="utf-8")), encoding="utf-8")
    for p, _ in remove:
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    print(_t("aib.uninstall.done"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
