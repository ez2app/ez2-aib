#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ED.3 `aib doctor`:逐項體檢,每一項都回**機器可判的結果**。

「能直接使用」的定義就是 doctor 全綠,所以本工具不能只印訊息——印訊息的體檢
等於把判斷丟回給讀的人,而讀的人正是那個還不知道系統能不能用的人。

三種結果,`manual` 不是第三種綠:
  ok      機器已確認通過
  fail    機器已確認不通過,附可執行的補救
  manual  **機器無法確認,必須由真人做**(例如 provider 登入)
          —— 計為未通過。不假裝能修,也不因為修不了就跳過不算。
          今晚的教訓:守衛可以被證明有作用,同時作用在錯的東西上;
          一個把 manual 算成綠的 doctor,就是作用在錯的東西上。

唯讀:本工具**不建立、不修改任何檔案**。要建目錄是 ED.2 `aib init` 的事。

狀態根以可注入方式取得(參數 > 環境變數 > 預設)。**刻意不改既有 9 處硬編路徑**
——那是 ED.1 的獨佔範圍,現在動會與未來的 ED.1 互撞(pm 裁決)。

exit: 0 全部 ok · 1 有 fail 或 manual · 2 用法錯
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_STATE_ROOT = "~/.ez2-aib"
MIN_PYTHON = (3, 9)

OK, FAIL, MANUAL = "ok", "fail", "manual"


def _result(check_id, status, detail, remedy=""):
    return {"id": check_id, "status": status, "detail": detail, "remedy": remedy}


def check_python():
    v = sys.version_info
    got = "%d.%d.%d" % (v.major, v.minor, v.micro)
    if (v.major, v.minor) >= MIN_PYTHON:
        return _result("python_version", OK, got)
    return _result("python_version", FAIL, got,
                   "需要 Python %d.%d 以上" % MIN_PYTHON)


def check_git():
    path = shutil.which("git")
    if not path:
        return _result("git_present", FAIL, "找不到 git",
                       "安裝 git 後重跑")
    return _result("git_present", OK, path)


def check_state_root(root):
    path = Path(root)
    if not path.exists():
        return _result("state_root_exists", FAIL, "不存在:%s" % path,
                       "跑 aib init 建立狀態根")
    if not path.is_dir():
        return _result("state_root_exists", FAIL, "不是目錄:%s" % path,
                       "移走該檔後重跑 aib init")
    return _result("state_root_exists", OK, str(path))


def check_state_root_perms(root):
    """group/world 可寫的狀態根 = 同機任何人都能改帳本與收據。"""
    path = Path(root)
    if not path.is_dir():
        return _result("state_root_perms", FAIL, "狀態根不存在,無法判定",
                       "先讓 state_root_exists 通過")
    mode = path.stat().st_mode
    bad = []
    if mode & stat.S_IWGRP:
        bad.append("group 可寫")
    if mode & stat.S_IWOTH:
        bad.append("world 可寫")
    shown = stat.filemode(mode)
    if bad:
        return _result("state_root_perms", FAIL,
                       "%s(%s)" % (shown, "、".join(bad)),
                       "chmod g-w,o-w %s" % path)
    return _result("state_root_perms", OK, shown)


def check_writable(root):
    """真的寫得進去才算,不是看權限位元推測。唯讀原則的例外:寫完立刻刪。"""
    path = Path(root)
    if not path.is_dir():
        return _result("state_root_writable", FAIL, "狀態根不存在", "先跑 aib init")
    probe = path / ".aib-doctor-write-probe"
    try:
        fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(str(probe))
    except OSError as err:
        return _result("state_root_writable", FAIL, str(err),
                       "檢查 %s 的擁有者與權限" % path)
    return _result("state_root_writable", OK, "可寫且探針已清除")


