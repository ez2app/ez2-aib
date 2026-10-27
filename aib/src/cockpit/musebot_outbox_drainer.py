#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""MuseBot outbox 排水器:把 Muse 丟進 outbox、收件人是別席的信,由**本席的橋**經正規 dm 路送出。

為什麼要有這一支(2026-10-06,musebot a seat 起草於雲端 VM、root a seat 接手改寫):

- Muse 的腦在雲端,對這台 Mac **只有檔案讀寫,沒有 shell**:它寫得了 `bridge/<session>/outbox/*.json`,
  跑不了 `aib dm`、也跑不了 `aib-musebot-mail dm`。所以「寫一個檔 = 寄一封 dm」是它唯一走得到的寄信方式。
- 原稿想用 cron 從外面撿檔、拿訊息裡的 session_uuid 自報身分去寄。但 dm-ctl 對「自報」的抬頭固定寫成
  `seat_claimed:五碼(自稱)`,不給 @五碼 —— 從 cron 跑的程式查不到祖先在哪個席位 pane,這是設計不是缺陷。
  要抬頭是真的 @席碼,信**必須從席 pane 底下的行程送出**。橋(sand_bridge)就是那個行程 ⇒ 排水器搬進橋裡跑,
  寄信函式直接用橋的 dm_runner(與 dm socket 同一條路),不走 cron、不自報。

契約(Muse 側照這個寫檔;`aib-musebot-mail post --to <艙位碼>/<席碼>` 也會寫出同樣的檔):

    bridge/<session_uuid>/outbox/<毫秒>-<任意>.json
    {"schema": "aib-sand-bridge-msg/v1", "provider": "musebot", "from": "muse",
     "to": "<艙位碼>/<席碼>",        # 例 664d/2ad75;@五碼 會補本席的艙位碼(橋寫在 status.json 的 cabin)
     "session_uuid": "<本席 session uuid>", "text": "<正文>", "created_epoch": 1791276321}

    to = "human"(或沒寫)      ⇒ 不是 dm,橋只印在 pane 上(原本的行為),排水器不碰。
    to = 艙位碼/席碼 或 @五碼  ⇒ 排水器送;送達 ⇒ 搬到 delivered/;連續 MAX_RETRIES 次沒送達 ⇒ 搬到 failed/。
    to 形狀不對、schema 不對、正文空 ⇒ 直接 failed/(不送、不猜收件人)。

送達判準與 dm socket / MCP 同一條(`codexbot_dm_socket.outcome_ok`:回字含 rc=0 且沒有「未送出」)。
每分鐘最多 DM_PER_MIN 則(與 grokbot 信箱同值),防 bot 與 bot 互叫無限乒乓;超過的留在 outbox 下一分鐘再送。

防重複:送之前先把檔改名成 `<名>.sending`(同一席只有一條排水執行緒,這是防「橋重開時上一條還沒收尾」);
`.sending` 超過 STALE_SECS 沒動 ⇒ 視為上一條橋死在送信途中,搬回 outbox 重送(至少一次,不保證恰好一次)。

絕不寫 `state/dm-queue/`(繞過排隊與稽核,a seat 硬規則)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

MAX_RETRIES = 5
STALE_SECS = 600
POLL_S = 1.0
PROVIDER = "musebot"
MSG_SCHEMA = "aib-sand-bridge-msg/v1"
NOT_DM = ("", "human", "cursor")
# 與 aib_dm_tool.DM_ADDRESS 同形(艙位碼/席碼,可帶跨主機 IP/);@五碼 另外收、補本席艙位碼
_FULL_RE = re.compile(r"^(?:[0-9]{1,3}(?:\.[0-9]{1,3}){3}/)?[0-9a-z]{1,4}/@?[0-9a-z]{4,8}$")
_BARE_RE = re.compile(r"^@[0-9a-z]{4,8}$")

try:
    from grokbot_mailbox import DM_PER_MIN  # 同一個上限,不另抄一份數字
