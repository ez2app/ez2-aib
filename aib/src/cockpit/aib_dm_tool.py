# SPDX-License-Identifier: Apache-2.0
"""Shared native `aib dm --verify` for API-key seats and *bot bridges.

Only this runner may claim a DM was attempted. Plain text, `/dm` drafts in model
prose, and XML examples never send — except human slash `/dm` on bridge REPLs,
which still calls this same runner.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

# the maintainer 09-28「aib dm 一律要含艙位碼」「舊的停用」:<艙位碼>/<席碼>,跨主機 <IP>/<艙位碼>/<席碼>(拆法正本 dm_address.py)。
DM_ADDRESS = re.compile(r"((?:[0-9]{1,3}(?:\.[0-9]{1,3}){3}/)?[0-9a-z]{1,4}/@?[0-9a-z]{4,8})$")
DM_BARE = re.compile(r"@?([0-9a-z]{4,8})$")   # 舊寫法:只有席碼 ⇒ 拒並教正確寫法
DM_TOOL_NAME = "aib_dm"  # stable machine id — not i18n'd

# English/zh fallbacks if i18n dict is missing (keep tool id English).
_FALLBACK_TOOL_DESCRIPTION = (
    "Send a short AIB direct message to another seat. Call this only when "
    "the user explicitly asks to send a message."
)
_FALLBACK_PARAM_TARGET = (
    "Explicit recipient seat address, always <cabin>/<seat> (e.g. ab12/1857a; "
    "another host: <IP>/<cabin>/<seat>). Never assume a previous seat's address."
)
_FALLBACK_PARAM_MESSAGE = "Message body to send."
_FALLBACK_SYSTEM_SNIPPET = (
    "唯一合法私訊路徑（開席即生效，不要自己找別的指令）："
    "呼叫本機 function tool `aib_dm`（參數 target + message）。"
    "該工具只會執行原始 `aib dm <艙位碼>/<席碼> <訊息> --verify`；"
    "人類在 REPL 打的 `/dm <艙位碼/席碼> <訊>` 也走同一 runner。"
    "Plain text、自己發明的 XML、草稿、舊收據、口頭「已送出」一律不算送達。"
    "只有本機 aib_dm 工具回傳後才能說結果；不要猜路徑、不要重造 CLI。"
    "Call aib_dm only when the user explicitly asks to send a DM."
)
_FALLBACK_BOOT_BANNER = (
    "【AIB DM】唯一路徑：工具 aib_dm 或人工 /dm <艙位碼/席碼> <訊> → 皆跑 `aib dm --verify`。"
    "散文／XML／假收據不算送達。"
)


def _dm_t(key: str, fallback: str, locale=None) -> str:
    """Lookup i18n/<locale>.json via EZ2_AIB_LOCALE; never fail loud into !!key!!."""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale(locale)
        text = i18n_lib.t(key, locale=loc)
    except Exception:
        return fallback
    if not isinstance(text, str) or not text.strip():
        return fallback
    if text == key or (text.startswith("!!") and text.endswith("!!")):
        return fallback
    return text


_FALLBACK_HANDOFF_RULE = (
    "At 85% context (or when the maintainer says prepare to clear) you must do the three-step aib handoff wrap-up; "
    "it cannot be skipped. If you cannot run commands, write the three sections in your reply and ask the "
    "human to run aib handoff write."
)


def handoff_rule_line(locale=None) -> str:
    """10-01 the maintainer: the mandatory wrap-up rule every API seat carries in its system prompt (aib handoff)."""
    return _dm_t("aib.handoff.rule_line_api", _FALLBACK_HANDOFF_RULE, locale)


def dm_system_snippet(locale=None) -> str:
    """Locale-aware system reminder; tool id inside always stays `aib_dm`.
    10-01: the handoff rule rides on this snippet because every API seat's system prompt already carries it."""
    return _dm_t("aib.dm.system_snippet", _FALLBACK_SYSTEM_SNIPPET, locale) + " " + handoff_rule_line(locale)


def dm_boot_banner(locale=None) -> str:
    """Human-visible one-liner when an API-key / *bot REPL starts."""
    return _dm_t("aib.dm.boot_banner", _FALLBACK_BOOT_BANNER, locale)


def dm_tool(locale=None) -> dict:
    """Function-tool schema. Name is always aib_dm; descriptions follow locale."""
    return {
        "type": "function",
        "function": {
            "name": DM_TOOL_NAME,
            "description": _dm_t(
                "aib.dm.tool_description", _FALLBACK_TOOL_DESCRIPTION, locale
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": _dm_t(
                            "aib.dm.param_target", _FALLBACK_PARAM_TARGET, locale
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": _dm_t(
                            "aib.dm.param_message", _FALLBACK_PARAM_MESSAGE, locale
                        ),
                    },
                },
                "required": ["target", "message"],
                "additionalProperties": False,
            },
        },
    }