def check_ledger(root):
    """帳本在就要能通過鏈驗證。複用 E1.1,不另造第二套驗證器(反重造)。"""
    ledger = Path(root) / "ledger" / "aib-ledger.jsonl"
    if not ledger.exists():
        return _result("ledger_chain", FAIL, "帳本不存在:%s" % ledger,
                       "跑 aib init 建立空帳本")
    src = Path(__file__).resolve().parents[1] / "ledger"
    sys.path.insert(0, str(src))
    try:
        from ledger import Ledger
        report = Ledger(str(ledger)).verify_chain()
    except Exception as err:      # noqa: BLE001 — 不可信輸入決定這裡能拋什麼
        return _result("ledger_chain", FAIL,
                       "%s: %s" % (type(err).__name__, str(err)[:120]),
                       "帳本損壞,需人工裁決是否截斷或重建")
    if report.get("ok"):
        return _result("ledger_chain", OK, "鏈驗證通過(%d 筆)" % report["count"])
    err = report.get("error") or {}
    return _result("ledger_chain", FAIL,
                   "index %s %s" % (err.get("index"), err.get("code")),
                   "帳本鏈斷裂,需人工裁決")


def check_schemas(repo_root):
    """schema 是驗證的正本;讀不到就沒有任何『合格』是可信的(fail-closed)。"""
    schema_dir = Path(repo_root) / "schemas"
    if not schema_dir.is_dir():
        return _result("schemas_readable", FAIL, "找不到 schemas/",
                       "確認在 repo 根目錄執行")
    broken = []
    count = 0
    for f in sorted(schema_dir.glob("*.json")):
        count += 1
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            broken.append("%s(%s)" % (f.name, str(err)[:40]))
    if not count:
        return _result("schemas_readable", FAIL, "schemas/ 下沒有任何 .json",
                       "檢查 checkout 是否完整")
    if broken:
        return _result("schemas_readable", FAIL, "、".join(broken),
                       "修正壞掉的 schema 檔")
    return _result("schemas_readable", OK, "%d 份可解析" % count)


def check_provider_cli(name):
    """CLI 在不在是機器可判的;**有沒有登入不是**,所以那件回 manual。"""
    path = shutil.which(name)
    if not path:
        return _result("provider_cli_%s" % name, FAIL, "找不到 %s" % name,
                       "安裝 %s CLI" % name)
    return _result("provider_cli_%s" % name, OK, path)


def check_provider_login(name):
    return _result(
        "provider_login_%s" % name, MANUAL,
        "機器無法確認 %s 是否已登入" % name,
        "請你自己跑一次 %s 並完成登入;doctor 不會、也不該代你登入" % name)


# 漂移判準的基準線。★ 不是「這棵樹的檔案」——見 check_release_gate_hook 檔頭那段。
HOOK_BASELINE_REF = "origin/main"


