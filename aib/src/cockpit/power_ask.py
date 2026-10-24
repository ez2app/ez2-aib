#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室「問 AI」抽屜的核心(契約 contracts/power.json ask_receipt;SPEC-power-room §12)。

  aib power ask <家> [--model M] [--json]     問題從 stdin 讀:純文字,或 {"question": "...", "history": [{"q","a"}…]}
  aib power ask --suggest [--json]            建議問題 3 則(代碼;照「為什麼用 AIB」排名換)

10-24 Andy:「要有一個點擊他選的 AI 在這個視窗中，即時對話用來 Q&A」+ @abb89 硬線「只回答、不動手」。
- 只叫得動能驗證關工具的家:claude(--tools "" + --strict-mcp-config 不載 MCP + 關 skill / 指令)、
  codex(唯讀沙盒 + 關外掛 / apps / shell / 電腦與瀏覽器操作 / 看圖生圖 / 多代理 / hook / 網路搜尋 + 逐個停用 MCP、重列 MCP 與 features 確認 + 不留對話)。其他家 ⇒ power_ask_unsupported(驗不了就不叫,同帳號 AI 威脅守則)。
- 那家不是綠燈 ⇒ power_ask_login,頁面導去登入;不偷換別家。判燈與實際問用同一份環境(不帶 API key,只走訂閱登入)。
- 問題走 stdin(ps 看不到),跑在空的暫存目錄、跑完刪;不寫進任何席位、不存對話。
- 回答裡的指令只抽出來給頁面「複製」,核心不執行;回答去控制字元、有長度上限。
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TIMEOUT_S = 90
Q_MAX = 2000
A_MAX = 8000
HISTORY_MAX = 6
CMDS_MAX = 5
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,95}$")
_CMD_RE = re.compile(r"\s*(?:\$\s*)?(aib(?: [A-Za-z0-9_./:=,@-]+){1,12})\s*")
_TASK_RE = re.compile(r"\b(UR[1-7]|SSR(?:1[0-2]|[1-9])|SR(?:1[0-3]|[1-9])|R(?:1[0-2]|[1-9]))\b")

# 關工具的呼叫法;改這裡要先過同帳號 AI 威脅守則的檢查表
# --settings disableAllHooks:使用者 / aib 裝的 hook 不跑、也收不到問題全文(10-25 第 3 輪稽核 F5)
CLAUDE_CMD = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--tools", "",
              "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "1",
              "--settings", '{"disableAllHooks":true}']
CODEX_CMD = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "--ephemeral", "--color", "never"]
# 10-24 03:2x 實跑發現 -c mcp_servers={} 是合併不是取代(設定檔的 node_repl 等照開)⇒ 改成:關外掛 / apps / shell /
# 電腦與瀏覽器操作,再列 MCP、逐個停用、重列確認全關;驗不過就不叫(power_ask_unsupported hint mcp_not_disabled)
# 10-25 第 3 輪稽核 F2:codex 0.164 預設還開著看圖 / 生圖 / 多代理 / hook / 外部瀏覽器 / skill 搜尋…、網路搜尋也沒關
# ⇒ 一併關;叫之前再用 features list 重列確認(使用者 profile 蓋回來 ⇒ 不叫)。不認得的 features 鍵 codex 照收,舊版不會壞
LOCK_FEATURES = ("plugins", "apps", "shell_tool", "computer_use", "browser_use", "browser_use_external", "in_app_browser",
                 "in_app_local_automation", "view_image", "image_generation", "multi_agent", "hooks", "code_mode_host",
                 "skill_search", "skill_mcp_dependency_install", "tool_suggest", "sleep_tool", "goals")
CODEX_LOCK = [x for k in LOCK_FEATURES for x in ("-c", "features.%s=false" % k)] + ["-c", "web_search=disabled"]
_MCP_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MCP_LIST_TIMEOUT_S = 30
BRANDS = ("claude", "codex")

# 建議問題(代碼;字在頁面 i18n)。照「為什麼用 AIB」排名挑對應的,不夠再補通用的
SUGGEST = {k: "q_" + k for k in ("memory", "multi_ai", "coding", "automation", "ecommerce", "geo_seo", "support",
                                  "video", "erp", "warroom", "phone", "sites", "art", "room", "save")}
SUGGEST_BASE = ("q_first_seat", "q_lights", "q_handoff")

