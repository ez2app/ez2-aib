#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib verify 開關(verify-p1;由 255(a) resume on|off 擴成三模式)。

    aib verify 快驗|quick    挑受影響的測試 + 沿用 + 抽查(預設,the maintainer 09-26 已裁)
    aib verify 嚴格|strict   全套照跑,沿用只用來接中斷
    aib verify 狀態|status   模式、沿用池大小、上次省時、今天/本週/本月累計

「關閉|off」09-29 拿掉(the maintainer:「除非 1 沒啟用才用原來的」;docs/DESIGN-aib-test-entry-shadow-0929.md 第六節):
    打 `aib verify 關閉` ⇒ rc=2、說明已停用、設定檔一個位元組都不寫。
    舊設定檔裡已經寫著 off ⇒ 當快驗(source="legacy_off"),狀態頁說一句。
    ★ 不能讓舊 off 落進「讀不懂」:讀不懂在 is_off 裡等於關閉,那樣開關只是換個樣子還在。

設定存哪裡(理由):
    <git-common-dir>/aib/verify-mode.json
    1. `<倉>/.aib/verify.json` 已是專案宣告檔(第 113 項 src/verify_profile)的名字,不能共用。
    2. 不用 toml:aib 找不到 homebrew python 時退回 /usr/bin/python3(3.9),沒有 tomllib。
    3. 放 git 目錄而不放工作樹:不會被 commit、不讓 `git status` 變髒、同倉各工作樹共用一份。
    不在 git 倉裡時退回 ~/.ez2-aib/verify/mode.json。

優先序:設定檔 > AIB_WITNESS_SHADOW(舊開關;"0"=嚴格、其他=快驗)> 預設快驗。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "src" / "cockpit"))
import i18n_lib  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
SCHEMA = "aib-verify-mode/v1"
MODES = ("quick", "strict")
RETIRED_MODES = ("off",)   # 讀得懂、不再生效;寫不進去
DEFAULT_MODE = "quick"
# 中英都收;畫面文字仍依介面語言走翻譯檔。
ALIASES = {
    "quick": "quick", "快驗": "quick",
    "strict": "strict", "嚴格": "strict",
    "off": "retired-off", "關閉": "retired-off",
    "status": "status", "狀態": "status",
}


def T(key, params=None):
    return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale())


def _git(args, cwd=None):
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(cwd or os.getcwd()),
            capture_output=True, text=True, timeout=8,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = (p.stdout or "").strip()
    return out or None


def git_toplevel(cwd=None):
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    return Path(top) if top else None


def config_path(repo=None):
    if repo is None:
        repo = git_toplevel()
    if repo is not None:
        common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], repo)
        if common:
            return Path(common) / "aib" / "verify-mode.json"
    return Path(os.environ.get("HOME") or str(Path.home())) / ".ez2-aib" / "verify" / "mode.json"


def _read_config(path: Path):
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "not-object"
    return data, None


def current_mode(repo=None):
    """回 (mode, source, path)。mode ∈ quick/strict/None;source ∈ file/legacy_off/env/default/bad。"""
    path = config_path(repo)
    data, err = _read_config(path)
    # verify-p1-file-read-begin
    if data is not None:
        mode = data.get("mode")
        if mode in MODES:
            return mode, "file", path
        if mode in RETIRED_MODES:
            return DEFAULT_MODE, "legacy_off", path
        return None, "bad", path
    # verify-p1-file-read-end
    if err not in (None, "missing"):
        return None, "bad", path
    shadow = os.environ.get("AIB_WITNESS_SHADOW")
    if shadow is not None:
        return ("strict" if shadow == "0" else "quick"), "env", path
    return DEFAULT_MODE, "default", path


def is_off(repo=None):
    """給寫檔的一方問:什麼都不准寫嗎?只剩一種情形:設定檔讀不懂(寧可不寫,不猜)。
    「關閉」這一檔 09-29 已拿掉,舊檔寫著 off 的當快驗(見 current_mode),不會走到這裡變成不寫。"""
    _mode, source, _ = current_mode(repo)
    return source == "bad"


def _now_taipei():
    return datetime.now(TAIPEI).replace(microsecond=0).isoformat()


def _who():
    return os.environ.get("EZ2_AIB_SEAT") or os.environ.get("AIB_SEAT") or "unknown"


def write_mode(mode, repo=None):
    path = config_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": SCHEMA, "mode": mode, "since": _now_taipei(), "by": _who()}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        return path, e
    return path, None


def _env_disagrees(mode):
    shadow = os.environ.get("AIB_WITNESS_SHADOW")
    if shadow is None:
        return False
    return ("strict" if shadow == "0" else "quick") != mode


def _mode_word(mode):
    return T("aib.verify.mode." + mode)


def cmd_status(repo=None):
    mode, source, path = current_mode(repo)
    if source == "bad":
        print(T("aib.verify.bad_file", {"path": str(path)}), file=sys.stderr)
        return 3
    print(T("aib.verify.status_mode", {"mode": _mode_word(mode), "path": str(path)}))
    if source == "default":
        print(T("aib.verify.status_default_hint"))
    elif source == "legacy_off":
        print(T("aib.verify.legacy_off_as_quick", {"path": str(path)}))
    elif source == "env":
        print(T("aib.verify.bridged_from_env"), file=sys.stderr)
    elif _env_disagrees(mode):
        print(T("aib.verify.file_beats_env"), file=sys.stderr)
    try:
        import receipt  # 同目錄;延後匯入,關閉時不碰帳本
        for line in receipt.status_lines(repo or git_toplevel()):
            print(line)
    except Exception as e:  # 狀態頁不因帳本壞掉而整個失敗,但要看得見
        print(T("aib.verify.status_ledger_unreadable", {"why": type(e).__name__}), file=sys.stderr)
    return 0


def cmd_set(mode, repo=None):
    if mode not in MODES:            # 09-29:off 已拿掉;直接呼叫也不准寫進設定檔
        print(T("aib.verify.off_retired"), file=sys.stderr)
        return 2
    path, err = write_mode(mode, repo)
    if err is not None:
        print(T("aib.verify.write_failed", {"path": str(path)}), file=sys.stderr)
        return 3
    print(T("aib.verify.set_" + mode))
    if _env_disagrees(mode):
        print(T("aib.verify.file_beats_env"), file=sys.stderr)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["--help"], ["-h"]):
        print(T("aib.verify.help"))
        print(T("aib.verify.usage"))
        return 0
    if len(argv) != 1:
        print(T("aib.verify.usage"), file=sys.stderr)
        return 2
    cmd = ALIASES.get(argv[0].strip())
    if cmd is None:
        print(T("aib.verify.usage"), file=sys.stderr)
        return 2
    if cmd == "retired-off":
        print(T("aib.verify.off_retired"), file=sys.stderr)
        return 2
    if cmd == "status":
        return cmd_status()
    return cmd_set(cmd)


if __name__ == "__main__":
    sys.exit(main())
