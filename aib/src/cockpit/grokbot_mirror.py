#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""grokbot 席鏡射:把 Grok Bot.app 目前那條對話即時印在 AIB 的 grokbot 席上。

讀取一律走 grokbot_transcript.py 的 CLI --json(a seat 的讀取層)。本檔不 import
它的內部:介面變了會在這裡以「讀取層回了看不懂的東西」紅出來,不會靜默解析錯。

三條規格:
  N42 副本不是正本:每批新訊息前印副本時間,並標「本機副本,雲端才是正本」。
  N43 不落檔:本檔不寫任何檔、不寫日誌;poll() 只回傳要印的行,由呼叫端印到席上。
  N52 格式變了要紅:讀取層回 rc=5 ⇒ 印紅字、不印內容、不猜。

    python3 src/cockpit/grokbot_mirror.py once [--dir D] [--n 5]               # 印一輪就走(驗證用)
    python3 src/cockpit/grokbot_mirror.py poll --count 3 [--interval S] ...    # 連讀幾輪(尺用)

環境變數:
  AIB_GROKBOT_MIRROR=1              打開鏡射(預設關:本機副本不即時,見 mirror_enabled)
  AIB_GROKBOT_MIRROR_N              首次顯示最後幾則(預設 5)
  AIB_GROKBOT_MIRROR_INTERVAL_S     輪詢間隔秒(預設 3)
  AIB_GROKBOT_READER                讀取層路徑(治具用;預設同目錄的 grokbot_transcript.py)
  AIB_GROKBOT_DIR                   Grok Bot 資料根(讀取層與本檔同一個覆寫)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
READER_NAME = "grokbot_transcript.py"
ENV_ENABLED = "AIB_GROKBOT_MIRROR"
ENV_N = "AIB_GROKBOT_MIRROR_N"
ENV_INTERVAL = "AIB_GROKBOT_MIRROR_INTERVAL_S"
ENV_READER = "AIB_GROKBOT_READER"
ENV_DIR = "AIB_GROKBOT_DIR"

DEFAULT_N = 5
DEFAULT_INTERVAL_S = 3.0
# 每輪向讀取層要的窗口。比兩輪之間可能新增的則數大就不會漏;
# 真的漏了(前一輪的最後一則不在這一窗裡)會印出「中間有略過」,不會假裝連續。
FETCH_WINDOW = 50
# 一輪最多印幾則新訊息;超過的只報數目,不洗版。
MAX_BURST = 20
# 檔案簽章沒變就跳過讀取層;但最多隔這麼久一定真讀一次,
# 免得簽章判準本身錯了,鏡射就靜靜地永遠不更新。
FORCE_REFRESH_S = 30.0
READER_TIMEOUT_S = 10

RC_OK = 0
RC_UNREADABLE = 3
RC_NOT_FOUND = 4
RC_SCHEMA = 5
RC_NO_READER = 127
RC_BAD_OUTPUT = 126

REPLICA_NOTE = "本機副本,雲端才是正本"
PERSISTENCE_NAME = "sand-client-persistence"
DEFAULT_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Grok Bot"

LINE_PREFIX = "[App]"
GROK_LABEL = "Grok"
HUMAN_ROLE = "user"


def mirror_enabled(env=None) -> bool:
    """預設關(09-25):app 本機副本不是即時寫入的(啟動與點選時才寫),鏡射只看得到舊內容,
    即時內容已改由 bot 用 aib-grokbot-mail 自己寫回;而讀取層沒裝時每一席都印一行紅字。要看時 AIB_GROKBOT_MIRROR=1。"""
    env = os.environ if env is None else env
    return (env.get(ENV_ENABLED) or "0").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(env, key, default, lo):
    try:
        return max(lo, int((env.get(key) or "").strip()))
    except ValueError:
        return default


def _float_env(env, key, default, lo):
    try:
        return max(lo, float((env.get(key) or "").strip()))
    except ValueError:
        return default


def resolve_persistence(env=None) -> Path:
    """與讀取層 resolve_root 同一條規則:給的是 persistence 本身也認。"""
    env = os.environ if env is None else env
    raw = (env.get(ENV_DIR) or "").strip()
    p = Path(raw).expanduser() if raw else DEFAULT_APP_SUPPORT
    if p.name == PERSISTENCE_NAME:
        return p
    return p / PERSISTENCE_NAME


def blob_signature(pers: Path):
    """只用 stat,不讀內容(N43)。目錄不在回 None。"""
    try:
        stats = [p.stat() for p in pers.glob("*.blob")]
    except OSError:
        return None
    if not pers.is_dir():
        return None
    return (len(stats), max((s.st_mtime_ns for s in stats), default=0),
            sum(s.st_size for s in stats))


def _red(text: str, env) -> str:
    if (env.get("NO_COLOR") or "") != "":
        return "✖ " + text
    return "\033[31m✖ " + text + "\033[0m"


def _one_line(text: str) -> str:
    return " ⏎ ".join(part.strip() for part in str(text or "").splitlines() if part.strip())


