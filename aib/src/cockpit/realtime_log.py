#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""回應顯示區（realtime-lines）：與 AI 席原文直印。

契約（2026-09-20，the maintainer）：
  - 顯示：內層 aihub 作用中席的原文（去 ANSI／空行），**不經總結**。
  - 訊息輸入區只留打字；本模組不靠外層 hub（常只剩 ❯）。
  - summarize_transcript 仍保留給除錯 CLI，活艙同步改走 passthrough_transcript。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

REALTIME_FILE = "realtime-lines"
MAX_LINES = 80
MAX_BODY = 120

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# the maintainer> / you> / 你> / human>
_USER_RE = re.compile(
    r"^(?:the maintainer|you|You|YOU|你|human|Human|HUMAN)\s*[>：:]\s*(.*)$"
)
# grokbot> / deepseek> / codex> / nvidia> / Name>
_AI_RE = re.compile(
    r"^([A-Za-z][\w.-]{0,24})\s*[>：:]\s*(.*)$"
)
_PROMPT_ONLY = re.compile(r"^[❯>\$%]\s*.*$")
_WORKING = re.compile(r"\bWorking\b|\bthinking\b|loading\.\.\.|執行中", re.I)
_RAN = re.compile(r"^\s*(?:•\s*)?Ran\b|✓\s*Ran\b|已跑完|tool call|aib_dm", re.I)
_NOISE = re.compile(
    r"^(?:模型原文|RESULT stage=|aib-dm-ctl:|rc_contract|"
    r"─{5,}|esc to interrupt|Ask Codex|for shortcuts|"
    r"API key|Ctrl-D|/exit|/quit|session=)",
    re.I,
)


def realtime_path(cockpit_dir: str | Path) -> Path:
    return Path(cockpit_dir) / REALTIME_FILE


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


def _clip(s: str, n: int = MAX_BODY) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _is_prompt(s: str) -> bool:
    t = _strip_ansi(s).strip()
    if not t:
        return True
    if t in ("❯", ">", ">>>", "$", "%"):
        return True
    # bare prompt with only in-progress typing — keep out of history dump
    if t.startswith("❯") and len(t) < 80:
        return True
    if _PROMPT_ONLY.match(t) and not _USER_RE.match(t) and not _AI_RE.match(t):
        return True
    return False


