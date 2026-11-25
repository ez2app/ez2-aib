#!/usr/bin/env bash
# AP26 W-7 頁面字(fc7f9 11-25,ebab8 派;c2f0b PAGE-WORDS-ap26-c2f0b-1125 r0 §2):aibd 回的碼 → 頁面那一句。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, AP26 block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGOW1 (AP26 W-7 §2 碼 → 頁面字)
k '    pair_key_weak: '\''這台的配對金鑰不需要本人就能簽,可能被換過。請重新配對。'\'',' '    pair_key_weak: '\''配對金鑰有問題'\'','
# PGOW2 (AP26 W-7 §2 碼 → 頁面字)
k '    unavailable: '\''AIB 現在不在你登入的桌面上執行' '    unavailable: '\''AIB 現在不在桌面上執行'
# PGOW3 (AP26 W-7 §2 碼 → 頁面字)
k '    passphrase_locked: '\''密語打錯太多次,暫時鎖住。'\'',' '    passphrase_locked: '\''鎖住了'\'','
# PGOW4 (AP26 W-7 §2 碼 → 頁面字)
k 'if (!Object.prototype.hasOwnProperty.call(OWNER_WHY, c)) return '\'''\'';' 'if (!(c in OWNER_WHY)) return '\'''\'';'
# PGOW5 (AP26 W-7 §2 碼 → 頁面字)
k '/Win/.test(String((globalThis.navigator && navigator.platform) || '\'''\'')) ? OWNER_PIN_HOW : OWNER_CLASS_NA' 'OWNER_PIN_HOW'
# PGOW6 (AP26 W-7 §2 碼 → 頁面字)
k '/^\d\d:\d\d:\d\d$/.test(t) ? t + '\'' 後可以再試。'\''' 't ? t + '\'' 後可以再試。'\'''
# PGOW7 (AP26 W-7 §2 碼 → 頁面字)
k 'reason: REPO_WHY[why] ? why : '\''owner_check_unavailable'\'', why, pageMsg: ownerWhy(why, t) || undefined }' 'reason: REPO_WHY[why] ? why : '\''owner_check_unavailable'\'', why }'
# PGOW8 (AP26 W-7 §2 碼 → 頁面字)
k 'ticket, why: t.reason, pageMsg: ownerWhy(t.reason, t) || undefined });' 'ticket, why: t.reason });'
# PGOW9 (AP26 W-7 §2 碼 → 頁面字)
k 'fpcDone(card, false, (e && e.pageMsg) || FP_NO[rs] || ' 'fpcDone(card, false, FP_NO[rs] || '
# PGOW10 (AP26 W-7 §2 碼 → 頁面字)
k 'if (rc !== '\''unavailable'\'' && rc !== '\''cancel'\'') { const ow = ownerWhy(rc, e); if (ow) return ow; }' '{ const ow = ownerWhy(rc, e); if (ow) return ow; }'
# PGOW11 (AP26 W-7 §2 碼 → 頁面字)
k '「設定」→「帳戶」→「登入選項」→「PIN (Windows Hello)」' '「設定」→「登入選項」'
# PGOW12 (c2f0b 18:00:Touch ID 現在讀不到 = 蓋子蓋著 / 鍵盤沒接)
k '    owner_check_finger_not_now: '\''這台的 Touch ID 現在讀不到(蓋子蓋著,或指紋鍵盤沒接):打開蓋子或接上鍵盤再按一次。'\'',' '    owner_check_finger_not_now: '\''Touch ID 讀不到。'\'','
