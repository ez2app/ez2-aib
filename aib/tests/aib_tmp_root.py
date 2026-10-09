# SPDX-License-Identifier: Apache-2.0
"""測試共用的暫存根目錄。

macOS 上 `/private/tmp` 存在且可寫就用它(跟倉裡歷史測試一致);
Linux CI 沒有這個目錄,改用 `/tmp`。
兩個都不行就**大聲失敗**,不靜默回 `tempfile.gettempdir()` 或 `$TMPDIR`。

檔名不帶連字號是刻意的:`test-*.py` 的模組名含 `-`,無法被 `import`。
"""

import os

# review r1 m9:測試預設基因**關**。ez2_genes 沒設 AIB_GENES 時讀 $HOME/.ez2-aib/genes.json ——
#   沒換 HOME 的舊測試會讀到操作者真的開關,開著時一堆斷言開場字/系統提示的格就翻紅(或假綠)。
#   只 setdefault:格子自己設了 AIB_GENES(要量「開」的那幾支)照舊贏;子行程繼承 os.environ 一起關。
#   ★ 放在測試共用檔、不改 ez2_genes 的檔案位置:真使用者的開關語意一個字都不動。
os.environ.setdefault("AIB_GENES", "0")

CANDIDATES = ("/private/tmp", "/tmp")


class TmpRootUnavailable(RuntimeError):
    """兩個候選都不存在或不可寫。不是警告,是呼叫端必須處理的失敗。"""


def _why_unusable(path):
    if not os.path.exists(path):
        return "不存在"
    if not os.path.isdir(path):
        return "不是目錄"
    if not os.access(path, os.W_OK | os.X_OK):
        return "不可寫"
    return None


def aib_tmp_root():
    """回目前機器上可寫的測試暫存根目錄(字串)。

    順序寫死為 `CANDIDATES`:先 `/private/tmp`,再 `/tmp`。
    不接受呼叫端塞第三個「猜的」路徑——要改候選,改本檔並說明為什麼。
    """
    # 10-08:aib test 給每支測試自己的暫存資料夾(AIB_TEST_TMPDIR),跑完就刪 —— 有給而且能寫就用它,測試留下的東西不會堆在 /tmp
    given = os.environ.get("AIB_TEST_TMPDIR") or ""
    if given and _why_unusable(given) is None:
        return given
    tried = []
    for path in CANDIDATES:
        why = _why_unusable(path)
        if why is None:
            return path
        tried.append("%s(%s)" % (path, why))
    raise TmpRootUnavailable(
        "找不到可寫的測試暫存根目錄。試過: %s。"
        "本函式不會改猜 TMPDIR 或 tempfile.gettempdir()。"
        % "; ".join(tried)
    )
