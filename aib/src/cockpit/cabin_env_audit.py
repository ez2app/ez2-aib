#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艙環境稽核:看一個**活著的艙**的 tmux 全域環境裡,有沒有它不該帶的東西。

## 為什麼要有這支(2026-09-25,the maintainer 令「先做尺,讓它看得見」)

實例:艙 `aihub-20260921-150346-d6a8` 的三個 Claude 席整個介面是黑白的。
追下去不是渲染錯,是那一艙的 tmux **伺服器**帶著 `NO_COLOR=1` / `FORCE_COLOR=0`
——開艙的人不是人,是另一席 AI 的工具殼,那個殼的「把顏色關掉」整包被繼承下來。
同一包裡還有 `GROK_SESSION_ID`(上一代 grok 會話的身份)與重複三次的 `PYTHONPATH`。

**而這件事沒有任何一格會叫。** 顏色沒了不紅、身份帶錯不紅——失效是靜的。
這支就是那一格:讓「艙裡多了不該有的東西」變成看得見、量得到、有結束碼的事。

## ★ 母體怎麼圈的(這是本檔最重要的設計決定)

`src/cockpit/identity-env.sh` 那份洗艙名單是**用名字列**的:08-09 開檔 39 個名字,
到今天 45 個,七週加了 6 個,每一個都是出事之後補的。而 grok 是 08-31 登記成
provider 的(`e5863fd3`,commit 訊息自稱「八處消費面」),那次改了 7 個檔,
`identity-env.sh` 不在裡面——於是 `GROK_SESSION_ID` 至今不在名單上。

⇒ 本檔**不抄第二份名單**。身份類的母體從 `providers.json`(登記簿單源)長出來:
   provider id 大寫當字首 × 身份字尾。**登記一個新 AI,這支自動認得它**——
   這正是名字黑名單做不到、而且結構上做不到的事。

## ★ 只對「說得出後果」的兩類判紅

- `identity`:別人的會話身份。後果具體——從環境讀 id 的消費者會讀到上一代的。
- `colour`:關色控制。後果具體——CLI 整個介面退成單色(本案)。
- `other`:其餘。**只報數字與名字,不判紅。**

第三類不判紅是刻意的:要判它得先有一份「允許出現什麼」的基準,而那份基準
在不同機器/不同終端機上長得不一樣(實測:同一台機器上人開的 tmux 伺服器
34 個全域變數、AI 殼開的 92 個,差集裡混著 macOS 給的與洩漏進來的)。
**一個會因為換終端機就變紅的閘,一週內會被關掉。**

## ★ 取不到 ≠ 乾淨

