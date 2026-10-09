#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib bot ping @席碼 —— 一鍵檢查一隻 bot 橋接席通不通,卡在哪一段(10-07 the maintainer 令,root a seat)。

以前每一層只能分開驗:測試綠、status.json 對、右欄沒 ⚠。「席上說一句,AI 真的回來」沒有一個指令能證明。
這支照真流程走一次,逐段報:
  1 席活著     status.json 的 pid 在、心跳新鮮
  2 綁了對話   agent_id(musebot 沒綁也算正常)
  3 送進信箱   寫一封 human 來信(你本人在跑這個指令),照這一家的叫醒方式叫醒(bot_caps.wake)
  4 已讀走     那封信離開 inbox(bot 收了)
  5 有回覆     outbox 出現新的一封
★ 會真的叫醒 AI 一輪(花一輪額度);回覆會照常出現在那一席的畫面上。
codexbot 是直連 App Server 的另一種橋(沒有信箱),這支量不到 ⇒ 明說,不假裝。

    aib bot ping a seat [--timeout 秒] [--json]
    aib bot ping a seat --no-send      只查 1、2 段(不寫信、不叫醒 AI、不花額度)
    aib bot ping --all --no-send       這台每一隻沒停的信箱橋都查 1、2 段
rc:0 = 五段都過;1 = 卡在某一段(印哪一段);2 = 用法錯或找不到席;3 = 這一家量不到。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bot_caps  # noqa: E402

HEARTBEAT_STALE_S = 10.0
DEFAULT_TIMEOUT = {"schedule": 600.0}     # 排程制要等 Muse 下一輪(文件寫約 3 分鐘一次)
DEFAULT_TIMEOUT_S = 180.0


