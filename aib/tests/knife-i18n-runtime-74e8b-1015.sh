#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# 刀:test-i18n-runtime-74e8b-1015.py 的四刀,都落在整棵樹的複本上(真樹不動)。全部咬到 ⇒ 結束碼 0。
#   sh aib/tests/knife-i18n-runtime-74e8b-1015.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
TEST=test-i18n-runtime-74e8b-1015.py
bad=0
knife() {   # 名字 要紅的測試(方法名開頭) 檔(相對 repo) 原字 新字
  name=$1; want=$2; rel=$3; old=$4; new=$5
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/aib-knife-i18n.XXXXXX")
  mkdir "$tmp/tree"
  (cd "$REPO" && tar --exclude .git --exclude __pycache__ -cf - bin aib) | (cd "$tmp/tree" && tar -xf -)
  if ! python3 - "$tmp/tree/$rel" "$old" "$new" <<'PY'
import sys
p, old, new = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
if s.count(old) != 1:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
  then echo "$name: 錨不是恰好一處,沒落刀"; bad=1; rm -rf "$tmp"; return; fi
  out=$(cd "$tmp/tree/aib" && python3 "tests/$TEST" 2>&1)
  if echo "$out" | grep -q "^FAIL: $want"; then echo "$name: $want 紅(咬到)"; else echo "$name: $want 沒紅 —— 刀沒咬到"; bad=1; fi
  rm -rf "$tmp"
}
knife K1 test_r1_doctor aib/src/bootstrap/aib_doctor.py \
  '_C("state_root_exists.missing_remedy")' '"跑 aib init 建立狀態根"'
knife K2 test_r5_bottom_panel aib/src/cockpit/perf_tab_fmt.py "mark = _t('aib.perf_tab.mark_manual')" "mark = '手動'"
knife K3 test_r2_small_commands aib/src/cockpit/seat_now.py 'out.write(T("aib.now.all_none") + "\n")' 'out.write("量不到任何還在的席(席位檔讀不到)\n")'
knife K4 test_z2_four_locales aib/i18n/en.json \
  'needs at least {n} test tasks both with and without genes' 'needs {n} test tasks with and {n} without genes'
exit $bad
