#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位檔 socket 欄回填(`aib seat socket-backfill`)。

★ 為什麼需要它(cap `5c59e` 2026-08-29 04:47 裁乙):`socket` 欄是 08-29 才加的,
  而它**只在開席時寫入**。實測全機 86 份席位檔帶該欄的是 0 ——
  於是「缺 socket 就拒投」那個 fail-closed 讀端一合入,全艦 dm 立刻同時斷掉,
  包含用來協調重開席的那條路。本工具讓翻轉條件(`缺 socket 席位檔數 = 0`)
  今晚就能達成,而不是等每一席都重開過。

★ 導出規則寫死在這裡,**而且會印出來**(不是註解裡的承諾):
    <home>/instances/<艙號>/seats/wN.json  =>  socket = cabin_resolve.sh --hub <艙號>
    <home>/seats/wN.json                   =>  socket = cabin_resolve.sh --hub(無參數=主艙)
  其他路徑**不猜**,列進「導不出」並且一個位元組都不動。
  ★ 為什麼不猜:猜錯的表現是「送到了、送給別人、收據 rc=0」——
    那正是這一整批要修的洞。導不出時留白,讓讀端 fail-closed 去擋,
    比填一個看起來合理的值安全。

寫入走 `atomic_json.update_json`(全程持鎖的 read-modify-write):席位檔在活艙裡
每幾秒就被心跳寫一次,直接 read→write 會把那次心跳吃掉。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import atomic_json                                    # noqa: E402

FIELD = "socket"
# ★ 艙名由**單源**產生:`cabin_resolve.sh --hub [<艙號>]`。本檔第一版自己寫
#   自己把前綴與艙號接起來,那正是 R8 反重造掃描要擋的第二處拼艙名,而我合入之後
#   main 上那支臂就紅了 —— 它紅得對:寫死的那一行會在艙名規則改變的那天
#   靜靜給出舊格式,而沒有任何一格會叫。
CABIN_RESOLVE = os.path.join(HERE, "cabin_resolve.sh")
RULES = (
    "<home>/instances/<艙號>/seats/wN.json  =>  socket = cabin_resolve.sh --hub <艙號>",
    "<home>/seats/wN.json                   =>  socket = cabin_resolve.sh --hub(無參數=主艙)",
    "其他路徑                                =>  導不出,不猜、不寫、不動",
)


