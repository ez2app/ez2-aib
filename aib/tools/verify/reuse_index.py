#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""沿用索引:每支測試自己的輸入指紋 + 「檔 → 讀過它的測試」反查表(設計稿 §5 第 2 條)。

## 這支在修什麼

沿用鑰匙原本綁在**整棵樹**:`tools/verify/shadow_tracer.py` 把列寫成
`<tree_sha>.rows.jsonl`,呼叫端要自己指定 `--previous <那一份>`。
於是「上一輪」只有同一棵樹才找得到 —— **跨 commit 結構上沿用不到**,
而跨 commit 正是修紅迴圈每一次都在做的事。

本檔補兩件,都不動 shadow_tracer(它是熱檔,而且不在本包的寫入範圍):

1. **跨樹的上一輪**:把 shadow 目錄底下**所有**列檔讀進來,依檔案 mtime 由舊到新
   疊,得到每支測試的「最近一次量到的列」。哪一棵樹無所謂,鑰匙改由下面第 2 條判。
2. **反查表**:`檔 → 讀過它的測試`。來源是列裡的 `inputs`,那是追蹤器**實際量到**
   的讀取,不是靜態猜的。`tools/verify/select_changed.py` 用它補靜態挑選漏掉的。

## 沿用的鑰匙(比 shadow_tracer.reuse_row 嚴,永遠不比它鬆)

`shadow_tracer.reuse_row` 已經會對**現在的樹**重算輸入指紋(V3 收尾那一刀),
本檔照呼叫它那兩支純函式,不另寫一份重算邏輯。本檔只多壓一層:

    lock_fp = 專案 lockfile 的內容雜湊

為什麼要多這層:`env_fingerprint` 雜湊的是直譯器/node/tmux/git 版本與整份環境,
**但 lockfile 只有在測試自己讀過它時才會進 inputs**。python 專案幾乎沒有測試會去
讀 `package-lock.json`,而換掉鎖檔等於換掉一整組依賴 —— 那是會改變結果的輸入。

    ★ 舊列沒有 `lock_fp` 欄怎麼辦:**看這棵樹現在有沒有 lockfile**。
      一支都沒有 ⇒ 「缺欄」與「當時也是空的」是同一件事(空集合的雜湊只有一個值),
      判相等、准沿用。有 lockfile 而舊列缺欄 ⇒ **當時是什麼不知道** ⇒ 必跑一次;
      跑完的新列帶著 `lock_fp`,下一輪就對得上。自己會好,不必人手清池。
      (AIB 自己今天只有 tools/requirements-codexbot.txt,沒有任何鎖檔 ⇒ 這一層在本庫
       實質上不咬人。它是替客戶的 node/php 專案準備的,**本庫沒有量到它咬過**。)

## 必跑的判準(順序就是回報裡的理由;第一個成立的就是理由)

    no-previous          反查不到上一輪  → 必跑
    unmeasured           上一輪量不到     → 必跑(第 96 項:量不到的一律真跑)
    external             上一輪走外部     → 必跑
    previous-red         上一輪 rc 非 0   → 必跑 ★ 紅的永遠不沿用(設計稿 §5 第 4 條)
    test-modified        這次改到它自己   → 必跑
    input-changed        這次改到它讀過的檔 → 必跑
    fingerprint-shrink   指紋比上一輪短   → 必跑(少讀了東西 = 量到的範圍縮了)
    input-fp-changed     對現在的樹重算對不上 → 必跑
    env-fp-changed       環境指紋變了     → 必跑
    lock-fp-changed      鎖檔變了/舊列缺欄而本樹有鎖檔 → 必跑
    row-age-unknown / row-from-future / row-expired
                         列沒有 UTC 時間 / 時間在未來 / 超過有效期限(預設 72 小時)→ 必跑(10-04)
    date-crossed         它讀過的倉內檔寫著某個日期,而從那列到現在之間跨過了那一天 → 必跑(10-04)
    (最新一列只是指紋對不上 ⇒ 往回找鑰匙對得上的舊列;找到的那把鑰匙在池裡出現過紅/逾時 ⇒)
    unstable-same-key    同樣條件時好時壞 → 必跑(10-02 第 2 步,多台機器共用池)
    (以上都不成立)       → reuse

## 失效方向

- 讀不到列檔、shadow_tracer import 不進來、重算炸掉 ⇒ **整支回「全部必跑」**,
  不是回空反查表。窄的反查表與「這些檔沒有人讀過」在輸出上逐字相同,而前者會讓
  該跑的沒跑;寬的只是慢。
- 反查表結構上的下界:只有**被追蹤器量到**的測試才進得了表。09-13 實測約 33% 量得到,
  所以反查表永遠是下界,不是全集 —— 它的用途是**補**靜態挑選,不是取代。

## 介面

    reuse_index.py reverse --repo . [--rows P]...   < 改動檔路徑,一行一個
        → JSON {"reverse_hits": {檔: [測試…]}, "tests": [測試…], "rows_files": N, "tests_indexed": N}
    reuse_index.py decide  --repo . [--rows P]... [--changed F] [--tests F]
        → JSON {"decisions": [{test, verdict, reason, …}], "counts": {...}}
    reuse_index.py stats   --repo . [--rows P]...
        → JSON {"rows_files": N, "tests_indexed": N, "measured": N, "paths_indexed": N}

rc:0 算出來了 / 2 參數不合法 / 3 量不到(此時 decide 的每一支都是必跑,並寫明原因)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]

# ★ 順序是承重的(照 tools/governance/consumer_scope.py 的同一條):本樹 insert(0)、fallback append。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ── 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py:tools/**/*.py 的中文字面不准流到輸出口)──
# ★ 順序是承重的(照 consumer_scope.py 的同一條):本樹 insert(0)、fallback append。
_COCKPIT = Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))
_COCKPIT_FALLBACK = Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_COCKPIT_FALLBACK) not in sys.path:
    sys.path.append(str(_COCKPIT_FALLBACK))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)

try:  # pragma: no cover - 缺檔是「量不到」,不是沿用理由
    import shadow_tracer as st
except Exception as _err:  # noqa: BLE001 - 任何 import 失敗都一樣處理:全部必跑
    st = None
    _ST_ERR = "%s: %s" % (type(_err).__name__, _err)
else:
    _ST_ERR = ""

# 鎖檔:只認這些名字。認不得的寫法讓這一層變瞎 —— 那是本檔的下界,寫在 docstring。
LOCKFILE_NAMES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "poetry.lock", "Pipfile.lock", "uv.lock", "requirements.txt",
    "Gemfile.lock", "go.sum", "composer.lock", "Cargo.lock",
)

# inputs 項的形狀(shadow_tracer.inputs_from_events 寫的)。
#   kind:path:suffix → path 是**最後一個冒號之前**的全部(路徑自己可能含冒號)
_PATH_KINDS_WITH_SUFFIX = ("open", "listdir", "handoff", "probe")
#   kind:path        → 整段都是路徑
_PATH_KINDS_BARE = ("missing",)
#   路徑沒有意義的:selfwrite(自己的產物)、handoffdir(治具目錄)、argv(命令列)
_NO_PATH_KINDS = ("selfwrite", "handoffdir", "argv")


class Unmeasurable(RuntimeError):
    """量不到就全部必跑。回一份窄的反查表比回錯誤更貴。"""


def item_path(item):
    """一個 inputs 項 → (kind, 路徑或 None)。看不懂的形狀回 (kind, None),不猜。"""
    kind, sep, rest = str(item).partition(":")
    if not sep:
        return kind, None
    if kind in _PATH_KINDS_WITH_SUFFIX:
        path = rest.rpartition(":")[0]
        return kind, (path or None)
    if kind in _PATH_KINDS_BARE:
        return kind, (rest or None)
    if kind == "i18nkeys":                 # 10-06 晚:i18nkeys:路徑:鍵清單:摘要(翻譯字典以鍵為單位,見 shadow_tracer.i18n_narrow)
        return kind, (rest.partition(":")[0] or None)
    return kind, None


