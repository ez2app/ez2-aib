# SPDX-License-Identifier: Apache-2.0
"""Shared slow-warn numbers for `aib test` and the witness.

Do not copy SLOW_RATIO / SLOW_MIN_EXTRA_S elsewhere. test_entry and
witness_slow both import from here. tests/test-aib-test-entry-0929.py
knife k7 mutates the SLOW_RATIO assignment in this file.
"""
from __future__ import annotations

SLOW_RATIO = 1.10
SLOW_MIN_EXTRA_S = 1.0


def slow_threshold(base):
    """平常 base 秒 ⇒ 超過幾秒算慢;沒有前例回 None。"""
    if base is None:
        return None
    return max(base * SLOW_RATIO, base + SLOW_MIN_EXTRA_S)
