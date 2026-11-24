#!/usr/bin/env bash
# 刀名一律 PG 前綴(下面寫的是舊名):IM1 / IM2 / IM3 / IM4 / IM5 / IM6 / IM7 / IM8 / IM9 / IM10 / IM11 / IM12 / IM13 / IM14 / GH1 / DL1 / DL2 / GAP1 / TS1 / MX1(1a93b 11-23 輸入法;90856 照 pending KNIVES-ime-1a93b-014aa1c76d.tsv 逐把轉寫,f163c REVIEW M1)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGIM1
k '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);' '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || !!(e && e.timeStamp - IME.end < IME_GAP);'
# PGIM2
k '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);' '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < 0);'
# PGIM3
k '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);' '  const imeBusy = (e) => !!(e && (e.isComposing)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);'
# PGIM4
k '    if (imeHold(el)) return; // 組字中不換掉輸入框;組字結束後 imeWire 補畫' '    if (imeHold(el) && false) return; // 組字中不換掉輸入框;組字結束後 imeWire 補畫'
# PGIM5
k '    if (!imeOn() || !root.contains(document.activeElement)) return false;' '    if (!IME.on) return false;'
# PGIM6
k '    root.addEventListener('\''compositionstart'\'', () => { IME.on = true; IME.since = Date.now(); E2.at = 0; }, true);' '    root.addEventListener('\''compositionstart'\'', () => { IME.on = true; IME.since = Date.now(); }, true);'
# PGIM7
k '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };' '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };'
# PGIM8
k '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };' '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; } };'
# PGIM9
k '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };' '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; redraw(); } };'
# PGIM10
k '    root.addEventListener('\''focusout'\'', end, true);' '    root.addEventListener('\''focusout'\'', () => {}, true);'
# PGIM11
k '  imeWire(document, render);' '  // knife IM11'
# PGIM12
k '    if (e.target.id === '\''inp'\'' && e.key === '\''Enter'\'' && !imeBusy(e)) {' '    if (e.target.id === '\''inp'\'' && e.key === '\''Enter'\'' && !e.isComposing && e.keyCode !== 229) {'
# PGIM13
k '    if (e.target.id === '\''inp'\'' && !imeBusy(e) && !e.metaKey && !e.ctrlKey && !e.altKey) {' '    if (e.target.id === '\''inp'\'' && !e.isComposing && e.keyCode !== 229 && !e.metaKey && !e.ctrlKey && !e.altKey) {'
# PGIM14
k '    if (e.target.dataset && e.target.dataset.ansfor && e.key === '\''Enter'\'' && !imeBusy(e)) { e.preventDefault(); const b = (e.target.closest('\''.ans'\'') || el)' '    if (e.target.dataset && e.target.dataset.ansfor && e.key === '\''Enter'\'' && !e.isComposing) { e.preventDefault(); const b = (e.target.closest('\''.ans'\'') || el)'
# PGGH1
k '    if (e.target.matches && e.target.matches('\''[data-ghnum]'\'') && !imeBusy(e)) {' '    if (e.target.matches && e.target.matches('\''[data-ghnum]'\'') && !e.isComposing) {'
# PGDL1
k '        if (e.target.dataset && e.target.dataset.ansfor && e.key === '\''Enter'\'' && !imeBusy(e)) { e.preventDefault(); const b = e.target.closest('\''.ans'\'') && e.target.closest('\''.ans'\'').querySelector('\''[data-answer]'\''); if (b) b.click(); }' '        if (e.target.dataset && e.target.dataset.ansfor && e.key === '\''Enter'\'' && !e.isComposing) { e.preventDefault(); const b = e.target.closest('\''.ans'\'') && e.target.closest('\''.ans'\'').querySelector('\''[data-answer]'\''); if (b) b.click(); }'
# PGDL2
k '  imeWire(document, render);' '  imeWire(el, render);'
# PGGAP1
k '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, IME_GAP); } };' '    const end = (ev) => { if (!IME.on) return; IME.on = false; IME.end = ev.timeStamp; E2.at = 0; if (IME.held) { IME.held = false; setTimeout(redraw, 0); } };'
# PGTS1
k '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && e.timeStamp - IME.end < IME_GAP);' '  const imeBusy = (e) => !!(e && (e.isComposing || e.keyCode === 229)) || imeOn() || !!(e && Date.now() - IME.end < IME_GAP);'
# PGMX1
k '  const imeOn = () => IME.on && Date.now() - IME.since <= IME_MAX;' '  const imeOn = () => IME.on;'