def capture_active_seat(socket: str, lines: int = 200) -> tuple[str, list[str]]:
    """從內層 aihub 作用中窗抓列。回 (seat_label, lines)。"""
    sock = (socket or "").strip()
    if not sock:
        return "", []
    try:
        win = subprocess.run(
            ["tmux", "-L", sock, "display-message", "-t", "aihub", "-p",
             "#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
        name = (win.stdout or "").strip() or "seat"
        pane = subprocess.run(
            ["tmux", "-L", sock, "display-message", "-t", "aihub", "-p",
             "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pid = (pane.stdout or "").strip()
        if not pid:
            return name, []
        cap = subprocess.run(
            ["tmux", "-L", sock, "capture-pane", "-t", pid, "-p", "-J",
             "-S", f"-{int(lines)}"],
            capture_output=True, text=True, timeout=3,
        )
        raw = (cap.stdout or "").splitlines()
        return name, raw
    except Exception:
        return "", []


def summarize_transcript(raw_lines: list[str], seat: str = "") -> list[str]:
    """把席上原文收成短摘要列。

    約定：
      - the maintainer> / you> / 你> → 你
      - 與席名對應的提示符（如 DeepSeek>、Codex>）後接文字 → 你（正在打／已送出）
      - 小寫 bot 標籤（deepseek>、grokbot>）→ AI
      - Working / Ran → 短摘要；完整瀑布不保留
    """
    seat_bit = (seat or "").replace("root-", "").replace("root_", "").strip()
    seat_prompt = seat_bit[:1].upper() + seat_bit[1:] if seat_bit else ""
    # deepseek → DeepSeek；grokbot → Grokbot；nvidia → Nvidia
    if seat_bit.lower() == "deepseek":
        seat_prompt = "DeepSeek"
    elif seat_bit.lower() == "grokbot":
        seat_prompt = "Grokbot"
    elif seat_bit.lower() == "codex":
        seat_prompt = "Codex"
    ai_name = seat_bit.lower() or "AI"
    out: list[str] = []
    ai_buf: list[str] = []
    work_n = 0
    ran_n = 0

    def flush_ai():
        nonlocal ai_buf
        if not ai_buf:
            return
        body = next((x for x in ai_buf if x and x not in ("…", "...", "·")), "")
        if not body:
            body = ai_buf[0]
        # 略過「未執行 DM」噪音當唯一內容
        if "未執行 DM" in body and len(ai_buf) == 1:
            ai_buf = []
            return
        out.append(f"{ai_name}  {_clip(body)}")
        ai_buf = []

    def flush_proc():
        nonlocal work_n, ran_n
        if work_n or ran_n:
            bits = []
            if work_n:
                bits.append("執行中" if work_n == 1 else f"執行中×{work_n}")
            if ran_n:
                bits.append(f"已跑完 {ran_n} 步")
            out.append(f"{ai_name}  {' · '.join(bits)}")
            work_n = 0
            ran_n = 0

    for ln in raw_lines:
        tln = _strip_ansi(ln).rstrip()
        if not tln.strip():
            continue
        if _is_prompt(tln) and not re.match(r"^(?:the maintainer|you|You|你|human)\s*[>：:]", tln.strip()):
            # bare ❯ only
            if re.match(r"^[❯>$%]", tln.strip()) and not re.search(r"[>：:]\s*\S", tln):
                continue
        if _NOISE.search(tln.strip()):
            continue

        s = tln.strip()

        # 1) 明確使用者標籤
        um = _USER_RE.match(s)
        if um:
            flush_proc(); flush_ai()
            msg = _clip(um.group(1))
            if msg:
                out.append(f"你  {msg}")
            continue

        # 2) 席提示符 DeepSeek> / Codex> … → 使用者輸入
        if seat_prompt:
            pm = re.match(rf"^{re.escape(seat_prompt)}\s*[>：:]\s*(.*)$", s)
            if pm:
                flush_proc(); flush_ai()
                msg = _clip(pm.group(1))
                if msg:
                    out.append(f"你  {msg}")
                continue

        # 3) AI 標籤（小寫或明確 bot 名）
        am = _AI_RE.match(s)
        if am:
            name, rest = am.group(1), am.group(2)
            low = name.lower()
            if low in ("result", "model", "session", "aib-dm", "aib_dm", "system"):
                continue
            if "未執行 DM" in rest or rest.startswith("停投名單") or "暫存落點" in rest:
                ran_n += 1
                continue
            # 若名稱等於席提示符（大小寫相同那種大寫提示），上面已處理；
            # 這裡把「首字母大寫且等於席名」也當使用者（保險）
            if seat_prompt and name == seat_prompt:
                flush_proc(); flush_ai()
                msg = _clip(rest)
                if msg:
                    out.append(f"你  {msg}")
                continue
            flush_proc()
            if rest.strip() in ("…", "...", "·", ""):
                work_n += 1
                continue
            if _WORKING.search(rest):
                work_n += 1
                continue
            ai_name = low
            if "未執行 DM" in rest:
                ran_n += 1
                continue
            ai_buf.append(rest.strip())
            continue

        if _WORKING.search(tln):
            flush_ai(); work_n += 1; continue
        if _RAN.search(tln):
            flush_ai(); ran_n += 1; continue

        # 噪音／系統
        if "未執行 DM" in s or s.startswith("模型原文") or s.startswith("Recording it"):
            continue
        if s.startswith("aib-dm") or "停投名單" in s or "暫存落點" in s:
            continue
        # 無標籤正文：若前面剛有「你」且尚無 AI 正文，當 AI 回覆首句
        if (
            not ai_buf
            and out
            and out[-1].startswith("你  ")
            and len(s) >= 8
            and not s.startswith("•")
            and not s.startswith("Ran")
            and not _WORKING.search(s)
        ):
            # 跳過純英文系統段落開頭
            if s.startswith("Received a message") or s.startswith("I won't"):
                continue
            ai_buf.append(s)
            continue
        if ai_buf:
            if len(ai_buf) < 2 and not s.startswith("**"):
                if len(ai_buf[0]) < 40:
                    ai_buf[0] = _clip(ai_buf[0] + " " + s)
            continue

    flush_proc()
    flush_ai()

    dedup: list[str] = []
    for x in out:
        if dedup and dedup[-1] == x:
            continue
        # 連續「執行中」合併
        if (
            dedup
            and dedup[-1].endswith("執行中")
            and x.endswith("執行中")
            and dedup[-1].split()[0] == x.split()[0]
        ):
            continue
        dedup.append(x)
    return dedup[-MAX_LINES:]



def passthrough_transcript(raw_lines: list[str], seat: str = "") -> list[str]:
    """席上原文直通：只去 ANSI／純空行／裸提示符，不總結成短摘要。"""
    out: list[str] = []
    for ln in raw_lines or []:
        s = _strip_ansi(ln).rstrip()
        if not s.strip():
            continue
        # 裸 ❯／> 提示（沒有後續字）不佔顯示列
        st = s.strip()
        if st in ("❯", ">", ">>>", "$", "%"):
            continue
        if st.startswith("❯") and len(st) <= 2:
            continue
        out.append(s)
    return out[-MAX_LINES:]



def write_realtime(cockpit_dir, lines: list[str]) -> Path:
    path = realtime_path(cockpit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + ("\n" if lines else "")
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = None
    if old == text:
        return path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def sync_from_aihub(cockpit_dir, socket: str | None = None) -> list[str]:
    sock = socket or os.environ.get("EZ2_AIHUB_SOCKET") or ""
    seat, raw = capture_active_seat(sock)
    lines = passthrough_transcript(raw, seat=seat)
    write_realtime(cockpit_dir, lines)
    return lines


def _cli(argv=None):
    ap = argparse.ArgumentParser(prog="realtime_log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--cockpit", required=True)
    p.add_argument("--socket", default="")
    r = sub.add_parser("summarize")
    r.add_argument("--file", help="raw transcript file")
    r.add_argument("--seat", default="")
    args = ap.parse_args(argv)
    if args.cmd == "sync":
        for ln in sync_from_aihub(args.cockpit, socket=args.socket or None):
            print(ln)
    elif args.cmd == "summarize":
        raw = Path(args.file).read_text(encoding="utf-8").splitlines() if args.file else []
        for ln in summarize_transcript(raw, seat=args.seat):
            print(ln)


if __name__ == "__main__":
    _cli()
