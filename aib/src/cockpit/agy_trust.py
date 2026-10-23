#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開 agy 席前先信任工作目錄(10-24 @abb89,Andy「卡選項的新開席都要能自動選成功」)。

agy(Antigravity CLI)第一次在一個資料夾起來會先問「Do you trust the contents of this project?」,
信任清單記在 <GEMINI_CLI_HOME 或 ~/.gemini>/antigravity-cli/settings.json 的 trustedWorkspaces(只比完整路徑,
信任 ~ 不代表信任底下的資料夾)。開席單、首回合提示都打不進那個畫面 ⇒ 席卡在那裡,畫面上也沒有人說為什麼。

人用 aib 在這個資料夾開席,就是要 AI 在這裡做事 —— 跟 Codex 那條 usage_failover.ensure_project_trusted 一樣,
開席前把這個資料夾寫進信任清單。只加不刪、其他設定原樣保留;設定檔讀不懂(壞 JSON、不是物件)就不動它,
寧可讓 agy 自己問,也不蓋掉別人的設定。AIB_AGY_TRUST=0 關。

用法:agy_trust.py ensure <資料夾> [--json]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def settings_path(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("GEMINI_CLI_HOME") or os.path.expanduser("~/.gemini")
    return Path(base) / "antigravity-cli" / "settings.json"


def ensure(folder, path=None, environ=None):
    """回 (結果, 說明)。結果:added / already / off / skip。"""
    env = os.environ if environ is None else environ
    if env.get("AIB_AGY_TRUST") == "0":
        return "off", "AIB_AGY_TRUST=0"
    try:
        target = str(Path(folder).resolve())
    except OSError as exc:
        return "skip", "folder:%s" % exc
    if not Path(target).is_dir():
        return "skip", "not_a_dir"
    cfg = Path(path) if path else settings_path(env)
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
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(cfg.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if cfg.exists():
            os.chmod(tmp, cfg.stat().st_mode & 0o777)
        os.replace(tmp, cfg)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return "skip", "write:%s" % exc
    return "added", target


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if len(argv) != 2 or argv[0] != "ensure":
        print("用法:agy_trust.py ensure <資料夾> [--json]", file=sys.stderr)
        return 2
    result, why = ensure(argv[1])
    if as_json:
        print(json.dumps({"result": result, "detail": why}, ensure_ascii=False))
    return 0                          # 開席的附帶動作:成敗都不擋開席


if __name__ == "__main__":
    sys.exit(main())
