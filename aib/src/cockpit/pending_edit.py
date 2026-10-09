#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""訊息待審區的 pending_edit —— 已搬成收費外掛 pending-review(10-08 the maintainer:不放在免費程式裡)。這支只負責轉交:
有裝外掛 ⇒ 在這支的模組裡執行外掛那份(import 與直接執行都一樣;__file__ 仍是這支 ⇒ 找同層檔案的寫法不用改);
沒裝 ⇒ import 失敗(ImportError)、直接執行回 0。准不准開仍由功能開關判。"""
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import addon_path as _addon_path  # noqa: E402

_src = _addon_path.find("pending-review", "pending_edit.py")
if _src is None:
    if __name__ == "__main__":
        _sys.exit(0)
    raise ImportError("pending-review 外掛沒裝(pending_edit)")
exec(compile(_Path(_src).read_text(encoding="utf-8"), str(_src), "exec"), globals())