def check_release_gate_hook(repo_root):
    """發版 tag 閘裝上了沒。

    ## 為什麼這一項非有不可

    交付進 repo **不等於**啟用。08-15 的實況:`release-tag-gate.sh` 與 25 支
    測試全綠,而 `core.hooksPath` 底下只有 `pre-commit`——**下一次
    `git push origin v0.20.0` 不會被擋,而所有燈都是綠的。**
    整刀的價值在那個狀態下是 0,卻沒有任何東西會說出來。

    ## 為什麼是 doctor 不是單元測試

    「裝了沒」是**操作狀態**不是程式碼性質。CI runner 與任何一份新 clone 都
    不會裝 hook,寫成單元測試等於讓 CI 永遠紅——**而永遠紅的測試會被關掉,
    關掉之後連旁邊那幾根一起沒**(本 repo 已有實證:`test-eye-ui-launch`
    的「撞到就重跑」被寫進文件,等於制度化了「這支不可信」)。

    ## 為什麼「沒裝」是 MANUAL 而「裝了但是舊的」是 FAIL(2026-09-07 分開)

    機器測得出「沒裝」,但**裝上去是人的動作**——doctor 不替人改 hook 設定,
    那是跨所有 worktree 的共用狀態。MANUAL 不計入通過,所以它不會被當成綠。

    ★ 但那句話講的是**誰去修**,而「誰去修」對兩種狀態一模一樣(都是人跑
      `install-hooks.sh install`)⇒ 它分不出這兩件事。真正讓「沒裝」該是 MANUAL
      的是另一件:**「沒裝」有合法實例**——CI runner 與每一份新 clone 都沒裝;
      對它判 FAIL 等於製造一個永遠紅的格,而永遠紅的格會被關掉(本檔上面那段
      已經寫過這個判例)。
    ★ **「裝了但是舊的」一個合法實例都沒有**:新 clone 是「沒裝」,跑過安裝器是
      「相同」;漂移只能來自(a)正本在安裝之後又改了、(b)有人手改 hooks 目錄
      ——後者依 `install-hooks.sh` 檔頭是「繞過版控」。
    ★★ 而且兩種失效的危險方向相反:**「沒裝」是大聲且安全地失效**(沒有 hook 跑,
      沒有東西宣稱通過);**「裝了但是舊的」是安靜且危險地失效**(hook 跑了、
      印了自己的成功、操作者相信那道閘跑過)。09-07 實例:實裝的 pre-push 少了
      `--gate-new-unlabeled-pinned`,那道閘對每一次 main push 都是隱形的,分岔五天,
      而本項在那五天裡一直是 OK。**比較危險的那一種不該拿到比較軟的碼。**

    ## 為什麼判準是 `origin/main` 的 blob,不是這棵樹的檔案

    「版控正本」在不同樹上不是同一份東西:09-07 抽樣 40 棵 worktree 的
    `pre-push-adapter.sh` 得到 **4 種 blob**(21/13/4/2)。用「等於我這棵樹那份」
    當判準,40 棵裡有 27 棵會紅 —— 那種人人都紅的格會被關掉,等於白做。
    `origin/main` 不隨你在哪棵樹跑而變,而 main 正是所有 push 真正被擋的那條線。
    ★ 已具名盲區:本機 `origin/main` 可能是舊的(本項**不 fetch**)。那會讓判準
      晚一步發現「正本剛改」,但**不會把漂移說成相同** ——
      它失效時往「多問一次人」倒,不往「說沒事」倒。
    ★ 比對與槽對照表**只有一份**,在 `src/policy/hooks/install-hooks.sh`;
      本項只讀它的 `--porcelain` 狀態字。doctor 自己再比一次就是第二份,
      而第二份會在槽表變動時靜默過期。
    """
    gate = Path(repo_root) / "src" / "policy" / "hooks" / "release-tag-gate.sh"
    installer = Path(repo_root) / "src" / "policy" / "hooks" / "install-hooks.sh"
    if not gate.is_file():
        return _result("release_gate_hook", FAIL, "閘門腳本不在 repo:%s" % gate,
                       "確認 src/policy/hooks/release-tag-gate.sh 有沒有被誤刪")
    if not installer.is_file():
        return _result("release_gate_hook", FAIL, "安裝器不在 repo:%s" % installer,
                       "確認 src/policy/hooks/install-hooks.sh 有沒有被誤刪")

    fix = "在這棵樹上跑 src/policy/hooks/install-hooks.sh install"
    try:
        proc = subprocess.run(
            ["bash", str(installer), "verify",
             "--baseline", HOOK_BASELINE_REF, "--porcelain"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        return _result("release_gate_hook", FAIL, "安裝器跑不起來:%s" % err, fix)

    states = {}
    for line in proc.stdout.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 4:
            states[parts[0]] = parts[1:]
    row = states.get("pre-push")
    if row is None:
        # 單源沒有報這一槽 = 我們什麼都沒量到,不是通過。
        return _result("release_gate_hook", MANUAL,
                       "安裝器沒有報 pre-push 這一槽(rc=%s):%s"
                       % (proc.returncode, (proc.stderr or "").strip()[:200]),
                       fix)
    state, baseline_sha, installed_sha = row
    if state == "same":
        return _result("release_gate_hook", OK,
                       "pre-push 與 %s 的正本逐位元組相同(%s)"
                       % (HOOK_BASELINE_REF, baseline_sha))
    if state == "missing":
        return _result("release_gate_hook", MANUAL,
                       "pre-push 未安裝(hooks 目錄裡沒有這一支)",
                       "裝上發版閘(沒裝的話 tag 推上去不會被擋):%s" % fix)
    if state == "no-baseline":
        # ★ 量不到不是 OK 也不是 FAIL:本機沒有那個 ref(新 clone 沒 fetch 過)。
        return _result("release_gate_hook", MANUAL,
                       "基準 %s 問不到,本項這一輪量不到" % HOOK_BASELINE_REF,
                       "git fetch origin,再跑一次 doctor")
    if state == "drift":
        return _result("release_gate_hook", FAIL,
                       "pre-push 裝出去那份與 %s 的正本不同:正本 %s 實裝 %s"
                       % (HOOK_BASELINE_REF, baseline_sha, installed_sha),
                       fix)
    if state == "not-executable":
        return _result("release_gate_hook", FAIL,
                       "pre-push 位元組相同但沒有執行權 —— 裝了等於沒裝", fix)
    return _result("release_gate_hook", FAIL,
                   "pre-push 狀態 %s(安裝器 rc=%s)" % (state, proc.returncode), fix)


def check_seat_files(state_root, repo_root):
    """席位檔結構體檢——`src/cockpit/seat_guard.py` 的唯一生產呼叫點。

    ★ 為什麼接在 doctor,不接在開席流程(08-16 初審高-1,the maintainer 裁):
      `aib` 的 `write_seat_record` **結構上從來不寫 session_uuid**,那一欄由
      `seat-detect.py --apply` 事後回填,而 seat-detect 在候選不唯一時是
      **刻意不寫**的(「錯綁比空值更糟」)。把守衛掛在寫入後並開
      `--require-uuid`,會對一條合法路徑次次判紅——**用一個恆紅的閘去擋合法
      行為,就是這道守衛原本要解決的病本身**。故走預設模式,且不擋開席。

    ★★ 它真正有價值的工作不是抓缺 uuid,是**抓被右欄靜默跳過的壞席位檔**:
      面板遇到壞 JSON / schema 不符 / 檔名對不上時走 `continue` + `note()`,
      而 note() 的輸出被導進暫存檔、**只有渲染失敗時才會被放出來**,渲染成功
      時直接刪掉。也就是說一份壞掉的席位檔今天會安靜地從畫面上消失,沒有任何
      人看得到。那是面板結構上做不到的事,守衛做得到——但前提是有人跑它。
      在此之前守衛**零生產呼叫點**,全庫只有測試在叫它。
    """
    sdir = Path(state_root).expanduser() / "seats"
    if not sdir.is_dir():
        # 還沒開過席=沒有席位檔可驗,這不是缺陷。但要把「本項什麼都沒檢查」
        # 講出來,不能只回一個看起來像通過的 ok。
        return _result("seat_files", OK,
                       "席位目錄尚未建立(還沒開過席),本項未檢查任何東西:%s"
                       % sdir)
    cockpit = Path(repo_root) / "src" / "cockpit"
    try:
        if str(cockpit) not in sys.path:
            sys.path.insert(0, str(cockpit))
        import seat_guard
    except ImportError as err:
        return _result("seat_files", FAIL, "載入 seat_guard 失敗:%s" % err,
                       "確認 %s/seat_guard.py 存在且可 import" % cockpit)
    paths = sorted(sdir.glob("w*.json"))
    if not paths:
        # 與 seat_guard 的高-2 修法同一條紀律:0 份是合法狀態(全新 instance),
        # 不判紅;但**不得長得像通過**。
        return _result("seat_files", OK,
                       "目錄存在但 0 份 w*.json,本項未檢查任何東西:%s" % sdir)
    bad = []
    for path in paths:
        fail, code, detail = seat_guard.check_file(str(path))
        if fail:
            bad.append("%s [%s] %s" % (path.name, code, detail))
    if bad:
        return _result("seat_files", FAIL,
                       "%d/%d 份席位檔有結構性缺陷:%s"
                       % (len(bad), len(paths), ";".join(bad)),
                       "逐檔詳情:python3 %s/seat_guard.py --seats %s"
                       % (cockpit, sdir))
    return _result("seat_files", OK, "%d 份席位檔結構完好:%s" % (len(paths), sdir))


def _secrets_mod(repo_root):
    boot = Path(repo_root) / "src" / "bootstrap"
    if str(boot) not in sys.path:
        sys.path.insert(0, str(boot))
    import aib_secrets
    return aib_secrets


def check_secrets_perms(state_root, repo_root):
    """金鑰櫃權限。太寬=紅。目錄還沒建=尚未使用,不判紅。"""
    S = _secrets_mod(repo_root)
    env = dict(os.environ)
    env["EZ2_AIB_STATE_ROOT"] = str(Path(state_root).expanduser())
    d = S.secrets_dir(env)
    if not d.is_dir():
        return _result("secrets_perms", OK,
                       "金鑰目錄尚未建立,本項未檢查任何東西:%s" % d)
    bad = []
    if S._too_open(d, S.DIR_MODE):
        bad.append("%s 目錄權限不是 0700" % d)
    for path in sorted(d.rglob("*")):
        if path.is_file():
            if S._too_open(path, S.FILE_MODE):
                bad.append("%s 檔權限不是 0600" % path.relative_to(d))
        elif path.is_dir():
            if S._too_open(path, S.DIR_MODE):
                bad.append("%s 目錄權限不是 0700" % path.relative_to(d))
    if bad:
        return _result("secrets_perms", FAIL, "; ".join(bad[:8]),
                       "chmod 0700 目錄、chmod 0600 檔:%s" % d)
    return _result("secrets_perms", OK, "目錄與檔權限皆為 0700/0600")


def check_secrets_feature(state_root, repo_root, feature):
    """缺行只停該功能。全空=未啟用。權限太寬交給 secrets_perms。"""
    S = _secrets_mod(repo_root)
    env = dict(os.environ)
    env["EZ2_AIB_STATE_ROOT"] = str(Path(state_root).expanduser())
    cid = "secrets_%s" % feature.replace("-", "_")
    spec = S.FEATURES.get(feature)
    if not spec:
        return _result(cid, OK, "沒有這個功能")
    rel, required = spec
    d = S.secrets_dir(env)
    if not d.is_dir():
        return _result(cid, OK, "金鑰目錄尚未建立,未啟用 %s" % feature)
    path = d / rel
    if path.is_file() and S._too_open(path, S.FILE_MODE):
        return _result(cid, OK, "檔權限太寬,見 secrets_perms")
    try:
        rows = S.load_file(rel, env)
    except S.SecretError as err:
        if err.reason == S.PERM_TOO_OPEN:
            return _result(cid, OK, "檔權限太寬,見 secrets_perms")
        return _result(cid, OK, "讀不到 %s,未啟用" % rel)
    filled = [n for n in required if rows.get(n)]
    missing = [n for n in required if not rows.get(n)]
    if not filled:
        return _result(cid, OK, "未啟用 %s(必填行全空)" % feature)
    if missing:
        return _result(cid, MANUAL,
                       "%s 已啟用但缺 %s" % (feature, "、".join(missing)),
                       "在 %s 填上缺的行名(只停這一個功能)" % (d / rel))
    return _result(cid, OK, "%s 必填行齊" % feature)


def _feature_policy_mod(repo_root=None):
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    cockpit = root / "src" / "cockpit"
    if str(cockpit) not in sys.path:
        sys.path.insert(0, str(cockpit))
    import feature_policy
    return feature_policy


def check_feature_policy(env=None, repo_root=None):
    """功能區各件的開關與授權(第 47 項)。

    ★ **關著不是紅。** 功能區每一件預設就是關的,那是正常狀態;把「關著」
    判成 fail,等於要求每一台機器都把所有東西打開才算健康,而 doctor 一紅
    就沒有人會再看它。紅只留給一種情形:**開關檔在那裡但讀不懂** ——
    那時使用者以為自己開著的東西其實是關的,兩者在畫面上同形。

    量不到艙家也不是紅(治具、未開艙的殼都是這樣),但要**說出來**。
    """
    try:
        FP = _feature_policy_mod(repo_root)
    except Exception as err:  # noqa: BLE001
        return _result("feature_policy", FAIL, "載入功能區開關失敗:%s" % err,
                       "確認 src/cockpit/feature_policy.py")
    st = FP.status(env)
    how = st["policy_how"]
    on = "、".join(st["on"]) or "無"
    off = "、".join(st["off"]) or "無"
    base = "艙 %s・方案 %s・開著:%s・關著:%s(%s)" % (
        st["cabin"], st["plan"], on, off, st["off_means"])
    if FP.lacks_plus(env, how):
        # 10-01:基本版時畫面上功能區什麼都不畫;這裡一行說出是「要 Plus 授權」,不是關著。
        base = "%s・%s" % (base, FP.plus_needed_notice())
    if how == "unreadable":
        # ★ 這一句與畫面那一列**共用同一份字面**(FP.unreadable_notice)——
        #   兩邊各寫一份的話,改了一邊而另一邊沒改,使用者看到的兩個說法會不一樣。
        return _result("feature_policy", FAIL,
                       "%s:%s" % (FP.unreadable_notice(), st["policy_path"]),
                       "刪掉或修好 %s(格式 schema=%s);在那之前你以為開著的"
                       "功能其實是關的" % (st["policy_path"], FP.POLICY_SCHEMA))
    if how == FP.R_NO_HOME:
        return _result("feature_policy", OK,
                       "量不到艙家,功能區當沒開(%s)" % base)
    if how == "missing":
        return _result("feature_policy", OK, "還沒設過開關,三件都是關的・%s" % base)
    return _result("feature_policy", OK, base)


def check_handoff_fields(state_root, repo_root):
    """最新那份交接單解不解得出六格(第 41b 項,艦長 09-12 05:2x)。

    ★ doctor 只有 ok / fail / manual 三種狀態(tests/test-aib-doctor.py 釘死狀態集合),沒有「黃」。
      艦長 09-12 05:25 裁:**不加第四態** —— 第四態是 doctor 全域改動(exit 語意、印表、既有尺),
      不值得為這一格動;「黃」做成 ok + detail 以 △ 開頭說「舊單(標題層)」。fail 只留給
      「最新那份連六格都解不出」與「解析器載不到」—— 那才是接手的人會被誤導的情形。
    ★ 「最新」= state_root/pending/HANDOFF-*.md 裡 mtime 最新的一份(印出名字,讓人對得回去);
      沒有任何一份 ⇒ ok 但說出來(治具、新機器都是這樣)。
    """
    pend = Path(state_root).expanduser() / "pending"
    # ★ 不寫成單行條件式配空 list:tests/test-provider-registry.py 用「else 後接方括號」的字面抓本檔 main() 的
    #   預設 provider 清單(取第一個命中),這裡先出現就把它抓成空集合(live24 實紅;連註解裡的字面也算)。
    files = []
    if pend.is_dir():
        files = sorted(pend.glob("HANDOFF-*.md"), key=lambda p: p.stat().st_mtime)
    if not files:
        return _result("handoff_fields", OK, "沒有交接單可驗(%s 下無 HANDOFF-*.md)" % pend)
    latest = files[-1]
    tool = Path(repo_root) / "tools" / "dispatch" / "handoff_fields.py"
    if not tool.is_file():
        return _result("handoff_fields", FAIL, "解析器不在:%s" % tool,
                       "確認 tools/dispatch/handoff_fields.py 在這棵樹裡(第 41 項)")
    try:
        proc = subprocess.run([sys.executable, str(tool), str(latest), "--json", "--strict-fixed"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        return _result("handoff_fields", FAIL, "解析器跑不動:%s" % err, "python3 %s %s --strict-fixed" % (tool, latest))
    rc = proc.returncode
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        data = {}
    missing = "、".join(data.get("missing") or []) or "無"
    if rc == 0:
        return _result("handoff_fields", OK, "%s:固定層六格齊(接自 @%s)" % (latest.name, data.get("from_seat")))
    if rc == 4:
        return _result("handoff_fields", OK,
                       "△ 舊單(標題層):%s 六格解得出但沒有 handoff-fields 圍欄;下一份請照 docs/handbook/HANDOFF-FIELDS.md 寫圍欄"
                       % latest.name)
    if rc == 3:
        return _result("handoff_fields", FAIL, "%s 缺格:%s" % (latest.name, missing),
                       "補齊六格再交棒;python3 %s %s --strict-fixed" % (tool, latest))
    return _result("handoff_fields", FAIL, "%s 不像交接單或解析器 rc=%s:%s" % (latest.name, rc, proc.stderr.strip()[:200]),
                   "python3 %s %s --strict-fixed" % (tool, latest))


def _pct(values, q):
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * q))))
    return xs[k]


