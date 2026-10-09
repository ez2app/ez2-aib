#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""大綱 -> 任務 生成器(三層樹)。

遷自 ~/ez2-cockpit-demo/acb-sandbox/outline-to-tasks.py(E2.1;解析改
cockpit_lib,大綱與任務板路徑改 env)。

只把「approved 且沒有子項的葉子條目」轉成任務;draft 一律不碰。冪等。

E3.1 起支援兩個後端(--board,env EZ2_TASK_BOARD,預設 sandbox 不變):
  sandbox  沙盒 JSON 板(demo 指揮艙現行讀的板;行為與 E2.1 版逐字相同)
  acb      真 ACB 板,呼叫 acb task-create。真板是跨專案共用的生產板,故:
           - 預設 dry-run:只印將執行的完整指令,不打真板;--execute 才真跑
           - 一律帶 --phase 命名空間(env EZ2_ACB_PHASE,預設 aib),
             標題帶 [E編號] token,冪等靠「task-list --phase 掃既有 token」;
             掃描為寬鬆比對(全形括號正規化+token 內容忍空白),token 對不上但
             板面已有同名標題時**警示且不建立**(共用板寧漏建勿重複;agy 異質審
             E3.1 審單成立項的處置,詳 reviews/E3.1-agy.md)
           - task-list 讀不到(rc!=0)=fail-closed rc 2,一筆都不創建
             (看不見板就往板上倒任務=盲寫,原理 10 的閘門側)
           - --execute 必須帶 --who(有權者具名;task-create 第一參數),
             T0 候綁 session 依 E8.1 不該真跑,dry-run 給誰都可以
           - 逐筆 task-create rc!=0 如實計失敗續跑其餘,結尾 rc 1(不早退不吞紅)
           acb 執行檔可用 EZ2_ACB_BIN 覆寫(測試注入假 acb 用,不碰真板)。

--only <軸>(可逗號多值)只處理指定軸/條目:E3=整軸(E3.1、E3.2…),E2.8=單條;
兩後端皆適用(試水首跑靠它;真板 execute 時機由 the maintainer 拍板,本工具不自作主張)。

rc:0=完成(dry-run 或全數成功)/ 1=部分 task-create 失敗 / 2=用法錯或真板不可讀
   / 3=acb 後端不可用(執行檔不存在)。

★ 2026-08-21(ACB 廢止,the maintainer 直令):acb 是**可選相依**。本檔與其他 ACB 殘量
  不同類——它呼叫的是 `acb` **執行檔**,不是讀 `~/.acb` 路徑,所以刪目錄殺不到
  它,而 binary 一旦不在就整條後端不可用。
  3 與 2 刻意分開:改動前兩者共用 rc=2 與同一個 `[fail-closed]` 標籤,於是
  「這個後端已經退場」與「板壞了要修」給出**同一個答案格式**——讀的人分不出
  該去修板還是該改用 sandbox。同一族的病今天出現過很多次。
測試:tests/test-outline-to-tasks.py(acb 後端+過濾)+ tests/test-cockpit-smoke.py(沙盒冪等)。
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cockpit_lib import parse_outline_file  # noqa: E402

