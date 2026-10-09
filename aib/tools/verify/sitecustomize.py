# SPDX-License-Identifier: Apache-2.0
# Loaded first on PYTHONPATH by site.py. Keep this file tiny.
# 10-08:跟 py-site/sitecustomize.py 同一套 —— 編譯好的稽核模組快取在 AIB_HOOK_CACHE(aib test 給),每個 python 省掉重新編譯。
import os as _os
import sys as _sys

_p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "sitecustomize_audit.py")
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
