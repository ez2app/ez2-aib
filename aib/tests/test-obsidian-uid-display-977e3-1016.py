#!/usr/bin/env python3
"""Obsidian 顯示層外掛(extensions/obsidian-uid-display)— 10-16 維護者裁。

uid 版面的庫在 Obsidian 檔案樹只看得到 fld_… / <uid>,認不出來。外掛只改畫面:
資料夾顯示 .ez2/display/labels.json 的 title,筆記顯示 frontmatter title(沒有就原名),不寫任何檔。

D1 labels 解析 + 巢狀路徑取最後一段的 folder id
D2 labels.json 缺檔 / 壞 JSON / 不是物件 ⇒ null,不丟例外
D3 frontmatter 缺 title / 空字串 / 數字 / 沒 frontmatter
D4 i18n 欄位預留:指定語言有值用它,null 退回 title
D5 外掛載入(假 obsidian + 假 DOM):檔案樹、分頁換成標題;沒標籤的夾不動;卸載後全部還原
D6 缺 labels.json 載入:資料夾維持 id、筆記照樣顯示 title、不報錯
D7 靜態:外掛原始碼沒有任何寫檔 / 改名 API、沒寫死路徑;manifest 合法
D8 排序:照顯示的標題排(數字自然序)、資料夾在前、反向、照時間排時不動
D9 外掛接上檔案樹的 getSortedFolderItems:載入後照標題排、卸載後換回原本的函式並重排
刀測走 run_guarded(子行程 node)。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from aib_proc_guard import run_guarded  # noqa: E402

REPO = HERE.parent.parent
PLUGIN = REPO / "extensions" / "obsidian-uid-display"
MAIN = PLUGIN / "main.js"
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)

LABELS = {
    "schema": "2026-10-05.ez2-display-labels",
    "folders": {
        "fld_x_group_aaaa": {"title": "00-暫存區 Inbox", "i18n": {"zh-TW": "00-暫存區 Inbox", "en": None, "ja": "受信箱", "ko": None}},
        "fld_x_sub_bbbb": {"title": "WordPress-SEO", "i18n": {"zh-TW": "WordPress-SEO", "en": None, "ja": None, "ko": None}},
    },
    "notes": {"3dhgg0": {"title": "總覽", "path": "fld_x_group_aaaa/3dhgg0.md"}},
}

# 假 obsidian 模組 + 假 DOM:只做外掛用到的那幾個 API
FAKE_HARNESS = r"""
const Module = require("module");
const origLoad = Module._load;
class Plugin { constructor(app) { this.app = app; this._ev = []; this._iv = []; }
  registerEvent(e) { this._ev.push(e); } registerInterval(i) { this._iv.push(i); return i; } }
Module._load = function (req, ...rest) { return req === "obsidian" ? { Plugin } : origLoad.call(this, req, ...rest); };

class El { constructor(text) { this.textContent = text; this.dataset = {}; this.isConnected = true; } }
class Row { constructor(cls, path, text) { this.cls = cls; this.path = path; this.inner = new El(text); }
  getAttribute(k) { return k === "data-path" ? this.path : null; }
  querySelector(sel) { return sel === this.cls + "-content" ? this.inner : null; } }
const rows = [];
global.document = { querySelectorAll(sel) {
  const cls = sel.split("[")[0]; return rows.filter(r => r.cls === cls); } };
global.window = { setInterval: () => 1, clearInterval() {}, setTimeout: (f) => { f(); return 0; }, clearTimeout() {} };
global.MutationObserver = class { observe() {} disconnect() {} };

