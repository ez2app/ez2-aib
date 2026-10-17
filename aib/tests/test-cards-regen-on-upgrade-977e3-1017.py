#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""升級後角色卡清單 cards.json 自動變新(10-17 @abb89 裁,@977e3 做)。治具全在 tmpdir,不碰真 vault / ~/.ez2-aib。

10-17 實量:升到 0.1.17 後艙裡 cards.json 還是舊程式產的(57 張 card_updated 空),要手動 scan 才吃到 #126 ⇒
用戶會以為沒修好。裁:升級收尾(install.sh / aib update)與 aib doctor 偵測到「舊程式產的」就直接重產並回報。

「舊程式產的」= 收據的產生器雜湊不是現在這支、沒人手改、卡沒增減、每張卡檔 persona_sha 都一樣(只是算法變了)。

U1 舊程式產的 cards.json(card_updated 全空、收據產生器是舊的)⇒ refresh 重產:card_updated 補上、收據換成現在這支、check rc 0
U2 aib doctor(子行程、--state-root 指暫存)⇒ cards_projection ok、說已重產、檔案真的變新
U3 角色庫改過(某張卡檔變了)⇒ stale、檔案位元組不變;doctor 只列(note)
U4 有人手改 cards.json ⇒ edited_by_hand、不蓋;doctor fail
U5 產生器跟現在同一支但有落差(不是升級造成)⇒ stale、不蓋
U6 還沒產過 ⇒ absent、不建檔;U7 已是新的 ⇒ current、位元組不變
U8 install.sh:版號真的變了才設 UPGRADED=1,refresh 只在 UPGRADED=1 時跑、在 aib init 之後
I1 新鍵四語都有、佔位符一致
刀(複本,走 run_guarded):K1 不看產生器(有落差就蓋)⇒ U5 紅;K2 不看 persona_sha ⇒ U3 紅;K3 doctor 不跑這一項 ⇒ U2 紅
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "memory"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import card_registry as CR  # noqa: E402

DOCTOR = ROOT / "src" / "bootstrap" / "aib_doctor.py"
INSTALL = ROOT.parent / "install.sh"
LANGS = ("zh-TW", "en", "ja", "ko")
CARD = '---\nuid: "%s"\ncode: t%s\nname: "%s"\nupdated: 2026-10-17T08:40:42.123Z\n---\n你是測試。\n'


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aib-cardregen-")
        self.t = Path(self._tmp.name)
        self.vault = self.t / "Vault"
        self.lib = self.vault / "10-Roles"
        for i, uid in enumerate(("abc001", "abc002")):
            (self.lib / uid).mkdir(parents=True)
            (self.lib / uid / "角色.md").write_text(CARD % (uid, i, "測試員%d" % i), encoding="utf-8")
        self.contract = self.t / "contract.json"
        self.contract.write_text(json.dumps({"routes": [{"routeKey": CR.LIBRARY_ROUTE_KEY, "folder": "10-Roles"}]}),
                                 encoding="utf-8")
        self.state = self.t / "state"
        (self.state / "identity").mkdir(parents=True)
        self.out = self.state / "identity" / "cards.json"
        self.env = dict(os.environ, EZ2_VAULT_DIR=str(self.vault), EZ2_ROUTE_REGISTRY=str(self.contract), EZ2_ROUTE_CONTRACT_SPEC=str(self.contract),
                        EZ2_AIB_STATE_ROOT=str(self.state), HOME=str(self.t))

    def tearDown(self):
        self._tmp.cleanup()

    def project(self):
        return CR.project(str(self.vault), str(self.contract))

    def write_old(self, generator="0" * 64, blank=True):
        """模擬舊程式產的那份:card_updated 讀不到(全空),收據的產生器雜湊是別支。"""
        _v, _l, doc, sources = self.project()
        if blank:
            for c in doc["cards"].values():
                c["card_updated"] = None
        sources["generator"] = {"path": "/old/card_registry.py", "sha256": generator}
        CR.write_registry(self.out, doc, sources)
        return self.out.read_bytes()

    def refresh(self):
        return CR.refresh(self.out, str(self.vault), str(self.contract))

    def doctor(self):
        p = subprocess.run([sys.executable, str(DOCTOR), "--json", "--state-root", str(self.state),
                            "--provider", "claude"], capture_output=True, text=True, timeout=120, env=self.env,
                           stdin=subprocess.DEVNULL)
        checks = json.loads(p.stdout)["checks"]
        found = [c for c in checks if c["id"] == "cards_projection"]
        self.assertEqual(len(found), 1, p.stderr[-500:])
        return found[0]


