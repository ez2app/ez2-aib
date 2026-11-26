#!/usr/bin/env bash
# 「能改程式」開關(fc7f9 11-26;c2f0b ef615567db;3e53b PRE-READ-dgrs-switch D6 / D7;ebab8 21:51 客戶 Windows 回報):紅字後接按這裡打開(本人確認)、開著照實寫 + 關鈕。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, dg_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# DG1 開不走本人確認
k 'if (on === true) await fingerAct(DG_SET, { on: true }); else' 'if (on === true) await act(DG_SET, { on: true }); else'
# DG2 開多送欄位
k 'await fingerAct(DG_SET, { on: true });' 'await fingerAct(DG_SET, { on: true, by: '\''page'\'' });'
# DG3 關走本人確認
k 'else await act(DG_SET, { on: false });' 'else await fingerAct(DG_SET, { on: false });'
# DG4 紅字後不接打開鈕
k 'if (d.can_on === true && hasAct(DG_SET) === true) { // D7' 'if (false) { // D7'
# DG5 沒有 set 動作也出打開鈕
k 'if (d.can_on === true && hasAct(DG_SET) === true) { // D7' 'if (d.can_on === true) { // D7'
# DG6 環境變數開的也出關鈕
k 'const off = d.can_off === true && d.source !== '\''env'\'' && hasAct(DG_SET) === true;' 'const off = d.can_off === true && hasAct(DG_SET) === true;'
# DG7 開著不照實寫
k 'return `<div class="occ" role="status">這台已開「能改程式」。' 'return `<div class="occ" role="status">。'
# DG8 沒本人確認不照實寫
k 'return (d.owner_check === false ? `<div class="occ warn">${esc(DG_NOOC)}</div>` : '\'''\'') + msg;' 'return msg;'
# DG9 開成功不清開席紅字
k 'if (on === true && S.add) { S.add.dgNeed = false; S.add.msg = '\'''\''; S.add.err = false; }' 'if (false) { S.add.dgNeed = false; S.add.msg = '\'''\''; S.add.err = false; }'
# DG10 一次不只一個
k 'const D = S.dg; if (!D || D.busy || hasAct(DG_SET) !== true) return;' 'const D = S.dg; if (!D || hasAct(DG_SET) !== true) return;'
# DG11 開席被拒不記 dgNeed
k 'A.dgNeed = rgCode(e) === '\''dgrs_needs_switch'\'';' 'A.dgNeed = false;'
# DG12 owner_check_unavailable 不說人話
k 'D.msg = { bad: true, text: c === '\''owner_check_unavailable'\'' ? DG_NOOC :' 'D.msg = { bad: true, text: false ? DG_NOOC :'
# DG13 測試機 internal 仍只寫 internal
k 'msg: thErr(c) || (c === '\''internal'\'' && m && m !== c ? m : errText(e)), bad: true };' 'msg: thErr(c) || errText(e), bad: true };'