def _input_paths(items):
    """一組 inputs 項 → 它們涉及的 (kind, 路徑) 集合;沒有路徑語意的項不進來。

    ★ 「指紋縮短」要問的是「**哪一個輸入不見了**」,不是「哪一串字不一樣了」。
      沒有路徑的項(argv/selfwrite/handoffdir)由 `input_fp` 那一層比,不在這裡比。
    """
    out = set()
    for it in items or []:
        kind, path = item_path(it)
        if path:
            out.add((kind, path))
    return out


def repo_rel(path):
    """列裡 repo 底下的路徑是 `./x`(shadow_tracer.normalize_path 寫的);其餘(stdlib、/opt、
    <tmpN> 角色 ID)不是本倉的檔,反查表不收 —— 它們對不到 git diff 吐出的路徑。"""
    if not path or not path.startswith("./"):
        return None
    rel = path[2:]
    return rel or None


def lock_fp(repo):
    """本樹鎖檔的指紋。一支都沒有 ⇒ 回固定字串 `none`(空集合只有一個值,所以
    「缺欄」與「當時也是空的」可以判相等)。"""
    root = Path(repo)
    items = []
    for name in LOCKFILE_NAMES:
        for p in sorted(root.rglob(name)):
            if ".git" in p.parts or "node_modules" in p.parts:
                continue
            try:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                h = "unreadable"
            items.append("%s:%s" % (p.relative_to(root).as_posix(), h))
    if not items:
        return "none"
    return hashlib.sha256("\n".join(sorted(items)).encode("utf-8")).hexdigest()


def rows_files(paths, on_default=None):
    """給定的檔/目錄 → 列檔清單,依 mtime 由舊到新。沒給就用 shadow 目錄的預設位置。

    `on_default` 是個 callable,用了預設目錄就叫它一次 —— 預設目錄是**全機共用**的,
    裡面可能躺著別棵樹的列,呼叫端要看得到自己讀的是哪一種。
    """
    out = []
    if not paths:
        if on_default is not None:
            on_default()
        default = (st.shadow_dir_default() if st is not None
                   else Path(os.environ.get("EZ2_VERIFY_SHADOW_DIR")
                             or (Path.home() / ".ez2-aib" / "verify-shadow")))
        paths = [default]
    for p in paths:
        p = Path(p)
        if p.is_dir():
            # `*.roundN.rows.jsonl` 是同一輪的中途快照,`*.rows.jsonl` 是收尾那一份。
            # 兩種都收:中途快照裡也有量到的輸入,而反查表是**越寬越安全**的那一側。
            out.extend(sorted(p.glob("*.rows.jsonl")))
        elif p.is_file():
            out.append(p)
    def _order(q):
        try:
            mt = q.stat().st_mtime
        except OSError:
            mt = 0.0
        # ★ `X.round2.rows.jsonl` 與 `X.rows.jsonl` 是同一輪同一秒寫出來的,
        #   只用 mtime 排,誰排在後面(=誰贏)每次都可能不同,而不同的樣子是綠的。
        #   收尾那一份(沒有 .round)才是那一輪的結論,所以讓它排在中途快照之後。
        return (mt, 0 if ".round" in q.name else 1, q.name)
    return sorted(set(out), key=_order)


