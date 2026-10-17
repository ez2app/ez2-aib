#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""uid_layout_convert.py — 把 Obsidian 第二大腦轉成「uid 版面」(the maintainer 2026-09-26 R7/R8/R9;10-05 令「先寫轉換,一併完成」)。

    資料夾名 = folderId(fld_<模組>_<種類>_<12 hex>)
    筆記檔名 = <uid>.md(uid = ez2-note-time-base36-v1 六碼;既有 uid 一律保留)
    標題與多語顯示名 → <vault>/.ez2/display/labels.json(folderId / uid → {title, i18n})
    路由 → <vault>/.ez2/index/route-key-index.json(folderPath = 改名後的真路徑)
    資料夾身分 → <vault>/.ez2/index/folder-id-index.json(新增 title / legacyPaths / status)

四個子命令,照順序:
    plan   --vault V --contract C [--legacy DIR --legacy-map MAP.json] --out plan.json     只讀,產計畫
    apply  --plan plan.json --backup-dir DIR --confirm-text "我已備份並同意轉成 uid 版面"   寫(先 tar 整庫到庫外)
    verify --vault V                                                                        只讀,驗收
    undo   --receipt receipt.json                                                           退回改名、連結、設定、.ez2 狀態(frontmatter 補的身分留著)
轉換後維護(10-16):
    reindex       --vault V                                                                 只重寫衍生索引
    folder-add    --vault V --title 名稱 [--parent folderId] [--route-key K] [--dry-run]     新增資料夾並登記(同名回原本那個;
                  --route-key 給夾自己的路由代號,權限沿用上層路由;同名夾還沒代號就補掛)
    folder-retire --vault V --folder folderId --backup-dir DIR [--include-notes] [--dry-run] 打包到庫外 → 拿掉索引 → 刪資料夾;
                  路由夾、非筆記檔、還被連結的筆記一律擋下;資料夾已被直接刪掉時只清索引

鐵則:
- 既有 uid 不換號;重複 uid 只在「一邊明顯是副本(.bak / 鏡像夾)」時給副本新號並記 duplicate_of,否則列 blocker、兩邊都不改名。
- 不搬、不改名 .obsidian / .ez2 / .git / .trash 裡的東西;非 .md 附件只跟著資料夾走,不改名。
- 舊檔名、舊路徑全部進 aliases / legacy_paths / legacyPaths(aib 照這些找得到)。frontmatter 清單單行、區塊寫法都認,原有值一個不丟。
- Obsidian 自己只照檔名解 [[連結]]、不看 aliases ⇒ 正文裡指向改名筆記的 [[舊名]]、[[舊名|字]]、![[舊名]]、[[舊名#標題]]
  改寫成指向 uid,畫面上原本的字留在 | 後面;同名多篇的不猜、不改,plan 列出來。frontmatter 裡的連結不改。
- .obsidian 的日記/範本/碎片/附件/新檔位置與書籤,指到改名路徑的一併改(只改這幾格)。
- 範本(範本資料夾、Templater 範本資料夾、日記/碎片指定的範本檔)不補 uid、不改名——補了 uid,用範本新建的筆記會抄到同一個 uid;
  範本資料夾可跟著上層搬,verify 從 .obsidian 讀範本位置跳過。
- 角色卡預設一 byte 不改;plan 帶 --rewrite-card-links 才把卡正文裡的連結也改指 uid(frontmatter 不動;卡片指紋會變,
  釘過舊指紋的席會亮「卡已更新」,重綁確認;apply 帶 --rescan-cards)。
- 寫入前整庫 tar 到庫外;apply 產 receipt(含 undo map)。undo 退回:檔名、資料夾名、正文連結、上面那幾格設定、
  .ez2 的狀態與索引(回到轉換前,migration-state 不再說是 uid 版面);frontmatter 補的身分留著(要全退就從 tar 還原)。

同步外掛 remotely-save(10-15 彩排讀真庫設定得出;本工具只看有沒有裝,不讀它的設定檔——裡面有雲端帳密):
- 它不同步「.」開頭的檔與夾(README:以 . 或 _ 開頭都當隱藏檔;只能另外打開 _ 與 .obsidian)⇒ .ez2(標題對照、索引、
  轉換狀態)只留在跑轉換的這台;手機與其他電腦拿到的只有 <uid>.md。
  建議:aib 只在跑轉換的那台用(.ez2 在這台);其他裝置要看標題就靠每篇 frontmatter 的 title(會同步),
  配 Obsidian 的標題顯示外掛。另一台也要跑 aib 時,在那台對同步下來的庫再跑一次 plan → apply 可重建 .ez2
  (uid 已在 frontmatter,不換號、不改名;筆記標題從 frontmatter 的 title 回來),但**資料夾標題只存在 .ez2**,
  那台重建出來的資料夾標題會是 fld_ 名 —— 要不要把標題對照另存到會同步的位置,待 the maintainer 決定。
- .obsidian 預設也不同步 ⇒ 上面改的日記/範本/附件/書籤設定,每台各自要改(或那台也跑一次 apply)。
- 改名對它來說是「刪舊檔 + 傳新檔」,會碰到「一次改太多」的保護門檻(預設 50%)。
- 步驟:① 所有裝置先同步到最新、停止編輯;② 這台關掉 remotely-save 的自動同步;③ plan → apply(--rescan-cards)→ verify;
  ④ 這台手動同步一次(門檻警告確認後繼續);⑤ 其他裝置先備份或清掉本機庫再拉,不要帶著轉換前改過的舊名檔去同步,
  不然舊名檔傳回雲端會跟 <uid>.md 並存;⑥ 全部同步完,這台再 verify 一次(出現非 uid 檔名 = 有裝置傳回舊檔)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cockpit"))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """給人看的字一律走 i18n(照 tools/release/ship.py);鍵 aib.vault_convert.<key>。"""
    return i18n_lib.t("aib.vault_convert." + key, params, locale=LOC)


UID_EPOCH_MS = 1577836800000          # 2020-01-01T00:00:00Z,與 ez2-ai shared/ez2-note-format.js 同源
UID_RE = re.compile(r"^[0-9a-z]{6}$")
UID_PREFIX_RE = re.compile(r"^([0-9a-z]{6})-(.+)$")
FOLDER_ID_RE = re.compile(r"^fld_[a-z]_[a-z]+_[0-9a-f]{8,12}$")
SKIP_DIRS = {".obsidian", ".ez2", ".git", ".trash", ".DS_Store", "node_modules"}
MANAGED_KEYS = ("uid", "aliases", "title", "orig_name", "uid_scheme", "routeKey", "category_uid",
                "privacy_level", "source_kind", "created", "updated", "ez2fmt", "tags", "legacy_paths",
                "folder_id", "duplicate_of")
CONFIRM_TEXT = T("confirm_text")   # 當前語系的確認句;apply 也接受 zh-TW 那句(文件與測試都用它)
LANGS = ("zh-TW", "en", "ja", "ko")


# ---------------------------------------------------------------- 小工具
def sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def b36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def mint_uid(ms: int, used: set) -> str:
    n = max(0, (int(ms) - UID_EPOCH_MS) // 1000)
    code = b36(n).rjust(6, "0")
    while code in used or not UID_RE.match(code):
        n += 1
        code = b36(n).rjust(6, "0")
    used.add(code)
    return code


def iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + ".%03dZ" % (ms % 1000)


def module_of(top: str) -> str:
    c = (top[:1] or "x").lower()
    return c if c in "abcdefghijklmnopqrstuvwxyz" else "x"


def yaml_str(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def yaml_list(items) -> str:
    return "[" + ", ".join(yaml_str(x) for x in items) + "]"


# ---------------------------------------------------------------- frontmatter
FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.S)


def split_frontmatter(text: str):
    m = FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def fm_lines_to_map(fm: str) -> dict:
    """只讀我們關心的單行鍵(uid/title/routeKey/...)。清單類(aliases/tags/legacy_paths)只認單行 [..] 形。"""
    out = {}
    for ln in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*(.*)$", ln)
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


BLOCK_ITEM_RE = re.compile(r"^\s*-(?:\s+(.*))?$")


def _block_follows(lines: list, i: int) -> bool:
    """第 i 行之後(跳過空行與註解)接的是不是「- 值」清單項目。"""
    for nxt in lines[i + 1:]:
        if not nxt.strip() or nxt.lstrip().startswith("#"):
            continue
        return bool(BLOCK_ITEM_RE.match(nxt))
    return False


def _is_block_start(value: str, lines: list, i: int) -> bool:
    """「key:」或「key: # 註解」且下面真的接清單項目 ⇒ 區塊起頭;「tags: #idea」底下沒有項目 ⇒ 是單行值。"""
    v = value.strip()
    return (v == "" or v.startswith("#")) and (v == "" or _block_follows(lines, i))


def strip_managed(fm: str) -> list[str]:
    """把要重寫的鍵(含其多行清單區塊)拿掉,保留其他原行。區塊裡的空行與註解跟著拿掉,區塊後面的照留。"""
    keep, skip_block = [], False
    lines = fm.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9_.\-]+):(.*)$", ln)
        if m:
            key = m.group(1)
            skip_block = key in MANAGED_KEYS and _is_block_start(m.group(2), lines, i)
            if key in MANAGED_KEYS:
                continue
            keep.append(ln)
        elif skip_block and BLOCK_ITEM_RE.match(ln):
            continue
        elif skip_block and (not ln.strip() or ln.lstrip().startswith("#")) and _block_follows(lines, i):
            continue                                       # 區塊中間的空行與註解(後面還有項目)
        else:
            skip_block = False
            keep.append(ln)
    return keep


