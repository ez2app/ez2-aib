#!/usr/bin/env bash
# AR / MK17 / NL (fc7f9 11-23 r6h–r6j: 已封存區 + ↺ 還原、合併名去隱形字元、一直允許綁人格;疊上 main 11-25).
# Own script from the start: page2 / page2a / page2b / page2c are full at 71–72 knives (one script must finish inside the per-script watchdog).
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs §37 §38 §39) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# PGAR1 (08de5 A1 已封存區 + ↺ 還原)
k '.filter((x) => x && x.uid).slice(0, 100).map((x) => [String(x.uid), String(x.display_name || x.name || x.uid)]);' '.slice(0, 100).map((x) => [String(x.uid), String(x.display_name || x.name || x.uid)]);'
# PGAR2 (08de5 A1 已封存區 + ↺ 還原)
k 'if (!L.length || !rgHas(kind, '\''restore'\'')) return '\'''\'';' 'if (!L.length) return '\'''\'';'
# PGAR3 (08de5 A1 已封存區 + ↺ 還原)
k '${open ? L.map(([u, nm]) =>' '${true ? L.map(([u, nm]) =>'
# PGAR4 (08de5 A1 已封存區 + ↺ 還原)
k '<b>${esc(nm)}</b> <i>${esc(u.slice(-6))}</i>' '<b>${nm}</b> <i>${esc(u.slice(-6))}</i>'
# PGAR5 (08de5 A1 已封存區 + ↺ 還原)
k 'if (!it || !rgHas(kind, '\''restore'\'') || S.rgArchBusy) return;' 'if (!it) return;'
# PGAR6 (08de5 A1 已封存區 + ↺ 還原)
k 'const r = await fingerAct(RG_ACTS[kind].restore, { uid });' 'const r = await fingerAct(RG_ACTS[kind].restore, { uid, kind });'
# PGAR7 (08de5 A1 已封存區 + ↺ 還原)
k 'S.arch[kind] = L.filter(([u]) => u !== uid); // 從封存區拿掉' '// 從封存區拿掉'
# PGAR8 (08de5 A1 已封存區 + ↺ 還原)
k 'if (kind === '\''persona'\'') S.personaList = personas().filter(([u]) => u !== uid).concat([[uid, it[1]]]); else loadRegistry();' 'if (kind === '\''persona'\'') S.personaList = personas(); else loadRegistry();'
# PGAR9 (08de5 A1 已封存區 + ↺ 還原)
k 'if (rgCode(e) === '\''not_archived'\'') S.arch[kind] = L.filter(([u]) => u !== uid); }' '}'
# PGAR10 (08de5 A1 已封存區 + ↺ 還原)
k 'if (!rgOk(r)) throw Object.assign(new Error('\''not_done'\''), { code: '\''not_done'\'', arch: true });' ' '
# PGAR11 (08de5 A1 已封存區 + ↺ 還原)
k '    S.arch = Object.assign(S.arch || {}, { role: rgArchOf(r), brain: rgArchOf(b) });' ' '
# PGAR12 (08de5 A1 已封存區 + ↺ 還原)
k ' not_archived: '\''它已經不在封存區了'\'',' ' '
# PGAR13 (08de5 A1 已封存區 + ↺ 還原)
k '${S.rgArchBusy ? '\''disabled'\'' : '\'''\''}>↺ 還原</button>' '>↺ 還原</button>'
# PGAR14 (08de5 A1 已封存區 + ↺ 還原)
k ',[data-rgao],[data-rgrs]'\'') && await rgClick(x)) return;' ''\'') && await rgClick(x)) return;'
# PGMK17 (f163c r6 N1 合併名稱去零寬 / 方向字元)
k 'replace(/[\u0000-\u001f\u007f\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g, '\'''\'').trim()).slice(0, 20)' 'replace(/[\u0000-\u001f\u007f]/g, '\'''\'').trim()).slice(0, 20)'
