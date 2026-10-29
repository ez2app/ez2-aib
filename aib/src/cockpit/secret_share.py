#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib secret copy / paste:把金鑰櫃裡的一支金鑰交給本人用,值不經過呼叫端(指令庫第二步;@aeb40 10-30 §9 裁示,
草稿 DRAFT-command-library-step2-copy-paste-b5799-1030)。

    aib secret copy  NAME --json                        放進剪貼簿,30 秒後清(還是我們放的那份才清)
    aib secret paste NAME --seat <艙位碼>/<席碼> --json   打進那一席的提示列,只打字、不按 Enter

兩支都只回代碼:{"ok": true[, "cleared_after": 30]} | {"ok": false, "why": "<代碼>"}。
代碼:bad_args / no_vault / vault_off / bad_value / denied / too_long / no_seat / seat_busy / paste_failed /
copy_failed / not_mac / caller_is_seat。

防線(GUIDE-same-user-ai-threat-model):
  - 每次都 Touch ID,一次一按:兩個 site 都是 secret 類(高風險、max_window 0)。copy 也一樣 —— 剪貼簿同帳號任何程式
    (含 AI)都讀得到,開時間窗 = 窗內 AI 自己 copy 再讀剪貼簿就拿到值。失敗 / 取消 / 逾時 / 叫不出 / 不是 Mac ⇒ 一律不給。
  - 視窗那句由核心組:金鑰櫃名字、席位(艙位碼 / 席碼,從名冊現查)都不截;整句放不下 ⇒ too_long,不截。
  - 值只在本行程記憶體與管線裡:讀金鑰櫃、pbcopy(stdin)、tmux load-buffer(stdin)、比雜湊都在核心行程內;
    不進任何 argv、環境變數、暫存檔、輸出、錯誤、紀錄。清剪貼簿的背景行程從 stdin 拿雜湊,不拿值。
  - 清除固定 30 秒,沒有參數(AI 叫得到指令,可調就能把清除拉長)。
  - 紀錄只記 op / ok / why;金鑰櫃名字與值都不記。
  - 30 秒清剪貼簿、只打字不按 Enter 都只防本人誤操作,不算防線;真正的防線是那一按。
"""
import hashlib
import json
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


def read_value(name, env):
    """回 (值, None) 或 (None, 代碼)。SE 專用 / 只放遠端的名字本機沒有值 ⇒ no_vault;讀取開關擋下 ⇒ vault_off。"""
    import aib_secrets as S
    if not S.valid_name(name) or S.file_for_name(name) is None or name in S.SE_ONLY_NAMES or S.is_remote_only(name):
        return None, "no_vault"
    try:
        value = S.secret(name, env=env, fill=False)
    except S.SecretError as e:
        return None, "vault_off" if e.reason in ("disabled", "token_short", "outside_window", "needs_confirm") else "no_vault"
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

def clear_later(value, run=subprocess.Popen):
    """背景 30 秒後清剪貼簿:子行程從 stdin 拿 sha256(不拿值),到時讀剪貼簿比對,還是同一份才清。"""
    try:
        p = run([sys.executable, "-I", os.path.abspath(__file__), "_clear"], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                env={"PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8"})
        p.stdin.write(_sha(value).encode("ascii"))
        p.stdin.close()
        return True
    except (OSError, ValueError):
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
    value, why = read_value(name, env)
    try:
        if why:
            return {"ok": False, "why": why}, 2
        why = prove(AG.reason("secret_share.copy", {"name": name}, env=env), env, platform, touchid, touchid_available)
        if why:
            return {"ok": False, "why": why}, 3
        try:
            r = run([PBCOPY], input=value, capture_output=True, text=True, timeout=5,
                    env={"PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8"})
        except (OSError, subprocess.SubprocessError):
            return {"ok": False, "why": "copy_failed"}, 4
        if r.returncode != 0:
            return {"ok": False, "why": "copy_failed"}, 4
        if not (spawn or clear_later)(value):
            return {"ok": False, "why": "copy_failed"}, 4     # 清不掉就不算成功(值已在剪貼簿,本人看得到這個代碼)
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


def paste_value(rec, value, tmux="tmux", run=subprocess.run):
    """值走 stdin 進 tmux(load-buffer -),paste-buffer -p -d 打進那一席;不按 Enter。buffer 名隨機,失敗也刪。"""
    import seat_act as SA
    sock, wid = SA._target(rec)
    if not sock:
        return "paste_failed"
    base, buf = [tmux, "-L", sock], "aib-secret-" + secrets.token_hex(16)
    try:
        r = run(base + ["load-buffer", "-b", buf, "-"], input=value, capture_output=True, text=True, env=SA._tmux_env(), timeout=10)
        if r.returncode != 0:
            return "paste_failed"
        r = run(base + ["paste-buffer", "-p", "-d", "-b", buf, "-t", wid], capture_output=True, text=True,
                env=SA._tmux_env(), stdin=subprocess.DEVNULL, timeout=10)
        return None if r.returncode == 0 else "paste_failed"
    except (OSError, subprocess.SubprocessError):
        return "paste_failed"
    finally:
        try:
            run(base + ["delete-buffer", "-b", buf], capture_output=True, text=True, env=SA._tmux_env(),
                stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


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
    value, why = read_value(name, env)
    try:
        if why:
            return {"ok": False, "why": why}, 2
        # 句子裡的席位用名冊現查出來的那筆,不照呼叫端傳的字串印
        r = AG.reason("secret_share.paste", {"seat": "%s/%s" % (cabin_of(rec), code), "name": name}, env=env)
        why = prove(r, env, platform, touchid, touchid_available)
        if why:
            return {"ok": False, "why": why}, 3
        if not SA.fresh_target(rec, code, env) or not _at_prompt(code, env, screen):
            return {"ok": False, "why": "seat_busy"}, 2       # 按指紋期間換了人 / 開始忙 ⇒ 不打
        why = paste_value(rec, value, tmux=tmux, run=run)
        if why:
            return {"ok": False, "why": why}, 4
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