class Index(object):
    """每支測試的最近一次列 + 檔→測試的反查表。"""

    def __init__(self):
        self.latest = {}          # test -> row
        self.history = {}         # test -> [row, …](讀入順序,舊到新;多台機器/多棵樹共用池時要看全部,不只最新)
        self.reverse = {}         # repo 相對路徑 -> set(test)
        self.files = []           # 讀過的列檔
        self.bad_lines = 0
        self.degraded = ""        # 非空 = 這份索引量不到,呼叫端一律必跑
        self.repo = None
        self.foreign_rows = 0        # 列自己說它是別棵樹的 ⇒ 丟掉
        self.unknown_origin_rows = 0 # 列沒寫是哪棵樹的(舊格式)⇒ 收下但報出來
        self.rows_default_dir = False   # 沒帶 --rows,讀的是全機共用的預設目錄
        self.repo_id = None           # 本樹所屬 repo 的身分(同一個 git common dir 的工作樹共用)
        self.cross_tree_rows = 0      # 同一個 repo、別的工作樹量到的列(收下)
        self._item_rel = {}       # inputs 項字串 -> (只存一份的字串, kind, repo 相對路徑或 None);值只由字串決定
        self._lock_cache = {}     # repo -> lock_fp(對同一棵樹是同一個值)
        self.voids = []           # 作廢列(第 2d 步)
        self.voided_rows = 0      # 被作廢掉的列數
        self.local_only = False   # 跨主機沿用被停用(第 3 步抽查抓到真錯)
        self.foreign_host_rows = 0

    def lock_for(self, repo):
        """本樹的鎖檔指紋,一棵樹只算一次。

        ★ 沒有這層快取,`decide` 對 700 支測試會把整棵樹走 700 遍(每次約 0.07 秒
          ≈ 49 秒)。而它算出來的是同一個值 —— 慢不會讓結論變錯,但會讓人關掉它。
        """
        if repo not in self._lock_cache:
            self._lock_cache[repo] = lock_fp(repo)
        return self._lock_cache[repo]

    # -- 建表 ------------------------------------------------------------
    def add_row(self, row):
        test = row.get("test")
        if not isinstance(test, str) or not test:
            return
        # ★ 列裡 repo 底下的路徑寫成 `./x`,**而 `./x` 沒有說是哪一棵樹的 x**。
        #   影子目錄預設是全機共用的 ~/.ez2-aib/verify-shadow,裡面可能躺著別棵樹
        #   (治具倉、別的工作樹)的列 ⇒ 反查會給出假命中,而假命中與真命中同形。
        #   兩個來源要兩個名字:
        #     known-foreign  列自己寫了 repo 而且不是這一棵 ⇒ 丟掉(數出來)
        #     unknown-origin 列沒有 repo 欄(舊列)⇒ 收下,但**數出來並報**
        #   收下舊列是刻意的:反查的用途是補靜態挑選的漏,多挑是安全側;
        #   但「可能多挑了幾支、來源不明」必須看得見。
        origin = row.get("repo")
        # ★ 跨開發樹(the maintainer 09-28「以優化跨不同開發樹」):同一個 repo 的不同工作樹路徑不同,只比路徑會把
        #   隔壁工作樹量到的列當成別人的丟掉 ⇒ 跨樹沿用結構上做不到。列若帶 repo_id(git common dir 的指紋,
        #   resume_store.repo_id)而且等於本樹的 ⇒ 同一個 repo,收下(數在 cross_tree_rows);不等 ⇒ 別人的。
        #   沒有 repo_id 的舊列照舊比路徑。收下之後每支仍由 decide 對**本樹**重算輸入指紋,對不上就必跑。
        rid = row.get("repo_id")
        if rid and self.repo_id:
            if str(rid) != str(self.repo_id):
                self.foreign_rows += 1
                return
            if origin and self.repo and str(origin) != str(self.repo):
                self.cross_tree_rows += 1
        elif origin and self.repo and str(origin) != str(self.repo):
            self.foreign_rows += 1
            return
        if not origin:
            self.unknown_origin_rows += 1
        inputs = row.get("inputs")
        if isinstance(inputs, list):
            # 10-05:同一個輸入字串(路徑 + 內容雜湊)在很多列裡重複;只存一份(值不變,只省記憶體:113MB 池 423 → 251MB)
            # 10-08:同一張表(_item_rel)兼做「只存一份」與「路徑只算一次」—— 每項一次字典查找,取代 intern + 路徑解析
            canon = self._item_rel
            out = []
            for x in inputs:
                if isinstance(x, str):
                    got = canon.get(x)
                    if got is None:
                        kind, path = item_path(x)
                        got = canon[x] = (sys.intern(x), kind, repo_rel(path) if path is not None else None)
                    x = got[0]
                out.append(x)
            row["inputs"] = out
        self.latest[test] = row      # 後讀到的覆蓋先讀到的(檔依 mtime 由舊到新)
        self.history.setdefault(test, []).append(row)
        canon = self._item_rel
        for item in row.get("inputs") or []:
            got = canon.get(item) if isinstance(item, str) else None
            if got is not None:
                kind, rel = got[1], got[2]
            else:
                kind, path = item_path(item)
                if path is None:
                    continue
                rel = repo_rel(path)
            if rel is None:
                continue
            # ★ `missing:` 與 `probe:…:missing` 也要進表:測試探測過一個**不存在**的
            #   路徑,而這次改動把它**建出來**,那支測試的結果就會變。
            #   只收「讀過的檔」會漏掉這一整類,而漏掉的樣子是綠的。
            self.reverse.setdefault(rel, set()).add(test)

    def tests_reading(self, changed):
        """{改動檔: [讀過它的測試…]};只回有命中的檔。"""
        hits = {}
        for c in changed:
            got = self.reverse.get(c)
            if got:
                hits[c] = sorted(got)
        return hits

    # -- 判沿用 ----------------------------------------------------------
    # ---- 10-02 雲端 claudebot(docs/PLAN-shadow-cross-device-1002.md 第 2 步):多台機器/多棵樹共用一個池 ----
    # 以前只看「最新一列」,誰最後寫誰贏:
    #   (1) 別台(環境不同)剛寫的列蓋掉本機鑰匙對得上的舊列 ⇒ 本機沿用不到(安全,但白跑);
    #   (2) 兩台環境對齊、鑰匙相同時,快機的綠蓋掉慢機同鑰匙的紅 ⇒ 慢機上的紅被藏起來(時間敏感的測試就是這形)。
    # 現在:最新一列照舊先判(理由字串不變);最新一列量得到、只是指紋對不上時,往回找鑰匙對得上的舊列(最多
    # FALLBACK_KEYS 把不同的鑰匙)。不論沿用哪一列,只要池裡**同一把鑰匙**出現過紅或逾時 ⇒ 不穩定,永不沿用。
    FALLBACK_KEYS = 8
    # 最新一列因這些理由不能用時,才准往回找;量不到、外部、缺欄、改到自己/讀過的檔 ⇒ 不往回找(照舊必跑)
    #   ★ 最新一列是紅或逾時 ⇒ 不往回找(保守:最近有人看到紅,就重跑;即使那一列的鑰匙和現在不同)。
    FALLBACK_REASONS = ("fingerprint-shrink", "input-fp-changed", "env-fp-changed", "key-mismatch",
                        "lock-fp-changed")

    def decide(self, repo, test, changed=None, lock_now=None, environ=None):
        """回 (verdict, reason, detail)。verdict ∈ {"reuse", "must-run"}。沿用時 detail = 被沿用那一列的鑰匙前 12 碼
        (row_for(test, detail) 取回那一列;它不一定是最新一列)。"""
        if self.degraded:
            return "must-run", "index-degraded", self.degraded
        rows = self.history.get(test) or ([self.latest[test]] if test in self.latest else [])
        if not rows:
            return "must-run", "no-previous", ""
        cache = {}
        first = self._decide_row(repo, test, rows[-1], changed, lock_now, environ, cache)
        if first[0] == "reuse":
            return self._stable(test, rows[-1], first)
        if first[1] not in self.FALLBACK_REASONS:
            return first
        if rows[-1].get("measured") is not True or rows[-1].get("external") or not rows[-1].get("key"):
            return first
        # 10-03 審查第 1 條:隔離壞行留下的必跑標記(墓碑)只在它是「最新一列」時才擋得住;往回找會越過它。
        #   ⇒ 這支測試有墓碑就不往回找;往回找時碰到任何不能用的列(沒鑰匙、量不到、外部、紅、逾時)就停,不越過。
        if any(_is_tombstone(r) for r in rows):
            return first
        seen = {rows[-1].get("key")}
        for prev in reversed(rows[:-1]):
            k = prev.get("key")
            if not _walkable(prev):
                break
            if k in seen:
                continue
            seen.add(k)
            if len(seen) > self.FALLBACK_KEYS + 1:
                break
            got = self._decide_row(repo, test, prev, changed, lock_now, environ, cache)
            if got[0] == "reuse":
                return self._stable(test, prev, got)
        return first

    ENV_CHANGE_SHOW = 5

    def _env_change(self, cache, repo, prev_fp, environ):
        """「環境變了」是哪幾項(10-06):上一列那個指紋存過逐項原料 ⇒ 跟現在逐項比,回項目名;沒存過 ⇒ ""(照舊只說變了)。
        同一次判斷裡同一個舊指紋只比一次。只回名字,不回值。"""
        key = ("env_change", prev_fp)
        if key not in cache:
            names = []
            try:
                import env_parts_store as eps
                old = eps.load_env_parts(prev_fp)
                if old is not None:
                    if "env_parts_now" not in cache:
                        env = st.child_env(Path(repo), Path("/tmp/.ez2-verify-reuse-probe"), environ)
                        cache["env_parts_now"] = st.env_parts(env, repo=repo)
                    names = eps.env_change_names(old, cache["env_parts_now"])
            except Exception:  # noqa: BLE001 - 說不出是哪項就照舊只說變了
                names = []
            more = len(names) - self.ENV_CHANGE_SHOW
            cache[key] = "、".join(names[:self.ENV_CHANGE_SHOW]) + (" …+%d" % more if more > 0 else "")
        return cache[key]

    def explain(self, repo, test, environ=None, limit=20):
        """aib test --why(10-04):decide 的結論 + 卡在哪個輸入。只讀,不跑、不寫池。
        回 {"verdict","why","detail","time","changed":[...],"unmeasured":[...],"more":n}。
        ★ 結論一律照 decide(同一套判準),這裡只補「是哪一項」—— 不准自己另判一次沿用。"""
        verdict, why, detail = self.decide(repo, test, changed=[], environ=environ)
        out = {"verdict": verdict, "why": why, "detail": detail, "time": None,
               "changed": [], "unmeasured": [], "more": 0}
        rows = self.history.get(test) or ([self.latest[test]] if test in self.latest else [])
        if not rows:
            return out
        last = rows[-1]          # decide 不沿用時回的就是最新一列的判斷
        out["time"] = last.get("time")
        found = []
        if why == "unmeasured":
            found = [str(w) for w in (last.get("why") or [])]
            key = "unmeasured"
        elif why in ("input-fp-changed", "fingerprint-shrink") and st is not None:
            key = "changed"
            try:
                items_now, _fp = st.recompute_input_fp(repo, last.get("inputs") or [])
            except Exception as err:  # noqa: BLE001 - 說不出是哪項就照實說,不猜
                found = ["recompute-failed: %s: %s" % (type(err).__name__, err)]
            else:
                now, was = set(map(str, items_now)), [str(i) for i in (last.get("inputs") or [])]
                for it in was:
                    if it in now:
                        continue
                    kind, path = item_path(it)
                    found.append("%s:%s" % (kind, path) if path else (it if len(it) <= 160 else it[:157] + "..."))
        else:
            return out
        out[key] = found[:limit]
        out["more"] = max(0, len(found) - limit)
        return out

    def _stable(self, test, prev, got):
        """同一把鑰匙在池裡出現過紅/逾時/缺結果 ⇒ 不穩定(不管哪台、哪一輪),永不沿用。"""
        k = prev.get("key")
        for other in self.history.get(test) or ():
            if other is prev or other.get("key") != k:
                continue
            rc = other.get("rc")
            if isinstance(rc, bool) or not isinstance(rc, int) or rc != 0 or other.get("timed_out"):
                return "must-run", "unstable-same-key", "%s rc=%r" % ((k or "")[:12], rc)
        return got

    def row_for(self, test, key_prefix=""):
        """decide 回 reuse 時被沿用的那一列(以 detail 的鑰匙前綴找,新的優先);找不到回最新一列。"""
        if key_prefix:
            for r in reversed(self.history.get(test) or ()):
                if str(r.get("key") or "").startswith(key_prefix):
                    return r
        return self.latest.get(test)

    def _decide_row(self, repo, test, prev, changed=None, lock_now=None, environ=None, cache=None):
        """回 (verdict, reason, detail)。verdict ∈ {"reuse", "must-run"}。

        ★ 判準順序就是 docstring 那張表;**任何一步算不出來都倒向必跑**。
        ★ environ:測試**實際會拿到**的環境(執行器洗過的乾淨環境)。沒給 ⇒ 用本行程自己的環境(舊行為)。
          09-29 a seat:快驗執行器在乾淨環境跑測試,卻用自己的環境算「現在的環境指紋」⇒ 兩邊對不上,
          或更糟:剛好對上的是錯的那一份。判沿用的環境必須就是跑測試的那一份(docs/DESIGN-shadow-resume-complete-0929.md 零節 11)。
        """
        changed = set(changed or ())
        cache = {} if cache is None else cache
        if prev.get("measured") is not True:
            return "must-run", "unmeasured", ";".join(
                str(w) for w in (prev.get("why") or [])[:3])
        if prev.get("external"):
            return "must-run", "external", ""
        rc = prev.get("rc")
        # ★ `isinstance(True, int)` 在 Python 是真的,所以 bool 要單獨擋掉
        #   (第 96 項量尺同一條:判準用 is,不用 ==)。
        if isinstance(rc, bool) or not isinstance(rc, int):
            return "must-run", "previous-rc-absent", repr(rc)
        if rc != 0:
            return "must-run", "previous-red", "rc=%d" % rc
        if prev.get("timed_out"):
            return "must-run", "previous-timeout", ""
        if test in changed:
            return "must-run", "test-modified", test
        touched_inputs = sorted(
            c for c in changed if test in self.reverse.get(c, ()))
        if touched_inputs:
            return "must-run", "input-changed", "、".join(touched_inputs[:3])
        # 10-04:有效期限(放在「這次改到它/它讀的檔」之後:那兩個理由對人更有用)(見檔頭「跟日期有關的結果」)。沒有 UTC 時間的舊列 ⇒ 不知道多舊 ⇒ 必跑一次,跑完就補齊。
        born = row_time_utc(prev)
        now_utc = self._now()
        if born is None:
            return "must-run", "row-age-unknown", ""
        age_h = (now_utc - born).total_seconds() / 3600.0
        if age_h < -0.1:
            return "must-run", "row-from-future", prev.get("time_utc", "")
        ttl = min(self._ttl_h(test, prev), max_row_age_h(environ))
        if age_h > ttl:
            return "must-run", "row-expired", "%dh/%dh" % (int(age_h), int(ttl))
        if st is None:
            return "must-run", "tracer-unavailable", _ST_ERR
        if not prev.get("key") or "input_fp" not in prev:
            return "must-run", "previous-key-absent", ""
        try:
            items_now, input_fp_now = st.recompute_input_fp(
                repo, prev.get("inputs") or [])
        except Exception as err:  # noqa: BLE001 - 重算炸掉 = 量不到 = 必跑
            return "must-run", "recompute-failed", "%s: %s" % (
                type(err).__name__, err)
        # ★ 指紋縮短單獨報一個理由:`input-fp-changed` 只說「變了」,而「少讀了東西」
        #   與「檔內容改了」要人做的事不同(前者多半是量測掉了一段)。
        # ★★ 比的是**路徑**不是整串。整串比的話,`open:X:舊sha` 與 `open:X:新sha`
        #   對不上 ⇒ 每一次內容改動都會被貼上「縮短」的標籤,而縮短要人去查的是
        #   「誰把量測弄不見了」—— 貼錯標籤比沒有標籤更貴。
        gone = _input_paths(prev.get("inputs") or []) - _input_paths(items_now)
        if gone:
            return "must-run", "fingerprint-shrink", "、".join(sorted(
                "%s:%s" % (k, v) for k, v in gone)[:3])
        if input_fp_now != (prev.get("input_fp") or ""):
            return "must-run", "input-fp-changed", ""
        if "env_fp" not in cache:      # 同一次判斷裡環境只算一次(它要跑 node/tmux --version)
            try:
                env = st.child_env(Path(repo), Path("/tmp/.ez2-verify-reuse-probe"), environ)
                cache["env_fp"] = (st.env_fingerprint(env, repo=repo)[0], None)
            except Exception as err:  # noqa: BLE001
                cache["env_fp"] = (None, "%s: %s" % (type(err).__name__, err))
        env_fp_now, env_err = cache["env_fp"]
        if env_err is not None:
            return "must-run", "env-fp-failed", env_err
        if env_fp_now != (prev.get("env_fp") or ""):
            return "must-run", "env-fp-changed", self._env_change(cache, repo, prev.get("env_fp") or "", environ)
        if st.make_key(test, input_fp_now, env_fp_now) != prev.get("key"):
            return "must-run", "key-mismatch", ""
        now = lock_now if lock_now is not None else self.lock_for(repo)
        was = prev.get("lock_fp")
        if was is None:
            # 舊列沒有這一欄 ⇒ 當成 `none`(空集合)。本樹也沒有鎖檔的話兩邊相等、
            # 准沿用;本樹**有**鎖檔的話 none != 現在的值 ⇒ 必跑一次,跑完的新列
            # 帶著 lock_fp,下一輪就對得上。自己會好,不必人手清池。
            was = "none"
        if was != now:
            return "must-run", "lock-fp-changed", "%s→%s" % (was[:8], now[:8])
        crossed = self._date_crossed(repo, prev, born, now_utc)
        if crossed:
            return "must-run", "date-crossed", crossed
        return "reuse", "fingerprint-match", prev.get("key", "")[:12]

    def remaining_h(self, repo, test, environ=None):
        """現在判得到沿用的話,還剩幾小時過期;判不到沿用 ⇒ None。給閒置預跑挑「快過期的」用。"""
        verdict, _why, detail = self.decide(repo, test, changed=[], environ=environ)
        if verdict != "reuse":
            return None
        prev = self.row_for(test, detail) or {}
        born = row_time_utc(prev)
        if born is None:
            return None
        ttl = min(self._ttl_h(test, prev), max_row_age_h(environ))
        return ttl - (self._now() - born).total_seconds() / 3600.0

    def _ttl_h(self, test, prev):
        """這一列的期限(小時)。見檔頭「72 小時都沒有改動就延長一倍」。"""
        key = prev.get("key")
        born = row_time_utc(prev)
        chain = sorted(((row_time_utc(r), r) for r in (self.history.get(test) or ())
                        if r.get("key") == key and _walkable(r) and row_time_utc(r) is not None
                        and row_time_utc(r) <= born), key=lambda x: x[0])
        ttl = float(MAX_ROW_AGE_H)
        last = None
        for t, r in chain:
            if last is not None:
                if _row_sig(r) != _row_sig(last[1]):
                    ttl = float(MAX_ROW_AGE_H)
                elif (t - last[0]).total_seconds() / 3600.0 >= ttl * 0.9:
                    ttl = min(ttl * 2, MAX_TTL_H)
            last = (t, r)
        return ttl * _jitter(test, key)

    def _now(self):
        """現在時刻(UTC)。測試可以設 self.now_utc 固定它;不從環境變數讀(撥回過去 = 讓過期列又能沿用)。"""
        import datetime as _dt
        return getattr(self, "now_utc", None) or _dt.datetime.now(_dt.timezone.utc)

    def _date_crossed(self, repo, prev, born, now_utc):
        """回 "" 或「哪個檔、哪一天」。只看倉內 open: 項(那些是本樹的檔;倉外的 stdlib 等不會寫業務日期)。"""
        for it in prev.get("inputs") or ():
            kind, path = item_path(it)
            rel = repo_rel(path) if kind in ("open", "i18nkeys") else None
            if not rel:
                continue
            if _THIRD_PARTY_DIRS & set(rel.replace("\\", "/").split("/")):
                # 10-07(外部倉 requests 實踩):虛擬環境放在專案資料夾裡 ⇒ 第三方套件檔也算「倉內」,剛編譯的 .so 寫著
                #   今天的日期 ⇒ 每支每次都判跨日重跑。第三方套件跟放在倉外時一樣不掃(倉外的本來就不看,見上)。
                continue
            sha = str(it).rpartition(":")[2] if kind == "open" else ""   # 以鍵為單位的字典:照整檔掃日期(保守,與縮小前相同)
            dates = file_dates(os.path.join(str(repo), rel), sha)
            if dates is None:
                return "%s(讀不到,當作跨過)" % rel
            hit = crossed_dates(dates, born, now_utc)
            if hit:
                return "%s %s" % (rel, hit[0].isoformat())
        return ""


