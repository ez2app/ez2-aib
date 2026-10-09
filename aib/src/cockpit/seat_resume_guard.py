#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席前:環境裡那顆 AIB_RESUME_SESSION_UUID 是不是**別的活席正在用的**。

2026-09-24(a seat 開 codexbot 席時 --dry-run 抓到):
`AIB_SEAT_ENV` 把 AIB_RESUME_UUID / AIB_RESUME_SESSION_UUID 塞進**每一席**的環境,
於是任何 AI 從自己的席位殼裡開席,新席就會**繼承開席者自己的 uuid** 去登記 ——
新席用開席者的碼上線,開席者自己的位址被蓋掉(dm 會投錯席)。

判準**不是**「有沒有設」(每一席都有設),是「這顆 uuid 是不是已經有活席持有」:
  · 席位檔 session_uuid 相同、沒有 exit_reason、pid 還活著 ⇒ 持有者 ⇒ 要洗掉
  · 席位檔在但 pid 死了 ⇒ 不算持有(那正是 09-14 要「接回既有對話」的情境)
  · 找不到 ⇒ 不算持有

只印持有者描述(空字串=沒有持有者),不做決定;決定在 aib 那一行。

2026-09-24 21:xx(a seat):判準搬到 seat_liveness.py(跨艙 + 行程表 + fork 不算持有);
本檔只剩相容入口。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seat_liveness  # noqa: E402  「持有」的唯一判準(09-24 a seat:本檔原本自帶一份,只掃本艙、只看 pid)


def owner_of(seats_dir, session_uuid, aib_home=None, procs=None):
    """回持有者描述或 ''。seats_dir 不存在/讀不到 ⇒ 該處缺資料不算持有。

    aib_home 沒給 = 舊語意:只看這一個登記簿(尺用)。
    aib_home 有給 = 09-24 語意:再加**所有艙**的登記簿 + 全機行程表(aib 開席用)。
      理由:98fe/@6 與 d6a8/@4 同時開著同一段對話,本艙守衛看不到別艙那一個。
    """
    if not session_uuid:
        return ""
    dirs = [seats_dir] if seats_dir and os.path.isdir(seats_dir) else []
    if aib_home:
        seen = {os.path.realpath(d) for d in dirs}
        dirs += [d for d in seat_liveness.all_seat_dirs(aib_home) if os.path.realpath(d) not in seen]
    h = seat_liveness.holders(session_uuid, seat_dirs=dirs, procs=procs)
    if not aib_home:
        h["process"] = []
    return seat_liveness.describe(h)


def main(argv):
    # seat_resume_guard.py <seats_dir> <session_uuid> [--all-cabins <aib_home>]
    if len(argv) == 5 and argv[3] == "--all-cabins":
        sys.stdout.write(owner_of(argv[1], argv[2], aib_home=argv[4]))
        return 0
    if len(argv) != 3:
        print("用法:seat_resume_guard.py <seats_dir> <session_uuid> [--all-cabins <aib_home>]", file=sys.stderr)
        return 2
    sys.stdout.write(owner_of(argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
