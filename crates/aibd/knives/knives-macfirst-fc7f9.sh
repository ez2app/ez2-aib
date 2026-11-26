#!/usr/bin/env bash
# Mac 首次使用(fc7f9 11-26;c2f0b dc4e963b56 session-policy kind=unsigned + 20:01 定字):沒裝 / 不是官方簽章 ⇒ 有 provider_install 才給 AIB 自己裝;policy_exists 新字。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, sp_* / w10_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# MF1 unsigned 不在 SP_KINDS(整列不畫)
k ''\''not_found'\'', '\''unsigned'\'']; // c2f0b' ''\''not_found'\'']; // c2f0b'
# MF2 unsigned 有 provider_install 也不給重新安裝鈕
k '${inst ? `按「重新安裝」,AIB 會改裝官方版。' '${false ? `按「重新安裝」,AIB 會改裝官方版。'
# MF3 沒有 provider_install 也給鈕
k 'const inst = typeof lcHas === '\''function'\'' && lcHas('\''provider_install'\''), busy' 'const inst = true, busy'
# MF4 沒裝不指向安裝卡
k '${inst && typeof lcNeeds === '\''function'\'' && lcNeeds(f.name) ? '\''按下面的「同意並安裝」' '${false ? '\''按下面的「同意並安裝」'
# MF5 重新安裝送錯 body
k 'const body = { provider: k }; try {' 'const body = { provider: k, path: '\''x'\'' }; try {'
# MF6 重新安裝不限 unsigned
k 'if (!spFound(SP.d).some((f) => f.name === k && f.kind === '\''unsigned'\'')) return;' 'if (false) return;'
# MF7 一次不只一個
k 'if (!SP || SP.fix || typeof k !== '\''string'\''' 'if (!SP || typeof k !== '\''string'\'''
# MF8 點鈕沒接
k 'if (sf) { spFix(sf.dataset.spfix); return; } }' 'if (sf) { return; } }'
# MF9 重新安裝錯誤不寫在那列
k 'SP.fixErr[k] = lcWhy(rgCode(e), lcName(k)); }' 'SP.fixErr[k] = '\'''\''; }'
# MF10 policy_exists 回舊字
k 'policy_exists: '\''這台的開席安全設定已經設好了,AIB 不會改掉它。'\'',' 'policy_exists: '\''這台已經有開席設定,AIB 不會蓋掉它。'\'','
