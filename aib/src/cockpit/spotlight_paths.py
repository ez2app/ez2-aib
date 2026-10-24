# SPDX-License-Identifier: Apache-2.0
"""Mac 的 Spotlight 會不會掃這個路徑(10-24 the maintainer「mac 會一直掃檔案建快取」「不是提醒，是用程式必要規定」)。

Spotlight 掃家目錄底下的一般夾;點開頭的夾(~/.ez2-aib)與名字結尾 .noindex 的夾不掃。
工作樹 / clone 放在會被掃的地方 ⇒ 測試一跑就重掃、拖慢機器。新樹一律開在 ~/wt.noindex/。
只在 Mac 上算數(CI 有 Linux)。
"""
import os
import sys

NOINDEX_HOME = "~/wt.noindex"


def indexed(path, home=None, platform=None) -> bool:
    if (platform or sys.platform) != "darwin" or not path:
        return False
    home = os.path.realpath(home or os.path.expanduser("~"))
    p = os.path.realpath(path)
    if p != home and not p.startswith(home + os.sep):
        return False
    parts = [x for x in p[len(home):].split(os.sep) if x]
    return bool(parts) and not any(x.startswith(".") or x.endswith(".noindex") for x in parts)
