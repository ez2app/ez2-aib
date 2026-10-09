#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位檔機器守衛(the maintainer 08-15 直令第(2)件)。

派工原話:「席位檔寫入後 session_uuid 缺失即失敗並**指名是哪一份檔**」。

★ 一個誠實修正,寫在最前面(08-15 實查後由 root 席裁決採納):
  派工原本的理由是「這個回歸能溜進來,就是因為沒有任何東西在看它」。
  **那個前提不成立,已由 root 席撤回。** 實查結論:
    - `aib:243 write_seat_record` **從來不寫 session_uuid**;該欄一律由
      開席尾段 `seat-detect.py --apply` 事後回填。
    - seat-detect 對 claude 只有一個探針(transcript 建檔時間窗 ±150s),
      且**候選不唯一就誠實不寫**(seat-detect.py:76-78:「錯綁比空值更糟」)。
  所以「缺 uuid」不是溜進來的回歸,是**設計上的誠實輸出**。

  這件事直接決定本守衛的預設值:如果預設就對「缺 uuid」判失敗,那麼本機
  既有的 w1/w2/w11/w14/w15 會讓它**永遠紅**——而永遠紅的守衛沒有人看,
  那正是派工想解決的病本身。故:

    預設      = 只對**結構性缺陷**判失敗(記錄認不出、uuid 有值但形狀壞)。
                「有值但形狀壞」比「沒值」嚴重:它會讓下游以為綁好了。
    --require-uuid = 額外對「缺 uuid」判失敗(即派工要的那個模式)。
                用在**剛開完席**這種「缺就是可行動」的情境;拿去掃陳年
                席位目錄只會得到一片可預期的紅。

  兩種模式都**逐檔指名**(路徑+診斷碼+人話),這是派工的硬要求。

判定不在本檔:一律呼叫 `seat_status`(單一真相源)。守衛與右欄面板共用同
一套判準,兩份實作遲早分岔成「面板說未編制、守衛說沒事」。

CLI:
  seat_guard.py [--seats DIR] [--require-uuid] [--quiet]
  rc:0=無失敗 / 1=有失敗(逐檔指名於 stdout) / 2=用法或環境錯誤

  ★ rc=0 **不等於「檢查過了」**:目錄存在但一份 w*.json 都沒有時也是 rc=0
    (空目錄是合法狀態,見 main() 裡的理由)。那種情形一律往 stderr 出聲,
    `--quiet` 也擋不住;摘要行則一律指名實際掃描的目錄。呼叫端若要「必須
    掃到東西」,請自己判 stderr 或先確認目錄非空——**不要拿 rc=0 當覆蓋率**。

★ 觸發條件是**兩條**(no_seat / no_uuid)。曾經有第三條 child_source,
  08-16 初審高-3 移除——它在生產路徑上不可達且邏輯上多餘,理由與取代它的
  不變量測試見 `seat_status.py` 檔頭。
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seat_status import (  # noqa: E402
    NO_SEAT, NO_UUID, record_legible, unassigned_reason, uuid_shape_ok)

# 診斷碼 → 人話(輸出給人看的那一欄;碼給機器 grep)
#
# ★ 08-16 初審中-1 原本的修法是:把印不出來的 CHILD_SOURCE 從這裡移除、
#   改列進 `UNCOVERED` 具名承認「本守衛看不到 session,涵蓋不到 (c)」。
#   **那個修法的前提是 (c) 還存在。** 同一輪的高-3 裁掉了 (c) 本身(它在
#   生產路徑上不可達,且邏輯上多餘,見 seat_status 檔頭),前提沒了,
#   `UNCOVERED` 也一併移除——**沒有東西可以「涵蓋不到」了**。
#   留著一個空的 UNCOVERED 只會讓下一個人去找那個不存在的第三條。
REASON_TEXT = {
    NO_SEAT: "席位檔認不出來(schema/window_id/檔名其一不合)",
    NO_UUID: "session_uuid 缺失 → 顯示為 ❓未編制",
}
BAD_UUID = "invalid_session_uuid"
UNREADABLE = "unreadable"


