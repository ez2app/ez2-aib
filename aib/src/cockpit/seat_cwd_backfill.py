#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""EH.3 回填端:替**既有**席位檔補上 `cwd`,不猜、對不上就留空。

寫入端(`aib` 的 `_mutate`)只在開席那一刻寫 `cwd`,所以已經在跑的席位
永遠不會有這個欄位 —— 實測本艙 0/7。EH.4「自動綁定索引」把 cwd 當硬前置,
所以缺這一段,EH.4 就只能拿 role 當鍵,而 role 在第二個專案會撞且撞了不報錯。

★ 值的來源必須與寫入端同源,不能是另一個代理指標
  可以拿到的候選有三個,只有一個能用:

    tmux pane_current_path   ✗ 那是「這個窗**現在**在哪」,agent 會 cd 來 cd 去
    席位檔 last_used/pid 推  ✗ 推不出目錄
    session transcript 的 cwd ✓ 那是 claude 行程自己記下的 cwd

  第三個之所以同源:寫入端寫的是 `aib:22` 的 `pwd -P`,而同一個值在 `:395`
  當 PROJECT_DIR 傳給 provider-launch.sh 去啟動 claude,claude 的 cwd 就是它。
  **回填進去的值與開席寫進去的值指的是同一件事**,不是兩種意思混在一個欄位裡。

★ 自洽檢查(這支的承重判準)
  transcript 落在 `~/.claude/projects/<escape_cwd(cwd)>/<uuid>.jsonl`,
  也就是**目錄名**與**檔內 cwd 欄**是同一個事實的兩次獨立落地。
  只有 `escape_cwd(檔內 cwd) == 目錄名` 才寫。對不上代表其中一邊被搬過或
  被改過,那時候「猜一個」比「留空」貴 —— 留空 `seat_project_dir()` 回 None,
  EH.4 分得出「不知道」;猜錯的話它會被當成一個正常的專案歸屬,而且不報錯。

★ 為什麼要寫 `cwd_source`
  沒有它的話,「開席當下寫的權威值」與「事後回推的值」在檔裡**逐字相同**,
  之後沒有人分得出來。這個欄位的用途不是給 EH.4 讀,是讓「回填錯了」
  這件事在事後查得出來。

用法:
    python3 src/cockpit/seat_cwd_backfill.py            # 只報告,不寫(預設)
    python3 src/cockpit/seat_cwd_backfill.py --apply    # 真寫
    python3 src/cockpit/seat_cwd_backfill.py --seats-dir DIR --projects-root DIR

rc: 0=跑完(不論補了幾筆) · 2=席位目錄不可讀
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from activity_probe import escape_cwd            # noqa: E402  單一真相源,不另寫第二份
import atomic_json                               # noqa: E402
import seats_dir_lib                             # noqa: E402

# transcript 第一筆通常就有 cwd;掃到這個行數還沒有就當它沒有,
# 不整檔讀 —— 有些 transcript 是幾十 MB。
SCAN_LINES = 60

SOURCE_BACKFILL = "backfill-transcript"


