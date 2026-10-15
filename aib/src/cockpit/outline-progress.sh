#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 大綱進度一行摘要(掛 tmux status-right,主畫面/戰情室永遠可見)
# 遷自 ~/ez2-cockpit-demo/outline-progress.sh(E2.1;解析改 cockpit_lib,路徑全 env)
. "$(dirname "$0")/cockpit-env.sh"
"$EZ2_PY3" - <<'PY'
import glob, json, os
from cockpit_lib import parse_outline_file
try:
    from i18n_lib import Translator
    _t = Translator().t
except Exception:   # 字典讀不到也要印得出一行
    _t = lambda k, p=None: k

try:
    items = parse_outline_file(os.environ['EZ2_OUTLINE'])
except Exception:
    print(_t('aib.outline_progress.read_failed'))
    raise SystemExit
leaves = [x for x in items if x['leaf']]
drafts = sum(1 for x in leaves if x['approval'] == 'draft')
# done/total 走真相源(acceptance 契約 + git 主旨),不數板卡 status——板卡缺卡+
# 狀態值不認(draft-ready≠done)會嚴重低報(E3.2 診斷);分母=大綱 approved 葉子
import subprocess, re
repo = os.path.dirname(os.path.dirname(os.environ['EZ2_OUTLINE']))
done_nodes = set(os.path.basename(p)[:-5]
                 for p in glob.glob(os.path.join(repo, 'acceptance', '*.json')))
try:
    _log = subprocess.run(['git', '-C', repo, 'log', '--format=%s', '-400'],
                          capture_output=True, text=True).stdout
    done_nodes |= set(re.findall(
        r'^(?:feat|fix|chore|refactor)\([^)]*\): ([EA-Z]+\d+\.\d+)', _log, re.M))
except Exception:
    pass
leaf_ids = {x['id'] for x in leaves}
done = len(done_nodes & leaf_ids)
total = len(leaves)
tasks = {}
for p in glob.glob(os.path.join(os.environ.get('EZ2_TASKS_DIR', ''), '*.json')):
    try:
        t = json.load(open(p))
        tasks[t['outline']] = t.get('status')
    except Exception:
        pass
run = sum(1 for s in tasks.values() if s == 'in_progress')
# ★ 2026-08-21 ACB 廢止(工單 DISPATCH-acb-sweep-0821 §二·A·3):
# 原本這裡數 ~/.acb/inbox/pm/*.md 的未讀人類訊息,加一個 ' 📩人N' 尾綴。
# 該目錄現存 *.md = 0 檔(最後寫入 07-18),所以 hn 恆 0、尾綴恆空——
# 刪掉它畫面逐位元組不變(交付時已用改動前後輸出比對證明)。
# 選「刪」不選「改源」的理由:AIB 這邊**沒有**人類收件匣可以改指
# (~/.ez2-aib/inbox 不存在)。要恢復這個指標,得先有那個資料源;
# 在沒有來源時保留一段恆 0 的計數,只會讓人以為「沒有未讀」是量出來的。
print(_t('aib.outline_progress.line', {'done': done, 'total': total, 'run': run, 'drafts': drafts}))
PY
