# SPDX-License-Identifier: Apache-2.0
# 10-04 晚(修法 A 第三版):只給「測試換掉/拿掉 PYTHONPATH」的子 python 用,由 sitecustomize_audit 附在它 PYTHONPATH 的最後。
# 只載稽核模組本身:不把 tools/verify 加進 sys.path(那裡有 runner/planner/receipt… 會蓋過或救回子程式的 import),
# 也不多 import 任何模組(觀測者不改被觀測物的 sys.modules)。
# 10-08:編譯好的程式碼快取起來(以前每個 python 都重新編譯 1500 行,約 13 毫秒;測試環境不准寫 __pycache__)。
#   鑰匙 = 原始碼大小 + 修改時間 + 這支 python 的版本標記(跟 Python 自己的 pyc 同一套判法);只用已經載入的 marshal / os。
import os as _os
import sys as _sys

_p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "sitecustomize_audit.py")
if "sitecustomize_audit" not in _sys.modules and _os.path.isfile(_p):
    _m = type(_sys)("sitecustomize_audit")
    _m.__file__ = _p
    _sys.modules["sitecustomize_audit"] = _m
    _code = None
    _st = _os.stat(_p)
    _dir = _os.environ.get("AIB_HOOK_CACHE") or ""
    _key = _os.path.join(_dir, "audit-%d-%d-%s.bin" % (_st.st_size, _st.st_mtime_ns, _sys.implementation.cache_tag)) if _dir else ""
    if _key:
        try:
            with open(_key, "rb") as _f:
                _code = __import__("marshal").loads(_f.read())
        except (OSError, ValueError, EOFError, TypeError):
            _code = None
    if _code is None:
        with open(_p, "rb") as _f:
            _code = compile(_f.read(), _p, "exec")
        if _key:
            try:
                _tmp = "%s.%d.tmp" % (_key, _os.getpid())
                with open(_tmp, "wb") as _f:
                    _f.write(__import__("marshal").dumps(_code))
                _os.replace(_tmp, _key)
            except OSError:
                pass
    exec(_code, _m.__dict__)
del _os, _sys, _p
