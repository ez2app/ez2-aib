#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 戰情室看板:三層樹(大綱 -> 子項目 -> Task),CJK 對齊,無框線字元
# 遷自 ~/ez2-cockpit-demo/acb-sandbox/outline-board.sh(E2.1)
# 修正:大綱/任務板/深度檔全改 env;CJK 函數與解析改 cockpit_lib 唯一實作
. "$(dirname "$0")/cockpit-env.sh"

render_frame() {
  "$EZ2_PY3" - <<'PY'
import glob, json, os
from cockpit_lib import pad, trunc, parse_outline_file
from render.render_lib import clean_data

# ACB 活碼 P-2 已拔(REMOVAL-PLAN-acb-live-P2-0822.md §二,2026-08-23)。原本這裡每一幀
# 都 json.load 全部 ~/.acb/sessions/by-id/*.json(實測 517 檔)來算 in_progress 的心跳,
# 而那個來源最後一筆停在 08-11 18:41 —— 燈死了 11 天,畫面上恆顯示「⚠ 查無心跳」。
# 留字面不留功能等於留一顆下次掃描會再長回來的 P,所以整段拔掉,不改預設值。
OUTLINE = os.environ['EZ2_OUTLINE']


def _t(key, **params):
    # 走 aib/i18n 四語鍵;字典出事就印鍵名,畫面不因翻譯失敗變空。
    try:
        import sys
        sys.path.insert(0, os.environ.get('EZ2_COCKPIT_DIR', ''))
        from i18n_lib import t
        return t(key, params)
    except Exception:
        return key


# 大綱檔不在(公開版沒附開發大綱、使用者也還沒設 EZ2_OUTLINE)是正常狀態,不是錯誤:
# 說一句怎麼設,下面的裁示任務照樣列。檔在但讀不了才是錯誤,照舊報錯停在這。
if os.path.exists(OUTLINE):
    try:
        items = parse_outline_file(OUTLINE)
    except Exception:
        print(' ' + _t('aib.warroom.outline_unreadable', path=OUTLINE))
        raise SystemExit
else:
    items = None

tasks = {}
ruling_tasks = []
for p in glob.glob(os.path.join(os.environ.get('EZ2_TASKS_DIR', ''), '*.json')):
    try:
        t = clean_data(json.load(open(p)))
        if t.get('source') == 'ruling':
            ruling_tasks.append(t)
        elif t.get('outline'):
            tasks[t['outline']] = t
    except Exception:
        pass

def task_live(t):
    who = t.get('assignee') or '-'
    st = t.get('status')
    if st == 'open':
        return who, '待認領', ''
    if st == 'done':
        return who, '完成', '✅'
    # in_progress 不再標心跳:唯一的心跳來源(ACB P-2)已拔,見上方。
    return who, '進行中', ''

depth_path = os.path.join(os.environ['EZ2_COCKPIT_STATE_DIR'], 'view-depth')
try:
    view = int(open(depth_path).read().strip())
except Exception:
    view = 3
view = min(3, max(1, view))

TREE, WHO, ST = 50, 10, 8
if items is None:
    print(' ' + _t('aib.warroom.no_outline'))
    items = []
else:
    print(f' 戰情室:{os.path.basename(OUTLINE)}    展開 {view}/3')
    print(' ' + pad('', TREE) + pad('認領', WHO) + pad('板上', ST) + '實況')
    print(' ' + '-' * 100)

def parent_of(x):
    return x['id'].rsplit('.', 1)[0] if '.' in x['id'] else None

def is_last_sibling(x):
    sibs = [y for y in items if parent_of(y) == parent_of(x) and y['depth'] == x['depth']]
    return sibs and sibs[-1] is x

for i, it in enumerate(items):
    if it['depth'] >= view:
        continue
    if it['depth'] == 0:
        ind = ''
    else:
        ind = '  ' * (it['depth'] - 1) + (' ╰─ ' if is_last_sibling(it) else ' ├─ ')
    # 彙總:此節點子孫(含自身)的葉子任務
    kids = [x for x in items if x is it or x['id'].startswith(it['id'] + '.')]
    leaves = [x for x in kids if x['leaf']]
    total = len(leaves)
    done = sum(1 for x in leaves if tasks.get(x['id'], {}).get('status') == 'done')
    label = f"{it['id']} {it['title']}"
    if it['depth'] == 0 and not it['leaf']:
        row = pad(trunc(label, TREE - 1), TREE) + pad('', WHO) + pad(f'{done}/{total}', ST)
        print(' ' + row)
        continue
    mark = '' if it['approval'] == 'approved' else '(候拍板)'
    print(' ' + pad(trunc(ind + label + mark, TREE - 1), TREE))
    if it['leaf'] and it['approval'] == 'approved' and view >= it['depth'] + 2:
        t = tasks.get(it['id'])
        if t:
            who, st, live = task_live(t)
            tind = '  ' * it['depth'] + ' ╰─ '
            trow = pad(tind + t['id'], TREE) + pad(who, WHO) + pad(st, ST) + live
            print(' ' + trow)
active_rulings = [t for t in ruling_tasks if t.get('status') not in
                  {'done', 'cancelled', 'closed', 'abandoned'}]
if active_rulings:
    print()
    print(' Ruling work:')
    for task in sorted(active_rulings, key=lambda value: str(value.get('id', ''))):
        print(' * %s %s %s' % (
            task.get('id') or '?', task.get('status') or 'unknown',
            task.get('title') or ''))
print()
print(' 板上=任務板宣稱 | 完成打 ✅ | Opt-←→ 切分頁 Opt-↑↓ 切欄')
PY
}

