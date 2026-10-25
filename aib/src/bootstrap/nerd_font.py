#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""AI 符號字型:安裝 / 更新 aib 時一併補好(10-25 Andy「把 @ae6a1 的建議拿回來,預設在安裝或更新 aib 時一併優化」)。

起因:Mac Pro 13(fishmac)右欄 / 狀態列的 AI 符號是空白 —— 沒有含 U+EC82 的字型,而且 Apple Terminal 不能設備用字型。
@ae6a1 實際跑過的解法(pending/NOTE-icon-font-install-ae6a1-1025.md):

  status    只讀:字型檔有沒有、外層終端機畫不畫得出(engine_badge_mode 同一套判法)
  install   沒有就裝 JetBrainsMono Nerd Font v3.5.1(官方 release 的 tar.xz,約 7 MB,sha256 釘死);
            NF / NFM 各四個字重放進 ~/Library/Fonts/(不用 sudo、不用 Homebrew)。下載 / 驗證 / 解開任何一步不對 ⇒ 一個檔都不放
  terminal  Apple Terminal 設定檔字型畫不出 ⇒ 改成 JetBrainsMono NF(--mono ⇒ NFM)。改之前備份偏好檔、記下原字型,字級不動
  restore   把 terminal 改過的設定檔字型改回原值;之後你自己又改過 ⇒ 不動、提示(--force 才改)
  auto      安裝程式(install.sh,aib update 也是重跑它)呼叫:字型沒有就裝(不問);終端機畫不出 ⇒ --ask 且有終端機才問一次,
            只有回 y / m(或「是 / 好」)才改,其他(Enter、打錯字)一律不改;回答過就記住、之後不再問;沒得問 ⇒ 印一行怎麼改

