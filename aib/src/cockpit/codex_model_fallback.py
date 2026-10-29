# SPDX-License-Identifier: Apache-2.0
"""Codex 型號被登入拒絕時退一版重試，並把實際用到的那一版寫回席位檔。

10-28(@aeb40 工作單 B,#396 審查第 2 點):換帳號頁(codex_account_recover)登入後直接
exec 最新預設型號，被拒就停在那裡，不像 provider-launch 的啟動迴圈會一版一版往舊的試。
退版表是 codex_luna_model.previous_default_slug(Terra 由新到舊、試完換 Luna),
上限 MAX_STEPS 與 provider-launch 相同。退到哪一版：
  - 印一行到 stderr(不印的話型號被換掉是無聲的);
  - 追加一筆到 <狀態根>/codex-model-fallback.jsonl;
  - 這一席的席位檔 model_requested 改成實際用到的那一版(原地復活照席位檔開，
    不再先撞一次被拒的型號)。只寫 model_requested:fleet_restore 拒絕席位檔裡其他 model 欄位。
手指定的型號(AIB_CODEX_MODEL_EXPLICIT=1)不退版，照 provider-launch 的規矩：使用者指定 > 預設。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import codex_luna_model  # noqa: E402
import seats_dir_lib  # noqa: E402

#: 與 provider-launch.sh 的 `[ "$_model_steps" -lt 4 ]` 同一個上限。
MAX_STEPS = 4
LOG_NAME = "codex-model-fallback.jsonl"
_MODEL_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._:/-]*")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _tmux(environ) -> str:
    return environ.get("AIB_TMUX_BIN") or "tmux"


def own_window(environ=None, run: Runner = subprocess.run) -> str:
    """這一格所在的窗 id(@N)。不在 tmux 裡、或問不到 ⇒ 空字串。"""
    environ = os.environ if environ is None else environ
    pane = environ.get("TMUX_PANE") or ""
    if not pane:
        return ""
    try:
        out = run([_tmux(environ), "display-message", "-p", "-t", pane, "#{window_id}"],
                  capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    wid = (out.stdout or "").strip()
    return wid if re.fullmatch(r"@\d+", wid) else ""


def seat_file(environ=None, window: str = "") -> Path | None:
    if not re.fullmatch(r"@\d+", window or ""):
        return None
    environ = os.environ if environ is None else environ
    return Path(seats_dir_lib.seats_dir(environ=environ)) / ("w%s.json" % window[1:])


def record(old: str, new: str, *, window: str = "", reason: str = "model_rejected",
           environ=None, now: float | None = None) -> dict:
    """記下這次退版。席位檔不在就不建(只改既有的那一席)。回傳寫了什麼，給呼叫端與測試看。"""
    environ = os.environ if environ is None else environ
    if not _MODEL_RE.fullmatch(new or ""):
        raise ValueError("invalid model: %r" % new)
    when = int(time.time() if now is None else now)
    seat_written = False
    path = seat_file(environ, window)
    if path is not None and path.is_file():
        from atomic_json import update_json

        skipped = []

        def _mutate(rec):
            rec = dict(rec)
            if rec.get("requested_source") == "flag":
                skipped.append(True)      # 手指定的席不改(使用者明講的型號不偷換,#400 審查 Medium)
                return rec
            rec["model_requested"] = new
            return rec
        try:
            update_json(str(path), _mutate)
            seat_written = not skipped
        except Exception:  # noqa: BLE001  寫不進席位檔不擋開席，紀錄檔照記、stderr 照印
            seat_written = False
    row = {"ts": when, "from": old, "to": new, "reason": reason, "window": window,
           "seat_file": str(path) if path is not None else "", "seat_written": seat_written}
    root = Path(seats_dir_lib.state_root(environ)[0])
    try:
        root.mkdir(parents=True, exist_ok=True)
        with open(root / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


def capture_seen(environ=None, run: Runner = subprocess.run, errfile: str = "") -> str:
    """Codex 結束後畫面上留下的字。測試走 errfile;真席走 tmux capture-pane -J(同 provider-launch)。"""
    environ = os.environ if environ is None else environ
    if errfile:
        try:
            return Path(errfile).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    pane = environ.get("TMUX_PANE") or ""
    if not pane:
        return ""
    try:
        out = run([_tmux(environ), "capture-pane", "-p", "-J", "-t", pane],
                  capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


def launch_with_fallback(argv_for: Callable[[str], list[str]], model: str, env: dict, *,
                         auto: bool, run: Runner = subprocess.run,
                         capture: Callable[[], str] | None = None,
                         record_fn: Callable[..., dict] = record,
                         window: str = "", errfile: str = "",
                         cache_path: str | None = None,
                         err=sys.stderr) -> tuple[int, str, int]:
    """跑 Codex;型號被拒(而且是自動挑的型號)就照退版表換舊一版再跑，最多 MAX_STEPS 次。

    回 (結束碼, 最後用的型號, 退了幾次)。"""
    steps = 0
    while True:
        argv = argv_for(model)
        if errfile:
            with open(errfile, "w", encoding="utf-8") as fh:
                rc = run(argv, env=env, stdout=fh, stderr=subprocess.STDOUT, check=False).returncode
        else:
            rc = run(argv, env=env, check=False).returncode
        if not auto or steps >= MAX_STEPS:
            return rc, model, steps
        if rc == 0:
            # 正常結束不判被拒(同 provider-launch:rc 0 且不用續跑就收)。畫面上可能還殘留先前的
            #   "model is not supported",不看 rc 的話使用者正常離開後又會開更舊的型號(#400 審查 Low)。
            return rc, model, steps
        seen = capture() if capture is not None else capture_seen(env, run, errfile)
        if not codex_luna_model.model_rejected(seen):
            return rc, model, steps
        older = codex_luna_model.previous_default_slug(model, cache_path)
        if not older:
            return rc, model, steps
        err.write("codex: %s 開不起來，改用舊一版 %s\n" % (model, older))
        err.flush()
        record_fn(model, older, window=window, environ=env)
        model = older
        steps += 1


def main(argv: list[str] | None = None) -> int:
    """provider-launch 啟動迴圈退版後呼叫：codex_model_fallback.py record <舊> <新>。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "record":
        try:
            record(argv[1], argv[2], window=own_window())
        except ValueError:
            return 2
        return 0
    sys.stderr.write("usage: codex_model_fallback.py record <from> <to>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