def parse_inline_list(v: str) -> list[str]:
    v = (v or "").strip()
    if v.startswith("[") and v.endswith("]"):
        try:
            return [str(x) for x in json.loads(v)]
        except ValueError:
            return [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
    if v.startswith('"') or v.startswith("'"):
        return [v.strip('"\'')]
    return [v] if v else []


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return str(json.loads(v))
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def fm_list(fm: str | None, key: str) -> list[str]:
    """frontmatter 的清單鍵:單行 [a, b]、單一值、YAML 區塊寫法(下一行起「  - 值」或頂格「- 值」)都認。
    10-15 彩排:只認單行時,區塊寫法的 tags/aliases 整串被清成 [](真庫 450 篇掉標籤、208 篇掉別名)。"""
    if not fm:
        return []
    lines = fm.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*(.*)$", ln)
        if not m or m.group(1) != key:
            continue
        v = m.group(2).strip()
        if not _is_block_start(v, lines, i):               # 「tags: # 註解」+ 下面有項目 = 區塊;「tags: #idea」= 單行值
            return parse_inline_list(v)
        out = []
        for nxt in lines[i + 1:]:
            bm = BLOCK_ITEM_RE.match(nxt)
            if bm:
                item = _unquote(bm.group(1) or "")
                if item:
                    out.append(item)
            elif nxt.strip() and not nxt.lstrip().startswith("#"):
                break                                      # 空行與註解行不結束區塊
        return out
    return []


# ---------------------------------------------------------------- 索引
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_contract(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    routes = {}
    for r in d.get("routes", []):
        if r.get("routeKey") and r.get("folder"):
            routes[r["routeKey"]] = dict(r)
    return routes


# ---------------------------------------------------------------- 正文連結 / Obsidian 設定(10-15 彩排)
# Obsidian 的 [[連結]] 只照檔名或路徑解,不看 aliases(彩排:真庫解不開的連結 270 → 669)。
# 所以正文裡指向「這次改名的筆記」的連結改寫成指向 uid,原本畫面上看到的字留在 | 後面;
# 指向「搬了資料夾的其他檔」的路徑型連結(![[資料夾/圖.png]])改成新路徑。程式碼區塊與 `行內碼` 不動。
# 只改正文;frontmatter 裡的連結不碰。每一處改寫都記進收據,undo 照位置退回。
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
CODE_SPAN_RE = re.compile(r"(`+).+?\1")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")            # 縮排 4 格以上是縮排碼,不是圍欄
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")    # 收尾圍欄不能帶語言名(CommonMark)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


class LinkRewriter:
    """舊名 → 新目標的對照(由計畫算出,plan 與 apply 共用同一份規則)。
    md:每個 .md 的舊路徑(去 .md)→ ("note", uid)(這次改名的筆記)或 ("path", 新路徑)(不改名、只跟著資料夾搬,例如角色卡);
    other:其他檔(圖、pdf…)舊路徑 → ("path", 新路徑);by_name:舊檔名(去 .md)→ {uid 或 ""(不改名的檔)}。
    同名不只一篇、或部分路徑對到不只一個檔 ⇒ Obsidian 自己挑哪篇我們不猜,不改寫,記 ambiguous。"""

    def __init__(self, vault: Path, notes: list, folders: list, drops: list, kept_md: list):
        self.vault = vault
        self.dir_map = {_nfc(f["old_rel"]): f["new_rel"] for f in folders}
        self.md: dict[str, tuple] = {}
        self.other: dict[str, tuple] = {}
        self.by_name: dict[str, set] = {}
        self.by_name_cf: dict[str, set] = {}
        self._cf: dict = {}
        for old_rel, uid in [(n["old_rel"], n["uid"]) for n in notes] + [(d["old_rel"], d["uid"]) for d in drops] + [(k, "") for k in kept_md]:
            key = _nfc(old_rel[:-3] if old_rel.endswith(".md") else old_rel)
            self.md[key] = ("note", uid) if uid else ("path", self._moved(key))
            stem = key.rsplit("/", 1)[-1]
            self.by_name.setdefault(stem, set()).add(uid)
            self.by_name_cf.setdefault(stem.casefold(), set()).add(uid)
        for dp, dn, fn in os.walk(vault):
            dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
            for f in fn:
                if not f.endswith(".md"):
                    rel = _nfc(str(Path(dp, f).relative_to(vault)))
                    self.other[rel] = ("path", self._moved(rel))

    def _moved(self, rel: str) -> str:
        parent, _, name = rel.rpartition("/")
        return (self.dir_map.get(parent, parent) + "/" + name) if parent else name

    def _lookup(self, table: dict, key: str):
        """完整路徑優先;否則 Obsidian 的「部分路徑」= 尾端相符(不分大小寫)。回 (對到的完整舊路徑, entry, ambiguous)。"""
        if key in table:
            return key, table[key], False
        cands = [k for k in table if k.endswith("/" + key)]
        if not cands:
            cf = self._cf.setdefault(id(table), [(k.casefold(), k) for k in table])
            kc = key.casefold()
            cands = [k for kf, k in cf if kf == kc or kf.endswith("/" + kc)]
        if len(cands) > 1:
            return None, None, True
        return (cands[0], table[cands[0]], False) if cands else (None, None, False)

    def resolve(self, target: str):
        """回 (新目標, 種類);種類 note=改指 uid、path=換成新路徑、ambiguous=對到不只一個不改、None=不動。"""
        t = _nfc(target.strip())
        is_md = t.lower().endswith(".md")
        t_noext = t[:-3] if is_md else t
        if not t_noext:
            return None, None
        if "/" in t_noext:
            key = t_noext.lstrip("/")
            full, entry, amb = self._lookup(self.md, key)
            if entry is None and not amb and not is_md:
                full, entry, amb = self._lookup(self.other, t.lstrip("/"))
                if entry:                                  # 圖、pdf…:對到的檔沒搬就不動(部分路徑、大小寫不同都照原樣)
                    return (entry[1], "path") if entry[1] != full else (None, None)
            if amb:
                return None, "ambiguous"
            if entry is None:
                return None, None
            if entry[0] == "note":
                if full.rsplit("/", 1)[-1] == entry[1] and self._moved(full) == full:
                    return None, None                      # 已經指著 uid、資料夾也沒搬(第二次 apply)
                return entry[1], "note"
            return (entry[1] + (t[len(t_noext):] if is_md else ""), "path") if entry[1] != full else (None, None)
        us = self.by_name.get(t_noext) or self.by_name_cf.get(t_noext.casefold())
        if not us:
            return None, None
        if len(us) > 1:
            return None, "ambiguous"
        u = next(iter(us))
        return (u, "note") if u and u != t_noext else (None, None)

    def rewrite(self, text: str):
        """回 (新文字, edits[[位置, 新, 舊]], ambiguous 目標清單);位置是新文字裡的字元位置。"""
        out, edits, amb = [], [], []
        pos, fence = 0, None
        lines = text.splitlines(keepends=True)
        table_lines = _table_lines(lines)
        for idx, ln in enumerate(lines):
            fm = FENCE_RE.match(ln)
            if fence:
                fc = FENCE_CLOSE_RE.match(ln)
                if fc and fc.group(1)[0] == fence[0] and len(fc.group(1)) >= len(fence):
                    fence = None
                out.append(ln); pos += len(ln); continue
            if fm:
                fence = fm.group(1); out.append(ln); pos += len(ln); continue
            code = [(m.start(), m.end()) for m in CODE_SPAN_RE.finditer(ln)]
            table = idx in table_lines                     # 表格裡(含沒有開頭 | 的、> 引用框裡的)補的分隔用 \|
            new_ln, last = [], 0
            for m in WIKILINK_RE.finditer(ln):
                if any(a <= m.start() < b for a, b in code):
                    continue
                rep = self._one(m.group(1), m.group(2), table, amb)
                if rep is None:
                    continue
                new_ln.append(ln[last:m.start()])
                edits.append([pos + sum(len(x) for x in new_ln), rep, m.group(0)])
                new_ln.append(rep); last = m.end()
            new_ln.append(ln[last:])
            s = "".join(new_ln); out.append(s); pos += len(s)
        return "".join(out), edits, amb

    def _one(self, bang: str, inner: str, table: bool, amb: list):
        mm = re.match(r"^(.*?)(\\?\|)(.*)$", inner, re.S)
        tgt_part, sep, disp = (mm.group(1), mm.group(2), mm.group(3)) if mm else (inner, "", None)
        h = tgt_part.find("#")
        target, head = (tgt_part[:h], tgt_part[h:]) if h >= 0 else (tgt_part, "")
        if not target.strip():
            return None                                    # [[#標題]]:同篇內連結
        new, kind = self.resolve(target)
        if kind == "ambiguous":
            amb.append(target)
            return None
        if kind not in ("note", "path"):
            return None
        if disp is None and not bang:
            sep, disp = ("\\|" if table else "|"), tgt_part   # 畫面上原本看到的字留著
        return "%s[[%s%s%s]]" % (bang, new, head, (sep + disp) if disp is not None else "")


TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$|^\s*\|\s*:?-+:?\s*\|\s*$")


def _table_lines(lines: list) -> set:
    """哪幾行在表格裡:有分隔列(|---|---|)的那一段——上一行是表頭,往下接著有 | 的行都是;> 引用框裡的表格也算。"""
    def body(ln):
        return re.sub(r"^\s*(>\s*)+", "", ln.rstrip("\r\n"))
    out = set()
    for i, ln in enumerate(lines):
        if "-" in ln and TABLE_DELIM_RE.match(body(ln)) and i > 0 and "|" in body(lines[i - 1]):
            out.add(i - 1); out.add(i)
            j = i + 1
            while j < len(lines) and body(lines[j]).strip() and "|" in body(lines[j]):
                out.add(j); j += 1
    return out


def undo_link_edits(text: str, edits: list):
    """照位置退回(檔案沒被改過時);位置對不上 ⇒ 逐筆找第一個「新」字串換回「舊」。回 (文字, 退回筆數, 是否照位置)。"""
    if all(text[p:p + len(new)] == new for p, new, _ in edits):
        for p, new, old in sorted(edits, key=lambda e: -e[0]):
            text = text[:p] + old + text[p + len(new):]
        return text, len(edits), True
    done = 0
    for _, new, old in edits:
        if new in text:
            text = text.replace(new, old, 1); done += 1
    return text, done, False


# Obsidian 自己的設定裡記著資料夾/檔案路徑:轉完不改,新日記、範本、附件會把舊名夾建回來,書籤會失效。
OBSIDIAN_PATH_KEYS = {"daily-notes.json": ("folder", "template"), "templates.json": ("folder",),
                      "zk-prefixer.json": ("folder", "template"), "app.json": ("attachmentFolderPath", "newFileFolderPath")}


def map_vault_path(p: str, note_map: dict, dir_map: dict):
    """Vault 相對路徑 → 轉換後路徑;不在這次改名範圍 ⇒ None。資料夾、筆記(有沒有 .md 都認)、其他檔(跟著資料夾走)。"""
    if not isinstance(p, str) or not p.strip() or p.startswith(("./", "../", "/")):
        return None
    tail = "/" if p.endswith("/") else ""
    s = _nfc(p.strip().rstrip("/"))
    if s in dir_map:
        new = dir_map[s]
    elif s in note_map:
        new = note_map[s]
    elif s + ".md" in note_map:
        new = note_map[s + ".md"][:-3]
    elif "/" in s and s.rsplit("/", 1)[0] in dir_map:
        new = dir_map[s.rsplit("/", 1)[0]] + "/" + s.rsplit("/", 1)[1]
    else:
        return None
    return None if new == s else new + tail


def obsidian_setting_edits(vault: Path, notes: list, folders: list) -> list:
    """回 [{file, keys(JSON 路徑), old, new}]。只看日記/範本/碎片/附件/新檔位置與書籤;讀不到或壞掉的 JSON 略過。"""
    cfg = vault / ".obsidian"
    note_map = {_nfc(n["old_rel"]): n["new_rel"] for n in notes}
    dir_map = {_nfc(f["old_rel"]): f["new_rel"] for f in folders}
    edits = []
    for name, keys in OBSIDIAN_PATH_KEYS.items():
        d = load_json(cfg / name, None)
        if not isinstance(d, dict):
            continue
        for k in keys:
            new = map_vault_path(d.get(k), note_map, dir_map)
            if new is not None:
                edits.append({"file": name, "keys": [k], "old": d[k], "new": new})
    bm = load_json(cfg / "bookmarks.json", None)

    def walk(items, path):
        for i, it in enumerate(items or []):
            if not isinstance(it, dict):
                continue
            if it.get("type") in ("file", "folder"):
                new = map_vault_path(it.get("path"), note_map, dir_map)
                if new is not None:
                    edits.append({"file": "bookmarks.json", "keys": path + [i, "path"], "old": it["path"], "new": new})
            if isinstance(it.get("items"), list):
                walk(it["items"], path + [i, "items"])
    if isinstance(bm, dict):
        walk(bm.get("items"), ["items"])
    return edits


def _json_set(doc, keys, value):
    for k in keys[:-1]:
        doc = doc[k]
    doc[keys[-1]] = value


def _json_get(doc, keys):
    for k in keys:
        doc = doc[k]
    return doc


def apply_obsidian_edits(vault: Path, edits: list, forward: bool) -> int:
    """forward=True 寫新值;False 退回(只在目前值還是我們寫的那個時才退,用戶之後改過的不動)。回改了幾處。"""
    done, by_file = 0, {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e)
    for name, es in by_file.items():
        p = vault / ".obsidian" / name
        doc = load_json(p, None)
        if doc is None:
            continue
        changed = 0
        for e in es:
            want, put = (e["old"], e["new"]) if forward else (e["new"], e["old"])
            try:
                if _json_get(doc, e["keys"]) == want:
                    _json_set(doc, e["keys"], put); changed += 1
            except (KeyError, IndexError, TypeError):
                continue
        if changed:
            tmp = p.with_name(p.name + ".tmp-uidlayout")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(tmp, p)
            done += changed
    return done


# 範本(10-15 the maintainer 裁「範本排除」):Obsidian 把範本資料夾裡每一篇都當範本;範本補了 uid,用它新建的筆記就抄到同一個 uid。
# ⇒ 範本資料夾(內建範本、Templater)與日記/碎片指定的範本檔:不補 uid、不改名(資料夾可跟著上層搬);verify 從 .obsidian 讀同一份位置跳過。
TEMPLATE_SETTINGS = (("templates.json", "folder", "dir"), ("plugins/templater-obsidian/data.json", "templates_folder", "dir"),
                     ("daily-notes.json", "template", "file"), ("zk-prefixer.json", "template", "file"))


def template_paths(vault: Path):
    """回 (範本資料夾集合, 範本檔集合(去 .md));路徑是 Vault 相對、NFC。"""
    dirs, files = set(), set()
    for name, key, kind in TEMPLATE_SETTINGS:
        v = (load_json(vault / ".obsidian" / name, {}) or {}).get(key)
        if not isinstance(v, str) or v.startswith(("./", "../")):
            continue
        v = _nfc(v.strip().strip("/"))
        if not v:
            continue
        if kind == "dir":
            dirs.add(v)
        else:
            files.add(v[:-3] if v.endswith(".md") else v)
    return dirs, files


def in_templates(rel: str, dirs: set, files: set) -> bool:
    r = _nfc(str(rel))
    return (r[:-3] if r.endswith(".md") else r) in files or any(r == d or r.startswith(d + "/") for d in dirs)


# 轉換會寫的 .ez2 檔:apply 前先留一份,undo 照原樣放回(原本沒有的就刪掉)⇒ migration-state 回到轉換前
EZ2_STATE_FILES = (".ez2/migration-state.json", ".ez2/index/folder-id-index.json", ".ez2/index/route-key-index.json",
                   ".ez2/index/uid-index.json", ".ez2/display/labels.json", ".ez2/routes/obsidian-routes-v2.json")


# ---------------------------------------------------------------- plan
def build_plan(vault: Path, contract_path: str, legacy: Path | None, legacy_map: dict | None,
               rewrite_card_links: bool = False) -> dict:
    vault = vault.resolve()
    routes = load_contract(contract_path)                      # routeKey -> {folder, module, ...}
    folder_to_route = {r["folder"]: k for k, r in routes.items()}
    def under_library(rel_s: str) -> bool:
        return any(rel_s.startswith(lib + "/") for lib in library_olds)
    idx_dir = vault / ".ez2" / "index"
    fid_index = load_json(idx_dir / "folder-id-index.json", {"items": []})
    rk_index = load_json(idx_dir / "route-key-index.json", {"items": []})
    known_fid = {it["folderPath"]: it for it in fid_index.get("items", []) if it.get("folderPath")}
    known_rk = {it["routeKey"]: it for it in rk_index.get("items", [])}
    LIBRARY_RK = "product.expert.agent_library"
    # 角色庫位置:vault 自己的索引優先(已轉過的庫路徑是 folderId),沒有才用契約字面;兩個都存在就兩個都算
    _idx_lib = next((it.get("folderPath") for it in rk_index.get("items", []) if it.get("routeKey") == LIBRARY_RK and (vault / it.get("folderPath", "")).is_dir()), "")
    _con_lib = routes.get(LIBRARY_RK, {}).get("folder", "")
    library_old = _idx_lib or _con_lib
    library_olds = {x for x in (_idx_lib, _con_lib) if x and (vault / x).is_dir()}
    tpl_dirs, tpl_files = template_paths(vault)
    # 範本資料夾蓋到路由資料夾或角色庫 ⇒ 不能整夾當範本(路由會沒有 folderId、筆記不補 uid):擋下,請先把範本改到專用資料夾
    protected = {_nfc(x) for x in folder_to_route if (vault / x).is_dir()} | {_nfc(x) for x in library_olds}
    def _overlap(d):                                         # 範本夾等於或包住路由夾/角色庫;範本夾在路由夾裡面沒關係
        return sorted(x for x in protected if x == d or x.startswith(d + "/"))
    tpl_blockers = [{"template_dir": d, "covers": _overlap(d)[:5], "reason": T("blocker_template_covers_route")}
                    for d in sorted(tpl_dirs) if _overlap(d)]
    tpl_dirs = {d for d in tpl_dirs if not _overlap(d)}

    plan = {"schema": "2026-10-05.ez2-uid-layout-plan", "vault": str(vault), "contract": contract_path,
            "generated_at": iso(int(time.time() * 1000)), "legacy_copies": [], "folders": [], "notes": [],
            "blockers": tpl_blockers, "warnings": [], "stats": {}}

    # ---- 1. 舊庫導入(只複製,目的地照對照表) ----
    legacy_targets: dict[str, dict] = {}   # 導入後在 vault 內的相對路徑 -> {legacy_rel, routeKey}
    if legacy and legacy_map:
        legacy = legacy.resolve()
        for zone, spec in legacy_map.items():
            if zone.startswith("_"):
                continue
            src = legacy / zone
            if not src.exists():
                plan["warnings"].append(T("warn_zone_missing", {"zone": zone}))
                continue
            dst_rel = spec["dst"]; rk = spec.get("routeKey", "")
            if src.is_file():
                files = [src]
            else:
                files = [p for p in src.rglob("*") if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(src).parts)]
            for p in files:
                rel_in_zone = p.relative_to(src) if src.is_dir() else Path(p.name)
                sub = spec.get("subdir", "")
                dst = Path(dst_rel) / sub / rel_in_zone if sub else Path(dst_rel) / rel_in_zone
                if (vault / dst).exists():
                    plan["warnings"].append(T("warn_dst_exists", {"dst": str(dst)}))
                    continue
                if library_old and str(dst).startswith(library_old + "/"):
                    plan["warnings"].append(T("warn_library_skip", {"dst": str(dst)}))
                    continue
                plan["legacy_copies"].append({"src": str(p), "dst_rel": str(dst)})
                legacy_targets[str(dst)] = {"legacy_rel": str(Path(zone) / rel_in_zone) if src.is_dir() else zone, "routeKey": rk}

    # ---- 2. 資料夾身分 ----
    def all_dirs():
        out = []
        for dp, dn, _ in os.walk(vault):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
            for d in dn:
                out.append(Path(dp, d).relative_to(vault))
        # 舊庫導入會新建的資料夾
        for dst_rel in legacy_targets:
            parent = Path(dst_rel).parent
            while str(parent) not in ("", "."):
                out.append(parent); parent = parent.parent
        uniq = sorted({str(p) for p in out}, key=lambda s: (s.count("/"), s))
        return [Path(s) for s in uniq]

    def card_uid_of(rel: Path) -> str:
        p = vault / rel / "角色.md"
        if not p.is_file():
            return ""
        fm, _ = split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        u = (fm_lines_to_map(fm).get("uid") if fm else "") or ""
        u = u.strip().strip('"\'')
        return u if UID_RE.match(u) else ""

    folder_recs: dict[str, dict] = {}
    for rel in all_dirs():
        rel_s = str(rel); top = rel.parts[0]; mod = module_of(top)
        parent_rel = str(rel.parent) if len(rel.parts) > 1 else ""
        if under_library(rel_s):
            _lib = next(lib for lib in library_olds if rel_s.startswith(lib + "/"))
            depth = len(Path(rel_s).parts) - len(Path(_lib).parts)
            cu = card_uid_of(rel) if depth == 1 else ""
            if depth == 1 and UID_RE.match(rel.name):
                fid, rk, kind = rel.name, "", "card"               # 已經是卡片 uid
            elif depth == 1 and cu and not rel.name.startswith("_"):
                fid, rk, kind = cu, "", "card"                     # 卡夾 → 卡片 uid(SOP §2 規則 2)
            else:
                fid, rk, kind = rel.name, "", "keep"               # _結構夾、卡內夾:名字不動
            folder_recs[rel_s] = {"old_rel": rel_s, "folderId": fid, "kind": kind, "routeKey": rk,
                                  "parent_old_rel": parent_rel, "title": rel.name, "level": len(rel.parts)}
            continue
        if in_templates(rel_s, tpl_dirs, set()):
            folder_recs[rel_s] = {"old_rel": rel_s, "folderId": rel.name, "kind": "template", "routeKey": "",
                                  "parent_old_rel": parent_rel, "title": rel.name, "level": len(rel.parts)}
            continue                                                # 範本資料夾:名字不動(可跟著上層搬)
        if FOLDER_ID_RE.match(rel.name):
            fid = rel.name; kind = "route" if "_route_" in fid else ("group" if "_group_" in fid else "sub")
            rk = next((it.get("routeKey", "") for it in fid_index.get("items", []) if it.get("folderId") == fid), "")
            if not rk:
                rk = next((k for k, r in routes.items() if "fld_%s_route_%s" % (mod, sha12(k)) == fid), "")
        elif rel_s in known_fid:
            fid = known_fid[rel_s]["folderId"]; rk = known_fid[rel_s].get("routeKey", "")
            kind = "route" if "_route_" in fid else ("group" if "_group_" in fid else "sub")
        elif rel_s in folder_to_route:
            rk = folder_to_route[rel_s]; fid = "fld_%s_route_%s" % (mod, sha12(rk)); kind = "route"
        elif len(rel.parts) == 1:
            fid = "fld_%s_group_%s" % (mod, sha12(rel_s)); rk = "folder.group.%s.%s" % (mod, sha12(rel_s)[:8]); kind = "group"
        else:
            fid = "fld_%s_sub_%s" % (mod, sha12(rel_s)); rk = ""; kind = "sub"
        folder_recs[rel_s] = {"old_rel": rel_s, "folderId": fid, "kind": kind, "routeKey": rk,
                              "parent_old_rel": parent_rel, "title": rel.name, "level": len(rel.parts)}
    # 新路徑 = 祖先全部換成 folderId
    def new_rel_of_dir(old_rel: str) -> str:
        parts = Path(old_rel).parts; out = []
        for i in range(1, len(parts) + 1):
            out.append(folder_recs[str(Path(*parts[:i]))]["folderId"])
        return "/".join(out)
    for rec in folder_recs.values():
        rec["new_rel"] = new_rel_of_dir(rec["old_rel"])
        rec["parentFolderId"] = folder_recs[rec["parent_old_rel"]]["folderId"] if rec["parent_old_rel"] else ""
        rec["routeKey_inherited"] = rec["routeKey"]
    # 繼承最近祖先的 routeKey(給筆記用)
    def inherited_rk(old_rel: str) -> str:
        p = Path(old_rel)
        while str(p) not in ("", "."):
            rk = folder_recs.get(str(p), {}).get("routeKey", "")
            if rk and not rk.startswith("folder.group."):
                return rk
            p = p.parent
        return ""
    for rec in folder_recs.values():
        rec["routeKey_inherited"] = inherited_rk(rec["old_rel"])
    plan["folders"] = sorted(folder_recs.values(), key=lambda r: (r["level"], r["old_rel"]))

    # ---- 3. 筆記身分 ----
    used: set[str] = set()
    notes_raw = []
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = str(Path(dp).relative_to(vault))
        if rel_dir in library_olds or under_library(rel_dir):
            continue                                                # 角色庫:自己的 git 倉與指紋規則,不改寫、不改名
        for f in sorted(fn):
            if f.endswith(".md") and not in_templates(Path(dp, f).relative_to(vault), tpl_dirs, tpl_files):
                notes_raw.append((Path(dp, f).relative_to(vault), False, None))   # 範本不補 uid、不改名
    for dst_rel, meta in legacy_targets.items():
        if dst_rel.endswith(".md"):
            notes_raw.append((Path(dst_rel), True, meta))

    def read_text(rel: Path, is_legacy: bool):
        p = (legacy / legacy_targets[str(rel)]["legacy_rel"]) if is_legacy else (vault / rel)
        raw = p.read_bytes()
        try:
            return raw.decode("utf-8"), p
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace"), p

    first_pass = []
    for rel, is_legacy, meta in notes_raw:
        text, src_path = read_text(rel, is_legacy)
        fm, body = split_frontmatter(text)
        kv = fm_lines_to_map(fm) if fm is not None else {}
        uid = (kv.get("uid") or "").strip().strip('"\'')
        m = UID_PREFIX_RE.match(rel.stem)
        if not UID_RE.match(uid):
            uid = ""
        first_pass.append({"rel": rel, "is_legacy": is_legacy, "meta": meta, "fm": fm, "kv": kv, "uid": uid,
                           "name_uid": m.group(1) if m else "", "mtime_ms": int(src_path.stat().st_mtime * 1000)})
    # 重複 uid 處理
    by_uid: dict[str, list] = {}
    for n in first_pass:
        if n["uid"]:
            by_uid.setdefault(n["uid"], []).append(n)
    def _sha_of(n) -> str:
        p = (legacy / n["meta"]["legacy_rel"]) if n["is_legacy"] else (vault / n["rel"])
        return hashlib.sha256(p.read_bytes()).hexdigest()
    for uid, lst in list(by_uid.items()):
        if len(lst) > 1 and len({_sha_of(n) for n in lst}) == 1:
            # 內容逐 byte 相同 = 同一篇的副本(例如另一台 rsync 過來的):留一份(已在 fld_ 路徑下的優先,否則最舊),其餘 apply 時直接丟
            def _rank(n):
                return (0 if (not n["is_legacy"] and str(n["rel"]).startswith("fld_")) else 1, n["mtime_ms"], str(n["rel"]))
            keep = sorted(lst, key=_rank)[0]
            for n in lst:
                if n is not keep:
                    n["drop_identical"] = True
            by_uid[uid] = [keep]
    def is_copy(n) -> bool:
        s = str(n["rel"])
        return (".bak" in s) or ("_我的狀態/" in s) or ("/backup" in s.lower()) or ("副本" in s) or ("99.Ez2_垃圾桶/" in s) or ("/.trash/" in s) or n["is_legacy"]
    for uid, lst in by_uid.items():
        if len(lst) == 1:
            used.add(uid); continue
        used.add(uid)
        originals = [n for n in lst if not is_copy(n)]
        if len(originals) == 1:
            keep = originals[0]
        else:
            # 真撞號(都不是副本)或全是副本:最舊的那篇留 uid,其餘換新號並打 #ez2/uid-collision 留人審
            keep = sorted(lst, key=lambda n: (n["mtime_ms"], str(n["rel"])))[0]
            plan["warnings"].append(T("warn_uid_collision", {"uid": uid, "count": len(lst), "keep": str(keep["rel"])}))
        for n in lst:
            if n is not keep:
                n["duplicate_of"] = uid; n["uid"] = ""      # 之後補新號
                n["collision"] = (len(originals) != 1)
    # 補號(按 mtime 排序,穩定)
    plan["drops"] = [{"old_rel": str(n["rel"]), "uid": n["uid"], "reason": "identical duplicate"} for n in first_pass if n.get("drop_identical")]
    for n in sorted(first_pass, key=lambda n: (n["mtime_ms"], str(n["rel"]))):
        if n.get("blocked") or n.get("drop_identical"):
            continue
        minted = False
        if not n["uid"]:
            if n["name_uid"] and n["name_uid"] not in used and not n.get("duplicate_of"):
                n["uid"] = n["name_uid"]; used.add(n["uid"])      # 檔名前綴是舊身分,沿用
            else:
                n["uid"] = mint_uid(n["mtime_ms"], used); minted = True
        kv = n["kv"]; rel = n["rel"]
        folder_old = str(rel.parent) if str(rel.parent) != "." else ""
        frec = folder_recs.get(folder_old)
        rk_existing = (kv.get("routeKey") or "").strip().strip('"\'')
        rk = rk_existing or (n["meta"]["routeKey"] if n["meta"] and n["meta"].get("routeKey") else "") or (frec["routeKey_inherited"] if frec else "") or "core.inbox"
        cat_existing = (kv.get("category_uid") or "").strip().strip('"\'')
        cat = cat_existing or ("cat_" + re.sub(r"[^a-z0-9_]", "_", rk.replace(".", "_")))
        title_existing = (kv.get("title") or "").strip().strip('"\'')
        base_title = UID_PREFIX_RE.sub(r"\2", rel.stem) if UID_PREFIX_RE.match(rel.stem) else rel.stem
        title = title_existing or base_title
        aliases = fm_list(n["fm"], "aliases")
        for a in (n["uid"], rel.stem, base_title):
            if a and a not in aliases:
                aliases.append(a)
        legacy_paths = fm_list(n["fm"], "legacy_paths")
        orig_name_existing = (kv.get("orig_name") or "").strip().strip('"\'')
        created = (kv.get("created") or "").strip().strip('"\'') or iso(n["mtime_ms"])
        updated = (kv.get("updated") or "").strip().strip('"\'') or iso(n["mtime_ms"])
        source_kind = (kv.get("source_kind") or "").strip().strip('"\'') or ("legacy_obsidian" if (n["is_legacy"] or minted) else "manual")
        privacy = (kv.get("privacy_level") or "").strip().strip('"\'') or "internal"
        tags = fm_list(n["fm"], "tags")
        if n["is_legacy"] and "#ez2/imported" not in tags:
            tags.append("#ez2/imported")
        if n.get("duplicate_of") and "#ez2/duplicate" not in tags:
            tags.append("#ez2/duplicate")
        if n.get("collision") and "#ez2/uid-collision" not in tags:
            tags.append("#ez2/uid-collision")
        new_dir = frec["new_rel"] if frec else ""
        new_rel = str(Path(new_dir) / (n["uid"] + ".md")) if new_dir else n["uid"] + ".md"
        old_path_for_record = n["meta"]["legacy_rel"] if n["is_legacy"] else str(rel)
        if (n["is_legacy"] or str(rel) != new_rel) and old_path_for_record not in legacy_paths:
            legacy_paths.append(old_path_for_record)          # 只在這一次真的搬了/改名了才記,重跑不長
        plan["notes"].append({
            "old_rel": str(rel), "new_rel": new_rel, "uid": n["uid"], "minted": minted,
            "is_legacy": n["is_legacy"], "legacy_src": (str(legacy / n["meta"]["legacy_rel"]) if n["is_legacy"] else ""),
            "had_frontmatter": n["fm"] is not None, "duplicate_of": n.get("duplicate_of", ""),
            "folder_id": frec["folderId"] if frec else "",
            "fm_set": {"uid": n["uid"], "aliases": aliases, "title": title, "orig_name": orig_name_existing or rel.name,
                       "uid_scheme": "ez2-note-time-base36-v1", "routeKey": rk, "category_uid": cat,
                       "privacy_level": privacy, "source_kind": source_kind, "created": created, "updated": updated,
                       "ez2fmt": 2, "tags": tags, "legacy_paths": legacy_paths}})
    # 新路徑撞檔(同資料夾同 uid)
    seen_new: dict[str, str] = {}
    for nrec in plan["notes"]:
        if nrec["new_rel"] in seen_new:
            plan["blockers"].append({"new_rel": nrec["new_rel"], "paths": [seen_new[nrec["new_rel"]], nrec["old_rel"]], "reason": T("blocker_same_new_rel")})
        seen_new[nrec["new_rel"]] = nrec["old_rel"]
    # 正文連結:先算一遍,讓人在 apply 前看到要改幾處、幾處同名多篇不敢改
    planned = {n["old_rel"] for n in plan["notes"]} | {d["old_rel"] for d in plan["drops"]}
    plan["kept_md"] = sorted(str(Path(dp, f).relative_to(vault)) for dp, dn, fn in os.walk(vault)
                             if not any(part in SKIP_DIRS or part.startswith(".") for part in Path(dp).relative_to(vault).parts)
                             for f in fn if f.endswith(".md") and str(Path(dp, f).relative_to(vault)) not in planned)
    lr = LinkRewriter(vault, plan["notes"], plan["folders"], plan["drops"], plan["kept_md"])
    n_links, amb_all = 0, []
    for nrec in plan["notes"]:
        if nrec["is_legacy"]:
            continue
        p = vault / nrec["old_rel"]
        if not p.is_file():
            continue
        _, body = split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        _, edits, amb = lr.rewrite(body)
        n_links += len(edits); amb_all += amb
    # 角色庫的卡(角色.md 等)預設一 byte 不改 ⇒ 卡裡指向改名筆記的連結轉完在 Obsidian 解不開。
    # plan 帶 --rewrite-card-links 才改卡的正文連結(frontmatter 不動);卡片指紋會變,綁定時釘過舊指紋的席會亮「卡已更新」。
    plan["rewrite_card_links"] = bool(rewrite_card_links)
    plan["card_files"] = [rel for rel in plan["kept_md"] if under_library(rel)]
    card_links, card_files = 0, 0
    for rel in plan["card_files"]:
        _, body = split_frontmatter((vault / rel).read_text(encoding="utf-8", errors="replace"))
        k = len(lr.rewrite(body)[1])
        card_links += k; card_files += 1 if k else 0
    if card_links:
        plan["warnings"].append(T("warn_card_links_rewrite" if rewrite_card_links else "warn_card_links",
                                  {"count": card_links, "files": card_files}))
    if amb_all:
        plan["warnings"].append(T("warn_link_ambiguous", {"count": len(amb_all), "sample": ", ".join(sorted(set(amb_all))[:5])}))
    plan["obsidian_edits"] = obsidian_setting_edits(vault, plan["notes"], plan["folders"])
    plan["template_files"] = [rel for rel in plan["kept_md"]                       # 角色庫裡的照卡片規則走,不當範本改
                              if in_templates(rel, tpl_dirs, tpl_files) and not under_library(rel)]
    n_tpl = len(plan["template_files"])
    tpl_links = 0                                            # 範本正文裡的連結照改(範本沒有指紋;不改的話用它新建的筆記都帶斷鏈)
    for rel in plan["template_files"]:
        _, body = split_frontmatter((vault / rel).read_text(encoding="utf-8", errors="replace"))
        tpl_links += len(lr.rewrite(body)[1])
    if n_tpl:
        plan["warnings"].append(T("warn_template_kept", {"count": n_tpl}))
    attach = (load_json(vault / ".obsidian" / "app.json", {}) or {}).get("attachmentFolderPath")
    if isinstance(attach, str) and attach.startswith("./") and attach.strip("./"):
        plan["warnings"].append(T("warn_attach_relative", {"path": attach}))
    if (vault / ".obsidian" / "plugins" / "remotely-save" / "manifest.json").is_file():
        plan["warnings"].append(T("warn_remotely_save"))   # 只看有沒有裝;設定檔有帳密,不讀
    plan["stats"] = {
        "folders": len(plan["folders"]),
        "folders_route": sum(1 for f in plan["folders"] if f["kind"] == "route"),
        "folders_group": sum(1 for f in plan["folders"] if f["kind"] == "group"),
        "folders_sub": sum(1 for f in plan["folders"] if f["kind"] == "sub"),
        "folders_card": sum(1 for f in plan["folders"] if f["kind"] == "card"),
        "folders_keep": sum(1 for f in plan["folders"] if f["kind"] == "keep"),
        "notes": len(plan["notes"]),
        "notes_minted": sum(1 for n in plan["notes"] if n["minted"]),
        "notes_kept_uid": sum(1 for n in plan["notes"] if not n["minted"]),
        "notes_legacy": sum(1 for n in plan["notes"] if n["is_legacy"]),
        "notes_no_frontmatter_before": sum(1 for n in plan["notes"] if not n["had_frontmatter"]),
        "duplicates_renumbered": sum(1 for n in plan["notes"] if n["duplicate_of"]),
        "legacy_copies": len(plan["legacy_copies"]),
        "blockers": len(plan["blockers"]),
        "drops_identical": len(plan.get("drops", [])),
        "links_rewritten": n_links,
        "links_ambiguous": len(amb_all),
        "links_in_cards_not_rewritten": 0 if rewrite_card_links else card_links,
        "links_in_cards_rewritten": card_links if rewrite_card_links else 0,
        "folders_template": sum(1 for f in plan["folders"] if f["kind"] == "template"),
        "notes_template_kept": n_tpl,
        "links_in_templates_rewritten": tpl_links,
        "obsidian_settings": len(plan["obsidian_edits"]),
        "warnings": len(plan["warnings"]),
    }
    return plan


# ---------------------------------------------------------------- apply
def render_frontmatter(old_fm: str | None, fm_set: dict, folder_id: str, duplicate_of: str) -> str:
    keep = strip_managed(old_fm) if old_fm else []
    def uid_yaml(u: str) -> str:
        return '"%s"' % u if re.match(r"^[0-9]+(e[0-9]+)?$", u) or re.match(r"^[0-9]*e[0-9]+$", u) else u
    lines = ["---",
             "uid: %s" % uid_yaml(fm_set["uid"]),
             "aliases: %s" % yaml_list(fm_set["aliases"]),
             "title: %s" % yaml_str(fm_set["title"])]
    if fm_set.get("orig_name"):                 # 10-17:新筆記沒有「原名」(以前 newpost 寫 <uid>.md,沒意義)⇒ 不寫這欄
        lines.append("orig_name: %s" % yaml_str(fm_set["orig_name"]))
    lines += ["uid_scheme: %s" % fm_set["uid_scheme"],
             "routeKey: %s" % fm_set["routeKey"],
             "category_uid: %s" % fm_set["category_uid"],
             "folder_id: %s" % folder_id,
             "privacy_level: %s" % fm_set["privacy_level"],
             "source_kind: %s" % fm_set["source_kind"],
             "created: %s" % fm_set["created"],
             "updated: %s" % fm_set["updated"],
             "ez2fmt: %d" % fm_set["ez2fmt"],
             "tags: %s" % yaml_list(fm_set["tags"]),
             "legacy_paths: %s" % yaml_list(fm_set["legacy_paths"])]
    if duplicate_of:
        lines.append("duplicate_of: %s" % duplicate_of)
    lines += keep
    lines.append("---")
    return "\n".join(lines) + "\n"


def _merge_dir(src: Path, dst: Path, vault: Path, receipt: dict):
    """把 src 的內容併進已存在的 dst:新檔搬過去、同內容檔刪 src 的、同名目錄遞迴;src 清空就移除。回 (merged, kept)。"""
    merged, kept = 0, 0
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if not target.exists():
            os.rename(child, target); merged += 1
        elif child.is_dir() and target.is_dir():
            m, k = _merge_dir(child, target, vault, receipt); merged += m; kept += k
        elif child.is_file() and target.is_file() and hashlib.sha256(child.read_bytes()).hexdigest() == hashlib.sha256(target.read_bytes()).hexdigest():
            child.unlink(); merged += 1; receipt["files_deduped"] = receipt.get("files_deduped", 0) + 1
        else:
            kept += 1; receipt["errors"].append({"path": str(child.relative_to(vault)), "error": T("err_merge_differs", {"dst": str(target.relative_to(vault))})})
    if kept == 0 and not any(src.iterdir()):
        src.rmdir()
    return merged, kept


def is_routed(f: dict) -> bool:
    """有寫入路由的夾:轉換時的路由夾,或 folder-add --route-key 掛上自己路由代號的夾(inheritsFrom = 權限沿用的上層路由)。"""
    return bool(f.get("routeKey")) and (f.get("kind") == "route" or bool(f.get("inheritsFrom")))


def write_indexes(vault: Path, fid_items: list, note_labels: dict | None, uid_items: list | None, now: str):
    """從資料夾身分表寫出四份衍生索引:folder-id-index、route-key-index、routes v2(契約第三複本,同步 folderPath)、labels。
    note_labels / uid_items 給 None 表示沿用檔裡既有的(reindex 用)。"""
    idx_dir = vault / ".ez2" / "index"; idx_dir.mkdir(parents=True, exist_ok=True)
    disp_dir = vault / ".ez2" / "display"; disp_dir.mkdir(parents=True, exist_ok=True)
    routes_dir = vault / ".ez2" / "routes"
    by_id = {f["folderId"]: f for f in fid_items}
    rk_items = [dict({"routeKey": f["routeKey"], "folderId": f["folderId"], "folderPath": f["folderPath"], "module": module_of(f["folderPath"].split("/")[0][4:5] if f["folderPath"].startswith("fld_") else f["folderPath"].split("/")[0]).upper()},
                     **({"inheritsFrom": f["inheritsFrom"]} if f.get("inheritsFrom") else {}))
                for f in fid_items if is_routed(f)]
    old_labels = load_json(disp_dir / "labels.json", {})
    labels = {"schema": "2026-10-05.ez2-display-labels", "generatedAt": now,
              "folders": {f["folderId"]: {"title": f["title"], "i18n": ((old_labels.get("folders") or {}).get(f["folderId"]) or {}).get("i18n") or {"zh-TW": f["title"], "en": None, "ja": None, "ko": None}} for f in fid_items},
              "notes": note_labels if note_labels is not None else (old_labels.get("notes") or {})}
    (idx_dir / "folder-id-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-folder-id-index", "generatedAt": now, "items": fid_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    (idx_dir / "route-key-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-route-key-index", "generatedAt": now, "items": rk_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    if uid_items is not None:
        (idx_dir / "uid-index.json").write_text(json.dumps({"schema_version": "2026-10-05.ez2-uid-index", "generatedAt": now, "items": uid_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    (disp_dir / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    # routes v2(ez2-ai 外掛第一順位讀的那份):只同步 folderPath / parentFolderId,其餘欄位原樣;索引裡沒有的舊條目保留但標 status
    v2p = routes_dir / "obsidian-routes-v2.json"
    v2 = load_json(v2p, None)
    if isinstance(v2, dict) and isinstance(v2.get("routes"), list):
        seen = set()
        for r in v2["routes"]:
            f = by_id.get(r.get("folderId"))
            if f:
                r["folderPath"] = f["folderPath"]; r["parentFolderId"] = f["parentFolderId"]; r["level"] = f["level"]; seen.add(f["folderId"])
            else:
                r["status"] = "not-on-disk"
        for f in fid_items:
            if f["folderId"] not in seen and f.get("kind") in ("route", "group"):
                v2["routes"].append({"folderId": f["folderId"], "parentFolderId": f["parentFolderId"], "routeKey": f["routeKey"], "folderPath": f["folderPath"], "level": f["level"], "userEditableName": True})
        v2["generatedAt"] = now; v2["uidLayout"] = True
        v2p.write_text(json.dumps(v2, ensure_ascii=False, indent=1), encoding="utf-8")


def reindex(vault: Path) -> dict:
    """只重寫衍生索引(含 routes v2),不碰筆記、不改名;資料夾身分來自既有 folder-id-index。"""
    vault = vault.resolve()
    fid = load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []}).get("items", [])
    if not fid:
        raise SystemExit(T("err_no_folder_index"))
    missing = [f["folderPath"] for f in fid if not (vault / f["folderPath"]).is_dir()]
    write_indexes(vault, fid, None, None, time.strftime("%Y-%m-%d %H:%M:%S"))
    v2 = load_json(vault / ".ez2" / "routes" / "obsidian-routes-v2.json", {})
    return {"folders": len(fid), "paths_missing": missing, "routes_v2": len(v2.get("routes", [])) if isinstance(v2, dict) else None}


def apply_plan(plan: dict, backup_dir: Path, confirm_text: str) -> dict:
    accepted = {CONFIRM_TEXT, i18n_lib.t("aib.vault_convert.confirm_text", None, locale="zh-TW")}
    if confirm_text not in accepted:
        raise SystemExit(T("err_confirm_mismatch", {"text": CONFIRM_TEXT}))
    if plan["blockers"]:
        raise SystemExit(T("err_blockers", {"count": len(plan["blockers"])}))
    vault = Path(plan["vault"])
    backup_dir = backup_dir.resolve()
    if str(backup_dir).startswith(str(vault.resolve())):
        raise SystemExit(T("err_backup_inside"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tar_path = backup_dir / ("vault-before-uid-layout-%s.tgz" % stamp)
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(str(vault), arcname=vault.name)
    receipt = {"schema": "2026-10-05.ez2-uid-layout-receipt", "vault": str(vault), "backup_tar": str(tar_path),
               "started_at": iso(int(time.time() * 1000)), "legacy_copied": 0, "frontmatter_written": 0,
               "files_renamed": [], "dirs_renamed": [], "errors": []}

    # 0. 同內容副本先丟(計入 files_deduped)
    for d in plan.get("drops", []):
        p = vault / d["old_rel"]
        if p.is_file():
            p.unlink(); receipt["files_deduped"] = receipt.get("files_deduped", 0) + 1
    # 1. 舊庫導入
    for c in plan["legacy_copies"]:
        dst = vault / c["dst_rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(c["src"], dst); receipt["legacy_copied"] += 1

    # 2. frontmatter + 正文連結(在舊路徑上寫;連結改寫要在改名前,路徑型連結才查得到舊檔在不在)
    lr = LinkRewriter(vault, plan["notes"], plan["folders"], plan.get("drops", []), plan.get("kept_md", []))
    receipt["links_rewritten"] = 0; receipt["link_edits"] = []
    for n in plan["notes"]:
        p = vault / n["old_rel"]
        try:
            raw = p.read_bytes(); text = raw.decode("utf-8", errors="replace")
        except OSError as err:
            receipt["errors"].append({"path": n["old_rel"], "error": str(err)}); continue
        old_fm, body = split_frontmatter(text)
        head = render_frontmatter(old_fm, n["fm_set"], n["folder_id"], n["duplicate_of"])
        rest, edits, _ = lr.rewrite(body if old_fm is not None else "\n" + text if not text.startswith("\n") else text)
        new_text = head + rest
        tmp = p.with_name(p.name + ".tmp-uidlayout")
        tmp.write_bytes(new_text.encode("utf-8")); os.replace(tmp, p)
        receipt["frontmatter_written"] += 1
        if edits:
            receipt["links_rewritten"] += len(edits)
            receipt["link_edits"].append({"path": n["old_rel"], "edits": [[pos + len(head), new, old] for pos, new, old in edits]})
    # 2b. 角色卡(plan 帶 --rewrite-card-links 才做)與範本的正文連結:frontmatter 一字不動,undo 照位置退回
    receipt["card_links_rewritten"] = 0; receipt["template_links_rewritten"] = 0
    cards = set((plan.get("card_files") or []) if plan.get("rewrite_card_links") else [])
    for rel in sorted(cards) + [t for t in plan.get("template_files") or [] if t not in cards]:
        p = vault / rel
        try:
            text = p.read_bytes().decode("utf-8", errors="replace")
        except OSError as err:
            receipt["errors"].append({"path": rel, "error": str(err)}); continue
        _, body = split_frontmatter(text)
        head = text[:len(text) - len(body)]
        rest, edits, _ = lr.rewrite(body)
        if edits:
            tmp = p.with_name(p.name + ".tmp-uidlayout")
            tmp.write_bytes((head + rest).encode("utf-8")); os.replace(tmp, p)
            receipt["card_links_rewritten" if rel in cards else "template_links_rewritten"] += len(edits)
            receipt["links_rewritten"] += len(edits)
            receipt["link_edits"].append({"path": rel, "edits": [[pos + len(head), new, old] for pos, new, old in edits]})

    # 3. 檔案改名(在舊資料夾裡)
    for n in plan["notes"]:
        src = vault / n["old_rel"]
        dst = src.with_name(n["uid"] + ".md")
        if src == dst:
            continue
        if dst.exists():
            if hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(dst.read_bytes()).hexdigest():
                src.unlink(); receipt.setdefault("files_deduped", 0); receipt["files_deduped"] += 1; continue
            receipt["errors"].append({"path": n["old_rel"], "error": T("err_dst_exists_differs", {"name": dst.name})}); continue
        os.rename(src, dst); receipt["files_renamed"].append([str(src.relative_to(vault)), str(dst.relative_to(vault))])

    # 4. 資料夾改名(最深的先)
    for f in sorted(plan["folders"], key=lambda r: (-r["level"], r["old_rel"])):
        # 父層還是舊名(因為由深到淺),所以用 舊父路徑 + 新 basename
        old = vault / f["old_rel"]
        if not old.exists():
            receipt["errors"].append({"path": f["old_rel"], "error": T("err_dir_missing")}); continue
        new = old.with_name(f["folderId"])
        if new == old:
            continue
        if new.exists():
            # 目的夾已在(例如另一台已轉好再 rsync 過來):遞迴併入,同名檔內容相同就丟舊的,不同就留著報錯
            merged, kept = _merge_dir(old, new, vault, receipt)
            receipt.setdefault("dirs_merged", []).append([str(old.relative_to(vault)), str(new.relative_to(vault)), merged, kept])
            continue
        os.rename(old, new); receipt["dirs_renamed"].append([str(old.relative_to(vault)), str(new.relative_to(vault))])

    # 5. 索引、顯示名、狀態、undo(寫之前先把原本的 .ez2 狀態檔留一份,undo 放回去)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    pre_dir = vault / ".ez2" / "migration" / ("pre-%s" % stamp)
    receipt["ez2_before_dir"] = str(pre_dir.relative_to(vault))
    receipt["ez2_before"] = {}
    for rel in EZ2_STATE_FILES:
        src = vault / rel
        receipt["ez2_before"][rel] = src.is_file()
        if src.is_file():
            (pre_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, pre_dir / rel)
    # Obsidian 設定裡的舊路徑(原檔先留一份,undo 時檔案沒被動過就整檔放回,byte 不差)
    receipt["obsidian_edits"] = plan.get("obsidian_edits", [])
    for name in sorted({e["file"] for e in receipt["obsidian_edits"]}):
        if (vault / ".obsidian" / name).is_file():
            (pre_dir / ".obsidian").mkdir(parents=True, exist_ok=True)
            shutil.copy2(vault / ".obsidian" / name, pre_dir / ".obsidian" / name)
    receipt["obsidian_settings_written"] = apply_obsidian_edits(vault, receipt["obsidian_edits"], forward=True)
    receipt["obsidian_after_sha"] = {name: hashlib.sha256((vault / ".obsidian" / name).read_bytes()).hexdigest()
                                     for name in {e["file"] for e in receipt["obsidian_edits"]} if (vault / ".obsidian" / name).is_file()}
    fid_items = []
    for f in plan["folders"]:
        prev = {}
        for it in load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []}).get("items", []):
            if it.get("folderId") == f["folderId"]:
                prev = it; break
        legacy = list(dict.fromkeys((prev.get("legacyPaths") or []) + ([f["old_rel"]] if f["old_rel"] != f["new_rel"] else [])))
        fid_items.append({"folderId": f["folderId"], "routeKey": f["routeKey"], "folderPath": f["new_rel"],
                          "parentFolderId": f["parentFolderId"], "level": f["level"], "title": f["title"],
                          "legacyPaths": legacy, "status": "active", "kind": f["kind"]})
    note_labels = {n["uid"]: {"title": n["fm_set"]["title"], "i18n": {"zh-TW": n["fm_set"]["title"], "en": None, "ja": None, "ko": None}, "path": n["new_rel"]} for n in plan["notes"]}
    uid_items = [{"uid": n["uid"], "path": n["new_rel"], "title": n["fm_set"]["title"], "routeKey": n["fm_set"]["routeKey"], "folderId": n["folder_id"]} for n in plan["notes"]]
    write_indexes(vault, fid_items, note_labels, uid_items, now)
    ms_path = vault / ".ez2" / "migration-state.json"
    ms = load_json(ms_path, {})
    ms.update({"updated_at": now, "mode": "uid_layout", "formal_rename": True, "folder_moves": False, "uid_layout_applied_at": now,
               "uid_layout_receipt": str((vault / ".ez2" / "migration" / ("uid-layout-receipt-%s.json" % stamp)).relative_to(vault))})
    ms_path.write_text(json.dumps(ms, ensure_ascii=False, indent=1), encoding="utf-8")
    mig_dir = vault / ".ez2" / "migration"; mig_dir.mkdir(parents=True, exist_ok=True)
    receipt["finished_at"] = iso(int(time.time() * 1000))
    rpath = mig_dir / ("uid-layout-receipt-%s.json" % stamp)
    rpath.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(rpath, backup_dir / rpath.name)
    receipt["receipt_path"] = str(rpath)
    return receipt


# ---------------------------------------------------------------- undo
def undo(receipt_path: Path) -> dict:
    r = json.loads(receipt_path.read_text(encoding="utf-8"))
    vault = Path(r["vault"]); out = {"dirs_restored": 0, "files_restored": 0, "errors": []}
    for old, new in reversed(r["dirs_renamed"]):          # 淺的先回(反向)
        src, dst = vault / new, vault / old
        try:
            os.rename(src, dst); out["dirs_restored"] += 1
        except OSError as err:
            out["errors"].append({"dir": new, "error": str(err)})
    for old, new in reversed(r["files_renamed"]):
        src, dst = vault / new, vault / old
        try:
            os.rename(src, dst); out["files_restored"] += 1
        except OSError as err:
            out["errors"].append({"file": new, "error": str(err)})
    # 正文連結(檔已回到舊路徑)
    out["links_restored"], out["links_fuzzy"] = 0, 0
    for fe in r.get("link_edits", []):
        p = vault / fe["path"]
        try:
            text = p.read_bytes().decode("utf-8", errors="replace")   # 不經換行轉換:CRLF 的位置要跟 apply 時一樣
        except OSError as err:
            out["errors"].append({"file": fe["path"], "error": str(err)}); continue
        new_text, done, exact = undo_link_edits(text, fe["edits"])
        if new_text != text:
            tmp = p.with_name(p.name + ".tmp-uidlayout")
            tmp.write_bytes(new_text.encode("utf-8")); os.replace(tmp, p)
        out["links_restored"] += done
        if not exact:
            out["links_fuzzy"] += done
    # Obsidian 設定:apply 之後沒人動過 ⇒ 整檔放回原檔;動過 ⇒ 只把我們改的那幾格(值還是我們寫的)改回去
    pre_dir = vault / r["ez2_before_dir"] if r.get("ez2_before_dir") else None
    out["obsidian_settings_restored"], by_key = 0, []
    for name in sorted({e["file"] for e in r.get("obsidian_edits", [])}):
        es = [e for e in r["obsidian_edits"] if e["file"] == name]
        cur, orig = vault / ".obsidian" / name, (pre_dir / ".obsidian" / name) if pre_dir else None
        if orig and orig.is_file() and cur.is_file() and hashlib.sha256(cur.read_bytes()).hexdigest() == (r.get("obsidian_after_sha") or {}).get(name):
            shutil.copy2(orig, cur); out["obsidian_settings_restored"] += len(es)
        else:
            by_key += es
    out["obsidian_settings_restored"] += apply_obsidian_edits(vault, by_key, forward=False)
    # .ez2 狀態檔放回轉換前(原本沒有的刪掉)⇒ migration-state 不再說是 uid 版面
    out["ez2_restored"] = 0
    for rel, existed in (r.get("ez2_before") or {}).items():
        cur = vault / rel
        try:
            if existed:
                shutil.copy2(pre_dir / rel, cur)
            elif cur.is_file():
                cur.unlink()
            out["ez2_restored"] += 1
        except OSError as err:
            out["errors"].append({"file": rel, "error": str(err)})
    out["note"] = T("undo_note", {"backup": r["backup_tar"]})
    return out


# ---------------------------------------------------------------- verify
def verify(vault: Path, cards_path=None) -> dict:
    vault = vault.resolve()
    res = {"vault": str(vault), "dirs_total": 0, "dirs_not_folder_id": [], "md_total": 0, "md_not_uid_name": [],
           "md_uid_mismatch": [], "md_no_uid": [], "uid_duplicates": [], "index_ok": {}, "ok": False}
    uids: dict[str, list] = {}
    rk = load_json(vault / ".ez2" / "index" / "route-key-index.json", {"items": []})
    lib_new = next((it["folderPath"] for it in rk.get("items", []) if it.get("routeKey") == "product.expert.agent_library"), None)
    def in_library(rel: str) -> bool:
        return bool(lib_new) and (rel == lib_new or rel.startswith(lib_new + "/"))
    fid0 = load_json(vault / ".ez2" / "index" / "folder-id-index.json", {"items": []})
    registered_keep = {it["folderPath"] for it in fid0.get("items", []) if it.get("kind") == "keep"}
    tpl_dirs, tpl_files = template_paths(vault)                 # 範本:名字不動、不補 uid(位置照 .obsidian 現值)
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = str(Path(dp).relative_to(vault))
        for d in dn:
            res["dirs_total"] += 1
            rel_d = str(Path(dp, d).relative_to(vault))
            if in_library(rel_dir):
                depth = len(Path(rel_d).parts) - len(Path(lib_new).parts)
                if depth == 1 and not (UID_RE.match(d) or d.startswith("_") or rel_d in registered_keep):
                    res["dirs_not_folder_id"].append(rel_d)
                elif depth == 1 and rel_d in registered_keep:
                    res.setdefault("library_dirs_without_card_uid", []).append(rel_d)
                continue
            if not FOLDER_ID_RE.match(d) and not in_templates(rel_d, tpl_dirs, set()):
                res["dirs_not_folder_id"].append(rel_d)
        if in_library(rel_dir) or in_templates(rel_dir, tpl_dirs, set()):
            continue
        for f in fn:
            if not f.endswith(".md") or in_templates(Path(dp, f).relative_to(vault), set(), tpl_files):
                continue
            res["md_total"] += 1
            rel = str(Path(dp, f).relative_to(vault))
            stem = f[:-3]
            if not UID_RE.match(stem):
                res["md_not_uid_name"].append(rel)
            fm, _ = split_frontmatter(Path(dp, f).read_text(encoding="utf-8", errors="replace"))
            kv = fm_lines_to_map(fm) if fm is not None else {}
            u = (kv.get("uid") or "").strip().strip('"\'')
            if not UID_RE.match(u):
                res["md_no_uid"].append(rel)
            else:
                uids.setdefault(u, []).append(rel)
                if UID_RE.match(stem) and u != stem:
                    res["md_uid_mismatch"].append(rel)
    res["uid_duplicates"] = [{"uid": k, "paths": v} for k, v in uids.items() if len(v) > 1]
    idx = vault / ".ez2" / "index"
    for name in ("folder-id-index.json", "route-key-index.json", "uid-index.json"):
        d = load_json(idx / name, None)
        res["index_ok"][name] = bool(d and d.get("items"))
    fid = load_json(idx / "folder-id-index.json", {"items": []})
    missing = [it["folderPath"] for it in fid.get("items", []) if not (vault / it["folderPath"]).is_dir()]
    res["index_paths_missing"] = missing
    rk = load_json(idx / "route-key-index.json", {"items": []})
    res["route_paths_missing"] = [it["routeKey"] for it in rk.get("items", []) if not (vault / it["folderPath"]).is_dir()]
    v2 = load_json(vault / ".ez2" / "routes" / "obsidian-routes-v2.json", None)
    res["routes_v2_stale"] = [r.get("folderId") for r in (v2.get("routes") if isinstance(v2, dict) else []) or [] if r.get("status") != "not-on-disk" and not (vault / str(r.get("folderPath", ""))).is_dir()]
    lab = load_json(vault / ".ez2" / "display" / "labels.json", {})
    res["labels"] = {"folders": len(lab.get("folders", {})), "notes": len(lab.get("notes", {}))}
    res.update(check_cards(vault, cards_path))
    res["ok"] = (not res["dirs_not_folder_id"] and not res["md_not_uid_name"] and not res["md_uid_mismatch"]
                 and not res["md_no_uid"] and not res["uid_duplicates"] and all(res["index_ok"].values())
                 and not missing and not res["route_paths_missing"] and not res["routes_v2_stale"]
                 and not res["cards_path_missing"])
    return res


def default_cards_path() -> Path:
    """aib 的卡片索引(<狀態根>/identity/cards.json;狀態根 = EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。"""
    root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    return Path(root) / "identity" / "cards.json"


def check_cards(vault: Path, cards_path=None) -> dict:
    """10-15 盤點 U6:轉換只改 vault,卡片索引的 card_path_rel 還指著舊卡夾 ⇒ 7 處讀不到卡。
    只檢查「這份索引掃的就是這個 vault」的情形;別的 vault 的索引或沒有索引 ⇒ cards_checked=False,不判。"""
    path = Path(cards_path) if cards_path else default_cards_path()
    out = {"cards_checked": False, "cards_path_missing": [], "cards_index": str(path)}
    doc = load_json(path, None)
    if not isinstance(doc, dict):
        return out
    src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    try:
        same = Path(str(src.get("vault_root") or "")).expanduser().resolve() == vault.resolve()
    except OSError:
        same = False
    if not same:
        return out
    out["cards_checked"] = True
    cards = doc.get("cards") if isinstance(doc.get("cards"), dict) else {}
    for uid, c in cards.items():
        rel = str((c or {}).get("card_path_rel") or "")
        if rel and not (vault / rel).is_file():
            out["cards_path_missing"].append(uid)
    if out["cards_path_missing"]:
        out["cards_hint"] = "python3 %s scan --vault %s" % (
            Path(__file__).resolve().parents[2] / "src" / "memory" / "card_registry.py", vault)
    return out


def rescan_cards(vault: Path) -> dict:
    """apply / undo 帶 --rescan-cards 才跑:重掃 aib 卡片索引,讓 card_path_rel 指到改名後的卡夾。
    不預設跑:測試在暫存 vault 上 apply,預設重掃會把假卡寫進真狀態根。"""
    reg = Path(__file__).resolve().parents[2] / "src" / "memory" / "card_registry.py"
    p = subprocess.run([sys.executable, str(reg), "scan", "--vault", str(vault)],
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    return {"rc": p.returncode, "tail": (p.stdout + p.stderr).strip().splitlines()[-3:]}


# ---------------------------------------------------------------- CLI
# ---------------------------------------------------------------- 轉換後新增 / 退役資料夾(10-16)
# uid 庫的每個資料夾都要登記在 folder-id-index;直接 mkdir / rm 會讓 verify 變紅(索引與硬碟對不上)。
def _fid_load(vault: Path):
    p = vault / ".ez2" / "index" / "folder-id-index.json"
    doc = load_json(p, None)
    if not isinstance(doc, dict) or not doc.get("items"):
        raise SystemExit(T("err_no_folder_index"))
    return doc


def _legacy_of(item: dict, by_id: dict) -> str:
    """這個資料夾的「舊路徑字面」(算 sha12 用):有 legacyPaths 用第一條,沒有就用祖先標題串起來。"""
    lp = item.get("legacyPaths") or []
    if lp:
        return lp[0]
    parent = by_id.get(item.get("parentFolderId") or "")
    return (_legacy_of(parent, by_id) + "/" if parent else "") + item.get("title", item["folderId"])


def _route_contract_keys() -> set | None:
    """路由契約(不含 Vault 自掛的路由)的 routeKey 集合;讀不到 ⇒ None。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "persona_route"))
    import route_register
    return route_register.contract_route_keys(route_register.load_contract(with_vault_routes=False))


def _route_for(items: list, by_id: dict, parent: str, route_key: str, own: str = "") -> str:
    """--route-key 的檢查 ⇒ 權限沿用的上層路由代號(最近一層轉換時的路由夾)。不合就 SystemExit。
    契約只有維護者能改;Vault 自掛的路由不另給權限,一律沿用上層那條(dataLevel / aiWritable 都照它)。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "persona_route"))
    import route_register
    try:
        route_register.validate_route_key(route_key)
    except route_register.RouteError:
        raise SystemExit(T("err_route_key_bad", {"key": route_key}))
    taken = next((i for i in items if i.get("routeKey") == route_key and i["folderId"] != own), None)
    if taken:
        raise SystemExit(T("err_route_key_taken", {"key": route_key, "folder": taken.get("title") or taken["folderId"]}))
    keys = _route_contract_keys()
    if keys is None:
        raise SystemExit(T("err_route_no_contract"))
    if route_key in keys:
        raise SystemExit(T("err_route_key_in_contract", {"key": route_key}))
    cur = by_id.get(parent)
    while cur is not None:
        if cur.get("kind") == "route" and cur.get("routeKey") in keys:
            return cur["routeKey"]
        cur = by_id.get(cur.get("parentFolderId") or "")
    raise SystemExit(T("err_route_no_parent", {"key": route_key}))


def folder_add(vault: Path, parent: str, title: str, dry_run: bool = False, route_key: str = "") -> dict:
    """在 parent(folderId;空字串 = 庫根)底下新增資料夾並登記。同一個父夾已有同名 ⇒ 回那一個,不重複建。
    route_key:給這個夾自己的路由代號(newproj --folder / aib memory write --route 才認得);
    權限沿用最近一層上層路由。已有同名夾且還沒有代號 ⇒ 補掛上去;已掛別的代號 ⇒ 擋。"""
    vault = vault.resolve()
    title = unicodedata.normalize("NFC", (title or "").strip())
    route_key = (route_key or "").strip()
    if not title or "/" in title or "\\" in title or title in (".", "..") or title.startswith("."):
        raise SystemExit(T("err_folder_title_bad", {"title": title}))
    doc = _fid_load(vault)
    items = doc["items"]
    by_id = {i["folderId"]: i for i in items}
    if parent and parent not in by_id:
        raise SystemExit(T("err_folder_unknown", {"folder": parent}))
    same = next((i for i in items if (i.get("parentFolderId") or "") == (parent or "")
                 and i.get("title") == title and i.get("status", "active") == "active"), None)
    if same:
        out = {"folderId": same["folderId"], "folderPath": same["folderPath"], "title": title, "created": False}
        if not route_key or same.get("routeKey") == route_key:
            return dict(out, **({"routeKey": route_key, "inheritsFrom": same.get("inheritsFrom", ""), "route_added": False}
                                if route_key else {}))
        if same.get("routeKey"):
            raise SystemExit(T("err_folder_has_route", {"folder": title, "key": same["routeKey"]}))
        inherits = _route_for(items, by_id, parent, route_key, own=same["folderId"])
        if not dry_run:
            same.update({"routeKey": route_key, "inheritsFrom": inherits})
            write_indexes(vault, items, None, None, time.strftime("%Y-%m-%d %H:%M:%S"))
        return dict(out, routeKey=route_key, inheritsFrom=inherits, route_added=not dry_run, dry_run=dry_run)
    inherits = _route_for(items, by_id, parent, route_key) if route_key else ""
    if parent:
        par = by_id[parent]
        legacy = _legacy_of(par, by_id) + "/" + title
        mod = par["folderId"].split("_")[1]
        fid, kind, rk = "fld_%s_sub_%s" % (mod, sha12(legacy)), "sub", ""
        path, level = par["folderPath"] + "/" + fid, int(par.get("level") or 1) + 1
    else:
        legacy = title
        mod = module_of(title)
        fid, kind, rk = "fld_%s_group_%s" % (mod, sha12(legacy)), "group", "folder.group.%s.%s" % (mod, sha12(legacy)[:8])
        path, level = fid, 1
    if fid in by_id:                     # 同字面路徑以前登記過(例如退役後又加回來時已被別處用掉)
        raise SystemExit(T("err_folder_id_taken", {"folder": fid}))
    item = {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent or "", "level": level,
            "title": title, "legacyPaths": [legacy], "status": "active", "kind": kind}
    if route_key:
        item.update({"routeKey": route_key, "inheritsFrom": inherits})
    if not dry_run:
        if parent and not (vault / by_id[parent]["folderPath"]).is_dir():
            raise SystemExit(T("err_dir_missing"))
        (vault / path).mkdir(parents=False, exist_ok=True)
        items.append(item)
        write_indexes(vault, items, None, None, time.strftime("%Y-%m-%d %H:%M:%S"))
    return dict({"folderId": fid, "folderPath": path, "title": title, "created": not dry_run, "dry_run": dry_run},
                **({"routeKey": route_key, "inheritsFrom": inherits} if route_key else {}))


def _links_to(vault: Path, uids: set, skip_prefix: str) -> list:
    """庫裡(skip_prefix 以外)連到這些 uid 的筆記:[[uid]] / [[uid|…]] / [[uid#…]] 與 (…/uid.md)。"""
    if not uids:
        return []
    pat = re.compile(r"\[\[(%s)(?:[|#\]])|[(/](%s)\.md\b" % ("|".join(map(re.escape, uids)), "|".join(map(re.escape, uids))))
    hits = []
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = str(Path(dp).relative_to(vault))
        if rel_dir == skip_prefix or rel_dir.startswith(skip_prefix + "/"):
            continue
        for f in fn:
            if f.endswith(".md"):
                rel = str(Path(rel_dir, f)) if rel_dir != "." else f
                text = Path(dp, f).read_text(encoding="utf-8", errors="replace")
                m = pat.search(text)
                if m:
                    hits.append({"from": rel, "uid": m.group(1) or m.group(2)})
    return hits


def folder_retire(vault: Path, folder: str, backup_dir: Path | None, include_notes: bool = False,
                  dry_run: bool = False) -> dict:
    """退役一個資料夾(連子夾):先打包到庫外,再從 folder-id-index / uid-index / labels 拿掉、刪掉資料夾。
    擋下:路由資料夾(契約寫入點)、裡面有筆記但沒帶 --include-notes、筆記還被別處連結、其他檔案。
    資料夾已經不在硬碟上(以前直接刪掉的)⇒ 只清索引,verify 就會回綠。"""
    vault = vault.resolve()
    doc = _fid_load(vault)
    items = doc["items"]
    top = next((i for i in items if i["folderId"] == folder), None)
    if top is None:
        raise SystemExit(T("err_folder_unknown", {"folder": folder}))
    sub = [i for i in items if i["folderPath"] == top["folderPath"] or i["folderPath"].startswith(top["folderPath"] + "/")]
    routes = [i["routeKey"] for i in sub if is_routed(i)]
    if routes:
        raise SystemExit(T("err_folder_is_route", {"routes": ", ".join(routes)}))
    root = vault / top["folderPath"]
    on_disk = root.is_dir()
    notes, others = [], []
    if on_disk:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != ".DS_Store":
                (notes if p.suffix == ".md" and UID_RE.match(p.stem) else others).append(str(p.relative_to(vault)))
    if others:
        raise SystemExit(T("err_folder_has_files", {"count": len(others), "files": ", ".join(others[:5])}))
    if notes and not include_notes:
        raise SystemExit(T("err_folder_has_notes", {"count": len(notes), "files": ", ".join(notes[:5])}))
    uids = {Path(n).stem for n in notes}
    linked = _links_to(vault, uids, top["folderPath"])
    if linked:
        raise SystemExit(T("err_notes_linked", {"count": len(linked),
                                               "links": ", ".join("%s → %s" % (h["from"], h["uid"]) for h in linked[:5])}))
    out = {"folderId": folder, "folders": len(sub), "notes": sorted(uids), "on_disk": on_disk, "dry_run": dry_run, "backup": None}
    if dry_run:
        return out
    if on_disk:
        if backup_dir is None:
            raise SystemExit(T("err_backup_required"))
        backup_dir = backup_dir.resolve()
        if backup_dir == vault or vault in backup_dir.parents:
            raise SystemExit(T("err_backup_inside"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        tgz = backup_dir / ("retire-%s-%s.tgz" % (folder, time.strftime("%Y%m%d-%H%M%S")))
        with tarfile.open(tgz, "w:gz") as tar:
            tar.add(str(root), arcname=top["folderPath"])
        out["backup"] = str(tgz)
    gone = {i["folderId"] for i in sub}
    doc["items"] = [i for i in items if i["folderId"] not in gone]
    idx = vault / ".ez2" / "index"
    uidx = load_json(idx / "uid-index.json", None)
    if isinstance(uidx, dict) and uids:
        uidx["items"] = [i for i in uidx.get("items", []) if i.get("uid") not in uids]
        (idx / "uid-index.json").write_text(json.dumps(uidx, ensure_ascii=False, indent=1), encoding="utf-8")
    lab = load_json(vault / ".ez2" / "display" / "labels.json", {})
    notes_lab = {k: v for k, v in (lab.get("notes") or {}).items() if k not in uids}
    if on_disk:
        shutil.rmtree(root)
    write_indexes(vault, doc["items"], notes_lab, None, time.strftime("%Y-%m-%d %H:%M:%S"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=T("desc"), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--vault", required=True); p.add_argument("--contract", required=True)
    p.add_argument("--legacy"); p.add_argument("--legacy-map"); p.add_argument("--out", required=True)
    p.add_argument("--rewrite-card-links", action="store_true",
                   help="角色卡正文裡的連結也改指 uid(卡片指紋會變,釘過舊指紋的席會亮「卡已更新」;apply 時要帶 --rescan-cards)")
    a = sub.add_parser("apply"); a.add_argument("--plan", required=True); a.add_argument("--backup-dir", required=True); a.add_argument("--confirm-text", required=True)
    a.add_argument("--rescan-cards", action="store_true", help="apply 完重掃 aib 卡片索引(真庫轉換時要帶)")
    v = sub.add_parser("verify"); v.add_argument("--vault", required=True); v.add_argument("--cards", help="aib 卡片索引路徑(預設狀態根)")
    u = sub.add_parser("undo"); u.add_argument("--receipt", required=True)
    u.add_argument("--rescan-cards", action="store_true", help="退回後重掃 aib 卡片索引")
    ri = sub.add_parser("reindex"); ri.add_argument("--vault", required=True)
    fa = sub.add_parser("folder-add", help=T("help_folder_add"))
    fa.add_argument("--vault", required=True); fa.add_argument("--title", required=True)
    fa.add_argument("--parent", default="", help=T("help_folder_parent"))
    fa.add_argument("--route-key", default="", help=T("help_folder_route_key"))
    fa.add_argument("--dry-run", action="store_true")
    fr = sub.add_parser("folder-retire", help=T("help_folder_retire"))
    fr.add_argument("--vault", required=True); fr.add_argument("--folder", required=True)
    fr.add_argument("--backup-dir", help=T("help_folder_backup"))
    fr.add_argument("--include-notes", action="store_true", help=T("help_folder_include_notes"))
    fr.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        lm = json.loads(Path(args.legacy_map).read_text(encoding="utf-8")) if args.legacy_map else None
        plan = build_plan(Path(args.vault), args.contract, Path(args.legacy) if args.legacy else None, lm,
                          rewrite_card_links=args.rewrite_card_links)
        Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"out": args.out, "stats": plan["stats"], "blockers": plan["blockers"][:5], "warnings": plan["warnings"][:5]}, ensure_ascii=False, indent=1))
        return 0 if not plan["blockers"] else 3
    if args.cmd == "apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        rec = apply_plan(plan, Path(args.backup_dir), args.confirm_text)
        if args.rescan_cards and not rec.get("errors"):
            rec["cards_rescan"] = rescan_cards(Path(plan["vault"]))
        print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in rec.items()}, ensure_ascii=False, indent=1))
        return 0 if not rec["errors"] else 4
    if args.cmd == "verify":
        res = verify(Path(args.vault), args.cards)
        print(json.dumps({k: (v if not isinstance(v, list) else (v if len(v) <= 8 else v[:8] + ["…+%d" % (len(v) - 8)])) for k, v in res.items()}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 5
    if args.cmd == "undo":
        out = undo(Path(args.receipt))
        if args.rescan_cards and not out.get("errors"):
            out["cards_rescan"] = rescan_cards(Path(json.loads(Path(args.receipt).read_text(encoding="utf-8"))["vault"]))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "reindex":
        print(json.dumps(reindex(Path(args.vault)), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "folder-add":
        print(json.dumps(folder_add(Path(args.vault), args.parent, args.title, args.dry_run, args.route_key), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "folder-retire":
        out = folder_retire(Path(args.vault), args.folder, Path(args.backup_dir) if args.backup_dir else None,
                            args.include_notes, args.dry_run)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
