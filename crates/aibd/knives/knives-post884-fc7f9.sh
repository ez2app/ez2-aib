#!/usr/bin/env bash
# post-884 頁面小修(fc7f9 11-25 接 08b0e,f163c REVIEW-post884 M1):省時檔次 tier_set 回的 dropped 是數字。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §15b) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGTD1 (f163c post884 M1:dropped 是數字)
k 'if (Number.isInteger(dropN) && dropN > 0) {' 'if (dropN && dropN.length) {'
# PGTD2 (f163c post884 M1:dropped 是數字)
k '已刪掉最舊的 ${dropN} 組</div>' '已略過較舊設定</div>'
