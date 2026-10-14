#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""疑似金鑰的樣式 —— 一張共用表(10-13 a seat,紀錄倉設計 docs/DESIGN-records-repo-1013.md §2)。

原本只在 create_and_bind_persona.py 的 LINT_RULES 裡寫了三條(sk-…、AKIA…、-----BEGIN);
紀錄倉推上 git 前要掃同一批再多幾條,所以抽成這一張表:
  BASE  = 原本那三條(人格卡建立時的檢查照舊只用這三條,行為不變)
  EXTRA = 紀錄倉多掃的(GitHub token、Slack token、token= 長字串)
  ALL   = BASE + EXTRA
BASE/EXTRA/ALL 是既有人格建立與使用者選入紀錄的政策,保留其歷史門檻。
EXPORT_SECRET_PATTERNS 是公開匯出與大腦備份共用的憑證表;publish_public 另加人名匿名化,
brain 另加賦值/前綴鍵等嚴格檢測。兩種門檻刻意分工,不是要求所有呼叫者套用同一政策。
日後若統一政策,需一起驗人格建立與既有紀錄匯出,不在此更動那些呼叫者的行為。
每條 (名稱, 正規式)。命中時只回名稱,不回命中的值(不把金鑰印出來)。
"""
import re

BASE = (
    ("secret-apikey", r"sk-[A-Za-z0-9]{20,}"),
    ("secret-aws", r"AKIA[0-9A-Z]{16}"),
    ("secret-pem", r"-----BEGIN"),
)
EXTRA = (
    ("secret-github", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("secret-slack", r"xox[bp]-[A-Za-z0-9-]{10,}"),
    ("secret-token", r"token=[A-Za-z0-9_\-]{20,}"),
)
ALL = BASE + EXTRA


def hits(text, rules=ALL):
    """回命中的樣式名稱清單(依表順序、不重複);不回值。"""
    return [name for name, pattern in rules if re.search(pattern, text)]


# Public-export and local backup share these credential detectors. Maintainer
# anonymity rules belong only to the private public-export command.
EXPORT_SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "api key": re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{24,}"),
    "google key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}
