#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib persona 來源|狀態|source|status 的入口(10-15 the maintainer「把 aib persona 來源 的入口也補上」)。

src/cockpit/aib 用它選好的 Python 跑這支;這裡只把 src 放進 sys.path,再交給 entitlement.sources_cli。
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from entitlement.sources_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
