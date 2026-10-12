#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""推成後自動更新另一台(推包管道 P9,docs/DESIGN-ship-merge-queue-1011.md §4b P9;the maintainer 10-11 23:5x 核准、a seat 派)。

為什麼:Mini 正本推成後會自動快轉(ship.sync_canonical),Pro 不會;10-11 每推一輪都要叫 a seat 手動去 Pro
ff-only + 重啟艦長室,忘了就是 Pro 上的艦長室跑舊版。

做什麼:對艦長室主機名冊(<狀態根>/state/captain-room/config.json 的 hosts[])每一台,連過去:
  正本在 main、沒有未提交的追蹤檔、找得到指向同一個 GitHub 倉的遠端 ⇒ fetch ⇒ 能快轉才 `merge --ff-only`;
  有前進且裝了艦長室開機項 ⇒ `launchctl kickstart -k` 重啟艦長室。
  不能快轉(本機多了提交)/不在 main/有改動/找不到遠端/連不上 ⇒ **一律不動**,回報原因。不 reset、不 stash、不強推。
連線用 captain_room.ssh_argv(不借金鑰代理、不開轉發、BatchMode,與艦長室同一套),**但不用艦隊金鑰**:
  艦隊金鑰在對方 authorized_keys 是 forced command(fleet-cmd.sh 只准 probe / version),
  這裡要跑 git,會被拒(10-12 第二輪審查)。改用推包那台使用者自己的 SSH 設定(跟人手動連過去一樣)。
名冊那台可選備援位址 ssh_fallback(例 user 在區網 192.168.0.10):主位址連線本身失敗(rc 255 / 指令起不來;
  逾時不算,多半已連上在跑)才改連它再試一次,加 HostKeyAlias=<主位址的主機名> 與 StrictHostKeyChecking=yes,
  只認 known_hosts 裡原主機的金鑰(對不上或沒紀錄都拒連);結果帶 via=<備援位址>。
  沒設或格式不對 ⇒ 照舊回報連不上(a seat 10-12 13:31:Pro 的 Tailscale 22 逾時、區網通)。
AIB_SHIP_NO_AFTER_PUSH=1 ⇒ 整段跳過。結果回一串 dict,由 ship 印出並寫進推包回報。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOM_LABEL = "com.ez2.aib-captain-room"

# 遠端腳本:每一步不符合就印 STATE=<原因> 並 exit 0(連線本身成功;原因由這邊判)。只讀 + 快轉 + 重啟,沒有別的寫入。
REMOTE = r'''
set -u
REPO="$1"; SLUG="$2"; LABEL="$3"
cd "$REPO" 2>/dev/null || { echo "STATE=no_repo"; exit 0; }
[ "$(git symbolic-ref -q --short HEAD)" = main ] || { echo "STATE=not_main"; exit 0; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "STATE=dirty"; exit 0; }
R=""
while read -r n u t; do
  [ "$t" = "(fetch)" ] || continue
  case "$u" in *[:/]"$SLUG"|*[:/]"$SLUG.git"|*[:/]"$SLUG/") R="$n"; break;; esac   # 整段相等,ez2-aib 不會配到 ez2-aibot
done < <(git remote -v)
[ -n "$R" ] || { echo "STATE=no_remote"; exit 0; }
# 明寫 refspec:遠端的 fetch 設定不一定對應 main,只寫 main 不保證會更新 $R/main(審查實測:舊的 $R/main 被當成最新)
git fetch -q "$R" "+refs/heads/main:refs/remotes/$R/main" 2>/dev/null || { echo "STATE=fetch_failed"; exit 0; }
T=$(git rev-parse -q --verify "refs/remotes/$R/main") || { echo "STATE=fetch_failed"; exit 0; }
B=$(git rev-parse HEAD)
git merge-base --is-ancestor HEAD "$T" || { echo "STATE=diverged AHEAD=$(git rev-list --count "$T..HEAD")"; exit 0; }
# 不跑那台的掛勾(post-merge 之類):這一步只准快轉,不准順帶做別的事
git -c core.hooksPath=/dev/null merge -q --ff-only "$T" 2>/dev/null || { echo "STATE=ff_failed"; exit 0; }
A=$(git rev-parse HEAD)
# P8:快轉後順手裝「正本 main 禁直接提交」掛勾(用剛快轉到的那份);槽被別的掛勾佔著不覆寫,印 GUARD=3
G=src/policy/hooks/install-canonical-guard.sh
if [ -f "$G" ]; then bash "$G" install >/dev/null 2>&1; echo "GUARD=$?"; fi
if [ "$A" = "$B" ]; then echo "STATE=same SHA=$A"; exit 0; fi
echo "STATE=synced SHA=$A FROM=$B"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "ROOM=restarted" || echo "ROOM=restart_failed"
else
  echo "ROOM=none"
fi
'''


def state_root():
    return Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def hosts(config=None):
    p = Path(config) if config else state_root() / "state" / "captain-room" / "config.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [h for h in d.get("hosts") or [] if isinstance(h, dict) and h.get("ssh") and h.get("repo")]


