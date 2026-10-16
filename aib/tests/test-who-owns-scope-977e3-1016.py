#!/usr/bin/env python3
"""範圍外導引:aib who-owns / aib scope(10-16 維護者裁;介面與 @ee0bb 對過)。

W1 @ee0bb 驗收三案:suntan.com.hk 命中 scope_domains、www.suntan.com.hk 去 www 後命中、
   jurassicmuseum.com.tw 不屬 @ee0bb(建議新建、離開碼 1)
W2 子網域算命中上層網域;依據帶 frontmatter 行號;重開指令 = 分出新席(AIB_FORK_FROM_SESSION_UUID)
W3 專案卡 scope 關鍵字出現在一句話裡 ⇒ 命中專案,席位從 project-seats 反查
W4 Vault 全文:查得到作者的筆記 ⇒ 0.55 建議重開;同作者併一筆;查不到作者 ⇒ 0.3 只列不建議
W5 一句話含卡名大半個詞 ⇒ 列為候選但不建議重開;沒填範圍的舊卡不壞
W6 CLI:--json 欄位、離開碼 0 / 1 / 2(空查詢)
S1 scope set 人格:只動 scope_* 幾行(其他 frontmatter 與正文一個位元組不變)、預設加入、--replace 換掉、
   網域正規化、壞網域擋下不寫、改前快照、記進 tidy 寫入紀錄、重掃卡片
S2 scope set 專案:其他欄位保留、show 讀得回來
S3 讀 frontmatter 的清單:flow 與 block 兩種寫法
CLI 子行程走 run_guarded;一律暫存 HOME / 狀態根 / Vault,不碰真 ~/.ez2-aib 與真 Vault。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "identity"))
import aib_testlib  # noqa: E402,F401
from aib_proc_guard import run_guarded  # noqa: E402

SUNTAN_UUID = "604e5b2e-6e91-4418-b75c-c7c6e9eee0bb"
PM_UUID = "11111111-2222-3333-4444-5555555ab123"
CARD = """---
uid: "{uid}"
code: {code}
category: 營運
scope_domains: {domains}
---
你是「{name}」。