def check_dm_latency(state_root, repo_root, env=None, now=None):
    """第 70 項 P6 後半(需求 §八 2):每席信的 sent_at→delivered_at / →read_at 的 p50/p95 與最舊未讀,每天印得出來的數字。
    沒有樣本印「量不到」但不算失敗(沒信不是病);佇列裡有壞檔才 FAIL。歷史 18–32 分只作事故紀錄,不當比較基準。"""
    env = dict(os.environ if env is None else env)
    env.setdefault("EZ2_AIB_STATE_ROOT", str(state_root))
    now = int(time.time() if now is None else now)
    try:
        sys.path.insert(0, str(Path(repo_root) / "src" / "cockpit"))
        import dm_queue as Q
    except Exception as exc:  # noqa: BLE001
        return _result("dm_latency", FAIL, "dm_queue 載不進來:%r" % (exc,), "修 src/cockpit/dm_queue.py")
    root = Q.queue_root(env)
    if not root.is_dir():
        return _result("dm_latency", OK, "量不到:佇列目錄不存在(%s),沒有樣本" % root)

    def _m(vals, q):
        return "量不到" if not vals else "%d分" % (_pct(vals, q) // 60)
    rows, bad = [], 0
    for seat in Q.all_seats(env):
        items, errors = Q._all_items(seat, env=env, with_errors=True)
        bad += len(errors)
        dl = [int(i["delivered_at"]) - int(i["sent_at"]) for i in items
              if i.get("delivered_at") is not None and i.get("sent_at") is not None]
        rd = [int(i["read_at"]) - int(i["sent_at"]) for i in items
              if i.get("read_at") is not None and i.get("sent_at") is not None]
        unread = [int(i["sent_at"]) for i in items
                  if i.get("status") in ("queued", "delivered", "claimed") and i.get("read_at") is None and i.get("sent_at")]
        rows.append("%s:送達p50/p95=%s/%s 讀取p50/p95=%s/%s 樣本=%d/%d 最舊未讀=%s" % (
            seat, _m(dl, .5), _m(dl, .95), _m(rd, .5), _m(rd, .95), len(dl), len(rd),
            ("%d分" % ((now - min(unread)) // 60)) if unread else "無"))
    if bad:
        return _result("dm_latency", FAIL, "佇列有 %d 個檔讀不到;%s" % (bad, " | ".join(rows)),
                       "python3 src/cockpit/dm_queue.py tabs --seat <席> 看 errors")
    return _result("dm_latency", OK, " | ".join(rows) if rows else "量不到:沒有任何席的佇列,沒有樣本")


def _cabin_env_mod(repo_root):
    cock = Path(repo_root) / "src" / "cockpit"
    if str(cock) not in sys.path:
        sys.path.insert(0, str(cock))
    import cabin_env_audit
    return cabin_env_audit


def check_cabin_env(repo_root, env=None):
    """這一艙的 tmux 全域環境有沒有帶它不該帶的東西(2026-09-25)。

    ★ 為什麼掛在 doctor 上:09-25 之前這件事**沒有任何一格會叫**——
      三個 Claude 席整個介面黑白(艙帶著 NO_COLOR=1),外加上一代 grok 會話的
      GROK_SESSION_ID,兩件都不紅。一把沒有人走過的尺等於沒有尺。

    ★ 三態分得開,而且**取不到絕不判 ok**:
      沒有 EZ2_AIHUB_SOCKET    → manual(不在艙裡,本項什麼都沒檢查)
      socket 死了 / 讀不到      → manual 並說出理由(不是乾淨)
      量到了                    → 乾淨 ok / 髒 fail 並逐名列出
    """
    env = dict(os.environ if env is None else env)
    sock = (env.get("EZ2_AIHUB_SOCKET") or "").strip()
    if not sock:
        return _result("cabin_env", MANUAL,
                       "沒有 EZ2_AIHUB_SOCKET:不在艙裡,本項未檢查任何東西",
                       "在艙裡重跑,或 --socket 指定")
    try:
        M = _cabin_env_mod(repo_root)
        ids = M.provider_ids(str(Path(repo_root) / "src" / "cockpit" / "providers.json"))
        names = M.read_global_env(sock)
    except Exception as err:  # noqa: BLE001
        return _result("cabin_env", MANUAL,
                       "量不到(**不是乾淨**):%s" % err,
                       "確認 socket 還活著:tmux -L %s list-sessions" % sock)
    got = M.classify(names, ids)
    bad = got["identity"] + got["colour"]
    if not bad:
        return _result("cabin_env", OK,
                       "艙 %s 乾淨(%d 個變數,沒有別人的會話身份、沒有關色旗標)"
                       % (sock, len(names)))
    parts = []
    if got["identity"]:
        parts.append("別人的會話身份 %s" % " ".join(got["identity"]))
    if got["colour"]:
        parts.append("關色旗標 %s(這艙開的 CLI 會是單色)" % " ".join(got["colour"]))
    return _result("cabin_env", FAIL,
                   "艙 %s 帶了 %d 個不該帶的變數:%s" % (sock, len(bad), ";".join(parts)),
                   "這一艙是從別人的殼裡開出來的。逐項看:"
                   "python3 src/cockpit/cabin_env_audit.py --socket %s" % sock)


def run_all(state_root, repo_root, providers):
    checks = [check_python(), check_git(),
              check_state_root(state_root), check_state_root_perms(state_root),
              check_writable(state_root), check_ledger(state_root),
              check_schemas(repo_root), check_release_gate_hook(repo_root),
              check_seat_files(state_root, repo_root),
              check_secrets_perms(state_root, repo_root),
              check_feature_policy(repo_root=repo_root),
              check_handoff_fields(state_root, repo_root),
              check_cabin_env(repo_root)]
    try:
        S = _secrets_mod(repo_root)
        for feat in S.CUSTOMER_FIRST_BATCH + ("push",):
            checks.append(check_secrets_feature(state_root, repo_root, feat))
    except Exception as err:  # noqa: BLE001
        checks.append(_result("secrets_perms", FAIL,
                              "載入金鑰櫃失敗:%s" % err,
                              "確認 src/bootstrap/aib_secrets.py"))
    for name in providers:
        checks.append(check_provider_cli(name))
        checks.append(check_provider_login(name))
    checks.append(check_dm_latency(state_root, repo_root))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="aib-doctor", description="ED.3 逐項體檢,每項回機器可判結果")
    parser.add_argument("--state-root",
                        default=os.environ.get("EZ2_AIB_STATE_ROOT",
                                               os.path.expanduser(DEFAULT_STATE_ROOT)))
    parser.add_argument("--repo-root",
                        default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--provider", action="append", default=None,
                        help="要檢查的 provider CLI,可重複;預設=登記簿裡 status=supported 的四個")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 供機器讀")
    args = parser.parse_args(argv)

    providers = args.provider if args.provider is not None else [
        # ★ 這份清單與 src/cockpit/providers.json 的 supported 對帳
        # (tests/test-provider-registry.py)。加新 AI 要同時改兩處,
        # 漏掉會紅並指名是這一處。刻意保持字面值:對帳閘的解析器讀
        # 的是字面 list,改成動態讀登記簿會讓它解不到而誤判成儀器壞掉。
        "claude", "codex", "agy", "copilot", "grok", "deepseek", "kiosapi", "nvidia", "teamo", "teamoclaude", "openrouter", "typesafe", "openrouterjev", "sand", "grokbot", "codexbot", "claudebot", "musebot"]
    checks = run_all(args.state_root, args.repo_root, providers)

    counts = {OK: 0, FAIL: 0, MANUAL: 0}
    for c in checks:
        counts[c["status"]] += 1

    if args.json:
        print(json.dumps({"checks": checks, "counts": counts},
                         ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = {OK: "OK  ", FAIL: "FAIL", MANUAL: "你做"}[c["status"]]
            line = "%s %-24s %s" % (mark, c["id"], c["detail"])
            if c["status"] != OK and c["remedy"]:
                line += "\n         -> %s" % c["remedy"]
            print(line)
        print("\nok=%d fail=%d 需你自己做=%d" % (counts[OK], counts[FAIL], counts[MANUAL]))
        if counts[MANUAL]:
            print("★『需你自己做』不算通過:機器沒辦法確認的事,doctor 不會假裝它綠了。")

    return 0 if (counts[FAIL] == 0 and counts[MANUAL] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
