#!/usr/bin/env bash
# 新用戶指引頁面(fc7f9 11-26;SPEC-onboarding-projects-ebab8-1126 r6、IFACE-guide-6770f-1126 r4b、3e53b DECISION-onboarding-bounds r4、f163c PREREAD 1–16)
# Each, broken, turns the_page_is_checked_without_a_browser (apps/web/test/ws-static.test.cjs, guide_* block) red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
kc() { python3 -I scripts/knife.py apps/web/dist/ws.css "$1" "$2" \
  --expect-fail the_page_is_checked_without_a_browser \
  -- cargo test -p aibd --locked --test page_static the_page_is_checked_without_a_browser; }
# GD1 走完了面板還是暗(新手期不看 aibd 的 panel_lit)
k 'const guideNew = (G) => !!(G && gProj(G, '\''start'\'') && G.panel_lit === false);' 'const guideNew = (G) => !!(G && gProj(G, '\''start'\''));'
# GD2 判不出(done null)也打勾
k 'return s.done === true ? '\''ok'\'' : s.done === null ? '\''unsure'\'' : '\''bad'\'';' 'return s.done === true || s.done === null ? '\''ok'\'' : '\''bad'\'';'
# GD3 配對前鎖著的步驟不標鎖
k 'if (s.why === '\''locked'\'' || !Object.prototype.hasOwnProperty.call(s, '\''done'\'')) return '\''lock'\'';' 'if (false) return '\''lock'\'';'
# GD4 下一版的步驟不標下一版
k 'if (s.why === '\''next_version'\'') return '\''later'\'';' 'if (false) return '\''later'\'';'
# GD5 每個沒完成的步驟都出鈕(不是一次一顆)
k 'const right = on && gOwn(GUIDE_GO, na) ?' 'const right = st === '\''bad'\'' && gOwn(GUIDE_GO, na) ?'
# GD6 next_act 不只當表的鍵(繼承來的名字也出鈕)
k 'const gOwn = (o, k) => !!o && typeof k === '\''string'\'' && Object.prototype.hasOwnProperty.call(o, k);' 'const gOwn = (o, k) => !!o && typeof k === '\''string'\'' && k in o;'
# GD7 新手期面板什麼都沒調暗
k '(GUIDE_LIT[a] === v ? m :' '(true ? m :'
# GD8 暗格上面沒有那一句提示
k '.replace(/(<div class="apsrc">[^<]*<\/div>)/, `$1<div class="apn gdimh">🔒 ${t}</div>`);' '.replace(/^$/, '\'''\'');'
# GD9 暗格沒標 aria-disabled
k 'class="${c} gdim" aria-disabled="true" title="${t}"' 'class="${c} gdim" title="${t}"'
# GD10 專案一沒完成專案二也亮
k 'if (id === '\''hosts'\'' && P.lit !== true) { out.push(' 'if (false) { out.push('
# GD11 走完了不收成一行
k 'const open = gOwn(O, id) ? O[id] : !all' 'const open = gOwn(O, id) ? O[id] : true'
# GD12 終點挑到 disabled 的鈕
k 'if (h && h.ok && !h.disabled && (!dlg || h.inDlg)) pick = s;' 'if (h && h.ok && (!dlg || h.inDlg)) pick = s;'
# GD13 面板開著還挑面板外面的終點
k 'if (h && h.ok && !h.disabled && (!dlg || h.inDlg)) pick = s;' 'if (h && h.ok && !h.disabled) pick = s;'
# GD14 錯誤說明出現路線層不拆
k 'if (doc.querySelector('\''.pgbg,.anndlg,[role="alertdialog"],[role="alert"]'\'')) return true;' 'if (doc.querySelector('\''.pgbg,.anndlg,[role="alertdialog"]'\'')) return true;'
# GD15 有別的對話框疊在「+ AI 夥伴」上路線層不拆
k 'return D.length > 1 || (D.length === 1 && !D[0].querySelector('\''.adlg.ap'\''));' 'return D.length === 1 && !D[0].querySelector('\''.adlg.ap'\'');'
# GD16 蓋暗那層沒挖洞(底下那顆點不到)
k 'holes.map((o) => `M${o.x} ${o.y}h${o.w}v${o.h}h${-o.w}Z`)' 'holes.map((o) => '\'''\'')'
# GD17 第 6 步同時按兩次送兩次
k 'if (S.gTourSent || hasAct('\''guide_done'\'') !== true) return;' 'if (hasAct('\''guide_done'\'') !== true) return;'
# GD18 aibd 沒有 guide_done 也送
k 'if (S.gTourSent || hasAct('\''guide_done'\'') !== true) return;' 'if (S.gTourSent) return;'
# GD19 409 guide_not_ready 當成錯誤
k 'catch (e) { if (!(e && e.status === 409)) {' 'catch (e) { if (true) {'
# GD20 不是第 6 步打開面板也記走完
k 'if (!c || c.step.id !== '\''tour'\'') { S.gTourLine = false; return; }' 'if (!c) { S.gTourLine = false; return; }'
# GD21 guide_done 帶用戶端時間
k 'const r = await act('\''guide_done'\'');' 'const r = await act('\''guide_done'\'', { at: Date.now() });'
# GD22 略過沒存成本機偏好
k 'if (S.gSkip) localStorage.setItem(GUIDE_SKIP, '\''1'\'');' 'if (S.gSkip) localStorage.setItem(GUIDE_SKIP, '\''0'\'');'
# GD23 略過之後沒有「再看一次」
k 'again = gSkipped() && cur && cur.proj === id;' 'again = false;'
# GD24 暗格點了照樣動作
k 'if (x.closest('\''.gdim'\'')) return; // 新手期暗格' 'if (false) return; // 新手期暗格'
# GD25 打開「+ AI 夥伴」不記第 6 步
k 'guideOnPanel();' 'void 0;'
# GD26 畫完不重算路線層
k '    guidePaintSoon(); // 新用戶指引路線層' '    void 0; // 新用戶指引路線層'
# GD27 不讀 guide
k 'if (!S.gAt || Date.now() - S.gAt > 8000) await guideLoad();' 'if (false) await guideLoad();'
# GD28 面板沒套暗格
k '${S.add ? guideDim(addHtml()) : '\'''\''}' '${S.add ? addHtml() : '\'''\''}'
# GD29 配對頁沒有步驟清單
k '${guidePreHtml()}${pairHtml()}' '${pairHtml()}'
# GD30 不該畫時路線層留著(只是沒更新)
k 'if (!R || !R.to) { if (old) old.remove(); return; }' 'if (!R || !R.to) { return; }'
# GD31 走完且不是專案二剛亮仍建路線層
k 'if (!(guideNew(G) || (S.gTourHere && cur.proj === '\''hosts'\''))) return null;' 'if (false) return null;'
# GD32 有本人確認 / 錯誤時仍畫路線
k 'if (!gOwn(GUIDE_GO, na) || guideBlocked(document)) return null;' 'if (!gOwn(GUIDE_GO, na)) return null;'
# GD33 Esc 不能略過
k 'if (e.key === '\''Escape'\'' && document.getElementById('\''gtour'\'') && !(typeof imeBusy === '\''function'\'' && imeBusy(e))) guideSkip(true);' 'if (false) guideSkip(true);'
# GD34 Windows 不寫 Windows Hello
k 'gW('\''hello'\'')[typeof fpWin === '\''function'\'' && fpWin() ? 0 : 1]' 'gW('\''hello'\'')[1]'
# GD35 不是 Windows 也出設 PIN 說明
k 'const pin = win && typeof ocW === '\''function'\'' ?' 'const pin = typeof ocW === '\''function'\'' ?'
# GD36 終點指到核准並啟用
k '    session_policy_init: { hello: true, to: ['\''[data-add="1"]'\''] },' '    session_policy_init: { hello: true, to: ['\''[data-spok="1"]'\''] },'
# GD42 終點指到安裝(lcgo,會跳本人確認)
k '    provider_install: { hello: true, to: ['\''[data-add="1"]'\''] },' '    provider_install: { hello: true, to: ['\''[data-lcgo="claude"]'\''] },'
# GD43 終點指到重新登入(relogin,會跳本人確認)
k '    provider_relogin: { hello: true, to: ['\''[data-add="1"]'\''] },' '    provider_relogin: { hello: true, to: ['\''[data-relogin="claude"]'\''] },'
# GD44 終點指到加測試機(thadd,會跳本人確認)
k '    add_host: { to: ['\''[data-thopen="1"]'\''] },' '    add_host: { to: ['\''[data-thadd="x"]'\''] },'
# GD37 韓文少一個步驟字
k 'host_handoff: '\''파트너에게 관리 맡기기'\'', site_first: '\''첫 사이트'\'' },' 'host_handoff: '\''파트너에게 관리 맡기기'\'' },'
# GD38 剛走完的那一句沒放進面板
k '<div class="apsrc">${esc(apSrc())}</div>${guideTourLine()}' '<div class="apsrc">${esc(apSrc())}</div>'
# GD39 左欄專案卡沒有內建專案
k '<b>專案</b><small>唯讀</small></div>${guideHtml()}<div class="treebox">' '<b>專案</b><small>唯讀</small></div><div class="treebox">'
# GD40 一顆鈕沒標要本人確認
k '    start_claude: { hello: true, to:' '    start_claude: { to:'
# GD41 連不上 AIB / 讀取出錯時遮罩還蓋著
k 'const gDown = () => !!(S.banner && S.bannerKind === '\''err'\'');' 'const gDown = () => false;'
# GD45 面板的 ✕ / 取消被遮罩蓋住
k 'const guideExits = (doc) => Array.from(doc.querySelectorAll('\''.adlg.ap [data-addx]'\''));' 'const guideExits = (doc) => [];'
# GD46 點到遮罩時確認卡已在畫面上也不拆
k 'if (guideBlocked(document)) d.remove(); });' '});'
# GD47 不經 render 冒出的確認卡不重判
k 'ms.some((m) => !(m.target.closest && m.target.closest('\''#gtour'\'')))) guidePaintSoon(); })' 'false) guidePaintSoon(); })'
# GD48 配對前 401 不畫 1、2 步(f163c b0abe2097a N1)
k '.catch((e) => (e && e.status === 401 && S.pair ? GUIDE_UNPAIRED : null));' '.catch(() => null);'
# GC1 路線層整片擋點擊
kc '.gtour{position:fixed;inset:0;z-index:60;pointer-events:none}' '.gtour{position:fixed;inset:0;z-index:60}'
# GC2 路線那條線擋點擊
kc 'stroke-dasharray:8 7;pointer-events:none;' 'stroke-dasharray:8 7;'
# GC3 減少動態還在動
kc '@media (prefers-reduced-motion:reduce){.gtour .gline,.gtour .gring{animation:none}}' ''
# GC4 蓋暗的地方點得穿(擋不住不必要的點擊)
kc 'fill:rgba(0,0,0,.55);pointer-events:visiblePainted}' 'fill:rgba(0,0,0,.55)}'
# GD49 開出席後第 5 步路線又指去「+ AI 夥伴」(真 AIB 實看 B1)
k 'const gNa = (na) => (na === '\''start_claude'\'' && typeof present === '\''function'\'' && present().length > 0 ? '\''focus_chat'\'' : na);' 'const gNa = (na) => na;'
# GC5 第 5 步的鈕又擠在同一行、步驟名變「跟…」(B2)
kc '.gstep.gcur .gact{grid-column:1/-1;' '.gstep.gcur .gact{'
# GC6 略過鈕回到右上、蓋住待審區(B4)
kc '.gskip{position:absolute;bottom:16px;right:16px;' '.gskip{position:absolute;top:12px;right:12px;'
# GD50 後端回 why=seat_open 時中文那行空白(6770f 14:00)
k ', seat_open: '\''夥伴開好了,到下面打一句話'\''' ''
# GD51 客戶走「加一台主機」時測試機那行又是開發字(B5)
k 'return !!(c && c.proj === '\''hosts'\'' && c.step && c.step.id === '\''host_add'\'');' 'return false;'
# GD52 指引鈕又寫「打開測試機」(B5)
k 'add_host: '\''加一台主機'\''' 'add_host: '\''打開測試機'\'''
# GD53 Windows 配對頁又叫人開終端機(B6)
k 'if (typeof t !== '\''string'\'' || !(typeof fpWin === '\''function'\'' && fpWin())) return t; let o = t; for (const [a, b] of PAIR_WIN_SAY)' 'if (true) return t; let o = t; for (const [a, b] of PAIR_WIN_SAY)'
# GD54 配對表單沒經 pairSay(B6)
k 'return pairSay(`<form class="pairbox" id="pairf"' 'return (`<form class="pairbox" id="pairf"'
# GC7 測試機那行的鈕又被切邊(B5)
kc '.thh{flex-wrap:wrap}' '.thh{}'
# GD55 Windows 等確認頁又說「AIB 的視窗按確定」,沒說在 Hello 對碼(3e53b 1a7f49dea0 L1)
k 'Windows Hello 視窗會寫同一組碼,一樣才輸入 PIN;不一樣按取消。' 'AIB 的視窗會顯示同一組短碼,一樣就在那裡按確定。'
# GD60 第 5 步蓋層只挖 #inp、送出鈕被吞(客戶 11-26)
k '    focus_chat: { to: ['\''#inp'\''], hole: '\''.send'\'' },' '    focus_chat: { to: ['\''#inp'\''] },'
# GD61 路線的洞不用整個輸入列
k 'const tr = (R.ring || R.to).getBoundingClientRect()' 'const tr = R.to.getBoundingClientRect()'
# GD62 關指引沒把輸入框的字存回草稿(客戶 11-26)
k 'if (i && !i.disabled && S.drafts) S.drafts[S.mode] = i.value;' 'if (false) S.drafts[S.mode] = i.value;'
# GD63 組字中按 Esc 也關指引(連組字一起丟)
k '&& !(typeof imeBusy === '\''function'\'' && imeBusy(e))) guideSkip(true);' ') guideSkip(true);'
# GD56 終端機確認通道也換成 Windows Hello 字(f163c 1a7f49de N1)
k 'const pairWaitSay = (chan) => (chan === '\''terminal-unverified'\'' ? (t) => t : ' 'const pairWaitSay = (chan) => (false ? (t) => t : '
# GD57 等確認狀態沒記 channel
k 'chan: typeof st.channel === '\''string'\'' ? st.channel : '\'''\'' }' 'chan: '\'''\'' }'
# GD64 Mac Touch ID 通道等確認框仍叫人到終端機按 y(3e53b ea75d93f24 N1)
k 'chan === '\'''\'' ? (t) => t.split(PAIR_WAIT_TERM).join(PAIR_WAIT_MAC) : (t) => t);' 'chan === '\'''\'' ? (t) => t : (t) => t);'
# GD65 channel 還沒讀到也換成 Touch ID 字
k 'chan === '\'''\'' ? (t) => t.split(PAIR_WAIT_TERM).join(PAIR_WAIT_MAC) : (t) => t);' 'chan !== '\''x'\'' ? (t) => t.split(PAIR_WAIT_TERM).join(PAIR_WAIT_MAC) : (t) => t);'
# GD66 imeBusy 不認 keyCode 229(組字中 Esc 會關指引;f163c aa7164f7ed N1 行為測試)
k '!!(e && (e.isComposing || e.keyCode === 229)) || imeOn()' '!!(e && (e.isComposing)) || imeOn()'
# GD67 Mac 等確認框並列 Touch ID 與登入密碼(3e53b 203a4beb32 L1)
k '會寫同一組碼,一樣才確認;不一樣按取消。</p>'\'';' '會寫同一組碼,一樣才用 Touch ID 或輸入登入密碼;不一樣按取消。</p>'\'';'
# GD68 Windows 也出測試機(客戶當成加主機,回 internal;ebab8 21:51)
k 'const thDev = () => !(typeof fpWin === '\''function'\'' && fpWin());' 'const thDev = () => true;'
# GD69 測試機卡不看 thDev
k 'if (hasAct('\''test_host_list'\'') !== true || (typeof thDev === '\''function'\'' && !thDev())) return '\'''\'';' 'if (hasAct('\''test_host_list'\'') !== true) return '\'''\'';'
# GD70 開席視窗的測試機候選不看 thDev
k '    if (typeof thDev === '\''function'\'' && !thDev()) return '\'''\''; // 測試機候選' '    if (false) return '\'''\''; // 測試機候選'
# GD71 Windows 指引「加一台主機」仍是現在那一步、出鈕
k 'if (s.id === '\''host_add'\'' && typeof thDev === '\''function'\'' && !thDev()) return '\''later'\'';' 'if (false) return '\''later'\'';'