# Compat names. 10-14 語系盤查第 6 條:原本 import 時就照當時語系組好 ⇒ 長駐的 bot / bridge 之後切語系不會跟。
#   改成取用時才照當下語系組(PEP 562 模組 __getattr__;from aib_dm_tool import DM_TOOL 也走這裡)。
_LAZY = {"DM_SYSTEM_SNIPPET": dm_system_snippet, "DM_BOOT_BANNER": dm_boot_banner, "DM_TOOL": dm_tool}


def __getattr__(name):
    fn = _LAZY.get(name)
    if fn is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return fn()

# the maintainer 2026-09-28 令「首要基因」(v0.69.2 起六支 API 席都帶);09-28 再定:**要能開關、預設關**。
#   正本 docs/Ez2-GENES.md;程式 ez2_genes.py(開法 AIB_GENES=1 或 ~/.ez2-aib/genes.json;開任何一條時 3、5 一定跟著開)。
#   PRIMARY_GENE = 全部基因的文字(與正本逐字相同);GENE_HEAD = 這次真的放在系統提示最前面的那段(關 ⇒ 空字串)。
#   放在這裡是因為六支都已 import 本模組。
try:
    from ez2_genes import GENE_PROMPT as PRIMARY_GENE, GENE_HEAD, suggestion as _gene_suggestion  # noqa: E402
except ImportError:  # a partial install without ez2_genes.py: no genes rather than no seat
    PRIMARY_GENE, GENE_HEAD = "", ""

    def _gene_suggestion():
        return ""


def gene_banner() -> str:
    """Start-up banner line: genes on/off, and when off the measured gain of turning them on."""
    line = _gene_suggestion()
    return ("\n" + line) if line else ""

# 09-28 P1 r3:dm 停用後換上的系統提示尾句。停用後的系統提示**不得**再出現 <aib_tool> aib_dm 範本或「呼叫 aib_dm」的說明
#   (原本那句教模型「不能用工具時寫 <aib_tool> aib_dm」,工具收回後模型就照做)。
DM_DISABLED_NOTE = ("aib_dm is disabled for the rest of this turn because it failed repeatedly. "
                    "Do not try to send any DM in any form; answer the user in plain text.")

TEXT_TOOL = re.compile(r"^\s*<aib_tool>(\{.*\})</aib_tool>\s*$", re.DOTALL)
UNVERIFIED_DM_CLAIM = re.compile(
    r"(?:已送出|送出成功|stage\s*[:=]\s*delivered|"
    r"(?:aib[_-]?dm|DM|私訊).{0,30}(?:結果|成功|delivered|rc\s*[:=]\s*0))",
    re.IGNORECASE | re.DOTALL,
)
# Human slash on bridge REPLs: /dm <target> <message>
DM_SLASH = re.compile(
    r"^/dm\s+((?:[0-9.]+/)?(?:[0-9a-z]{1,4}/)?@?[0-9a-z]{4,8})\s+(.+)$",   # 舊寫法照認出來,交 run_aib_dm 拒並教新寫法
    re.IGNORECASE | re.DOTALL,
)

# Aliases used by older deepseek / docs wording
DM_ADDRESS = DM_ADDRESS
UNVERIFIED_DM_CLAIM = UNVERIFIED_DM_CLAIM
DM_SLASH = DM_SLASH


def aib_bin() -> Path:
    return Path(__file__).resolve().parent / "aib"


def hub_env(base: dict | None = None) -> dict:
    """Copy env and inherit EZ2_AIHUB_SOCKET from TMUX when missing."""
    child = dict(base or os.environ)
    if not child.get("EZ2_AIHUB_SOCKET"):
        tmux_socket = (child.get("TMUX") or "").split(",", 1)[0]
        socket_name = Path(tmux_socket).name if tmux_socket else ""
        if socket_name.startswith("aihub-"):
            child["EZ2_AIHUB_SOCKET"] = socket_name
            child.setdefault(
                "EZ2_AIB_INSTANCE_HOME",
                str(
                    Path.home()
                    / ".ez2-aib"
                    / "instances"
                    / socket_name.removeprefix("aihub-")
                ),
            )
    return child


