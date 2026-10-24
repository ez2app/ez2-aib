#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# classify_env_blocked v1.2.0 — 把測試輸出的 FAIL 拆成「被環境擋住」與「真的紅」
#
# 10-11 從舊協作板 scripts/classify-env-blocked.py 搬回 AIB(A4,the maintainer 10-11 直令)。改了三處,其餘逐字:
#   1. 結果行不限兩格縮排:`PASS 名稱` / `  FAIL 名稱` 都認(AIB 的 check() 式測試不縮排)
#   2. 探針暫存目錄前綴改成 aib-envprobe-
#   3. 用法路徑改成 tools/verify/classify_env_blocked.py;清單與參考檔由呼叫端給,本工具不預設任何路徑
#   4. 印給人看的字走 i18n(aib.env_blocked.*,四語)
#
# 為什麼要有這個(舊板 2026-08-01):
#   一支測試在本機現況環境 PASS=97 FAIL=78,
#   在乾淨環境 PASS=172 FAIL=3。同一份程式碼、同一台機器,差在環境。
#   長期下來這 78 紅變成背景音:沒有人分得出哪幾條是「環境沒配好」、
#   哪幾條是「真的有 bug」,於是兩種都被一起忽略——這正是假紅燈久了真紅燈也沒人看。
#
#   這支工具給第三種狀態一個名字:ENV_BLOCKED = 被環境擋住,沒測到。
#   它不是通過,也不是缺陷。三條原則由本工具強制,不靠人自律:
#
#     1. 絕不計入通過   ENV_BLOCKED 單獨計數,永遠不加進 PASS。
#     2. 大於 0 即非零   只要有一條被擋住,退出碼就不是 0。沒測到就是沒測到。
#     3. acknowledge 需吻合數量
#                      清單裡的 ACK_COUNT 必須與實際分類數完全相等,多一條少一條都紅。
#                      擋的是「悄悄長大」與「清單過期」兩種漂移。
#
#   ★最重要的一條是資格條件:一條測試只有在乾淨環境參考結果裡是 PASS,
#     才有資格被標成 ENV_BLOCKED。兩種環境都紅的東西是缺陷,不得靠標記消音。
#     沒有這條,這個機制就只是一個「把不想看的紅燈藏起來」的開關。
#
#   ★v1.1.0 資格第二條:環境探針(舊板 2026-08-01)。
#     「環境擋住」不能只靠人宣稱,要機器當場量得到:
#       探針 locale — 在 ambient 環境(刻意不釘 LC_ALL)跑 /bin/ps -o lstart=,
#                     若 strptime C 格式解析不了,代表未釘語系的解析會垮。
#       探針 umask  — 目前 umask 會讓新建目錄帶 group/world write bit,
#                     authority/writer 的祖先檢查會(正確地)整棵拒絕。
#     本次執行若分類出 ENV_BLOCKED 而沒有任何探針觸發,清單無效(exit 4):
#     探針全綠代表環境是乾淨的,那些紅就是真紅,不得靠清單消音。
#
# 用法:
#   python3 tools/verify/classify_env_blocked.py <測試輸出log> \
#       --list <ENV_BLOCKED 清單(每行一個測試名 + ACK_COUNT: N)> \
#       --reference <乾淨環境參考結果(每行 PASS|FAIL<TAB>名稱)>
#   python3 tools/verify/classify_env_blocked.py --probes-only
#       只跑環境探針(給測試 helpers 或人肉盤查用):全綠 exit 0,有觸發 exit 3。
#
# 退出碼:
#   0  沒有真紅,也沒有 ENV_BLOCKED(--probes-only 時=探針全綠)
#   1  有真紅(REAL_FAIL > 0)
#   3  沒有真紅,但有 ENV_BLOCKED(原則 2);--probes-only 時=有探針觸發
#   4  清單本身無效:ACK_COUNT 不符、標了沒資格的項目、或分類出 ENV_BLOCKED
#      而探針全綠(原則 3 與兩條資格條件)
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cockpit"))
import i18n_lib  # noqa: E402


def T(key, params=None):
    return i18n_lib.t("aib.env_blocked." + key, params, locale=i18n_lib.resolve_locale())

