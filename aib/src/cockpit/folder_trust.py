#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席前先信任工作目錄(10-24 @abb89,Andy「卡選項的新開席都要能自動選成功」)。

agy 和 grok(1.0.67 起)第一次在一個資料夾起來都會先問信不信任;開席單、首回合提示都打不進那個畫面
⇒ 席卡在那裡,畫面上也沒有人說為什麼。人用 aib 在這個資料夾開席,就是要 AI 在這裡做事 ——
跟 Codex 那條 usage_failover.ensure_project_trusted 一樣,開席前把這個資料夾寫進各家的信任清單。

  agy :<GEMINI_CLI_HOME 或 ~/.gemini>/antigravity-cli/settings.json 的 trustedWorkspaces(完整路徑逐字比)
  grok:<GROK_HOME 或 ~/.grok>/trusted_folders.toml 的 [folders."<路徑>"] trusted = true
        (它問的是 git 根目錄;不在 git 裡就是資料夾本身)

只加不刪、其他設定原樣保留;設定檔讀不懂就不動它(寧可讓 CLI 自己問,也不蓋掉別人的設定);
grok 那邊已經記了 trusted = false(人自己按過不信任)⇒ 照人的決定,不改。AIB_FOLDER_TRUST=0 關。

用法:folder_trust.py ensure <agy|grok> <資料夾> [--json]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import tomllib            # Python 3.11+
except ImportError:           # Mac 系統 Python 3.9:grok 那條讀不了 TOML ⇒ 不動(讓 grok 自己問)
    tomllib = None


def _env(environ):
    return os.environ if environ is None else environ


def _atomic_write(cfg, text):
    fd, tmp = tempfile.mkstemp(prefix=".%s-" % cfg.stem, suffix=cfg.suffix, dir=str(cfg.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if cfg.exists():
            os.chmod(tmp, cfg.stat().st_mode & 0o777)
        os.replace(tmp, cfg)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _too_wide(path, environ=None):
    """/、家目錄、家目錄的上層:信任下去等於整台 / 整個帳號都免問(第 2 輪審查)⇒ 不寫,讓 CLI 自己問。"""
    p = Path(path).resolve()
    home = Path(_env(environ).get("HOME") or os.path.expanduser("~")).resolve()
    return p == Path(p.anchor) or p == home or p in home.parents


def _folder(folder):
    target = Path(folder).resolve()
    return target if target.is_dir() else None


def _symlinked(cfg):
    # 設定檔是符號連結(連到 dotfiles)⇒ os.replace 會把連結換成一般檔、連結斷掉 ⇒ 不動
    return cfg.is_symlink()


def agy_settings_path(environ=None):
    base = _env(environ).get("GEMINI_CLI_HOME") or os.path.expanduser("~/.gemini")
    return Path(base) / "antigravity-cli" / "settings.json"


def ensure_agy(folder, path=None, environ=None):
    """回 (結果, 說明)。結果:added / already / skip。"""
    target = _folder(folder)
    if target is None:
        return "skip", "not_a_dir"
    if _too_wide(target, environ):
        return "skip", "too_wide"
    target = str(target)
    cfg = Path(path) if path else agy_settings_path(environ)
    if _symlinked(cfg):
        return "skip", "settings_is_symlink"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return "skip", "unreadable:%s" % type(exc).__name__
        if not isinstance(data, dict):
            return "skip", "not_an_object"
    else:
        if not cfg.parent.is_dir():
            return "skip", "no_agy_home"     # 這台還沒跑過 agy:不替它建目錄
        data = {}
    trusted = data.get("trustedWorkspaces")
    if trusted is None:
        trusted = []
    if not isinstance(trusted, list):
        return "skip", "trustedWorkspaces_not_a_list"
    if target in trusted:
        return "already", target
    data["trustedWorkspaces"] = trusted + [target]
    try:
        _atomic_write(cfg, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        return "skip", "write:%s" % exc
    return "added", target


def grok_trust_path(environ=None):
    base = _env(environ).get("GROK_HOME") or os.path.expanduser("~/.grok")
    return Path(base) / "trusted_folders.toml"


_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES", "GIT_INDEX_FILE",
                 "GIT_DISCOVERY_ACROSS_FILESYSTEM")


def grok_root(folder):
    """grok 問的是 git 根目錄;不在 git 裡(或沒有 git)就是資料夾本身。
    繼承來的 GIT_DIR / GIT_WORK_TREE 會讓 git 回別的地方(例如 /)⇒ 先清掉(第 2 輪審查)。"""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    try:
        r = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=5, stdin=subprocess.DEVNULL, env=env)
        top = r.stdout.strip()
        if r.returncode == 0 and top:
            return str(Path(top).resolve())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return str(Path(folder).resolve())


def _toml_str(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def ensure_grok(folder, path=None, environ=None, now=None):
    target = _folder(folder)
    if target is None:
        return "skip", "not_a_dir"
    root = grok_root(target)
    if _too_wide(root, environ):
        return "skip", "too_wide"         # 家目錄當 dotfiles 倉:git 根目錄 = 家目錄,不整個信任
    cfg = Path(path) if path else grok_trust_path(environ)
    if _symlinked(cfg):
        return "skip", "settings_is_symlink"
    if tomllib is None:
        return "skip", "no_tomllib"
    text = ""
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8")
            data = tomllib.loads(text)
        except (OSError, ValueError) as exc:
            return "skip", "unreadable:%s" % type(exc).__name__
        folders = data.get("folders", {})
        if not isinstance(folders, dict):
            return "skip", "folders_not_a_table"
        rec = folders.get(root)
        if isinstance(rec, dict) and "trusted" in rec:
            return ("already", root) if rec.get("trusted") is True else ("skip", "human_declined")
    elif not cfg.parent.is_dir():
        return "skip", "no_grok_home"
    block = "[folders.%s]\ntrusted = true\ndecided_at = %d\n" % (_toml_str(root), int(now or time.time()))
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    try:
        tomllib.loads(text + block)          # 寫之前先確定加完還是合法 TOML
        _atomic_write(cfg, text + block)
    except (OSError, ValueError) as exc:
        return "skip", "write:%s" % type(exc).__name__
    return "added", root


ENSURE = {"agy": ensure_agy, "grok": ensure_grok}


def ensure(provider, folder, environ=None):
    if _env(environ).get("AIB_FOLDER_TRUST") == "0":
        return "off", "AIB_FOLDER_TRUST=0"
    fn = ENSURE.get(provider)
    if fn is None:
        return "skip", "unsupported_provider"
    try:
        return fn(folder, environ=environ)
    except OSError as exc:
        return "skip", "folder:%s" % exc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if len(argv) != 3 or argv[0] != "ensure":
        print("用法:folder_trust.py ensure <agy|grok> <資料夾> [--json]", file=sys.stderr)
        return 2
    result, why = ensure(argv[1], argv[2])
    if result == "added":         # 動了人家的設定 ⇒ 開席畫面留一行看得到(審查第 2 位)
        print("aib: 已把 %s 加進 %s 的信任清單(在這裡開席 = 要它在這裡做事;不要自動加:AIB_FOLDER_TRUST=0)"
              % (why, argv[1]), file=sys.stderr)
    if as_json:
        print(json.dumps({"result": result, "detail": why}, ensure_ascii=False))
    return 0                          # 開席的附帶動作:成敗都不擋開席


if __name__ == "__main__":
    sys.exit(main())
