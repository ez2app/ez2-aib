#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 左上:人格面板(綁定卡+runtime store 訊號+候審佇列)
# 遷自 ~/ez2-cockpit-demo/acb-left-persona.sh(E2.1;trunc 改用 cockpit_lib 唯一實作)
. "$(dirname "$0")/cockpit-env.sh"
while true; do
  [ "${EZ2_ONESHOT:-0}" = "1" ] || clear
  if [ -n "${TMUX_PANE:-}" ]; then
    read -r EZ2_PANE_W EZ2_PANE_H <<EOF
$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-44}" "${LINES:-30}")
EOF
    export EZ2_PANE_W EZ2_PANE_H
  fi
  "$EZ2_PY3" - <<'PY' | "$EZ2_PY3" "$EZ2_COCKPIT_DIR/pane-fit.py" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'
import glob, json, os, sys
from cockpit_lib import trunc
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
from i18n_lib import Translator  # noqa: E402

tr = Translator()

runtime = os.environ.get('EZ2_PERSONA_RUNTIME', os.path.expanduser('~/.ez2-persona-runtime'))

# ★★ 2026-08-21:人格來源從 ACB registry 換成 AIB 身份層(艦長 20:32 裁為
#   「現在畫面上的正確性缺陷」,不是清理項)。舊碼有兩個獨立的問題:
#   ① 來源已死:~/.acb/sessions/by-id 最後一筆 08-11,而畫面沒有任何過期標示。
#   ② **取的不是本艙的席**:它挑的是全部 517 筆裡 beat_epoch 最大且有人格的那一筆
#      —— 一個全域 argmax,與這一艙、這一席都無關。畫面上只印人格名字,
#      所以「這是誰的人格」從來沒有被顯示過,也就沒有人能發現它是別人的。
#   ★ 實測當下它顯示 ROOT-架構總管 / d65bc6,而 d65bc6 正好**也是**本艙 96188
#     在 AIB 側的 persona_uid —— **一個巧合讓錯的來源看起來是對的**。
#   語意翻譯(刻意記下來,這是行為改變不是等價替換):
#     舊 = 全域 beat_epoch 最大的一筆
#     新 = **本艙 last_used_epoch 最大的那一席**,並且把是哪一席一起印出來
#   來源:identity/bindings.json + identity/seat-display.json(實測 08-21 18:23 仍在更新)
#   席位目錄走 seat_onboard.seats_dir()(HUB 導出),不自己再寫一份。
# ★ 09-29 a seat:身份層是**全機一份**(`<狀態根>/identity`),艙的 instance home 底下沒有 identity/
#   (真機 instances/*/identity 零個)⇒ 以前每一艙這一格都印「源讀不到」。
#   先找狀態根;艙底下真的有一份才用艙的(相容舊的單艙佈局)。
_inst_ident = os.path.join(os.environ.get('EZ2_AIB_INSTANCE_HOME') or '', 'identity')
ident = (_inst_ident if os.environ.get('EZ2_AIB_INSTANCE_HOME') and os.path.isdir(_inst_ident)
         else os.path.join(os.environ.get('EZ2_AIB_HOME') or os.path.expanduser('~/.ez2-aib'), 'identity'))
# ★ 五態語彙(DESIGN-SPEC-source-state-vocabulary,a seat):
#   這一格的三種退化以前**全部顯示成「(未綁定)」**,而它們的處置相反:
#     ok         查得到人格
#     empty      席位在、身份層可讀,**這一席真的沒綁**  → 要去綁
#     unreadable 身份層讀不到(檔缺、壞 JSON、席位目錄解不出) → 要去修
#   規格第一條:`rows == []` 不得單獨決定畫面上那句話。舊碼正是那樣。
persona, puid, whose, state = None, None, None, 'unreadable'
try:
    from seat_onboard import seats_dir
    from seat_persona import display_entry, display_key
    best = 0
    seat_id = None
    seat_rec = None
    for sp in glob.glob(os.path.join(seats_dir(), 'w*.json')):
        try:
            rec = json.load(open(sp))
        except Exception:
            continue
        lu = rec.get('last_used_epoch') or 0
        if lu > best and rec.get('seat_id'):
            best, seat_id, seat_rec = lu, rec['seat_id'], rec
            # 09-29:畫面上的席位一律寫席碼(@後面那五碼),與右欄、dm 抬頭同一個字
            whose = '%s@%s' % (rec.get('role') or '?', display_key(rec) or rec['seat_id'])
    if seat_id:
        # ★ 09-29 a seat:兩份投影的鍵都是**席碼**(uuid 尾五碼),以前拿 seat_id 去查 ⇒ 永遠查不到。
        #   查表只走 seat_persona.display_entry(全庫唯一的投影查表處,含遷移期雙認)。
        disp = json.load(open(os.path.join(ident, 'seat-display.json')))['display']
        bind = json.load(open(os.path.join(ident, 'bindings.json')))['bindings']
        entry = display_entry(seat_rec, disp) or {}
        persona = entry.get('display_name')
        puid = entry.get('persona_uid') or (display_entry(seat_rec, bind) or {}).get('persona_uid')
        # 身份層讀得到 → 剩下只有兩態,由「這一席有沒有綁」決定
        state = 'ok' if (persona or puid) else 'empty'
except Exception:
    state = 'unreadable'

print(' 人格')
print()
if state == 'ok':
    print(' ' + trunc(persona or '?', 18))
    print(f' uid {puid or "--"}')
else:
    # 兩種空看起來不能一樣 —— 這正是本規格要解的問題
    print(' ' + trunc(tr.t('aib.source.' + state), 18))
    if state == 'unreadable':
        print(' ' + trunc(tr.t('aib.source.which', {'name': 'identity/'}), 18))
    else:
        print(f' uid --')
# ★ 這一行是本次修正的重點,不是裝飾:舊畫面只印人格名字,不印它屬於誰,
#   所以「顯示的是別人的人格」在畫面上沒有任何跡象。印出席位之後,
#   任何人看一眼就能發現對不對 —— 判準要放在人已經在看的地方。
print(f' 席 {whose or "--"}')
try:
    s = json.load(open(os.path.join(runtime, 'persona-signal-summary.json')))
    print(f' 事件 {s.get("eventCount", 0)}')
    print(f' 訊號 {s.get("signalCount", 0)} 種')
    print()
    for sig in (s.get('signals') or [])[:3]:
        print(' ' + trunc(sig.get('label', '?'), 18))
        print(f'   {sig.get("strengthPercent", 0)}% x{sig.get("count", 0)}')
    up = (s.get('updatedAt') or '')[5:16]
    print()
    print(f' 更新 {up}')
except Exception:
    print(' (runtime store 無資料)')
try:
    st = json.load(open(os.path.join(runtime, 'claude-conv-distill-state.json')))
    print(f' 候審 {st.get("staged_total", 0)} 筆(staging)')
    print(' -> ' + trunc(st.get('target', '主人格'), 14))
except Exception:
    print(' 候審 0(管線休眠)')
PY
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  sleep 10
done