# ---- 10-04:跟日期有關的結果(E0.2 活例:9/30 前綠、10/1 起紅,中間沒有任何檔案改過)--------------------------
# 沿用只看「讀了什麼、環境是什麼」,不看今天幾號 ⇒ 一列 9/29 的綠可以在 10/4 被沿用 = 紅藏成綠。
# 兩層,都放在核心(不靠哪一種語言的接頭):
#   1. 有效期限:列超過 MAX_ROW_AGE_H 小時就不沿用(兜底:看不出來的時間相依,例如「建立超過 7 天」)。
#      環境變數 AIB_REUSE_MAX_AGE_H 只能**縮短**,不能放寬。
#   2. 跨日:測試讀過的倉內檔裡寫著日期(YYYY-MM-DD、YYYY/MM/DD,或 *_at/epoch/expire 欄位的 10/13 位秒數),
#      而從那一列到現在之間,這台的本地時區或 UTC 跨過了那一天的開始或隔天的開始 ⇒ 必跑。
#      兩種邊界都算(「到期日當天還有效」與「當天起失效」寫法都有),寬的只是多跑。
_THIRD_PARTY_DIRS = frozenset(("site-packages", "dist-packages", "node_modules"))
MAX_ROW_AGE_H = 72
_DATE_RE = re.compile(rb"(?<![0-9])(20[0-9]{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12][0-9]|3[01])(?![0-9])")
_EPOCH_RE = re.compile(rb"(?i)(?:epoch|_at|_ts|expir[a-z_]*|deadline|until)[\"']?\s*[:=]\s*[\"']?"
                       rb"(1[6-9][0-9]{8}|2[01][0-9]{8})([0-9]{3})?(?![0-9])")
