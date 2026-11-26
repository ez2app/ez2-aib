#!/usr/bin/env bash
# Windows 開席流程頁面字(fc7f9 11-26,ebab8 12:12 / Andy 12:1x 徹查):Windows 上本人確認寫 Windows Hello、不寫指紋;persona_add / brain_add 沒接 ⇒ 照實「這一版還不能從頁面新增」。
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, ebab8 12:12 block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# CW1 Windows 也照寫指紋
k 'return o ? /^win/.test(o) :' 'return o ? false :'
# CW2 「按一次指紋」沒換成通順的句子
k '['\''按一次指紋'\'', '\''用 Windows Hello 確認一次'\''], ' ''
# CW3 錯誤總出口沒換平台字
k 'function errText(e) { return (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(errText0(e)); }' 'function errText(e) { return errText0(e); }'
# CW4 新增人格鈕照出、按了才被拒
k 'persona: false, brain: false, role: true }' 'persona: true, brain: false, role: true }'
# CW5 新增被拒仍寫要本人指紋
k '(F.kind === '\''persona'\'' || F.kind === '\''brain'\'' ? regNotYet(F.kind) :' '(false ? regNotYet(F.kind) :'
# CW6 Mac 也被改字
k '!t.includes('\''指紋'\'') || !fpWin()) return t;' '!t.includes('\''指紋'\'')) return t;'
# CW7 沒有 version 時不看 thermal 的平台
k '(S.version.capabilities && S.version.capabilities.os))) || (S.thermal && S.thermal.os) || '\'''\'').toLowerCase(); return o ?' '(S.version.capabilities && S.version.capabilities.os))) || '\'''\'').toLowerCase(); return o ?'
# CW8 登記倉錯誤沒換平台字
k 'return (typeof fpSay === '\''function'\'' ? fpSay : (t) => t)(gateWhy(e)' 'return ((t) => t)(gateWhy(e)'
# CW9 第二大腦候選仍寫按指紋
k '這一版還不能從頁面登記成第二大腦;先不綁也能開席。' '登記要在這台按指紋,這台還不能按。先不綁也能開席。'
# CW10 builtin 旗標沒讀(內建人格當一般卡)
k 'S.personaBuiltin = P.filter((p) => p.builtin === true)' 'S.personaBuiltin = P.filter((p) => false)'
# CW11 只有內建一張時沒自動選
k 'personaBuiltin(S.personaList[0][0])) A.persona' 'false) A.persona'
# CW12 內建人格照出改 / 刪鈕
k '`, personaBuiltin(u))).join('\'''\'')' '`, false)).join('\'''\'')'
# CW13 內建人格英文名字缺
k 'en: ['\''AIB Partner'\'', '\''Built-in'\'']' 'en0: ['\''AIB Partner'\'', '\''Built-in'\'']'
# CW14 新增鈕被藏掉(ebab8 12:14 要留)
k 'if (!REG_ADD_OPEN[kind]) return `<button type="button" class="anew" disabled' 'if (!REG_ADD_OPEN[kind]) return `<div class="anew" disabled'
# CW15 人格卡讀不到仍寫舊句
k '這台的人格卡檔讀不到(檔壞了或沒有權限),開不了任何席' '這台讀不到人格卡,開不了任何席'
# CW16 方案人格找不到不落到內建(ebab8 / f163c 12:16)
k 'if (b) { A.persona = b[0]; A.pkMiss = '\'''\''; }' 'if (false) { A.persona = b[0]; A.pkMiss = '\'''\''; }'
# CW17 人格列又印出方案的人格名(客戶看到「找不到「唐僧」」)
k '(A.pkMiss ? '\''這台沒有這個方案配的人格,請自己選'\'' :' '(A.pkMiss ? '\''找不到「'\'' + A.pkMiss + '\''」,請自己選'\'' :'
# CW18 方案標籤不說會用內建
k ': personas().some(([u]) => personaBuiltin(u)) ? ` · 用' ': false ? ` · 用'
# CW19 找不到時把人格名存下來
k 'A.persona = '\'''\''; A.pkMiss = '\''1'\''; }' 'A.persona = '\'''\''; A.pkMiss = p.pk; }'
# CW20 新機打開沒選好方案(ebab8 12:20 一鍵)
k 'A.firstDone = true; apPick(A, AP_FIRST_PLAN);' 'A.firstDone = true;'
# CW21 已有席也被蓋成預設
k '|| (S.seats || []).some((s) => s && !isArch(s))) return false;' ') return false;'
# CW22 有別的人格也被蓋成預設
k 'if (!(L.length === 1 && personaBuiltin(L[0][0]))' 'if (!(L.length >= 1)'
# CW23 預設品牌沒選 Claude
k 'if (cl) A.provider = '\''claude'\'';' 'if (cl) A.provider = '\'''\'';'
# CW24 開始用 Claude 只開面板不開席
k '{ A.autoGo = false; doAdd(); }' '{ A.autoGo = false; }'
# CW25 空狀態沒有開始用 Claude 鈕
k '<button type="button" class="k g startc" data-startclaude="1"' '<button type="button" class="k g startc" data-startclaudeX="1"'
# CW26 只有一席時沒 @ 仍送不出
k 'if (!T.length && !(targets.amb || []).length && soleSeat()) T = [soleSeat()];' 'if (false) T = [soleSeat()];'
