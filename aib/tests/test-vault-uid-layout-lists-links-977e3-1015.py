#!/usr/bin/env python3
"""uid 版面轉換 10-15 彩排抓到的四件(@977e3;真庫副本:450 篇掉標籤、208 篇掉別名、Obsidian 解不開的連結 270 → 669)。

跑法:python3 tests/test-vault-uid-layout-lists-links-977e3-1015.py >log 2>&1; echo $?

釘的格:
  B1 frontmatter 區塊寫法(縮排「  - 值」、頂格「- 值」、帶引號)的 tags / aliases / legacy_paths 轉完一個不少。
  L1 正文 [[舊名]]、[[舊名|字]]、![[舊名]]、[[舊名#標題]]、[[資料夾/舊名]]、表格裡 [[舊名\\|字]] 改指 uid、原本的字留著;
     轉完每個連結用 Obsidian 的規則(檔名或路徑)都解得開;程式碼區塊與行內碼不動;同名多篇不改、plan 有警告。
  L2 路徑型與部分路徑(![[資料夾/圖.png]]、[[卡夾/角色]])跟著資料夾改路徑;角色卡本身不改,卡裡的連結數 plan 警告。
  O1 .obsidian 的日記/範本/附件/書籤設定改到新路徑;相對附件位置與 remotely-save 只出警告。
  U1 undo:正文 byte 回到轉換前、.obsidian 設定回原值、.ez2 狀態檔回到轉換前(原本沒有的刪掉、原本有的照原樣)。
  U2 undo 後再 plan → apply:uid 與路徑跟第一次完全相同,verify ok。
  F1–F5 審查抓到的五件:CRLF undo 照位置、無開頭 | 表格與引用框表格補 \\|、第二次 apply 0 改寫、清單註解/空行、帶語言名的圍欄。
  K1 刀:清單解析退回「只認單行」⇒ B1 的標籤會掉(證明 B1 有牙)。
  K2 刀:連結改寫拿掉 ⇒ L1 的連結解不開(證明 L1 有牙)。
治具全部在 tmpdir,不碰真 vault / ~/.ez2-aib。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
from aib_tmp_root import aib_tmp_root  # noqa: E402
import uid_layout_convert as U  # noqa: E402

TOOL = ROOT / "tools" / "vault" / "uid_layout_convert.py"
CONFIRM = "我已備份並同意轉成 uid 版面"
C80 = "C-Ez2 Core系統底層/C80-系統記憶交接"
INBOX = "00-暫存區 Inbox"
STUDIO = "03-製作區 Studio"
LIB = "P-Ez2 AI專家/P10-專家模板/角色庫"
CARD_UID = "ab12cd"
CARD_MD = '---\nuid: "%s"\ncode: tst\n---\n你是測試卡,開工先讀 [[範本一]]。\n' % CARD_UID
TPL_MD = "---\ntags:\n  - 日記\n---\n# {{date}}\n"
CONTRACT = {"routes": [
    {"routeKey": "core.handoff", "module": "C", "folder": C80, "aiReadable": True, "aiWritable": True},
    {"routeKey": "product.expert.agent_library", "module": "P", "folder": LIB, "aiReadable": True, "aiWritable": False},
]}
BLOCK_NOTE = """---
uid: 3cpog0
aliases:
  - 3cpog0
  - "📋 Dashboard"
  - ACB總覽
tags:
- project/acb
- "#dashboard"
- 'auto-generated'
legacy_paths:
  - 舊/位置.md