def state_root() -> Path:
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def find_seat(code: str, base: Path | None = None):
    """(bridge 目錄, status) —— 席碼對得上、沒停的那一席;找不到回 None。同碼兩席取心跳較新的。"""
    code = code.strip().lstrip("@").lower()
    best = None
    for st in (base or state_root()).glob("bridge/*/status.json"):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or str(d.get("code") or "").lower() != code or d.get("state") == "stopped":
            continue
        m = st.stat().st_mtime
        if best is None or m > best[2]:
            best = (st.parent, d, m)
    return (best[0], best[1]) if best else None


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def ping(code: str, timeout: float | None = None, base: Path | None = None, poll_s: float = 0.5,
         ring=None, say=print, send: bool = True) -> dict:
    """回 {ok, stage, stages:[{name, ok, detail, s}], provider}。ring 是測試注入點(預設照 bot_caps.wake 叫醒)。"""
    t0 = time.time()
    stages: list[dict] = []

    def mark(name, ok, detail):
        row = {"name": name, "ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
        stages.append(row)
        say(("✓ " if ok else "✗ ") + f"{name}:{detail}" + (f"({row['s']} 秒)" if ok and row["s"] >= 0.5 else ""))
        return ok

    found = find_seat(code, base)
    if not found:
        mark("找到席", False, f"@{code.lstrip('@')} 沒有在跑的 bot 橋接席(bridge/*/status.json 對不上席碼)")
        return {"ok": False, "stage": "找到席", "stages": stages, "provider": "", "rc": 2}
    root, st = found
    provider = str(st.get("provider") or "")
    cap = bot_caps.caps(provider)
    if cap.bridge != "mailbox" or provider not in bot_caps.CAPS:
        mark("找到席", False, f"{provider or '不明'} 不是信箱橋(codexbot 直連 App Server),這支量不到;請在那一席直接打一句看回覆")
        return {"ok": False, "stage": "找到席", "stages": stages, "provider": provider, "rc": 3}
    alive = pid_alive(st.get("pid"))
    try:
        age = time.time() - (root / "status.json").stat().st_mtime
    except OSError:
        age = 1e9
    if not mark("1 席活著", alive and age < HEARTBEAT_STALE_S,
                f"{provider} @{st.get('code')} pid {st.get('pid')}" + ("" if alive else " 已不在")
                + (f",心跳 {int(age)} 秒沒更新" if age >= HEARTBEAT_STALE_S else "")):
        return {"ok": False, "stage": "1 席活著", "stages": stages, "provider": provider, "rc": 1}
    agent = str(st.get("agent_id") or "")
    if not mark("2 綁了對話", bool(agent) or cap.mail_without_bind,
                f"{cap.bind_what} {agent[:8]}" if agent else
                ("沒綁 Muse 對話(排程拉信照常)" if cap.mail_without_bind else
                 f"沒綁{cap.bind_what}" + ("(席上打 /bind 綁)" if cap.bind_in_seat else ""))):
        return {"ok": False, "stage": "2 綁了對話", "stages": stages, "provider": provider, "rc": 1}
    if not send:
        say("(--no-send:沒寫信、沒叫醒,3–5 段沒量)")
        return {"ok": True, "stage": "", "stages": stages, "provider": provider, "rc": 0, "partial": True}

    import sand_bridge as sb
    tag = "ping-" + secrets.token_hex(3)
    text = f"[連線測試 aib bot ping {tag}] 這是 the maintainer 跑的連線測試,請只回一句「pong {tag}」,不要做別的事。"
    seen_out = {p.name for p in (root / "outbox").glob("*.json")}
    sess = str(st.get("session_uuid") or root.name)
    path = sb.enqueue(root, "inbox", text, sess, provider, "human")
    rung = ""
    if cap.wake == "webhook":
        res = (ring or sb.wake_cursor_chat)(sess, sb.short_code(sess), text, provider)
        rung = "門鈴 ok" if res.get("ok") else f"門鈴失敗({res.get('reason') or res.get('status')})"
    elif cap.wake == "hook":
        rung = "Claude 的 hook 自己盯信箱"
    elif cap.wake == "schedule":
        rung = "等 Muse 排程來拉"
    mark("3 送進信箱", True, f"{path.name[:13]}… · {rung}")

    limit = timeout if timeout is not None else DEFAULT_TIMEOUT.get(cap.wake, DEFAULT_TIMEOUT_S)
    read_at = None
    while time.time() - t0 < limit:
        if read_at is None and not path.exists():
            read_at = time.time()
            mark("4 已讀走", True, "bot 收走了這封")
        for p in sorted((root / "outbox").glob("*.json")):
            if p.name in seen_out:
                continue
            seen_out.add(p.name)
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if body.get("role") == "user":     # 人在 app 那邊說的話被回報過來,不是回覆
                continue
            reply = str(body.get("text") or body.get("body") or "")
            if read_at is None:
                mark("4 已讀走", True, "bot 收走了這封")
            mark("5 有回覆", True, reply.strip().replace("\n", " ")[:80] + ("" if tag in reply else "(沒帶 pong 標記,可能是別封的回覆)"))
            return {"ok": True, "stage": "", "stages": stages, "provider": provider, "rc": 0}
        time.sleep(poll_s)
    if read_at is None:
        why = {"webhook": "門鈴沒叫醒它,或 bot 沒來收信", "hook": "Claude 那邊的 hook 沒收(對話關了?hook 沒裝?)",
               "schedule": "Muse 這段時間沒來拉信"}.get(cap.wake, "")
        mark("4 已讀走", False, f"{int(limit)} 秒內信還在 inbox;{why}")
        return {"ok": False, "stage": "4 已讀走", "stages": stages, "provider": provider, "rc": 1}
    mark("5 有回覆", False, f"信收走了,但 {int(limit)} 秒內沒有回覆(AI 可能還在做,或回覆沒寫回 outbox)")
    return {"ok": False, "stage": "5 有回覆", "stages": stages, "provider": provider, "rc": 1}


def live_codes(base: Path | None = None) -> list[str]:
    """沒停的信箱橋席碼(去重、排序)。"""
    out = set()
    for st in (base or state_root()).glob("bridge/*/status.json"):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("state") != "stopped" and d.get("code") \
                and bot_caps.caps(str(d.get("provider") or "")).bridge == "mailbox" and d.get("provider") in bot_caps.CAPS:
            out.add(str(d["code"]))
    return sorted(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib bot ping", description="一鍵檢查一隻 bot 橋接席通不通(會真的叫醒 AI 一輪;--no-send 不會)")
    ap.add_argument("code", nargs="?", help="席碼,例 @00000")
    ap.add_argument("--all", action="store_true", help="這台每一隻沒停的信箱橋(只能配 --no-send)")
    ap.add_argument("--no-send", action="store_true", help="只查 1、2 段,不寫信不叫醒")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.all:
        if not a.no_send:
            print("--all 只能配 --no-send(一次叫醒每一隻 AI 太貴)", file=sys.stderr)
            return 2
        worst = 0
        for c in live_codes():
            print(f"── @{c}")
            r = ping(c, send=False)
            worst = max(worst, r["rc"])
        return worst
    if not a.code:
        ap.error("要給席碼,或用 --all --no-send")
    res = ping(a.code, a.timeout, say=(lambda s: None) if a.json else print, send=not a.no_send)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    elif res["ok"] and res.get("partial"):
        print("結論:前兩段通(席活著、綁了對話);信有沒有人收沒量。")
    elif res["ok"]:
        print("結論:通。")
    else:
        print(f"結論:不通,卡在「{res['stage']}」。")
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
