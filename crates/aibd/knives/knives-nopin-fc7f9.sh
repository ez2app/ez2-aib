#!/usr/bin/env bash
# 沒有 PIN 時頁面要引導(fc7f9 11-26,ebab8 03:10 Windows 今天上線;c2f0b PAGE-WORDS-ap26 §1 (b) 三步):Windows 上 no_biometry / owner_check_too_weak 後面接設 PIN 三步。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, AP26 W-7 block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# NP1 (沒設 Hello 只說沒有)
k '    if (c === '\''no_biometry'\'' && isWin) return w + sp + word('\''pin'\'', OWNER_PIN_HOW);' ''
# NP2 (Mac 也接 PIN 步驟)
k 'if (c === '\''no_biometry'\'' && isWin)' 'if (c === '\''no_biometry'\'')'
# NP3 (少第 2 步)
k '2. 在「PIN (Windows Hello)」按「設定」,照畫面設一個 PIN。' ''
# NP4 (英文少第 3 步)
k '3. Come back to this page and press again. ' ''
# NP5 (沒寫要先設密碼)
k '設好回到這頁再按一次。沒有 Windows 登入密碼的話,要先設密碼才能設 PIN。' '設好回到這頁再按一次。'
# NP6 (軟體 Hello 仍接 PIN 三步;f163c DECISION-w2 附條件 1)
k '(ocCls === '\''hello_software'\'' ? word('\''no_tpm'\'', OWNER_NO_TPM) : word('\''pin'\'', OWNER_PIN_HOW))' '(word('\''pin'\'', OWNER_PIN_HOW))'
# NP7 (class 不看平台(mac 的 class 也算);f163c DECISION-w2 附條件 1)
k 'S.oc.d.platform === '\''windows'\'' ? String(S.oc.d.class || '\'''\'') : '\'''\''' 'S.oc.d.platform ? String(S.oc.d.class || '\'''\'') : '\'''\'''
# NP8 (沒說設 PIN 解決不了;f163c DECISION-w2 附條件 1)
k ';設 PIN 也解決不了。'\'';' '。'\'';'
# NP9 (英文表少 no_tpm、退回中文;f163c DECISION-w2 附條件 1)
k '      no_tpm: '\''This computer has no security chip' '      no_tpm0: '\''This computer has no security chip'
