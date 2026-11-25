#!/usr/bin/env bash
# W-10 開席設定引導卡(fc7f9 11-25,ebab8 派;c2f0b IFACE-w10-session-policy r2)。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, W-10 block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# W10K1
k 'npm 版其實是用 Node 執行的腳本,檔案任何程式都改得到,AIB 沒辦法確定開的是不是真的' 'npm 版不安全,AIB 沒辦法確定開的是不是真的'
# W10K2
k 'canW = d.can_write === true && F.some((f) => f.kind === '\''native'\'');' 'canW = d.can_write === true;'
# W10K3
k 'const SP_INSTALL_NA = '\''官方安裝說明的連結:這一版 AIB 還沒有'\'';' 'const SP_INSTALL_NA = '\''官方安裝說明:https://example.com/install'\'';'
# W10K4
k 'f && typeof f.name === '\''string'\'' && f.name && SP_KINDS.includes(f.kind)' 'f && typeof f.name === '\''string'\'' && f.name'
# W10K5
k 'd.policy === '\''missing'\'' || d.policy === '\''invalid'\'' || ' 'd.policy === '\''missing'\'' || '
# W10K6
k 'const r = await fingerAct(SP_INIT, {});' 'const r = await fingerAct(SP_INIT, { programs: spFound(SP.d).map((f) => f.name) });'
# W10K7
k '${S.sp && S.sp.d && spNeed(S.sp.d) ? spCard(S.sp) : `' '${false ? spCard(S.sp) : `'
# W10K8
k '      spLoad(); // W-10' '      // W-10'
# W10K9
k 'policy_exists: '\''這台已經有開席設定,AIB 不覆蓋。'\'',' 'policy_exists: '\''已有設定。'\'','
# W10K10
k 'const n = esc(spName(f.name)), pt =' 'const n = spName(f.name), pt ='
# W10K11
k 'return a.length > 5 && p.length > 48 ? a.slice(0, 3).join(sep) + sep + '\''…'\'' + sep + a[a.length - 1] : p;' 'return p;'
# W10K12
k 'return `<li class="spok">✅ ${n}:找到了' 'return `<li class="spok">✅ ${n}:可以用'
# W10K13
k 'if (S.add && x.closest('\''[data-spok]'\'')) { spInit(); return; }' 'if (S.add && x.closest('\''[data-spok]'\'')) { return; }'
# W10K14
k 'const why = (held && held.block) || npm || (!p ?' 'const why = (held && held.block) || (!p ?'
# W10K15
k 'map((f) => spName(f.name) + '\''('\'' + f.path + '\'')'\'').join('\''、'\'')' 'map((f) => f.path + '\'' '\'' + spName(f.name)).join('\''、'\'')'
# W10K16
k 'policy_bad_request: '\''頁面送出的設定請求格式不對,這次沒做。'\'',' 'policy_bad_request: '\''格式不對。'\'','
# W10K17
k 'spFound(S.sp && S.sp.d).some((f) => f.name === k && f.kind === '\''npm'\'') ? SP_NPM_WHY(spName(k)) : '\'''\''' ''\'''\'''
