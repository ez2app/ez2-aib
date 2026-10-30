#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""發版簽核:彈 Touch ID 視窗,本人按過才跑 SIGN 腳本(Andy 10-25「把發版簽核,做成彈出式生物辨識」;
10-28 @aeb40 TID-1 從 ~/.ez2-aib/bin/aib-sign-approve 搬進倉:走統一入口 approve_gate,類別「發版簽章」= 高風險、一次一按)。

用法(從席位跑;視窗跳在本人的螢幕上):
  aib-sign-approve ~/.ez2-aib/pending/SIGN-<外掛>-<版號>-<席>-<日期>.sh [更多…]
~/.ez2-aib/bin/aib-sign-approve 是 aib init(安裝 / 更新時都會跑)寫的薄殼,只 exec 這一支;不掛 aib 子指令。

每支腳本一個視窗、一次指紋只簽那一件:
  1. 讀腳本裡的 BRANCH / PIN_SHA / VER 與 clone 的倉庫,向 GitHub 查分支頭;頭不是 PIN ⇒ 不彈窗、跳過。
  2. 把腳本複製到暫存(按完指紋後跑的是這份,不是之後可能被改過的原檔)。
  3. 視窗字由核心組(approve_gate「release.sign_approve」:外掛、版號、頭碼不截,太長直接拒);取消 / 失敗 / 逾時 = 不簽。
  4. 過了才 bash 跑那份複本;完整輸出寫 <狀態根>/pending/sign-logs/,最後一行回報 + 系統通知。
防線只有指紋本身(LocalAuthentication,AI 按不到);整台同時只開一個核准視窗(跟 aib 其他核准共用鎖)。

10-30 @aeb40(Andy 23:5x 選 1):也收授權單 LICENSE-<名>-<席>-<MMDD>.sh(簽發 / 重簽 license.json)。
  - 腳本頂層要宣告 PLAN(free / go / plus / pro / ultra)、EXPIRES(ISO 時間)、BIND=this(只綁這台),
    且內文要走私倉 aib-license.py sign-entitlement;缺一樣 ⇒ 跳過、不彈窗。
  - 視窗字由核心組(approve_gate「release.license_approve」,類別「授權簽發」= 高風險、一次一按):名稱、方案、到期不截。
  - 宣告值和腳本實際做的一不一樣,由腳本自己在簽之前比對現有授權後停(視窗只照宣告值寫);
    同帳號 AI 讀得到簽章鑰,這一按是本人同意的紀錄,不是擋 AI 的防線(GUIDE-same-user-ai-threat-model)。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
COCKPIT = HERE.parents[2] / "src" / "cockpit"
if str(COCKPIT) not in sys.path:
    sys.path.insert(0, str(COCKPIT))
import approve_gate as AG  # noqa: E402
import touchid_approve as TA  # noqa: E402