class Refresh(Base):
    def test_u1_old_program_output_is_regenerated(self):
        self.write_old()
        res = self.refresh()
        self.assertEqual(res["status"], "refreshed", res)
        self.assertEqual(res["changed_fields"], ["card_updated"])
        cards = json.loads(self.out.read_text(encoding="utf-8"))["cards"]
        self.assertEqual({c["card_updated"] for c in cards.values()}, {"2026-10-17T08:40:42.123Z"})
        receipt = json.loads(CR.provenance_path(self.out).read_text(encoding="utf-8"))
        self.assertEqual(receipt["sources"]["generator"]["sha256"], CR._digest_file(Path(CR.__file__).resolve()))
        p = subprocess.run([sys.executable, CR.__file__, "check", "--vault", str(self.vault), "--contract",
                            str(self.contract), "--out", str(self.out)], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.refresh()["status"], "current")

    def test_u3_library_edited_is_not_overwritten(self):
        before = self.write_old()
        card = self.lib / "abc001" / "角色.md"
        card.write_text(card.read_text(encoding="utf-8") + "多一行。\n", encoding="utf-8")
        self.assertEqual(self.refresh()["status"], "stale")
        self.assertEqual(self.out.read_bytes(), before)

    def test_u4_hand_edit_is_not_overwritten(self):
        self.write_old()
        text = self.out.read_text(encoding="utf-8").replace("測試員0", "手改的")
        self.out.write_text(text, encoding="utf-8")
        self.assertEqual(self.refresh()["status"], "edited_by_hand")
        self.assertEqual(self.out.read_text(encoding="utf-8"), text)

    def test_u5_same_generator_drift_is_not_overwritten(self):
        before = self.write_old(generator=CR._digest_file(Path(CR.__file__).resolve()))
        self.assertEqual(self.refresh()["status"], "stale")
        self.assertEqual(self.out.read_bytes(), before)

    def test_u6_absent_is_not_created(self):
        self.assertEqual(self.refresh()["status"], "absent")
        self.assertFalse(self.out.exists())
        self.assertFalse(CR.provenance_path(self.out).exists())

    def test_u7_current_is_untouched(self):
        before = self.write_old(generator=CR._digest_file(Path(CR.__file__).resolve()), blank=False)
        self.assertEqual(self.refresh()["status"], "current")
        self.assertEqual(self.out.read_bytes(), before)


class Doctor(Base):
    def test_u2_doctor_regenerates_and_says_so(self):
        self.write_old()
        c = self.doctor()
        self.assertEqual(c["status"], "ok", c)
        self.assertIn("card_updated", c["detail"])
        cards = json.loads(self.out.read_text(encoding="utf-8"))["cards"]
        self.assertNotIn(None, [x["card_updated"] for x in cards.values()])

    def test_u3b_doctor_only_lists_library_edit(self):
        before = self.write_old()
        card = self.lib / "abc002" / "角色.md"
        card.write_text(card.read_text(encoding="utf-8") + "多一行。\n", encoding="utf-8")
        c = self.doctor()
        self.assertEqual(c["status"], "note", c)
        self.assertIn("scan", c["remedy"])
        self.assertEqual(self.out.read_bytes(), before)

    def test_u4b_doctor_fails_on_hand_edit(self):
        self.write_old()
        self.out.write_text(self.out.read_text(encoding="utf-8").replace("測試員1", "手改的"), encoding="utf-8")
        self.assertEqual(self.doctor()["status"], "fail")


class Install(unittest.TestCase):
    @unittest.skipUnless(INSTALL.is_file(), "install.sh is at the repo root")
    def test_u8_install_only_after_version_change(self):
        s = INSTALL.read_text(encoding="utf-8")
        self.assertEqual(subprocess.run(["bash", "-n", str(INSTALL)]).returncode, 0)
        self.assertEqual(len(re.findall(r"UPGRADED=1", s)), 1)
        self.assertRegex(s, r'\[ "\$ver" = "\$ver_before" \] \|\| UPGRADED=1')
        block = s[s.index('if [ "$UPGRADED" = 1 ]; then'):]
        self.assertIn("card_registry.py", block[:800])
        self.assertIn(" refresh ", block[:800])
        self.assertLess(s.index('"$abin" init'), s.index('if [ "$UPGRADED" = 1 ]; then'))
        self.assertNotIn("card_registry.py\" refresh", s[:s.index('if [ "$UPGRADED" = 1 ]; then')])


class Keys(unittest.TestCase):
    def test_i1_keys(self):
        d = {lang: json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8")) for lang in LANGS}
        keys = [k for k in d["zh-TW"] if k.startswith("aib.doctor.check.cards_projection.")]
        self.assertEqual(len(keys), 7)
        for k in keys:
            holes = {lang: set(re.findall(r"\{\w+\}", d[lang].get(k, "?"))) for lang in LANGS}
            self.assertEqual(len({frozenset(h) for h in holes.values()}), 1, (k, holes))
            for lang in LANGS:
                self.assertIn(k, d[lang], (lang, k))


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_ignore_generator(self):
        self._knife("src/memory/card_registry.py", 'if not same_cards or old_generator == sources["generator"]["sha256"]:',
                    "if not same_cards:", "Refresh.test_u5_same_generator_drift_is_not_overwritten")

    def test_k2_ignore_persona_sha(self):
        self._knife("src/memory/card_registry.py",
                    'old[u].get("persona_sha") == doc["cards"][u].get("persona_sha") for u in cmp_["changed"])',
                    'True for u in cmp_["changed"])', "Refresh.test_u3_library_edited_is_not_overwritten")

    def test_k3_doctor_skips_check(self):
        self._knife("src/bootstrap/aib_doctor.py", "    checks.append(check_cards_projection(state_root, repo_root))\n",
                    "", "Doctor.test_u2_doctor_regenerates_and_says_so")


if __name__ == "__main__":
    unittest.main(verbosity=2)