_DATE_SCAN_MAX = 4 * 1024 * 1024
_DATE_CACHE = {}


def max_row_age_h(environ=None):
    """環境變數 AIB_REUSE_MAX_AGE_H 給的上限(只能縮短);沒設 ⇒ 無限(由 ttl_h 決定)。"""
    environ = os.environ if environ is None else environ
    raw = environ.get("AIB_REUSE_MAX_AGE_H")
    if not raw:
        return float("inf")
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return float(MAX_ROW_AGE_H)


# ---- 10-04(the maintainer:「72 小時都沒有改動,就自動延長一倍」,同意加上限與確認)-----------------------------------
# 期限不存在列裡,每次從池的歷史算(別台的列不能自稱期限很長;壓縮掉舊列 ⇒ 退回 3 天 = 錯在保守那邊):
#   同一把鑰匙的綠列依時間排;相鄰兩列「隔了至少上一段期限的九成」= 到期後重跑又綠 = 確認一次 ⇒ 期限加倍,
#   最多 MAX_TTL_H(14 天)。相鄰兩列跑的格數或跳過格數不同 ⇒ 行為變了 ⇒ 打回 3 天。期限內的提早重跑不加倍也不打回。
#   最後乘上「每支測試固定的 ±10%」錯開到期日,免得某一天大家一起過期。
MAX_TTL_H = 14 * 24.0
_SKIPPED_RE = re.compile(r"skipped=(\d+)")


def _row_sig(row):
    m = _SKIPPED_RE.search(row.get("tail") or "")
    return (row.get("ran"), int(m.group(1)) if m else 0)


def _jitter(test, key):
    h = int(hashlib.sha256(("%s|%s" % (test, key)).encode("utf-8")).hexdigest()[:8], 16)
    return 0.9 + 0.2 * (h / 0xFFFFFFFF)


def row_time_utc(row):
    """列的 UTC 時間(datetime,帶時區);沒有或看不懂 ⇒ None。"""
    import datetime as _dt
    v = row.get("time_utc")
    if not isinstance(v, str):
        return None
    try:
        return _dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def file_dates(path, sha=""):
    """一個檔裡寫到的日期(date 集合)。讀不到/太大 ⇒ None(呼叫端當「量不到」)。以內容 sha 快取。"""
    import datetime as _dt
    key = (str(path), sha)
    if sha and key in _DATE_CACHE:
        return _DATE_CACHE[key]
    try:
        if os.path.getsize(path) > _DATE_SCAN_MAX:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    out = set()
    for y, m, d in _DATE_RE.findall(data):
        try:
            out.add(_dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for sec, ms in _EPOCH_RE.findall(data):
        try:
            out.add(_dt.datetime.fromtimestamp(int(sec), _dt.timezone.utc).date())
        except (ValueError, OverflowError, OSError):
            pass
    if sha:
        _DATE_CACHE[key] = out
    return out


def crossed_dates(dates, since, now):
    """since/now:帶時區的 datetime。回「從 since 到 now 之間跨過了它(或它隔天)開始」的那些日期。
    時區:UTC 加上這台在兩個時刻的本地偏移(夏令時間前後可能不同)。"""
    import datetime as _dt
    offsets = {_dt.timedelta(0)}
    for t in (since, now):
        off = t.astimezone().utcoffset()
        if off is not None:
            offsets.add(off)
    hit = []
    for d in sorted(dates):
        for day in (d, d + _dt.timedelta(days=1)):
            start = _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc)
            if any(since < start - off <= now for off in offsets):
                hit.append(d)
                break
    return hit


def _parse_row(line):
    """One rows line ⇒ the row dict, or None when it is damaged (bad JSON or bad shape)."""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return _row_shape(row)


def _row_shape(row):
    """Already-parsed line ⇒ the row dict, or None when its shape is bad (None in ⇒ None out)."""
    if (not isinstance(row, dict) or not isinstance(row.get("test"), str) or not row.get("test")
            or not isinstance(row.get("inputs"), list)):
        return None
    return row


_TEST_IN_RAW = re.compile(r'"test"\s*:\s*"([^"\\]+)"')