def repo_slug(url):
    """git@github.com:ez2app/ez2-aib.git / https://github.com/ez2app/ez2-aib ⇒ ez2app/ez2-aib;認不出 ⇒ ""。
    給好幾行(一個遠端設了好幾個推送網址)⇒ 取第一個 github.com 的那行;沒有 github.com 就取第一行(第二輪審查)。"""
    lines = [ln.strip() for ln in (url or "").splitlines() if ln.strip()]
    pick = next((ln for ln in lines if "github.com" in ln), lines[0] if lines else "")
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", pick)
    return m.group(1) if m else ""


def _ssh_argv(hst):
    sys.path.insert(0, str(ROOT / "src" / "cockpit"))
    import captain_room
    return captain_room.ssh_argv(hst, fleet_key=False)


# 只收 user@host(不能 - 開頭,免得被 ssh 當成選項);host 是主機名 / IPv4
_SSH_DEST = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*$")


def fallback_argv(hst, ssh_argv=None):
    """有合格的 ssh_fallback ⇒ (ssh 參數, 備援位址);沒有 ⇒ (None, "")。HostKeyAlias 用主位址的主機名。"""
    fb = hst.get("ssh_fallback")
    main = str(hst.get("ssh") or "")
    if not isinstance(fb, str) or not _SSH_DEST.match(fb) or not _SSH_DEST.match(main) or fb == main:
        return None, ""
    argv = (ssh_argv or _ssh_argv)(dict(hst, ssh=fb))
    # accept-new 換成 yes(ssh 取第一個值,不能只在後面補):別名在 known_hosts 沒紀錄時,accept-new 會把備援那台的金鑰
    #   用主位址的名字收下,之後連主位址也信它(審查 10-12)。改 yes ⇒ 沒紀錄就拒連
    argv = ["StrictHostKeyChecking=yes" if a == "StrictHostKeyChecking=accept-new" else a for a in argv]
    return argv[:-1] + ["-o", "HostKeyAlias=" + main.split("@", 1)[1]] + argv[-1:], fb


def update_host(hst, slug, run=None, timeout=120, label=ROOM_LABEL):
    """回 {"name", "state", "sha", "from", "ahead", "room", "rc"}。run 給測試換掉 ssh;
    label 給測試換成不存在的名字(★ 測試在本機跑遠端腳本,用真名會重啟本機的艦長室)。"""
    cmd = "bash -s -- %s %s %s" % (shlex.quote(str(hst["repo"])), shlex.quote(slug), shlex.quote(label))
    run = run or (lambda a: subprocess.run(a, input=REMOTE, capture_output=True, text=True, timeout=timeout))
    out = {"name": hst.get("name") or hst["ssh"], "state": "unreachable", "sha": "", "from": "", "ahead": "",
           "room": "", "guard": "", "via": "", "rc": None}

    def attempt(argv):
        try:
            p = run(argv + [cmd])
        except OSError as e:                  # 連線指令本身起不來 ⇒ 算連線失敗
            return None, repr(e)[:80], True
        except subprocess.SubprocessError as e:   # 逾時:ConnectTimeout=5 早就過了 ⇒ 多半已連上、腳本在跑,不換路(審查 10-12)
            return None, repr(e)[:80], False
        return p, p.returncode, p.returncode == 255

    p, out["rc"], conn_failed = attempt(_ssh_argv(hst))
    if conn_failed:                           # 只有「連線本身失敗」才換路;遠端腳本出錯 / 逾時不重試(可能已經動過)
        fb_argv, fb = fallback_argv(hst)
        if fb_argv:
            p2, rc2, _ = attempt(fb_argv)
            if p2 is not None and p2.returncode == 0:
                p, out["rc"], out["via"] = p2, rc2, fb
            else:
                p, out["rc"] = None, "%s; %s rc=%s" % (out["rc"], fb, rc2)
    if p is None or p.returncode != 0:
        return out
    for tok in (p.stdout or "").split():
        k, sep, v = tok.partition("=")
        if sep and k in ("STATE", "SHA", "FROM", "AHEAD", "ROOM", "GUARD"):
            out[{"STATE": "state", "SHA": "sha", "FROM": "from", "AHEAD": "ahead", "ROOM": "room", "GUARD": "guard"}[k]] = v
    return out


def run_all(repo, run=None, config=None):
    """ship 推成後呼叫。回結果清單;AIB_SHIP_NO_AFTER_PUSH=1 或名冊空 ⇒ []。"""
    if os.environ.get("AIB_SHIP_NO_AFTER_PUSH") == "1":
        return []
    hs = hosts(config)
    if not hs:
        return []
    # --push:ship 實際推去的那個網址。正本的 origin 抓的是 Pro 鏡像、推的才是 GitHub(審查實測:不加 --push 倉名算成
    #   user/ez2-aib,Pro 永遠找不到遠端、永遠不快轉)
    p = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "--push", "--all", "origin"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    slug = repo_slug(p.stdout)
    if not slug:
        return [{"name": h.get("name") or h["ssh"], "state": "no_slug", "sha": "", "from": "", "ahead": "",
                 "room": "", "guard": "", "via": "", "rc": None} for h in hs]
    return [update_host(h, slug, run=run) for h in hs]
