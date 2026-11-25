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
k 'if (again) return doAdd(); }' 'if (again) return render(); }'
# WL5 登入完不重抓 version
k 'S.version = await soft(get('\''/api/v1/version'\'')).catch(() => S.version); // 08de5 定案 4' '// 08de5 定案 4'
# WL6 Windows 沒驗過又叫人去終端機
k 'login_not_tried_here: '\''AIB 在這台(Windows)還不能從頁面登入這一家(先做 Claude)'\'',' 'login_not_tried_here: '\''請在這台的終端機先登入它'\'','
# WL7 品牌原因字改回只寫沒登入
k 'not_logged_in: '\''這台的登入沒有或已過期'\'' };' 'not_logged_in: '\''這台還沒登入'\'' };'
