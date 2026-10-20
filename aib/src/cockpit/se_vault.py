#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Secure Enclave 封存:讀的時候由系統強制 Touch ID 的秘密(@abb89 審 #222 10-21:母鑰放本機檔 ⇒ 同帳號 AI 讀檔就拿到)。

    seal("CF_TOKEN_MINTER", value)                   ⇒ <狀態根>/secrets/se/CF_TOKEN_MINTER.sealed(不用指紋)
    unseal("CF_TOKEN_MINTER", reason)                ⇒ 系統彈 Touch ID,過了才回明文

實際加解密在 sehelper/aib-sehelper.swift(CryptoKit SecureEnclave.P256,accessControl = biometryCurrentSet)。
這支只負責:編譯 / 快取那支輔助程式、把值走 stdin / stdout 交給它、回 (value, why)。

規矩:
  - 值不進 argv、不寫檔、不印;stdout 讀回來就交給呼叫端。
  - 沒有略過指紋的路:封存檔被讀走也沒用(只有這台的 SE + 本人指紋解得開)。
  - 不是 Mac / 沒有 swiftc / 沒有 SE ⇒ 不給,不退回任何檔案。
  - 輔助程式編到 <狀態根>/bin/aib-sehelper,以原始碼 sha256 當快取鍵;編完 ad-hoc 簽(codesign -s -)。
    剩餘風險:那個位置是本人帳號可寫的,同帳號 AI 能換掉它(見 docs/CF-GRANT.md)。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "sehelper", "aib-sehelper.swift")
CLT_INCLUDE = "/Library/Developer/CommandLineTools/usr/include/swift"
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# 只有這些名字一定要放 SE;金鑰櫃(aib_secrets)拒寫、secret ask 改走 seal
SE_ONLY = ("CF_TOKEN_MINTER",)


def _state_root(env):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import seats_dir_lib
    return seats_dir_lib.state_root(env)[0]


def se_dir(env):
    return os.path.join(_state_root(env), "secrets", "se")


def has_sealed(name, env):
    return os.path.isfile(os.path.join(se_dir(env), name + ".sealed"))


def _vfs_overlay(tmpdir):
    """Command Line Tools 有兩份 SwiftBridging modulemap(module.modulemap + bridging.modulemap),swiftc 會報
    redefinition。用 VFS 把重複那份蓋成空檔,不用 sudo、不改系統檔。沒那個問題的機器不加。"""
    if not (os.path.isfile(os.path.join(CLT_INCLUDE, "module.modulemap"))
            and os.path.isfile(os.path.join(CLT_INCLUDE, "bridging.modulemap"))):
        return []
    path = os.path.join(tmpdir, "vfs.yaml")
    with open(path, "w") as f:
        json.dump({"version": 0, "case-sensitive": "false", "roots": [
            {"type": "directory", "name": CLT_INCLUDE,
             "contents": [{"type": "file", "name": "module.modulemap", "external-contents": "/dev/null"}]}]}, f)
    return ["-vfsoverlay", path]


def build_helper(env, run=subprocess.run, platform=None):
    """回 (輔助程式路徑, why)。原始碼沒變就用快取;變了才重編。"""
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return None, "not_mac"
    with open(SRC, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    bindir = os.path.join(_state_root(env), "bin")
    exe, stamp = os.path.join(bindir, "aib-sehelper"), os.path.join(bindir, "aib-sehelper.sha256")
    try:
        if os.path.isfile(exe) and open(stamp).read().strip() == digest:
            return exe, None
    except OSError:
        pass
    try:
        if run(["xcrun", "--find", "swiftc"], capture_output=True, text=True, timeout=30).returncode != 0:
            return None, "no_swiftc"
    except (OSError, subprocess.TimeoutExpired):
        return None, "no_swiftc"
    os.makedirs(bindir, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aib-sehelper-") as td:
        out = os.path.join(td, "aib-sehelper")
        try:
            r = run(["xcrun", "swiftc", "-O", "-parse-as-library"] + _vfs_overlay(td) + ["-o", out, SRC],
                    capture_output=True, text=True, timeout=600)
            if r.returncode != 0 or not os.path.isfile(out):
                return None, "build_failed"
            if run(["codesign", "-s", "-", "-f", out], capture_output=True, text=True, timeout=60).returncode != 0:
                return None, "sign_failed"
        except (OSError, subprocess.TimeoutExpired):
            return None, "build_failed"
        os.replace(out, exe)
    os.chmod(exe, 0o700)
    with open(stamp, "w") as f:
        f.write(digest + "\n")
    return exe, None


def _call(args, env, data, run, helper, platform):
    exe, why = (helper, None) if helper else build_helper(env, run=run, platform=platform)
    if not exe:
        return None, why
    try:
        r = run([exe] + args, input=data, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None, "helper_failed"
    if r.returncode != 0:
        why = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return None, (why[-1] if why and re.match(r"^[a-z_]{2,32}$", why[-1]) else "helper_failed")
    return r.stdout or b"", None


def seal(name, value, env=None, run=subprocess.run, helper=None, platform=None):
    """回 (fingerprint, why)。值只走 stdin。"""
    env = dict(os.environ if env is None else env)
    if not _NAME.match(name or ""):
        return None, "bad_name"
    if not isinstance(value, str) or not value.strip():
        return None, "empty"
    out, why = _call(["seal", "--name", name, "--dir", se_dir(env)], env, value.strip().encode("utf-8"), run, helper, platform)
    if why:
        return None, why
    try:
        res = json.loads(out.decode("utf-8"))
    except ValueError:
        return None, "helper_failed"
    return (res.get("fingerprint"), None) if res.get("ok") is True else (None, "helper_failed")


def unseal(name, reason, env=None, run=subprocess.run, helper=None, platform=None):
    """回 (value, why)。系統彈 Touch ID(視窗字 = reason,由 aib 組好傳進來)。沒封過 ⇒ not_found,不找別處。"""
    env = dict(os.environ if env is None else env)
    if not _NAME.match(name or ""):
        return None, "bad_name"
    if not has_sealed(name, env):
        return None, "not_found"
    out, why = _call(["unseal", "--name", name, "--dir", se_dir(env), "--reason", reason], env, b"", run, helper, platform)
    if why:
        return None, why
    value = out.decode("utf-8", "replace").strip()
    return (value, None) if value else (None, "empty")
