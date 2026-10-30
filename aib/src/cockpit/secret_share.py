#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib secret copy / paste:把金鑰櫃裡的一支金鑰交給本人用,值不經過呼叫端(指令庫第二步;@aeb40 10-30 §9 裁示,
草稿 DRAFT-command-library-step2-copy-paste-b5799-1030)。

    aib secret copy  NAME --json                        放進剪貼簿,30 秒後清(還是我們放的那份才清)
    aib secret paste NAME --seat <艙位碼>/<席碼> --json   打進那一席的提示列,只打字、不按 Enter

兩支都只回代碼:{"ok": true[, "cleared_after": 30]} | {"ok": false, "why": "<代碼>"}。
代碼:bad_args / no_vault / vault_off / bad_value / denied / too_long / no_seat / seat_busy / paste_failed / buffer_left /
copy_failed / clear_failed / not_mac / caller_is_seat。

防線(GUIDE-same-user-ai-threat-model):
  - 每次都 Touch ID,一次一按:兩個 site 都是 secret 類(高風險、max_window 0)。copy 也一樣 —— 剪貼簿同帳號任何程式
    (含 AI)都讀得到,開時間窗 = 窗內 AI 自己 copy 再讀剪貼簿就拿到值。失敗 / 取消 / 逾時 / 叫不出 / 不是 Mac ⇒ 一律不給。
  - 視窗那句由核心組:金鑰櫃名字、席位(艙位碼 / 席碼,從名冊現查)都不截;整句放不下 ⇒ too_long,不截。
  - 順序:先查讀取開關(擋下 ⇒ 不彈指紋、不讀值)⇒ Touch ID ⇒ 才讀值。開關是本人設的守門,按過指紋也不繞。
  - 值只在本行程記憶體與管線裡:讀金鑰櫃、pbcopy(stdin)、tmux load-buffer(stdin)、比雜湊都在核心行程內;
    不進任何 argv、環境變數、暫存檔、輸出、錯誤、紀錄。清剪貼簿的背景行程從 stdin 拿雜湊,不拿值。
  - 清除固定 30 秒,沒有參數(AI 叫得到指令,可調就能把清除拉長)。
  - 紀錄只記 op / ok / why;金鑰櫃名字與值都不記。例外(@aeb40 10-30 03:10):讀取開關擋下時,開關本身照舊在
    金鑰櫃夾的提醒紀錄寫一列(含名字)—— 那是金鑰櫃既有的稽核,只在被擋時記,這裡不改。
  - 30 秒清剪貼簿、只打字不按 Enter 都只防本人誤操作,不算防線;真正的防線是那一按。
