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
  note    **提醒,不算失敗**(10-15 稽核 C9):每個新用戶都一定會有、而且不是「壞了」的事 ——
          AI 工具要自己登入一次、不在艙裡跑所以艙內那項沒量、發版閘只有發版的人要裝。
          以前這些算 manual ⇒ 新用戶的 doctor 幾乎永遠 rc 1,「不通過」失去意思。

唯讀:本工具**不建立、不修改任何檔案**。要建目錄是 ED.2 `aib init` 的事。
  唯一例外(10-17 @abb89 裁):角色卡清單 cards.json 是角色庫的投影,升級後是舊程式產的(卡檔沒變、只是算法變了)⇒
  直接重產並在 cards_projection 那一項回報,不叫用戶手動 scan。角色庫改過或有人手改 ⇒ 照舊只列。
  例外二(10-24 @abb89 准,動力室 R10):從指令列跑完記最後一次 state/run-marks/doctor.json = {at, rc}(覆寫、不累積、
  不帶任何檢查內容);寫不進去照樣回原本的 rc。只在 main(),run_all 給別支呼叫時不記。

狀態根以可注入方式取得(參數 > 環境變數 > 預設)。**刻意不改既有 9 處硬編路徑**
——那是 ED.1 的獨佔範圍,現在動會與未來的 ED.1 互撞(pm 裁決)。