PREAMBLE = """You are the help desk inside the aib Power Room. aib is a command-line cockpit that runs several AI \
coding seats (Claude, Codex, Grok, Copilot, Antigravity) side by side, with a captain-room game view.
Rules:
- Only answer. You cannot run commands, read or change files, or press any buttons; never claim you did.
- If something needs doing, tell the user how to do it themselves. Put each command on its own line starting with `aib`.
- If a beginner task card helps, mention its id (R1-R12, SR1-SR13, SSR1-SSR12, UR1-UR7).
- Never ask for or repeat passwords, keys, tokens or purchase serials.
- Be brief. Answer in the same language as the question.
Common commands (from the beginner curriculum; do not invent others, say "see aib --help" when unsure):
- open a seat: aib <brand> safe <role>   e.g. aib claude safe dev   (brand = claude / codex / grok / copilot / agy)
- open a new cabin: aib <brand> safe <role> new
- bind a persona: aib persona   ·   second-brain project: aib newproj   ·   write memory: aib memory write
- status line: aib now "<main item>" 1/3   ·   message a seat: aib dm <cabin>/<seat> "<text>"
- end-of-shift handoff: aib handoff   ·   health check: aib doctor   ·   tests: aib test
- log in an AI: aib power login <brand>   ·   upgrade aib: aib update
"""


# 不是 Cf 但看不見的字:韓文填充字、組合字素連接符;行 / 段分隔(Zl / Zp)也一律去掉(#302 審查)
# 變體選擇符(U+FE00–FE0F、U+E0100–E01EF、蒙古文 U+180B–180F)與點字空白 U+2800 也算(第 3 輪稽核 F4:可藏資料)
INVISIBLE_EXTRA = frozenset((0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800) + tuple(range(0xFE00, 0xFE10))
                            + tuple(range(0x180B, 0x1810)) + tuple(range(0xE0100, 0xE01F0)))


def invisible(c):
    return unicodedata.category(c) in ("Cf", "Cs", "Zl", "Zp") or ord(c) in INVISIBLE_EXTRA


def _clean(s, limit, keep_newlines=True):
    """去 ANSI、C0 / C1 控制字元、Unicode 格式字元(方向控制 U+202E / U+2066、零寬)與單獨代理字元。"""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", s)
    out = "".join(c for c in s if (c in "\n\t" and keep_newlines) or (ord(c) >= 32 and ord(c) != 127
                                                                        and not 0x80 <= ord(c) < 0xa0
                                                                        and not invisible(c)))
    return out.strip()[:limit]


_ENV_DROP_PREFIX = ("TMUX", "CLAUDECODE", "CLAUDE_CODE_", "AIB_SEAT")
# 問 AI 只准走本人的訂閱登入，不准悄悄改走 API 計費(花錢一律本人決定;10-25 @abb89 裁 #342 F1)
_ENV_DROP_NAMES = frozenset(("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
                             "OPENAI_BASE_URL", "CODEX_API_KEY"))


def _clean_env():
    """子 CLI 不帶席位 / tmux / 父 Claude 工作階段的環境(訊息 token、socket、埠):不給它回連父席的路。"""
    return {k: v for k, v in os.environ.items() if not k.startswith(_ENV_DROP_PREFIX) and k not in _ENV_DROP_NAMES}


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(5)
    except (OSError, subprocess.SubprocessError):
        pass


def run_group(cmd, *, input=None, timeout=None, capture_output=False, **kw):
    """subprocess.run 的替身：子 CLI 自成一組，結束 / 逾時 / 被中斷都把整組砍掉(npm 版 codex 是 node 包原生檔，
    只砍直接子行程會留孤兒;第 3 輪稽核 F3)。"""
    if capture_output:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    p = subprocess.Popen(cmd, start_new_session=True, **kw)
    try:
        out, err = p.communicate(input, timeout=timeout)
    finally:
        _kill_group(p)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _probe_run(cmd):
    """power_login 的登入狀態指令，但用跟實際問一樣的 _clean_env(#342 F1:判燈帶完整環境 ⇒ 綠燈卻叫失敗)。"""
    try:
        import power_login
        r = run_group(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                      timeout=power_login.STATUS_TIMEOUT_S, env=_clean_env())
        return r.returncode, r.stdout or ""
    except (OSError, subprocess.SubprocessError, ImportError):
        return None, ""