def _cabin_resolve(*args):
    try:
        return subprocess.run(["bash", CABIN_RESOLVE, *args],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def hub_of(instance=None):
    """向單源要艙名;不給 instance = 問單源自己的主艙。★ 解不出來回 None,**不自己拼**。

    ★ 主艙那一格刻意「不給參數」而不是傳 `"main"`:那個字面是單源內部的常數,
      抄過來就是第二處寫死,而 R8 正是在擋這件事。
    """
    out = _cabin_resolve(*(["--hub", instance] if instance else ["--hub"]))
    if out is None or out.returncode != 0:
        return None
    return (out.stdout or "").strip() or None


def default_home():
    """席位根:**先看 HUB**,經 `cabin_resolve.sh --print` 導出,不硬編主艙路徑。

    ★ 為什麼不能寫 `~/.ez2-aib`(第一版就是那樣):多艙環境下硬編的預設會安靜地
      退回主艙,拿到一個**格式正確的錯答案**——那正是
      tests/test-seats-dir-resolution.py 在擋的形狀(它抓到過我一次)。
    ★ 本工具要掃**所有艙**,所以取的是艙根(含 `instances/` 的那一層):
      單源給的是「我這個艙的 home」,艙根是它去掉 `/instances/<艙號>` 之後那一段。
      方向仍是先問單源再往上,不是自己從家目錄拼下來。
    """
    env = os.environ.get("EZ2_AIB_HOME")
    if env:
        return env
    out = _cabin_resolve("--print")
    if out is not None and out.returncode == 0:
        for line in (out.stdout or "").splitlines():
            if line.startswith("EZ2_AIB_INSTANCE_HOME="):
                home = line.split("=", 1)[1].strip()
                marker = os.sep + "instances" + os.sep
                return home.split(marker)[0] if marker in home else home
    return None


def derive_socket(path, home):
    """由路徑導出 socket;導不出回 None。**只認兩種形狀,其餘一律 None。**"""
    seats_dir = os.path.dirname(os.path.abspath(path))
    if os.path.basename(seats_dir) != "seats":
        return None
    parent = os.path.dirname(seats_dir)
    home = os.path.abspath(home)
    if os.path.abspath(parent) == home:
        return hub_of()
    grand = os.path.dirname(parent)
    if os.path.abspath(grand) == os.path.join(home, "instances"):
        cabin = os.path.basename(parent)
        # 艙號要能組成 tmux socket 名;含奇怪字元的一律當導不出。
        if cabin and all(c.isalnum() or c in "-_" for c in cabin):
            return hub_of(cabin)
    return None


def seat_files(home):
    """主艙那本 + 每個 instances/<艙號>/seats。順序固定,方便逐次比對。"""
    dirs = [os.path.join(home, "seats")]
    inst_root = os.path.join(home, "instances")
    if os.path.isdir(inst_root):
        for name in sorted(os.listdir(inst_root)):
            cand = os.path.join(inst_root, name, "seats")
            if os.path.isdir(cand):
                dirs.append(cand)
    out = []
    for seats_dir in dirs:
        if not os.path.isdir(seats_dir):
            continue
        for name in sorted(os.listdir(seats_dir)):
            if name.startswith("w") and name.endswith(".json"):
                out.append(os.path.join(seats_dir, name))
    return out


def classify(path, home):
    """回 (kind, socket)。kind: 'have' 已有 / 'fill' 可補 / 'skip' 導不出 / 'bad' 讀不了。"""
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return "bad", None
    if not isinstance(rec, dict):
        return "bad", None
    if FIELD in rec:
        return "have", rec.get(FIELD)
    sock = derive_socket(path, home)
    if sock is None:
        return "skip", None
    return "fill", sock


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="aib seat socket-backfill",
        description="把席位檔缺的 socket 欄由路徑補上(預設乾跑)")
    ap.add_argument("--apply", action="store_true",
                    help="真的寫入(預設只乾跑;寫前留 .bak)")
    ap.add_argument("--dry-run", action="store_true",
                    help="乾跑(預設就是乾跑,寫出來是為了讓指令自我說明)")
    ap.add_argument("--home", default=None, help="席位根(預設 $EZ2_AIB_HOME 或 ~/.ez2-aib)")
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        sys.stderr.write("aib seat socket-backfill: --apply 與 --dry-run 互斥\n")
        return 2
    home = args.home or default_home()
    # ★ 單源說不出席位根時**具名拒絕**,不要讓它掉進 os.path.join(None, …)。
    #   qa 0c6a3 08:32 在 open-fleet6 抓到的是同一族:`rc=0 而 stdout 空`
    #   ——shell 只按子程序 rc 判成功,空輸出因此繞過 `||`。
    #   我的 hub_of() 對那一格是安全的(空輸出 -> None -> 導不出、不寫;已實測),
    #   但**這一格**只是碰巧 fail-closed:它是靠 TypeError 停下來的,
    #   而 traceback 看起來像「工具壞了」不像「環境答不出來」,
    #   下一個人會去修工具而不是去修環境。具名拒絕才把方向指對。
    if not home:
        sys.stderr.write(
            "aib seat socket-backfill: 取不到席位根 —— "
            "cabin_resolve.sh --print 沒有給出 EZ2_AIB_INSTANCE_HOME,"
            "且未設 EZ2_AIB_HOME、未給 --home。\n"
            "用法:aib seat socket-backfill [--apply] [--home <席位根>]\n"
            "★ 這裡不猜預設值:猜錯的表現是「補了 socket、補成別的艙」,"
            "而那正是本工具要修的洞。\n")
        return 2

    print("導出規則(路徑->艙號在本檔;艙號->hub 名一律問 cabin_resolve.sh 單源):")
    for line in RULES:
        print("  " + line)
    print("席位根:%s" % home)
    print("模式:%s" % ("APPLY(會寫入)" if args.apply else "DRY-RUN(不寫)"))
    print("")

    files = seat_files(home)
    fill, have, skip, bad, failed = [], [], [], [], []
    for path in files:
        kind, sock = classify(path, home)
        if kind == "have":
            have.append((path, sock))
        elif kind == "fill":
            fill.append((path, sock))
        elif kind == "skip":
            skip.append(path)
        else:
            bad.append(path)

    for path, sock in fill:
        if not args.apply:
            print("[會補] %s  <=  %s" % (path, sock))
            continue
        try:
            shutil.copy2(path, path + ".bak")
            atomic_json.update_json(
                path, lambda rec, s=sock: dict(rec, **{FIELD: s}))
        except Exception as exc:                      # noqa: BLE001
            failed.append((path, exc.__class__.__name__))
            print("[失敗] %s  (%s)" % (path, exc.__class__.__name__))
            continue
        print("[已補] %s  <=  %s" % (path, sock))
    for path in skip:
        print("[導不出] %s  —— 未動" % path)
    for path in bad:
        print("[讀不了] %s  —— 未動" % path)

    n = len(fill) - len(failed)
    print("")
    print("N 補=%d  M 已有=%d  K 導不出=%d  讀不了=%d  失敗=%d  總數=%d"
          % (n if args.apply else 0, len(have), len(skip), len(bad),
             len(failed), len(files)))
    if not args.apply and fill:
        print("★ 這是乾跑,一個位元組都沒改。要真的補:加 --apply")
    # ★ 失敗也要有出口碼,不能只印一行字:失敗與全過在畫面上會很像。
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