"""
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "bootstrap")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CLEAR_AFTER_S = 30                     # 固定,不開參數(@aeb40 §9-1)
PBCOPY, PBPASTE = "/usr/bin/pbcopy", "/usr/bin/pbpaste"
SEAT_RE = re.compile(r"([0-9a-f]{4,8})/([0-9a-f]{5})\Z")
_BAD = re.compile(r"[\x00-\x1f\x7f]")  # 換行會在提示列送出 ⇒ 值有控制字元一律不給
USAGE = ("usage: aib secret copy NAME --json\n"
         "       aib secret paste NAME --seat <cabin>/<seat> --json\n")


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def log_action(op, ok, why, env=None):
    """只記 op / ok / why(金鑰櫃名字、席位、值都不記)。"""
    import seat_act as SA
    p = SA.state_root(env) / "logs" / "secret-share.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": int(time.time()), "op": op, "ok": bool(ok), "why": why}) + "\n")
        os.chmod(p, 0o600)
    except OSError:
        pass


GATE_REASONS = ("disabled", "token_short", "outside_window", "needs_confirm")


def precheck(name, env):
    """彈指紋之前:名字要是本機金鑰櫃的(SE 專用 / 只放遠端 / 不認得 ⇒ no_vault),讀取開關要開(擋下 ⇒ vault_off)。
    不讀值。開關是本人設的守門,按過指紋也不繞(@aeb40 10-30 03:10);被擋時開關自己寫的提醒紀錄是金鑰櫃既有稽核,照舊。"""
    import aib_secrets as S
    from secret_read_gate import assert_read_allowed
    if not S.valid_name(name) or S.file_for_name(name) is None or name in S.SE_ONLY_NAMES or S.is_remote_only(name):
        return "no_vault"
    try:
        assert_read_allowed(name, env=env)
    except S.SecretError as e:
        return "vault_off" if e.reason in GATE_REASONS else "no_vault"
    except Exception:  # noqa: BLE001
        return "vault_off"
    return None


def read_value(name, env):
    """按過指紋之後才叫。回 (值, None) 或 (None, 代碼)。"""
    import aib_secrets as S
    why = precheck(name, env)
    if why:
        return None, why
    try:
        value = S.secret(name, env=env, fill=False)
    except S.SecretError as e:
        return None, "vault_off" if e.reason in GATE_REASONS else "no_vault"
    except Exception:  # noqa: BLE001  例外訊息可能帶路徑 / 內容 ⇒ 只回代碼
        return None, "no_vault"
    if not isinstance(value, str) or not value:
        return None, "no_vault"
    if _BAD.search(value):
        return None, "bad_value"
    return value, None


def prove(r, env, platform, touchid=None, touchid_available=None):
    """r = 呼叫點用 AG.reason 組好的那句(site 登記的 mod 就是這支)。回 None = 本人按過;否則代碼。"""
    import approve_gate as AG
    import seat_act as SA
    if AG.ready(env, platform, touchid_available):
        return "denied"
    res = AG.ask(r, SA.state_root(env), touchid)
    if res.get("ok") is True:
        return None
    return "too_long" if res.get("detail") in ("too_long",) or str(res.get("detail") or "").startswith("fact_too_long") else "denied"


# ── copy ──

def start_clearer(run=subprocess.Popen):
    """先起清除子行程(30 秒後比雜湊清剪貼簿),起不來就不 pbcopy(#435 R1 L4)。子行程從 stdin 拿 sha256、不拿值;
    回 Popen 或 None。"""
    try:
        return run([sys.executable, "-I", os.path.abspath(__file__), "_clear"], stdin=subprocess.PIPE,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                   env={"PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8"})
    except (OSError, ValueError):
        return None


def arm_clearer(proc, value):
    """pbcopy 成功後才交雜湊;value None ⇒ 只關 stdin(子行程讀到空的就 rc 1 收工,不碰剪貼簿)。回 True = 已交。"""
    try:
        if value is not None:
            proc.stdin.write(_sha(value).encode("ascii"))
        proc.stdin.close()
        return value is not None
    except (OSError, ValueError, AttributeError):
        return False


def clear_if_ours(digest, run=subprocess.run, sleep=time.sleep):
    """_clear 子行程本體。回 "cleared" / "changed" / "failed"(都不帶值)。"""
    sleep(CLEAR_AFTER_S)
    env = {"PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8"}
    try:
        now = run([PBPASTE], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return "failed"
    if now.returncode != 0 or _sha(now.stdout or "") != digest:
        return "changed"
    try:
        r = run([PBCOPY], input="", capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "cleared" if r.returncode == 0 else "failed"


def do_copy(name, env=None, platform=None, touchid=None, touchid_available=None, run=subprocess.run, spawn=None):
    import approve_gate as AG
    import seat_act as SA
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    if SA.caller_is_seat(env):
        return {"ok": False, "why": "caller_is_seat"}, 3
    if platform != "darwin":
        return {"ok": False, "why": "not_mac"}, 2
    why = precheck(name, env)                        # 開關擋下 ⇒ 不彈指紋、不讀值
    if why:
        return {"ok": False, "why": why}, 2
    why = prove(AG.reason("secret_share.copy", {"name": name}, env=env), env, platform, touchid, touchid_available)
    if why:
        return {"ok": False, "why": why}, 3
    value, why = read_value(name, env)               # 按過才讀
    try:
        if why:
            return {"ok": False, "why": why}, 2
        proc = (spawn or start_clearer)()           # 清除排得上才放剪貼簿
        if proc is None:
            return {"ok": False, "why": "clear_failed"}, 4
        try:
            r = run([PBCOPY], input=value, capture_output=True, text=True, timeout=5,
                    env={"PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8"})
            copied = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            copied = False
        if not copied:
            arm_clearer(proc, None)
            return {"ok": False, "why": "copy_failed"}, 4
        if not arm_clearer(proc, value):            # 已放進去但交不了雜湊 ⇒ 現在就比對清掉
            clear_if_ours(_sha(value), run=run, sleep=lambda s: None)
            return {"ok": False, "why": "clear_failed"}, 4
        return {"ok": True, "cleared_after": CLEAR_AFTER_S}, 0
    finally:
        value = None  # noqa: F841


# ── paste ──

def cabin_of(rec):
    """席位檔在 instances/<…-艙位碼>/seats/ 底下 ⇒ 艙位碼。"""
    try:
        return Path(rec.get("_file") or "").parent.parent.name.rsplit("-", 1)[-1]
    except (TypeError, ValueError):
        return ""


def find_seat(spec, env):
    """'<艙位碼>/<席碼>' ⇒ 名冊上那一席(非退役、最新那筆);只給席碼 / 格式不對 / 找不到 ⇒ None。"""
    import seat_act as SA
    import seat_address
    import seat_panel as SPN
    m = SEAT_RE.match(spec or "")
    if not m:
        return None
    cabin, code = m.groups()
    hits = [r for r in SPN.seat_records(str(SA.state_root(env))) if seat_address.code_of(r) == code and cabin_of(r) == cabin]
    hits.sort(key=lambda r: float(r.get("started_epoch") or 0), reverse=True)
    return hits[0] if hits else None


def pid_started(pid, run=subprocess.run):
    """行程啟動時間(epoch 秒,ps -o lstart= 用 C 語系);查不到 ⇒ None。"""
    try:
        r = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10,
                env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        if r.returncode != 0:
            return None
        return time.mktime(time.strptime(" ".join((r.stdout or "").split()), "%a %b %d %H:%M:%S %Y"))
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return None


PANE_START_EARLY_S = 2     # pane 行程比席位檔 started_epoch 早:實測最多 1 秒
PANE_START_LATE_S = 120    # 晚:開席外殼可能晚起(10-30 本艙 20 席有一席晚 30 秒,@fd787 旁審);只防 PID 重用,不必更緊


def seat_pane(base, wid, pid, run=subprocess.run, started=None):
    """視窗裡 pane_pid = 席位檔 pid 的那一格(%N);找不到 ⇒ None。-t @N 會打到視窗目前作用中的 pane,
    同視窗另切一格再 select-pane 就會打錯格(#435 R1 L3:不是防線,只減少打錯格)。
    PID 會被重用:那一格行程的啟動時間要落在席位檔 started_epoch 前 2 秒到後 120 秒內,缺或不符 ⇒ None(#442 r1 M1)。"""
    import seat_act as SA
    try:
        r = run(base + ["list-panes", "-t", wid, "-F", "#{pane_id} #{pane_pid}"], capture_output=True, text=True,
                env=SA._tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    hits = [f[0] for f in (ln.split() for ln in (r.stdout or "").splitlines())
            if len(f) == 2 and re.fullmatch(r"%\d+", f[0]) and f[1] == str(pid)]
    if len(hits) != 1:
        return None
    try:
        want = float(started)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(want):   # NaN 跟誰比都不大於 2 秒,會繞過比對(#442 r2 L2)
        return None
    got = pid_started(pid, run=run)
    if got is None or not (want - PANE_START_EARLY_S <= got <= want + PANE_START_LATE_S):
        return None
    return hits[0]


CLEAN_TRIES = 3


def drop_buffer(base, buf, run=subprocess.run):
    """刪 buffer 並用 list-buffers 確認真的不在了(paste-buffer -d 成功時早已刪掉,delete-buffer 回非零是正常的,
    所以不看 delete 的 rc,只看清單)。最多試 CLEAN_TRIES 次;清單讀不到也算沒確認。回 True = 確認不在。"""
    import seat_act as SA
    for _ in range(CLEAN_TRIES):
        try:
            run(base + ["delete-buffer", "-b", buf], capture_output=True, text=True, env=SA._tmux_env(),
                stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            r = run(base + ["list-buffers", "-F", "#{buffer_name}"], capture_output=True, text=True, env=SA._tmux_env(),
                    stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and buf not in (r.stdout or "").split():
            return True
        if r.returncode != 0 and "no server running" in (r.stderr or ""):   # tmux 伺服器已經不在 ⇒ buffer 也不在
            return True
    return False


def paste_value(rec, value, tmux="tmux", run=subprocess.run):
    """值走 stdin 進 tmux(load-buffer -),paste-buffer -p -d 打進那一席自己的 pane(pane_pid 對席位檔 pid);不按 Enter。
    buffer 名隨機,失敗也刪。找不到那一格 ⇒ seat_busy,值不進 buffer。
    值進過 buffer 後確認不了已刪 ⇒ buffer_left(蓋過原本結果;#442 r1 M2:paste 失敗 + 刪除也失敗會殘留)。"""
    import seat_act as SA
    sock, wid = SA._target(rec)
    if not sock:
        return "paste_failed"
    base, buf = [tmux, "-L", sock], "aib-secret-" + secrets.token_hex(16)
    pane = seat_pane(base, wid, rec.get("pid"), run=run, started=rec.get("started_epoch")) if str(rec.get("pid") or "").isdigit() else None
    if pane is None:
        return "seat_busy"
    why = "paste_failed"
    try:
        r = run(base + ["load-buffer", "-b", buf, "-"], input=value, capture_output=True, text=True, env=SA._tmux_env(), timeout=10)
        if r.returncode == 0:
            r = run(base + ["paste-buffer", "-p", "-d", "-b", buf, "-t", pane], capture_output=True, text=True,
                    env=SA._tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
            why = None if r.returncode == 0 else "paste_failed"
    except (OSError, subprocess.SubprocessError):
        why = "paste_failed"
    return why if drop_buffer(base, buf, run=run) else "buffer_left"


def _at_prompt(code, env, screen):
    import seat_act as SA
    import seat_panel as SPN
    pend, _rc = SPN.pending(code, state_root=str(SA.state_root(env)), screen=screen, env=env)
    return not pend.get("ask") and pend.get("at_prompt") is True


def do_paste(name, seat, env=None, platform=None, touchid=None, touchid_available=None, screen=None, tmux="tmux", run=subprocess.run):
    import approve_gate as AG
    import seat_act as SA
    import seat_address
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    if SA.caller_is_seat(env):
        return {"ok": False, "why": "caller_is_seat"}, 3
    if platform != "darwin":
        return {"ok": False, "why": "not_mac"}, 2
    rec = find_seat(seat, env)
    if rec is None:
        return {"ok": False, "why": "no_seat"}, 2
    code = seat_address.code_of(rec)
    if not _at_prompt(code, env, screen):
        return {"ok": False, "why": "seat_busy"}, 2
    why = precheck(name, env)                        # 開關擋下 ⇒ 不彈指紋、不讀值
    if why:
        return {"ok": False, "why": why}, 2
    # 句子裡的席位用名冊現查出來的那筆,不照呼叫端傳的字串印
    r = AG.reason("secret_share.paste", {"seat": "%s/%s" % (cabin_of(rec), code), "name": name}, env=env)
    why = prove(r, env, platform, touchid, touchid_available)
    if why:
        return {"ok": False, "why": why}, 3
    value, why = read_value(name, env)               # 按過才讀
    try:
        if why:
            return {"ok": False, "why": why}, 2
        if not SA.fresh_target(rec, code, env) or not _at_prompt(code, env, screen):
            return {"ok": False, "why": "seat_busy"}, 2       # 按指紋期間換了人 / 開始忙 ⇒ 不打
        why = paste_value(rec, value, tmux=tmux, run=run)
        if why:
            return {"ok": False, "why": why}, 2 if why == "seat_busy" else 4
        return {"ok": True}, 0
    finally:
        value = None  # noqa: F841


def main(argv, out=sys.stdout):
    """argv = copy / paste 之後的參數(secret_ask.main 轉進來)。"""
    import argparse
    op = argv[0] if argv else ""
    ap = argparse.ArgumentParser(prog="aib secret " + op, add_help=False)
    ap.add_argument("name")
    ap.add_argument("--json", action="store_true")
    if op == "paste":
        ap.add_argument("--seat", required=True)
    try:
        a, extra = ap.parse_known_args(argv[1:])
    except SystemExit:
        a, extra = None, ["?"]
    if op not in ("copy", "paste") or a is None or extra:
        res, rc = {"ok": False, "why": "bad_args"}, 2
        sys.stderr.write(USAGE)
    elif op == "copy":
        res, rc = do_copy(a.name)
    else:
        res, rc = do_paste(a.name, a.seat)
    log_action(op if op in ("copy", "paste") else "bad", res.get("ok"), res.get("why"))
    out.write(json.dumps(res) + "\n")
    return rc


if __name__ == "__main__" and sys.argv[1:2] == ["_clear"]:
    digest = sys.stdin.read(64)
    sys.exit(0 if re.fullmatch(r"[0-9a-f]{64}", digest or "") and clear_if_ours(digest) == "cleared" else 1)
