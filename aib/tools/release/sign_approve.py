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

10-30 @aeb40(Andy 23:5x 選 1;#449 Codex r1 兩條 High 後改):也收授權申請 LICENSE-<名>-<席>-<MMDD>.json。
  - 申請檔只放 {"plan": <free/go/plus/pro/ultra>, "priv": <私倉路徑>, "rev": <私倉 commit>};不跑任何腳本。
  - 待簽內容由核心自己組:讀現有 license.json(只收 tier / addons / providers / expires_at / note,到期不延長)、
    加 plan 與 license_id、用核心 device.device_id 算這台的綁機值 ⇒ 視窗字照這一份寫(名稱、方案、到期、綁機值前 8 碼)。
  - 按過才:從私倉那顆 commit 解出 aib-license.py,只對核心寫的那份暫存檔跑 sign-entitlement --device <算出的值>;
    簽完核心比對:除了 signature / issued_at,內容要跟視窗那份一模一樣、簽章要過信任公鑰、plan 要真的評得出來,
    才交 entitlement.install_obj 換上(舊檔備份)。申請檔只讀一次(之後換檔不影響這一輪)。
  - SIGN 腳本內文碰到 aib-license.py / license.json ⇒ 拒(授權只能走這條,不能換前綴走發版簽章)。
  - 同帳號 AI 讀得到簽章鑰,這一按是本人同意的紀錄,不是擋 AI 的防線(GUIDE-same-user-ai-threat-model);
    這條只保證「視窗寫的」=「裝上去的」。
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
COCKPIT = HERE.parents[2] / "src" / "cockpit"
for _p in (COCKPIT, COCKPIT.parent):           # src/ 給 entitlement(授權申請)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import approve_gate as AG  # noqa: E402
import touchid_approve as TA  # noqa: E402

NAME_RE = re.compile(r"^SIGN-(?P<plugin>[a-z0-9-]+?)-(?P<ver>\d+\.\d+\.\d+)-")
LIC_RE = re.compile(r"^LICENSE-(?P<name>[a-z0-9-]+?)-(?P<seat>[0-9a-f]{5})-(?P<date>\d{4})\.json$")
LIC_KEEP = ("tier", "addons", "providers", "expires_at", "note")
LIC_TOUCH = re.compile(r"aib-license\.py|license\.json")
REV_RE = re.compile(r"^[0-9a-f]{7,40}$")
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


def plan_license(p, m, raw):
    """讀授權申請(只讀這一次的位元組)。回 (job, None) 或 (None, why)。"""
    try:
        req = json.loads(raw)
    except ValueError:
        return None, "no_lic_vars"
    if not isinstance(req, dict) or set(req) != {"plan", "priv", "rev"}:
        return None, "no_lic_vars"
    if req["plan"] not in LIC_PLANS or not isinstance(req["priv"], str) or not isinstance(req["rev"], str) \
            or not REV_RE.match(req["rev"]):
        return None, "no_lic_vars"
    return {"kind": "license", "path": p, "name": m.group("name"), "plan": req["plan"],
            "priv": os.path.expanduser(req["priv"]), "rev": req["rev"]}, None


def build_license(job, env=None, raw_machine=None):
    """核心自己組待簽內容 + 算綁機值。回 (obj, did, None, 驗過的現有授權) 或 (None, None, why, None)。"""
    from entitlement import device, signing, store
    cur = store.read_license_obj(env=env)
    if not isinstance(cur, dict) or set(cur) - set(LIC_KEEP) - {"issued_at", "signature"} or cur.get("tier") != "all":
        return None, None, "lic_current", None
    if signing.verify_object(cur) != signing.OK:
        return None, None, "lic_current", None         # #449 r2 H1:舊授權要先過信任公鑰,才承接 addons / 到期
    try:
        when = datetime.fromisoformat(cur.get("expires_at") or "")
    except (TypeError, ValueError):
        return None, None, "lic_current", None
    if when.tzinfo is None:
        return None, None, "lic_current", None
    obj = {k: cur[k] for k in LIC_KEEP if k in cur}
    obj["plan"] = job["plan"]
    obj["license_id"] = "lic-" + secrets.token_hex(8)
    did = device.device_id(obj["license_id"], raw_machine)
    if not did:
        return None, None, "lic_machine", None
    obj["devices"] = [did]
    return obj, did, None, cur


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
    if LIC_TOUCH.search(text):
        return None, "sign_touches_license"      # 授權只能走 LICENSE-*.json(#449 r1 H1:換前綴繞走發版簽章)
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


def one(job, env=None, run=subprocess.run, head_fn=None, touchid=None, raw_machine=None):
    """簽一支。回 (ok, 一行回報)。"""
    env = os.environ if env is None else env
    if job.get("kind") == "license":
        return one_license(job, env=env, run=run, touchid=touchid, raw_machine=raw_machine)
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


def _same_as_shown(signed, shown):
    """簽完的內容除了 signature / issued_at,要跟視窗那份一模一樣。"""
    if not isinstance(signed, dict):
        return False
    a = {k: v for k, v in signed.items() if k not in ("signature", "issued_at")}
    b = {k: v for k, v in shown.items() if k != "issued_at"}
    return a == b


def _loads(data):
    try:
        return json.loads(data)
    except (TypeError, ValueError):
        return None


def ECLI_restore(path, data):
    """簽發途中現有授權被動過 ⇒ 用簽前那份蓋回去。回 True = 蓋回且覆讀一致。
    #449 r3:暫存名不可預測(mkstemp = O_EXCL、0600、同資料夾),失敗不吞:呼叫端看回值;換完覆讀比對。"""
    fd, tmpname = None, None
    try:
        fd, tmpname = tempfile.mkstemp(prefix=".%s.restore." % path.name, dir=str(path.parent))
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(data)
        os.chmod(tmpname, 0o600)
        os.replace(tmpname, str(path))          # 換成功後暫存名已不在,finally 的 unlink 只是保險
        return path.read_bytes() == data and not path.is_symlink()
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)
        if tmpname:
            try:
                os.unlink(tmpname)
            except OSError:
                pass


