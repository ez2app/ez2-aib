#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""root 擁有的固定路徑 aib-sehelper(系統錨定驗單第 1 張;規格 SPEC-anchored-grant-verify-e339d-1029,Andy 10-29 21:1x 裁 Q1 / Q3)。

#428 R1 H1:時間窗驗單信 <狀態根>/bin/aib-sehelper,那裡同帳號可寫、EZ2_AIB_STATE_ROOT 還換得掉 ⇒ 不算防線。
這支只認一個寫死的路徑 EXE,而且從它一路往上到 / 每一層都要:
  - 不是捷徑(lstat);擁有者 uid 0;群組 / 其他人都不能寫(mode & 0o022 == 0)。
  - 旁邊的 EXE.sha256(也要 root 擁有、不可寫)= 現在 sehelper 原始碼的 sha256;不同 ⇒ outdated(aib update 改了 helper
    ⇒ 時間窗自動關,doctor 提示本人重裝;Q1)。
  - 簽章帶 hardened runtime(codesign flags 有 runtime);沒有 ⇒ not_hardened(#432 R1 H1:dyld 看簽章旗標不看擁有者,
    沒 runtime 的 root binary 照樣吃 DYLD_INSERT_LIBRARIES ⇒ 同帳號 AI 帶個環境變數就能接管 helper)。
不吃任何環境變數 / PATH / 狀態根。任何一項不成立 ⇒ (None, 原因),呼叫端當沒有 helper(一律一次一按)。

安裝 / 更新由本人在終端跑(Q3,AI 不代跑):sudo /usr/bin/python3 -I <倉>/tools/sehelper/install_anchored_helper.py
  —— 以 root 從原始碼編、ad-hoc 簽、裝到 EXE、寫 stamp;中間產物都在 root 擁有的暫存目錄,同帳號行程換不掉。

照實寫:
  - 擋得住:換狀態根 / PATH / 覆寫使用者目錄裡的 binary(都不會被採用);帶 DYLD_INSERT_LIBRARIES 注入(hardened runtime
    ⇒ dyld 不理;叫 helper 也只給 PATH=/usr/bin:/bin)。
  - 擋不住(已定案邊界):AI 改 Python 核心程式碼(這支、approve_grants)跳過檢查;改 sehelper 原始碼後騙本人跑安裝
    (本人跑 sudo 前看得到 git 狀態,這是「改程式碼」那條)。
  - stat 失敗 / 讀不到 / 不是 Mac ⇒ 拒,不退回狀態根的 helper。
"""
import hashlib
import os
import re
import shlex
import stat as _stat
import subprocess
import sys

ANCHOR_DIR = "/usr/local/libexec/ez2-aib"
EXE = ANCHOR_DIR + "/aib-sehelper"
STAMP = EXE + ".sha256"
CODESIGN = "/usr/bin/codesign"
CLEAN_ENV = {"PATH": "/usr/bin:/bin"}       # 叫系統工具 / helper 一律給這份(不帶 DYLD_* / HOME / 呼叫端的任何東西)
RUNTIME_FLAG = 0x10000                      # CS_RUNTIME
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "sehelper", "aib-sehelper.swift")
REPO = os.path.dirname(os.path.dirname(HERE))
INSTALL_SCRIPT = os.path.join(REPO, "tools", "sehelper", "install_anchored_helper.py")


def _src_digest(src=SRC):
    with open(src, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _root_locked(path, lstat):
    """path 本身是 root 擁有、群組 / 其他人不可寫、不是捷徑。回 None 或原因。"""
    try:
        st = lstat(path)
    except OSError:
        return "missing"
    if _stat.S_ISLNK(st.st_mode):
        return "symlink"
    if st.st_uid != 0:
        return "not_root_owned"
    if st.st_mode & 0o022:
        return "writable"
    return None


def hardened(path, run=subprocess.run):
    """path 的簽章旗標有 hardened runtime(數值位元與名稱兩處都要有)。codesign 失敗 / 讀不懂 ⇒ False。"""
    try:
        p = run([CODESIGN, "-d", "--verbose=2", path], capture_output=True, text=True, timeout=10,
                env=dict(CLEAN_ENV), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    if p.returncode != 0:
        return False
    m = re.search(r"\bflags=0x([0-9a-fA-F]+)\(([^)]*)\)", (p.stderr or "") + "\n" + (p.stdout or ""))
    return bool(m) and bool(int(m.group(1), 16) & RUNTIME_FLAG) and "runtime" in m.group(2).split(",")


def check(lstat=os.lstat, read=None, src_digest=None, platform=None, run=subprocess.run):
    """回 (EXE, None) 或 (None, why)。why ∈ not_mac / not_installed / symlink / not_root_owned / writable / not_file /
    outdated / stamp_unreadable / not_hardened。lstat / read / src_digest / platform / run 只給測試注入(不讀環境變數)。"""
    if (platform or sys.platform) != "darwin":
        return None, "not_mac"
    for p in (EXE, STAMP):
        why = _root_locked(p, lstat)
        if why:
            return None, "not_installed" if why == "missing" else why
        if not _stat.S_ISREG(lstat(p).st_mode):
            return None, "not_file"
    d = ANCHOR_DIR
    while True:                          # 每一層目錄:root 擁有、不可寫、不是捷徑(否則改得了名 ⇒ 換得掉)
        why = _root_locked(d, lstat)
        if why:
            return None, "not_installed" if why == "missing" else why
        if d == "/":
            break
        d = os.path.dirname(d)
    try:
        got = (read or _read)(STAMP).strip()
        want = (src_digest or _src_digest)()
    except OSError:
        return None, "stamp_unreadable"
    if got != want:
        return None, "outdated"
    if not hardened(EXE, run):
        return None, "not_hardened"
    return EXE, None


def _read(path):
    with open(path, encoding="ascii") as f:
        return f.read(128)


def install_command():
    """給本人在終端跑的那一行(AI 不代跑)。"""
    return "sudo /usr/bin/python3 -I %s" % shlex.quote(INSTALL_SCRIPT)
