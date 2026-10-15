#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""跨主機 ssh 加固(10-11,ACB 搬回 A3;源自 ACB tests/test-l6-ssh-hardening.sh)。

AIB 程式裡每一個真的會連到別台主機的 ssh 呼叫,都要帶:
  -o ForwardAgent=no         不把本機金鑰代理借給對方(對方被入侵時拿不到你的金鑰)
  -o ClearAllForwardings=yes 不開任何轉發通道
ACB 還要求 IdentitiesOnly=yes;AIB 這次**不要求** —— 它會讓 ssh 不用金鑰代理裡的金鑰,靠代理登入的機器會連不上,要另外確認。

R1 src/ 與 tools/ 底下(git 追蹤的 .sh/.py,不含 tests/、文件)每一行 ssh 呼叫都帶兩個選項
R2 判準自己會抓:餵一段沒帶選項的寫法,恰好抓到那幾行;註解、ssh-keygen、ssh_config、印給人看的字、叫 ssh 的變數不算
   (10-11 疊新底:captain_room.py 的說明文字/錯誤訊息被誤抓 4 行,真正組指令的 Python 清單 [_ssh_bin(), ...] 反而沒抓到)
R3 Python 清單寫法(["ssh", ...] 或 [_ssh_bin(), ...])組 ssh 指令也要帶兩個選項
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r'(?:^|[\s;(|&"`=\[])ssh(?:\s|$)')
LIST_CALL = re.compile(r'\[\s*(?:"ssh"|\'ssh\'|_ssh_bin\(\))\s*,')     # Python 清單組 ssh 指令
TEXT = re.compile(r'\bsay\b|\becho\b|printf|\.write\(|"""|"err":|\bssh\s+or\b')   # 印給人看的字 / 說明 / 變數,不是呼叫
NEED = ("ForwardAgent=no", "ClearAllForwardings=yes")


def offenders(text):
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if not code.strip():
            continue
        if not LIST_CALL.search(code):
            if not CALL.search(code) or TEXT.search(code):
                continue
        if not all(k in code for k in NEED):
            bad.append(n)
    return bad


def tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", "src", "tools"], capture_output=True,
                         text=True, stdin=subprocess.DEVNULL, check=True).stdout.split()
    return [p for p in out if p.endswith((".sh", ".py")) or "." not in Path(p).name]


class SshHardening(unittest.TestCase):
    def test_r1_every_ssh_call_is_hardened(self):
        bad = []
        for rel in tracked():
            p = ROOT / rel
            if not p.is_file() or p.is_symlink():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            bad += ["%s:%d" % (rel, n) for n in offenders(text)]
        self.assertEqual(bad, [], "這些 ssh 呼叫沒帶 %s:\n%s" % (" / ".join(NEED), "\n".join(bad)))

    def test_r2_the_rule_catches_bad_lines_only(self):
        sample = "\n".join([
            'ssh "$HOST" uptime',                                                  # 1 壞
            'SSH=(ssh -o BatchMode=yes "$HOST")',                                  # 2 壞
            'ssh -o ForwardAgent=no -o ClearAllForwardings=yes "$H" true',         # 3 好
            '# ssh "$HOST" 註解',                                                  # 4 不算
            'ssh-keygen -t ed25519',                                               # 5 不算
            'say "這台沒有 ssh 金鑰"',                                              # 6 不算
            'export GIT_SSH_COMMAND="ssh -o BatchMode=yes"',                       # 7 壞
            '    """ssh to use; tests point AIB_ROOM_SSH at a fake."""',              # 8 不算(說明)
            '    return {"ok": False, "err": "ssh rc=%d" % rc}',                      # 9 不算(錯誤訊息)
            '    out.write("先確定 `ssh %s` 能登入" % ssh)',                            # 10 不算(印給人看)
            '    if not re.fullmatch(r"x", ssh or ""):',                              # 11 不算(變數)
        ])
        self.assertEqual(offenders(sample), [1, 2, 7])

    def test_r3_python_list_argv_is_checked(self):
        sample = "\n".join([
            '    argv = [_ssh_bin(), "-o", "ConnectTimeout=5"]',                                          # 1 壞
            '    argv = ["ssh", "-o", "BatchMode=yes", host]',                                            # 2 壞
            '    argv = [_ssh_bin(), "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", h]',       # 3 好
        ])
        self.assertEqual(offenders(sample), [1, 2])


if __name__ == "__main__":
    unittest.main()