except Exception:  # noqa: BLE001
    DM_PER_MIN = 6


def _outcome_ok(outcome: str) -> bool:
    try:
        from codexbot_dm_socket import outcome_ok
        return bool(outcome_ok(outcome))
    except Exception:  # noqa: BLE001
        return "rc=0" in (outcome or "") and "未送出" not in (outcome or "")


def is_dm_target(to) -> bool:
    """這一則是不是要寄給別席(而不是印給人看)。"""
    t = (to or "").strip() if isinstance(to, str) else ""
    return t not in NOT_DM


def resolve_target(to: str, session: str, status: dict | None) -> str:
    """回給 aib dm 的目標;解不出就回空字串(不猜)。
    艙位碼/席碼 原樣;@五碼 用橋寫在 status.json 的 cabin 補(與 grokbot_mailbox.resolve_dm_target 同一條規則)。"""
    t = (to or "").strip()
    if _FULL_RE.match(t):
        return t
    if _BARE_RE.match(t):
        try:
            from grokbot_mailbox import resolve_dm_target
            got, err = resolve_dm_target(t, session, status=status)
            return got if (got and not err and _FULL_RE.match(got)) else ""
        except Exception:  # noqa: BLE001
            cab = ((status or {}).get("cabin") or "").strip() if isinstance(status, dict) else ""
            return (cab + "/" + t[1:]) if re.fullmatch(r"[0-9a-z]{1,4}", cab) else ""
    return ""