exit: 0 沒有 fail 也沒有 manual(note 不影響)· 1 有 fail 或 manual · 2 用法錯
"""

import argparse
import json
import re
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_STATE_ROOT = "~/.ez2-aib"
MIN_PYTHON = (3, 9)

OK, FAIL, MANUAL, NOTE = "ok", "fail", "manual", "note"


def _T(key, params=None):
    """使用者看得到的字一律走 i18n(aib/i18n/<語系>.json);語系照 aib lang / EZ2_AIB_LOCALE。"""
    cockpit = str(Path(__file__).resolve().parents[1] / "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    import i18n_lib  # noqa: E402
    return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale())


def _C(key, **params):
    """檢查項的字:鍵 = aib.doctor.check.<key>。"""
    return _T("aib.doctor.check." + key, params or None)


def _join(items):
    """清單分隔字跟語系走(中日:「、」;英韓:「, 」)。"""
    return _T("aib.doctor.list_sep").join(items)


def _result(check_id, status, detail, remedy=""):
    return {"id": check_id, "status": status, "detail": detail, "remedy": remedy}


def check_python():
    v = sys.version_info
    got = "%d.%d.%d" % (v.major, v.minor, v.micro)
    if (v.major, v.minor) >= MIN_PYTHON:
        return _result("python_version", OK, got)
    return _result("python_version", FAIL, got,
                   _C("python_version.remedy", version="%d.%d" % MIN_PYTHON))


def check_git():
    path = shutil.which("git")
    if not path:
        return _result("git_present", FAIL, _C("git_present.missing"),
                       _C("git_present.remedy"))
    return _result("git_present", OK, path)


def check_state_root(root):
    path = Path(root)
    if not path.exists():
        return _result("state_root_exists", FAIL, _C("state_root_exists.missing", path=path),
                       _C("state_root_exists.missing_remedy"))
    if not path.is_dir():
        return _result("state_root_exists", FAIL, _C("state_root_exists.not_dir", path=path),
                       _C("state_root_exists.not_dir_remedy"))
    return _result("state_root_exists", OK, str(path))


def check_state_root_perms(root):
    """group/world 可寫的狀態根 = 同機任何人都能改帳本與收據。"""
    path = Path(root)
    if not path.is_dir():
        return _result("state_root_perms", FAIL, _C("state_root_perms.no_root"),
                       _C("state_root_perms.no_root_remedy"))
    mode = path.stat().st_mode
    bad = []
    if mode & stat.S_IWGRP:
        bad.append(_C("state_root_perms.group_writable"))
    if mode & stat.S_IWOTH:
        bad.append(_C("state_root_perms.world_writable"))
    shown = stat.filemode(mode)
    if bad:
        return _result("state_root_perms", FAIL,
                       "%s(%s)" % (shown, _join(bad)),
                       "chmod g-w,o-w %s" % path)
    return _result("state_root_perms", OK, shown)


def check_writable(root):
    """真的寫得進去才算,不是看權限位元推測。唯讀原則的例外:寫完立刻刪。"""
    path = Path(root)
    if not path.is_dir():
        return _result("state_root_writable", FAIL, _C("state_root_writable.no_root"),
                       _C("state_root_writable.no_root_remedy"))
    probe = path / ".aib-doctor-write-probe"
    try:
        fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(str(probe))
    except OSError as err:
        return _result("state_root_writable", FAIL, str(err),
                       _C("state_root_writable.remedy", path=path))
    return _result("state_root_writable", OK, _C("state_root_writable.ok"))


def check_ledger(root):
    """帳本在就要能通過鏈驗證。複用 E1.1,不另造第二套驗證器(反重造)。"""
    ledger = Path(root) / "ledger" / "aib-ledger.jsonl"
    if not ledger.exists():
        return _result("ledger_chain", FAIL, _C("ledger_chain.missing", path=ledger),
                       _C("ledger_chain.missing_remedy"))
    src = Path(__file__).resolve().parents[1] / "ledger"
    sys.path.insert(0, str(src))
    try:
        from ledger import Ledger
        report = Ledger(str(ledger)).verify_chain()
    except Exception as err:      # noqa: BLE001 — 不可信輸入決定這裡能拋什麼
        return _result("ledger_chain", FAIL,
                       "%s: %s" % (type(err).__name__, str(err)[:120]),
                       _C("ledger_chain.broken_remedy"))
    if report.get("ok"):
        return _result("ledger_chain", OK, _C("ledger_chain.ok", count=report["count"]))
    err = report.get("error") or {}
    return _result("ledger_chain", FAIL,
                   "index %s %s" % (err.get("index"), err.get("code")),
                   _C("ledger_chain.chain_remedy"))


def check_schemas(repo_root):
    """schema 是驗證的正本;讀不到就沒有任何『合格』是可信的(fail-closed)。"""
    schema_dir = Path(repo_root) / "schemas"
    if not schema_dir.is_dir():
        return _result("schemas_readable", FAIL, _C("schemas_readable.no_dir"),
                       _C("schemas_readable.no_dir_remedy"))
    broken = []
    count = 0
    for f in sorted(schema_dir.glob("*.json")):
        count += 1
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            broken.append("%s(%s)" % (f.name, str(err)[:40]))
    if not count:
        return _result("schemas_readable", FAIL, _C("schemas_readable.empty"),
                       _C("schemas_readable.empty_remedy"))
    if broken:
        return _result("schemas_readable", FAIL, _join(broken),
                       _C("schemas_readable.broken_remedy"))
    return _result("schemas_readable", OK, _C("schemas_readable.ok", count=count))


def check_provider_cli(name):
    """CLI 在不在是機器可判的;**有沒有登入不是**,所以那件回 manual。"""
    path = shutil.which(name)
    if not path:
        return _result("provider_cli_%s" % name, FAIL, _C("provider_cli.missing", name=name),
                       _C("provider_cli.remedy", name=name))
    return _result("provider_cli_%s" % name, OK, path)


def check_provider_login(name):
    """登入與否機器量不到,但「沒量到」不是「壞了」⇒ note(提醒),不讓每個新用戶的 doctor 都 rc 1(稽核 C9)。"""
    return _result(
        "provider_login_%s" % name, NOTE,
        _C("provider_login.detail", name=name),
        _C("provider_login.remedy", name=name))


# 漂移判準的基準線。★ 不是「這棵樹的檔案」——見 check_release_gate_hook 檔頭那段。
HOOK_BASELINE_REF = "origin/main"


def _uses_git_hooks(repo_root):
    """這份倉有沒有在用發版閘:hooks 目錄(git rev-parse --git-path hooks,有設 core.hooksPath 就是那裡)裡已經有
    pre-push,或明設了 core.hooksPath。不是 git 倉 / git 跑不起來 ⇒ False。"""
    def git(*args):
        try:
            r = subprocess.run(["git"] + list(args), cwd=str(repo_root), capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None
    hooks = git("rev-parse", "--git-path", "hooks")
    if not hooks:
        return False
    if (Path(repo_root) / hooks / "pre-push").exists():
        return True
    return bool(git("config", "--get", "core.hooksPath"))


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
        return _result("release_gate_hook", FAIL, _C("release_gate_hook.no_gate", path=gate),
                       _C("release_gate_hook.no_gate_remedy"))
    if not installer.is_file():
        return _result("release_gate_hook", FAIL, _C("release_gate_hook.no_installer", path=installer),
                       _C("release_gate_hook.no_installer_remedy"))

    fix = _C("release_gate_hook.fix")
    # 10-15 稽核 C9:發版閘只有「發版的人」要裝;pipx / 一行安裝 / 一般 clone 的新用戶不需要,
    #   沒裝不該讓 doctor rc 1。只有這份倉已經在用(hooks 目錄已有 pre-push,或設了 core.hooksPath)才照舊嚴查。
    if not _uses_git_hooks(repo_root):
        return _result("release_gate_hook", NOTE, _C("release_gate_hook.not_used"), "")
    try:
        proc = subprocess.run(
            ["bash", str(installer), "verify",
             "--baseline", HOOK_BASELINE_REF, "--porcelain"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        return _result("release_gate_hook", FAIL, _C("release_gate_hook.installer_error", error=err), fix)

    states = {}
    for line in proc.stdout.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 4:
            states[parts[0]] = parts[1:]
    row = states.get("pre-push")
    if row is None:
        # 單源沒有報這一槽 = 我們什麼都沒量到,不是通過。
        return _result("release_gate_hook", MANUAL,
                       _C("release_gate_hook.no_slot", rc=proc.returncode,
                          stderr=(proc.stderr or "").strip()[:200]),
                       fix)
    state, baseline_sha, installed_sha = row
    if state == "same":
        return _result("release_gate_hook", OK,
                       _C("release_gate_hook.same", ref=HOOK_BASELINE_REF, sha=baseline_sha))
    if state == "missing":
        return _result("release_gate_hook", MANUAL,
                       _C("release_gate_hook.missing"),
                       _C("release_gate_hook.missing_remedy", fix=fix))
    if state == "no-baseline":
        # ★ 量不到不是 OK 也不是 FAIL:本機沒有那個 ref(新 clone 沒 fetch 過)。
        return _result("release_gate_hook", MANUAL,
                       _C("release_gate_hook.no_baseline", ref=HOOK_BASELINE_REF),
                       _C("release_gate_hook.no_baseline_remedy"))
    if state == "drift":
        return _result("release_gate_hook", FAIL,
                       _C("release_gate_hook.drift", ref=HOOK_BASELINE_REF,
                          baseline=baseline_sha, installed=installed_sha),
                       fix)
    if state == "not-executable":
        return _result("release_gate_hook", FAIL,
                       _C("release_gate_hook.not_executable"), fix)
    return _result("release_gate_hook", FAIL,
                   _C("release_gate_hook.other", state=state, rc=proc.returncode), fix)


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
                       _C("seat_files.no_dir", path=sdir))
    cockpit = Path(repo_root) / "src" / "cockpit"
    try:
        if str(cockpit) not in sys.path:
            sys.path.insert(0, str(cockpit))
        import seat_guard
    except ImportError as err:
        return _result("seat_files", FAIL, _C("seat_files.import_error", error=err),
                       _C("seat_files.import_remedy", path=cockpit))
    paths = sorted(sdir.glob("w*.json"))
    if not paths:
        # 與 seat_guard 的高-2 修法同一條紀律:0 份是合法狀態(全新 instance),
        # 不判紅;但**不得長得像通過**。
        return _result("seat_files", OK,
                       _C("seat_files.empty", path=sdir))
    bad = []
    for path in paths:
        fail, code, detail = seat_guard.check_file(str(path))
        if fail:
            bad.append("%s [%s] %s" % (path.name, code, detail))
    if bad:
        return _result("seat_files", FAIL,
                       _C("seat_files.bad", bad=len(bad), total=len(paths), items=";".join(bad)),
                       _C("seat_files.bad_remedy", cockpit=cockpit, path=sdir))
    return _result("seat_files", OK, _C("seat_files.ok", count=len(paths), path=sdir))


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
                       _C("secrets_perms.no_dir", path=d))
    bad = []
    if S._too_open(d, S.DIR_MODE):
        bad.append(_C("secrets_perms.dir_mode", path=d))
    for path in sorted(d.rglob("*")):
        if path.is_file():
            if S._too_open(path, S.FILE_MODE):
                bad.append(_C("secrets_perms.file_mode", path=path.relative_to(d)))
        elif path.is_dir():
            if S._too_open(path, S.DIR_MODE):
                bad.append(_C("secrets_perms.dir_mode", path=path.relative_to(d)))
    if bad:
        return _result("secrets_perms", FAIL, "; ".join(bad[:8]),
                       _C("secrets_perms.remedy", path=d))
    return _result("secrets_perms", OK, _C("secrets_perms.ok"))


def check_secrets_feature(state_root, repo_root, feature):
    """缺行只停該功能。全空=未啟用。權限太寬交給 secrets_perms。"""
    S = _secrets_mod(repo_root)
    env = dict(os.environ)
    env["EZ2_AIB_STATE_ROOT"] = str(Path(state_root).expanduser())
    cid = "secrets_%s" % feature.replace("-", "_")
    spec = S.FEATURES.get(feature)
    if not spec:
        return _result(cid, OK, _C("secrets_feature.unknown"))
    rel, required = spec
    d = S.secrets_dir(env)
    if not d.is_dir():
        return _result(cid, OK, _C("secrets_feature.no_dir", feature=feature))
    path = d / rel
    if path.is_file() and S._too_open(path, S.FILE_MODE):
        return _result(cid, OK, _C("secrets_feature.too_open"))
    try:
        rows = S.load_file(rel, env)
    except S.SecretError as err:
        if err.reason == S.PERM_TOO_OPEN:
            return _result(cid, OK, _C("secrets_feature.too_open"))
        return _result(cid, OK, _C("secrets_feature.unreadable", file=rel))
    filled = [n for n in required if rows.get(n)]
    missing = [n for n in required if not rows.get(n)]
    if not filled:
        return _result(cid, OK, _C("secrets_feature.off", feature=feature))
    if missing:
        return _result(cid, MANUAL,
                       _C("secrets_feature.missing", feature=feature, missing=_join(missing)),
                       _C("secrets_feature.missing_remedy", path=d / rel))
    return _result(cid, OK, _C("secrets_feature.ok", feature=feature))


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
        return _result("feature_policy", FAIL, _C("feature_policy.load_error", error=err),
                       _C("feature_policy.load_remedy"))
    st = FP.status(env)
    how = st["policy_how"]
    none = _T("aib.doctor.none")
    on = _join(st["on"]) or none
    off = _join(st["off"]) or none
    cabin = st["cabin"]
    if cabin == "量不到":      # feature_policy 的「量不到艙家」字面(該模組自己不走 i18n)
        cabin = _T("aib.doctor.unmeasured")
    base = _C("feature_policy.base", cabin=cabin, plan=st["plan"], on=on, off=off,
              off_means=st["off_means"])
    if FP.lacks_plus(env, how):
        # 10-01:基本版時畫面上功能區什麼都不畫;這裡一行說出是「要 Plus 授權」,不是關著。
        base = _C("feature_policy.plus", base=base, notice=FP.plus_needed_notice())
    if how == "unreadable":
        # ★ 這一句與畫面那一列**共用同一份字面**(FP.unreadable_notice)——
        #   兩邊各寫一份的話,改了一邊而另一邊沒改,使用者看到的兩個說法會不一樣。
        return _result("feature_policy", FAIL,
                       "%s:%s" % (FP.unreadable_notice(), st["policy_path"]),
                       _C("feature_policy.unreadable_remedy", path=st["policy_path"],
                          schema=FP.POLICY_SCHEMA))
    if how == FP.R_NO_HOME:
        return _result("feature_policy", OK,
                       _C("feature_policy.no_home", base=base))
    if how == "missing":
        return _result("feature_policy", OK, _C("feature_policy.missing", base=base))
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
        return _result("handoff_fields", OK, _C("handoff_fields.none", path=pend))
    latest = files[-1]
    tool = Path(repo_root) / "tools" / "dispatch" / "handoff_fields.py"
    if not tool.is_file():
        return _result("handoff_fields", FAIL, _C("handoff_fields.no_tool", path=tool),
                       _C("handoff_fields.no_tool_remedy"))
    try:
        proc = subprocess.run([sys.executable, str(tool), str(latest), "--json", "--strict-fixed"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        return _result("handoff_fields", FAIL, _C("handoff_fields.tool_error", error=err), "python3 %s %s --strict-fixed" % (tool, latest))
    rc = proc.returncode
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        data = {}
    missing = _join(data.get("missing") or []) or _T("aib.doctor.none")
    if rc == 0:
        return _result("handoff_fields", OK, _C("handoff_fields.ok", name=latest.name, seat=data.get("from_seat")))
    if rc == 4:
        return _result("handoff_fields", OK,
                       _C("handoff_fields.old", name=latest.name))
    if rc == 3:
        return _result("handoff_fields", FAIL, _C("handoff_fields.missing", name=latest.name, missing=missing),
                       _C("handoff_fields.missing_remedy", tool=tool, file=latest))
    return _result("handoff_fields", FAIL, _C("handoff_fields.not_handoff", name=latest.name, rc=rc,
                                               stderr=proc.stderr.strip()[:200]),
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
    env["EZ2_AIB_STATE_ROOT"] = str(state_root)          # 一律讀 doctor 指定的庫(外面設了別的也不蓋過;@abb89 #246 審)
    now = int(time.time() if now is None else now)
    cock = str(Path(repo_root) / "src" / "cockpit")
    try:
        if cock not in sys.path:
            sys.path.insert(0, cock)
        import dm_queue as Q
    except Exception as exc:  # noqa: BLE001
        return _result("dm_latency", FAIL, _C("dm_latency.import_error", error=repr(exc)),
                       _C("dm_latency.import_remedy"))
    root = Q.queue_root(env)
    if not root.is_dir():
        return _result("dm_latency", OK, _C("dm_latency.no_dir", path=root))

    def _m(vals, q):
        return _T("aib.doctor.unmeasured") if not vals else _C("dm_latency.minutes", n=_pct(vals, q) // 60)
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
        rows.append(_C("dm_latency.row", seat=seat, d50=_m(dl, .5), d95=_m(dl, .95), r50=_m(rd, .5),
                       r95=_m(rd, .95), nd=len(dl), nr=len(rd),
                       oldest=_C("dm_latency.minutes", n=(now - min(unread)) // 60) if unread
                       else _T("aib.doctor.none")))
    if bad:
        return _result("dm_latency", FAIL, _C("dm_latency.bad", count=bad, rows=" | ".join(rows)),
                       _C("dm_latency.bad_remedy"))
    return _result("dm_latency", OK, " | ".join(rows) if rows else _C("dm_latency.no_seats"))


def check_temp(state_root, repo_root, env=None, now=None):
    """aib temp 登記的臨時主機變更(@ee4f8 第十件):過期沒拆 ⇒ MANUAL(要人去拆或延);沒登記 / 都沒到期 ⇒ OK。
    清單是本機檔,這項只防忘記。"""
    env = dict(os.environ if env is None else env)
    env["EZ2_AIB_STATE_ROOT"] = str(state_root)          # 一律讀 doctor 指定的庫(外面設了別的也不蓋過)
    cock = str(Path(repo_root) / "src" / "cockpit")
    try:
        if cock not in sys.path:
            sys.path.insert(0, cock)
        import aib_temp
        rows = aib_temp.listing(env, now=now)
    except Exception as exc:  # noqa: BLE001
        return _result("temp", NOTE, _C("temp.error", error=repr(exc)))
    late = [r for r in rows if r["overdue"]]
    if late:
        return _result("temp", MANUAL, _C("temp.overdue", n=len(late), items=_join(
            "%s %s(%s)" % (r["id"], r["host"], r.get("seat") or "?") for r in late)), _C("temp.overdue_remedy"))
    return _result("temp", OK, _C("temp.ok", n=len(rows)))


def check_auto_update(state_root, repo_root, env=None):
    """aib update auto(10-23 Andy「更新版本要自動 aib update」):開著 ⇒ OK 一行(上次自動更新到哪版 / 上次查的結果);
    關著 / 設定檔讀不懂 ⇒ NOTE(不算失敗)。只讀設定與狀態檔,不連網路。"""
    env = dict(os.environ if env is None else env)
    env["EZ2_AIB_STATE_ROOT"] = str(state_root)
    cock = str(Path(repo_root) / "src" / "cockpit")
    try:
        if cock not in sys.path:
            sys.path.insert(0, cock)
        import auto_update
        status, line = auto_update.doctor_line(env)
    except Exception as exc:  # noqa: BLE001
        return _result("auto_update", NOTE, _C("auto_update.error", error=type(exc).__name__))
    return _result("auto_update", OK if status == "ok" else NOTE, line,
                   "" if status == "ok" else _C("auto_update.remedy"))


def check_badge_font(repo_root, env=None, runner=None, platform=None):
    """AI 符號(右欄 / 狀態列的引擎 logo,Nerd Font 私用區字)畫不畫得出(10-25 Mac Pro 13:字型檔裝了、
    Apple Terminal 設定檔卻還是 SF Mono ⇒ 空白)。畫得出 ⇒ OK;字型檔缺 / 終端機字型畫不出 ⇒ NOTE + 怎麼補;
    使用者自己設了 letters ⇒ OK。只讀(字型檔 cmap + Terminal 偏好),不改任何設定、不連網。"""
    env = dict(os.environ if env is None else env)
    rend = str(Path(repo_root) / "src" / "cockpit" / "render")
    try:
        if rend not in sys.path:
            sys.path.insert(0, rend)
        import engine_badge_mode as M
        import render_lib as R
        if (env.get(M.MODE_ENV) or "").strip().lower() == "letters":
            return _result("badge_font", OK, _C("badge_font.letters"))
        b = M.badge_inputs(env, icons=R.ENGINE_ICONS, tty=True, runner=runner, platform=platform)
    except Exception as exc:  # noqa: BLE001
        return _result("badge_font", NOTE, _C("badge_font.error", error=type(exc).__name__))
    if b["missing_reason"] is None:
        return _result("badge_font", OK, _C("badge_font.ok"))
    if b["missing_reason"] == "terminal_font":
        tf = M.terminal_font_check({ord(c) for c in R.ENGINE_ICONS.values()}, env, runner, platform)
        return _result("badge_font", NOTE, _C("badge_font.terminal", profile=tf.get("profile") or "?", font=tf.get("font") or "?"),
                       _C("badge_font.terminal_remedy", profile=tf.get("profile") or "?"))
    return _result("badge_font", NOTE, _C("badge_font.missing"), _C("badge_font.missing_remedy"))


# ez2.page 開了哪些功能(Andy 10-21 夜「建議全部做」第 4 件;@abb89 裁 L1:伺服器只有 GET /api/v1/page/features)。
# 核心常比伺服器先上(例:unbind 回 page_unbind_unsupported),會員要分得出「自己的問題」還是「伺服器還沒開」。
# 抓取跟 60 秒快取一律走 remote_link.fetch_features(跟 aib seat remote status 同一份,@abb89 裁)。
PAGE_FEATURE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")


def check_page_features(state_root, repo_root, env=None, call=None, now=None):
    """功能清單 ⇒ OK 列出已開 / 還沒開;伺服器還沒有這支 / 連不上 ⇒ NOTE(不算失敗,也不擋)。
    AIB_DOCTOR_NO_NET=1 或這台沒綁 ez2.page ⇒ 不連網路(測試 / 離線)。名稱只印小寫英數那些,伺服器亂回的字不直接印。"""
    env = dict(os.environ if env is None else env)
    env["EZ2_AIB_STATE_ROOT"] = str(state_root)          # 快取寫在 doctor 指定的庫(外面設了別的也不蓋過)
    if str(env.get("AIB_DOCTOR_NO_NET", "")).strip() == "1":
        return _result("page_features", NOTE, _C("page_features.skipped"))
    cock = str(Path(repo_root) / "src" / "cockpit")
    try:
        for d in (cock, str(Path(cock) / "page")):
            if d not in sys.path:
                sys.path.insert(0, d)
        import store
        if not store.list_tokens(env):                   # 這台沒綁 ez2.page ⇒ 不連網路(也讓沒綁的測試 / CI 不打外網)
            return _result("page_features", NOTE, _C("page_features.not_bound"))
        import remote_link as RL
        feats, why = RL.fetch_features(env, call=call or RL.http, now=now)
    except Exception as exc:  # noqa: BLE001
        return _result("page_features", NOTE, _C("page_features.unreachable") + " (%s)" % type(exc).__name__)
    if why or feats is None:
        return _result("page_features", NOTE,
                       _C("page_features.unreachable" if why == "server_unreachable" else "page_features.no_endpoint"))
    named = {k: v for k, v in feats.items() if PAGE_FEATURE_NAME.fullmatch(k)}
    on = sorted(k for k, v in named.items() if v)
    off = sorted(k for k, v in named.items() if not v)
    return _result("page_features", OK, _C("page_features.ok", on=_join(on) or _T("aib.doctor.none"),
                                           off=_join(off) or _T("aib.doctor.none")))


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
        return _result("cabin_env", NOTE,
                       _C("cabin_env.no_socket"),
                       _C("cabin_env.no_socket_remedy"))
    try:
        M = _cabin_env_mod(repo_root)
        ids = M.provider_ids(str(Path(repo_root) / "src" / "cockpit" / "providers.json"))
        names = M.read_global_env(sock)
    except Exception as err:  # noqa: BLE001
        return _result("cabin_env", MANUAL,
                       _C("cabin_env.unmeasured", error=err),
                       _C("cabin_env.unmeasured_remedy", socket=sock))
    got = M.classify(names, ids)
    bad = got["identity"] + got["colour"]
    if not bad:
        return _result("cabin_env", OK,
                       _C("cabin_env.ok", socket=sock, count=len(names)))
    parts = []
    if got["identity"]:
        parts.append(_C("cabin_env.identity", names=" ".join(got["identity"])))
    if got["colour"]:
        parts.append(_C("cabin_env.colour", names=" ".join(got["colour"])))
    return _result("cabin_env", FAIL,
                   _C("cabin_env.bad", socket=sock, count=len(bad), parts=";".join(parts)),
                   _C("cabin_env.bad_remedy", socket=sock))


def check_second_brain(repo_root):
    """10-12 新用戶全面檢查:第二大腦三件事 —— vault 在哪、路由契約讀得到、筆記格式模組拿得到(ez2-ai 或隨附副本)。"""
    out = []
    try:
        sys.path.insert(0, str(Path(repo_root) / "src" / "persona_route"))
        import route_register as RR
    except Exception as err:  # noqa: BLE001
        return [_result("brain_contract", FAIL, _C("brain_contract.load_error", error=err),
                        _C("brain_contract.load_remedy"))]
    vault = RR.default_vault()
    default = os.path.abspath(vault) == os.path.abspath(RR.aib_default_vault())
    out.append(_result("brain_vault", OK, "%s%s" % (vault, _C("brain_vault.default_note") if default else "")))
    spec = RR.default_contract_spec()
    if RR.read_contract_bytes(spec) is None:
        out.append(_result("brain_contract", FAIL, _C("brain_contract.unreadable", vault=vault),
                           _C("brain_contract.unreadable_remedy")))
    else:
        out.append(_result("brain_contract", OK, str(spec)))
    vendor = Path(repo_root) / "src" / "memory" / "vendor"
    ez2ai = Path(os.path.expanduser(os.environ.get("EZ2_AI_REPO") or "~/ez2-ai"))
    if ez2ai.is_dir():
        out.append(_result("brain_note_format", OK, _C("brain_note_format.ez2ai", path=ez2ai)))
    elif (vendor / "shared" / "ez2-note-format.js").is_file():
        out.append(_result("brain_note_format", OK, _C("brain_note_format.vendor", path=vendor)))
    else:
        out.append(_result("brain_note_format", FAIL, _C("brain_note_format.missing"),
                           _C("brain_note_format.missing_remedy")))
    if not shutil.which("node"):
        out.append(_result("brain_node", FAIL, _C("brain_node.missing"), _C("brain_node.remedy")))
    return out


def check_cards_projection(state_root, repo_root):
    """角色卡清單(<狀態根>/identity/cards.json)是不是現在這版程式產的;舊程式產的就重產(見檔頭「唯一例外」)。"""
    try:
        sys.path.insert(0, str(Path(repo_root) / "src" / "memory"))
        import card_registry as CR
    except Exception as err:  # noqa: BLE001
        return _result("cards_projection", NOTE, _C("cards_projection.error", error=err))
    out = Path(state_root) / "identity" / "cards.json"
    try:
        res = CR.refresh(out)
    except Exception as err:  # noqa: BLE001 —— 量不到(沒 vault、契約讀不到…)不算壞,說一聲
        return _result("cards_projection", NOTE, _C("cards_projection.error", error=err))
    st, n = res["status"], res.get("cards", 0)
    if st == "absent":
        return _result("cards_projection", OK, _C("cards_projection.absent"))
    if st == "current":
        return _result("cards_projection", OK, _C("cards_projection.current", n=n))
    if st == "refreshed":
        return _result("cards_projection", OK, _C("cards_projection.refreshed", n=n,
                                                       fields=_join(res.get("changed_fields") or ["-"])))
    scan_cmd = "python3 %s scan" % (Path(repo_root) / "src" / "memory" / "card_registry.py")
    if st == "edited_by_hand":
        return _result("cards_projection", FAIL, _C("cards_projection.edited", path=out),
                       _C("cards_projection.rescan_remedy", cmd=scan_cmd))
    return _result("cards_projection", NOTE,
                   _C("cards_projection.stale", added=len(res.get("added") or []),
                      removed=len(res.get("removed") or []), changed=len(res.get("changed") or [])),
                   _C("cards_projection.rescan_remedy", cmd=scan_cmd))


def provider_checks(providers, explicit):
    """預設(沒給 --provider)只檢查**裝了的** AI 工具;沒裝的合成一行「可選、沒裝」,一個都沒裝才算缺。
    明給 --provider 的照舊逐一檢查(沒裝就 FAIL)。"""
    if explicit:
        out = []
        for name in providers:
            out += [check_provider_cli(name), check_provider_login(name)]
        return out
    have = [n for n in providers if shutil.which(n)]
    out = []
    for name in have:
        out += [check_provider_cli(name), check_provider_login(name)]
    if not have:
        out.append(_result("provider_any", FAIL, _C("provider_any.none"), _C("provider_any.remedy")))
    missing = [n for n in providers if n not in have]
    if missing:
        out.append(_result("provider_optional", OK, _C("provider_optional.detail", names=_join(missing))))
    return out


def run_all(state_root, repo_root, providers, explicit_providers=True):
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
                              _C("secrets_perms.load_error", error=err),
                              _C("secrets_perms.load_remedy")))
    checks += check_second_brain(repo_root)
    checks.append(check_cards_projection(state_root, repo_root))
    checks += provider_checks(providers, explicit_providers)
    checks.append(check_dm_latency(state_root, repo_root))
    checks.append(check_temp(state_root, repo_root))
    checks.append(check_auto_update(state_root, repo_root))
    checks.append(check_badge_font(repo_root))
    checks.append(check_page_features(state_root, repo_root))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="aib doctor", description=_T("aib.doctor.help.description"))
    parser.add_argument("--state-root",
                        default=os.environ.get("EZ2_AIB_STATE_ROOT",
                                               os.path.expanduser(DEFAULT_STATE_ROOT)),
                        help=_T("aib.doctor.help.state_root"))
    parser.add_argument("--repo-root",
                        default=str(Path(__file__).resolve().parents[2]),
                        help=_T("aib.doctor.help.repo_root"))
    parser.add_argument("--provider", action="append", default=None,
                        help=_T("aib.doctor.help.provider"))
    parser.add_argument("--json", action="store_true", help=_T("aib.doctor.help.json"))
    args = parser.parse_args(argv)

    providers = args.provider if args.provider is not None else [
        # ★ 這份清單與 src/cockpit/providers.json 的 supported 對帳
        # (tests/test-provider-registry.py)。加新 AI 要同時改兩處,
        # 漏掉會紅並指名是這一處。刻意保持字面值:對帳閘的解析器讀
        # 的是字面 list,改成動態讀登記簿會讓它解不到而誤判成儀器壞掉。
        "claude", "codex", "agy", "copilot", "grok", "deepseek", "nvidia", "openrouter", "typesafe", "openrouterjev", "sand", "grokbot", "codexbot", "claudebot", "musebot"]
    checks = run_all(args.state_root, args.repo_root, providers, explicit_providers=args.provider is not None)

    counts = {OK: 0, FAIL: 0, MANUAL: 0, NOTE: 0}
    for c in checks:
        counts[c["status"]] += 1

    if args.json:
        print(json.dumps({"checks": checks, "counts": counts},
                         ensure_ascii=False, indent=2))
    else:
        for c in checks:
            tag = {OK: "OK  ", FAIL: "FAIL", MANUAL: _T("aib.doctor.mark_manual"),
                   NOTE: _T("aib.doctor.mark_note")}[c["status"]]      # 別叫 mark:會蓋掉 main 的 mark 參數
            line = "%s %-24s %s" % (tag, c["id"], c["detail"])
            if c["status"] != OK and c["remedy"]:
                line += "\n         -> %s" % c["remedy"]
            print(line)
        print("\n" + _T("aib.doctor.summary", {"ok": counts[OK], "fail": counts[FAIL], "manual": counts[MANUAL],
                                                "note": counts[NOTE]}))
        if counts[MANUAL]:
            print(_T("aib.doctor.manual_note"))

    rc = 0 if (counts[FAIL] == 0 and counts[MANUAL] == 0) else 1
    _mark_run(args.state_root, rc)
    return rc


def _mark_run(state_root, rc):
    """最後一次從指令列跑的時間與 rc(動力室 R10 讀);任何錯都吞掉,不改 doctor 的結果。"""
    try:
        d = os.path.join(os.path.expanduser(state_root), "state", "run-marks")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "doctor.json")
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": int(time.time()), "rc": rc}, f)
        os.replace(tmp, p)
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    sys.exit(main())
