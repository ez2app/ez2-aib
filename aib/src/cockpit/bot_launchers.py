#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""bot 收信入口(~/.local/bin/aib-grokbot-mail 等)由 aib 自己裝(10-28 Andy:必要功能)。

為什麼:各家 bot 的例行任務 / hook 只寫固定入口 ~/.local/bin/aib-<bot>-mail(合併、搬樹都不用請 bot 改路徑)。
以前由私有倉的 tools/aib-install-path 裝;公開版(ez2-aib)沒有那支 ⇒ 新機器上入口根本不在,提示叫人跑的指令也不存在。

何時跑(可重跑、不出錯):
  - 一行安裝(install.sh)、aib init(pip / pipx 首次安裝後的那一步)
  - aib update 與自動更新(auto_update)換版成功之後 —— 用**換版後**這份
  - 開 grokbot / claudebot / musebot 席之前、aib bot connect grokbot 綁好之後(補救)
  - 手動:aib bot install-launchers
做什麼:每個入口 = 一支 0755 的小 sh,exec 目前這份安裝裡的 <bot>_mailbox.py(pip 裝在 site-packages、原始碼裝在 checkout,
  一律用這支檔案自己所在的資料夾,不猜)。
  不在 ⇒ 建;是連結(例如指向舊倉 ~/ez2-ai-b/tools/…)、不是 aib 寫的、指的不是目前這份、直譯器不見了、不能執行 ⇒ 覆寫。
  不是 aib 寫的一般檔案先備份到 ~/.ez2-aib/backup/launchers/。先寫暫檔再原子 rename。
  AIB_BOT_LAUNCHERS=0 全關;AIB_PATH_BIN 換放置資料夾(預設 ~/.local/bin)。

    python3 bot_launchers.py ensure [--quiet] [--only aib-grokbot-mail]   rc 0 = 全部就緒、1 = 有寫不進去的
    python3 bot_launchers.py status [--json]                              只看,不寫
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHERS = {
    "aib-grokbot-mail": "grokbot_mailbox.py",
    "aib-claudebot-mail": "claudebot_mailbox.py",
    "aib-musebot-mail": "musebot_mailbox.py",
}
MARK = "# aib-bot-launcher v1"
ENV_BIN = "AIB_PATH_BIN"
ENV_OFF = "AIB_BOT_LAUNCHERS"
OK, CREATED, UPDATED, FAILED, OFF = "ok", "created", "updated", "failed", "off"