## 職責
1. 管{what}。
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-whoowns-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.state = self.td / "state"
        self.vault = self.td / "vault"
        self.saved = {k: os.environ.get(k) for k in ("HOME", "EZ2_AIB_HOME", "EZ2_AIB_STATE_ROOT", "EZ2_VAULT_DIR",
                                                    "EZ2_VAULT_ROOT", "EZ2_AIB_LOCALE")}
        os.environ.update({"HOME": str(self.td / "home"), "EZ2_AIB_HOME": str(self.state),
                           "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_VAULT_DIR": str(self.vault),
                           "EZ2_AIB_LOCALE": "en"})
        os.environ.pop("EZ2_VAULT_ROOT", None)
        self.addCleanup(self.restore_env)
        (self.td / "home").mkdir()
        lib = self.vault / "fld_p_group_aaaaaaaaaaaa"
        self.cards = {}
        self.add_card("cbk1z2", "eu01", "EU01將能工程師", '["suntan.com.hk", "jn-ec.com"]', "將能 46 站", lib)
        self.add_card("f990bc", "qzseo", "清展SEO主管", "[]", "清展的 SEO", lib)
        self.write_cards()
        seats = self.state / "instances" / "20261014-133230-6ddf" / "seats"
        seats.mkdir(parents=True)
        (seats / "w5.json").write_text(json.dumps({
            "seat_id": "seat-ee", "session_uuid": SUNTAN_UUID, "provider": "claude", "mode": "dgrs", "role": "dev",
            "last_used_epoch": 1792000000}), encoding="utf-8")
        (seats / "retired").mkdir()
        (seats / "retired" / "w9.json").write_text(json.dumps({
            "seat_id": "seat-pm", "session_uuid": PM_UUID, "provider": "codex", "mode": "safe", "role": "pm",
            "last_used_epoch": 1791000000}), encoding="utf-8")
        cont = self.state / "identity" / "seat-continuity"
        cont.mkdir(parents=True)
        (cont / "seat-ee.json").write_text(json.dumps({
            "seat_id": "seat-ee", "persona_uid": "cbk1z2", "session_uuid": SUNTAN_UUID,
            "updated_wall_ms": 1792000000000}), encoding="utf-8")
        proj = self.state / "projects" / "proj:0123456789abcdef"
        proj.mkdir(parents=True)
        self.proj_card = proj / "card.json"
        self.proj_card.write_text(json.dumps({
            "schema": "aib-project-card/v1", "project_uid": "proj:0123456789abcdef", "title": "清展紗窗網站",
            "route_key": "x.y", "layers": {"constitution": {"human_rulings": ["keep"]}},
            "scope": {"keywords": ["紗窗", "清展"], "sites": [], "domains": ["qz-screen.com"], "aliases": []}},
            ensure_ascii=False), encoding="utf-8")
        (self.state / "state").mkdir(parents=True)
        (self.state / "state" / "project-seats.json").write_text(json.dumps({
            "schema": "aib-project-seats/v1", "projects": {"proj:0123456789abcdef": {"seats": {
                "seat-pm": {"since": "2026-10-10T10:00:00+08:00", "by": "x"}}}}}), encoding="utf-8")
        import who_owns
        self.W = who_owns

    def restore_env(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def add_card(self, uid, code, name, domains, what, lib):
        d = lib / uid
        d.mkdir(parents=True)
        (d / "角色.md").write_text(CARD.format(uid=uid, code=code, name=name, domains=domains, what=what), encoding="utf-8")
        self.cards[uid] = {"card_path_rel": str((d / "角色.md").relative_to(self.vault)), "display_name": name, "code": code}

    def write_cards(self):
        (self.state / "identity").mkdir(parents=True, exist_ok=True)
        (self.state / "identity" / "cards.json").write_text(
            json.dumps({"schema_version": 1, "cards": self.cards}, ensure_ascii=False), encoding="utf-8")

    def note(self, uid, body, persona=""):
        d = self.vault / "fld_c_group_bbbbbbbbbbbb"
        d.mkdir(parents=True, exist_ok=True)
        fm = "---\nuid: %s\ntitle: \"筆記 %s\"\n%s---\n" % (uid, uid, ("custom.aib.persona_uid: %s\n" % persona) if persona else "")
        (d / ("%s.md" % uid)).write_text(fm + body + "\n", encoding="utf-8")

    def cli(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIB_", "TMUX")) and k != "PYTHONPATH"}
        env.update({"LANG": "en_US.UTF-8", "XDG_CACHE_HOME": str(self.td / "cache"), "AIB_GENES": "0",
                    "EZ2_COCKPIT_STATE_DIR": str(self.td / "cockpit")})
        r = run_guarded([sys.executable, str(ROOT / "src" / "identity" / "who_owns.py"), *args],
                        env=env, timeout=120, max_procs=40, stdin=__import__("subprocess").DEVNULL)
        self.assertIsNone(r.tripped)
        return r


class TestWhoOwns(Base):
    def test_W1_ee0bb_acceptance(self):
        for q in ("suntan.com.hk", "www.suntan.com.hk", "https://WWW.Suntan.com.hk/zh/"):
            res = self.W.who_owns(q)
            self.assertEqual(res["verdict"], "reopen", q)
            top = res["matches"][0]
            self.assertEqual((top["kind"], top["uid"], top["field"], top["score"]),
                             ("persona", "cbk1z2", "scope_domains", 1.0), q)
            self.assertEqual((top["seat"], top["owner"]), ("6ddf/ee0bb", "persona:cbk1z2"))
        res = self.W.who_owns("jurassicmuseum.com.tw")
        self.assertEqual(res["verdict"], "new")
        self.assertNotIn("cbk1z2", [m["uid"] for m in res["matches"]])
        self.assertEqual(res["new_cmd"], 'aib newproj --title "jurassicmuseum.com.tw"')

    def test_W2_subdomain_evidence_reopen_cmd(self):
        top = self.W.who_owns("shop.suntan.com.hk")["matches"][0]
        self.assertEqual((top["uid"], top["score"]), ("cbk1z2", 0.95))
        ev = top["evidence"][0]
        self.assertEqual((ev["source"], ev["line"], ev["field"]), ("card", 5, "scope_domains"))
        self.assertIn("suntan.com.hk", ev["text"])
        self.assertTrue(ev["path"].endswith("cbk1z2/角色.md"))
        self.assertEqual(top["reopen_cmd"], "AIB_FORK_FROM_SESSION_UUID=%s aib seat open claude dgrs dev" % SUNTAN_UUID)
        self.assertTrue(top["last_bound"])

    def test_W3_project_keyword_in_sentence(self):
        res = self.W.who_owns("客戶問紗窗下單頁打不開")
        top = res["matches"][0]
        self.assertEqual((res["verdict"], top["kind"], top["uid"], top["field"]), ("reopen", "project", "proj:0123456789abcdef", "scope.keywords"))
        self.assertEqual(top["seat"], "6ddf/ab123")                              # 已收的席也反查得到
        self.assertEqual(top["reopen_cmd"], "AIB_FORK_FROM_SESSION_UUID=%s aib seat open codex safe pm" % PM_UUID)
        top = self.W.who_owns("qz-screen.com")["matches"][0]
        self.assertEqual((top["kind"], top["field"], top["score"]), ("project", "scope.domains", 1.0))

    def test_W4_vault_full_text(self):
        self.note("3aaaa1", "10-15 shop.example.org 上線完成", persona="cbk1z2")
        self.note("3aaaa2", "另一篇也提到 shop.example.org", persona="cbk1z2")
        self.note("3aaaa3", "沒有作者的 other.example.net 筆記")
        res = self.W.who_owns("shop.example.org")
        self.assertEqual(res["verdict"], "reopen")
        vault = [m for m in res["matches"] if m["kind"] == "vault"]
        self.assertEqual(len(vault), 1)                                          # 同作者併一筆
        self.assertEqual((vault[0]["score"], vault[0]["seat"], vault[0]["owner"]), (0.55, "6ddf/ee0bb", "persona:cbk1z2"))
        self.assertEqual(len(vault[0]["evidence"]), 2)
        self.assertEqual(vault[0]["evidence"][0]["line"], 6)                    # frontmatter 5 行之後
        res = self.W.who_owns("other.example.net")
        self.assertEqual(res["verdict"], "new")
        self.assertEqual([(m["kind"], m["score"], m["seat"]) for m in res["matches"]], [("vault", 0.3, None)])

    def test_W5_name_tokens_and_old_cards(self):
        res = self.W.who_owns("幫我看清展的 SEO 排名")
        cand = [m for m in res["matches"] if m["uid"] == "f990bc"]
        self.assertEqual(len(cand), 1)
        self.assertLess(cand[0]["score"], self.W.REOPEN_AT)
        self.cards["old000"] = {"card_path_rel": "nope/角色.md", "display_name": "沒有檔的舊卡", "code": "old"}
        self.write_cards()
        self.assertEqual(self.W.who_owns("suntan.com.hk")["matches"][0]["uid"], "cbk1z2")

    def test_W6_cli_json_and_exit_codes(self):
        r = self.cli("who-owns", "--json", "www.suntan.com.hk")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(set(d), {"version", "query", "verdict", "matches", "new_cmd", "new_role_cmd"})
        self.assertEqual(set(d["matches"][0]) >= {"kind", "uid", "name", "seat", "score", "field", "evidence",
                                                  "last_bound", "reopen_cmd", "owner"}, True)
        self.assertTrue(all(m["owner"] for m in d["matches"] if m["kind"] != "vault"))
        r = self.cli("who-owns", "jurassicmuseum.com.tw")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("aib newproj", r.stdout)
        r = self.cli("who-owns", " ")
        self.assertEqual(r.returncode, 2)


class TestScope(Base):
    def card_text(self, uid):
        return (self.vault / self.cards[uid]["card_path_rel"]).read_text(encoding="utf-8")

    def test_S1_persona_set(self):
        before = self.card_text("f990bc")
        res = self.W.scope_set(persona="f990bc", new={"domains": ["https://www.QZ-Screen.com/", "qz-screen.com"],
                                                      "keywords": ["紗窗"]}, rescan=lambda: 0)
        self.assertTrue(res["changed"])
        after = self.card_text("f990bc")
        self.assertEqual(self.W.read_fm_scope(after)["domains"], ["qz-screen.com"])
        self.assertEqual(self.W.read_fm_scope(after)["keywords"], ["紗窗"])
        keep = lambda t: [l for l in t.splitlines() if not l.startswith("scope_")]       # noqa: E731
        self.assertEqual(keep(before), keep(after))                                      # 其他行與正文不變
        self.assertEqual(before.split("---", 2)[2], after.split("---", 2)[2])
        self.W.scope_set(persona="f990bc", new={"keywords": ["清展"]}, rescan=lambda: 0)
        self.assertEqual(self.W.read_fm_scope(self.card_text("f990bc"))["keywords"], ["紗窗", "清展"])
        self.W.scope_set(persona="f990bc", new={"keywords": ["窗簾"]}, replace=True, rescan=lambda: 0)
        sc = self.W.read_fm_scope(self.card_text("f990bc"))
        self.assertEqual((sc["keywords"], sc["domains"]), (["窗簾"], ["qz-screen.com"]))
        archive = list((self.state).rglob("*f990bc*"))
        self.assertTrue(any("archive" in str(p) for p in archive), archive)               # 改前快照
        writes = (self.state / "state" / "tidy" / "writes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(writes), 3)
        snap = self.card_text("f990bc")
        with self.assertRaises(self.W.WhoOwnsError):
            self.W.scope_set(persona="f990bc", new={"domains": ["not a domain"]}, rescan=lambda: 0)
        self.assertEqual(snap, self.card_text("f990bc"))
        res = self.W.scope_set(persona="f990bc", new={"keywords": ["窗簾"]}, rescan=lambda: 0)
        self.assertFalse(res["changed"])
        top = self.W.who_owns("www.qz-screen.com")["matches"]
        self.assertEqual({m["uid"] for m in top if m["score"] == 1.0}, {"f990bc", "proj:0123456789abcdef"})

    def test_S2_project_set_and_show(self):
        self.W.scope_set(project="proj:0123456789abcdef", new={"sites": ["清展官網"]})
        raw = json.loads(self.proj_card.read_text(encoding="utf-8"))
        self.assertEqual(raw["scope"]["sites"], ["清展官網"])
        self.assertEqual(raw["scope"]["keywords"], ["紗窗", "清展"])
        self.assertEqual((raw["route_key"], raw["layers"]), ("x.y", {"constitution": {"human_rulings": ["keep"]}}))
        self.assertEqual(self.W.scope_show(project="proj:0123456789abcdef")["scope"]["sites"], ["清展官網"])
        with self.assertRaises(self.W.WhoOwnsError):
            self.W.scope_show(project="proj:ffffffffffffffff")
        r = self.cli("scope", "set", "--persona", "nope00", "--domains", "a.com")
        self.assertEqual(r.returncode, 2)

    def test_S3_read_block_and_flow_lists(self):
        text = "---\nuid: x\nscope_domains:\n  - a.com\n  - \"b.com\"\nscope_keywords: [甲, \"乙\"]\nother: 1\n---\nbody\n"
        sc = self.W.read_fm_scope(text)
        self.assertEqual((sc["domains"], sc["keywords"], sc["sites"]), (["a.com", "b.com"], ["甲", "乙"], []))
        out = self.W.write_fm_scope(text, dict(sc, domains=["c.com"]))
        self.assertIn('scope_domains: ["c.com"]', out)
        self.assertNotIn("a.com", out)
        self.assertIn("other: 1", out)
        self.assertTrue(out.endswith("---\nbody\n"))
        with self.assertRaises(self.W.WhoOwnsError):
            self.W.write_fm_scope("no frontmatter", sc)


if __name__ == "__main__":
    unittest.main()
