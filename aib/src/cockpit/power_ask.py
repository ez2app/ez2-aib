#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室「問 AI」抽屜的核心(契約 contracts/power.json ask_receipt;SPEC-power-room §12)。

  aib power ask <家> [--model M] [--json]     問題從 stdin 讀:純文字,或 {"question": "...", "history": [{"q","a"}…]}
  aib power ask --suggest [--json]            建議問題 3 則(代碼;照「為什麼用 AIB」排名換)

10-24 Andy:「要有一個點擊他選的 AI 在這個視窗中，即時對話用來 Q&A」+ @abb89 硬線「只回答、不動手」。
- 只叫得動能驗證關工具的家:claude(--tools "" + --strict-mcp-config 不載 MCP + 關 skill / 指令)、
  codex(唯讀沙盒 + 清空 MCP + 不留對話)。其他家 ⇒ power_ask_unsupported(驗不了就不叫,同帳號 AI 威脅守則)。
- 那家不是綠燈 ⇒ power_ask_login,頁面導去登入;不偷換別家。
- 問題走 stdin(ps 看不到),跑在空的暫存目錄、跑完刪;不寫進任何席位、不存對話。
- 回答裡的指令只抽出來給頁面「複製」,核心不執行;回答去控制字元、有長度上限。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TIMEOUT_S = 90
Q_MAX = 2000
A_MAX = 8000
HISTORY_MAX = 6
CMDS_MAX = 5
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,95}$")
_CMD_RE = re.compile(r"^\s*(?:\$\s*)?(aib(?: [A-Za-z0-9_./:=,@-]+){0,12})\s*$")
_TASK_RE = re.compile(r"\b(UR[1-7]|SSR(?:1[0-2]|[1-9])|SR(?:1[0-3]|[1-9])|R(?:1[0-2]|[1-9]))\b")

# 關工具的呼叫法;改這裡要先過同帳號 AI 威脅守則的檢查表
CLAUDE_CMD = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--tools", "",
              "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "1"]
CODEX_CMD = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "--ephemeral", "--color", "never",
             "-c", "mcp_servers={}"]
BRANDS = ("claude", "codex")

# 建議問題(代碼;字在頁面 i18n)。照「為什麼用 AIB」排名挑對應的,不夠再補通用的
SUGGEST = {"memory": "q_memory", "multi_ai": "q_multi_ai", "coding": "q_coding", "phone": "q_phone",
           "sites": "q_sites", "art": "q_art", "room": "q_room", "save": "q_save"}
SUGGEST_BASE = ("q_first_seat", "q_lights", "q_handoff")

PREAMBLE = """You are the help desk inside the aib Power Room. aib is a command-line cockpit that runs several AI \
coding seats (Claude, Codex, Grok, Copilot, Antigravity) side by side, with a captain-room game view.
Rules:
- Only answer. You cannot run commands, read or change files, or press any buttons; never claim you did.
- If something needs doing, tell the user how to do it themselves. Put each command on its own line starting with `aib`.
- If a beginner task card helps, mention its id (R1-R12, SR1-SR13, SSR1-SSR12, UR1-UR7).
- Never ask for or repeat passwords, keys, tokens or purchase serials.
- Be brief. Answer in the same language as the question.
"""


def _clean(s, limit, keep_newlines=True):
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", s)
    out = "".join(c for c in s if (c in "\n\t" and keep_newlines) or (ord(c) >= 32 and ord(c) != 127
                                                                        and not 0x80 <= ord(c) < 0xa0))
    return out.strip()[:limit]


def _clean_env():
    drop = {"TMUX", "TMUX_PANE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"}
    return {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("AIB_SEAT")}


def _light(brand):
    try:
        import power_login
        return power_login.light_of(brand).get("light")
    except Exception:  # noqa: BLE001
        return "unknown"


def suggest(rank=None):
    """3 個建議問題代碼。rank = why_rank;None ⇒ 讀艦員檔案。"""
    if rank is None:
        try:
            import power_crew
            rank = power_crew.why()["why_rank"]
        except Exception:  # noqa: BLE001
            rank = []
    out = [SUGGEST[k] for k in rank if k in SUGGEST]
    out += [k for k in SUGGEST_BASE if k not in out]
    return out[:3]


