#!/usr/bin/env python3
"""who-owns 拆詞(10-16 @abb89 報:「LINE RichMenu」回找不到,Vault 有 7 篇寫 richmenu / 圖文選單)。

T1 拆詞:RichMenu ⇒ richmenu / rich menu / rich-menu / rich_menu;大小寫不分;虛詞與單字元不算;
   超過 4 字的中日韓長串不當詞
T2 全文:「LINE RichMenu」找得到寫 richmenu / Rich Menu / rich-menu 的筆記;最具體的詞一定要中;
   每個詞都中、查得到作者 ⇒ 0.55 建議重開;只中一部分 ⇒ 0.3 只列
T3 卡的正文:兩個詞都寫到 ⇒ 列為候選(0.4),不單靠正文建議重開
T4 卡的 scope_aliases 去空白 / 連字號再比:Rich Menu ↔ RichMenu;句子裡含「圖文選單」⇒ 命中、建議重開
T5 網域查詢照舊:中一個網域就算
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

UUID = "d1d63632-411b-416d-9cdc-10324fc0c105"
CARD = "---\nuid: \"{uid}\"\ncode: {code}\n{extra}---\n你是「{name}」。\n\n## 職責\n{body}\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-whotok-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.state, self.vault = self.td / "state", self.td / "vault"
        self.saved = {k: os.environ.get(k) for k in ("HOME", "EZ2_AIB_HOME", "EZ2_AIB_STATE_ROOT", "EZ2_VAULT_DIR",
                                                    "EZ2_VAULT_ROOT", "EZ2_AIB_LOCALE")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in self.saved.items()])
        os.environ.update({"HOME": str(self.td), "EZ2_AIB_HOME": str(self.state), "EZ2_AIB_STATE_ROOT": str(self.state),
                           "EZ2_VAULT_DIR": str(self.vault), "EZ2_AIB_LOCALE": "en"})
        os.environ.pop("EZ2_VAULT_ROOT", None)
        self.cards = {}
        self.card("fek1wt", "linemenu", "LINE 圖文選單工程師", "負責 LINE 官方帳號的圖文選單(rich menu)換版與上線。")
        self.card("urzvdk", "aibdev", "AIB工程師", "網站、LINE、WordPress 只在派到時做(line-richmenu-swap 等技能)。")
        seats = self.state / "instances" / "20261014-133230-6ddf" / "seats"
        seats.mkdir(parents=True)
        (seats / "w9.json").write_text(json.dumps({"seat_id": "seat-line", "session_uuid": UUID, "provider": "claude",
                                                   "mode": "dgrs", "role": "dev", "last_used_epoch": 1}), encoding="utf-8")
        cont = self.state / "identity" / "seat-continuity"
        cont.mkdir(parents=True)
        (cont / "seat-line.json").write_text(json.dumps({"seat_id": "seat-line", "persona_uid": "fek1wt",
                                                         "session_uuid": UUID, "updated_wall_ms": 1}), encoding="utf-8")
        import who_owns
        self.W = who_owns

    def card(self, uid, code, name, body, extra=""):
        d = self.vault / "fld_p_group_aaaaaaaaaaaa" / uid
        d.mkdir(parents=True, exist_ok=True)
        (d / "角色.md").write_text(CARD.format(uid=uid, code=code, name=name, body=body, extra=extra), encoding="utf-8")
        self.cards[uid] = {"card_path_rel": "fld_p_group_aaaaaaaaaaaa/%s/角色.md" % uid, "display_name": name, "code": code}
        (self.state / "identity").mkdir(parents=True, exist_ok=True)
        (self.state / "identity" / "cards.json").write_text(json.dumps({"cards": self.cards}, ensure_ascii=False),
                                                            encoding="utf-8")

    def note(self, uid, body, persona=""):
        d = self.vault / "fld_c_group_bbbbbbbbbbbb"
        d.mkdir(parents=True, exist_ok=True)
        fm = "---\nuid: %s\ntitle: \"筆記 %s\"\n%s---\n" % (uid, uid, ("custom.aib.persona_uid: %s\n" % persona) if persona else "")
        (d / ("%s.md" % uid)).write_text(fm + body + "\n", encoding="utf-8")


class TestTokens(Base):
    def test_T1_word_groups(self):
        g = self.W.word_groups("LINE RichMenu")
        self.assertEqual(g[0], ["line"])
        self.assertEqual(set(g[1]), {"richmenu", "rich menu", "rich-menu", "rich_menu"})
        self.assertEqual(self.W.word_groups("the A of SEO"), [["seo"]])
        self.assertEqual(self.W.word_groups("幫我看清展的 SEO 排名"), [["seo"], ["排名"]])
        self.assertEqual(self.W.squash("Rich-Menu  v2"), "richmenuv2")

    def test_T2_vault_full_text(self):
        self.note("3itb4a", "LINE 選單上線:RichMenu 換版完成", persona="fek1wt")
        self.note("3itb4l", "line 的 rich menu 計數轉址")
        self.note("3itatf", "rich-menu 退回")
        self.note("3zzzz1", "只有 LINE,沒有選單")
        res = self.W.who_owns("LINE RichMenu")
        vault = {m["uid"]: m for m in res["matches"] if m["kind"] == "vault"}
        self.assertEqual(set(vault), {"3itb4l"})                      # 3itatf 沒寫 LINE、3zzzz1 沒寫選單
        self.assertEqual(vault["3itb4l"]["score"], 0.3)                # 沒有作者 ⇒ 只列
        top = res["matches"][0]                                        # fek1wt 的卡寫到 + 它寫的 3itb4a 每個詞都中
        self.assertEqual((top["uid"], top["score"], top["field"], top["seat"]), ("fek1wt", 0.55, "vault", "6ddf/0c105"))
        ev = [e for e in top["evidence"] if e["source"] == "vault"][0]
        self.assertEqual(ev["line"], 6)                                   # frontmatter 5 行之後
        self.assertIn("RichMenu", ev["text"])
        self.assertEqual(res["verdict"], "reopen")
        self.card("urzvdk", "aibdev", "AIB工程師", "不相干的卡。")      # 沒有卡寫到時:筆記自己帶作者 ⇒ 0.55
        self.card("fek1wt", "linemenu", "LINE 圖文選單工程師", "負責官方帳號。")
        vault = {m["uid"]: m for m in self.W.who_owns("LINE RichMenu")["matches"] if m["kind"] == "vault"}
        self.assertEqual((vault["3itb4a"]["score"], vault["3itb4a"]["seat"]), (0.55, "6ddf/0c105"))
        res = self.W.who_owns("RichMenu")
        self.assertIn("3itatf", {m["uid"] for m in res["matches"] if m["kind"] == "vault"})

    def test_T3_card_body_is_candidate_only(self):
        res = self.W.who_owns("LINE RichMenu")
        body = {m["uid"]: m for m in res["matches"] if m["kind"] == "persona"}
        self.assertEqual(set(body), {"fek1wt", "urzvdk"})
        self.assertTrue(all(m["score"] == 0.4 and m["field"] == "body" for m in body.values()))
        self.assertEqual(res["verdict"], "new")

    def test_T4_scope_aliases_normalized(self):
        self.card("fek1wt", "linemenu", "LINE 圖文選單工程師", "負責 LINE 官方帳號。",
                  extra='scope_aliases: ["Rich Menu", "圖文選單"]\n')
        for q in ("LINE RichMenu", "line rich-menu", "幫我改 LINE 的圖文選單"):
            top = self.W.who_owns(q)["matches"][0]
            self.assertEqual((top["uid"], top["field"], top["score"], top["seat"]),
                             ("fek1wt", "scope_aliases", 0.9, "6ddf/0c105"), q)

    def test_T5_domain_query_unchanged(self):
        self.note("3dom01", "suntan.com.hk 上線", persona="fek1wt")
        res = self.W.who_owns("www.suntan.com.hk")
        self.assertEqual([m["uid"] for m in res["matches"] if m["kind"] == "vault"], ["3dom01"])


if __name__ == "__main__":
    unittest.main()