OUTLINE = os.environ.get(
    "EZ2_OUTLINE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "docs", "EZ2-AIB-DEV-OUTLINE.md"))
# ED.1(2026-08-22):預設不再寫死家目錄——cockpit-env.sh 把 EZ2_TASKS_DIR 放在
#   $EZ2_AIB_INSTANCE_HOME/cockpit/sandbox-tasks。ED.1 (e):這條鏈改由
#   seats_dir_lib.tasks_dir() 單源提供(同結果,多加一層 EZ2_COCKPIT_STATE_DIR),
#   不在第四支檔再抄一份 env 鏈。
from seats_dir_lib import tasks_dir  # noqa: E402
TASKS = tasks_dir()
ACB_BIN = os.environ.get("EZ2_ACB_BIN", "acb")
ACB_PHASE = os.environ.get("EZ2_ACB_PHASE", "aib")

# 標題 token:[E2.8] 之類;真板冪等靠掃這個,不靠自帶狀態檔(板即真相)。
# agy 異質審(E3.1 審單)指出:嚴格正則會被格式變體(全形括號/空白)靜默繞過,
# 導致共用板重複下單。處置=寬鬆比對:掃描前全形括號正規化、token 內容忍空白。
_TOKEN_RE = re.compile(r"\[\s*(E[^\]\s]+?)\s*\]")


def _normalize_board(text):
    """板面正規化:全形括號()-> 半形,再交給 _TOKEN_RE。"""
    return text.replace("［", "[").replace("］", "]")


def _squash(text):
    """同名比對鍵:去除所有空白。標題級比對用,抓「token 不合格式但同一任務」。"""
    return re.sub(r"\s+", "", str(text))


def match_only(item_id, only):
    """--only 過濾:E3 涵蓋 E3 與 E3.*;E2.8 恰指該條。only 為 None=不過濾。"""
    if only is None:
        return True
    return any(item_id == o or item_id.startswith(o + ".") for o in only)


def run_sandbox(items):
    """E2.1 原行為,逐字不動(smoke 測試鎖此輸出)。"""
    os.makedirs(TASKS, exist_ok=True)
    made, skipped = [], []
    for it in items:
        if not it["leaf"]:
            continue
        path = os.path.join(TASKS, f"T-{it['id']}.json")
        if it["approval"] != "approved":
            skipped.append((it["id"], "draft:候拍板"))
            continue
        if os.path.exists(path):
            skipped.append((it["id"], "已有任務"))
            continue
        task = {"id": f"T-{it['id']}", "outline": it["id"], "title": it["title"],
                "priority": it["priority"], "status": "open", "assignee": None,
                "worktree": None, "updated_at": int(time.time())}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        made.append(it["id"])
    print(f"生成 {len(made)} 筆: {','.join(made) or '-'}")
    for i, why in skipped:
        print(f"跳過 {i}: {why}")
    return 0


def acb_available():
    """acb 執行檔在不在 → (bool, 解析到的路徑或 None)。

    ★ 只回事實不做決定:呼叫端才知道「不在」該怎麼處置。
      帶路徑分隔符時直接看該檔可不可執行(EZ2_ACB_BIN 注入的假 acb 走這條);
      裸名字才查 PATH。
    """
    if os.sep in ACB_BIN or ACB_BIN.startswith("."):
        ok = os.path.isfile(ACB_BIN) and os.access(ACB_BIN, os.X_OK)
        return ok, (ACB_BIN if ok else None)
    found = shutil.which(ACB_BIN)
    return bool(found), found


def acb_existing_tokens(phase):
    """掃真板既有 [E編號] token(寬鬆比對)。讀不到板 raise(fail-closed,
    呼叫端轉 rc 2)。回 (token 集合, 板面原文)——板面原文供標題級同名偵測。"""
    p = subprocess.run([ACB_BIN, "task-list", "--phase", phase],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"task-list rc={p.returncode},真板不可讀,一筆都不創建:{p.stderr.strip()}")
    return set(_TOKEN_RE.findall(_normalize_board(p.stdout))), p.stdout


def run_acb(items, who, execute, phase):
    # ★ 可選相依檢查排在最前:acb 不在時**一個 subprocess 都不要起**,
    #   否則拿到的是 FileNotFoundError,而它會被下面的 OSError 分支吃成
    #   「真板不可讀」——那正是要分開的兩件事。
    ok, resolved = acb_available()
    if not ok:
        print("[acb-absent] acb 後端不可用:找不到可執行的 %r。" % ACB_BIN,
              file=sys.stderr)
        print("  ACB 已於 2026-08-21 判廢除,本後端自此是**可選**的。",
              file=sys.stderr)
        print("  要用沙盒板:--board sandbox(預設值,行為不變)。",
              file=sys.stderr)
        print("  要指定執行檔:EZ2_ACB_BIN=<path>。", file=sys.stderr)
        print("  ★ 這不是「板上沒有任務」,也不是「板壞了」——是相依不在,"
              "所以一筆都沒有讀、也沒有寫。", file=sys.stderr)
        return 3
    try:
        existing, board_text = acb_existing_tokens(phase)
    except (RuntimeError, OSError) as err:
        print(f"[fail-closed] {err}", file=sys.stderr)
        return 2
    board_squashed = _squash(board_text)
    planned, skipped, suspects, failed = [], [], [], []
    for it in items:
        if not it["leaf"] or it["approval"] != "approved":
            continue
        if it["id"] in existing:
            skipped.append(it["id"])
            continue
        # 標題級同名偵測(agy 審單處置 b):token 對不上但板面已有一模一樣的標題
        # =疑似同一任務以他種格式在板——共用板寧可警示要人工確認,不可靜默重複下單
        if _squash(it["title"]) and _squash(it["title"]) in board_squashed:
            suspects.append(it["id"])
            print(f"警示 {it['id']}: 板上疑似已有同名任務(token 格式不合),"
                  "不建立,請人工確認後處置", file=sys.stderr)
            continue
        cmd = [ACB_BIN, "task-create", who or "<who>", f"[{it['id']}] {it['title']}",
               "--phase", phase,
               "--desc", f"outline={it['id']} prio={it['priority']} "
                         f"src=docs/EZ2-AIB-DEV-OUTLINE.md"]
        if it["priority"] == "p1":
            cmd.append("--critical")
        if execute:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                planned.append(it["id"])
            else:
                failed.append((it["id"], p.returncode, p.stderr.strip()))
        else:
            planned.append(it["id"])
            print("DRY-RUN: " + shlex.join(cmd))
    mode = "建立" if execute else "將建立(dry-run,--execute 才打真板)"
    print(f"{mode} {len(planned)} 筆: {','.join(planned) or '-'}")
    print(f"已在板上跳過 {len(skipped)} 筆: {','.join(skipped) or '-'}")
    if suspects:
        print(f"疑似同名未建 {len(suspects)} 筆: {','.join(suspects)}(見 stderr 警示)")
    for tid, rc, err in failed:
        print(f"失敗 {tid}: rc={rc} {err}", file=sys.stderr)
    return 1 if failed else 0


def main(argv):
    board = os.environ.get("EZ2_TASK_BOARD", "sandbox")
    who = None
    execute = False
    phase = ACB_PHASE
    only = None
    it = iter(argv[1:])
    for a in it:
        if a == "--board":
            board = next(it, "") or ""
        elif a == "--who":
            who = next(it, None)
        elif a == "--phase":
            phase = next(it, "") or ""
        elif a == "--only":
            spec = next(it, "") or ""
            only = [x for x in spec.split(",") if x]
            if not only:
                print("--only 不可為空,例:--only E3 或 --only E3,E7.0", file=sys.stderr)
                return 2
        elif a == "--execute":
            execute = True
        else:
            print("usage: outline-to-tasks.py [--board sandbox|acb] "
                  "[--who NAME] [--phase P] [--only 軸,...] [--execute]", file=sys.stderr)
            return 2
    items = [x for x in parse_outline_file(OUTLINE) if match_only(x["id"], only)]
    if only:
        print(f"--only {','.join(only)}:過濾後 {len(items)} 筆候選")
    if board == "sandbox":
        return run_sandbox(items)
    if board == "acb":
        if execute and not who:
            print("--execute 需要 --who(task-create 具名執行者)", file=sys.stderr)
            return 2
        if not phase:
            print("--phase 不可為空(真板必須命名空間隔離)", file=sys.stderr)
            return 2
        return run_acb(items, who, execute, phase)
    print(f"未知 board: {board}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
