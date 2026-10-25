#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""各家 AI CLI 畫面樣式表(對話面板 C3 / C1 用;SPEC-chat-panel-formats v0.1 第 C3 節、v0.5)。

Claude Code 的對話紀錄檔**沒有**權限詢問的紀錄,所以「要不要允許」這種框只能從 tmux 畫面認;
每家 CLI 一組樣式,獨立放這一檔,改畫面只動這裡 + 真畫面 fixture(tests/fixtures/chat-panel/screens/)。

permission_ask(screen, provider) ⇒ {prompt, options[{key,label}], multi: False} 或 None
at_prompt(screen, provider) ⇒ 提示列是不是在等人打字(不在 less / vim / 權限框 / 思考中)
prompt_block(screen, provider) ⇒ 同上的原因版:(None, "") 或 (代碼, 擋下的那一行)
busy_state(screen, provider) ⇒ thinking | idle | unknown(只看畫面;有沒有 ask 由呼叫端合)
"""
import hashlib
import re

TAIL_LINES = 40

# ── Claude Code ──
_CLAUDE_ASK = re.compile(r"(Do you want to (?:proceed|make this edit|run this command|allow|create|delete|overwrite)[^\n]*\?|"
                         r"Allow (?:this|once|always)[^\n]*\?|Would you like to[^\n]*\?)")
_OPTION = re.compile(r"^\s*(?:❯\s*)?(\d)\.\s+(.+?)\s*$")
_CLAUDE_PROMPT = re.compile(r"^\s*(?:│\s*)?[❯>](?:\s|$)")   # 10-20 實機:提示列的 ❯ 後面是 NBSP(\xa0),用 \s 不用空格
_CLAUDE_PROMPT_EMPTY = re.compile(r"^\s*(?:│\s*)?[❯>]\s*(?:│\s*)?$")   # 提示列空的才准打字(有草稿 = 人正在打,10-20 實機 cc203)
_CLAUDE_THINKING = re.compile(r"[⠁⠂⠄⡀⢀⠠⠐⠈⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✻✽✶✳✢·]\s*\S*(?:ing|…)|esc to interrupt|Thinking…|Brewing|Working", re.I)
_PAGER = re.compile(r"^\(END\)$|^:$|^-- (?:INSERT|NORMAL|VISUAL) --|^~\s*$", re.M)
_CLAUDE_ESC_CANCEL = re.compile(r"Esc to cancel", re.I)

# ── Grok CLI(10-20 Pro 13 真畫面:底部「Enter:send │ Shift+Tab:mode │ Ctrl+x:shortcuts」)──
_GROK_PROMPT = re.compile(r"Enter:send|Shift\+Tab:mode")
_GROK_THINKING = re.compile(r"Thinking…|Worked for|⠧|⠇|Esc:cancel")

PROVIDERS = ("claude", "grok", "codex")


def tail(screen, n=TAIL_LINES):
    """最後 n 行;先去掉畫面底部的空行(capture-pane 會把 pane 底下的空行也給出來,10-20 實機:提示列被 12 行空行擠出視野)。"""
    lines = (screen or "").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:] if n else lines


def _strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s or "")


def permission_ask(screen, provider="claude"):
    """畫面最後幾行有權限框 ⇒ {prompt, options, multi};沒有 ⇒ None。只做 Claude Code;其他家回 None。"""
    if provider not in ("claude", None, ""):
        return None
    lines = [_strip_ansi(x) for x in tail(screen)]
    text = "\n".join(lines)
    m = _CLAUDE_ASK.search(text)
    if not m:
        return None
    opts = []
    for ln in lines[lines.index(next(l for l in lines if m.group(1) in l)):]:
        om = _OPTION.match(ln)
        if om and om.group(1) not in [o["key"] for o in opts]:
            opts.append({"key": om.group(1), "label": om.group(2).strip()})
    if not opts:
        return None
    return {"prompt": m.group(1).strip(), "options": opts, "multi": False}


def ask_id(ask):
    """同一題輪詢多次 id 不變;題目或選項變了 id 就變。"""
    raw = (ask.get("prompt", "") + "|" + "|".join(o["key"] + "=" + o["label"] for o in ask.get("options", []))).encode("utf-8")
    return "screen:" + hashlib.sha256(raw).hexdigest()[:8]


def busy_state(screen, provider="claude"):
    lines = [_strip_ansi(x) for x in tail(screen, 20)]
    text = "\n".join(lines)
    if provider == "grok":
        if _GROK_THINKING.search(text) and "Enter:send" not in text:
            return "thinking"
        return "idle" if _GROK_PROMPT.search(text) else "unknown"
    if _CLAUDE_THINKING.search(text):
        return "thinking"
    if any(_CLAUDE_PROMPT.match(l) for l in lines):
        return "idle"
    return "unknown"


def at_prompt(screen, provider="claude"):
    """C1 送鍵前的門:提示列在等人打字才算(10-20 @abb89 第六節:在 less / vim / 權限框 / 思考中一律拒)。"""
    return prompt_block(screen, provider)[0] is None


def prompt_block(screen, provider="claude"):
    """at_prompt 的原因版 ⇒ (None, "") 可以打;否則 (代碼, 擋下的那一行)。
    代碼:permission | pager | thinking | esc-cancel | draft(提示列有字)| no-prompt(找不到提示列)。
    10-25 Andy:席位明明停著、艦長室卻說「它正在忙」,被擋那次沒留紀錄查不出原因 ⇒ 呼叫端把代碼與這一行記下來。"""
    if permission_ask(screen, provider):
        return "permission", ""
    lines = [_strip_ansi(x) for x in tail(screen, 20)]
    text = "\n".join(lines)
    m = _PAGER.search(text)
    if m:
        return "pager", m.group(0)
    if provider == "grok":
        m = _GROK_THINKING.search(text.replace("Esc:cancel", ""))
        if m:
            return "thinking", _line_of(lines, m.group(0))
        return (None, "") if _GROK_PROMPT.search(text) else ("no-prompt", lines[-1] if lines else "")
    for code, rx in (("thinking", _CLAUDE_THINKING), ("esc-cancel", _CLAUDE_ESC_CANCEL)):
        m = rx.search(text)
        if m:
            return code, _line_of(lines, m.group(0))
    if any(_CLAUDE_PROMPT_EMPTY.match(l) for l in lines):
        return None, ""
    draft = next((l for l in reversed(lines) if _CLAUDE_PROMPT.match(l)), None)
    if draft is not None:   # 草稿是人正在打的字,不記內容只記字數
        body = re.sub(r"^\s*(?:│\s*)?[❯>]", "", draft).strip().rstrip("│").strip()
        return "draft", "❯ …(%d 字)" % len(body)
    return "no-prompt", lines[-1] if lines else ""


def _line_of(lines, hit):
    return next((l for l in lines if hit in l), hit)