const VAULT = process.argv[2];
const files = {
  "fld_x_group_aaaa/3dhgg0.md": { basename: "3dhgg0", extension: "md", fm: { title: "📋 WordPress-SEO 總覽" } },
  "fld_x_group_aaaa/3notit.md": { path: "fld_x_group_aaaa/3notit.md", name: "3notit.md", basename: "3notit", extension: "md", fm: { uid: "3notit" } },
  "fld_x_group_aaaa/pic.png": { path: "fld_x_group_aaaa/pic.png", name: "pic.png", basename: "pic", extension: "png", fm: null },
};
const tabEl = new El("3dhgg0");
const F = (path, children) => ({ path, name: path.split("/").pop(), children });
const own = function (folder) { return folder.items; };   // Obsidian 自己的結果:照 id 排
const explorer = { sortOrder: "alphabetical", sorts: 0, containerEl: {}, getSortedFolderItems: own,
                   requestSort() { this.sorts++; } };
const top = { items: [
  { file: F("fld_x_group_0000", []) }, { file: F("fld_x_group_aaaa", []) }, { file: F("fld_x_group_zzzz", []) },
  { file: files["fld_x_group_aaaa/3notit.md"] }, { file: files["fld_x_group_aaaa/pic.png"] } ] };
const app = {
  vault: { adapter: { getBasePath: () => VAULT }, on: () => ({}),
           getAbstractFileByPath: (p) => files[p] || null },
  metadataCache: { on: () => ({}), getFileCache: (f) => ({ frontmatter: f.fm }) },
  workspace: { on: () => ({}), onLayoutReady: (f) => f(),
               getLeavesOfType: (t) => (t === "file-explorer" ? [{ view: explorer }] : []),
               iterateAllLeaves: (cb) => cb({ view: { file: files["fld_x_group_aaaa/3dhgg0.md"] }, tabHeaderInnerTitleEl: tabEl }) },
};
rows.push(new Row(".nav-folder-title", "fld_x_group_aaaa", "fld_x_group_aaaa"));
rows.push(new Row(".nav-folder-title", "fld_x_group_aaaa/fld_x_sub_bbbb", "fld_x_sub_bbbb"));
rows.push(new Row(".nav-folder-title", "fld_x_group_aaaa/fld_x_sub_zzzz", "fld_x_sub_zzzz"));
rows.push(new Row(".nav-file-title", "fld_x_group_aaaa/3dhgg0.md", "3dhgg0"));
rows.push(new Row(".nav-file-title", "fld_x_group_aaaa/3notit.md", "3notit"));
rows.push(new Row(".nav-file-title", "fld_x_group_aaaa/pic.png", "pic"));

const P = require(process.argv[1]);
const p = new P(app);
const label = (it) => it.file.path;
const snap = () => ({ rows: rows.map(r => r.inner.textContent), tab: tabEl.textContent,
  order: explorer.getSortedFolderItems(top).map(label), own: explorer.getSortedFolderItems === own, sorts: explorer.sorts });
