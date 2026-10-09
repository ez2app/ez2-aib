# SPDX-License-Identifier: Apache-2.0
"""Codex 帳號管理與自動續跑是同一套。關掉就回到原本的 codex 命令列。"""
import os
from pathlib import Path


def pack_path() -> Path:
    """每次現讀。測試改 HOME 之後才導入，不能在導入當下把路徑釘死。"""
    override = os.environ.get("AIB_CODEX_PACK_FILE")
    if override:
        return Path(override)
    return Path.home() / ".ez2-aib" / "codex-pack"


def enabled() -> bool:
    """預設開。環境變數蓋過檔案。沒有這個檔就是開。"""
    env = os.environ.get("AIB_CODEX_PACK")
    if env is not None and env != "":
        return env.strip().lower() not in ("0", "off", "false", "no")
    path = pack_path()
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip().lower()
        if raw in ("0", "off", "false", "no"):
            return False
    return True


def set_enabled(on: bool) -> None:
    path = pack_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on\n" if on else "off\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def status_line() -> str:
    if enabled():
        return (
            "Codex 套組：開。右欄帳號管理與自動續跑都在。"
            "關掉：aib codex-pack off（已開著的視窗要重開，才回到原本的命令列）"
        )
    return (
        "Codex 套組：關。右欄不畫帳號管理；新開的 Codex 依原本的命令列，"
        "不代選模型、不自動換號。打開：aib codex-pack on"
    )
