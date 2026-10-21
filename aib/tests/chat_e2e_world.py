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

OWNERS = [  # (key, 名, 國, 艙, [(視窗, uuid, 人格 uid, 席名, 角色, AI 家, emoji, 📌)])  第一席 = 艦長(role root,Andy 10:5x)
    ("andy", "Andy-demo", "TW", "a0d1", [
        ("w2", "00000000-0000-4000-8000-00000000a0a1", "andy01", "Andy 艦長", "root", "claude", "👑", "看今天的線上來信"),
        ("w3", "00000000-0000-4000-8000-00000000a0a2", "andy02", "工程席", "dev", "codex", "🛠️", "修艦長室通訊錄")]),
    ("bob", "Bob", "JP", "b0b1", [
        ("w2", "00000000-0000-4000-8000-00000000b0b1", "bob001", "Bob 艦長", "root", "claude", "🧭", "整理客戶回信"),
        ("w3", "00000000-0000-4000-8000-00000000b0b2", "bob002", "業務席", "dev", "claude", "💼", "準備週三提案")]),
    ("cara", "Cara", "US", "c0c1", [("w2", "00000000-0000-4000-8000-00000000c0c1", "cara01", "Cara 艦長", "root", "claude", "📣", "群發廣告")]),
    ("dan", "Dan", "KR", "d0d1", [("w2", "00000000-0000-4000-8000-00000000d0d1", "dan001", "Dan 艦長", "root", "claude", "🤝", "找合作夥伴")]),
    ("eve", "Eve", "SG", "e0e1", [("w2", "00000000-0000-4000-8000-00000000e0e1", "eve001", "Eve 艦長", "root", "claude", "🌀", "到處留言")]),
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
    last = 0.0
    while not stop.is_set():
        if time.time() - last >= 60:          # 模擬真艙的狀態列定時重畫:每分鐘替每位假主人的席跳一次心跳
            last = time.time()
            for beat in Path(d).glob("*/home/.ez2-aib/state/seat-now/*.beat"):
                try:
                    rec = json.loads(beat.read_text(encoding="utf-8"))
                    rec["at"] = int(time.time())
                    beat.write_text(json.dumps(rec), encoding="utf-8")
                except (OSError, ValueError):
                    pass
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
                    "AIB_CHAT_PRESENCE_TICK": "2", "AIB_CHAT_PRESENCE_GAP": "2", "AIB_CHAT_PRESENCE_EVERY": "20", "AIB_CHAT_ROOM_GAP": "5",
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

    def init(self):
        """真的 aib init(0.1.15 的家目錄骨架:secrets、identity、instances…)。"""
        r = subprocess.run([sys.executable, str(REPO.parent / "bin" / "aib"), "init"], capture_output=True, text=True,
                           env=self.env(), timeout=120)
        if r.returncode != 0:
            raise RuntimeError("%s: aib init ⇒ rc %d\n%s%s" % (self.key, r.returncode, r.stdout, r.stderr))

    def seat(self, inst, idx, win, uuid, uid, name, role, provider, emoji):
        """照 0.1.15 真的格式寫一席:席位檔、人格卡索引(cards.json aib-cards/v2)、綁定、席位顯示。"""
        code = uuid.replace("-", "")[-5:]
        now = int(time.time())
        sd = self.home / ".ez2-aib" / "instances" / inst / "seats"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / ("%s.json" % win)).write_text(json.dumps({
            "born_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "cabin_id": inst, "fleet_id": "f-" + inst,
            "cwd": str(self.home), "last_used_epoch": now, "mode": "dgrs", "model_requested": "claude-opus-5-5" if provider == "claude" else provider,
            "pid": 0, "project_title": "線上對話示範", "project_uid": "proj:e2e0demo0000000", "provider": provider, "role": role,
            "schema": "aib-seat/v1", "seat_id": "%05x" % (0x10000 + idx), "session_uuid": uuid, "socket": "aihub-" + inst,
            "started_epoch": now, "trust": "self_reported", "window_id": "@%d" % (idx + 2)}, ensure_ascii=False))
        ident = self.home / ".ez2-aib" / "identity"
        ident.mkdir(parents=True, exist_ok=True)

        def upd(fn, default, mutate):
            f = ident / fn
            cur = json.loads(f.read_text(encoding="utf-8")) if f.exists() else default
            mutate(cur)
            f.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        sha = "%064x" % (int(uid.encode().hex(), 16) % (1 << 255))
        upd("cards.json", {"cards": {}, "generated_wall_ms": now * 1000, "schema_version": "aib-cards/v2", "skipped": [],
                           "source": {"library_folder": "e2e", "library_route_key": "product.expert.agent_library",
                                      "vault_root": str(self.home / "Vault")}},
            lambda c: c["cards"].__setitem__(uid, {"uid": uid, "code": uid, "display_name": name, "emoji": emoji,
                                                   "category": "示範/" + self.key, "card_ref": "P10/" + name,
                                                   "card_path_rel": "e2e/%s/角色.md" % uid, "card_updated": None,
                                                   "default_route_key": None, "dims": [], "persona_sha": sha}))
        upd("bindings.json", {"bindings": {}, "schema_version": "0.1.0-draft"},
            lambda b: b["bindings"].__setitem__(code, {"persona_uid": uid, "route_key": "core.handoff"}))
        upd("seat-display.json", {"display": {}},
            lambda d: d["display"].__setitem__(code, {"persona_uid": uid, "display_name": name, "persona_sha": sha,
                                                      "bound_persona_sha": sha, "brain_binding_status": "bound",
                                                      "brain_status_text": "大腦已綁", "folder": "e2e/" + uid,
                                                      "persona_drift": False, "persona_ref_legacy": False,
                                                      "persona_source": "cards", "route_key": "core.handoff"}))
        return code

    def now(self, code, item, state="busy", done=2, total=5):
        """📌 狀態列用真的 seat_now.write;心跳照狀態列定時重畫的格式寫(.beat)。"""
        import seat_now as SN
        root = str(self.home / ".ez2-aib")
        SN.write(code, item, done, total, state=state, state_root=root)
        t = int(time.time())
        (Path(SN.now_dir(root)) / (code + ".beat")).write_text(json.dumps({"at": t, "src": "statusline", "code": code}),
                                                                encoding="utf-8")

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
        o.init()
        inst = "20261017-000000-%s" % cab
        codes = [o.seat(inst, n, w, u, uid, nm, role, prov, emo) for n, (w, u, uid, nm, role, prov, emo, _pin) in enumerate(seats)]
        o.cli("on", "--no-venv", "--name", name, "--country", cc, check=True)
        for c, row in zip(codes, seats):
            o.cli("publish", c, check=True)
            o.now(c, "示範:" + row[7])
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
    import chat_roster as CR                                    # 10-21:改分享等級要本人 Touch ID(門在 CLI),這裡直接走函式
    CR.set_share("status", andy.owner(), bob.env())             # 在線分享到 📌 狀態列
    CR.set_space("room", andy.owner(), bob.env())               # 第 34 節 v2:Bob 讓 Andy 走進他的島與艦長室
    (bob.chat / "island.json").write_text(json.dumps({          # 艦長室寫的島(故意夾禁止欄位與超出範圍的值測過濾)
        "v": 1, "name": "Bob 港", "size": {"w": 24, "h": 16}, "age": 2, "res": {"wood": 30, "stone": 12},
        "owner": {"name": "Bob", "avatar": {"emoji": "🧔", "bg": "#2a6"}},
        "buildings": [{"cabin": bob.cab, "x": 6, "y": 5, "style": "港口木造", "kind": "hall", "lv": 2},
                      {"cabin": bob.cab, "x": 18, "y": 8, "kind": "dock", "lv": 1, "building": {"start": 1792200000, "done": 1792286400}},
                      {"cabin": bob.cab, "x": 99, "y": 99, "kind": "far"}],
        "decor": [{"kind": "tree", "x": 1, "y": 1}], "terrain": {"sea": "east", "seed": 4217}, "ip": "192.168.1.9",
        # 包 K(第 36 節):風格、寵物、地形格子、建築外觀、掛單
        "style": "cn", "pets": [{"kind": "cat", "lv": 3}, {"kind": "Bad Kind", "lv": 1}],
        "offers": [{"id": "0a1b2c3d", "item": "plank", "n": 5, "unit": 3, "at": 1792200000},
                   {"id": "deadbeef", "item": "brick", "n": 1, "unit": 9, "at": 1792200000}]}, ensure_ascii=False),
        encoding="utf-8")
    isl = json.loads((bob.chat / "island.json").read_text(encoding="utf-8"))
    isl["terrain"]["grid"] = {"v": 1, "rle": "g200s100w84"}                       # 24 × 16 = 384 格
    isl["buildings"][0]["look"] = {"base": "cn", "wall": "stone", "roof": "eave", "sign": "food",
                                   "c": {"wall": "#AA3322", "roof": "#224488", "accent": "nope"}}
    (bob.chat / "island.json").write_text(json.dumps(isl, ensure_ascii=False), encoding="utf-8")
    bob.cli("offer", "host", "on", check=True)                    # 第 36.2 節:Bob 這台是掛單機
    (bob.chat / "room.json").write_text(json.dumps({            # 艦長室寫的房間資料(故意夾禁止欄位測過濾)
        "v": 1, "ship": {"name": "Bob 商會號", "sign": "BOB TRADING", "line": "歡迎來談合作", "ip": "192.168.1.9"},
        "cabins": [{"cabin": bob.cab, "floors": [{"id": "1F", "name": "大廳"}, {"id": "2F", "name": "業務部"}],
                    "style": "港口木造", "host": "bob-mbp"}],
        "seats": {bob.codes[0]: {"floor": "1F", "lv": 5, "stars": 2, "ctx": 81},
                  bob.codes[1]: {"floor": "2F", "lv": 3, "stars": 1, "unread": 4}},
        "layout": {"tiles": [[0, 1, 1, 0], [1, 1, 1, 1]], "theme": "harbor"}}, ensure_ascii=False), encoding="utf-8")
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
    wait_for(lambda: andy.cli("room", bob.owner(), "--json").returncode == 0, 60, "Bob 的空間快照(島 + 艦長室)到 Andy")
    wait_for(lambda: any(c["owner"] == bob.owner() and any(s.get("verified") for s in c.get("socials") or [])
                         for c in andy.json("contacts", "--json")["contacts"]), 60, "Bob 的社群證明驗過")
    wait_for(lambda: len(andy.json("offers", bob.owner(), "--json")["offers"]) == 2, 60, "Bob 的掛單到 Andy")
    # 掛單往來(第 36.2 節):成交、改價退回、搶最後一份(Bob 的核心一筆一筆在帳本鎖底下決定)
    buys = {"ok": andy.json("offer", "buy", bob.owner(), "0a1b2c3d", "2", "--unit", "3", "--json")["trade"]["nonce"],
            "price": andy.json("offer", "buy", bob.owner(), "0a1b2c3d", "1", "--unit", "2", "--json")["trade"]["nonce"],
            "race1": andy.json("offer", "buy", bob.owner(), "deadbeef", "1", "--unit", "9", "--json")["trade"]["nonce"],
            "race2": andy.json("offer", "buy", bob.owner(), "deadbeef", "1", "--unit", "9", "--json")["trade"]["nonce"]}
    wait_for(lambda: all(t["status"] != "pending" for t in andy.json("offer", "list", "--json")["trades"]), 60, "Bob 回覆 4 筆買單")
    world["results"]["buys"] = buys
    (d / "world.json").write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
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
    room = json.loads(andy.cli("room", O["bob"], "--json").stdout or "null") or {}
    ok((room.get("ship") or {}).get("name") == "Bob 商會號" and room.get("fresh") is True, "走進 Bob 的艦長室:艦名、新鮮")
    rs = {x["seat"]: x for x in room.get("seats") or []}
    bs = w["owners"]["bob"]["seats"]
    ok(set(rs) == set(bs) and rs.get(bs[0], {}).get("emoji") == "🧭" and rs.get(bs[0], {}).get("role") == "root"
       and rs.get(bs[1], {}).get("floor") == "2F" and rs.get(bs[0], {}).get("lv") == 5, "Bob 的兩席:emoji、角色、樓層、Lv 都在")
    blob = json.dumps(room, ensure_ascii=False)
    ok(all(k not in blob for k in ('"ip"', '"host"', '"ctx"', '"unread"', "192.168.1.9", "bob-mbp")), "房間快照沒有 IP / 主機名 / CTX / 未讀")
    ok(andy.cli("room", O["cara"], "--json").returncode != 0 and andy.cli("room", O["eve"], "--json").returncode != 0,
       "陌生 / 封鎖的沒有艦長室可看")
    isl = json.loads(andy.cli("island", O["bob"], "--json").stdout or "null") or {}
    ii = isl.get("island") or {}
    ok(ii.get("name") == "Bob 港" and (ii.get("owner") or {}).get("name") == "Bob" and ii.get("size") == {"w": 24, "h": 16},
       "走到 Bob 的島:島名、島主、大小")
    ok([b.get("kind") for b in ii.get("buildings") or []] == ["hall", "dock"] and "building" in (ii.get("buildings") or [{}, {}])[1],
       "島上建築:兩棟(島外那棟濾掉)、建造中那棟有時間")
    ok(ii.get("age") == 2 and ii.get("res") == {"wood": 30, "stone": 12} and "192.168.1.9" not in json.dumps(isl),
       "島的 age / res 照轉、沒有 IP")
    ok(isl.get("has_room") is True and len(isl.get("people") or []) == 2, "島上走動的人 = Bob 的兩席,有艦長室可進")
    wcab = world.get(O["bob"]) or {}
    ok((wcab.get("space") or {}).get("room") is True and (wcab.get("space") or {}).get("fresh") is True,
       "世界格標出 Bob 有島與艦長室可走進去")
    ok(andy.cli("island", O["cara"], "--json").returncode != 0, "陌生人的島看不到")
    ok(ii.get("style") == "cn" and ii.get("pets") == [{"kind": "cat", "lv": 3}]
       and (ii.get("terrain") or {}).get("grid") == {"v": 1, "rle": "g200s100w84"}, "島的風格、寵物(壞的濾掉)、地形格子")
    lk = (ii.get("buildings") or [{}])[0].get("look") or {}
    ok(lk.get("c") == {"wall": "#aa3322", "roof": "#224488"} and lk.get("sign") == "food", "建築外觀:顏色存小寫、壞色濾掉")
    offers = {o["id"]: o for o in andy.json("offers", O["bob"], "--json")["offers"]}
    ok(set(offers) == {"0a1b2c3d", "deadbeef"} and offers["0a1b2c3d"]["unit"] == 3, "Bob 島上的兩筆掛單")
    buys = w["results"].get("buys") or {}
    mine = {t["nonce"]: t for t in andy.json("offer", "list", "--json")["trades"] if t["role"] == "buyer"}
    st = {k: (mine.get(v) or {}).get("status") for k, v in buys.items()}
    ok(st.get("ok") == "ok" and (mine.get(buys.get("ok")) or {}).get("total") == 6, "買 2 個木板:成交、總價 6")
    ok(st.get("price") == "reject" and (mine.get(buys.get("price")) or {}).get("why") == "price_changed", "單價不對:退回 price_changed")
    ok(sorted([st.get("race1"), st.get("race2")]) == ["ok", "reject"], "搶最後一塊磚:一筆成交、一筆退回(不超賣)")
    bob_o = Owner(d, "bob", hub, "")
    bl = {o["id"]: o for o in bob_o.json("offer", "list", "--json")["offers"]}
    ok((bl.get("0a1b2c3d") or {}).get("left") == 3 and (bl.get("deadbeef") or {}).get("left") == 0, "Bob 的帳:木板剩 3、磚賣完")
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
    inst = "20261017-000000-%s" % w["owners"]["andy"]["cabin"]
    seat_files = sorted((Path(w["owners"]["andy"]["home"]) / ".ez2-aib" / "instances" / inst / "seats").glob("w*.json"))
    roles = [json.loads(f.read_text(encoding="utf-8")).get("role") for f in seat_files]
    ok(roles[:1] == ["root"] and roles.count("root") == 1, "Andy-demo 的艦長席(role root)恰好一席,信寄進艦長席")
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
