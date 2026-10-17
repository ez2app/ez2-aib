#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""線上對話端到端沙盒(第二層測試,Andy 10-17「回到開發主軸,與 @74e8b 深度配合,如何測試」)。

一個指令建好「假世界」:本機假中繼站 + 本機假證明站 + 五位假主人,全程**不碰真資料、不連公開站**:
  Andy-demo  你(兩席);艦長室接這一位
  Bob        好友:互加好友、寄一封 dm 給你、分享在線狀態(含 📌 狀態列)、名片(社群)+ NIP-05,證明頁在本機假站
  Cara       陌生人:寄 dm ⇒ 被拒收(進拒收紀錄)
  Dan        送好友申請(附一句話)⇒ 等你同意
  Eve        你封鎖了她:寄 dm ⇒ 靜默丟

  python3 aib/tests/chat_e2e_world.py up    --dir <資料夾> [--venv ~/.ez2-aib/chat/venv]
  python3 aib/tests/chat_e2e_world.py check --dir <資料夾>     # 核心這一半的斷言,rc 0 全過
  python3 aib/tests/chat_e2e_world.py down  --dir <資料夾>     # 停掉所有沙盒行程(資料夾留著,--rm 才刪)
up 完印出 andy.env 的位置:艦長室用它起在另一個埠(env $(cat andy.env) python3 captain_room.py serve --port 8798 …)。
要 nostr-sdk 0.45.1 的 venv(Pro 上的 ~/.ez2-aib/chat/venv 就是)。
"""
import argparse
import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
BRIDGE = COCKPIT / "chat_bridge.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))

OWNERS = [  # (名, 國, 艙, [(視窗, uuid, 人格 uid, 席名)])
    ("andy", "Andy-demo", "TW", "a0d1", [("w2", "00000000-0000-4000-8000-00000000a0a1", "andy01", "接待席"),
                                         ("w3", "00000000-0000-4000-8000-00000000a0a2", "andy02", "工程席")]),
    ("bob", "Bob", "JP", "b0b1", [("w2", "00000000-0000-4000-8000-00000000b0b1", "bob001", "業務席")]),
    ("cara", "Cara", "US", "c0c1", [("w2", "00000000-0000-4000-8000-00000000c0c1", "cara01", "推銷席")]),
    ("dan", "Dan", "KR", "d0d1", [("w2", "00000000-0000-4000-8000-00000000d0d1", "dan001", "合作席")]),
    ("eve", "Eve", "SG", "e0e1", [("w2", "00000000-0000-4000-8000-00000000e0e1", "eve001", "騷擾席")]),
]


# ── 本機假中繼站 + 假證明站(背景行程)──

def serve_hub(d, port):
    from aib_fake_relay import FakeRelay
    site_dir = Path(d) / "site"
    site_dir.mkdir(parents=True, exist_ok=True)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0].lstrip("/")
            if path == ".well-known/nostr.json":
                try:
                    names = json.loads((site_dir / "names.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    names = {}
                body, code = json.dumps({"names": names}), 200
            else:
                f = site_dir / path.replace("/", "_")
                body, code = (f.read_text(encoding="utf-8"), 200) if path and f.is_file() else ("no", 404)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    site = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    relay = FakeRelay(port=port).start()
    (Path(d) / "hub.json").write_text(json.dumps({"relay": relay.url, "site": "http://127.0.0.1:%d" % site.server_address[1],
                                                  "pid": os.getpid()}), encoding="utf-8")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        stop.wait(1)
    relay.stop()
    site.shutdown()


# ── 一位假主人 ──

class Owner(object):
    def __init__(self, d, key, hub, venv):
        self.d, self.key, self.hub, self.venv = Path(d), key, hub, venv
        self.home = self.d / key / "home"
        self.chat = self.home / ".ez2-aib" / "chat"
        self.root = self.d / key / "bridge"

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("EZ2_", "AIB_", "TMUX", "CAPTAIN_")) and k not in ("PYTHONPATH", "BASH_ENV")}
        env.update({"HOME": str(self.home), "AIB_CHAT_HOME": str(self.chat), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "EZ2_AIB_STATE_ROOT": str(self.home / ".ez2-aib"), "AIB_CHAT_RELAYS": self.hub["relay"],
                    "AIB_CHAT_TICK": "0.5", "AIB_CHAT_POLL": "0.2", "AIB_CHAT_NET_TIMEOUT": "3", "AIB_CHAT_RETRY_GAP": "0.5",
                    "AIB_CHAT_PRESENCE_TICK": "2", "AIB_CHAT_PRESENCE_GAP": "2", "AIB_CHAT_PRESENCE_EVERY": "20",
                    "AIB_CHAT_VERIFY_TICK": "3", "AIB_CHAT_VERIFY_ALLOW_LOCAL": "1", "AIB_CHAT_NIP05_BASE": self.hub["site"],
                    "AIB_CHAT_READ_BATCH": "2", "EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1"})
        env.update(extra)
        return env

    def cli(self, *args, check=False):
        r = subprocess.run([sys.executable, str(BRIDGE)] + list(args), capture_output=True, text=True, env=self.env(),
                           timeout=120)
        if check and r.returncode != 0:
            raise RuntimeError("%s: aib chat %s ⇒ rc %d\n%s%s" % (self.key, " ".join(args), r.returncode, r.stdout, r.stderr))
        return r

    def json(self, *args):
        return json.loads(self.cli(*args, check=True).stdout)

    def seat(self, inst, win, uuid, uid, name):
        sd = self.home / ".ez2-aib" / "instances" / inst / "seats"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / ("%s.json" % win)).write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": uuid, "role": "dev"}))
        disp = self.home / ".ez2-aib" / "identity" / "seat-display.json"
        disp.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(disp.read_text()) if disp.exists() else {"display": {}}
        cur["display"][uuid.replace("-", "")[-5:]] = {"persona_uid": uid, "display_name": name}
        disp.write_text(json.dumps(cur))
        return uuid.replace("-", "")[-5:]

    def now(self, code, item, state="busy", done=2, total=5):
        d = self.home / ".ez2-aib" / "state" / "seat-now"
        d.mkdir(parents=True, exist_ok=True)
        t = int(time.time())
        (d / (code + ".json")).write_text(json.dumps({"at": t, "code": code, "item": item, "state": state,
                                                       "steps_done": done, "steps_total": total}), encoding="utf-8")
        (d / (code + ".beat")).write_text(json.dumps({"at": t, "src": "statusline", "code": code}), encoding="utf-8")

    def start(self, pids):
        self.chat.mkdir(parents=True, exist_ok=True)
        if not (self.chat / "venv").exists():
            os.symlink(self.venv, str(self.chat / "venv"))
        log = open(str(self.d / ("%s-bridge.log" % self.key)), "ab")
        p = subprocess.Popen([sys.executable, str(BRIDGE), "run"], stdout=log, stderr=log, env=self.env(),
                             start_new_session=True)
        pids.append(p.pid)
        return p.pid

    def status(self):
        try:
            return json.loads((self.root / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def owner(self):
        import nostr_keys as NK
        return NK.read_map(self.env())["owner"]["npub"]


def wait_for(cond, secs, what):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        time.sleep(0.5)
    raise RuntimeError("等不到:" + what)


def _send(o, to, frm_seat, content, wait=20):
    import dm_global_files as GF
    rec = {"v": 1, "msgid": GF.new_msgid(), "created_at": int(time.time()), "op": "send",
           "from": {"cabin": o.cab, "seat": frm_seat, "attested": True}, "to": to, "content": content}
    return GF.send_and_wait(o.root, rec, wait=wait)


def _load(d):
    return json.loads((Path(d) / "world.json").read_text(encoding="utf-8"))


# ── up / check / down ──

def up(d, venv, out=sys.stdout):
    d = Path(d).resolve()
    d.mkdir(parents=True, exist_ok=True)
    if (d / "pids.json").exists():
        raise SystemExit("這個資料夾已有沙盒在跑(先 down)")
    if not Path(venv, "bin", "python").exists():
        raise SystemExit("找不到 nostr-sdk 的 venv:%s" % venv)
    pids = []
    hub = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_hub", "--dir", str(d)],
                           stdout=subprocess.DEVNULL, stderr=open(str(d / "hub.log"), "ab"), start_new_session=True)
    pids.append(hub.pid)
    (d / "pids.json").write_text(json.dumps(pids))
    wait_for(lambda: (d / "hub.json").exists(), 15, "假中繼站")
    hubinfo = json.loads((d / "hub.json").read_text(encoding="utf-8"))
    os.environ["AIB_CHAT_VERIFY_ALLOW_LOCAL"] = "1"
    world = {"hub": hubinfo, "owners": {}}
    owners = {}
    for key, name, cc, cab, seats in OWNERS:
        o = Owner(d, key, hubinfo, venv)
        o.cab = cab
        codes = [o.seat("20261017-000000-%s" % cab, w, u, uid, nm) for w, u, uid, nm in seats]
        o.cli("on", "--no-venv", "--name", name, "--country", cc, check=True)
        for c in codes:
            o.cli("publish", c, check=True)
            o.now(c, "示範:%s 的 %s 在忙" % (name, c))
        owners[key] = o
        o.codes = codes
    # 名字、證明(Bob)寫好再起轉接程式
    bob = owners["bob"]
    site = d / "site"
    site.mkdir(exist_ok=True)
    (site / "bob-proof").write_text("Bob 在 aib 的主人號碼:%s" % bob.owner(), encoding="utf-8")
    import nostr_keys as NK
    (site / "names.json").write_text(json.dumps({"bob": NK.npub_to_hex(bob.owner())}), encoding="utf-8")
    bob.cli("social", "add", "github", "bob-demo", "--proof", hubinfo["site"] + "/bob-proof", check=True)
    bob.cli("social", "add", "x", "@bob_demo", check=True)
    bob.cli("nip05", "bob@demo.local", check=True)
    for o in owners.values():
        o.start(pids)
        (d / "pids.json").write_text(json.dumps(pids))
    for o in owners.values():
        wait_for(lambda o=o: o.status().get("state") == "published" and (o.root / "heartbeat.json").exists(), 60,
                 "%s 的轉接程式上線" % o.key)
    andy, cara, dan, eve = owners["andy"], owners["cara"], owners["dan"], owners["eve"]
    a_addr = "%s/%s/%s" % (andy.owner(), andy.cab, andy.codes[0])
    b_addr = "%s/%s/%s" % (bob.owner(), bob.cab, bob.codes[0])
    # 關係
    andy.cli("friend", "add", b_addr, "--name", "Bob 客戶", check=True)
    bob.cli("friend", "add", a_addr, check=True)
    bob.cli("share", andy.owner(), "status", check=True)
    andy.cli("block", "add", eve.owner(), check=True)
    # 往來
    res = {"bob_dm": _send(bob, a_addr, bob.codes[0], "Andy 你好,下週的合作會議改到週三下午,可以嗎?"),
           "cara_dm": _send(cara, a_addr, cara.codes[0], "限時優惠!買一送一!"),
           "eve_dm": _send(eve, a_addr, eve.codes[0], "你為什麼封鎖我", wait=6)}
    r = dan.cli("request", a_addr, "--note", "我是 Dan,想讓我們的 AI 一起接案")
    res["dan_request"] = {"rc": r.returncode, "out": r.stdout.strip()}
    for k, o in owners.items():
        world["owners"][k] = {"owner": o.owner(), "cabin": o.cab, "seats": o.codes, "home": str(o.home), "root": str(o.root)}
    world["results"] = {k: (v if isinstance(v, dict) else v) for k, v in res.items()}
    (d / "world.json").write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
    # 給艦長室的環境
    env = andy.env(CAPTAIN_CHAT_BRIDGE=str(BRIDGE))
    keep = ("HOME", "AIB_CHAT_HOME", "AIB_CHAT_BRIDGE_DIR", "EZ2_AIB_STATE_ROOT", "AIB_CHAT_RELAYS", "EZ2_AIB_LOCALE",
            "CAPTAIN_CHAT_BRIDGE", "AIB_CHAT_VERIFY_ALLOW_LOCAL", "AIB_CHAT_NIP05_BASE", "PYTHONDONTWRITEBYTECODE")
    (d / "andy.env").write_text("".join("%s=%s\n" % (k, env[k]) for k in keep), encoding="utf-8")
    # 等在線狀態、名片與驗證都到
    wait_for(lambda: any(c["owner"] == bob.owner() and c["fresh"] for c in andy.json("world", "--json")["cabins"]), 60,
             "Bob 的在線狀態到 Andy")
    wait_for(lambda: any(c["owner"] == bob.owner() and any(s.get("verified") for s in c.get("socials") or [])
                         for c in andy.json("contacts", "--json")["contacts"]), 60, "Bob 的社群證明驗過")
    out.write("假世界好了:%s\n" % d)
    out.write("  中繼站 %s、證明站 %s\n" % (hubinfo["relay"], hubinfo["site"]))
    out.write("  Bob dm:%s;Cara dm:%s;Eve dm:%s;Dan 申請 rc %s\n" % (
        res["bob_dm"]["stage"], res["cara_dm"]["stage"], res["eve_dm"]["stage"], res["dan_request"]["rc"]))
    out.write("  艦長室接 Andy-demo:env $(cat %s) python3 <captain-room>/captain_room.py serve --port 8798 …\n" % (d / "andy.env"))
    out.write("  檢查:python3 %s check --dir %s;收掉:… down --dir %s\n" % (Path(__file__).resolve(), d, d))
    return 0


def check(d, out=sys.stdout):
    """核心這一半的斷言:Andy-demo 看到的通訊錄、世界、申請、拒收、佇列。回不過的項目數。"""
    d = Path(d).resolve()
    w = _load(d)
    hub = w["hub"]
    andy = Owner(d, "andy", hub, "")
    O = {k: v["owner"] for k, v in w["owners"].items()}
    bad = []

    def ok(cond, what):
        out.write(("✓ " if cond else "✗ ") + what + "\n")
        if not cond:
            bad.append(what)

    contacts = {c["owner"]: c for c in andy.json("contacts", "--json")["contacts"]}
    bob = contacts.get(O["bob"]) or {}
    ok(bob.get("relation") == "friend", "Bob 是好友")
    ok(bob.get("label") == "Bob 客戶" and bob.get("display_name") == "Bob", "Bob 的備註名與名冊名字")
    ok(bob.get("verified") is True and bob.get("human") is True, "Bob 有驗過的名冊、是人")
    ok((bob.get("nip05") or {}).get("verified") is True, "Bob 的 NIP-05 驗過")
    soc = {s["p"]: s.get("verified") for s in bob.get("socials") or []}
    ok(soc.get("github") is True and soc.get("x") is None, "Bob 的社群:github 驗過、x 自稱")
    ok(all(s.get("ai") for s in bob.get("seats") or []) and bool(bob.get("seats")), "Bob 的席都標 AI")
    ok((contacts.get(O["cara"]) or {}).get("relation") == "refused", "Cara 在拒收紀錄")
    ok(not (contacts.get(O["cara"]) or {}).get("socials"), "陌生人看不到社群")
    dan = contacts.get(O["dan"]) or {}
    ok(dan.get("relation") == "request_in" and "Dan" in ((dan.get("request") or {}).get("note") or ""), "Dan 的申請在等你同意")
    ok((contacts.get(O["eve"]) or {}).get("relation") == "blocked", "Eve 被封鎖")
    world = {c["owner"]: c for c in andy.json("world", "--json")["cabins"]}
    wb = world.get(O["bob"]) or {}
    ok(wb.get("fresh") is True and (wb.get("seats") or [{}])[0].get("state") in ("online", "idle"), "世界:Bob 在線")
    ok("示範" in ((wb.get("seats") or [{}])[0].get("now") or ""), "世界:Bob 分享了 📌 狀態列")
    ok(O["cara"] not in world and O["eve"] not in world, "世界:陌生 / 封鎖的不出現")
    reqs = andy.json("requests", "--json")["requests"]
    ok([r["owner"] for r in reqs] == [O["dan"]], "申請分頁只有 Dan")
    refused = andy.json("refused", "--json")["refused"]
    ok(any(r["who"] == O["cara"] for r in refused) and not any(r["who"] == O["eve"] for r in refused),
       "拒收紀錄有 Cara、沒有 Eve(封鎖是靜默)")
    r = subprocess.run([sys.executable, str(COCKPIT / "dm_queue.py"), "list", "--seat", w["owners"]["andy"]["seats"][0],
                        "--tab", "all"], capture_output=True, text=True, env=andy.env(), timeout=60)
    rows = json.loads(r.stdout or "[]")
    g = [x.get("global") or {} for x in rows if x.get("global")]
    ok(len(g) == 1 and g[0].get("contact_key") == "global:" + O["bob"] and g[0].get("relation") == "friend",
       "Andy 的佇列只有 Bob 那一封(contact_key、relation 對)")
    res = w["results"]
    ok(res["bob_dm"]["rc"] == 0 and res["cara_dm"]["stage"] == "refused" and res["eve_dm"]["rc"] == 4
       and res["dan_request"]["rc"] == 3, "寄件端:Bob rc 0、Cara refused、Eve 沒回條、Dan 等同意")
    out.write("%s(%d 項沒過)\n" % ("全部通過" if not bad else "有沒過的", len(bad)))
    return len(bad)


def down(d, rm=False, out=sys.stdout):
    d = Path(d).resolve()
    try:
        pids = json.loads((d / "pids.json").read_text())
    except (OSError, ValueError):
        pids = []
    for pid in pids:                  # 都是 start_new_session 起的 ⇒ 整組收(只收我們記下的,不寬比對)
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1)
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        (d / "pids.json").unlink()
    except FileNotFoundError:
        pass
    if rm:
        shutil.rmtree(str(d), ignore_errors=True)
    out.write("沙盒已停(%d 個行程組)%s\n" % (len(pids), ",資料夾已刪" if rm else ""))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="chat_e2e_world")
    p.add_argument("cmd", choices=("up", "check", "down", "_hub"))
    p.add_argument("--dir", required=True)
    p.add_argument("--venv", default=os.path.expanduser("~/.ez2-aib/chat/venv"))
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--rm", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "_hub":
        return serve_hub(a.dir, a.port) or 0
    if a.cmd == "up":
        try:
            return up(a.dir, a.venv)
        except Exception:
            down(a.dir)
            raise
    if a.cmd == "check":
        return 1 if check(a.dir) else 0
    return down(a.dir, a.rm)


if __name__ == "__main__":
    sys.exit(main())
