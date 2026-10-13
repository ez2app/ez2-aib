#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""每一種 bot 橋接席能做什麼 —— 單一一張表(10-07 the maintainer 令「深度比對各 bot、一併優化」,root a seat)。

為什麼要有:sand_bridge 一支行程跑 grokbot / claudebot / musebot 三家,以前用 `if is_claude`、`provider == "musebot"`
一處一處分岔,新加的一家就會掉進別家的分支。10-07 比對抓到兩個實例:
  1. musebot 席上打字走 grokbot 的 webhook 門鈴(印「沒叫醒——wake 沒設定」,與 musebot 自己的「不需要 webhook」矛盾);
  2. 綁了 Muse 對話的 musebot 席跑 grokbot 的收尾小幫手與「請 bot 在 app 裡改名」。
兩個都是 `agent and not is_claude` 這種「不是 A 就當成 B」的寫法。現在橋只問這張表。

成員從 providers.json 來(menu_group=="bot");表裡少一家或多一家 ⇒ tests/test-bot-caps-3f999-1007.py 紅。
「哪些 provider 是橋接席」那幾份散處名單由 tests/test-bot-provider-sites-987f7.py 守,這張表只管行為。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BotCaps:
    provider: str
    bridge: str                     # mailbox = sand_bridge 信箱橋;rpc = codexbot_bridge 直連 App Server
    bind_env: str = ""              # 這一席綁哪條對話的環境變數;空 = 不綁
    threads_module: str = ""        # 鎖檔、held_by 的模組(綁定一條對話只准一隻席)
    bind_what: str = "對話"          # 人話:綁的是什麼
    wake: str = "none"              # webhook = 席上打字就按門鈴;hook = 對方的 hook 自己盯信箱;schedule = 對方排程來拉;rpc;none
    rename_in_app: bool = False     # 綁上後請 bot 把 app 裡的名稱改成帶席碼(收尾小幫手關席時改回)
    unbound_is_fault: bool = False  # 沒綁 = 收不到也送不出 ⇒ 右欄 ⚠沒綁對話
    mail_without_bind: bool = False  # 沒綁也照發通行碼、照送人格包(信箱照常運作)
    bind_in_seat: bool = False      # 席上 /bind 就地綁
    live_status: bool = False       # 狀態列、已排隊/已讀(claudebot_live)
    outbox_drainer: bool = False    # bot 沒有 shell,dm 寫進 outbox 由橋代送
    reply_from: str = ""            # 開席橫幅「回覆來自」那一句
    read_marks_stale_s: float = 0.0  # >0 ⇒ 席上「✓ 已讀走」;信等這麼久沒人收 ⇒ ⚠沒來收信(bridge_readmarks)


CAPS = {
    "sand": BotCaps("sand", "mailbox", wake="webhook",
                    reply_from="回覆來自：Cursor 助手寫 outbox（預設）；或 EZ2_BRIDGE_LOCAL_REPLY=1 本地 CLI。"),
    "grokbot": BotCaps("grokbot", "mailbox", bind_env="AIB_GROKBOT_AGENT_ID", threads_module="grokbot_threads",
                       bind_what="agent", wake="webhook", rename_in_app=True, unbound_is_fault=True,
                       bind_in_seat=True,          # 10-11 a seat(P1):席上 /bind、/unbind,不用重開
                       read_marks_stale_s=300.0,   # 門鈴幾秒就該叫醒;5 分鐘還沒收 = 門鈴或 bot 有事
                       reply_from="回覆來自：Cursor 助手寫 outbox（預設）；或 EZ2_BRIDGE_LOCAL_REPLY=1 本地 CLI。"),
    "claudebot": BotCaps("claudebot", "mailbox", bind_env="AIB_CLAUDEBOT_SESSION_ID", threads_module="claudebot_threads",
                         bind_what="Claude 對話", wake="hook", unbound_is_fault=True, bind_in_seat=True, live_status=True,
                         reply_from="回覆來自:綁定的 Claude 對話(Claude 的 hook 收信、回信)。"),
    "musebot": BotCaps("musebot", "mailbox", bind_env="AIB_MUSEBOT_CONVERSATION_ID", threads_module="musebot_threads",
                       bind_what="Muse 對話", wake="schedule", mail_without_bind=True, outbox_drainer=True,
                       bind_in_seat=True,          # 10-11 a seat(P1)
                       read_marks_stale_s=900.0,   # 排程約 3 分鐘一次;15 分鐘(約 5 輪)還沒收 = 排程停了
                       reply_from="回覆來自:綁定的 Muse 對話(Meta Muse 經檔案信箱收信、回信;排程喚醒)。"),
    "codexbot": BotCaps("codexbot", "rpc", bind_env="AIB_CODEXBOT_THREAD_ID", threads_module="codexbot_threads",
                        bind_what="Codex thread", wake="rpc", unbound_is_fault=True,
                        bind_in_seat=True,          # 10-11 a seat(P1b):席上 /bind 換綁(記下選擇、同一席自動重開;不能只 /unbind)
                        reply_from="回覆來自:Codex 桌面 App Server(直接開 turn,沒有信箱)。"),
}


def caps(provider: str) -> BotCaps:
    """沒登記的 provider ⇒ 什麼能力都沒有的信箱橋(不猜成任何一家)。"""
    return CAPS.get(provider) or BotCaps(provider or "", "mailbox")


def registry_bots(path: Path | None = None) -> set[str]:
    """providers.json 裡 menu_group=="bot" 的 id。"""
    d = json.loads((path or HERE / "providers.json").read_text(encoding="utf-8"))
    ps = d.get("providers", d)
    items = ps.items() if isinstance(ps, dict) else ((p.get("id"), p) for p in ps)
    return {k for k, v in items if isinstance(v, dict) and v.get("menu_group") == "bot"}


def mailbox_bots() -> tuple[str, ...]:
    return tuple(k for k, c in CAPS.items() if c.bridge == "mailbox" and k != "sand")


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["is-bot"] and len(sys.argv) == 3:
        # 10-09 a seat:右鍵開席(mouse-ctl new-ai-open)問「這家是不是橋接 bot」—— 從 providers.json 問,不在殼裡再抄一份名單
        sys.exit(0 if sys.argv[2] in registry_bots() else 1)
    for k, c in CAPS.items():
        print(k, c)