規矩(#361 r1 M1 @abb89 裁示):改終端機字型是可還原的外觀設定,不上 Touch ID。auto 會先問;`aib fonts terminal` 是明確指令,
視同同意;終端機上的回答不證明是本人(同帳號 AI 也送得出按鍵)。非 Mac 什麼都不做;AIB_FONT_AUTO=0 整個關掉。
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE.parent / "cockpit" / "render"

NF_VERSION = "v3.5.1"                  # v3.4.0 沒有 U+EC82(@ae6a1 撲空過)
NF_URL = "https://github.com/ryanoasis/nerd-fonts/releases/download/%s/JetBrainsMono.tar.xz" % NF_VERSION
NF_SHA256 = "04d5e8f903693f9dd13e16f867e994834e681eb3c72c0d337a770dcda09010cf"   # 官方 SHA-256.txt(10-25 核對過)
NF_MAX_BYTES = 20 * 1024 * 1024
NF_TOTAL_S = 120                       # 下載總時限(#361 r1 M2:urlopen 的 timeout 只管單次 socket,慢送能拖很久)
BACKUP_KEEP = 3                        # 偏好檔備份只留最近幾份(#361 r1 L5)
YES = ("y", "yes", "是", "好", "要", "はい", "예", "m")
STYLES = ("Regular", "Bold", "Italic", "BoldItalic")
FAMILIES = {"nf": "JetBrainsMonoNerdFont", "nfm": "JetBrainsMonoNerdFontMono"}
PS_NAMES = {"nf": "JetBrainsMonoNF-Regular", "nfm": "JetBrainsMonoNFM-Regular"}   # Apple Terminal 設定檔用的字型名
STATE_FILE = "fonts.json"
SET_FONT_APPLESCRIPT = '''
on run argv
  set p to item 1 of argv
  set f to item 2 of argv
  tell application "Terminal"
    set font name of settings set p to f
    repeat with w in windows
      repeat with t in tabs of w
        try
          if name of current settings of t is p then set font name of current settings of t to f
        end try
      end repeat
    end repeat
  end tell
end run
'''


def _T(key, **params):
    cockpit = str(HERE.parent / "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    try:
        import i18n_lib
        return i18n_lib.t("aib.fonts." + key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:   # noqa: BLE001
        return "aib.fonts." + key


def _badge():
    if str(RENDER) not in sys.path:
        sys.path.insert(0, str(RENDER))
    import engine_badge_mode as M
    import render_lib as R
    return M, R


def _home(env):
    return Path(env.get("HOME") or os.path.expanduser("~"))


def fonts_dir(env):
    return _home(env) / "Library" / "Fonts"


def state_path(env):
    base = env.get("EZ2_AIB_STATE_ROOT") or str(_home(env) / ".ez2-aib")
    return Path(base) / "state" / STATE_FILE


def read_state(env):
    try:
        cur = json.loads(state_path(env).read_text(encoding="utf-8"))
        return cur if isinstance(cur, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(env, cur):
    p = state_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(p))


def status(env=None, runner=None, platform=None, terminal=True):
    """{"platform","font_ok","font_file","terminal":{ok,profile,font,missing}};只讀。terminal=False ⇒ 不查終端機(也不碰它的快取)。"""
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    M, R = _badge()
    cps = {ord(c) for c in R.ENGINE_ICONS.values()}
    forced = env.get(M.FONT_ENV)
    paths = [forced] if forced else sorted(str(p) for p in fonts_dir(env).glob("*NerdFont*-Regular.ttf"))
    missing, path = M.missing_glyphs(cps, paths)
    term = M.terminal_font_check(cps, env, runner, platform) if (terminal and not missing) else {"ok": None}
    return {"platform": platform, "font_ok": not missing, "font_file": os.path.basename(path) if path else None,
            "terminal": term}


def _fetch(url=NF_URL, timeout=30, total=None, opener=None, clock=time.monotonic):
    """分塊讀;超過總時限丟 TimeoutError(呼叫端回 timeout 碼,資料整份不要)。"""
    import urllib.request
    total = NF_TOTAL_S if total is None else total
    deadline = clock() + total
    buf = bytearray()
    with (opener or urllib.request.urlopen)(url, timeout=timeout) as r:      # noqa: S310  固定的官方 https 網址
        while True:
            if clock() > deadline:
                raise TimeoutError("total")
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            buf += chunk
            if len(buf) > NF_MAX_BYTES:
                raise ValueError("too_big")
    return bytes(buf)


def install(env=None, fetch=None, sha256=NF_SHA256, platform=None, force=False):
    """回 (code, detail)。code:already / installed / skipped_platform / timeout / download_failed / sha_mismatch / bad_archive / write_failed。
    任何一步不對 ⇒ 不放任何檔(先全部解到暫存夾,齊了才搬)。"""
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return "skipped_platform", None
    if not force and status(env, platform=platform, terminal=False)["font_ok"]:
        return "already", None
    try:
        data = (fetch or _fetch)()
    except TimeoutError:
        return "timeout", str(NF_TOTAL_S)
    except Exception as exc:  # noqa: BLE001  離線 / 太大
        return "download_failed", type(exc).__name__
    if hashlib.sha256(data).hexdigest() != sha256:
        return "sha_mismatch", None
    want = ["%s-%s.ttf" % (FAMILIES[f], s) for f in ("nf", "nfm") for s in STYLES]
    dest = fonts_dir(env)
    with tempfile.TemporaryDirectory(prefix="aib-nerdfont-") as td:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
                members = {m.name: m for m in tf.getmembers() if m.isfile()}
                for name in want:
                    m = members.get(name)
                    if m is None:
                        return "bad_archive", name
                    src = tf.extractfile(m)
                    with open(os.path.join(td, name), "wb") as fh:
                        shutil.copyfileobj(src, fh)
        except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
            return "bad_archive", type(exc).__name__
        tmps, moved = [dest / (".%s.aib-tmp" % n) for n in want], []
        try:                                         # 先全部複製成同夾暫名,齊了才逐一改名;中途失敗把這輪的都清掉(#361 r1 L4)
            dest.mkdir(parents=True, exist_ok=True)
            for name, tmp in zip(want, tmps):
                shutil.copyfile(os.path.join(td, name), str(tmp))
            for name, tmp in zip(want, tmps):
                os.replace(str(tmp), str(dest / name))
                moved.append(dest / name)
        except OSError as exc:
            for x in tmps + moved:
                try:
                    x.unlink()
                except OSError:
                    pass
            return "write_failed", type(exc).__name__
    cur = read_state(env)
    cur["installed"] = {"version": NF_VERSION, "at": int(time.time()), "files": want}
    write_state(env, cur)
    return "installed", None


def _osascript(argv, timeout=20):
    r = subprocess.run(["osascript", "-e", SET_FONT_APPLESCRIPT] + list(argv), capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode


def set_terminal_font(env=None, mono=False, runner=None, setter=None, platform=None):
    """回 (code, detail)。code:not_needed / not_apple_terminal / changed / failed。改之前備份偏好檔、記下原字型。"""
    env = dict(os.environ if env is None else env)
    st = status(env, runner, platform)
    term = st["terminal"]
    if not st["font_ok"]:
        return "font_missing", None
    if term.get("ok") is None:
        return "not_apple_terminal", None
    if term["ok"]:
        return "not_needed", None
    profile, old = term["profile"], term["font"]
    plist = _home(env) / "Library" / "Preferences" / "com.apple.Terminal.plist"
    backup = None
    if plist.is_file():
        backup = plist.with_name(plist.name + ".bak-font-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy2(str(plist), str(backup))
        except OSError as exc:
            return "failed", "backup:" + type(exc).__name__      # 備份不成就不改
        for old_bak in sorted(plist.parent.glob(plist.name + ".bak-font-*"))[:-BACKUP_KEEP]:
            try:
                old_bak.unlink()
            except OSError:
                pass
    to = PS_NAMES["nfm" if mono else "nf"]
    try:
        rc = (setter or _osascript)([profile, to])
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", type(exc).__name__
    if rc != 0:
        return "failed", "osascript:%s" % rc
    cur = read_state(env)                            # osascript 成功才記(#361 r1 L6)
    orig = cur.setdefault("terminal_original", {})
    orig.setdefault(profile, old)                    # 第一次改之前的原字型才算原值
    cur["terminal_changed"] = {"profile": profile, "to": to, "at": int(time.time()),
                               "backup": str(backup) if backup else None}
    cur["terminal_answered"] = int(time.time())
    write_state(env, cur)
    try:                                             # 徽章的終端機字型快取作廢,下一輪就照新字型判
        M, _R = _badge()
        M._term_cache.clear()
        os.unlink(str(M._cache_path(env)))
    except (OSError, AttributeError):
        pass
    return "changed", profile


GET_FONT_APPLESCRIPT = 'on run argv\n  tell application "Terminal" to get font name of settings set (item 1 of argv)\nend run'


def _get_font(profile, timeout=10):
    r = subprocess.run(["osascript", "-e", GET_FONT_APPLESCRIPT, profile], capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    return r.stdout.strip() if r.returncode == 0 else None


def restore_terminal_font(env=None, setter=None, getter=None, force=False):
    """改回原字型。現在的字型不是我們設的那個(使用者之後自己改過)⇒ 回 user_changed、不動;force 才改(#361 r1 L5)。"""
    env = dict(os.environ if env is None else env)
    cur = read_state(env)
    orig = cur.get("terminal_original") or {}
    if not orig:
        return "nothing", None
    ours = (cur.get("terminal_changed") or {}).get("to")
    if not force:
        try:
            now = {p: (getter or _get_font)(p) for p in orig}
        except (OSError, subprocess.SubprocessError):
            now = {}
        changed = sorted(p for p in orig if now.get(p) not in (ours, None))   # 讀不到現值 ⇒ 不擋
        if changed:
            return "user_changed", ",".join(changed)
    bad = []
    for profile, font in orig.items():
        try:
            if (setter or _osascript)([profile, font]) != 0:
                bad.append(profile)
        except (OSError, subprocess.SubprocessError):
            bad.append(profile)
    if bad:
        return "failed", ",".join(bad)
    cur.pop("terminal_original", None)
    cur.pop("terminal_changed", None)
    write_state(env, cur)
    return "restored", ",".join(orig)


def _ask(prompt, inp, out):
    out.write(prompt)
    out.flush()
    ans = (inp.readline() or "").strip().lower()
    return ans


def auto(env=None, ask=False, inp=None, out=None, fetch=None, runner=None, setter=None, platform=None, isatty=None,
         do_install=True, do_terminal=True):
    """安裝程式用。回 0(不擋安裝)。install.sh 分兩次叫:先 auto(有總時限、不碰終端機),再 auto --ask --no-install(問人)。"""
    env = dict(os.environ if env is None else env)
    out = out or sys.stdout
    inp = inp or sys.stdin
    platform = sys.platform if platform is None else platform
    if env.get("AIB_FONT_AUTO") == "0" or platform != "darwin":
        return 0
    code, detail = install(env, fetch=fetch, platform=platform) if do_install else ("already", None)
    if code == "installed":
        out.write(_T("installed", version=NF_VERSION) + "\n")
    elif code not in ("already",):
        out.write(_T("install_failed", code=code) + "\n")
        return 0
    if not do_terminal:                              # install.sh 接著要用 --ask 問 ⇒ 這次不印提示,免得同一件事出現兩次(#361 r2 Nit)
        return 0
    st = status(env, runner, platform)
    term = st["terminal"]
    if not st["font_ok"] or term.get("ok") is not False:
        return 0
    cur = read_state(env)
    tty = (inp.isatty() if isatty is None else isatty)
    if cur.get("terminal_declined") or cur.get("terminal_answered") or not (ask and tty):
        out.write(_T("terminal_hint", profile=term["profile"], font=term["font"]) + "\n")
        return 0
    out.write(_T("terminal_ask", profile=term["profile"], font=term["font"]) + " ")
    out.flush()
    raw = inp.readline()
    ans = (raw or "").strip().lower()
    if ans not in YES:                               # 只認肯定字;Enter / 打錯字 / 其他 ⇒ 不改(#361 r1 L3)
        if raw:                                      # 有回答(含 Enter)才記住;EOF(沒人回答)下次再問
            cur["terminal_declined"] = int(time.time())
            write_state(env, cur)
        out.write(_T("terminal_declined") + "\n")
        return 0
    mono = (ans == "m")
    code, detail = set_terminal_font(env, mono=mono, runner=runner, setter=setter, platform=platform)
    out.write((_T("terminal_changed", profile=detail, font=PS_NAMES["nfm" if mono else "nf"]) if code == "changed" else _T("terminal_failed", code=code)) + "\n")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="aib fonts", description="AI 符號字型(JetBrainsMono Nerd Font)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("install")
    t = sub.add_parser("terminal")
    t.add_argument("--mono", action="store_true", help="用 NFM(符號小一格、不壓到後面的字)")
    sub.add_parser("restore")
    a_ = sub.add_parser("auto")
    a_.add_argument("--ask", action="store_true")
    a_.add_argument("--no-install", action="store_true")
    a_.add_argument("--no-terminal", action="store_true")
    r_ = sub.choices["restore"]
    r_.add_argument("--force", action="store_true", help="現在的字型被你自己改過也照樣改回")
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cmd = a.cmd or "status"
    if cmd == "auto":
        return auto(ask=a.ask, do_install=not a.no_install, do_terminal=not a.no_terminal)
    if cmd == "status":
        res = status()
        if getattr(a, "json", False):
            print(json.dumps(res, ensure_ascii=False))
        else:
            t = res["terminal"]
            print(_T("status_font_ok", file=res["font_file"]) if res["font_ok"] else _T("status_font_missing"))
            if t.get("ok") is False:
                print(_T("terminal_hint", profile=t["profile"], font=t["font"]))
            elif t.get("ok"):
                print(_T("status_terminal_ok"))
        return 0
    if cmd == "install":
        code, detail = install(force=False)
    elif cmd == "terminal":
        code, detail = set_terminal_font(mono=a.mono)
    else:
        code, detail = restore_terminal_font(force=a.force)
    if getattr(a, "json", False):
        print(json.dumps({"code": code, "detail": detail}, ensure_ascii=False))
    else:
        print(_T("code." + code, detail=detail or ""))
    return 0 if code in ("already", "installed", "changed", "not_needed", "restored", "nothing", "skipped_platform") else 2


if __name__ == "__main__":
    sys.exit(main())
