#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 左欄:AI 艦隊列表(真資料:AIB 席位檔 + activity_probe 真活動探針)
# 遷自舊 demo 的 left-fleet(E2.1)
#
# ★ 2026-08-21(舊載體廢止,艦長 a seat 派工):本檔原本 glob
#   舊載體的 sessions/by-id/*.json,而該目錄最新一筆停在 08-11。
#   兩個缺陷一起修,第二個比第一個嚴重:
#     (1) 資料是化石——列出來的席全是墓碑,而畫面看起來一直在正常運作。
#     (2) **位址是錯的**——舊碼印 `role@uuid[-5:]`,而位址是 `seat_id`。
#         實測席位檔逐份比對:seat_id 與 uuid 末五碼**沒有一份相同**。
#         那是一份「格式完全正確的錯答案」,與 seat_onboard.py:seats_dir
#         docstring 記的同一種發作方式,也正是 the maintainer 08-21 定址新規要防的。
#   ★ 不新增第九個 seats_dir():本檔 import seat_onboard.seats_dir(),
#     它的解析順位(HUB 導出 > INSTANCE_HOME > 主艙)由
#     tests/test-seats-dir-resolution.py 守著。EZ2_SEATS_DIR 若已導出則優先,
#     與 pane-right-ai.sh 同一個覆寫入口。
#   ★ 活性一律走 activity_probe.activity_age:它回 None 表示**無訊號**,
#     本檔照實顯示 `·` 而不是畫成 ⚫——「久沒動」與「量不到」是兩件事,
#     把後者畫成前者就是用一個看起來正常的值蓋掉「我不知道」。
. "$(dirname "$0")/cockpit-env.sh"
while true; do
  clear
  "$EZ2_PY3" - <<'PY'
import glob, json, os, sys, time

cockpit = os.environ.get('EZ2_COCKPIT_DIR') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, cockpit)
from seat_onboard import seats_dir            # noqa: E402
from activity_probe import (                  # noqa: E402
    activity_age, resolve_session, session_dirs, live_session_for)

now = time.time()
sdir = os.environ.get('EZ2_SEATS_DIR') or seats_dir()

print(' AI 艦隊')
print()

# ★ 「目錄不在」與「目錄裡零席」必須分得開:守衛的失效值不可以等於它表示
#   安全的那個值。前者是壞掉,後者是真的沒人在。
if not os.path.isdir(sdir):
    print(' ⚠ 席位目錄不存在')
    print(' %.28s' % sdir)
    raise SystemExit(0)

sessions = session_dirs()
rows = []
for p in sorted(glob.glob(os.path.join(sdir, 'w*.json'))):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except (OSError, ValueError):
        continue
    if not isinstance(d, dict) or d.get('schema') != 'aib-seat/v1':
        continue
    seat = d.get('seat_id')
    if not isinstance(seat, str) or not seat:
        continue
    role = d.get('role') if isinstance(d.get('role'), str) else '?'
    uuid, _note = resolve_session(d, sessions)
    # D41:與 seat_for_window 同源 —— claude 的活動鍵換成 by-seat 現用 session,缺檔退登記 uuid
    uuid = live_session_for(d.get('provider'), str(d.get('window_id') or ''), uuid, d.get('cwd'))
    age = activity_age(d.get('provider'), uuid, now)
    # 排序鍵:有訊號的照新舊在前,無訊號的一律墊底(不假裝它很舊)
    # ★ 寧可截角色也不截位址:被截斷的五碼仍然長得像一個合法位址,
    #   那是本檔開頭那條「格式正確的錯答案」的同一型。角色上限 7 字
    #   (現行最長 monitor)+ '@' + 五碼 = 13,下面用固定 13 欄純補空白。
    rows.append((0 if age is not None else 1,
                 age if age is not None else 0.0,
                 '%.7s@%s' % (role, seat), age))

rows.sort()
if not rows:
    print(' (無席位)')
for _k0, _k1, name, age in rows[:8]:
    if age is None:
        mark, a = '·', '--'
    else:
        mark = '🟢' if age < 900 else ('🟡' if age < 3600 else '⚫')
        a = '%dm' % (age // 60) if age < 6000 else '--'
    print(f' {mark} {name:<13} {a}')          # <13 只補不截
print()
print(' 🟢<15m 🟡<1h ⚫久 ·無訊號')
PY
  # 順便更新標題與信任燈(E2.2):共用本迴圈,不另起行程。
  # 這個面板停了節拍就凍住,而「更新器死了」正是要看得見的事實。
  bash "$EZ2_COCKPIT_DIR/title/title-beat.sh" --once >/dev/null 2>&1 || true
  cur=$(tmux -L "$EZ2_AIHUB_SOCKET" display -p '#W' 2>/dev/null)
  echo
  echo " 中欄: ${cur:-?}"
  echo " 切換 C-a 0/1/2"
  sleep 3
done