created: 2026-06-05T00:00:00
---
# ACB 專案總覽
"""
LINKS_BODY = """看 [[3cpog0-📋 Dashboard]] 與 [[3cpog0-📋 Dashboard|總覽]]。
嵌入 ![[3cpog0-📋 Dashboard]],標題 [[3cpog0-📋 Dashboard#快速狀態]]。
路徑 [[06-工作區 Work/ACB/3cpog0-📋 Dashboard]]、副檔名 [[範本一.md]]。
| 表 | [[範本一\\|範本]] |
同名 [[_說明]] 不改。圖 ![[06-工作區 Work/ACB/圖.png]]。
部分路徑 [[測試卡/角色]] 與 ![[ACB/圖.png]]。
行內 `[[範本一]]` 不改。
```
[[範本一]]
```
"""


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def make_vault(root: Path):
    (root / C80).mkdir(parents=True)
    (root / "06-工作區 Work" / "ACB").mkdir(parents=True)
    (root / INBOX).mkdir()
    (root / LIB / "測試卡").mkdir(parents=True)
    (root / LIB / "測試卡" / "角色.md").write_text(CARD_MD, encoding="utf-8")
    (root / STUDIO).mkdir()
    (root / "06-工作區 Work" / "ACB" / "3cpog0-📋 Dashboard.md").write_text(BLOCK_NOTE, encoding="utf-8")
    (root / "06-工作區 Work" / "ACB" / "圖.png").write_bytes(b"\x89PNG fake")
    (root / STUDIO / "範本一.md").write_text("範本內容\n", encoding="utf-8")
    (root / STUDIO / "範本").mkdir()
    (root / STUDIO / "範本" / "日記範本.md").write_text(TPL_MD, encoding="utf-8")
    (root / INBOX / "_說明.md").write_text("收件匣說明\n", encoding="utf-8")
    (root / STUDIO / "_說明.md").write_text("製作區說明\n", encoding="utf-8")
    (root / C80 / "連結大全.md").write_text("---\ntitle: 連結大全\n---\n" + LINKS_BODY, encoding="utf-8")
    cfg = root / ".obsidian"; (cfg / "plugins" / "remotely-save").mkdir(parents=True)
    (cfg / "plugins" / "remotely-save" / "manifest.json").write_text('{"id": "remotely-save"}', encoding="utf-8")
    (cfg / "daily-notes.json").write_text(json.dumps({"format": "x-HHmmss", "folder": INBOX, "template": STUDIO + "/範本/日記範本"}, ensure_ascii=False), encoding="utf-8")
    (cfg / "templates.json").write_text(json.dumps({"folder": STUDIO + "/範本"}, ensure_ascii=False), encoding="utf-8")
    (cfg / "app.json").write_text(json.dumps({"attachmentFolderPath": "./附件", "alwaysUpdateLinks": True}), encoding="utf-8")
    (cfg / "bookmarks.json").write_text(json.dumps({"items": [
        {"type": "file", "ctime": 1, "path": "06-工作區 Work/ACB/3cpog0-📋 Dashboard.md"},
        {"type": "group", "ctime": 2, "title": "組", "items": [{"type": "folder", "ctime": 3, "path": STUDIO}]},
        {"type": "search", "ctime": 4, "query": "x"}]}, ensure_ascii=False), encoding="utf-8")
    (root / ".ez2").mkdir()
    (root / ".ez2" / "migration-state.json").write_text(json.dumps({"mode": "legacy_names", "note": "轉換前就有"}), encoding="utf-8")


def links_in(text: str):
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    body = re.sub(r"`[^`]*`", "", body)
    return [m.group(1) for m in re.finditer(r"!?\[\[([^\[\]\n]+?)\]\]", body)]


def unresolved(vault: Path, text: str) -> list:
    """Obsidian 的規則(不看 aliases):目標是檔名(去 .md)、從庫根算的路徑、或尾端相符的部分路徑。"""
    names, paths = set(), set()
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            rel = str(Path(dp, f).relative_to(vault))
            names.add(f[:-3] if f.endswith(".md") else f)
            paths.add(rel[:-3] if rel.endswith(".md") else rel)
    bad = []
    for inner in links_in(text):
        t = re.split(r"\\?\||#", inner, maxsplit=1)[0].strip()
        t = t[:-3] if t.endswith(".md") else t
        if t and t not in names and t not in paths and not ("/" in t and any(x.endswith("/" + t) for x in paths)):
            bad.append(t)
    return bad


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(dir=aib_tmp_root())
        self.addCleanup(self._td.cleanup)
        self.t = Path(self._td.name)
        self.vault = self.t / "vault"; self.vault.mkdir()
        self.contract = self.t / "contract.json"
        self.contract.write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        make_vault(self.vault)
        env = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW", "EZ2_AIB_HOME": str(self.t / "aib")})
        env.start(); self.addCleanup(env.stop)

    def plan(self, name="plan.json"):
        out = self.t / name
        r = _run("plan", "--vault", str(self.vault), "--contract", str(self.contract), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return out, json.loads(out.read_text(encoding="utf-8"))

    def apply(self, plan_path, backup="backup"):
        r = _run("apply", "--plan", str(plan_path), "--backup-dir", str(self.t / backup), "--confirm-text", CONFIRM)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def new_rel(self, plan, old_rel):
        return next(n["new_rel"] for n in plan["notes"] if n["old_rel"] == old_rel)


class ListsAndLinks(Fixture):
    def test_B1_block_lists_kept(self):
        _, plan = self.plan()
        self.apply(self.t / "plan.json")
        text = (self.vault / self.new_rel(plan, "06-工作區 Work/ACB/3cpog0-📋 Dashboard.md")).read_text(encoding="utf-8")
        fm, _ = U.split_frontmatter(text)
        tags, aliases = U.fm_list(fm, "tags"), U.fm_list(fm, "aliases")
        for t in ("project/acb", "#dashboard", "auto-generated"):
            self.assertIn(t, tags)
        for a in ("📋 Dashboard", "ACB總覽", "3cpog0"):
            self.assertIn(a, aliases)
        self.assertIn("舊/位置.md", U.fm_list(fm, "legacy_paths"))
        self.assertNotRegex(fm, r"(?m)^\s*-\s", "舊的區塊清單行不得殘留在新 frontmatter 裡")

    def test_L1_L2_links_rewritten_and_resolve(self):
        p, plan = self.plan()
        self.assertGreaterEqual(plan["stats"]["links_rewritten"], 7)
        self.assertEqual(plan["stats"]["links_ambiguous"], 1)
        self.assertEqual(plan["stats"]["links_in_cards_not_rewritten"], 1)
        self.assertTrue(any("1 處連結" in w for w in plan["warnings"]), plan["warnings"])
        card_sha = _sha(self.vault / LIB / "測試卡" / "角色.md")
        self.assertTrue(any("_說明" in w for w in plan["warnings"]), plan["warnings"])
        before = (self.vault / C80 / "連結大全.md").read_text(encoding="utf-8")
        self.assertEqual(unresolved(self.vault, before), [], "治具:轉換前每個連結都解得開")
        self.apply(p)
        dash = self.new_rel(plan, "06-工作區 Work/ACB/3cpog0-📋 Dashboard.md")
        tpl = Path(self.new_rel(plan, STUDIO + "/範本一.md")).stem
        text = (self.vault / self.new_rel(plan, C80 + "/連結大全.md")).read_text(encoding="utf-8")
        self.assertEqual(unresolved(self.vault, text), ["_說明"], "轉完每個連結都要照檔名解得開(同名多篇那個本來就不改)")
        self.assertIn("[[3cpog0|3cpog0-📋 Dashboard]]", text)
        self.assertIn("[[3cpog0|總覽]]", text)
        self.assertIn("![[3cpog0]]", text)
        self.assertIn("[[3cpog0#快速狀態|3cpog0-📋 Dashboard#快速狀態]]", text)
        self.assertIn("[[3cpog0|06-工作區 Work/ACB/3cpog0-📋 Dashboard]]", text)
        self.assertIn("[[%s|範本一.md]]" % tpl, text)
        self.assertIn("| 表 | [[%s\\|範本]] |" % tpl, text)
        self.assertIn("[[_說明]]", text)
        self.assertIn("![[%s/圖.png]]" % str(Path(dash).parent), text)
        self.assertTrue((self.vault / Path(dash).parent / "圖.png").is_file())
        lib_new = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == LIB)
        self.assertIn("[[%s/%s/角色|測試卡/角色]]" % (lib_new, CARD_UID), text)
        self.assertIn("![[%s/圖.png]]" % str(Path(dash).parent), text.split("部分路徑", 1)[1])
        self.assertEqual(_sha(self.vault / lib_new / CARD_UID / "角色.md"), card_sha, "角色卡一 byte 不改")
        self.assertIn("`[[範本一]]`", text)
        self.assertIn("```\n[[範本一]]\n```", text)

    def test_O1_obsidian_settings(self):
        p, plan = self.plan()
        self.assertTrue(any("./附件" in w for w in plan["warnings"]))
        self.assertTrue(any("remotely-save" in w for w in plan["warnings"]))
        self.assertTrue(any("範本" in w and "uid" in w for w in plan["warnings"]), plan["warnings"])
        self.apply(p)
        cfg = self.vault / ".obsidian"
        studio = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == STUDIO)
        inbox = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == INBOX)
        dn = json.loads((cfg / "daily-notes.json").read_text(encoding="utf-8"))
        self.assertEqual(dn["folder"], inbox)
        self.assertEqual(dn["template"], studio + "/範本/日記範本", "範本夾名字不動、跟著上層搬")
        self.assertEqual(dn["format"], "x-HHmmss")
        self.assertEqual(json.loads((cfg / "templates.json").read_text(encoding="utf-8"))["folder"], studio + "/範本")
        self.assertEqual((self.vault / studio / "範本" / "日記範本.md").read_text(encoding="utf-8"), TPL_MD, "範本不補 uid、不改名")
        self.assertEqual(plan["stats"]["notes_template_kept"], 1)
        self.assertEqual(_run("verify", "--vault", str(self.vault)).returncode, 0, "verify 認得範本")
        self.assertEqual(json.loads((cfg / "app.json").read_text(encoding="utf-8"))["attachmentFolderPath"], "./附件")
        bm = json.loads((cfg / "bookmarks.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual(bm[0]["path"], self.new_rel(plan, "06-工作區 Work/ACB/3cpog0-📋 Dashboard.md"))
        self.assertEqual(bm[1]["items"][0]["path"], studio)
        self.assertEqual(bm[2]["query"], "x")
        for p_ in (bm[0]["path"], bm[1]["items"][0]["path"], dn["folder"], dn["template"] + ".md"):
            self.assertTrue((self.vault / p_).exists(), p_)

    def test_U1_U2_undo_restores_then_reapply_same(self):
        body_before = (self.vault / C80 / "連結大全.md").read_text(encoding="utf-8").split("---\n", 2)[2]
        cfg_before = {f: _sha(self.vault / ".obsidian" / f) for f in ("daily-notes.json", "templates.json", "bookmarks.json")}
        ms_before = (self.vault / ".ez2" / "migration-state.json").read_bytes()
        p, plan = self.plan()
        self.apply(p)
        rec = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))[-1]
        r = _run("undo", "--receipt", str(rec))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["links_fuzzy"], 0)
        self.assertEqual(out["links_restored"], plan["stats"]["links_rewritten"])
        text = (self.vault / C80 / "連結大全.md").read_text(encoding="utf-8")
        self.assertEqual(text.split("---\n", 2)[2], body_before, "正文要 byte 回到轉換前")
        for f, sha in cfg_before.items():
            self.assertEqual(_sha(self.vault / ".obsidian" / f), sha, f)
        self.assertEqual((self.vault / ".ez2" / "migration-state.json").read_bytes(), ms_before, "migration-state 要回到轉換前")
        for rel in (".ez2/index/folder-id-index.json", ".ez2/index/uid-index.json", ".ez2/display/labels.json"):
            self.assertFalse((self.vault / rel).exists(), "轉換前沒有的索引 undo 後要拿掉:" + rel)
        # 再轉一次:uid 與路徑跟第一次一樣,verify ok
        p2, plan2 = self.plan("plan2.json")
        self.assertEqual({n["uid"]: n["new_rel"] for n in plan2["notes"]}, {n["uid"]: n["new_rel"] for n in plan["notes"]})
        self.apply(p2, "backup2")
        r = _run("verify", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stdout)
        text = (self.vault / self.new_rel(plan, C80 + "/連結大全.md")).read_text(encoding="utf-8")
        self.assertEqual(unresolved(self.vault, text), ["_說明"])

    def test_U3_undo_without_prior_state_removes_migration_state(self):
        (self.vault / ".ez2" / "migration-state.json").unlink()
        p, _ = self.plan()
        self.apply(p)
        rec = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))[-1]
        self.assertEqual(_run("undo", "--receipt", str(rec)).returncode, 0)
        self.assertFalse((self.vault / ".ez2" / "migration-state.json").exists())


class ReviewFixes(Fixture):
    """子代理審查抓到的五件(10-15):CRLF undo、沒開頭 | 的表格與引用框表格、第二次 apply 不再改、區塊清單裡的註解與空行、帶語言名的圍欄不算收尾。"""

    def _lr(self):
        plan = U.build_plan(self.vault, str(self.contract), None, None)
        return U.LinkRewriter(self.vault, plan["notes"], plan["folders"], plan["drops"], plan["kept_md"])

    def test_F1_crlf_undo_exact(self):
        f = self.vault / C80 / "crlf.md"
        raw = "---\r\ntitle: x\r\n---\r\nsee [[範本一]] and [[範本一|n]]\r\nline2 [[03-製作區 Studio/範本一]]\r\n".encode("utf-8")
        f.write_bytes(raw)
        p, plan = self.plan()
        self.apply(p)
        rec = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))[-1]
        out = json.loads(_run("undo", "--receipt", str(rec)).stdout)
        self.assertEqual(out["links_fuzzy"], 0)
        body = f.read_bytes().split(b"---\r\n", 2)[-1] if b"---\r\n" in f.read_bytes()[4:] else f.read_bytes().split(b"\n---\n", 1)[-1]
        self.assertIn(b"see [[\xe7\xaf\x84\xe6\x9c\xac\xe4\xb8\x80]] and", body)
        self.assertTrue(body.endswith("line2 [[03-製作區 Studio/範本一]]\r\n".encode("utf-8")), body)

    def test_F2_tables_without_leading_pipe_and_in_callout(self):
        lr = self._lr()
        uid = Path(next(n["new_rel"] for n in U.build_plan(self.vault, str(self.contract), None, None)["notes"] if n["old_rel"].endswith("範本一.md"))).stem
        src = ("a | b\n--- | ---\n[[範本一]] | 2\n\n"
               "> [!note]\n> | h | i |\n> |---|---|\n> | [[範本一]] | x |\n\n"
               "平常 [[範本一]],選 a | b 也不是表格\n")
        out, _, _ = lr.rewrite(src)
        self.assertIn("[[%s\\|範本一]] | 2" % uid, out)
        self.assertIn("> | [[%s\\|範本一]] | x |" % uid, out)
        self.assertIn("平常 [[%s|範本一]],選 a | b" % uid, out, "段落裡有 | 但不是表格 ⇒ 不用 \\|")

    def test_F3_second_apply_rewrites_nothing(self):
        p, plan = self.plan()
        self.apply(p)
        tpl = self.new_rel(plan, STUDIO + "/範本一.md")           # 轉完後用戶照新路徑寫的連結
        (self.vault / Path(tpl).parent / "新寫的.md").write_text("[[%s]] ![[%s]]\n" % (tpl[:-3], tpl), encoding="utf-8")
        _, plan2 = self.plan("plan2.json")
        self.assertEqual(plan2["stats"]["links_rewritten"], 0)

    def test_F4_block_list_comment_and_blank_lines(self):
        fm = "tags: # 註解\n  - a\n\n  # 中間註解\n  - b\nnext: 1"
        self.assertEqual(U.fm_list(fm, "tags"), ["a", "b"])
        self.assertEqual(U.strip_managed(fm), ["next: 1"])
        self.assertEqual(U.fm_list("tags: #idea\nx: 1", "tags"), ["#idea"], "單行 #標籤 不是註解起頭")
        self.assertEqual(U.strip_managed("tags:\n  - a\n\n# --- 我的段落 ---\nproject: x"), ["", "# --- 我的段落 ---", "project: x"],
                         "清單後面用戶自己的空行與註解要留著")

    def test_F6_partial_path_to_unmoved_file_untouched(self):
        p, plan = self.plan()
        self.apply(p)
        dash_dir = str(Path(self.new_rel(plan, "06-工作區 Work/ACB/3cpog0-📋 Dashboard.md")).parent)
        part = dash_dir.split("/", 1)[1] if "/" in dash_dir else dash_dir
        (self.vault / dash_dir / "部分.md").write_text("![[%s/圖.png]] ![[%s/圖.png]]\n" % (part, part.upper()), encoding="utf-8")
        _, plan2 = self.plan("plan2.json")
        self.assertEqual(plan2["stats"]["links_rewritten"], 0, "對到的檔沒搬 ⇒ 部分路徑、大小寫不同都照原樣")

    def test_F5_fence_with_info_string_does_not_close(self):
        lr = self._lr()
        src = "```\n[[範本一]]\n```js\n[[範本一]]\n```\n"
        out, edits, _ = lr.rewrite(src)
        self.assertEqual(out, src)
        self.assertEqual(edits, [])


class CardsAndTemplates(Fixture):
    """10-15 the maintainer 裁:角色卡連結明講才改(--rewrite-card-links)、範本排除。"""

    def test_C1_rewrite_card_links_opt_in_and_undo(self):
        out = self.t / "plan.json"
        r = _run("plan", "--vault", str(self.vault), "--contract", str(self.contract), "--out", str(out), "--rewrite-card-links")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(plan["stats"]["links_in_cards_rewritten"], 1)
        self.assertEqual(plan["stats"]["links_in_cards_not_rewritten"], 0)
        self.assertTrue(any("卡已更新" in w for w in plan["warnings"]), plan["warnings"])
        card_before = (self.vault / LIB / "測試卡" / "角色.md").read_bytes()
        self.apply(out)
        lib_new = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == LIB)
        tpl = Path(self.new_rel(plan, STUDIO + "/範本一.md")).stem
        card = (self.vault / lib_new / CARD_UID / "角色.md").read_text(encoding="utf-8")
        self.assertIn("開工先讀 [[%s|範本一]]" % tpl, card)
        self.assertTrue(card.startswith('---\nuid: "%s"\ncode: tst\n---\n' % CARD_UID), "卡的 frontmatter 一字不動")
        self.assertEqual(unresolved(self.vault, card), [])
        rec = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))[-1]
        self.assertEqual(_run("undo", "--receipt", str(rec)).returncode, 0)
        self.assertEqual((self.vault / LIB / "測試卡" / "角色.md").read_bytes(), card_before, "undo 後卡片 byte 回原樣")

    def test_T1_daily_template_file_outside_template_folder(self):
        cfg = self.vault / ".obsidian"
        (cfg / "templates.json").unlink()
        (self.vault / INBOX / "碎片範本.md").write_text(TPL_MD, encoding="utf-8")
        (cfg / "zk-prefixer.json").write_text(json.dumps({"folder": INBOX, "template": INBOX + "/碎片範本.md"}, ensure_ascii=False), encoding="utf-8")
        p, plan = self.plan()
        self.assertFalse(any(n["old_rel"].endswith("碎片範本.md") for n in plan["notes"]))
        self.apply(p)
        inbox = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == INBOX)
        self.assertEqual((self.vault / inbox / "碎片範本.md").read_text(encoding="utf-8"), TPL_MD)
        self.assertEqual(json.loads((cfg / "zk-prefixer.json").read_text(encoding="utf-8"))["template"], inbox + "/碎片範本.md")
        self.assertEqual(_run("verify", "--vault", str(self.vault)).returncode, 0)


class TemplateEdges(Fixture):
    """三審(1563cf4)抓到:範本正文連結沒改、範本夾蓋到路由夾時路由被吃掉。"""

    def test_T2_template_body_links_rewritten_and_undone(self):
        tpl = self.vault / STUDIO / "範本" / "週報範本.md"
        raw = "---\ntags: [週報]\n---\n參考 [[範本一]]\n"
        tpl.write_text(raw, encoding="utf-8")
        p, plan = self.plan()
        self.assertEqual(plan["stats"]["links_in_templates_rewritten"], 1)
        self.apply(p)
        studio = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == STUDIO)
        uid = Path(self.new_rel(plan, STUDIO + "/範本一.md")).stem
        text = (self.vault / studio / "範本" / "週報範本.md").read_text(encoding="utf-8")
        self.assertEqual(text, "---\ntags: [週報]\n---\n參考 [[%s|範本一]]\n" % uid, "範本 frontmatter 不動、正文連結改指 uid")
        rec = sorted((self.vault / ".ez2" / "migration").glob("uid-layout-receipt-*.json"))[-1]
        self.assertEqual(_run("undo", "--receipt", str(rec)).returncode, 0)
        self.assertEqual(tpl.read_text(encoding="utf-8"), raw)

    def test_T3_template_folder_covering_route_is_blocked(self):
        (self.vault / ".obsidian" / "templates.json").write_text(json.dumps({"folder": "C-Ez2 Core系統底層"}, ensure_ascii=False), encoding="utf-8")
        out = self.t / "plan.json"
        r = _run("plan", "--vault", str(self.vault), "--contract", str(self.contract), "--out", str(out))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        plan = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(plan["blockers"][0]["template_dir"], "C-Ez2 Core系統底層")
        self.assertIn(C80, plan["blockers"][0]["covers"])
        self.assertTrue(any(f["kind"] == "route" for f in plan["folders"] if f["old_rel"] == C80), "路由夾照常拿 folderId")
        r = _run("apply", "--plan", str(out), "--backup-dir", str(self.t / "backup"), "--confirm-text", CONFIRM)
        self.assertNotEqual(r.returncode, 0, "有 blocker 不准 apply")


class TemplateInLibrary(Fixture):
    def test_T4_template_dir_inside_library_does_not_touch_cards(self):
        (self.vault / ".obsidian" / "templates.json").write_text(json.dumps({"folder": LIB + "/測試卡"}, ensure_ascii=False), encoding="utf-8")
        card_before = (self.vault / LIB / "測試卡" / "角色.md").read_bytes()
        p, plan = self.plan()
        self.assertEqual([t for t in plan["template_files"] if t.startswith(LIB + "/")], [], "角色庫裡的檔不當範本")
        self.assertEqual(plan["stats"]["links_in_templates_rewritten"], 0)
        self.apply(p)
        lib_new = next(f["new_rel"] for f in plan["folders"] if f["old_rel"] == LIB)
        self.assertEqual((self.vault / lib_new / CARD_UID / "角色.md").read_bytes(), card_before, "沒帶 --rewrite-card-links 卡一 byte 不改")


class Knives(Fixture):
    """刀:把修正拿掉,上面的格就該紅(在這裡用相反的斷言證明那幾格有牙)。"""

    def test_K1_single_line_only_parser_drops_tags(self):
        def old_fm_list(fm, key):                       # 修正前:只認單行 [..]
            return U.parse_inline_list(U.fm_lines_to_map(fm).get(key, "")) if fm else []
        with mock.patch.object(U, "fm_list", old_fm_list):
            plan = U.build_plan(self.vault, str(self.contract), None, None)
        n = next(n for n in plan["notes"] if n["old_rel"].endswith("3cpog0-📋 Dashboard.md"))
        self.assertNotIn("project/acb", n["fm_set"]["tags"], "刀下標籤會掉 —— B1 的 assertIn 會紅")
        self.assertNotIn("ACB總覽", n["fm_set"]["aliases"])

    def test_K2_no_rewrite_leaves_links_broken(self):
        p, plan = self.plan()
        with mock.patch.object(U.LinkRewriter, "rewrite", lambda self, text: (text, [], [])):
            U.apply_plan(json.loads(p.read_text(encoding="utf-8")), self.t / "backup", CONFIRM)
        text = (self.vault / self.new_rel(plan, C80 + "/連結大全.md")).read_text(encoding="utf-8")
        self.assertGreater(len(unresolved(self.vault, text)), 1, "刀下連結解不開 —— L1 的 assertEqual 會紅")


if __name__ == "__main__":
    unittest.main(verbosity=2)
