#!/usr/bin/env bash
# Windows 開席登入(fc7f9 11-26;Andy 02:0x「用那台既有的登入,過期或沒有一樣彈出綁定視窗」;08de5 定案 TABLE-win-login-on-open-fc7f9-1126 §F)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, win_login_*) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# WL1 沒登入的品牌不標 nl(新增視窗不出重新登入卡)
k 'nl: !!(p && !p.open && p.reason === '\''not_logged_in'\''),' 'nl: false,'
# WL2 全灰清單沒登入那家不放重新登入卡
k '${p.nl && RELOG_NAME[p.key] ? '\''。'\'' + addLoginCard(p.key) :' '${false ? '\'''\'' :'
# WL3 開席 409 not_logged_in 不出卡、只寫錯誤
k 'if (nl && RELOG_NAME[A.provider]) { A.needLogin = A.provider;' 'if (false) { A.needLogin = A.provider;'
# WL4 登入完不自動再開
k 'if (snap) return doAdd(snap); }' 'if (snap) return render(); }'
# WL5 登入完不重抓 version
k 'S.version = await soft(get('\''/api/v1/version'\'')).catch(() => S.version); // 08de5 定案 4' '// 08de5 定案 4'
# WL6 Windows 沒驗過又叫人去終端機
k 'login_not_tried_here: '\''這一版 AIB 在 Windows 先只支援 Claude,這一家要等下一版'\'',' 'login_not_tried_here: '\''請在這台的終端機先登入它'\'','
# WL7 品牌原因字改回只寫沒登入
k 'not_logged_in: '\''這台的登入沒有或已過期'\'' };' 'not_logged_in: '\''這台還沒登入'\'' };'
# WL8 快照不分品牌(換品牌後登入完仍自動開)
k 'const addSnap = (b) => JSON.stringify(Object.assign({}, b, { idem_key: '\'''\'' }));' 'const addSnap = (b) => JSON.stringify(Object.assign({}, b, { idem_key: '\'''\'', provider: '\'''\'', mode: '\'''\'' }));'
# WL9 拿掉快照閘(設定改過也自動開)
k 'if (typeof onlySnap === '\''string'\'' && addSnap(body) !== onlySnap) {' 'if (false) {'
# WL10 不存被擋那次的快照
k '      A.nlSnap = addSnap(body);' '      A.nlSnap = null;'
# WL13 (英文退回中文(四語少一語);ebab8 03:34 Windows 先只支援 Claude)
k '    en: { login_not_tried_here: '\''This version' '    en0: { login_not_tried_here: '\''This version'
# WL14 (日文叫人開終端機;ebab8 03:34 Windows 先只支援 Claude)
k 'こちらは次の版で対応します'\'' },' 'ターミナルで先にログインしてください'\'' },'
# WL15 (四語表蓋過不認得的鍵(constructor 查到);ebab8 03:34 Windows 先只支援 Claude)
k 'if (typeof rs === '\''string'\'' && L && Object.prototype.hasOwnProperty.call(L, rs)) return L[rs];' 'if (L && L[rs]) return L[rs];'
# WL16 (中文表不守自己的鍵;ebab8 03:34 Windows 先只支援 Claude)
k '(typeof rs === '\''string'\'' && Object.prototype.hasOwnProperty.call(RELOG_WHY, rs) ? RELOG_WHY[rs] : '\'''\'')' 'RELOG_WHY[rs]'
