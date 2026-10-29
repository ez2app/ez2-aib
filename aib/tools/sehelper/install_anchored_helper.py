#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""把 aib-sehelper 以 root 編好、裝到 root 擁有的固定路徑(系統錨定驗單第 1 張;Andy 10-29 21:1x 裁 Q1 / Q3)。

本人在終端跑(AI 不代跑):
    sudo /usr/bin/python3 -I <倉>/aib/tools/sehelper/install_anchored_helper.py

做的事(全部以 root):
  1. 讀 <倉>/aib/src/cockpit/sehelper/aib-sehelper.swift,算 sha256。
  2. 在 root 擁有的 0700 暫存目錄裡 xcrun swiftc 編、codesign -s - 簽(同帳號行程碰不到中間產物)。
  3. 建 /usr/local/libexec/ez2-aib(root:wheel 0755),binary 0755、stamp(原始碼 sha256)0644,都 root:wheel,
     先寫 .new 再改名(換的那一刻不會有半個檔)。
  4. 往上每一層目錄都要 root 擁有、不可寫;不是就停下來、什麼都不裝(例:Homebrew 把 /usr/local 改成使用者擁有)。
不 import 倉庫裡任何模組(-I 也不讀使用者 site-packages / 環境變數),root 只讀這支與 swift 原始碼。
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile

ANCHOR_DIR = "/usr/local/libexec/ez2-aib"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "src", "cockpit", "sehelper", "aib-sehelper.swift"))
CLT_INCLUDE = "/Library/Developer/CommandLineTools/usr/include/swift"


def _vfs_overlay(tmpdir):
    """同 se_vault._vfs_overlay(CLT 兩份 SwiftBridging modulemap 會 redefinition);這裡不 import 它。"""
    if not (os.path.isfile(os.path.join(CLT_INCLUDE, "module.modulemap"))
            and os.path.isfile(os.path.join(CLT_INCLUDE, "bridging.modulemap"))):
        return []
    path = os.path.join(tmpdir, "vfs.yaml")
    with open(path, "w") as f:
        json.dump({"version": 0, "case-sensitive": "false", "roots": [
            {"type": "directory", "name": CLT_INCLUDE,
             "contents": [{"type": "file", "name": "module.modulemap", "external-contents": "/dev/null"}]}]}, f)
    return ["-vfsoverlay", path]


def _parents_locked(d, lstat):
    """d 的每一層上層(不含 d 本身)都 root 擁有、不可寫、不是捷徑。回 None 或那一層的路徑。
    還不存在的那幾層略過(等一下以 root 建,umask 022 ⇒ 0755 root:wheel)。"""
    p = os.path.dirname(d)
    while True:
        try:
            st = lstat(p)
        except FileNotFoundError:
            p = os.path.dirname(p)
            continue
        except OSError:
            return p
        if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            return p
        if p == "/":
            return None
        p = os.path.dirname(p)


def install(src=SRC, anchor_dir=ANCHOR_DIR, run=subprocess.run, euid=os.geteuid, chown=os.chown,
            lstat=os.lstat, makedirs=os.makedirs, out=sys.stdout, platform=None):
    """回 rc(0 = 裝好)。參數只給測試注入(目的地 / 編譯 / 擁有者 / 平台);正式只走 main()。"""
    if (platform or sys.platform) != "darwin":
        out.write("aib: 只有 Mac 需要(也只能)裝 Secure Enclave helper\n")
        return 2
    if euid() != 0:
        out.write("aib: 要用 sudo 跑(本人在終端打):sudo /usr/bin/python3 -I %s\n" % os.path.abspath(__file__))
        return 2
    bad = _parents_locked(anchor_dir, lstat)
    if bad:
        out.write("aib: %s 不是 root 擁有 / 別人寫得進去 ⇒ 裝在這底下換得掉,不裝\n" % bad)
        return 3
    with open(src, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    td = tempfile.mkdtemp(prefix="aib-sehelper-root-")          # 0700,擁有者 = 跑的人(root)
    try:
        exe_tmp = os.path.join(td, "aib-sehelper")
        r = run(["/usr/bin/xcrun", "swiftc", "-O", "-parse-as-library"] + _vfs_overlay(td) + ["-o", exe_tmp, src],
                capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not os.path.isfile(exe_tmp):
            out.write("aib: 編譯失敗(xcrun swiftc rc %s)\n" % r.returncode)
            return 4
        if run(["/usr/bin/codesign", "-s", "-", "-f", exe_tmp], capture_output=True, text=True, timeout=120).returncode != 0:
            out.write("aib: ad-hoc 簽章失敗\n")
            return 4
        makedirs(anchor_dir, mode=0o755, exist_ok=True)
        chown(anchor_dir, 0, 0)
        os.chmod(anchor_dir, 0o755)
        exe, stamp = os.path.join(anchor_dir, "aib-sehelper"), os.path.join(anchor_dir, "aib-sehelper.sha256")
        with open(exe_tmp, "rb") as f:
            data = f.read()
        for path, body, mode in ((exe, data, 0o755), (stamp, (digest + "\n").encode(), 0o644)):
            fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            chown(path + ".new", 0, 0)
            os.chmod(path + ".new", mode)
            os.replace(path + ".new", path)
        out.write("aib: 已裝 %s(原始碼 sha256 %s)\n" % (exe, digest[:16]))
        return 0
    finally:
        for n in os.listdir(td):
            os.unlink(os.path.join(td, n))
        os.rmdir(td)


def main():
    return install()


if __name__ == "__main__":
    sys.exit(main())
