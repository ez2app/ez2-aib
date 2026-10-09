#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# 板卡自動對帳(the maintainer 令 2026-08-08「進度沒被即時更新,為什麼還沒程式化」之制度解)
# 真相源優先序:git 提交主旨(done)> lane DRAFT-READY 標記(in_progress)> 板卡現值
# 由 captain-patrol.sh 每輪快照前呼叫;冪等,只升不降(done 不會被降回)。
import json, glob, os, re, subprocess, sys

SB = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/ez2-cockpit-demo/acb-sandbox")
REPO = os.path.expanduser("~/ez2-aib")

# 1. git 主旨裡的節點號 = done(慣例:type(scope): E<id> 標題)
log = subprocess.run(["git", "-C", REPO, "log", "--format=%s", "-300"],
                     capture_output=True, text=True).stdout
done_ids = set()
for line in log.splitlines():
    m = re.match(r"^(feat|fix|chore|refactor)\([^)]*\): (E[A-Z]?\d+\.\d+)\b", line)
    if m:
        done_ids.add(m.group(2))

# 2. lane DRAFT-READY 標記 = 已交付候審(板卡語彙=in_progress,不降 done)
draft_ids = set()
for marker in glob.glob(os.path.join(REPO, "src", "*", "DRAFT-READY")) + \
              glob.glob(os.path.join(REPO, "tools", "DRAFT-READY-*")):
    base = os.path.basename(marker)
    if base.startswith("DRAFT-READY-"):
        draft_ids.add(base.replace("DRAFT-READY-", ""))
    else:
        lane = os.path.basename(os.path.dirname(marker))
        # lane 目錄名對節點的對映:從 acceptance/ 契約反查
        for acc in glob.glob(os.path.join(REPO, "acceptance", "*.json")):
            try:
                txt = open(acc).read()
            except OSError:
                continue
            if f"src/{lane}/" in txt:
                draft_ids.add(os.path.basename(acc)[:-5])

changed = []
for p in glob.glob(os.path.join(SB, "tasks", "*.json")):
    try:
        d = json.load(open(p))
    except Exception:
        continue
    tid = (d.get("id") or "")[2:]  # T-E6.2 -> E6.2
    st = d.get("status")
    new = st
    if tid in done_ids:
        new = "done"
    elif tid in draft_ids and st == "open":
        new = "in_progress"
    if new != st:
        d["status"] = new
        json.dump(d, open(p, "w"), ensure_ascii=False, indent=1)
        changed.append(f"{tid}:{st}->{new}")
if changed:
    print("board-sync:", " ".join(changed))
