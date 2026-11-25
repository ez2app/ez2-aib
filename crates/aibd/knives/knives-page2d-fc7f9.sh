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
k 'replace(new RegExp(AIB_UNSEEN, '\''gu'\''), '\'''\'').trim()).slice(0, 20).join' 'replace(/[\u0000-\u001f\u007f]/g, '\'''\'').trim()).slice(0, 20).join'
# PGNL1 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k 'o.id === '\''always'\'' && !/\d+\s*天/.test(String(o.words || '\'''\''))' 'false && !/\d+\s*天/.test(String(o.words || '\'''\''))'
# PGNL2 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k '&& !/\d+\s*天/.test(String(o.words || '\'''\'')) ? '\''(每 '\''' '&& true ? '\''(每 '\'''
# PGNL3 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k 'n <= 3650 ? n : 30; };' 'n <= 3650 ? n : 90; };'
# PGNL4 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k 'return Number.isInteger(n) && n > 0 && n <= 3650' 'return false && n > 0 && n <= 3650'
# PGNL5 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k ' && String(x.state || '\'''\'').toLowerCase() !== '\''archived'\'').length : 0);' ').length : 0);'
# PGNL6 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k '.concat(b.choice === '\''always'\'' ? [['\''影響'\'', netAlwaysWho(c), true]] : [])]; },' ']; },'
# PGNL7 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k 'b.choice === '\''always'\'' ? [['\''影響'\'', netAlwaysWho(c), true]]' 'true ? [['\''影響'\'', netAlwaysWho(c), true]]'
# PGNL8 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k '>${esc(netAlwaysWords(o, c))}${o.needs_owner ? '\'' · 要驗證'\'' : '\'''\''}</button>' '>${esc(o.words)}${o.needs_owner ? '\'' · 要驗證'\'' : '\'''\''}</button>'
# PGNL9 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k '${x.expires_at ? ` <span class="apn">到' '${false ? ` <span class="apn">到'
# PGNL10 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k '到 ${esc(fpWhen(x.expires_at))} 要再確認' '到 ${fpWhen(x.expires_at)} 要再確認'
# PGNL11 (ebab8 DECISION-netallow-store:一直允許綁人格、每 30 天再確認、清單到期日)
k ''\''綁人格「'\'' + String(c.persona_name || c.persona_uid || '\''這個人格'\'')' ''\''綁人格「'\'' + String(c.persona_uid || '\''這個人格'\'')'
# PGUS1 (aibd is_bad_char 同一張表:少了 invisible_extra 那幾個就紅)
k '\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u2800\u3164\ufe00-\ufe0f\uffa0\u{e0100}-\u{e01ef}]' '\u034f]'
# PGUS2 (aibd 人格卡段落放行全形空白 U+3000:頁面預檢也要放行)
k 'test(t.replace(/[\p{Cc}\u3000]/gu, '\'''\'')))' 'test(t.replace(/[\p{Cc}]/gu, '\'''\'')))'
