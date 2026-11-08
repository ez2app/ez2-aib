# SPDX-License-Identifier: Apache-2.0
"""Guarded tmux socket access shared by sandbox tests."""
import os
import subprocess
import shlex
import sys
from pathlib import Path


class SandboxRefused(AssertionError):
    pass


def reject_global_selector(args):
    """Reject socket selectors only among tmux's global options, before its command."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return
        if not arg.startswith("-") or arg == "-":
            return
        options = arg[1:]
        while options:
            flag, options = options[0], options[1:]
            if flag in "LS":
                raise SandboxRefused("不允許覆寫沙盒 socket")
            if flag in "cfT":
                if not options:
                    i += 1
                    if i >= len(args):
                        raise SandboxRefused("tmux 全域選項缺少參數")
                break
            if flag not in "2CDlNuvV":
                raise SandboxRefused("不認識 tmux 全域選項:%s" % flag)
        i += 1


class Sandbox:
    """只准打本測試暫存夾內的 socket。每次呼叫前驗:夾還在、socket 在夾內、不是任何預設 socket。"""

    def __init__(self, root, tmux_bin):
        self.root = Path(root)
        self.sock = self.root / "s"
        self.tmux_bin = tmux_bin

    def check(self):
        root = os.path.realpath(str(self.root))
        sock = os.path.realpath(str(self.sock))
        if not self.root.is_dir():
            raise SandboxRefused("沙盒夾不在了:%s" % self.root)
        if not sock.startswith(root + os.sep):
            raise SandboxRefused("socket 不在沙盒夾內:%s" % sock)
        if os.path.basename(sock) == "default" or "/tmux-%d/" % os.getuid() in sock:
            raise SandboxRefused("socket 像是預設位置:%s" % sock)

    def env(self):
        e = {k: v for k, v in getattr(self, "environ", os.environ).items() if k not in ("TMUX", "TMUX_TMPDIR", "TMUX_PANE")}
        return e

    def tm(self, *a, **kw):
        self.check()
        reject_global_selector(a)
        return subprocess.run([self.tmux_bin, "-S", str(self.sock)] + list(a), env=self.env(),
                              capture_output=True, text=True, timeout=10, **kw)

    def popen(self, *a):
        self.check()
        reject_global_selector(a)
        return subprocess.Popen([self.tmux_bin, "-S", str(self.sock)] + list(a), env=self.env(),
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def cleanup(self):
        # 清理也走守門:夾已經不在 / 路徑不對就什麼都不做(絕不退回預設 socket 去 kill-server)
        try:
            self.tm("kill-server")
        except SandboxRefused as exc:
            print("tmux 沙盒清理被拒絕: %s" % exc, file=sys.stderr)


    def make_shim(self, path, alias):
        """Adapt a tested program's -L alias to a guarded real -S socket."""
        self.check()
        p = Path(path)
        p.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(x) for x in
                     (sys.executable, str(Path(__file__).resolve()), str(self.root.resolve()),
                      str(self.sock.resolve()), str(Path(self.tmux_bin).resolve()), alias)) +
                     ' "$@"\n', encoding="utf-8")
        p.chmod(0o700)
        return str(p)


def _shim_main(argv):
    root, sock, tmux, alias, *args = argv
    box = Sandbox(root, tmux)
    box.sock = Path(sock)
    box.check()
    if len(args) < 2 or not ((args[0] == "-L" and args[1] == alias) or
                             (args[0] == "-S" and os.path.realpath(args[1]) == os.path.realpath(sock))):
        raise SandboxRefused("shim 只接受本測試的 socket")
    args = args[2:]
    reject_global_selector(args)
    env = box.env()
    os.execve(tmux, [tmux, "-S", sock, *args], env)


if __name__ == "__main__":
    _shim_main(sys.argv[1:])