# 呼叫端遞來的「我是哪一段 codex 對話」——**證據不是身份**:這裡只檢查它長得像 uuid,
# 真正的判定在 dm-ctl(拿去比對席位登記簿,對得上唯一一個活席才推得出五碼)。
# 這條路存在的理由:MCP 伺服器掛在 codex 常駐服務底下(父程是 init),
# 行程祖先鏈**結構上**永遠回不到席的 pane,不給它證據就只能永遠不具名。
_CLAIM_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def run_aib_dm(target: str, message: str, claim_session_uuid: str = "") -> str:
    """Run the one permitted AIB DM path without passing model text through a shell."""
    match = DM_ADDRESS.fullmatch((target or "").strip())
    body = (message or "").strip()
    # 2026-09-20: DM 正文不得夾 C0(會被终端當 Ctrl 鍵殺席)
    import importlib.util as _ilu
    _dsa = Path(__file__).resolve().parent / "dangerous_seat_actions.py"
    _spec = _ilu.spec_from_file_location("dangerous_seat_actions", _dsa)
    _mod = _ilu.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    if _mod.guard_dm_body(body) != 0:
        return "DM 未送出：訊息含控制字元(C0)，已硬擋以免殺席。"
    if not match:
        if DM_BARE.fullmatch((target or "").strip()):
            return ("DM 未送出：地址要寫 艙位碼/席碼(例 ab12/1857a),只寫席碼的舊寫法已停用。"
                    "對方的艙位碼在它 dm 抬頭 IP:位址/艙位碼/名@席碼 裡;不知道就直接用文字回答對方。")
        return "DM 未送出：地址必須是 艙位碼/席碼(例 ab12/1857a;跨主機 IP/艙位碼/席碼)。"
    if not body:
        return "DM 未送出：訊息不可為空白。"
    # §114 S4(09-27 23:2x NVD a seat 實測:小模型照抄提示範例 @xxxx/@yyyy 連打 6 次,每次真跑 aib dm 幾秒):
    #   先只解析目標、不碰任何輸入框;解析不到就立刻擋回,告訴模型不要猜席碼。
    try:
        probe = subprocess.run(
            ["bash", str(aib_bin()), "dm", match.group(1), "--resolve-only"],
            text=True, capture_output=True, timeout=15, check=False, env=hub_env(), stdin=subprocess.DEVNULL)
        resolved = '"canonical_target"' in (probe.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        resolved = True          # 解析器量不到 ⇒ 交給正式投遞去判(量不到不擋)
    if not resolved:
        return ("DM 未送出：%s 不是現在活著的席。不要猜或照抄範例席碼;沒有人給你確定的席碼,就直接用文字回答對方。"
                % match.group(1))
    argv = ["bash", str(aib_bin()), "dm", match.group(1), body, "--verify"]
    claim = (claim_session_uuid or "").strip()
    if claim and _CLAIM_UUID_RE.fullmatch(claim):
        argv += ["--codex-session-uuid", claim]
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
            env=hub_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return "DM 未送出：本機 AIB 工具無法執行(%s)。" % err
    detail = (result.stdout + result.stderr).strip()
    detail = detail[-2000:] if detail else "沒有回傳內容"
    return "DM 指令 rc=%d：%s" % (result.returncode, detail)


def enforce_dm_truth(content: str, dm_outcomes) -> str:
    """Prevent unexecuted model prose from masquerading as a DM receipt."""
    content = content or ""
    outcomes = [str(item) for item in (dm_outcomes or []) if item]
    if outcomes:
        return content + "\n[AIB 本機 DM 收據]\n" + "\n".join(outcomes)
    if UNVERIFIED_DM_CLAIM.search(content):
        return (
            "未執行 DM：本回合沒有收到可執行的 aib_dm 工具呼叫，不能宣稱已送出。\n"
            "模型原文（未驗證）：\n" + content
        )
    return content


def text_tool_call(content: str):
    """Decode `<aib_tool>{...}</aib_tool>` fallback for models that skip API tools."""
    match = TEXT_TOOL.fullmatch(content or "")
    if not match:
        return None
    try:
        call = json.loads(match.group(1))
    except ValueError:
        return None
    return call if isinstance(call, dict) else None


def parse_dm_slash(line: str):
    """Human `/dm <target> <message>` for bridge REPLs. Returns (target, msg) or None."""
    match = DM_SLASH.fullmatch((line or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def handle_dm_tool_call(name: str, args) -> str | None:
    """If name is aib_dm, run it; else return None so caller handles other tools."""
    if name != DM_TOOL_NAME:
        return None
    if not isinstance(args, dict):
        try:
            args = json.loads(args or "{}")
        except (TypeError, ValueError):
            args = {}
    # `_claim_session_uuid` 只由本機 MCP 伺服器從 codex 的 turn metadata 填,呼叫端(模型)
    # 自己塞進 args 的值會被伺服器覆寫;即使漏到這裡,dm-ctl 那一關仍要對得上活席才算數。
    return run_aib_dm(args.get("target", ""), args.get("message", ""),
                      claim_session_uuid=args.get("_claim_session_uuid", ""))


# Compatibility aliases (deepseek / older patches)
run_aib_dm = run_aib_dm
enforce_dm_truth = enforce_dm_truth
text_tool_call = text_tool_call
parse_dm_slash = parse_dm_slash
handle_dm_tool_call = handle_dm_tool_call
