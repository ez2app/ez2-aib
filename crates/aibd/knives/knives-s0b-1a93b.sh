#!/usr/bin/env bash
# 刀名一律 PG 前綴(下面寫的是舊名):S01 / S02 / S03 / S04 / S05 / S06 / S07 / S08 / S09 / S10 / S11 / S12 / S13 / S14 / S61 / S62 / S63 / S64 / S65 / S66 / S67(1a93b 11-23 加一席S0b;90856 照 pending KNIVES-s0b-addseat-1a93b-5b53ced4a0.tsv 逐把轉寫,f163c REVIEW M1)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGS01
k '      const ap = x.closest('\''[data-addp]'\''); if (ap) { S.add.provider = ap.dataset.addp; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }' '      const ap = x.closest('\''[data-addp]'\''); if (ap) { S.add.provider = ap.dataset.addp; S.add.mode = '\''safe'\''; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }'
# PGS02
k '    if (!A.persona && !A.personaTried && (A.personasIn || (S.personaList && S.personaList.length))) { A.personaTried = true; let last = '\'''\''; try { last = localStorage.getItem('\''ws.lastPersona'\'') || '\'''\''; } catch (e) { /* 沒有就不選 */ } if (last && PS.some(([u]) => u === last)) { A.persona = last; brainPreselect(A); } }' '    if (!A.persona && !A.personaTried && true) { A.personaTried = true; let last = '\'''\''; try { last = localStorage.getItem('\''ws.lastPersona'\'') || '\'''\''; } catch (e) { /* 沒有就不選 */ } if (last && PS.some(([u]) => u === last)) { A.persona = last; brainPreselect(A); } }'
# PGS03
k '      if (S.add) { S.add.personasIn = true; render(); } // 拿到或拿不到都算回來了,這時才預選上次的人格' '      if (S.add) { render(); } // knife S03'
# PGS04
k '    const NO = nodeOpts(); const node = A.node || '\'''\''; // S0-3:不默默帶左樹選中的那項,要用戶自己選' '    const NO = nodeOpts(); const node = A.node !== undefined ? A.node : S.sel || '\'''\''; // S0-3:不默默帶左樹選中的那項,要用戶自己選'
# PGS05
k '      const node = A.node || '\'''\'';' '      const node = A.node !== undefined ? A.node : S.sel;'
# PGS06
k '      if (seat.seat_id && seatById(seat.seat_id)) { S.mode = seat.seat_id; S.atOpen = false; render(); } // 不走 setMode:它會清掉上面那句「開了 @… / 在排隊…」' '      // knife S06'
# PGS07
k '    if (addNoRepo(A) && !A.noRepoAsk) { A.noRepoAsk = true; return render(); } // S0-5:第一下只出提醒,再按一次才開' '    // knife S07'
# PGS08
k '  function addNoRepo(A) { return !!A && addRepoSec(A) && addRepos().length > 0 && !curRepo(A) && !(A.mode === '\''work'\'' && A.withSeat); }' '  function addNoRepo(A) { return !!A && addRepoSec(A) && addRepos().length > 0 && !curRepo(A); }'
# PGS09
k '  function addNoRepo(A) { return !!A && addRepoSec(A) && addRepos().length > 0 && !curRepo(A) && !(A.mode === '\''work'\'' && A.withSeat); }' '  function addNoRepo(A) { return !!A && addRepoSec(A) && !curRepo(A) && !(A.mode === '\''work'\'' && A.withSeat); }'
# PGS10
k '  function addRepoSec(A) { return (A.mode === '\''dgrs'\'' && !inPreview()) || (A.mode === '\''work'\'' && repoModes().includes('\''work'\'') && A.provider !== '\''codex'\''); }' '  function addRepoSec(A) { return (A.mode === '\''dgrs'\'' && !inPreview()) || (A.mode === '\''work'\'' && repoModes().includes('\''work'\'')); }'
# PGS11
k '${addNoRepo(A) ? `<span class="anorepo" role="alert">' '${false ? `<span class="anorepo" role="alert">'
# PGS12
k '    if (A.repo === undefined && addRepoSec(A) && addRepos().length === 1) A.repo = addRepos()[0]; // S0-5:只登記一個倉時預設選它' '    // knife S12'
# PGS13
k '      const rp = x.closest('\''[data-addrepo]'\''); if (rp) { S.add.repo = rp.dataset.addrepo; S.add.noRepoAsk = false; S.add.key = idem(); return render(); }' '      const rp = x.closest('\''[data-addrepo]'\''); if (rp) { S.add.repo = rp.dataset.addrepo; S.add.key = idem(); return render(); }'
# PGS14
k '      if (seat.seat_id && seatById(seat.seat_id)) { S.mode = seat.seat_id; S.atOpen = false; render(); } // 不走 setMode:它會清掉上面那句「開了 @… / 在排隊…」' '      if (seat.seat_id && seatById(seat.seat_id)) setMode(seat.seat_id); // 不走 setMode:它會清掉上面那句「開了 @… / 在排隊…」'
# PGS61
k '  const repoNameNorm = (v) => String(v || '\'''\'').replace(REPO_DASH, '\''-'\'').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, '\'' '\'').toLowerCase();' '  const repoNameNorm = (v) => String(v || '\'''\'').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, '\'' '\'').toLowerCase();'
# PGS62
k '  const repoNameNorm = (v) => String(v || '\'''\'').replace(REPO_DASH, '\''-'\'').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, '\'' '\'').toLowerCase();' '  const repoNameNorm = (v) => String(v || '\'''\'').replace(REPO_DASH, '\''-'\'').replace(/\u3000/g, '\'' '\'').toLowerCase();'
# PGS63
k '  const repoNameNorm = (v) => String(v || '\'''\'').replace(REPO_DASH, '\''-'\'').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, '\'' '\'').toLowerCase();' '  const repoNameNorm = (v) => String(v || '\'''\'').replace(REPO_DASH, '\''-'\'').replace(/[\uFF01-\uFF5E]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/\u3000/g, '\'' '\'');'
# PGS64
k '  const REPO_DASH = /[\uFF0D\u2010\u2011\u2012\u2013\u2014\u2212]/g;' '  const REPO_DASH = /[\uFF0D\u2010\u2011\u2012\u2013\u2014]/g;'
# PGS65
k '    return i < 0 ? '\'''\'' : '\''第 '\'' + (i + 1) + '\'' 個字「'\'' + C[i] + '\''」'\'' + (/[\s]/.test(C[i]) ? '\''是空白,'\'' : '\'''\'') + '\''不能用'\'';' '    return i < 0 ? '\'''\'' : '\''名字不對'\'';'
# PGS66
k '    const name = repoNameNorm(F.name).trim(), path = String(F.path || '\'''\'').trim();' '    const name = String(F.name || '\'''\'').trim(), path = String(F.path || '\'''\'').trim();'
# PGS67
k '      if (e.target.dataset.addrf === '\''name'\'' && !e.isComposing) { const t = e.target, v = repoNameNorm(t.value); if (v !== t.value) { const a = t.selectionStart, b = t.selectionEnd; t.value = v; try { t.setSelectionRange(a, b); } catch (er) { /* 不給設就算了 */ } } }' '      if (false) { /* knife S67 */ }'