def _projects_root(override=None):
    if override:
        return override
    env = os.environ.get("EZ2_CLAUDE_PROJECTS_DIR")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def cwd_in_transcript(path, scan_lines=SCAN_LINES):
    """transcript 檔 → 它記的 cwd;沒有回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= scan_lines:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("cwd"):
                    return record["cwd"]
    except OSError:
        return None
    return None


def recover_cwd(uuid, projects_root, scan_lines=SCAN_LINES):
    """(cwd, reason)。**回不出來時 cwd 是 None,而 reason 說得出為什麼。**

    ★ 每一條落空路徑都有自己的 reason:回報時「零筆」與「六筆全部因為
      同一個原因落空」看起來完全不同,而後者幾乎一定是我的探測器壞了。
    """
    if not uuid:
        return None, "no-session-uuid"
    hits = sorted(glob.glob(os.path.join(projects_root, "*", "%s.jsonl" % uuid)))
    if not hits:
        return None, "no-transcript"

    recovered = set()
    for hit in hits:
        cwd = cwd_in_transcript(hit, scan_lines)
        if not cwd:
            continue
        # 自洽檢查:目錄名與檔內 cwd 欄必須是同一個事實。
        if escape_cwd(cwd) != os.path.basename(os.path.dirname(hit)):
            continue
        recovered.add(cwd)

    if not recovered:
        return None, "no-self-consistent-cwd"
    if len(recovered) > 1:
        # 同一個 uuid 的 transcript 落在兩個彼此矛盾的目錄 —— 不挑一個。
        return None, "ambiguous:%d" % len(recovered)
    return recovered.pop(), "transcript"


def plan_seat(record, projects_root, scan_lines=SCAN_LINES):
    """席位紀錄 → (action, cwd, reason)。action ∈ has-cwd / fill / unknown。"""
    if not isinstance(record, dict):
        return "unknown", None, "not-a-record"
    if record.get("cwd"):
        # 開席寫的是權威值,回填**永遠不覆蓋**它。
        return "has-cwd", record["cwd"], record.get("cwd_source") or "seat-open"
    cwd, reason = recover_cwd(record.get("session_uuid"), projects_root,
                              scan_lines)
    if cwd:
        return "fill", cwd, reason
    return "unknown", None, reason


def seat_files(seats_dir):
    try:
        names = sorted(os.listdir(seats_dir))
    except OSError:
        return None
    return [os.path.join(seats_dir, n) for n in names if n.endswith(".json")]


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def apply_cwd(path, cwd):
    """只在 cwd 仍然缺席時寫入 —— 鎖內再判一次,不靠鎖外那次判斷。"""
    def mutate(record):
        if not isinstance(record, dict) or not record.get("schema"):
            return record
        if record.get("cwd"):
            return record
        record["cwd"] = cwd
        record["cwd_source"] = SOURCE_BACKFILL
        return record
    atomic_json.update_json(path, mutate)


def run(seats_dir, projects_root, apply=False, stream=sys.stdout,
        scan_lines=SCAN_LINES):
    paths = seat_files(seats_dir)
    if paths is None:
        stream.write("席位目錄不可讀:%s\n" % seats_dir)
        return 2

    rows = []
    for path in paths:
        record = load(path)
        action, cwd, reason = plan_seat(record, projects_root, scan_lines)
        rows.append((path, (record or {}).get("seat_id"),
                     (record or {}).get("provider"), action, cwd, reason))
        if apply and action == "fill":
            apply_cwd(path, cwd)

    counts = {}
    for row in rows:
        counts[row[3]] = counts.get(row[3], 0) + 1
    stream.write("席位目錄:%s\n" % seats_dir)
    stream.write("模式:%s\n\n" % ("--apply 真寫" if apply else "只報告(預設)"))
    for path, seat, provider, action, cwd, reason in rows:
        stream.write("%-10s %-8s %-8s %-8s %s (%s)\n" % (
            os.path.basename(path), seat or "-", provider or "-", action,
            cwd or "-", reason))
    stream.write("\n合計:%s / 共 %d 席\n" % (
        " ".join("%s=%d" % kv for kv in sorted(counts.items())), len(rows)))
    # 校準端:相異 cwd 個數。全部回同一個值時,這支分不出「查對了」與
    # 「它回一個常數」——數字是 1 就要自己去找一個 cwd 不同的席位再驗一次。
    distinct = {row[4] for row in rows if row[4]}
    stream.write("相異 cwd 個數:%d%s\n" % (
        len(distinct),
        "  ★ 只有一個值,本次執行沒有分辨力" if len(distinct) == 1 else ""))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="EH.3 席位檔 cwd 回填")
    parser.add_argument("--seats-dir")
    parser.add_argument("--projects-root")
    parser.add_argument("--apply", action="store_true",
                        help="真的寫入;預設只報告")
    args = parser.parse_args(argv)
    seats_dir = args.seats_dir or seats_dir_lib.seats_dir()
    return run(seats_dir, _projects_root(args.projects_root), args.apply)


if __name__ == "__main__":
    sys.exit(main())