def one_license(job, env=None, run=subprocess.run, touchid=None, raw_machine=None):
    """授權申請:核心組內容 ⇒ 視窗 ⇒ 私倉工具只簽核心那份 ⇒ 比對 + 驗簽 + 評級 ⇒ 換上。回 (ok, 一行回報)。"""
    from entitlement import cli as ECLI, plan as EPLAN, signing
    env = os.environ if env is None else env
    label, ver = "LICENSE-" + job["name"], job["plan"]
    from entitlement import store as ESTORE
    lic_path = ESTORE.resolve_license_path(env)
    try:
        snap = lic_path.read_bytes()             # #449 r3:基準 = 驗簽過那份的位元組(先讀,再確認跟驗的是同一份)
    except OSError:
        snap = None
    obj, did, why, cur = build_license(job, env, raw_machine)
    if obj and (snap is None or _loads(snap) != cur):
        obj, why = None, "lic_current"           # 讀位元組與驗簽之間被換過
    if not obj:
        return False, T("skip_" + why, path=label)
    reason = AG.reason("release.license_approve", {"name": job["name"], "plan": obj["plan"],
                                                   "addons": ", ".join(obj.get("addons") or []) or "-",
                                                   "expires": obj["expires_at"], "device": did[:8]}, env=env)
    root = Path(AG.state_root_default(env))
    got = AG.ask(reason, root, touchid)
    if not got["ok"]:
        return False, T("busy" if got["why"] == "busy" else "denied", plugin=label, ver=ver, why=got["why"])
    tmp = Path(tempfile.mkdtemp(prefix="sign-license."))
    logs = root / "pending" / "sign-logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / ("%s-%s-%s.log" % (label, ver, time.strftime("%m%d-%H%M%S")))
    so = se = ""
    try:
        draft = tmp / "license.json"
        fd = os.open(str(draft), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        try:
            a = run(["git", "-C", job["priv"], "archive", job["rev"]], capture_output=True, timeout=120)
            pk = tmp / "pk"
            pk.mkdir()
            ok_tar = a.returncode == 0 and run(["tar", "-x", "-C", str(pk)], input=a.stdout, capture_output=True,
                                               timeout=120).returncode == 0
            # #449 r2 H3:簽發器的 HOME / 狀態根都指暫存,只該碰草稿;簽完現有授權被動過 ⇒ 還原、不換上
            senv = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "AIB_LICENSE_KEY")}
            senv.setdefault("AIB_LICENSE_KEY", str(Path.home() / ".ez2-aib-signing" / "license.key"))
            senv.update(HOME=str(tmp), EZ2_AIB_HOME=str(tmp / "home"), EZ2_AIB_STATE_ROOT=str(tmp / "home"))
            r = run([sys.executable, str(pk / "tools" / "license" / "aib-license.py"), "sign-entitlement", str(draft),
                     "--device", did], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=RUN_TIMEOUT_S, env=senv, cwd=str(tmp)) if ok_tar else None
            rc = r.returncode if r else 1
            so, se = (r.stdout or "", r.stderr or "") if r else ("", "git archive / tar failed")
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, se = 124, type(e).__name__
        signed = None
        if rc == 0:
            try:
                signed = json.loads(draft.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                signed = None
        check = "ok"
        try:
            now_bytes = lic_path.read_bytes()
        except OSError:
            now_bytes = None
        if now_bytes != snap:
            check = "current_touched" if ECLI_restore(lic_path, snap) else "restore_failed"
        elif rc != 0:
            check = "sign_failed"
        elif not _same_as_shown(signed, obj):
            check = "not_as_shown"
        elif signing.verify_object(signed) != signing.OK:
            check = "bad_signature"
        else:
            p_ = EPLAN.evaluate(signed, "license", False, env, raw_machine=raw_machine)
            if (p_["plan"], p_["machine_bound"]) != (obj["plan"], True):
                check = "plan_not_effective"
        if check == "ok":
            res, rc2 = ECLI.install_obj(signed)
            if rc2 != 0:
                check = "install_" + str(res.get("error"))
        so += "\ncheck=%s\n" % check
    finally:
        log.write_text(so + "\n--- stderr ---\n" + se, encoding="utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
    ok = check == "ok"
    notify(T("notify_ok" if ok else "notify_fail", plugin=label, ver=ver), run)
    return ok, T("done_ok" if ok else "done_fail", plugin=label, ver=ver, rc=0 if ok else 1, last=check, log=str(log))


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