(async () => {
  await p.onload();
  p.apply();
  const loaded = snap();
  p.onunload();
  const unloaded = snap();
  console.log(JSON.stringify({ loaded, unloaded }));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def node(script, *args, cwd=None):
    r = run_guarded([NODE, "-e", script, *args], timeout=60, cwd=cwd)
    if r.tripped:
        raise AssertionError("node tripped guard: %s" % r.tripped)
    if r.returncode != 0:
        raise AssertionError("node rc=%s\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    return r.stdout


def core(expr, *args):
    """在 node 裡載入外掛的純函數,印出 expr 的 JSON。"""
    js = "const C = require(%s).core; const A = process.argv.slice(1); console.log(JSON.stringify(%s));" % (
        json.dumps(str(MAIN)), expr)
    return json.loads(node(js, *args))


@unittest.skipUnless(NODE, "needs node")
class TestUidDisplayCore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uiddisp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def vault(self, labels_text=None):
        v = self.tmp / "vault"
        (v / ".ez2" / "display").mkdir(parents=True, exist_ok=True)
        if labels_text is not None:
            (v / ".ez2" / "display" / "labels.json").write_text(labels_text, encoding="utf-8")
        return v

    def test_D1_labels_parse_and_nested_folder(self):
        v = self.vault(json.dumps(LABELS, ensure_ascii=False))
        out = core("(() => { const L = C.readLabels(A[0]); return [Object.keys(L.folders).length,"
                   " C.folderDisplay(L, 'fld_x_group_aaaa'), C.folderDisplay(L, 'fld_x_group_aaaa/fld_x_sub_bbbb/'),"
                   " C.folderIdFromPath('a/b/fld_c'), C.folderDisplay(L, 'fld_x_group_aaaa/fld_unknown')]; })()", str(v))
        self.assertEqual(out, [2, "00-暫存區 Inbox", "WordPress-SEO", "fld_c", None])

    def test_D2_missing_or_broken_labels(self):
        missing = self.vault()                       # 有 .ez2/display 但沒有 labels.json
        broken = self.tmp / "broken"
        (broken / ".ez2" / "display").mkdir(parents=True)
        (broken / ".ez2" / "display" / "labels.json").write_text("{not json", encoding="utf-8")
        out = core("[C.readLabels(A[0]), C.readLabels(A[1]), C.readLabels(A[2]), C.readLabels(''),"
                   " C.parseLabels('[1,2]'), C.parseLabels('null'), C.folderDisplay(null, 'fld_x'),"
                   " C.parseLabels('{\"folders\": [1]}')]",
                   str(missing), str(broken), str(self.tmp / "no-such-vault"))
        self.assertEqual(out[:7], [None, None, None, None, None, None, None])
        self.assertEqual(out[7], {"folders": {}, "notes": {}})

    def test_D3_frontmatter_title(self):
        out = core("[C.noteDisplay({title: '  標題 '}, '3abc'), C.noteDisplay({uid: '3abc'}, '3abc'),"
                   " C.noteDisplay({title: ''}, '3abc'), C.noteDisplay({title: 2026}, '3abc'),"
                   " C.noteDisplay(null, '3abc'), C.noteDisplay({title: ['x']}, '3abc')]")
        self.assertEqual(out, ["標題", "3abc", "3abc", "2026", "3abc", "3abc"])

    def test_D8_sort_by_shown_title(self):
        js_items = ("[{file:{path:'fld_b',children:[]}},{file:{path:'n10.md'}},{file:{path:'fld_a',children:[]}},"
                    "{file:{path:'n2.md'}},{file:{path:'fld_c',children:[]}}]")
        names = "{fld_a:'10-看板區',fld_b:'00-暫存區',fld_c:'2-素材','n10.md':'第10篇','n2.md':'第2篇'}"
        out = core("(() => { const I = %s, N = %s, f = (x) => N[x.path], p = (r) => r.map(i => i.file.path);"
                   " return [p(C.sortItems(I, f, 'alphabetical')), p(C.sortItems(I, f, 'alphabeticalReverse')),"
                   " p(C.sortItems(I, f, 'byModifiedTime')), p(C.sortItems(I, f, undefined)), C.sortItems(null, f, '')]; })()"
                   % (js_items, names))
        self.assertEqual(out[0], ["fld_b", "fld_c", "fld_a", "n2.md", "n10.md"])   # 00 < 2 < 10(數字自然序)
        self.assertEqual(out[1], ["fld_a", "fld_c", "fld_b", "n10.md", "n2.md"])   # 反向,資料夾仍在前
        self.assertEqual(out[2], ["fld_b", "n10.md", "fld_a", "n2.md", "fld_c"])   # 照時間排:不動
        self.assertEqual(out[3], out[0])
        self.assertIsNone(out[4])

    def test_D4_i18n_reserved(self):
        v = self.vault(json.dumps(LABELS, ensure_ascii=False))
        out = core("(() => { const L = C.readLabels(A[0]); return [C.folderDisplay(L, 'fld_x_group_aaaa', 'ja'),"
                   " C.folderDisplay(L, 'fld_x_group_aaaa', 'en'), C.folderDisplay(L, 'fld_x_group_aaaa', '')]; })()", str(v))
        self.assertEqual(out, ["受信箱", "00-暫存區 Inbox", "00-暫存區 Inbox"])


@unittest.skipUnless(NODE, "needs node")
class TestUidDisplayPlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uiddisp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_plugin(self, labels):
        v = self.tmp / "vault"
        (v / ".ez2" / "display").mkdir(parents=True)
        if labels is not None:
            (v / ".ez2" / "display" / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        before = sorted(str(p.relative_to(v)) for p in v.rglob("*"))
        out = json.loads(node(FAKE_HARNESS, str(MAIN), str(v)))
        after = sorted(str(p.relative_to(v)) for p in v.rglob("*"))
        self.assertEqual(before, after, "plugin must not write into the vault")
        return out

    ORIGINAL = ["fld_x_group_aaaa", "fld_x_sub_bbbb", "fld_x_sub_zzzz", "3dhgg0", "3notit", "pic"]

    def test_D5_load_shows_titles_unload_restores(self):
        out = self.run_plugin(LABELS)
        self.assertEqual(out["loaded"]["rows"],
                         ["00-暫存區 Inbox", "WordPress-SEO", "fld_x_sub_zzzz", "📋 WordPress-SEO 總覽", "3notit", "pic"])
        self.assertEqual(out["loaded"]["tab"], "📋 WordPress-SEO 總覽")
        self.assertEqual(out["unloaded"]["rows"], self.ORIGINAL)
        self.assertEqual(out["unloaded"]["tab"], "3dhgg0")

    def test_D9_explorer_sort_patched_and_restored(self):
        labels = json.loads(json.dumps(LABELS))
        labels["folders"]["fld_x_group_0000"] = {"title": "10-看板區 Dashboards"}
        out = self.run_plugin(labels)
        self.assertFalse(out["loaded"]["own"])
        self.assertEqual(out["loaded"]["order"],
                         ["fld_x_group_aaaa", "fld_x_group_0000", "fld_x_group_zzzz",       # 00-… < 10-… < fld_…(沒標籤)
                          "fld_x_group_aaaa/3notit.md", "fld_x_group_aaaa/pic.png"])
        self.assertGreaterEqual(out["loaded"]["sorts"], 1)
        self.assertTrue(out["unloaded"]["own"])
        self.assertEqual(out["unloaded"]["order"][:3], ["fld_x_group_0000", "fld_x_group_aaaa", "fld_x_group_zzzz"])
        self.assertGreater(out["unloaded"]["sorts"], out["loaded"]["sorts"])

    def test_D6_missing_labels_keeps_ids(self):
        out = self.run_plugin(None)
        self.assertEqual(out["loaded"]["rows"],
                         ["fld_x_group_aaaa", "fld_x_sub_bbbb", "fld_x_sub_zzzz", "📋 WordPress-SEO 總覽", "3notit", "pic"])
        self.assertEqual(out["unloaded"]["rows"], self.ORIGINAL)


class TestUidDisplayStatic(unittest.TestCase):
    def test_D7_no_write_api_no_hardcoded_path(self):
        src = MAIN.read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
        for bad in (r"\.write\w*\(", r"\.modify\(", r"\.rename\(", r"\.create\w*\(", r"(?<!touched)\.delete\(", r"\.trash\(",
                    r"\.mkdir", r"\.unlink", r"\.rm\w*\(", r"\.copyFile", r"\.append\w*\(", r"\.process\(",
                    r"fileManager", r"processFrontMatter", r"/Users/", r"Desktop", r"EZ2_VAULT"):
            self.assertIsNone(re.search(bad, code), "main.js must not use %s" % bad)
        m = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["id"], "ez2-uid-display")
        self.assertTrue(m["isDesktopOnly"])          # 讀 labels.json 用 node fs
        self.assertIsNone(re.search(r"[぀-ヿ㐀-鿿가-힯]", json.dumps(m, ensure_ascii=False)))


if __name__ == "__main__":
    unittest.main()