def _read_status(root: Path) -> dict | None:
    try:
        d = json.loads((root / "status.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _retry_path(msg_path: Path) -> Path:
    return msg_path.with_name(msg_path.name + ".retry")


def _read_retry(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _park(path: Path, dest: str) -> Path | None:
    """搬到 <席目錄>/<dest>/<原檔名>(去掉 .sending),順手刪 .retry。"""
    name = path.name[: -len(".sending")] if path.name.endswith(".sending") else path.name
    destdir = path.parent.parent / dest
    try:
        destdir.mkdir(parents=True, exist_ok=True)
        (path.parent / (name + ".retry")).unlink(missing_ok=True)
        target = destdir / name
        shutil.move(str(path), str(target))
        return target
    except OSError:
        return None


def reap_stale_sending(outbox: Path, now: float | None = None) -> int:
    """上一條橋死在送信途中留下的 .sending(超過 STALE_SECS)搬回 outbox 重送。回搬回幾個。"""
    now = time.time() if now is None else now
    n = 0
    for sending in sorted(outbox.glob("*.json.sending")):
        try:
            age = now - sending.stat().st_mtime
        except OSError:
            continue
        if age < STALE_SECS:
            continue
        back = sending.with_name(sending.name[: -len(".sending")])
        if back.exists():
            back = sending.with_name(sending.name[: -len(".sending")] + ".stale")
        try:
            sending.rename(back)
            n += 1
        except OSError:
            continue
    return n


def deliver_one(msg_path: Path, runner, say=None, status: dict | None = None, now: float | None = None) -> str:
    """送一則。回 delivered / skipped(不是 dm,不碰)/ failed(留著重試)/ dead(進 failed/)。
    runner(target, text) -> 回字(aib dm 的結果句);say(text) 印在席上。"""
    say = say or (lambda _t: None)
    try:
        msg = json.loads(msg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "skipped"
    except (OSError, ValueError):
        _park(msg_path, "failed")
        say(f"{PROVIDER}: outbox 裡 {msg_path.name} 不是合法 JSON,搬到 failed/")
        return "dead"
    if not isinstance(msg, dict) or msg.get("provider") != PROVIDER:
        return "skipped"
    if not is_dm_target(msg.get("to")):
        return "skipped"
    if msg.get("schema") != MSG_SCHEMA:
        _park(msg_path, "failed")
        say(f"{PROVIDER}: {msg_path.name} schema 不是 {MSG_SCHEMA},搬到 failed/")
        return "dead"
    session = (msg.get("session_uuid") or "").strip()
    target = resolve_target(str(msg.get("to") or ""), session, status)
    text = (msg.get("text") or "").strip() if isinstance(msg.get("text"), str) else ""
    if not target:
        _park(msg_path, "failed")
        say(f"{PROVIDER}: {msg_path.name} 收件人「{str(msg.get('to'))[:40]}」不是 艙位碼/席碼 也補不出艙位碼,搬到 failed/(不猜)")
        return "dead"
    if not text:
        _park(msg_path, "failed")
        say(f"{PROVIDER}: {msg_path.name} 正文是空的,搬到 failed/")
        return "dead"
    sending = msg_path.with_name(msg_path.name + ".sending")
    try:
        msg_path.rename(sending)
    except OSError:
        return "failed"
    try:
        outcome = runner(target, text) or ""
    except Exception as exc:  # noqa: BLE001 —— 送信炸掉不能讓橋停
        outcome = f"DM 未送出:排水器呼叫寄信失敗({type(exc).__name__}:{str(exc)[:120]})"
    if _outcome_ok(outcome):
        parked = _park(sending, "delivered")
        if parked is not None:
            _sign_delivered(parked, msg, target, text, status)
        say(f"{PROVIDER} → {target}:已送出({str(outcome)[:100]})")
        return "delivered"
    retry = _retry_path(msg_path)
    n = _read_retry(retry) + 1
    try:
        sending.rename(msg_path)
    except OSError:
        return "failed"
    if n >= MAX_RETRIES:
        _park(msg_path, "failed")
        say(f"{PROVIDER} → {target}:連續 {n} 次沒送達,放棄,搬到 failed/;最後一次:{str(outcome)[:160]}")
        return "dead"
    try:
        retry.write_text(str(n), encoding="utf-8")
    except OSError:
        pass
    say(f"{PROVIDER} → {target}:沒送達(第 {n}/{MAX_RETRIES} 次,留著重送):{str(outcome)[:160]}")
    return "failed"


def _sign_delivered(path: Path, msg: dict, target: str, text: str, status: dict | None) -> bool:
    """送達後在 delivered/ 那份信加 `_aib_sig`(SPEC §17)。寄件席取自本席橋寫的 status.json(cabin/code),
    不取檔內 from。任何失敗只略過簽章,不影響已送達(簽章是附加資訊,不是送信條件)。"""
    try:
        import bridge_sign as _bs
        st = status if isinstance(status, dict) else {}
        cabin, code = str(st.get("cabin") or "").strip(), str(st.get("code") or "").strip().lstrip("@")
        if not re.fullmatch(r"[0-9a-z]{1,4}", cabin) or not re.fullmatch(r"[0-9a-z]{4,8}", code):
            return False
        to = target.split("/", 1)[1] if target.count("/") == 2 else target  # 去掉跨主機 IP/ 前綴
        to = re.sub(r"/@", "/", to)
        state_root = path.parent.parent.parent.parent  # <state root>/bridge/<session>/delivered/<信>
        key = _bs.load_key(state_root if (state_root / "bridge").is_dir() else None, create=True)
        if not key:
            return False
        stem = path.name[: -len(".json")] if path.name.endswith(".json") else path.name
        mid = str(msg.get("message_id") or stem)
        d = dict(msg)
        d[_bs.SIG_FIELD] = _bs.make_sig(key, f"{cabin}/{code}", to, mid, text, time.time())
        tmp = path.with_name("." + path.name + ".sigtmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 —— 簽章失敗不能讓橋停
        return False


class _RateWindow:
    def __init__(self, per_min: int):
        self.per_min = per_min
        self.sent: list[float] = []

    def allow(self, now: float) -> bool:
        self.sent = [t for t in self.sent if now - t < 60]
        return len(self.sent) < self.per_min

    def mark(self, now: float) -> None:
        self.sent.append(now)


def drain_session(root: Path, runner, say=None, rate: _RateWindow | None = None, now: float | None = None) -> dict:
    """掃一席的 outbox 一輪。回各結果的數量。"""
    counts = {"delivered": 0, "skipped": 0, "failed": 0, "dead": 0, "reaped": 0, "deferred": 0}
    outbox = root / "outbox"
    if not outbox.is_dir():
        return counts
    now = time.time() if now is None else now
    counts["reaped"] = reap_stale_sending(outbox, now)
    status = _read_status(root)
    for p in sorted(outbox.glob("*.json")):
        try:
            peek = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            peek = None
        if isinstance(peek, dict) and peek.get("provider") == PROVIDER and is_dm_target(peek.get("to")):
            if rate is not None and not rate.allow(now):
                counts["deferred"] += 1
                continue
            out = deliver_one(p, runner, say, status, now)
            if out == "delivered" and rate is not None:
                rate.mark(now)
        else:
            out = deliver_one(p, runner, say, status, now)
        counts[out] = counts.get(out, 0) + 1
    return counts


def start_thread(root: Path, runner, say, stop: dict, poll_s: float = POLL_S) -> threading.Thread:
    """給 sand_bridge 用:在席 pane 的橋行程裡起一條排水執行緒。stop["flag"] 為真就收。"""
    rate = _RateWindow(DM_PER_MIN)

    def run() -> None:
        while not stop.get("flag"):
            try:
                drain_session(root, runner, say, rate)
            except Exception as exc:  # noqa: BLE001 —— 排水器任何錯都不能讓橋停
                try:
                    say(f"{PROVIDER}: 排水器這一輪出錯({type(exc).__name__}),下一輪再試")
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(poll_s)

    thr = threading.Thread(target=run, name="musebot-outbox-drainer", daemon=True)
    thr.start()
    return thr


def _socket_runner_for(root: Path):
    """CLI 模式(手動補掃一輪)用:經那一席橋的 dm socket 送,抬頭才是 @席碼。socket 不在回 None。"""
    try:
        import codexbot_dm_socket as _dms
    except ImportError:
        return None
    base = root.parent.parent  # <state root>/bridge/<session> → <state root>
    sock = base / "musebot-bindings" / (root.name + ".dm.sock")
    if not sock.exists():
        return None

    def run(target: str, text: str) -> str:
        reply = _dms.send_via_bridge(sock, target, text)
        return reply.get("outcome") or ("DM 未送出:橋沒回字" if not reply.get("ok") else "rc=0")

    return run


def main(argv=None) -> int:
    """手動掃一輪(平常不需要:橋自己在跑)。只對 socket 還在的席送;socket 不在的席留著並說明,不自報身分直送。"""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge-root", default=None, help="預設 <EZ2_AIB_STATE_ROOT|~/.ez2-aib>/bridge")
    args = ap.parse_args(argv)
    if args.bridge_root:
        root = Path(args.bridge_root)
    else:
        base = os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or str(Path.home() / ".ez2-aib")
        root = Path(base).expanduser() / "bridge"
    if not root.is_dir():
        print(f"bridge 目錄不在:{root}")
        return 1
    total = {}
    for sess in sorted(p for p in root.iterdir() if p.is_dir()):
        st = _read_status(sess)
        if not st or st.get("provider") != PROVIDER:
            continue
        runner = _socket_runner_for(sess)
        if runner is None:
            pending = [p for p in (sess / "outbox").glob("*.json") if is_dm_target((_peek(p) or {}).get("to"))]
            if pending:
                print(f"@{st.get('code') or '?'}:橋的 dm socket 不在(席沒開或橋是舊碼),{len(pending)} 則留在 outbox 不送(不自報身分直送)")
            continue
        counts = drain_session(sess, runner, print)
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    print("合計:" + json.dumps(total, ensure_ascii=False))
    return 0


def _peek(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