NAME_RE = re.compile(r"^SIGN-(?P<plugin>[a-z0-9-]+?)-(?P<ver>\d+\.\d+\.\d+)-")
LIC_RE = re.compile(r"^LICENSE-(?P<name>[a-z0-9-]+?)-(?P<seat>[0-9a-f]{5})-(?P<date>\d{4})\.sh$")
LIC_PLANS = ("free", "go", "plus", "pro", "ultra")
PIN_RE = re.compile(r"^[0-9a-f]{7,40}$")
SSH = ["ssh", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
RUN_TIMEOUT_S = 1200
SHIM_NAME = "aib-sign-approve"
SHIM_MARK = "# aib-managed: sign_approve shim"


def T(key, **kw):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.release.sign_approve." + key, kw or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if text and not text.startswith("!!") else key


def var(text, *names):
    for n in names:
        m = re.search(r'^%s="?\$\{%s:-([^}"]+)\}"?|^%s="?([^"\s#]+)"?' % (n, n, n), text, re.M)
        if m:
            return m.group(1) or m.group(2)
    return None


def plan_license(p, m, text):
    """讀授權單:PLAN / EXPIRES / BIND=this 都要有、內文要走 aib-license.py sign-entitlement。"""
    plan_, exp, bind = var(text, "PLAN"), var(text, "EXPIRES"), var(text, "BIND")
    try:
        when = datetime.fromisoformat(exp) if exp else None
    except ValueError:
        when = None
    if (plan_ not in LIC_PLANS or bind != "this" or when is None or when.tzinfo is None
            or not re.search(r"aib-license\.py\S*\s+sign-entitlement", text)):
        return None, "no_lic_vars"
    return {"kind": "license", "path": p, "name": m.group("name"), "plan": plan_, "expires": exp}, None


def plan(path):
    """讀 SIGN 腳本或授權單。回 (job, None) 或 (None, why 代碼)。"""
    p = Path(path).expanduser()
    lic = LIC_RE.match(p.name)
    m = NAME_RE.match(p.name)
    if not (m or lic) or not p.is_file():
        return None, "bad_name"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "bad_name"
    if lic:
        return plan_license(p, lic, text)
    branch, pin, ver = var(text, "BRANCH", "BR"), var(text, "PIN_SHA"), var(text, "VER") or m.group("ver")
    repo = re.search(r"git@github\.com:(ez2app/[\w.-]+?)(?:\.git)?\s", text.replace('"', " "))
    if not (branch and pin and repo) or not PIN_RE.match(pin):
        return None, "no_vars"
    return {"path": p, "plugin": m.group("plugin"), "ver": ver, "branch": branch, "pin": pin,
            "repo": repo.group(1)}, None


def remote_head(repo, branch, run=subprocess.run):
    env = dict(os.environ, GIT_SSH_COMMAND=" ".join(SSH))
    try:
        r = run(["git", "ls-remote", "git@github.com:%s.git" % repo, "refs/heads/" + branch],
                capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout.split() or [""])[0]


def notify(text, run=subprocess.run):
    shown = TA.clean(text, 120).replace('"', "'")
    try:
        run(["/usr/bin/osascript", "-e", 'display notification "%s" with title "%s"' % (shown, T("notify_title").replace('"', "'"))],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def one(job, env=None, run=subprocess.run, head_fn=None, touchid=None):
    """簽一支。回 (ok, 一行回報)。"""
    env = os.environ if env is None else env
    if job.get("kind") == "license":
        # 授權單沒有分支頭可對;回報沿用同一組字,「外掛」欄放 LICENSE-<名>、「版號」欄放方案
        job = dict(job, plugin="LICENSE-" + job["name"], ver=job["plan"])
        reason = AG.reason("release.license_approve", {"name": job["name"], "plan": job["plan"], "expires": job["expires"]},
                           env=env)
    else:
        head = (head_fn or remote_head)(job["repo"], job["branch"])
        if not head.startswith(job["pin"]):
            return False, T("skip_head", plugin=job["plugin"], ver=job["ver"], head=head[:7] or "?", pin=job["pin"])
        reason = AG.reason("release.sign_approve", {"plugin": job["plugin"], "ver": job["ver"], "pin": job["pin"]}, env=env)
    root = Path(AG.state_root_default(env))
    tmp = Path(tempfile.mkdtemp(prefix="sign-approve."))
    try:
        copy = tmp / job["path"].name
        shutil.copy2(job["path"], copy)
        got = AG.ask(reason, root, touchid)
        if not got["ok"]:
            key = "busy" if got["why"] == "busy" else "denied"
            return False, T(key, plugin=job["plugin"], ver=job["ver"], why=got["why"])
        logs = root / "pending" / "sign-logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / ("%s-%s-%s.log" % (job["plugin"], job["ver"], time.strftime("%m%d-%H%M%S")))
        try:
            r = run(["bash", str(copy)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
            rc, so, se = r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            rc, so, se = 124, "", "timeout"
        log.write_text(so + "\n--- stderr ---\n" + se, encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines = [ln for ln in (so + "\n" + se).splitlines() if ln.strip()]
    last = TA.clean(lines[-1], 200) if lines else T("no_output")
    ok = rc == 0
    notify(T("notify_ok" if ok else "notify_fail", plugin=job["plugin"], ver=job["ver"]), run)
    return ok, T("done_ok" if ok else "done_fail", plugin=job["plugin"], ver=job["ver"], rc=rc, last=last, log=str(log))


def ensure_shim(state_root, python=None):
    """<狀態根>/bin/aib-sign-approve 寫成只 exec 這一支的薄殼(aib init 呼叫;安裝 / 更新都會跑到)。
    舊的獨立版(沒有標記)先備份成 .bak-<時間> 再換。回 "created" / "updated" / "unchanged" / "refused"
    (暫存檔開不了:拿掉殘留後又被放回符號連結之類 ⇒ 不寫、不丟例外)。"""
    path = Path(state_root) / "bin" / SHIM_NAME
    body = "#!/bin/sh\n%s\n# 本體:%s(改這裡沒用,下次 aib init 會重寫)\nexec %s \"%s\" \"$@\"\n" % (
        SHIM_MARK, HERE, python or "/usr/bin/env python3", HERE)
    old = None
    try:
        old = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        old = None if not path.exists() else ""
    if old == body:
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    if old is not None and SHIM_MARK not in old:
        shutil.copy2(path, path.with_name(SHIM_NAME + ".bak-" + time.strftime("%Y%m%d-%H%M%S")))
    tmp = path.with_name(".%s.tmp" % SHIM_NAME)   # 固定名 + O_EXCL(@aeb40 #404 R2 Low:別的 pid 留下的 .tmp-* 也會堆)
    for left in path.parent.glob(".%s.tmp*" % SHIM_NAME):
        try:
            os.unlink(left)              # 上次殘留(含舊版 .tmp-<pid>,也可能是別人放的符號連結):拿掉再建,不跟著寫(R1 L3)
        except OSError:
            pass
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o755)
    except OSError:                      # 拿掉之後又被放回(競態)/ 是資料夾 ⇒ 拒,不寫、不印 traceback
        return "refused"
    try:
        os.write(fd, body.encode("utf-8"))
        os.fchmod(fd, 0o755)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return "created" if old is None else "updated"


def main(argv, env=None, platform=None, available=None, **kw):
    if not argv:
        print(T("usage"))
        return 2
    if AG.ready(env, platform, available):
        print(T("unavailable"))
        return 2
    rc = 0
    for a in argv:
        job, why = plan(a)
        if not job:
            print(T("skip_" + why, path=TA.clean(a, 160)))
            rc = 1
            continue
        ok, line = one(job, env=env, **kw)
        print(line)
        rc |= 0 if ok else 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
