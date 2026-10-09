#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 左下:第二大腦分類樹(route registry 為骨架,不掃資料夾名;Alt+o 開合)
# 遷自 ~/ez2-cockpit-demo/acb-left-brain.sh(E2.1;registry/vault/狀態檔全改 env)
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
import json, os
from cockpit_lib import trunc

reg_path = os.environ.get('EZ2_ROUTE_REGISTRY', '')
depth_path = os.path.join(os.environ['EZ2_COCKPIT_STATE_DIR'], 'brain-depth')
VAULT = os.environ.get('EZ2_VAULT_DIR', os.path.expanduser('~/Desktop/Obsidian/Ez2'))

try:
    depth = int(open(depth_path).read().strip())
except Exception:
    depth = 1
depth = 2 if depth == 2 else 1

try:
    routes = json.load(open(reg_path)).get('routes', [])
except Exception:
    print(' 第二大腦')
    print()
    print(' (route registry 不可用)')
    print(' 設 EZ2_ROUTE_REGISTRY 指到')
    print(' obsidian-route-registry-contract.json')
    raise SystemExit

MOD = {'C': '核心', 'P': '專家', 'F': '流程', 'G': '智囊', 'A': 'ACB'}
groups = {}
for r in routes:
    groups.setdefault(r.get('module', '?'), []).append(r)

print(f' 第二大腦 {"(開)" if depth == 2 else "(合)"}')
print()
for m in ['C', 'P', 'F', 'G', 'A']:
    rs = groups.get(m, [])
    if not rs:
        continue
    wr = sum(1 for r in rs if r.get('aiWritable'))
    print(f' {m} {MOD.get(m, m)} {len(rs)}路 ✏{wr}')
    if depth < 2:
        continue
    for r in rs[:6]:
        folder = (r.get('folder') or '').split('/')[-1]
        pen = '✏' if r.get('aiWritable') else '·'
        lv = r.get('dataLevel') or ''
        lock = '🔒' if lv in ('機密', '敏感') else ''
        n = ''
        try:
            fp = os.path.join(VAULT, r.get('folder') or '')
            n = str(len([f for f in os.listdir(fp) if f.endswith('.md')]))
        except OSError:
            n = '-'
        print(f'  ╰{pen} {trunc(folder, 12)} {lock}{n}')
    if len(rs) > 6:
        print(f'   …共{len(rs)}路')
print()
mod = os.environ.get('EZ2_MODIFIER_LABEL', 'Option' if os.uname().sysname == 'Darwin' else 'Alt')
print(f' {mod}+Shift+O 開合 ✏=AI可寫')
PY
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  sleep 8
done
