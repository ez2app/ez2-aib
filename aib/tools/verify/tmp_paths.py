# SPDX-License-Identifier: Apache-2.0
"""暫存路徑的所有寫法與正規化(043dd 11-09;Andy 18:1x 准「測試的暫存路徑統一用 helper 正規化成 <TMP>」)。

為什麼要有:同一個暫存資料夾在不同地方有不同寫法,測試把路徑寫進要比對的輸出時,各平台、各次跑都不一樣。
  macOS:tempfile 給 /var/folders/…/T,程式裡 resolve 之後變 /private/var/folders/…;/tmp 其實是 /private/tmp。
  Linux:/tmp/…;Windows:%TEMP%(C:\\Users\\RUNNER~1\\… 這種 8.3 短名,realpath 會展開成長名)。
  #570 就是這樣紅的:Linux 的暫存路徑剛好不到 40 字,被照原文記進對照表,每次都是隨機名字。

用法(測試從 aib/tests/aib_tmp_root.py 匯入同一組名字):
  norm_tmp(text, my_dir)       ⇒ my_dir(各種寫法)與系統暫存根換成 <TMP>;自己的資料夾比系統根長,先換。
  forms(path)                  ⇒ 一個根的寫法:abspath + realpath,長的在前(row_export 原本的 _roots,搬來這裡)。
  sub_root(text, roots, tag)   ⇒ 照給的順序換字;根後面接的是英數 . _ - 就不換(/tmpfoo 不是 /tmp)。

不留全域狀態:每次呼叫自己帶根(#570 a9136 N1:模組層級的 TMP_ROOTS 用完不清)。只用標準函式庫。
"""
import os
import re
import tempfile

TAG = "<TMP>"
_BOUND = r"(?=/|$|[^A-Za-z0-9._-])"     # 跟 row_export 原本的邊界一字不差(反斜線也算邊界)
_ENV_KEYS = ("AIB_TEST_TMPDIR", "TMPDIR", "TEMP", "TMP")


def forms(path):
    """一個根的所有寫法(abspath + realpath),長的在前;空的不收。只剩根目錄時記 "/"(sub_root 會跳過它)。"""
    out = []
    if not path:
        return out
    for p in (os.path.abspath(path), os.path.realpath(path)):
        p = p.rstrip("/") or "/"
        if p not in out:
            out.append(p)
    return sorted(out, key=len, reverse=True)


def tmp_roots(*extra):
    """呼叫端給的根 + 系統暫存根(tempfile、AIB_TEST_TMPDIR / TMPDIR / TEMP / TMP、/tmp)的所有寫法,長的在前、不重複。"""
    cands = [str(e) for e in extra if e] + [tempfile.gettempdir()]
    cands += [os.environ.get(k) or "" for k in _ENV_KEYS]
    if os.name != "nt":
        cands.append("/tmp")              # macOS 上 realpath 會補 /private/tmp
    out = []
    for c in cands:
        for f in forms(c):
            if f != "/" and f not in out:
                out.append(f)
    return sorted(out, key=len, reverse=True)


def sub_root(text, roots, tag):
    """照 roots 的順序把每個根換成 tag(呼叫端負責長的在前)。Windows 不分大小寫。"""
    flags = re.IGNORECASE if os.name == "nt" else 0
    for r in roots:
        if r in ("/", "\\"):
            continue
        text = re.sub(re.escape(r) + _BOUND, lambda _m: tag, text, flags=flags)
    return text


def norm_tmp(text, *roots, tag=TAG):
    """text 裡的暫存路徑(給的根 + 系統暫存根,各種寫法)換成 tag。非字串原樣回。"""
    if not isinstance(text, str):
        return text
    return sub_root(text, tmp_roots(*roots), tag)