def seats_dir_default():
    # EH.2:改走單一真相源 seats_dir_lib(HUB 導出優先)。--seats 明確指定時
    # 呼叫端不會用到本函式,所以這裡只影響「沒指定」那條路。
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from seats_dir_lib import seats_dir as _sd   # noqa: E402
    return _sd()


def check_file(path, require_uuid=False):
    """單檔 → (fail: bool, code: str|None, detail: str)。純判定不印。"""
    base = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as err:
        # 讀不到/壞 JSON 一律失敗:席位檔是顯示資料的正本,壞掉必須有人知道
        return True, UNREADABLE, "讀不到或不是合法 JSON:%s" % err
    legible, why = record_legible(rec, base)
    if not legible:
        return True, NO_SEAT, why
    raw = rec.get("session_uuid")
    if raw is not None and not uuid_shape_ok(raw):
        # ★「有值但形狀壞」永遠是失敗,與 --require-uuid 無關:它會讓下游
        #   誤以為這一席已經綁好(比缺值更毒)。
        return True, BAD_UUID, "session_uuid 有值但形狀不合:%r" % (raw,)
    reason = unassigned_reason(rec, base)
    if reason == NO_UUID:
        return bool(require_uuid), NO_UUID, REASON_TEXT[NO_UUID]
    if reason is not None:
        return False, reason, REASON_TEXT.get(reason, reason)
    return False, None, "ok"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seat_guard")
    ap.add_argument("--seats", default=None, help="席位目錄(預設走 env)")
    ap.add_argument("--require-uuid", action="store_true",
                    help="缺 session_uuid 也判失敗(剛開完席用;見檔頭)")
    ap.add_argument("--quiet", action="store_true", help="只印失敗行")
    args = ap.parse_args(argv)

    sdir = args.seats or seats_dir_default()
    if not os.path.isdir(sdir):
        print("seat_guard: 席位目錄不存在:%s" % sdir, file=sys.stderr)
        return 2

    paths = sorted(glob.glob(os.path.join(sdir, "w*.json")))
    failures = 0
    for path in paths:
        fail, code, detail = check_file(path, args.require_uuid)
        if fail:
            failures += 1
            # ★逐檔指名(派工硬要求):路徑在前,機器好 grep;碼次之;人話最後
            print("FAIL %s [%s] %s" % (path, code, detail))
        elif not args.quiet:
            print("ok   %s [%s] %s" % (path, code or "-", detail))
    # ★★ 08-16 初審高-2:掃 0 份原本回 rc=0,`--quiet` 下更是**輸出 0 位元組**
    #    ——一個什麼都沒檢查的守衛,對呼叫端長得跟「全部通過」一模一樣。
    #
    #    為什麼**不**判紅:全新 instance 的席位目錄本來就是空的,「0 份」是
    #    合法狀態。判紅會做出一個恆亮的告警,而**一個永遠會叫的閘和一個永遠
    #    不叫的閘,一樣沒人看**(哨兵門檻 14 天對 24h 窗即是此病)。
    #    所以修的是**可見性不是 rc**:0 份時一律出聲,`--quiet` 也擋不住。
    #    走 stderr,不污染 stdout 的逐檔清單(呼叫端可能在 grep FAIL 行)。
    if not paths:
        print("seat_guard: 掃 0 份 —— 目錄存在但沒有 w*.json:%s\n"
              "  (這不是通過:本次沒有檢查任何東西。多 instance 環境請確認"
              "抽屜:主艦隊 ~/.ez2-aib/seats 與 instances/<N>/seats 是不同的)"
              % sdir, file=sys.stderr)
    if not args.quiet:
        # ★ 摘要行指名**實際掃描的目錄**(與 pane-right-ai 的整片 ❓ 診斷同一
        #   條紀律:畫面/輸出說的「我沒找到問題」要能區分「真的沒問題」與
        #   「我在看錯的抽屜」)。86db75b 給了面板,沒給守衛。
        print("seat_guard: 掃 %d 份,失敗 %d 份%s;目錄=%s"
              % (len(paths), failures,
                 "" if args.require_uuid else "(未開 --require-uuid)", sdir))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
