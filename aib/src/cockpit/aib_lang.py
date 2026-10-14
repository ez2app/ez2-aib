#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib lang:看 / 切整台 AIB 的語言(10-14 aib-languages,the maintainer「所有介面、按鈕、mod、顯示、對話都要對應到」)。

  aib lang                    現在的語系、從哪來、可選哪些
  aib lang <語系> [--cabin]    寫人檔 <狀態根>/locale(這台全部艙);--cabin 寫艙檔 $EZ2_AIB_INSTANCE_HOME/locale(只這一艙)
  aib lang --clear [--cabin]  刪掉那一層,退回下一層
  --json                      給 mod / 艦長室網頁用;--no-notify 不寄 dm

順位(i18n_lib.resolve_locale):明給 > env EZ2_AIB_LOCALE > 艙檔 > 人檔 > zh-TW。
切完:1. 用**新語系**印結果(人當場看到換了);2. 每個活席寄一封 dm 請從下一句起改口(非 Claude 席靠這封;
Claude 席另有 aib-languages mod 的 prompt.compose);3. 不碰 tmux 全域環境 —— 環境變數排在檔案之上,
一旦寫進 tmux 就再也蓋不掉艙檔,而所有每輪重算的畫面都看檔。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18n_lib  # noqa: E402
import seat_address  # noqa: E402
import seat_pool  # noqa: E402
import seats_dir_lib  # noqa: E402

LAYERS = ("cabin", "user")


def T(key, params=None, locale=None):
    return i18n_lib.t("aib.lang." + key, params, locale=locale)


def _source_label(source, locale):
    return T("source_" + source, locale=locale) if source in ("explicit", "env", "cabin", "user", "system", "default") else source


def write_locale_file(path, loc):
    """一行語系;暫存檔 + rename(半寫的檔不會被別的行程讀到)。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(loc + "\n")
    os.replace(tmp, path)


def live_seat_addresses(environ):
    """活艙裡 pid 還在的席 → <艙位碼>/<席碼>。艙位碼 = 艙 id 最後一個 - 之後四碼(the maintainer 09-28)。"""
    out = []
    injected = environ.get(seat_pool.ENV_CABINS)
    hubs = [c for c in injected.split(",") if c] if injected is not None else seat_pool.live_cabins()
    for hub in hubs:
        inst = hub[len("aihub-"):]
        cabin_code = inst.rsplit("-", 1)[-1]
        seats_dir = seats_dir_lib.seats_dir_with_source(hub=hub, environ=environ)[0]   # 席位目錄向單源要,不自己拼
        try:
            names = sorted(os.listdir(seats_dir))
        except OSError:
            continue
        for name in names:
            if not (name.startswith("w") and name.endswith(".json")):
                continue
            try:
                with open(os.path.join(seats_dir, name), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            pid = rec.get("pid")
            if isinstance(pid, int) and pid > 0:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    pass
            code = seat_address.code_of(rec)
            if code:
                out.append("%s/%s" % (cabin_code, code))
    return out


def notify_seats(addrs, text, environ, dm_bin=None):
    """每席一封;回送成功的席數。送不出去不擋(語系已經切了)。AIB_LANG_DM_BIN 給測試換假的。"""
    cmd = dm_bin or environ.get("AIB_LANG_DM_BIN") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "aib")
    ok = 0
    for addr in addrs:
        try:
            rc = subprocess.run([cmd, "dm", addr, text], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                timeout=120, env=dict(environ)).returncode
        except (OSError, subprocess.SubprocessError):
            rc = -1
        ok += rc == 0
    return ok


def status(environ):
    loc, src = i18n_lib.resolve_locale_source(env=environ)
    return {"locale": loc, "source": src, "choices": list(i18n_lib.CANONICAL_LOCALES),
            "files": {layer: path for layer, path in i18n_lib.locale_file_candidates(environ)}}


def main(argv=None, out=None, err=None, environ=None, dm_bin=None):
    out = out or sys.stdout
    err = err or sys.stderr
    environ = dict(os.environ if environ is None else environ)
    ap = argparse.ArgumentParser(prog="aib lang", add_help=False)
    ap.add_argument("locale", nargs="?")
    ap.add_argument("--cabin", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        print(T("usage"), file=err)
        return 2
    cur = status(environ)
    if a.help:
        print(T("usage", locale=cur["locale"]), file=out)
        return 0
    layer = "cabin" if a.cabin else "user"
    if layer == "cabin" and "cabin" not in cur["files"]:
        print(T("need_cabin", locale=cur["locale"]), file=err)
        return 2
    path = cur["files"][layer]
    notified = 0

    if a.clear:
        try:
            os.remove(path)
            removed = True
        except FileNotFoundError:
            removed = False
        except OSError as e:
            print(T("write_failed", {"path": path, "what": str(e)}, locale=cur["locale"]), file=err)
            return 1
        cur = status(environ)
        print(T("cleared" if removed else "nothing_to_clear", {"path": path, "value": cur["locale"]},
                locale=cur["locale"]), file=out)
    elif a.locale:
        loc = i18n_lib.normalize_locale(a.locale)
        if not loc:
            print(T("bad_value", {"what": a.locale, "value": ", ".join(cur["choices"])}, locale=cur["locale"]), file=err)
            return 2
        try:
            write_locale_file(path, loc)
        except OSError as e:
            print(T("write_failed", {"path": path, "what": str(e)}, locale=cur["locale"]), file=err)
            return 1
        cur = status(environ)
        print(T("set", {"value": loc, "path": path}, locale=loc), file=out)
        if cur["locale"] != loc:      # 上面還有一層壓著(環境變數或艙檔),說清楚,不假裝切成功
            print(T("shadowed", {"value": cur["locale"], "what": _source_label(cur["source"], loc)}, locale=loc), file=err)
        if not a.no_notify:
            addrs = live_seat_addresses(environ)
            notified = notify_seats(addrs, T("notice", {"value": loc}, locale=loc), environ, dm_bin)
            print(T("notified", {"n": notified}, locale=loc), file=out)
    else:
        loc = cur["locale"]
        print(T("current", {"value": loc, "what": _source_label(cur["source"], loc)}, locale=loc), file=out)
        print(T("choices", {"what": ", ".join(cur["choices"])}, locale=loc), file=out)
    if a.json:
        print(json.dumps(dict(cur, notified=notified), ensure_ascii=False, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