def _t(key: str, fallback: str, **params) -> str:
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import i18n_lib
        text = i18n_lib.t("aib.launchers." + key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    if not text or text.startswith("!!"):
        try:
            text = fallback.format(**params)
        except (KeyError, IndexError, ValueError):
            text = fallback
    return text


def bin_dir(env=None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get(ENV_BIN) or "").strip()
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(raw).expanduser() if raw else Path(home) / ".local" / "bin"


def _state_root(env) -> Path:
    for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        if env.get(k):
            return Path(env[k]).expanduser()
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib"


def target(name: str) -> Path:
    return HERE / LAUNCHERS[name]


def desired(name: str, python: str = None) -> str:
    py = python or sys.executable or "/usr/bin/python3"
    return ("#!/bin/sh\n%s — aib 收信入口;aib 安裝 / 更新 / 開席時自動重寫(手動:aib bot install-launchers)\n"
            "exec %s %s \"$@\"\n") % (MARK, shlex.quote(py), shlex.quote(str(target(name))))


def _parse(text: str):
    """aib 寫的入口 ⇒ (直譯器, 腳本);不是 ⇒ None。"""
    if MARK not in text:
        return None
    for ln in text.splitlines():
        if ln.startswith("exec "):
            try:
                parts = shlex.split(ln[5:])
            except ValueError:
                return None
            if len(parts) >= 2:
                return parts[0], parts[1]
    return None


def state(name: str, env=None) -> tuple:
    """(狀態, 原因)。狀態 ∈ ok / missing / stale;原因給人看(不含機密,這些檔本來就沒有機密)。"""
    p = bin_dir(env) / name
    if p.is_symlink():
        return "stale", "symlink → %s" % os.readlink(p)
    if not p.exists():
        return "missing", ""
    if not p.is_file():
        return "stale", "not a file"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "stale", type(exc).__name__
    got = _parse(text)
    if got is None:
        return "stale", "not written by aib"
    py, script = got
    if Path(script) != target(name):
        return "stale", "points to %s" % script
    if not (os.path.isfile(py) and os.access(py, os.X_OK)):
        return "stale", "python missing: %s" % py
    if not os.access(p, os.X_OK):
        return "stale", "not executable"
    return "ok", ""


def _backup(p: Path, env) -> None:
    """不是 aib 寫的一般檔案:覆寫前先留一份(連結不用備份:它只是一個指向,訊息裡會講指到哪)。"""
    if p.is_symlink() or not p.is_file():
        return
    try:
        if _parse(p.read_text(encoding="utf-8", errors="replace")) is not None:
            return
        d = _state_root(env) / "backup" / "launchers"
        d.mkdir(parents=True, exist_ok=True)
        dest = d / ("%s.%s" % (p.name, time.strftime("%Y%m%d-%H%M%S")))
        dest.write_bytes(p.read_bytes())
    except OSError:
        pass


def ensure_one(name: str, env=None, python: str = None) -> tuple:
    env = os.environ if env is None else env
    st, why = state(name, env)
    if st == "ok":
        return OK, ""
    if not target(name).is_file():
        return FAILED, "missing %s" % target(name)
    d = bin_dir(env)
    p = d / name
    tmp = d / (".%s.tmp.%d" % (name, os.getpid()))
    try:
        d.mkdir(parents=True, exist_ok=True)
        _backup(p, env)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(desired(name, python))
        os.chmod(tmp, 0o755)
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return FAILED, "%s: %s" % (type(exc).__name__, exc.strerror or "")
    return (CREATED if st == "missing" else UPDATED), why


def ensure(env=None, only=None, python: str = None) -> list:
    """→ [(入口名, 動作, 原因)]。AIB_BOT_LAUNCHERS=0 ⇒ 全部 off、什麼都不寫。"""
    env = os.environ if env is None else env
    names = [n for n in LAUNCHERS if not only or n in only]
    if (env.get(ENV_OFF) or "1") == "0":
        return [(n, OFF, "") for n in names]
    return [(n,) + ensure_one(n, env, python) for n in names]


def lines(rows, quiet=False, env=None) -> list:
    out = []
    for name, act, why in rows:
        p = bin_dir(env) / name
        if act == OK and quiet:
            continue
        if act == OFF:
            if not quiet:
                out.append(_t("off", "{name}:自動安裝關著(AIB_BOT_LAUNCHERS=0),沒動", name=name))
            continue
        if act == OK:
            out.append(_t("ok", "{path}:已就緒", path=str(p)))
        elif act == CREATED:
            out.append(_t("created", "{path}:已建立收信入口(0755)", path=str(p)))
        elif act == UPDATED:
            out.append(_t("updated", "{path}:已改成指向目前這份 aib(原本:{why})", path=str(p), why=why))
        else:
            out.append(_t("failed", "{path}:收信入口寫不進去({why});之後跑 aib bot install-launchers 再試",
                          path=str(p), why=why))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib bot install-launchers")
    ap.add_argument("cmd", nargs="?", default="ensure", choices=("ensure", "status"))
    ap.add_argument("--quiet", action="store_true", help="只印有改動或失敗的")
    ap.add_argument("--only", action="append", default=[], choices=sorted(LAUNCHERS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        rows = [(n,) + state(n) for n in LAUNCHERS if not args.only or n in args.only]
        if args.json:
            print(json.dumps([{"name": n, "state": s, "why": w, "path": str(bin_dir() / n)} for n, s, w in rows],
                             ensure_ascii=False))
        else:
            for n, s, w in rows:
                print("%s  %s%s" % (s, bin_dir() / n, ("  (%s)" % w) if w else ""))
        return 0 if all(s == "ok" for _, s, _ in rows) else 1
    rows = ensure(only=args.only or None)
    if args.json:
        print(json.dumps([{"name": n, "action": a, "why": w} for n, a, w in rows], ensure_ascii=False))
    else:
        for ln in lines(rows, quiet=args.quiet):
            print("aib: " + ln, file=sys.stderr if args.quiet else sys.stdout)
    return 1 if any(a == FAILED for _, a, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
