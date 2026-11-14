# SPDX-License-Identifier: Apache-2.0
# 席位視角(非艦長畫面,Andy 11-13 改案)的 shell 接點:只找 Rust 程式 aib-seatview 並呼叫,邏輯都在
# crates/aib-plan/src/seatview.rs(Andy 11-13「要用 rust 寫而不是 py」)。
# 找程式順序:$AIB_SEATVIEW_BIN → ~/.ez2-aib/bin/aib-seatview → PATH。找不到 = 完整版面(照舊,一格不少)。
# 給 cockpit-full.sh / cockpit-resize.sh / aib 用 `.` 載入;不設 set -e 相關選項、不印東西。

aib_seatview_bin() {
  if [ -n "${AIB_SEATVIEW_BIN:-}" ]; then
    [ -x "$AIB_SEATVIEW_BIN" ] && printf '%s' "$AIB_SEATVIEW_BIN"
    return 0
  fi
  if [ -x "$HOME/.ez2-aib/bin/aib-seatview" ]; then
    printf '%s' "$HOME/.ez2-aib/bin/aib-seatview"
    return 0
  fi
  command -v aib-seatview 2>/dev/null || true
}

# 座艙窗($1 = SESSION:cockpit)是不是席位視角;沒程式 / 讀不到 ⇒ 不是(rc 1)
aib_seatview_is_seat() {
  local bin
  bin="$(aib_seatview_bin)"
  [ -n "$bin" ] || return 1
  [ "$("$bin" status --window "$1" 2>/dev/null)" = seat ]
}
