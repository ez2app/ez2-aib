#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cooldown.py EZ2_AIB_COOLDOWN_POLL_PIN(goal 2 治具,a63ab 11-12 01:00 定)。

P1 沒設:load1 10 ⇒ warn、poll_s 10(原行為不變)
P2 設 ok:等級、原因照真值(仍是 warn),只有 poll_s 換成 5,並記 poll_pinned=ok
P3 值不是 ok / warn / hot / critical(空字串、亂字、數字)⇒ 當沒設
P4 大小寫與前後空白不影響(" OK " ⇒ ok)
P5 釘在 hot ⇒ poll_s 30(釘的是一級,不是「永遠最快」)

不碰真艙:EZ2_AIB_INSTANCE 拿掉 ⇒ live_cabin() 為假,apply 只算等級。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "cockpit"))
os.environ.pop("EZ2_AIB_INSTANCE", None)
os.environ.pop("EZ2_AIB_COOLDOWN_POLL_PIN", None)

import cooldown  # noqa: E402

CELLS = [{"key": "load1", "label": "負載", "value": 10.0, "state": "warn"}]
FAILS = []


def check(cond, reason, detail=""):
    if not cond:
        FAILS.append("reason: %s %s" % (reason, detail))


def run(pin):
    if pin is None:
        os.environ.pop(cooldown.POLL_PIN_ENV, None)
    else:
        os.environ[cooldown.POLL_PIN_ENV] = pin
    try:
        return cooldown.apply(CELLS, dry_run=True)
    finally:
        os.environ.pop(cooldown.POLL_PIN_ENV, None)


def main():
    o = run(None)
    check(o["level"] == cooldown.WARN and o["poll_s"] == 10, "unpinned_changed", o)
    check("poll_pinned" not in o, "unpinned_marked", o)

    o = run("ok")
    check(o["poll_s"] == 5, "pin_not_applied", o)
    check(o.get("poll_pinned") == "ok", "pin_not_recorded", o)
    check(o["level"] == cooldown.WARN and o["reasons"], "pin_changed_level", o)
    check(o["load_level"] == cooldown.WARN, "pin_changed_level", o)

    for bad in ("", "fast", "5", "none"):
        o = run(bad)
        check(o["poll_s"] == 10 and "poll_pinned" not in o, "bad_pin_accepted", (bad, o))

    o = run(" OK ")
    check(o["poll_s"] == 5 and o.get("poll_pinned") == "ok", "pin_case_not_normalised", o)

    o = run("hot")
    check(o["poll_s"] == 30 and o.get("poll_pinned") == "hot", "pin_not_a_level", o)

    for f in FAILS:
        print(f)
    print("OK" if not FAILS else "FAIL %d" % len(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