def quarantine(path, apply=False, now=None):
    """Move damaged lines of one rows file to `<file>.bad` and append a must-run tombstone per affected test.

    Why tombstones and not just dropping the bad line: the bad line may have been the newest (red) result
    of a test; dropping it would let an older green row be reused. A tombstone (`measured: "unmeasured"`)
    is the newest row for that test, so `decide` says must-run once, then the next real run replaces it.
    A bad line whose test name cannot be read ⇒ every test in the file gets a tombstone (one full rerun).
    Refuses (changed=True) if the file grew while we worked: an appender was writing; run again later.
    """
    import time as _time
    p = Path(path)
    before = p.stat()
    raw = jsonl_safe.split_lines(p.read_text(encoding="utf-8", errors="replace"))
    good, bad, names, unknown = [], [], set(), False
    for n, line in enumerate(raw, start=1):
        if not line.strip():
            continue
        row = _parse_row(line.strip())
        if row is None:
            bad.append((n, line))
            m = _TEST_IN_RAW.search(line)
            if m:
                names.add(m.group(1))
            else:
                unknown = True
        else:
            good.append((line, row))
    if unknown:
        names |= {row["test"] for _, row in good}
    template = next((row for _, row in reversed(good)), {})
    stamp = now or _time.strftime("%Y-%m-%d %H:%M")
    tombs = [{"test": t, "rc": -1, "inputs": [], "measured": "unmeasured", "external": False,
              "why": ["quarantined-bad-row"], "time": stamp,
              **{k: template[k] for k in ("repo", "repo_id") if k in template}} for t in sorted(names)]
    report = {"file": str(p), "bad_lines": [n for n, _ in bad], "tombstones": sorted(names),
              "unknown_test_in_bad_line": unknown, "applied": False, "changed": False}
    if not bad or not apply:
        return report
    after = p.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        report["changed"] = True
        return report
    with open(str(p) + ".bad", "a", encoding="utf-8") as fh:
        for n, line in bad:
            fh.write(jsonl_safe.dumps({"quarantined_at": stamp, "line": n, "raw": line}) + "\n")
    tmp = p.with_name(p.name + ".quarantine-tmp")
    tmp.write_text("".join(l + "\n" for l, _ in good)
                   + "".join(jsonl_safe.dumps(t) + "\n" for t in tombs), encoding="utf-8")
    os.replace(str(tmp), str(p))
    report["applied"] = True
    return report


# ---- 10-02 第 2d 步:作廢(void)與壓縮 ---------------------------------------------------------------
# 作廢列:{"void": {條件…}, "before": "YYYY-MM-DD HH:MM:SS", "reason": "…"}。條件可用 test / key_prefix / host_id / source,
# 全部要成立才算命中;要全作廢得明寫 {"all": true}。命中且「列的 time 早於 before(或沒有 time)」的列,當作沒存在過。
# 用時間不用檔案順序:作廢列常寫在另一個檔,而檔案依修改時間排序,池檔一被追加就排到後面 —— 用順序會漏作廢。
# 時間以寫列那台機器的時鐘為準;跨機器作廢請指名 host_id。沒有任何條件的作廢列 = 壞列(整份索引降級,一律必跑)。
VOID_KEYS = ("test", "key_prefix", "host_id", "source", "all", "green_only")
CROSS_HOST_FLAG = "cross-host-disabled.json"   # 放在列檔所在的目錄;只有人能移除(false_reuse_audit.py enable-cross-host)


def _parse_void(obj):
    """一行若是作廢列,回正規化後的 dict;不是作廢列回 None;是作廢列但壞了回 False。"""
    if not isinstance(obj, dict) or "void" not in obj:
        return None
    cond = obj.get("void")
    before = obj.get("before")
    if not isinstance(cond, dict) or not isinstance(before, str) or not before:
        return False
    cond = {k: v for k, v in cond.items() if k in VOID_KEYS}
    if not cond or (cond.get("all") is not True and not any(isinstance(cond.get(k), str) and cond.get(k)
                                                              for k in VOID_KEYS if k not in ("all", "green_only"))):
        return False
    bu = obj.get("before_utc")
    return {"cond": cond, "before": before, "before_utc": bu if isinstance(bu, str) and bu else None,
            "reason": obj.get("reason") or ""}


def _void_hits(v, row):
    c = v["cond"]
    if c.get("all") is not True:
        for k in ("test", "host_id", "source"):
            if c.get(k) and row.get(k) != c[k]:
                return False
        if c.get("key_prefix") and not str(row.get("key") or "").startswith(c["key_prefix"]):
            return False
    # 10-03 審查第 3 條:抽查作廢「錯的綠」時,不能連剛跑出來的紅證據一起作廢 ⇒ green_only 只打綠列
    if c.get("green_only") is True and not _walkable(row):
        return False
    # 10-03 審查第 4 條:兩台機器的本地時間不能拿來比。兩邊都有 UTC ⇒ 比 UTC;任一邊沒有 ⇒ 一律算在作廢之前(保守:多重跑)
    if v.get("before_utc"):
        tu = row.get("time_utc")
        return not isinstance(tu, str) or not tu or tu < v["before_utc"]
    t = row.get("time")
    return not isinstance(t, str) or not t or t < v["before"]


def _is_tombstone(row):
    return "quarantined-bad-row" in (row.get("why") or []) or (row.get("rc") == -1 and row.get("measured") != True)


def _walkable(row):
    rc = row.get("rc")
    return (bool(row.get("key")) and row.get("measured") is True and not row.get("external")
            and isinstance(rc, int) and not isinstance(rc, bool) and rc == 0 and not row.get("timed_out"))


def _utc_now(slack_s=0):
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() + slack_s))


def write_void(path, cond, reason, now=None, slack_s=0):
    """在列檔尾端追加一筆作廢列(與執行器同一把檔鎖)。條件不合法 ⇒ ValueError,什麼都不寫。"""
    import fcntl
    import time as _time
    obj = {"void": dict(cond), "before": now or _time.strftime("%Y-%m-%d %H:%M:%S"),
           "before_utc": _utc_now(slack_s) if now is None else None, "reason": reason or ""}
    if obj["before_utc"] is None:
        del obj["before_utc"]
    if not _parse_void(obj):
        raise ValueError("void needs at least one of test/key_prefix/host_id/source, or all=true")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (jsonl_safe.dumps(obj) + "\n").encode("utf-8")
    with open(p, "ab") as f:   # 放鎖前整列寫完(見 runner._append_row 10-04 註)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(data)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return obj


def _row_class(row):
    """壓縮分組用:綠(rc 0、沒逾時)與非綠分開留 —— 同鑰匙一紅一綠是「不穩定」的證據,壓縮不准把它壓掉。"""
    rc = row.get("rc")
    green = isinstance(rc, int) and not isinstance(rc, bool) and rc == 0 and not row.get("timed_out")
    return "green" if green else "not-green"