def parse_input(raw):
    """stdin ⇒ (question, history)。JSON 物件就拆欄位,其他當純文字。"""
    raw = raw if isinstance(raw, str) else ""
    q, hist = raw, []
    if raw.lstrip().startswith("{"):
        try:
            d = json.loads(raw)
        except ValueError:
            d = None
        if isinstance(d, dict):
            q = d.get("question")
            for h in d.get("history") or [] if isinstance(d.get("history"), list) else []:
                if isinstance(h, dict) and isinstance(h.get("q"), str) and isinstance(h.get("a"), str):
                    hist.append({"q": _clean(h["q"], Q_MAX), "a": _clean(h["a"], A_MAX)})
    return _clean(q, Q_MAX + 1), hist[-HISTORY_MAX:]


def build_prompt(question, history):
    parts = [PREAMBLE]
    for h in history:
        parts.append("Earlier question:\n%s\nEarlier answer:\n%s\n" % (h["q"], h["a"]))
    parts.append("Question:\n%s\n" % question)
    return "\n".join(parts)


def extract(answer):
    """回答裡的 aib 指令(給頁面複製,不執行)與任務卡編號。"""
    cmds, cards = [], []
    for line in answer.splitlines():
        m = _CMD_RE.match(line.strip().strip("`"))
        if m and m.group(1) not in cmds:
            cmds.append(m.group(1))
    for m in _TASK_RE.finditer(answer):
        if m.group(1) not in cards:
            cards.append(m.group(1))
    return cmds[:CMDS_MAX], cards


def _receipt(ok, detail, brand=None, answer=None, hint=None):
    cmds, cards = extract(answer) if answer else ([], [])
    return {"ok": ok, "detail": detail, "brand": brand, "answer": answer, "cmds": cmds, "cards": cards, "hint": hint}


def ask(brand, raw, model=None, *, run=None, light=None, which=shutil.which, timeout=TIMEOUT_S):
    """回 (回條, rc)。rc 0 ok;2 參數錯 / 不支援 / 沒登入;1 叫了但失敗。"""
    if brand not in BRANDS:
        return _receipt(False, "power_ask_unsupported", hint="claude / codex"), 2
    if model is not None and not MODEL_RE.match(model):
        return _receipt(False, "power_ask_bad", brand, hint="model"), 2
    question, history = parse_input(raw)
    if not question or len(question) > Q_MAX:
        return _receipt(False, "power_ask_bad", brand, hint="question"), 2
    lt = (light or _light)(brand)
    if lt != "green":
        return _receipt(False, "power_ask_login", brand, hint="aib power login %s" % brand), 2
    if run is None and not which(brand):
        return _receipt(False, "power_ask_failed", brand, hint="not_installed"), 1
    prompt = build_prompt(question, history)
    cwd = tempfile.mkdtemp(prefix="aib-power-ask-")
    try:
        if brand == "claude":
            cmd = CLAUDE_CMD + (["--model", model] if model else [])
            out_file = None
        else:
            out_file = os.path.join(cwd, "last.txt")
            cmd = CODEX_CMD + (["-m", model] if model else []) + ["-o", out_file, "-"]
        try:
            r = (run or subprocess.run)(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                                        cwd=cwd, env=_clean_env())
        except subprocess.TimeoutExpired:
            return _receipt(False, "power_ask_failed", brand, hint="timeout"), 1
        except OSError:
            return _receipt(False, "power_ask_failed", brand, hint="not_installed"), 1
        text = r.stdout or ""
        if out_file:
            try:
                with open(out_file, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                pass
        answer = _clean(text, A_MAX)
        if r.returncode != 0 or not answer:
            return _receipt(False, "power_ask_failed", brand, hint="rc=%d" % r.returncode), 1
        return _receipt(True, "power_ask_ok", brand, answer), 0
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib power ask")
    p.add_argument("brand", nargs="?")
    p.add_argument("--model")
    p.add_argument("--suggest", action="store_true")
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv[1:] if argv and argv[0] == "ask" else argv)
    except SystemExit:
        return 2
    if a.suggest and not a.brand:
        r, rc = {"ok": True, "detail": "power_ask_suggest", "suggest": suggest()}, 0
    elif a.brand and not a.suggest:
        try:
            raw = sys.stdin.read(64 * 1024)
        except (OSError, ValueError):
            raw = ""
        r, rc = ask(a.brand, raw, a.model)
    else:
        r, rc = _receipt(False, "power_ask_bad", hint="brand"), 2
    print(json.dumps(r, ensure_ascii=False) if a.json else json.dumps(r, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
