#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""本人核准的第二條路:macOS 彈 Touch ID / 登入密碼視窗(the maintainer 10-17:「下次這種要重新在其他終端機打的,
能不能變成彈出視窗」⇒ 選彈窗 + Touch ID;純同意鈕不行)。

AI 席跑 `aib newproj --approve <uid>` 時,終端機三條件(human_attest)本來就過不了;這時改由這一支叫
/usr/bin/osascript 跑內嵌 JXA,呼叫 LocalAuthentication 的 LAContext(deviceOwnerAuthentication:
Touch ID,沒有或失敗時系統自己給登入密碼欄)。只有本人的手指 / 密碼過得了,AI 按不到。

規矩(@abb89 審):
  - 視窗文字只由 aib 自己組(呼叫端只給「核准哪一件」的資料,不給整句);這一支沒有 CLI,不吃外來字串。
  - 每次產生一次性 nonce 放進 JXA,成功時 JXA 要原樣印回;對不上 = 沒過(擋「隨便印一行 ok 的假 osascript」)。
  - 只認真的成功:取消、失敗、逾時、沒有圖形介面、osascript 不在、輸出讀不懂,一律沒過。
  - 一次只核准那一件:視窗文字綁專案 uid + 動作,成功只回給這一次呼叫,不留任何「已驗過」的狀態;
    整台機器同時只開一個視窗(one_at_a_time 鎖;別席已在等指紋 ⇒ 這次直接沒過,不排隊、不疊兩個視窗)。
  - 沒有放行的環境變數或旗標。唯一的開關 AIB_TOUCHID=0 只會**關掉**這條路(退回只剩終端機),失效方向只往低權。
  - 不是 macOS ⇒ 這條路不存在,照舊只有終端機。
"""
import json
import os
import re
import secrets
import subprocess
import sys

OSASCRIPT = "/usr/bin/osascript"
POLICY_DEVICE_OWNER = 2          # LAPolicyDeviceOwnerAuthentication(Touch ID 或登入密碼)
WAIT_S = 60                      # 人看到視窗到按下去最多等這麼久;超過就收掉視窗、算沒過
# LAError 代碼 ⇒ 給人看的原因(其他代碼一律 "failed:<code>")
LA_CODES = {"-1": "failed", "-2": "cancel", "-3": "fallback", "-4": "system_cancel",
            "-5": "no_passcode", "-6": "no_biometry", "-8": "lockout", "-9": "timeout"}

_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")
# 方向控制 / 零寬字(U+202E 之類):視窗裡看不到、卻能把標題排成別的樣子 ⇒ 直接拿掉(@abb89 審 10-17)
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")

JXA = r"""
ObjC.import('LocalAuthentication');
ObjC.import('Foundation');
var REASON = __REASON__, NONCE = __NONCE__, WAIT = __WAIT__;
var ctx = $.LAContext.alloc.init;
var done = false, ok = false, code = null;
try {
  ctx.evaluatePolicyLocalizedReasonReply(__POLICY__, REASON, function (success, e) {
    ok = (success === true);
    code = (e && !e.isNil()) ? String(e.code) : null;
    done = true;
  });
} catch (ex) { code = "throw"; done = true; }
var deadline = $.NSDate.dateWithTimeIntervalSinceNow(WAIT);
while (!done && $.NSDate.date.compare(deadline) < 0) {
  $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
}
if (!done) { ctx.invalidate; code = "-9"; }
JSON.stringify(ok ? {ok: true, nonce: NONCE} : {ok: false, code: code});
"""


def available(platform=None, environ=None, exists=os.path.exists):
    """這台機器能不能走 Touch ID 這條路(不代表一定按得過)。"""
    platform = sys.platform if platform is None else platform
    env = os.environ if environ is None else environ
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return False
    return platform == "darwin" and exists(OSASCRIPT)


def clean(text, limit=80):
    """把要放進視窗的一段資料壓成一行、去掉控制字元、截長度(資料是卡上的標題,不是指令)。"""
    text = _CTRL.sub(" ", _INVISIBLE.sub("", str(text or ""))).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class one_at_a_time:
    """整台機器同時只准一個 Touch ID 核准視窗(fcntl 不等待鎖)。拿不到 ⇒ self.held=False,呼叫端要當沒過。
    免得兩席同時跑 --approve、疊兩個視窗,本人一個指紋核到另一件。"""

    def __init__(self, lock_path):
        self.path = str(lock_path)
        self.f = None
        self.held = False

    def __enter__(self):
        import fcntl
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self.f = open(self.path, "a")
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.held = True
        except OSError:          # BlockingIOError(別人拿著)或開不了檔 ⇒ 一律沒拿到(失效往低權)
            self.held = False
        return self

    def __exit__(self, *a):
        if self.f is not None:
            if self.held:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


def build_script(reason, nonce, wait=WAIT_S):
    """組 JXA。字串一律經 json.dumps(ensure_ascii)放進去,標題裡有引號、換行、反斜線都只是字。"""
    return (JXA.replace("__REASON__", json.dumps(str(reason)))
               .replace("__NONCE__", json.dumps(str(nonce)))
               .replace("__WAIT__", str(int(wait)))
               .replace("__POLICY__", str(POLICY_DEVICE_OWNER)))


def ask(reason, *, run=subprocess.run, wait=WAIT_S):
    """彈視窗等本人。回 {"ok": bool, "why": str}。reason 必須是 aib 自己組好的整句。"""
    nonce = secrets.token_hex(16)
    script = build_script(reason, nonce, wait)
    try:
        r = run([OSASCRIPT, "-l", "JavaScript", "-"], input=script, capture_output=True, text=True,
                timeout=wait + 15)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "timeout"}
    except OSError as exc:
        return {"ok": False, "why": "osascript_failed:%s" % exc.__class__.__name__}
    if r.returncode != 0:
        return {"ok": False, "why": "osascript_rc:%d" % r.returncode}
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return {"ok": False, "why": "unreadable"}
    if out.get("ok") is True and out.get("nonce") == nonce:
        return {"ok": True, "why": "ok"}
    if out.get("ok") is True:
        return {"ok": False, "why": "nonce_mismatch"}
    code = str(out.get("code"))
    return {"ok": False, "why": LA_CODES.get(code, "failed:%s" % code)}
