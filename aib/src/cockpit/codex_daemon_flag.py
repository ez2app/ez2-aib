#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""codex 的 --no-daemon 旗標:只有這支 codex 真的認得才帶(單一判法,兩處呼叫者共用)。

為什麼要判:codex 0.158.0 起互動介面把對話與工具 shell 交給常駐 app-server daemon,
席位行程樹底下什麼都沒有;--no-daemon 讓它回到自己的行程底下。但舊版 codex(實測 Fish 上
0.144.1)的 --help 沒有這個參數,硬帶會印用法、rc=2 ⇒ 該機器所有 codex 席開席即失敗。
判法=問**實際要執行的那個 codex**:`<bin> --help` 輸出含 `--no-daemon`。
逾時、跑不起來、輸出沒有 ⇒ 不帶(寧可回到舊行為,也不讓開席失敗),並在 stderr 記一行原因。

CLI:codex_daemon_flag.py <codex 執行檔>  → 支援時印 `--no-daemon`(一行),否則什麼都不印;rc 恆 0。
"""
import subprocess
import sys

FLAG = "--no-daemon"
HELP_TIMEOUT_S = 8


def supported(codex_bin, why=None):
    """(bool)。why:可選 dict,填 reason。"""
    def _no(reason):
        if why is not None:
            why["reason"] = reason
        return False
    try:
        out = subprocess.run([codex_bin, "--help"], capture_output=True, text=True,
                             timeout=HELP_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return _no("--help 逾時 %ds" % HELP_TIMEOUT_S)
    except OSError as exc:
        return _no("--help 跑不起來:%s" % type(exc).__name__)
    if FLAG in (out.stdout or "") + (out.stderr or ""):
        return True
    return _no("這支 codex 的 --help 沒有 %s(舊版)" % FLAG)


def flag_args(codex_bin):
    """回要插進 codex 命令列的旗標 list:支援=[--no-daemon],否則 [](並在 stderr 記原因)。"""
    why = {}
    if supported(codex_bin, why):
        return [FLAG]
    print("codex_daemon_flag: 不帶 %s:%s" % (FLAG, why.get("reason")), file=sys.stderr)
    return []


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: codex_daemon_flag.py <codex-bin>", file=sys.stderr)
        return 2
    for a in flag_args(argv[0]):
        print(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
