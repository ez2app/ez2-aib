#!/usr/bin/env bash
# 開席設定卡頂端照 GET /api/v1/owner-check 的 class 寫這台是哪一種(fc7f9 11-26;c2f0b 03:31 欄位、ebab8 03:10 Windows 今天上線;W-7 §1)。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, oc_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# OC1 (非 Windows 也出)
k 'if (!d || d.platform !== '\''windows'\'' || !OC_CLASSES.includes(d.class)) return '\'''\'';' 'if (!d || !OC_CLASSES.includes(d.class)) return '\'''\'';'
# OC2 (不認得的 class 也查表)
k '|| !OC_CLASSES.includes(d.class)) return '\'''\'';' ') return '\'''\'';'
# OC3 (no_hello 不出三步)
k 'pin = c === '\''no_hello'\'' || c === '\''unknown'\''' 'pin = c === '\''unknown'\'''
# OC4 (unknown 不出三步)
k 'pin = c === '\''no_hello'\'' || c === '\''unknown'\''' 'pin = c === '\''no_hello'\'''
# OC5 (not_at_desk 沒有重新檢查)
k 're = pin || c === '\''not_at_desk'\''' 're = pin'
# OC6 (少第 2 步)
k ''\''在「PIN (Windows Hello)」按「設定」,照畫面設一個 PIN。'\'', ' ' '
# OC7 (卡上沒接)
k 'AIB 只開你核准過、這台電腦上真正安裝的 AI 程式。</div>${ocHtml()}${head}' 'AIB 只開你核准過、這台電腦上真正安裝的 AI 程式。</div>${head}'
# OC8 (開卡不讀)
k '    ocLoad();' '    void 0;'
# OC9 (重新檢查鈕沒接)
k '    if (S.add && x.closest('\''[data-ocre]'\'')) { ocLoad(); return; }' ' '
# OC10 (讀錯端點)
k 'get('\''/api/v1/owner-check'\'')' 'get('\''/api/v1/owner'\'')'
# OC12 (ja 步驟退成中文、不是英文)
k '? T[k] : OC_W.en[k];' '? T[k] : OC_W['\''zh-TW'\''][k];'
# OC13 (今天沒 W-2 卻說高風險不開放;c2f0b REVIEW 264d6a91 N2)
k '金鑰存在一般檔案裡。'\'',' '金鑰存在一般檔案裡,所以高風險的動作不開放。'\'','