RESULT_RE = re.compile(r"^\s*(PASS|FAIL)\s+(.+?)(?:\s+\(exp:.*)?$")


def parse_log(path):
    """回傳 {測試名: 狀態}。同名重複時,只要有一次 FAIL 就算 FAIL。"""
    out = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            m = RESULT_RE.match(line.rstrip("\n"))
            if not m:
                continue
            status, name = m.group(1), m.group(2).strip()
            if out.get(name) == "FAIL":
                continue
            out[name] = status
    return out


def parse_list(path):
    """回傳 (名稱集合, ACK_COUNT 或 None)。"""
    names, ack = [], None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("ACK_COUNT:"):
                ack = int(stripped.split(":", 1)[1].strip())
                continue
            names.append(stripped)
    return names, ack


def parse_reference(path):
    ref = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or "\t" not in line:
                continue
            status, name = line.rstrip("\n").split("\t", 1)
            ref[name.strip()] = status.strip()
    return ref


def probe_locale():
    """探針 locale:ambient 環境下未釘 LC_ALL 的 ps lstart 是否不可解析。

    刻意繼承呼叫者的環境跑 ps——量的就是「沒釘語系的程式碼在這台機器會不會垮」。
    觀測不到(pid 消失、ps 失敗、輸出為空)一律不觸發:探針只在有證據時說話,
    不得憑空替人定性環境紅。
    """
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(os.getpid()), "-o", "lstart="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return False
    value = " ".join(result.stdout.split())
    if result.returncode != 0 or not value:
        return False
    try:
        time.strptime(value, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return True
    return False


def probe_umask():
    """探針 umask:目前 umask 是否會讓新建目錄帶 group/world write bit。"""
    current = os.umask(0)
    os.umask(current)
    return (current & 0o022) != 0o022


def _fixture_root():
    """治具實際會落腳的目錄(與 tests 的 mktemp -d 同源)。"""
    return os.environ.get("TMPDIR") or "/tmp"


def probe_created_dir(path=None):
    """探針 dirmode:照治具的方式**實際建一個目錄**,量它拿到的 mode。

    量的是結果不是設定值。與 probe_umask 的差別:
      probe_umask 讀 umask 值(proxy);本探針建出目錄再 stat(觀測)。
      umask 以外的成因——預設 ACL、繼承 setgid、掛載選項——只有觀測抓得到。

    ★對齊真正的判準(舊板 2026-08-01 實查當時的 session_authority `_safe_directory`):
      它只檢查**傳進去的那個目錄本身**
      (descendants 由 openat 從釘住的 fd 開,不重走絕對路徑),
      **不會沿祖先鏈往上走到根**。
      所以本探針只量「新建目錄自己的 mode」,不去掃 /tmp 那類共用祖先——
      我第一版寫成掃到根,結果 /private/tmp=1777 讓探針永遠觸發,
      資格第二條就會變成永遠有藉口、完全沒牙。探針要量與失敗同一個東西,不是量更多東西。

    回傳 (是否觸發, 說明)。建不出來一律不觸發:探針只在有證據時說話。
    """
    root = path or _fixture_root()
    probe_dir = None
    try:
        probe_dir = tempfile.mkdtemp(prefix="aib-envprobe-", dir=root)
        inner = os.path.join(probe_dir, "d")
        os.mkdir(inner)                      # 與治具同樣用 mkdir,吃同一個 umask
        mode = stat.S_IMODE(os.stat(inner).st_mode)
    except OSError as exc:
        return False, T("probe_mkdir_failed", {"root": root, "errno": exc.errno})
    finally:
        if probe_dir:
            shutil.rmtree(probe_dir, ignore_errors=True)
    if mode & 0o022:
        return True, T("probe_dir_mode_writable", {"mode": "%04o" % mode})
    return False, T("probe_dir_mode", {"mode": "%04o" % mode})


def run_probes():
    """回傳 [(名稱, 是否觸發, 說明)],順序固定。"""
    umask_value = os.umask(0)
    os.umask(umask_value)
    dirmode_hit, dirmode_detail = probe_created_dir()
    return [
        ("locale", probe_locale(), T("probe_locale")),
        ("umask", probe_umask(), "umask=%04o" % umask_value),
        ("dirmode", dirmode_hit, dirmode_detail),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?")
    ap.add_argument("--list")
    ap.add_argument("--reference")
    ap.add_argument("--probes-only", action="store_true")
    args = ap.parse_args()

    if args.probes_only:
        probes = run_probes()
        print(T("probes_head"))
        for name, triggered, detail in probes:
            print("    %-7s %s  (%s)" % (name, "triggered" if triggered else "clear", detail))
        return 3 if any(t for _, t, _ in probes) else 0

    if not args.log or not args.list or not args.reference:
        print(T("need_args"))
        return 4

    for path in (args.log, args.list, args.reference):
        if not os.path.exists(path):
            print(T("no_file", {"path": path}))
            return 4

    results = parse_log(args.log)
    marked, ack = parse_list(args.list)
    reference = parse_reference(args.reference)

    # ── 資格條件:被標記的項目必須在乾淨環境是 PASS ──
    # 這是整個機制唯一的擋門。沒有它,任何人把真紅寫進清單就能讓它消失。
    ineligible = []
    for name in marked:
        ref_status = reference.get(name)
        if ref_status is None:
            ineligible.append((name, T("inel_missing")))
        elif ref_status != "PASS":
            ineligible.append((name, T("inel_also_red", {"status": ref_status})))

    marked_set = set(marked)
    failed = {n for n, s in results.items() if s == "FAIL"}
    passed = {n for n, s in results.items() if s == "PASS"}

    env_blocked = sorted(failed & marked_set)
    real_fail = sorted(failed - marked_set)
    not_triggered = sorted(marked_set & passed)

    probes = run_probes()
    print(T("probes_head"))
    for name, triggered, detail in probes:
        print("    %-7s %s  (%s)" % (name, "triggered" if triggered else "clear", detail))

    # ★原則 1:ENV_BLOCKED 單獨計數,永遠不併進 PASS。
    #   marked 但這次是綠的(not_triggered)本來就是真的通過,照常計入 PASS——
    #   那代表環境變乾淨了,是好事,不是清單過期。
    print(T("result_head"))
    print("    PASS        %d" % len(passed))
    print(T("count_env_blocked", {"n": len(env_blocked)}))
    print("    REAL_FAIL   %d" % len(real_fail))
    if not_triggered and env_blocked:
        print(T("not_triggered", {"n": len(not_triggered)}))

    if ineligible:
        print(T("inel_head"))
        for name, why in ineligible:
            print("      %s  <- %s" % (name, why))
        print(T("inel_rule", {"ref": os.path.basename(args.reference)}))
        print(T("inel_why"))
        return 4

    # ── 資格第二條:分類出 ENV_BLOCKED 就必須有探針當場觸發 ──
    # 探針全綠代表這台機器現在是乾淨環境,「被環境擋住」無從成立,
    # 那些紅就是真紅——不得靠清單消音(這正是本機制最危險的濫用路徑)。
    if env_blocked and not any(t for _, t, _ in probes):
        print(T("clean_env_head", {"n": len(env_blocked)}))
        print(T("clean_env_why"))
        for n in env_blocked:
            print("      %s" % n)
        return 4

    if ack is None:
        print(T("no_ack"))
        return 4

    # ── 原則 3:acknowledge 需吻合數量 ──
    # 只在「這個環境真的擋住了東西」時比對。乾淨環境跑出 0 是理想狀態,
    # 不該因為「跟清單宣告的 76 不一樣」而變成錯誤——那會逼人為了讓工具閉嘴而改清單。
    if env_blocked and len(env_blocked) != ack:
        print(T("ack_mismatch", {"ack": ack, "n": len(env_blocked)}))
        print(T("ack_why"))
        print(T("ack_fix"))
        return 4

    if real_fail:
        print(T("real_fail_head", {"n": len(real_fail)}))
        for n in real_fail:
            print("      %s" % n)
        return 1

    if env_blocked:
        # ── 原則 2:大於 0 即非零 ──
        print(T("env_blocked_head", {"n": len(env_blocked)}))
        print(T("env_blocked_fix", {"ref": os.path.basename(args.reference)}))
        return 3

    print(T("all_pass"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
