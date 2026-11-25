#!/usr/bin/env bash
# AP26 W-7 頁面字(fc7f9 11-25,ebab8 派;c2f0b PAGE-WORDS-ap26-c2f0b-1125 r0 §2):aibd 回的碼 → 頁面那一句。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, AP26 block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGOW1 (AP26 W-7 §2 碼 → 頁面字)
k '    pair_key_weak: '\''這台的配對金鑰不需要本人就能簽,可能被換過。請重建配對金鑰。'\'',' '    pair_key_weak: '\''配對金鑰有問題'\'','
# PGOW2 (AP26 W-7 §2 碼 → 頁面字)
k '    unavailable: '\''AIB 現在不在你登入的桌面上執行' '    unavailable: '\''AIB 現在不在桌面上執行'
# PGOW3 (AP26 W-7 §2 碼 → 頁面字)
k '    passphrase_locked: '\''密語打錯太多次,暫時鎖住。'\'',' '    passphrase_locked: '\''鎖住了'\'','
# PGOW4 (AP26 W-7 §2 碼 → 頁面字)
k 'if (!Object.prototype.hasOwnProperty.call(OWNER_WHY, c)) return '\'''\'';' 'if (!(c in OWNER_WHY)) return '\'''\'';'
# PGOW5 (AP26 W-7 §2 碼 → 頁面字)
k '(isWin ? (ocCls === '\''hello_software'\''' '(true ? (ocCls === '\''hello_software'\'''
# PGOW6 (AP26 W-7 §2 碼 → 頁面字)
k '/^\d\d:\d\d:\d\d$/.test(t) ? word('\''lock_at'\''' 't ? word('\''lock_at'\'''
# PGOW7 (AP26 W-7 §2 碼 → 頁面字)
k 'reason: REPO_WHY[why] ? why : '\''owner_check_unavailable'\'', why, pageMsg: ownerWhy(why, t) || undefined }' 'reason: REPO_WHY[why] ? why : '\''owner_check_unavailable'\'', why }'
# PGOW8 (AP26 W-7 §2 碼 → 頁面字)
k 'ticket, why: t.reason, pageMsg: ownerWhy(t.reason, t) || undefined });' 'ticket, why: t.reason });'
# PGOW9 (AP26 W-7 §2 碼 → 頁面字)
k 'fpcDone(card, false, (e && e.pageMsg) || FP_NO[rs] || ' 'fpcDone(card, false, FP_NO[rs] || '
# PGOW10 (AP26 W-7 §2 碼 → 頁面字)
k 'if (rc !== '\''unavailable'\'' && rc !== '\''cancel'\'') { const ow = ownerWhy(rc, e); if (ow) return ow; }' '{ const ow = ownerWhy(rc, e); if (ow) return ow; }'
# PGOW11 (AP26 W-7 §2 碼 → 頁面字)
k '1. 打開「設定」→「帳戶」→「登入選項」。' '1. 打開「設定」→「登入選項」。'
# PGOW12 (c2f0b 18:00:Touch ID 現在讀不到 = 蓋子蓋著 / 鍵盤沒接)
k '    owner_check_finger_not_now: '\''這台的 Touch ID 現在讀不到(蓋子蓋著,或指紋鍵盤沒接):打開蓋子或接上鍵盤再按一次。'\'',' '    owner_check_finger_not_now: '\''Touch ID 讀不到。'\'','

# 四語(fc7f9 11-26;ebab8 00:35,Andy 定「本人驗證」「配對金鑰」):每語至少一把改字 + 一把刪條;退英文、不猜介面原名、語言跟 askLang
# AP4L1 en 改字
k '      cancel: '\''You pressed Cancel. Nothing was changed.'\'',' '      cancel: '\''You pressed Cancel.'\'','
# AP4L2 en 刪條
k '      pair_key_missing: '\''This computer has no pairing key yet. Make a pairing key first.'\'',' ' '
# AP4L3 ja 改字
k '      cancel: '\''キャンセルを押しました。何も変更していません。'\'',' '      cancel: '\''キャンセルしました。'\'','
# AP4L4 ja 刪條
k '      pair_key_missing: '\''このパソコンにはまだペアリングキーがありません。先にペアリングキーを作ってください。'\'',' ' '
# AP4L5 ko 改字
k '      cancel: '\''취소를 눌렀습니다. 아무것도 바꾸지 않았습니다.'\'',' '      cancel: '\''취소했습니다.'\'','
# AP4L6 ko 刪條
k '      pair_key_missing: '\''이 컴퓨터에는 아직 페어링 키가 없습니다. 먼저 페어링 키를 만드세요.'\'',' ' '
# AP4L7 ja 猜介面原名
k '      no_biometry: '\''このパソコンには Windows Hello が設定されていません。'\'',' '      no_biometry: '\''このパソコンには Windows Hello が設定されていません。'\'', unavailable: '\''AIB はサインインしたデスクトップで動いていません。'\'','
# AP4L8 退英文改退中文
k ': OWNER_WHY_L.en[k]);' ': zh);'
# AP4L9 不看語言
k '(L === '\''zh-TW'\'' ? zh : has(' '(true ? zh : has('
# AP4L10 語言不跟 askLang
k 'const L = typeof askLang === '\''function'\'' ? askLang() : '\''zh-TW'\''' 'const L = '\''zh-TW'\'''
# AP4L11 接句不空格
k 'sp = L === '\''zh-TW'\'' || L === '\''ja'\'' ? '\'''\'' : '\'' '\'';' 'sp = '\'''\'';'
# AP4L12 解鎖時間沒填
k '.replace('\''{t}'\'', () => t)' '.replace('\''{t}'\'', '\'''\'')'
# AP4L13 中文改回舊字
k '    pair_key_missing: '\''這台還沒有配對金鑰。請先建配對金鑰。'\'',' '    pair_key_missing: '\''這台還沒配對。請先配對。'\'','
# AP4L14 中文解鎖尾
k 'const OWNER_LOCK_NA = '\''解鎖時間:這一版 AIB 還沒有。'\'';' 'const OWNER_LOCK_NA = '\''解鎖時間:這一版 AIB 還沒給。'\'';'
# AP4L15 ko 刪 class_na
k '      class_na: '\''이 컴퓨터의 본인 확인 종류와 강화 방법: 이 버전의 AIB에는 아직 없습니다.'\'',' ' '