# The war-room is a separate cockpit window, but it has the same selection
# contract as the three static side panes: data may be sampled often without
# repainting identical terminal cells every second.
FIRST_PAINT=1
LAST_FRAME=""
LAST_GEOMETRY=""
REFRESH_TICKS="${EZ2_WARROOM_REFRESH_TICKS:-10}"
case "$REFRESH_TICKS" in
  ''|*[!0-9]*|0) REFRESH_TICKS=10 ;;
esac
PANE_SELF_SCRIPT="$EZ2_COCKPIT_DIR/warroom-board.sh"   # BL:碼換了自己重跑
. "$EZ2_COCKPIT_DIR/pane-tick.sh"
pane_tick_init
while true; do
  pane_reexec_if_changed                # BL:腳本換了就 exec 自己(每 ~30 秒問一次)
  pane_wait_if_detached && continue     # 沒人看:不渲染,維持運作(§59)
  pane_wait_if_composing && continue    # 終端機組字中:不渲染(10-10,避開 Terminal 重釘組字區的崩潰;見 pane-tick.sh)
  PANE_GEOM_DIRTY=0
  if [ -n "${TMUX_PANE:-}" ]; then
    PANE_GEOMETRY="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-100}" "${LINES:-30}")"
  else
    PANE_GEOMETRY="${COLUMNS:-100} ${LINES:-30}"
  fi
  FRAME="$(render_frame)"
  if [ "$FIRST_PAINT" = "1" ] || [ "$FRAME" != "$LAST_FRAME" ] || \
     [ "$PANE_GEOMETRY" != "$LAST_GEOMETRY" ]; then
    printf '\033[H%s\033[J' "$FRAME"
    LAST_FRAME="$FRAME"
    LAST_GEOMETRY="$PANE_GEOMETRY"
    FIRST_PAINT=0
  fi
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  for ((tick=0; tick<REFRESH_TICKS; tick++)); do
    pane_tick_sleep 0.1
    [ "$PANE_GEOM_DIRTY" = "1" ] && break          # resize 事件(WINCH),不再每 tick 問 tmux(§59)
  done
done