`read_global_env` 取不到就 raise,不回空 dict。這一族本庫踩過很多次:
空集合與「守住了」在報表上同形,而失效方向是綠的。
"""

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY = os.path.join(ROOT, "src", "cockpit", "providers.json")

# 身份字尾:一個變數名長這樣,就是在講「哪一個會話」。
# 逐條都對得上今天 identity-env.sh 裡真實存在的名字:
#   CODEX_THREAD_ID / CLAUDE_CODE_SESSION_ID / AGY_SESSION_UUID /
#   COPILOT_AGENT_SESSION_ID / GROK_AGENT(實測在本案洩漏的那一個)
IDENTITY_SUFFIXES = ("SESSION_ID", "SESSION_UUID", "THREAD_ID", "AGENT")

# 關色控制:這四個是跨 CLI 的標準旗標,被測物自己會讀。
# 09-25 實測 claude 2.1.281 的二進位檔內 NO_COLOR 出現 16 次、FORCE_COLOR 23 次、
# CLICOLOR_FORCE 2 次;兩臂實測(帶/不帶)同一畫面 SGR 色碼 32 → 134。
COLOUR_SUPPRESSORS = ("NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE")


class ProbeFailed(RuntimeError):
    """取不到就是取不到。**不得**退化成空集合——那會讓髒艙看起來乾淨。"""


def provider_ids(registry_path=REGISTRY):
    """登記簿單源 → provider id 集合。解不到就 raise,不回空集合。"""
    with open(registry_path, encoding="utf-8") as fh:
        reg = json.load(fh)
    ids = {p["id"] for p in reg.get("providers", []) if p.get("id")}
    if not ids:
        raise ProbeFailed("providers.json 解出空集合:先查解析,不要當成沒有 provider")
    return ids


def identity_pattern(ids):
    """由 provider id 生出身份變數的判準(純函式,不讀檔不讀環境)。"""
    heads = "|".join(sorted((re.escape(i.upper()) for i in ids), key=len, reverse=True))
    tails = "|".join(IDENTITY_SUFFIXES)
    return re.compile(r"^(?:%s)_(?:[A-Z0-9_]*_)?(?:%s)$" % (heads, tails))


def classify(env_names, ids):
    """純函式:一組變數名 → {identity, colour, other}。

    `ids` 由呼叫端注入(本函式不讀 providers.json)——同 render_lib 的紀律:
    可測的核心不讀檔、不讀環境、不讀時鐘。
    """
    pat = identity_pattern(ids)
    identity, colour, other = [], [], []
    for name in sorted(set(env_names)):
        if pat.match(name):
            identity.append(name)
        elif name in COLOUR_SUPPRESSORS:
            colour.append(name)
        else:
            other.append(name)
    return {"identity": identity, "colour": colour, "other": other}


def read_global_env(socket, tmux_bin="tmux"):
    """讀活艙的 tmux **全域**環境。唯讀動詞(show-environment),不碰別人的艙。

    ★ 一定帶 -L:不帶就是用繼承來的 socket,可能打到別套指揮艙
      (`src/policy/hooks/inherit-context-gate.py` 的第一條規則)。
    """
    cmd = [tmux_bin, "-L", socket, "show-environment", "-g"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeFailed("跑不起來 %s:%s" % (" ".join(cmd), exc))
    if p.returncode != 0:
        raise ProbeFailed(
            "tmux -L %s 取不到全域環境(rc=%d):%s——"
            "socket 可能是死的。**不要把這個當成乾淨**"
            % (socket, p.returncode, (p.stderr or "").strip()))
    names = []
    for line in p.stdout.splitlines():
        if not line or line.startswith("-"):     # `-NAME` = 這一格是被移除的
            continue
        if "=" in line:
            names.append(line.split("=", 1)[0])
    if not names:
        raise ProbeFailed(
            "tmux -L %s 回了 0 個變數——一個活著的伺服器不會是空的,先查取法" % socket)
    return names


def read_env_file(path):
    """離線輸入(治具、或 `tmux show-environment -g > 檔`)。同樣:空的就 raise。"""
    names = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("-") or "=" not in line:
                continue
            names.append(line.split("=", 1)[0])
    if not names:
        raise ProbeFailed("%s 解不出任何變數名:先查檔的格式,不要當成乾淨" % path)
    return names


def report(result, socket_label):
    """人話報表。★ 第一句就是「這艙現在乾不乾淨」。"""
    bad = len(result["identity"]) + len(result["colour"])
    out = []
    if bad == 0:
        out.append("艙 %s:乾淨(沒有別人的會話身份,也沒有關色旗標)" % socket_label)
    else:
        out.append("艙 %s:髒的——%d 個不該帶的變數" % (socket_label, bad))
    if result["identity"]:
        out.append("")
        out.append("  ▲ 別人的會話身份 %d 個:%s" % (
            len(result["identity"]), " ".join(result["identity"])))
        out.append("    後果:從環境讀這些 id 的消費者會讀到上一代的席,而不會報錯。")
    if result["colour"]:
        out.append("")
        out.append("  ▲ 關色旗標 %d 個:%s" % (
            len(result["colour"]), " ".join(result["colour"])))
        out.append("    後果:這艙開出來的 CLI 整個介面退成單色(logo、輸入框、預選字)。")
    out.append("")
    out.append("  · 其餘 %d 個變數只列不判(見檔頭:會因終端機而異的東西不判紅)" % len(result["other"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="艙環境稽核:活艙帶了什麼不該帶的")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--socket", help="tmux -L 的 socket 名(預設讀 EZ2_AIHUB_SOCKET)")
    src.add_argument("--env-file", help="離線輸入:show-environment -g 的輸出檔")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    try:
        ids = provider_ids(args.registry)
        if args.env_file:
            label, names = args.env_file, read_env_file(args.env_file)
        else:
            sock = args.socket or os.environ.get("EZ2_AIHUB_SOCKET") or ""
            if not sock:
                print("要 --socket 或 --env-file,或設 EZ2_AIHUB_SOCKET", file=sys.stderr)
                return 2
            label, names = sock, read_global_env(sock)
    except ProbeFailed as exc:
        print("稽核**沒有做成**(不是乾淨):%s" % exc, file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError) as exc:
        print("稽核**沒有做成**(不是乾淨):%s" % exc, file=sys.stderr)
        return 4

    result = classify(names, ids)
    if args.as_json:
        print(json.dumps(dict(result, socket=label), ensure_ascii=False, indent=1))
    else:
        print(report(result, label))
    return 3 if (result["identity"] or result["colour"]) else 0


if __name__ == "__main__":
    sys.exit(main())