def _light(brand):
    """跟 power_login.light_of 同順序(沒登入 yellow、額度到上限 red、登入著 green、量不到 unknown),但每次用
    _clean_env 現量、不讀也不寫共用的燈號快取(頁面燈號照舊用完整環境的快取)。"""
    try:
        import power_login
        li = power_login.PROBES[brand](_probe_run)
        if li is False:
            return "yellow"
        if power_login.quota(brand)[0]:
            return "red"
        return "green" if li is True else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _mcp_enabled(flags, run):
    """codex <flags> mcp list --json ⇒ 還開著的伺服器名稱;讀不懂 ⇒ None。"""
    try:
        r = run(["codex"] + flags + ["mcp", "list", "--json"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=MCP_LIST_TIMEOUT_S, stdin=subprocess.DEVNULL, env=_clean_env())
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        return None
    return [x.get("name") for x in rows if x.get("enabled") is not False]


def _features_off(flags, run):
    """codex <flags> features list ⇒ LOCK_FEATURES 全是 false 才 True;讀不懂 / 少一個 ⇒ False。"""
    try:
        r = run(["codex"] + flags + ["features", "list"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=MCP_LIST_TIMEOUT_S, stdin=subprocess.DEVNULL, env=_clean_env())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    if r.returncode != 0 or not isinstance(r.stdout, str):
        return False
    seen = {}
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 2:
            seen[f[0]] = f[-1]
    return all(seen.get(k) == "false" for k in LOCK_FEATURES)


def codex_lockdown(run):
    """回 codex 要加的關工具旗標;關不乾淨(還有 MCP 開著 / 名稱怪 / 讀不懂)⇒ None。"""
    on = _mcp_enabled(CODEX_LOCK, run)
    if on is None or not all(isinstance(n, str) and _MCP_NAME_RE.fullmatch(n) for n in on):
        return None
    # codex 的 -c 照「.」直接切、不認 TOML 引號(加引號會變成另一台叫 "x" 的伺服器)⇒ 名稱只收 [A-Za-z0-9_-]、不加引號
    flags = CODEX_LOCK + [x for n in on for x in ("-c", "mcp_servers.%s.enabled=false" % n)]
    return flags if _mcp_enabled(flags, run) == [] and _features_off(flags, run) else None


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
        m = _CMD_RE.fullmatch(line.strip().strip("`"))
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
    if model is not None and not (isinstance(model, str) and MODEL_RE.fullmatch(model)):
        return _receipt(False, "power_ask_bad", brand, hint="model"), 2
    question, history = parse_input(raw)
    if not question or len(question) > Q_MAX:
        return _receipt(False, "power_ask_bad", brand, hint="question"), 2
    try:
        lt = (light or _light)(brand)
    except Exception:  # noqa: BLE001
        lt = "unknown"
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
            lock = codex_lockdown(run or run_group)
            if lock is None:
                return _receipt(False, "power_ask_unsupported", brand, hint="mcp_not_disabled"), 2
            out_file = os.path.join(cwd, "last.txt")
            cmd = CODEX_CMD + lock + (["-m", model] if model else []) + ["-o", out_file, "-"]
        try:
            r = (run or run_group)(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                          errors="replace", timeout=timeout, cwd=cwd, env=_clean_env())
        except subprocess.TimeoutExpired:
            return _receipt(False, "power_ask_failed", brand, hint="timeout"), 1
        except OSError:
            return _receipt(False, "power_ask_failed", brand, hint="not_installed"), 1
        except (ValueError, UnicodeError):
            return _receipt(False, "power_ask_failed", brand, hint="io"), 1
        text = r.stdout if isinstance(r.stdout, str) else ""
        if out_file:
            try:
                with open(out_file, encoding="utf-8", errors="replace") as f:
                    text = f.read(A_MAX * 4)
            except OSError:
                pass
        answer = _clean(text, A_MAX)
        if r.returncode != 0 or not answer:
            return _receipt(False, "power_ask_failed", brand, hint="rc=%d" % r.returncode), 1
        return _receipt(True, "power_ask_ok", brand, answer), 0
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def install_term_handler():
    """頁面關抽屜用 SIGTERM / SIGHUP 收 ⇒ 轉成 SystemExit,finally 才會砍子 CLI 整組、刪暫存目錄(codex 的答案檔)。"""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, lambda n, f: sys.exit(128 + n))
        except (ValueError, OSError):   # 不在主執行緒(被當模組叫)⇒ 不裝
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    install_term_handler()
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