def compact(path, apply=False, keep_per_test=None):
    """同一支測試、同一把鑰匙、同一類結果(綠/非綠)只留最新一列;其餘搬到 `<檔>.archive`(只滾不刪)。
    keep_per_test=N(10-03):每支測試再只留最新 N 列(池越長越大:測試改一次就換一把鑰匙,舊鑰匙幾乎不會再對上)。
      ★ 不把紅藏成綠的兩條:(1) 留下的列用到的鑰匙,那把鑰匙更舊的列一起留(「同鑰匙紅過 ⇒ 不穩定」的證據不丟);
      (2) 剪掉的位置原地插一列封存標記(rc -1、unmeasured,與隔離墓碑同形)⇒ 這支測試從此不往回找
      —— 多檔合併時剪掉的舊列可能正是讓往回找停下的那一列,有標記就只會更保守(必跑),不會多沿用。
    ★ 改寫後把檔案修改時間還原(10-03):多檔合併依檔案 mtime 排先後,改寫會讓整檔變「最新」,
      例如別台收進來的 shared 檔一壓縮,裡面的舊列就排到本機新列之後。
    作廢列一律保留。有壞行 ⇒ 拒絕(先 quarantine)。就地改寫,持有與執行器追加時同一把檔鎖:
    執行器用 O_APPEND 開檔、寫前拿鎖 ⇒ 我們改寫完放鎖後,它的寫入接在新檔尾,不會掉列。"""
    import fcntl
    p = Path(path)
    report = {"file": str(p), "rows": 0, "kept": 0, "archived": 0, "voids": 0, "slimmed": 0, "applied": False, "refused": ""}
    report["trimmed"] = 0
    with open(p, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            st0 = os.fstat(f.fileno())
            # 10-05:兩趟逐行讀。以前整檔讀進來、每列都留著(行字串 + 解析後的列),自動壓縮正好在池子大時才跑,
            #   那一下的記憶體是檔案大小的好幾倍。第一趟每列只留判斷要用的幾個小欄位,第二趟逐行寫出。
            meta = []          # 每個非空行:None = 作廢列;否則 (test, key, class, time, time_utc)
            n = 0
            for raw in jsonl_safe.iter_lines(p):
                line = raw
                if not line.strip():
                    continue
                n += 1
                try:
                    obj = json.loads(line)     # 10-08:每行只解析一次(以前作廢列判斷、_parse_row 各解析一次)
                except ValueError:
                    obj = None
                v = _parse_void(obj)
                if v is False:
                    report["refused"] = "bad-void-line:%d" % n
                    return report
                if v is not None:
                    meta.append(None)
                    report["voids"] += 1
                    continue
                row = _row_shape(obj)
                if row is None:
                    report["refused"] = "bad-line:%d" % n
                    return report
                meta.append((row.get("test"), row.get("key") or "", _row_class(row), row.get("time"), row.get("time_utc"),
                             row.get("key")))
            report["rows"] = sum(1 for m in meta if m is not None)
            newest = {}
            for i, m in enumerate(meta):
                if m is not None:
                    newest[(m[0], m[1], m[2])] = i
            keep_idx = set(newest.values())
            marker_at = {}
            if keep_per_test:
                by_test = {}
                for i in sorted(keep_idx):
                    by_test.setdefault(meta[i][0], []).append(i)
                for t, idxs in by_test.items():
                    if len(idxs) <= keep_per_test:
                        continue
                    retained = idxs[-keep_per_test:]
                    rkeys = {meta[i][5] for i in retained if meta[i][5]}
                    cut = [i for i in idxs[:-keep_per_test] if meta[i][5] not in rkeys]
                    if not cut:
                        continue
                    keep_idx -= set(cut)
                    report["trimmed"] += len(cut)
                    marker_at[max(cut)] = t
            report["kept"] = sum(1 for i, m in enumerate(meta) if m is not None and i in keep_idx)
            tmp = Path(str(p) + ".compact-%d" % os.getpid())
            archived = 0
            arc = None      # 有東西要搬才開(沒搬就不留下空的 .archive;乾跑不寫)
            try:
                with open(tmp, "w", encoding="utf-8") as out:
                    i = -1
                    for raw in jsonl_safe.iter_lines(p):
                        line = raw
                        if not line.strip():
                            continue
                        i += 1
                        if i >= len(meta):
                            break       # 第一趟之後追加的列:有檔鎖,不會有;防萬一不碰
                        m = meta[i]
                        if i in marker_at:
                            out.write(json.dumps({"test": marker_at[i], "rc": -1, "measured": "unmeasured", "external": False,
                                                  "why": ["archived-history"], "inputs": [], "key": "",
                                                  "time": m[3], "time_utc": m[4]}, ensure_ascii=False) + "\n")
                        if m is not None and i in keep_idx:
                            # 10-03:舊追蹤器把同一個原因重複寫進 why(雲端一列 403 萬筆、88MB,池被撐到 965MB)。
                            #   只去重、不截斷:判斷用到的標記(quarantined-bad-row)一定還在,鑰匙與其他欄不動
                            row = json.loads(line)
                            why = row.get("why")
                            if isinstance(why, list) and len(why) > len(set(map(str, why))):
                                seen = set()
                                row = dict(row, why=[w for w in why if not (str(w) in seen or seen.add(str(w)))])
                                line = jsonl_safe.dumps(row)
                                report["slimmed"] += 1
                        if m is None or i in keep_idx:
                            out.write(line + "\n")
                        else:
                            if apply and arc is None:
                                arc = open(str(p) + ".archive", "a", encoding="utf-8")
                            if arc is not None:
                                arc.write(line + "\n")
                            archived += 1
                if arc is not None:
                    arc.flush()
                    os.fsync(arc.fileno())
                    arc.close()
                report["archived"] = archived
                if not apply or not (archived or report["slimmed"]):
                    return report
                f.seek(0)
                f.truncate()
                with open(tmp, "r", encoding="utf-8", newline="\n") as src:
                    shutil.copyfileobj(src, f, 1 << 20)
                f.flush()
                os.fsync(f.fileno())
                os.utime(f.fileno(), ns=(st0.st_atime_ns, st0.st_mtime_ns))
            finally:
                if arc is not None and not arc.closed:
                    arc.close()
                try:
                    tmp.unlink()
                except OSError:
                    pass
            report["applied"] = True
            return report
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


AUTO_COMPACT_MB = 200.0
AUTO_COMPACT_KEEP = 3


def auto_compact(paths=None, environ=None):
    """紀錄池檔超過上限就自動壓縮(10-05)。回每個真的壓了的檔的報告。
    為什麼:壓縮原本只有手動,沒人跑 ⇒ 池長到 544MB,讀池時整檔載入,推前檢查三份同時跑,其中一份被記憶體上限殺掉。
    用的是 compact(keep_per_test=3) 同一條路:剪掉的位置留封存標記 ⇒ 只會更保守(必跑),不會多沿用;剪掉的列搬到 .archive 不刪。
    上限:AIB_POOL_COMPACT_MB(預設 200;<= 0 關掉)。有壞行的檔 compact 自己會拒絕,這裡照樣回報、不硬壓。"""
    environ = os.environ if environ is None else environ
    try:
        limit = float(environ.get("AIB_POOL_COMPACT_MB", AUTO_COMPACT_MB))
    except ValueError:
        limit = AUTO_COMPACT_MB
    if limit <= 0:
        return []
    out = []
    for f in rows_files(paths or []):
        try:
            big = f.stat().st_size > limit * 1024 * 1024
        except OSError:
            continue
        if big:
            out.append(compact(f, apply=True, keep_per_test=AUTO_COMPACT_KEEP))
    return out


def build_index(paths, repo=None):
    idx = Index()
    idx.repo = str(Path(repo).resolve()) if repo else None
    idx.repo_id = repo_identity(repo) if repo else None

    def _mark_default():
        idx.rows_default_dir = True
    files = rows_files(paths, on_default=_mark_default)
    idx.files = [str(f) for f in files]
    if not files:
        idx.degraded = "no-rows-files"
        return idx
    for f in files:
        try:
            for line_number, line in enumerate(jsonl_safe.iter_lines(f), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)     # 10-08:每行只解析一次(以前作廢列判斷、_parse_row 各解析一次)
                except ValueError:
                    obj = None
                v = _parse_void(obj)
                if v is False:
                    idx.bad_lines += 1
                    if not idx.degraded:
                        idx.degraded = "bad-void:%s:%d" % (f, line_number)
                    continue
                if v is not None:
                    idx.voids.append(v)
                    continue
                row = _row_shape(obj)
                # A partial or malformed index can omit the newest evidence for a test
                # while leaving an older successful row eligible for reuse. Treat both
                # invalid JSON and invalid row shapes as index damage; never silently
                # skip a row that may have superseded a reusable result.
                # (Repair: `reuse_index quarantine --rows F --apply` moves bad lines aside and leaves a
                #  must-run tombstone for every test they could have been about.)
                if row is None:
                    idx.bad_lines += 1
                    if not idx.degraded:
                        idx.degraded = "bad-jsonl:%s:%d" % (f, line_number)
                    continue
                idx.add_row(row)
        except OSError as err:
            idx.degraded = "unreadable:%s:%s" % (f, err)
            return idx
    # 第 3 步:抽查抓到「真的沿用錯了」⇒ 寫停用旗標;旗標在 ⇒ 別台機器的列一律不用(沒有 host_id 的舊列視為本機)
    flag_dirs = [Path(p) for p in (paths or [])] or [Path(st.shadow_dir_default()) if st is not None else None]
    for d in flag_dirs:
        if d is not None and (d if d.is_dir() else d.parent).joinpath(CROSS_HOST_FLAG).is_file():
            idx.local_only = True
    if idx.local_only:
        me = host_id()
        for test in list(idx.history):
            # 10-03 審查第 2 條:從別台收進來的列(帶 via)不管自己寫了什麼 host_id,一律當別台
            kept = [r for r in idx.history[test] if not r.get("via") and r.get("host_id") in (None, "", me)]
            idx.foreign_host_rows += len(idx.history[test]) - len(kept)
            if kept:
                idx.history[test] = kept
                idx.latest[test] = kept[-1]
            else:
                del idx.history[test]
                idx.latest.pop(test, None)
    if idx.voids:
        for test in list(idx.history):
            kept = [r for r in idx.history[test] if not any(_void_hits(v, r) for v in idx.voids)]
            idx.voided_rows += len(idx.history[test]) - len(kept)
            if kept:
                idx.history[test] = kept
                idx.latest[test] = kept[-1]
            else:
                del idx.history[test]
                idx.latest.pop(test, None)
        # 反查表不收窄:多挑是安全側
    if not idx.latest and not idx.degraded and not idx.voided_rows and not idx.foreign_host_rows:
        # 全被作廢 ≠ 池壞了:那是「沒有上次紀錄」(decide 回 no-previous,一樣必跑),不是降級
        idx.degraded = "no-usable-rows"
    return idx


def repo_identity(repo):
    """同一個 repo 的所有工作樹共用的身分(resume_store.repo_id:git common dir 的指紋);取不到回 None。"""
    try:
        import resume_store  # noqa: E402 - same folder
        return resume_store.repo_id(repo)
    except Exception:  # noqa: BLE001 - no identity ⇒ fall back to the path rule
        return None


def host_id():
    """這台機器的代號(12 碼):主機名與 machine-id 的雜湊,不帶主機名本身(紀錄要能離開這台機器)。
    10-02 第 2d 步:多台機器共用池時,作廢(void)要能指名「某台的列」。AIB_HOST_ID 可覆寫(治具用)。"""
    forced = os.environ.get("AIB_HOST_ID", "").strip()
    if forced:
        return forced
    import platform
    parts = [platform.node() or ""]
    for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            parts.append(Path(f).read_text(encoding="utf-8").strip())
            break
        except OSError:
            continue
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def stamp_row(row, repo, lock_now=None):
    """把本樹的 `lock_fp` 與 `repo` 寫進一列。

    跑過的新列帶著這兩欄:`lock_fp` 讓下一輪對得上鎖檔,`repo` 讓反查表分得出
    「這一列是哪一棵樹量的」。兩欄都是自己會好的那種 —— 舊列缺欄照樣能用,
    跑過一次之後就補齊了。執行器(不在本包)寫列之前呼叫它。
    """
    out = dict(row)
    out["lock_fp"] = lock_now if lock_now is not None else lock_fp(repo)
    out["repo"] = str(Path(repo).resolve())
    rid = repo_identity(repo)
    if rid:
        out["repo_id"] = rid
    out.setdefault("host_id", host_id())
    return out


# 舊名字留著:本包以外還沒有人叫它,但名字換了要有人看得到。
stamp_lock_fp = stamp_row


def _stdin_lines():
    return [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]


def _read_list(path):
    if not path:
        return []
    if path == "-":
        return _stdin_lines()
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reuse_index")
    ap.add_argument("mode", choices=("reverse", "decide", "stats", "quarantine", "void", "compact"))
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--rows", action="append", default=[])
    ap.add_argument("--changed", default="",
                    help=T("aib.vp2.help.changed"))
    ap.add_argument("--tests", default="",
                    help=T("aib.vp2.help.tests"))
    ap.add_argument("--apply", action="store_true", help="quarantine/compact: really rewrite (default: dry run)")
    ap.add_argument("--keep-per-test", type=int, default=None,
                    help="compact: also keep only the newest N rows per test (older keys archived, a stop marker left in place)")
    # void(第 2d 步):往 --rows 指的那一個列檔追加一筆作廢列
    ap.add_argument("--void-test", default="")
    ap.add_argument("--void-key-prefix", default="")
    ap.add_argument("--void-host-id", default="")
    ap.add_argument("--void-source", default="")
    ap.add_argument("--void-all", action="store_true")
    ap.add_argument("--reason", default="")
    args = ap.parse_args(argv)
    repo = str(Path(args.repo).resolve())
    if args.mode == "void":
        if len(args.rows) != 1:
            print(json.dumps({"error": "void needs exactly one --rows file"}))
            return 2
        cond = {k: v for k, v in (("test", args.void_test), ("key_prefix", args.void_key_prefix),
                                  ("host_id", args.void_host_id), ("source", args.void_source)) if v}
        if args.void_all:
            cond["all"] = True
        try:
            obj = write_void(args.rows[0], cond, args.reason)
        except ValueError as err:
            print(json.dumps({"error": str(err)}))
            return 2
        print(json.dumps(obj, ensure_ascii=False, sort_keys=True))
        return 0
    if args.mode == "compact":
        reports = [compact(f, apply=args.apply, keep_per_test=args.keep_per_test) for f in rows_files(args.rows)]
        print(json.dumps(reports, ensure_ascii=False, sort_keys=True))
        return 2 if any(r["refused"] for r in reports) else 0
    if args.mode == "quarantine":
        reports = [quarantine(f, apply=args.apply) for f in rows_files(args.rows)]
        print(json.dumps(reports, ensure_ascii=False, sort_keys=True))
        return 2 if any(r["changed"] for r in reports) else 0
    idx = build_index(args.rows, repo=repo)

    if args.mode == "stats":
        print(json.dumps({
            "rows_files": len(idx.files),
            "tests_indexed": len(idx.latest),
            "measured": sum(1 for r in idx.latest.values()
                            if r.get("measured") is True),
            "paths_indexed": len(idx.reverse),
            "bad_lines": idx.bad_lines,
            "foreign_rows": idx.foreign_rows,
            "cross_tree_rows": idx.cross_tree_rows,
            "unknown_origin_rows": idx.unknown_origin_rows,
            "voids": len(idx.voids),
            "voided_rows": idx.voided_rows,
            "local_only": idx.local_only,
            "foreign_host_rows": idx.foreign_host_rows,
            "rows_default_dir": idx.rows_default_dir,
            "degraded": idx.degraded,
        }, ensure_ascii=False, sort_keys=True))
        return 3 if idx.degraded else 0

    if args.mode == "reverse":
        changed = _read_list(args.changed) if args.changed else _stdin_lines()
        hits = idx.tests_reading(changed)
        tests = sorted({t for v in hits.values() for t in v})
        print(json.dumps({
            "reverse_hits": hits,
            "tests": tests,
            "rows_files": len(idx.files),
            "tests_indexed": len(idx.latest),
            "degraded": idx.degraded,
        }, ensure_ascii=False, sort_keys=True))
        return 3 if idx.degraded else 0

    changed = _read_list(args.changed)
    tests = _read_list(args.tests) or sorted(idx.latest)
    lock_now = lock_fp(repo)
    out = []
    for t in tests:
        verdict, reason, detail = idx.decide(repo, t, changed, lock_now=lock_now)
        out.append({"test": t, "verdict": verdict, "reason": reason,
                    "detail": detail})
    counts = {"reuse": 0, "must-run": 0}
    reasons = {}
    for d in out:
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
    print(json.dumps({"decisions": out, "counts": counts,
                      "reasons": reasons, "lock_fp": lock_now,
                      "degraded": idx.degraded},
                     ensure_ascii=False, sort_keys=True))
    return 3 if idx.degraded else 0


if __name__ == "__main__":
    sys.exit(main())
