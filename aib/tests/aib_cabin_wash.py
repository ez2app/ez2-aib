# SPDX-License-Identifier: Apache-2.0
"""艙導向環境變數:**單源**抽取 + 洗環境助手。

紅9 §3 的判準是「導向集合不得手列,由 `src/cockpit/cabin_resolve.sh` 自身導出」。
本模組就是那個單一來源;任何考卷要洗環境,呼叫這裡,不要自己列一份。

★ 為什麼不寫成常數:判準作者自己在做這件事時,手列的名單**兩個方向都差**
(多列兩個不在集合裡的、漏掉兩個在集合裡的),而乾淨輪之所以沒有洞,只因為
漏掉的那兩個在他環境裡剛好沒值——**那是運氣不是方法**。一個當下正在專心處理
這個 bug 的人,手列名單仍然漏兩個。

★ 抽取規則:剝掉註解後的可執行碼裡出現的 `EZ2_*` 名字。剝註解是必要的——
不剝的話,`cabin_resolve.sh` 檔頭那張說明表會讓「碼裡已經沒有它了」與
「碼裡還有它」得到一模一樣的答案。
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aib_testlib as _lib  # noqa: E402

CABIN_RESOLVE_RELPATH = os.path.join("src", "cockpit", "cabin_resolve.sh")


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def product_cabin_env(root=None):
    """回 `cabin_resolve.sh` **當下**宣告的艙導向名字集合,已排序去重。

    ★★★ 08-28 改法(艦長派工 + `qa3` 17:45/17:47 裁):**跑產品,不再用正則猜。**
    舊版是 `re.findall(r"EZ2_[A-Z0-9_]+", 剝註解後的碼)`,而那個前綴讓它
    **結構上看不見** `AIB_DM_SOCKET` / `AIB_DM_TMUX` —— 而那兩個正是七個消費者
    解「這是哪一個艙」時**排第一順位**讀的名字(`seats_dir_lib.py:70`)。
    ⇒ 舊版的漏,補再多名字也補不到,因為漏的是**尺**不是名單。

    ★ 現在問產品自己:`cabin_resolve.sh --env-names`。
    **必落空端保留且更強**:餵一份把碼全搬進註解的 `cabin_resolve.sh`,
    那個 `case` 分支也一起被註解掉 ⇒ 沒有輸出 ⇒ 這裡回空 tuple。
    ★ `rc != 0` 是**探測失敗**,不是空集合 —— 直接 raise,不得回空(回空會讓
    「產品壞了」與「沒有導向變數」得到一模一樣的答案)。
    """
    path = os.path.join(root or repo_root(), CABIN_RESOLVE_RELPATH)
    proc = subprocess.run(["bash", path, "--env-names"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            "cabin_resolve.sh --env-names 失敗(rc=%d):%s —— 這是探測失敗,"
            "不是空集合" % (proc.returncode, proc.stderr.strip()[-300:]))
    return tuple(sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}))


def wash_cabin_env(env=None, root=None):
    """把導向集合從 `env` 清掉,回傳被清掉的原值(給 restore 用)。

    回傳的 dict **含被清掉的每一個鍵**,值可能是 None(本來就沒設)——
    「本來沒設」與「設了空字串」是兩件事,還原時不可壓成同一種。
    """
    env = os.environ if env is None else env
    saved = {}
    for name in product_cabin_env(root):
        saved[name] = env.get(name)
        env.pop(name, None)
    return saved


def restore_cabin_env(saved, env=None):
    env = os.environ if env is None else env
    for name, value in saved.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value


class washed_cabin_env(object):
    """with 區塊:進去洗乾淨,離開原樣還原。"""

    def __init__(self, env=None, root=None):
        self._env = env
        self._root = root
        self._saved = None

    def __enter__(self):
        self._saved = wash_cabin_env(self._env, self._root)
        return self._saved

    def __exit__(self, *exc):
        restore_cabin_env(self._saved, self._env)
        return False