def format_entry(entry: dict, human_label: str) -> str:
    role = entry.get("role") or ""
    if role == HUMAN_ROLE:
        who = human_label
    elif role == GROK_LABEL:
        who = GROK_LABEL
    else:
        who = "·"
    return "%s %s: %s" % (LINE_PREFIX, who, _one_line(entry.get("content") or ""))


def entry_key(entry: dict) -> str:
    eid = entry.get("id")
    if isinstance(eid, str) and eid:
        return "id:" + eid
    return "rc:%s\x1f%s" % (entry.get("role") or "", entry.get("content") or "")


def new_since(prev_keys: list, current: list):
    """回 (新列, 是否有斷層)。

    以前一輪「最後一則」在這一輪的位置為錨,錨之後都是新的。
    錨不在這一窗裡 ⇒ 兩輪之間新增超過一窗,有斷層,整窗都當新的。
    """
    if not prev_keys:
        return list(current), False
    keys = [entry_key(e) for e in current]
    anchor = prev_keys[-1]
    for i in range(len(keys) - 1, -1, -1):
        if keys[i] == anchor:
            return list(current[i + 1:]), False
    return list(current), True


class Reader:
    """讀取層 CLI 的薄殼。回 (rc, payload, stderr)。"""

    def __init__(self, reader_path=None, env=None, py=None):
        env = dict(os.environ if env is None else env)
        self.env = env
        self.path = Path(reader_path or env.get(ENV_READER) or (HERE / READER_NAME))
        self.py = py or sys.executable

    def _run(self, args):
        if not self.path.is_file():
            return RC_NO_READER, None, "找不到讀取層 %s" % self.path.name
        try:
            # stdin 一定要切斷:鏡射跑在席裡,席的 stdin 就是人打字的終端機
            r = subprocess.run([self.py, str(self.path)] + args + ["--json"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=READER_TIMEOUT_S, env=self.env)
        except subprocess.TimeoutExpired:
            return RC_UNREADABLE, None, "讀取層逾時 %ss" % READER_TIMEOUT_S
        err = (r.stderr or "").strip()
        if r.returncode != RC_OK:
            return r.returncode, None, err
        try:
            payload = json.loads(r.stdout or "")
        except ValueError:
            return RC_BAD_OUTPUT, None, "讀取層回了看不懂的東西"
        if not isinstance(payload, dict):
            return RC_BAD_OUTPUT, None, "讀取層回了看不懂的東西"
        return RC_OK, payload, err

    def agents(self):
        return self._run(["agents"])

    def tail(self, agent_id, n):
        return self._run(["tail", "--agent", agent_id, "--n", str(n)])


class Mirror:
    """狀態機。poll() 回要印的行;錯誤狀態只在「變成那個狀態」時印一次。"""

    def __init__(self, reader=None, env=None, human_label="你", n=None, clock=None, pin_agent=None):
        self.env = dict(os.environ if env is None else env)
        self.reader = reader or Reader(env=self.env)
        self.human_label = human_label
        self.n = n if n is not None else _int_env(self.env, ENV_N, DEFAULT_N, 1)
        self.clock = clock or time.monotonic
        self.pers = resolve_persistence(self.env)
        self.pin_agent = pin_agent  # G5:席綁了 agent 就固定看它,不跟 app 的選中狀態跑
        self.agent_id = None
        self.prev_keys = []
        self.state = None
        self.last_sig = None
        self.last_full = None

    def _enter(self, state, line):
        if state == self.state:
            return []
        self.state = state
        return [line]

    def _fail(self, rc, err, what):
        if rc == RC_SCHEMA:
            # N52:讀取層那句「格式變了」原樣轉出來,本檔不改寫它。
            msg = err or "Grok Bot 的檔案格式變了,本工具不猜"
            return self._enter(("schema",), _red(msg + "(鏡射停印內容)", self.env))
        if rc == RC_NO_READER:
            return self._enter(("no-reader",), _red("鏡射讀取層還沒裝:" + err, self.env))
        if rc == RC_NOT_FOUND:
            return self._enter(("not-found", what), _red("Grok Bot 沒有這條對話的本機副本", self.env))
        if rc == RC_BAD_OUTPUT:
            return self._enter(("bad-output",), _red(err + "(讀取層介面變了?)", self.env))
        return self._enter(("unreadable", rc), _red("讀不到 Grok Bot 副本(rc=%s)%s" % (
            rc, (":" + err) if err else ""), self.env))

    def _skip_unchanged(self) -> bool:
        sig = blob_signature(self.pers)
        now = self.clock()
        if (sig is not None and sig == self.last_sig and self.last_full is not None
                and now - self.last_full < FORCE_REFRESH_S and self.state == ("ok",)):
            return True
        self.last_sig = sig
        self.last_full = now
        return False

    def poll(self) -> list:
        if self._skip_unchanged():
            return []
        rc, payload, err = self.reader.agents()
        if rc != RC_OK:
            return self._fail(rc, err, "agents")
        rows = payload.get("agents")
        if not isinstance(rows, list):
            return self._fail(RC_BAD_OUTPUT, "讀取層回了看不懂的東西", "agents")
        if not rows:
            return self._enter(("empty",), "%s Grok Bot 還沒有任何對話副本(%s)" % (
                LINE_PREFIX, REPLICA_NOTE))
        if self.pin_agent:
            chosen = next((r for r in rows if r.get("agentId") == self.pin_agent), None)
            if chosen is None:
                return self._enter(("pinned-missing",), _red(
                    "這一席綁的 agent %s 在 Grok Bot 本機副本裡找不到" % self.pin_agent[:8], self.env))
            guessed = False
        else:
            chosen = next((r for r in rows if r.get("selected")), None)
            guessed = chosen is None
            if guessed:
                chosen = rows[0]  # 讀取層已按副本時間新到舊排
        agent_id = chosen.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            return self._fail(RC_BAD_OUTPUT, "讀取層回了看不懂的東西", "agents")

        rc, tail, err = self.reader.tail(agent_id, FETCH_WINDOW)
        if rc != RC_OK:
            return self._fail(rc, err, agent_id)
        entries = tail.get("entries")
        if not isinstance(entries, list):
            return self._fail(RC_BAD_OUTPUT, "讀取層回了看不懂的東西", agent_id)
        stamp = tail.get("replica_mtime") or chosen.get("replica_mtime") or "?"

        out = []
        if self.state != ("ok",):
            self.state = ("ok",)
        if agent_id != self.agent_id:
            self.agent_id = agent_id
            how = ("這一席綁的那條" if self.pin_agent else
                   "最近更新的那條(app 沒標選中)" if guessed else "app 目前選中的那條")
            out.append("%s ── 鏡射 Grok Bot:%s %s · 副本 %s(%s)──" % (
                LINE_PREFIX, how, agent_id[:8], stamp, REPLICA_NOTE))
            shown = entries[-self.n:]
            if len(entries) > len(shown):
                out.append("%s ……前面還有 %d 則沒顯示" % (LINE_PREFIX, len(entries) - len(shown)))
            out.extend(format_entry(e, self.human_label) for e in shown)
            self.prev_keys = [entry_key(e) for e in entries]
            return out

        fresh, gap = new_since(self.prev_keys, entries)
        self.prev_keys = [entry_key(e) for e in entries]
        if not fresh:
            return out
        out.append("%s ·· 副本 %s(%s)··" % (LINE_PREFIX, stamp, REPLICA_NOTE))
        if gap:
            out.append("%s ……兩次讀之間新增超過 %d 則,中間有略過" % (LINE_PREFIX, FETCH_WINDOW))
        if len(fresh) > MAX_BURST:
            out.append("%s ……這一批 %d 則,只印最後 %d 則" % (LINE_PREFIX, len(fresh), MAX_BURST))
            fresh = fresh[-MAX_BURST:]
        out.extend(format_entry(e, self.human_label) for e in fresh)
        return out


def start_thread(say, stop, env=None, human_label="你", interval_s=None, mirror=None, pin_agent=None):
    """在席上起鏡射執行緒。say(line) 由呼叫端印;stop 是 dict {"flag": bool}。"""
    env = dict(os.environ if env is None else env)
    if not mirror_enabled(env):
        return None
    m = mirror or Mirror(env=env, human_label=human_label, pin_agent=pin_agent)
    interval = interval_s if interval_s is not None else _float_env(
        env, ENV_INTERVAL, DEFAULT_INTERVAL_S, 0.5)

    def loop():
        while not stop.get("flag"):
            try:
                for line in m.poll():
                    say(line)
            except Exception as exc:  # 鏡射壞了不能拖垮席;但要看得見
                say(_red("鏡射這一輪出錯:%s" % type(exc).__name__, env))
            slept = 0.0
            while slept < interval and not stop.get("flag"):
                time.sleep(0.1)
                slept += 0.1

    thr = threading.Thread(target=loop, name="grokbot-mirror", daemon=True)
    thr.start()
    return thr


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="grokbot_mirror.py")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("once", "poll"):
        sp = sub.add_parser(name)
        sp.add_argument("--dir", default="")
        sp.add_argument("--n", type=int, default=None)
        sp.add_argument("--agent", default=None)
        if name == "poll":
            sp.add_argument("--count", type=int, default=2)
            sp.add_argument("--interval", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.cmd not in ("once", "poll"):
        ap.print_help(sys.stderr)
        return 2
    env = dict(os.environ)
    if args.dir:
        env[ENV_DIR] = args.dir
    m = Mirror(env=env, n=args.n, pin_agent=args.agent)
    count = 1 if args.cmd == "once" else max(1, args.count)
    for i in range(count):
        if i:
            print("-- poll %d --" % (i + 1))
            time.sleep(max(0.0, getattr(args, "interval", 0.0)))
        for line in m.poll():
            print(line, flush=True)
    return 0 if m.state in (("ok",), ("empty",)) else 1


if __name__ == "__main__":
    sys.exit(main())
