#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Python startup audit hook for VERIFY-SHADOW-v1.

Records open/import/listdir/subprocess argv into EZ2_VERIFY_TRACE (jsonl).
_IN_HOOK blocks recursion when the hook itself reads files to hash them.
"""
from __future__ import annotations

import hashlib
import io       # 直譯器啟動就載入了(sys.stdout 用它),不算多 import
import json
import os
import re
import sys

_IN_HOOK = False
from _thread import get_ident as _get_ident   # 內建模組,不多 import threading(觀測者不改被觀測物的 sys.modules)
from _thread import _local as _ThreadLocal   # 同上:每執行緒一份,不 import threading
from _weakref import ref as _weakref          # 同上:seek 鉤子只弱參照檔案物件
_DIRFD_CTX = {}   # 執行緒 id -> os.open(..., dir_fd=d) 呼叫期間 d 的絕對路徑(見 _DirFdOpenWrapper)
_GIT_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
                 "GIT_CONFIG_PARAMETERS", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_REPLACE_REF_BASE",
                 "GIT_NO_REPLACE_OBJECTS", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE")   # 與 shadow_tracer.GIT_REPO_ENV 同一組
_COPY_PENDING = {}   # 10-05:執行緒 id -> (來源, 複本);shutil.copyfile 事件到「下一筆 open」之間有效,其他任何事件都清掉
_CREATED = set()   # 10-07:本行程**新建**的檔(第一次寫之前不存在);列目錄時另算一份不含它們的名單雜湊
_WRITTEN = set()   # 本行程寫過的路徑;結束時把它們的最終內容 hash 記成 selfwrite(大綱第 28 項契約:被正規化的路徑要有內容指紋)
_STARTED = False

_ABS_EXEC = frozenset(("cat", "cp", "head", "tail"))


def _trace_path():
    return os.environ.get("EZ2_VERIFY_TRACE") or ""


def _emit(obj):
    path = _trace_path()
    if not path:
        return
    # 96:每筆帶 pid ——「自己寫過的再讀不是輸入」只對**同一個行程**成立;子行程讀父行程寫的檔,那是交出去的內容。
    if "pid" not in obj:
        obj = dict(obj, pid=os.getpid())
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    # 10-04:U+2028/U+2029/U+0085 原樣寫出會被讀的那邊 splitlines() 切成兩行(見 jsonl_safe.py);這支在每個子行程裡跑,不另 import
    line = line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("\x85", "\\u0085")
    try:
        line.encode("utf-8")
    except UnicodeEncodeError:
        # 10-06(click 實踩):檔名不是合法 UTF-8(os.fsdecode 留下 \udcxx 代理字元)⇒ 原樣寫不進 UTF-8 檔,
        #   以前在這裡丟例外 ⇒ hook-error ⇒ 整支量不到。改用 \u 跳脫寫出,讀回 json.loads 得到同一個字串。
        line = json.dumps(obj, ensure_ascii=True, sort_keys=True)
    try:
        data = (line + "\n").encode("utf-8")
        with _TRACE_LOCK:
            fd = _trace_fd(path)
            while data:
                data = data[os.write(fd, data):]
    except OSError:
        pass   # 10-04:觀測者不得讓被觀測物出錯(例:子行程降成一般身分寫不進 root 的追蹤檔);少的紀錄由分析端判量不到


# 10-24(@044e0 本機效能):以前每筆 open(path, "a") → 寫 → 關。APFS 上每次關檔都把整塊重寫一次,實測 1000 筆
#   131 KB 的追蹤實際寫進磁碟 9.9 MB(76 倍);一天 3500 支次測試 ⇒ Python 一天寫 1.1 TB(10-23→24 系統 disk writes 報告)。
#   改成每個行程留一個 O_APPEND 的 fd、每筆 os.write 一次(實測 1 倍)。整行一次 write,多行程同時追加不會交錯。
#   ★ 寫之前比 fstat 的 (dev, ino):測試自己 closerange 後又開檔拿到同一個號碼 ⇒ 對不上就重開,絕不寫進別人的檔。
#   fd 帶 O_CLOEXEC(子行程 exec 後不帶走);fork 出來的子行程沿用同一個描述(同一個檔、O_APPEND),身分照樣比對。
_TRACE_FD = [None, None, None]          # [路徑, fd, (st_dev, st_ino)]
from _thread import allocate_lock as _allocate_lock   # 內建模組,不多 import threading
_TRACE_LOCK = _allocate_lock()


def _trace_fd(path):
    c = _TRACE_FD
    if c[0] == path and c[1] is not None:
        try:
            st = os.fstat(c[1])
            if (st.st_dev, st.st_ino) == c[2]:
                return c[1]
        except OSError:
            pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o666)
    st = os.fstat(fd)
    c[:] = [path, fd, (st.st_dev, st.st_ino)]
    return fd


def _as_path(value):
    if isinstance(value, bytes):
        # 10-06(click 實踩):以前 utf-8 解不開就回 None ⇒ 這次讀檔**完全沒記**(讀了卻不是輸入,內容改了也照沿用)。
        #   改用 os.fsdecode(surrogateescape,與 os 模組同一套),非 UTF-8 檔名也保留原名;寫進追蹤檔時由 _emit 跳脫。
        value = os.fsdecode(value)
    if isinstance(value, str) and value:
        return value
    return None


def _sha_file(path):
    try:
        if not os.path.isfile(path):
            return None, not os.path.exists(path)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), False
    except (OSError, IOError):
        return None, True


_PYC_SOURCES = set()   # 已補記過的原始碼(每行程一次)


def _pyc_source(path):
    """`<dir>/__pycache__/<mod>.<tag>[.opt-N].pyc` ⇒ `<dir>/<mod>.py`;PYTHONPYCACHEPREFIX 底下的鏡像路徑 ⇒ 原位置。對不上 ⇒ None。"""
    try:
        ap = os.path.abspath(path)
    except (OSError, ValueError, TypeError):
        return None
    if not ap.endswith(".pyc"):
        return None
    d, name = os.path.split(ap)
    mod = name.split(".", 1)[0]
    if os.path.basename(d) == "__pycache__":
        return os.path.join(os.path.dirname(d), mod + ".py")
    prefix = os.environ.get("PYTHONPYCACHEPREFIX") or ""
    if prefix:
        prefix = os.path.abspath(prefix)
        if ap.startswith(prefix + os.sep):
            return os.path.join("/" + os.path.relpath(d, prefix), mod + ".py")
    return None


_TMP_PREFIXES = tuple({"/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"}
                      | ({os.environ["TMPDIR"].rstrip("/") + "/"} if os.environ.get("TMPDIR") else set()))


def _emit_t0():
    """10-03 產物規則:追蹤檔還是空的 ⇒ 我是這次追蹤的第一個行程(測試本身)⇒ 記開始時間,與當下各暫存根底下
    已經有哪些名字(之後才出現的名字 = 這次跑測試才建的暫存目錄)。分析端只採用第一筆 t0。"""
    try:
        trace = _trace_path()
        if not trace or (os.path.exists(trace) and os.path.getsize(trace) > 0):
            return
        import time
        t0_ns = time.time_ns()
        # 10-03:起跑標記檔(bash 側用內建 `[ f -nt 標記 ]` 判「這次才出生」)。先建標記、再列暫存根:
        #   兩者之間才建的目錄會出現在名單裡 ⇒ 被當成「開始前就有」(保守方向)。
        try:
            with open(trace + ".t0", "w"):
                pass
        except OSError:
            pass
        snap = {}
        for d in ("/tmp", "/private/tmp", os.environ.get("TMPDIR") or ""):
            d = d.rstrip("/")
            if not d or d in snap:
                continue
            try:
                snap[d] = sorted(os.listdir(d))
            except OSError:
                continue
            try:
                r = os.path.realpath(d)
                if r != d:
                    snap.setdefault(r, snap[d])
            except (OSError, ValueError):
                pass
        _emit({"ch": "t0", "ns": t0_ns, "tmp": snap})
    except Exception:  # noqa: BLE001  記不到 t0 ⇒ 分析端不用產物規則(保守)
        pass


def _record_path(path, channel="open", copy_to=None):
    if not path:
        return
    # ★ 09-29 a seat 實量漏洞:有編譯快取時 import 只讀 .pyc(衍生檔、分析端略過),原始碼**內容從沒進鑰匙**
    #   ⇒ 之後改那支原始碼,沿用判斷看不到 ⇒ 錯誤沿用(v0.69.4 第三輪 test-aib-dm-tool 等 4 支 measured=True 卻缺原始碼)。
    #   讀 .pyc 當下補記對應原始碼的內容(不改被測程式的環境;不存在的原始碼 = 只有 .pyc 的散佈,照舊不記)。
    if isinstance(path, str) and path.endswith(".pyc"):
        src = _pyc_source(path)
        if src and src not in _PYC_SOURCES and os.path.isfile(src):
            _PYC_SOURCES.add(src)
            _record_path(src, channel)
    try:
        trace = _trace_path()
        if trace and os.path.abspath(path) == os.path.abspath(trace):
            return
    except (OSError, ValueError):
        return
    digest, missing = _sha_file(path)
    # ★ 記**絕對**路徑(大綱第 28 項,09-11):git 在治具倉裡探 objects/xx 之類的**相對**路徑,
    #   相對路徑對不上暫存根,於是每輪 110 個 missing:xx 讓鑰匙永不相同;絕對化之後暫存根排除才看得到它。
    try:
        rec = os.path.abspath(path)
    except (OSError, ValueError, TypeError):
        rec = path
    if missing:
        _emit({"ch": "missing", "path": rec})
        return
    if digest:
        ev = {"ch": channel, "path": rec, "sha256": digest}
        if copy_to:
            ev["copy_to"] = copy_to
        try:
            real = os.path.realpath(rec)
            if real != rec:
                ev["real"] = real   # 96:讀 symlink 時記實體路徑,分析端才能認出「讀的是自己寫的 target」
        except (OSError, ValueError):
            pass
        if channel == "open" and _KEYED_SPEC and _keyed_declared(rec):
            _KEYED_SHAS.setdefault(digest, set()).add(os.path.realpath(rec))   # §15.3c 第 2 項;同內容多檔都記(審查低 4)
        if channel == "open" and isinstance(rec, str) and rec.startswith(_TMP_PREFIXES):
            # 10-03 產物規則:暫存檔記開檔當下的 mtime(跟過 symlink = 目標的時間);分析端拿它比這次追蹤的開始時間
            try:
                ev["mtime_ns"] = os.stat(rec).st_mtime_ns
            except (OSError, ValueError):
                pass
        _emit(ev)


def _realpath(p):
    """寫入事件也帶實體路徑:/var/folders 與 /private/var/folders 是同一個檔,symlink 的讀取要對得上它寫的 target。"""
    try:
        r = os.path.realpath(p)
        return r if r != p else None
    except (OSError, ValueError, TypeError):
        return None


def _looks_like_python(exe):
    base = os.path.basename(exe or "")
    return base == "python" or base.startswith("python")


# ---- 96(09-13):交給子行程的東西在交出去的當下記內容 ----------------------------------------
# 為什麼在 Popen 當下記,不在結束時記:live40 164 列 argv-tmp-path-without-content 的主形是
# 「測試把樹複製到暫存目錄 → 子行程在裡面跑 → TemporaryDirectory 收掉 → 結束時檔已不在,selfwrite 記不到」,
# 而父行程自己寫過的路徑再被讀會被 written 濾掉。交出去那一刻的內容才是子行程真正看到的輸入。
_HANDOFF_MAX_FILES = 5000
_HANDOFF_MAX_BYTES = 200 * 1024 * 1024
_TREE_CACHE = {}          # dir → (stat 簽名, 檔數, 雜湊):同一個治具目錄被幾十次 Popen 交出去只雜湊一次
_EPHEMERAL_ROOTS = ("/private/tmp/", "/tmp/", "/var/folders/", "/private/var/folders/")
TOOL_CACHE_DIRS = (".hypothesis",)    # 與 shadow_tracer.TOOL_CACHE_DIRS 同一組(為什麼不含 .mypy_cache 見那裡)
_DERIVED_DIRS = ("__pycache__", ".git", "node_modules") + TOOL_CACHE_DIRS
_DERIVED_SUFFIXES = (".pyc", ".pyo")
_DERIVED_NAMES = (".DS_Store",)


def _is_ephemeral(path):
    tmp = (os.environ.get("TMPDIR") or "").rstrip("/")
    roots = _EPHEMERAL_ROOTS + ((tmp + "/",) if tmp else ())
    return str(path).startswith(roots)


def _tree_signature(root):
    """(簽名, 檔數, 位元組數 | None=超界)。簽名=每個檔的 (相對路徑, size, mtime_ns);只 stat 不讀。"""
    sig = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _DERIVED_DIRS)
        for fn in sorted(filenames):
            if fn in _DERIVED_NAMES or fn.endswith(_DERIVED_SUFFIXES):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            sig.append((os.path.relpath(fp, root), st.st_size, st.st_mtime_ns))
            total += st.st_size
            if len(sig) > _HANDOFF_MAX_FILES or total > _HANDOFF_MAX_BYTES:
                return None, len(sig), total
    return tuple(sig), len(sig), total


def _tree_sha(root):
    """目錄的內容雜湊:排序後每個檔 `相對路徑:sha256` 一行;衍生檔(.git/__pycache__/.pyc/.DS_Store)不算。回 (sha, 檔數) 或 (None, 檔數)=超界。"""
    sig, n, _total = _tree_signature(root)
    if sig is None:
        return None, n
    cached = _TREE_CACHE.get(root)
    if cached and cached[0] == sig:
        return cached[2], cached[1]
    h = hashlib.sha256()
    for rel, _size, _mt in sig:
        digest, missing = _sha_file(os.path.join(root, rel))
        h.update(("%s:%s\n" % (rel, digest or "missing")).encode("utf-8"))
    out = h.hexdigest()
    _TREE_CACHE[root] = (sig, n, out)
    return out, n


def _candidate_paths(argv, cwd):
    """argv 每一項(含 argv[0]):本身是路徑就收;`--k=/path` 收等號後;`-c "cd /tmp/x && …"` 這種字串裡的暫存根路徑也收。
    相對路徑以 cwd(沒給就本行程 cwd)解析。只收**現在存在**的;不存在的不是交出去的內容(missing 由別的通道管)。"""
    base = cwd if cwd else os.getcwd()
    out = []
    seen = set()

    def _add(p):
        if not p or p in seen:
            return
        seen.add(p)
        ap = p if os.path.isabs(p) else os.path.join(base, p)
        try:
            ap = os.path.abspath(ap)
            if os.path.exists(ap):
                out.append(ap)
        except (OSError, ValueError):
            pass

    for i, a in enumerate(argv):
        if not isinstance(a, str) or not a:
            continue
        if "\n" not in a and " " not in a:
            _add(a)
            if a.startswith("-") and "=" in a:
                _add(a.split("=", 1)[1])
        for m in re.finditer(r"(?:/private)?(?:/tmp|/var/folders)/[^\s'\"`;&|<>()]+", a):
            cand = m.group(0)
            while cand and cand not in seen:
                if os.path.exists(cand):
                    _add(cand)
                    break
                cand = cand.rpartition("/")[0] if "/" in cand[1:] else ""
    if cwd:
        _add(str(cwd))
    return out


def _emit_handoff(paths):
    trace = _trace_path()
    for ap in paths:
        try:
            if trace and os.path.realpath(ap) == os.path.realpath(trace):
                continue
            if any(part in _DERIVED_DIRS for part in ap.split("/")):
                continue
            if os.path.isdir(ap):
                # 目錄只在暫存根底下才整棵雜湊:真倉的目錄(倉根、src/)交給非 python 子行程,
                # 政策端一律 untracked(bash/git 讀真樹量不到),在這裡雜湊整棵倉只會又慢又粗。
                if not _is_ephemeral(ap):
                    continue
                sha, n = _tree_sha(ap)
                if sha is None:
                    _emit({"ch": "unmeasured", "why": "handoff-too-large:%s" % ap})
                else:
                    _emit({"ch": "handoff", "path": ap, "tree_sha256": sha, "files": n})
            elif os.path.isfile(ap):
                digest, missing = _sha_file(ap)
                if digest and not missing:
                    _emit({"ch": "handoff", "path": ap, "sha256": digest})
        except (OSError, ValueError, TypeError):
            continue


def _emit_missing_tmp_args(argv, cwd):
    """argv 裡指向暫存根、而交出去當下**不存在**的路徑 ⇒ 記 missing(被交出去的缺路徑是真的負向輸入,不是隨機探測)。"""
    base = cwd if cwd else os.getcwd()
    for a in argv[1:]:
        if not isinstance(a, str) or not a or " " in a or "\n" in a:
            continue
        cand = a.split("=", 1)[1] if (a.startswith("-") and "=" in a) else a
        if not cand.startswith("/"):
            cand = os.path.join(base, cand) if cand.startswith(("./", "../")) else ""
        if not cand or not _is_ephemeral(cand):
            continue
        try:
            ap = os.path.abspath(cand)
            if not os.path.exists(ap):
                _emit({"ch": "missing", "path": ap})
        except (OSError, ValueError):
            continue


def _bash_shebang(path):
    """argv[0] 是檔且第一行是 #!/bin/bash / #!/usr/bin/env bash ⇒ 這個子行程其實是 bash(直接執行的 aib、*.sh)。"""
    try:
        with open(path, "rb") as fh:
            first = fh.readline(200)
    except (OSError, ValueError, TypeError):
        return False
    return first.startswith(b"#!") and b"bash" in first.split(b"\n", 1)[0]


def _argstr(x):
    """10-03:argv 元素是 bytes/路徑物件時解成字串(以前 str(b'git') = "b'git'",認不出是 git)。"""
    if isinstance(x, (bytes, bytearray)):
        return os.fsdecode(bytes(x))
    try:
        return os.fspath(x) if not isinstance(x, str) else x
    except TypeError:
        return str(x)


# 10-07(外部專案 django-taggit 改連 Postgres 實踩):C 寫的網路客戶端(libpq 等)自己開連線,不經過 Python 的 socket ⇒
#   沒有 socket.connect 審計事件 ⇒ 以前判「沒碰網路」⇒ 資料庫內容變了照樣沿用 = 紅藏成綠。
#   載入這些原生驅動(或用 ctypes 載入它們的函式庫)就記「碰到外部資源」⇒ 每次重跑。只載入沒連線的也算(多跑,不會錯沿用)。
#   純 Python 的客戶端(redis-py、asyncpg、pymysql、oracledb thin…)走 socket,原本就抓得到,不在名單裡。
_NATIVE_NET_MODULES = frozenset((
    "psycopg2._psycopg", "psycopg_c._psycopg", "psycopg_binary._psycopg", "MySQLdb._mysql", "_mysql_connector",
    "pymssql._pymssql", "pyodbc", "cx_Oracle", "ibm_db", "grpc._cython.cygrpc", "zmq.backend.cython._zmq",
    "zmq.backend.cython.socket", "pycurl", "pylibmc._pylibmc", "confluent_kafka.cimpl", "_ldap", "_snowflake",
))
_NATIVE_NET_LIB_RE = re.compile(r"^lib(pq|mysqlclient|mariadb|zmq|curl|rdkafka|odbc|memcached|ldap|grpc)\b")
_PB_UTILS = os.sep + os.path.join("pytest_benchmark", "utils.py")
_PB_PLUGIN = os.sep + os.path.join("pytest_benchmark", "plugin.py")


def _probe_origin():
    """10-07(外部倉 humanize 實踩):叫 git/hg 的是不是 pytest-benchmark 載入時算「自動存檔的預設檔名」那一條。
    呼叫鏈要一字不差:(本鉤子 / subprocess 的框)→ 只有 pytest_benchmark/utils.py 的框 → utils.get_tag
    → 它的上一層是 pytest_benchmark/plugin.py 的 pytest_addoption。那條的結果只當 --benchmark-autosave 的檔名
    (輸出),不影響測試紅綠。測試或 conftest 自己叫 get_tag / get_commit_info ⇒ 鏈不同 ⇒ 不標(照舊量不到)。"""
    try:
        f = sys._getframe(1)
        mine = _probe_origin.__code__.co_filename
        while f is not None and (f.f_code.co_filename == mine or f.f_code.co_filename.endswith(os.sep + "subprocess.py")):
            f = f.f_back
        while f is not None and f.f_code.co_filename.endswith(_PB_UTILS):
            if f.f_code.co_name == "get_tag":
                b = f.f_back
                if b is not None and b.f_code.co_filename.endswith(_PB_PLUGIN) and b.f_code.co_name == "pytest_addoption":
                    return "pytest-benchmark-tag"
                return None
            f = f.f_back
    except Exception:  # noqa: BLE001 - 判不出 ⇒ 不標(安全側)
        return None
    return None


def _child(argv, cwd, env):
    """一個子行程:記 argv+cwd+子行程有沒有鉤子;python 以外的子行程把交出去的路徑內容記成 handoff。
    96b:bash 子行程也判「有沒有鉤子」——鉤子是 BASH_ENV=…/bash-shim/_trace.bash(環境裡本來就有,或 PATH 上的 bash 墊片會掛)。"""
    exe = argv[0] if argv else ""
    here = os.path.dirname(os.path.abspath(__file__))
    env_map = env if isinstance(env, dict) else (os.environ if env is None else {})
    pypath = str(env_map.get("PYTHONPATH") or "")
    trace_ok = str(env_map.get("EZ2_VERIFY_TRACE") or "") == _trace_path()
    hooked = (here in pypath.split(os.pathsep) or os.path.join(here, PY_SITE_DIR) in pypath.split(os.pathsep)) and trace_ok
    if os.path.basename(str(exe or "")) == "node":   # 10-04 Node 接頭:NODE_OPTIONS 帶著 --require node-shim/audit.cjs 才算掛上
        hooked = trace_ok and os.path.join(here, "node-shim", "audit.cjs") in str(env_map.get("NODE_OPTIONS") or "")
    # 10-04:subprocess.Popen 的稽核事件把 cwd **原樣**交出來(Path 物件不先轉字串;open/listdir 則已轉好)⇒
    #   以前 _as_path(Path) 回 None ⇒ 記成本行程的 cwd(真倉)⇒ 治具裡的 git 全被判成碰真倉(acb-retirement 142 秒只卡這個)
    try:
        cwd = os.fspath(cwd) if cwd is not None else None
    except TypeError:
        pass
    cwd_s = _as_path(cwd) if cwd is not None else None
    try:
        cwd_abs = os.path.abspath(cwd_s) if cwd_s else os.getcwd()   # 10-03:沒給 cwd = 繼承本行程的(以前記 None,git 判不出在治具裡)
    except (OSError, ValueError, TypeError):
        cwd_abs = cwd_s
    shell = None
    base = os.path.basename(exe)
    if base == "bash":
        shell = "bash"
    elif exe and not _looks_like_python(exe):
        _p = exe if os.path.isabs(exe) else os.path.join(cwd_abs or os.getcwd(), exe)
        if ("/" in exe or os.path.isfile(_p)) and _bash_shebang(_p):
            shell = "bash"
    if shell == "bash":
        trace_bash = os.path.join(here, "bash-shim", "_trace.bash")
        via_env = str(env_map.get("BASH_ENV") or "") == trace_bash
        via_shim = False
        if base == "bash" and "/" not in exe:
            try:
                import shutil
                w = shutil.which("bash", path=str(env_map.get("PATH") or os.defpath))
                via_shim = bool(w) and os.path.dirname(os.path.abspath(w)) == os.path.join(here, "bash-shim")
            except (OSError, ValueError, TypeError):
                via_shim = False
        hooked = trace_ok and (via_env or via_shim)
        _emit({"ch": "argv", "argv": argv, "cwd": cwd_abs, "hooked": bool(hooked), "shell": "bash"})
        _emit_handoff(_candidate_paths(argv, cwd_s))
        _emit_missing_tmp_args(argv, cwd_s)
        return
    ev = {"ch": "argv", "argv": argv, "cwd": cwd_abs, "hooked": bool(hooked)}
    if base in ("git", "hg"):
        _origin = _probe_origin()
        if _origin:
            ev["origin"] = _origin
    if os.path.basename(str(exe or "")) == "git" or any(os.path.basename(str(a)) == "git" for a in argv[:4]):
        # 10-05:git 讀哪個倉也看環境(GIT_DIR…);有就記下名字與值,分析端判(shadow_tracer.GIT_REPO_ENV 同一組)
        try:
            _ge = {k: str(v) for k, v in env_map.items() if isinstance(k, str) and
                   (k in _GIT_REPO_ENV or k.startswith("GIT_CONFIG_KEY_") or k.startswith("GIT_CONFIG_VALUE_"))}
            if _ge:
                ev["gitenv"] = _ge
        except Exception:  # noqa: BLE001  env 是 bytes 對照表之類 ⇒ 當作不知道
            ev["gitenv"] = {"_unknown": True}
    if base in ("sh", "dash") and len(argv) == 3 and str(argv[1]) == "-c":
        # 10-04:`sh -c '<單一命令> >>log 2>&1'` 分析端會拆開看裡面那條;裡面若是 bash,要知道它有沒有鉤子
        #   (同 bash 分支的判法:BASH_ENV 是本追蹤器的 _trace.bash,或 PATH 上的 bash 是墊片)。沒記 ⇒ 分析端照舊量不到。
        try:
            import shutil
            _tb = os.path.join(here, "bash-shim", "_trace.bash")
            _w = shutil.which("bash", path=str(env_map.get("PATH") or os.defpath))
            ev["bash_hooked"] = bool(trace_ok and (str(env_map.get("BASH_ENV") or "") == _tb or
                                                   (bool(_w) and os.path.dirname(os.path.abspath(_w)) == os.path.join(here, "bash-shim"))))
        except (OSError, ValueError, TypeError):
            ev["bash_hooked"] = False
    if any(os.path.basename(str(a)) == "tmux" for a in argv[:6]):
        # 10-03(tmux 第一版):叫 tmux 時記交給它的 TMUX_TMPDIR / TMUX(沒設記 None),分析端判 socket 是不是測試自己的
        #   以及實際會執行的那支 tmux(PATH 解析後的真路徑;暫存根裡的 = 測試做的假 tmux ⇒ 分析端不放行)
        try:
            ev["tmux_tmpdir"] = env_map.get("TMUX_TMPDIR")
            ev["tmux"] = env_map.get("TMUX")
            tok = next(str(a) for a in argv[:6] if os.path.basename(str(a)) == "tmux")
            if "/" in tok:
                ev["tmux_exe"] = os.path.realpath(os.path.join(cwd_abs or os.getcwd(), tok))
            else:
                import shutil
                w = shutil.which(tok, path=str(env_map.get("PATH") or os.defpath))
                ev["tmux_exe"] = os.path.realpath(w) if w else None
        except Exception:  # noqa: BLE001  env 是 bytes 對照表之類 ⇒ 不記(分析端當作不知道 ⇒ 不放行)
            pass
    _emit(ev)
    if exe.startswith("/") and os.path.basename(exe) in _ABS_EXEC:
        _emit({"ch": "unmeasured", "why": "absolute-exec"})
    if _looks_like_python(exe) and not hooked:
        # 10-04 晚(診斷,不影響判斷):沒掛上鉤子的 python 子行程,環境是哪一種形狀、是不是測試本身起的 ——
        #   inherit=沒給 env;copy=複製來的(追蹤變數還在);fresh=從頭組(三個鍵都沒有);washed=刻意拿掉了追蹤變數
        try:
            if env is None:
                _shape = "inherit"
            elif str(env_map.get("EZ2_VERIFY_TRACE") or "") == _trace_path():
                _shape = "copy"
            elif not any(k in env_map for k in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")):
                _shape = "fresh"
            else:
                _shape = "washed"
            _emit({"ch": "diag", "what": "python-child-env", "shape": _shape,
                   "depth0": os.environ.get("EZ2_VERIFY_RUNNER_PID") == str(os.getppid())})
        except Exception:  # noqa: BLE001
            pass
    if _looks_like_python(exe):
        # ★ 96:以前只看 PYTHONPATH,沒看 EZ2_VERIFY_TRACE;而收尾又只用「inherit 數 ≥ 1+min(n,3)」的軟下界
        #   ⇒ 洗掉 trace 變數的子 python 靜靜沒被追到(live40 test-ai-view 那類)。現在精確:沒鉤子就記。
        if not hooked or any(a in ("-I", "-E", "-S") for a in argv[1:3]):
            _emit({"ch": "unmeasured", "why": "python-child-no-sitecustomize"})
        # 帶鉤子的子 python 自己會記讀取;但交出去的**暫存**路徑仍在這裡記一筆(存在 ⇒ handoff;不存在 ⇒ missing),
        # 因為孤兒契約要的是「同一列有內容訊號」,而只寫不讀的狀態目錄、exists() 探測(stat 沒有審計事件)子行程都不會留下讀取。
        _emit_handoff([p for p in _candidate_paths(argv, cwd_s) if _is_ephemeral(p)])
        _emit_missing_tmp_args(argv, cwd_s)
        return
    _emit_handoff(_candidate_paths(argv, cwd_s))
    _emit_missing_tmp_args(argv, cwd_s)


def _sqlite_connect(database):
    if hasattr(database, "__fspath__"):
        database = os.fspath(database)
    db = _as_path(database)
    if not db or db == ":memory:":
        return
    if db.startswith("file:"):
        db = db[5:].split("?", 1)[0]
        if db.startswith("//"):
            db = db[2:]
        if db == ":memory:" or not db:
            return
    try:
        db_abs = os.path.abspath(db)
    except (OSError, ValueError, TypeError):
        db_abs = db
    if db_abs in _WRITTEN:
        return   # 自己建的 db(第一次 connect 時不存在):之後每次 connect 讀到的都是自己的產物
    if not os.path.exists(db_abs):
        # 第一次 connect 而檔不存在 ⇒ 是本行程要建的 ⇒ 記 missing(負向輸入)並當成自己寫的
        _emit({"ch": "missing", "path": db_abs})
        _emit({"ch": "write", "path": db_abs})
        _WRITTEN.add(db_abs)
        return
    for suffix in ("", "-wal", "-journal"):
        p = db_abs + suffix
        if suffix and not os.path.exists(p):
            continue
        _record_path(p)


def _dirfd_join(fd, name):
    """相對名 + dir_fd ⇒ 絕對路徑;fd 為 None、名字已是絕對、或解不出目錄 ⇒ None(呼叫端照舊行為)。
    只讀 fd 的路徑(macOS F_GETPATH / Linux /proc/self/fd),不動 fd 的狀態 —— 觀測者不得改變被觀測物。"""
    if fd is None or not name or os.path.isabs(name):
        return None
    d = None
    try:
        import fcntl
        if hasattr(fcntl, "F_GETPATH"):
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
            d = raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape") or None
        elif os.path.isdir("/proc/self/fd"):
            d = os.readlink("/proc/self/fd/%d" % fd)
    except (OSError, ValueError, TypeError, ImportError):
        d = None
    return os.path.join(d, name) if d else None


# 10-08(express / Python 的本機測試伺服器):測試自己 bind 的本機 socket、自己連回去 —— 不是外部。只認同一個行程自己 bind、
#   還開著的;連到本機上別人開的服務(資料庫之類)照樣算外部。存的是 socket 的弱參照(不延後回收、不改被觀測物;用檔內既有的
#   _weakref,不 import weakref)。10-24 起不再存 fd 號碼再 dup:號碼重用成 macOS 受保護 fd 時 dup ⇒ 核心 SIGKILL。
_OWN_SOCKETS = []       # 這個行程 bind 過的 socket(弱參照;不記 fd 號碼)
_LOOPBACK = frozenset(("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"))


def _note_bind(sock):
    try:
        _OWN_SOCKETS[:] = [r for r in _OWN_SOCKETS if r() is not None]   # 只 bind 不 connect 的測試也不讓清單一直長
        _OWN_SOCKETS.append(_weakref(sock))
    except Exception:  # noqa: BLE001 - 不能弱參照 ⇒ 不記,connect 時算外部(安全側)
        pass
    _wrap_listen(type(sock))


def _fmt_addr(addr):
    """socket 位址 ⇒ 字串:Unix socket 是路徑,TCP/UDP 是 host:port。"""
    if isinstance(addr, (str, bytes)):
        return "unix:" + os.fsdecode(addr)
    if isinstance(addr, tuple) and len(addr) >= 2:
        return "%s:%s" % (addr[0], addr[1])
    return repr(addr)[:120]


def _wrap_listen(cls):
    """10-12 P17(a seat 派,the maintainer 同意):只多記資料 —— 這個行程真的開始聽的位址(bind 用埠 0 時,埠要 listen 之後才知道)。
    第一次 bind 時才換掉那個類別的 listen(沒 bind 的行程不碰 socket 模組);包裝回原函式的結果、原封不動丟原例外。"""
    try:
        orig = cls.listen
        if getattr(orig, "_aib_listen_wrapped", False):
            return

        def listen(self, *a, **k):
            r = orig(self, *a, **k)
            try:
                _emit({"ch": "listen", "addr": _fmt_addr(self.getsockname())})
            except Exception:  # noqa: BLE001 —— 多記資料失敗不影響被測物
                pass
            return r
        listen._aib_listen_wrapped = True
        cls.listen = listen
    except Exception:  # noqa: BLE001
        pass


def _net_dest(args):
    """socket.connect 的目的地:{"dest": 位址字串, "local": 是不是本機(loopback / localhost / Unix socket)}。判不出 ⇒ {}。"""
    try:
        addr = args[1]
        if isinstance(addr, (str, bytes)):
            return {"dest": _fmt_addr(addr), "local": True}
        if isinstance(addr, tuple) and len(addr) >= 2:
            host = str(addr[0])
            local = host in _LOOPBACK or host in ("localhost", "0.0.0.0", "::") or host.startswith("127.")   # 127.x 整段都是本機(審查)
            return {"dest": _fmt_addr(addr), "local": local}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _to_own_listener(args):
    """socket.connect 的目的地是不是這個行程自己 bind 的本機 socket(TCP 看埠、Unix socket 看路徑)。判不出 ⇒ False(算外部)。"""
    sk = sys.modules.get("socket")
    if sk is None:
        return False
    try:
        addr = args[1]
        # 10-24:不再 os.dup(fd)。fd 號碼關掉後會被重用,macOS 系統框架會把它設成受保護的 fd(EXC_GUARD),
        #   對受保護的 fd 做 dup ⇒ 核心直接 SIGKILL 整個行程(Python 3.15 測試連網時當機)。改記 socket 物件的弱參照、
        #   直接問它自己的 getsockname;關掉 / 被回收的就丟掉。
        for ref in list(_OWN_SOCKETS):
            own = ref()
            if own is None or own.fileno() < 0:
                _OWN_SOCKETS.remove(ref)
                continue
            try:
                mine = own.getsockname()
            except OSError:
                continue
            if isinstance(addr, (str, bytes)):
                if mine and os.fsdecode(mine) == os.fsdecode(addr):
                    return True
            elif isinstance(addr, tuple) and isinstance(mine, tuple) and len(addr) >= 2 and len(mine) >= 2:
                if str(addr[0]) in _LOOPBACK and int(mine[1]) != 0 and int(addr[1]) == int(mine[1]):
                    return True
    except Exception:  # noqa: BLE001 - 判不出 ⇒ 算外部(安全側)
        return False
    return False


_LAST_POPEN = {}    # 執行緒 ⇒ 上一個稽核事件若是 subprocess.Popen,它的 argv(10-08 posix_spawn 重複記的去重;任何下一個事件就清掉)


def _audit(event, args):
    global _IN_HOOK
    if _IN_HOOK:
        return
    if not _trace_path():
        return
    _IN_HOOK = True
    try:
        _after_popen = _LAST_POPEN.pop(_get_ident(), None) if _LAST_POPEN else None   # 上一個事件是 subprocess.Popen ⇒ 它的 argv
        if _COPY_PENDING and event not in ("open", "shutil.copyfile"):
            # 複製沒有讀來源(例如來源是 symlink、只建了一個 symlink)⇒ 標記不准留到之後,誤標真的讀取
            _COPY_PENDING.pop(_get_ident(), None)
        if event == "open" and not _GETTEXT_DONE and args and isinstance(args[0], str) and args[0].endswith(".mo"):
            _install_gettext_keys()
        if event == "open":
            # 寫入模式的 open 記成 write 事件:測試自己產生的檔(先寫後讀)不是候選的輸入(ef56b B 反例的分辨器)。
            _mode = args[1] if len(args) > 1 and isinstance(args[1], str) else None
            _flags = args[2] if len(args) > 2 and isinstance(args[2], int) else None
            _path = _as_path(args[0] if args else None)
            # ★ 2026-09-29 a seat:"open" 審計事件不帶 dir_fd ⇒ os.open(name, dir_fd=d) 的相對名會被接到 cwd(記錯檔、
            #   雜湊錯檔)。os.open 包裝(_DirFdOpenWrapper)在呼叫期間把 d 的路徑放在 _DIRFD_CTX[本執行緒],這裡接回去。
            if _path and not os.path.isabs(_path):
                _ctx = _DIRFD_CTX.get(_get_ident())
                if _ctx:
                    _path = os.path.join(_ctx, _path)
            _writing = False
            if _mode is not None:
                _writing = any(ch in _mode for ch in "wax+")
            elif _flags is not None:          # os.open / tempfile:模式在 flags 位元裡
                _writing = bool(_flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
            if _writing:
                # ★ 觀測工具自己的輸出(trace 檔)在**寫入通道也要排除**(ef56b 09-12 第 4 條:trace 進了 selfwrite,
                #   同輸入兩輪 key 不同);寫入當下就絕對化(第 5 條:相對路徑到結束時用當時 cwd 會認錯檔)。
                try:
                    _tp = _trace_path()
                    # realpath 兩邊都做:/var/folders 與 /private/var/folders 是同一個檔,abspath 分不出來(ef56b 第 4 條實測)
                    if _path and _tp and os.path.realpath(_path) == os.path.realpath(_tp):
                        return
                    if _path:
                        _path = os.path.abspath(_path)
                except (OSError, ValueError, TypeError):
                    pass
                # ★ r+/a/O_RDWR/O_APPEND(沒 O_TRUNC)會讀到**舊內容**:舊內容是候選的輸入(ef56b C 反例),先當 open 記一筆。
                _reads_old = False
                if _mode is not None:
                    _reads_old = ("+" in _mode or "a" in _mode) and "w" not in _mode
                elif _flags is not None:
                    _reads_old = bool(_flags & (os.O_RDWR | os.O_APPEND)) and not (_flags & os.O_TRUNC)
                if _reads_old and _path:
                    _record_path(_path)
                if _path and _path not in _WRITTEN and not os.path.lexists(_path):
                    _CREATED.add(_path)
                _emit({"ch": "write", "path": _path, "real": _realpath(_path)})
                if _path:
                    _WRITTEN.add(_path)
            else:
                _cp = _COPY_PENDING.pop(_get_ident(), None)
                if _cp and _path and os.path.abspath(_path) == _cp[0]:
                    _record_path(_path, "open", copy_to=_cp[1])
                else:
                    _record_path(_path, "open")
        elif event == "shutil.copyfile":
            # 10-05:shutil 複製(copy/copy2/copytree)讀來源只是為了複製 —— 先標起來,下一筆讀來源的 open 帶上 copy_to,
            #   分析端只在「複本真的被用到」時才把來源算成輸入(刀測試整棵抄 src/tests/tools 再只跑一兩支,見 shadow_tracer)
            try:
                _src = os.path.abspath(_as_path(os.fspath(args[0])) or "")
                _dst = os.path.abspath(_as_path(os.fspath(args[1])) or "")
                _COPY_PENDING[_get_ident()] = (_src, _dst)
            except (TypeError, ValueError, OSError, IndexError):
                pass
        elif event == "import":
            filename = args[1] if len(args) > 1 else None
            _record_path(_as_path(filename), "open")
            if args and str(args[0]) in _NATIVE_NET_MODULES:
                _emit({"ch": "external", "why": "native-network-client:%s" % args[0]})
        elif event == "ctypes.dlopen":
            if args and _NATIVE_NET_LIB_RE.search(os.path.basename(str(args[0] or ""))):
                _emit({"ch": "external", "why": "native-network-client:%s" % os.path.basename(str(args[0]))})
        elif event in ("os.listdir", "os.scandir"):
            path = _as_path(args[0] if args else ".") or "."
            try:
                # 與 shadow_tracer.filter_listdir_names 同一組名字過濾(__pycache__/.git/.pyc/.DS_Store),
                # 否則名單雜湊每輪都變(09-11 校準只沿用 8/112 的主因之一)。
                names = sorted(n for n in os.listdir(path)
                               if n not in ("__pycache__", ".git", ".DS_Store") and n not in TOOL_CACHE_DIRS
                               and not n.endswith((".pyc", ".pyo"))
                               and not (n == ".coverage" or (n.startswith(".coverage.") and not n.startswith(".coveragerc"))))
            except OSError:
                # 10-08(pytest-cov 實測:addopts 帶 --cov 永遠沿用不了):列一個「檔案」會丟 NotADirectoryError ——
                #   以前一律記 missing ⇒ 檔明明在,重算時對不上 ⇒ 每次必重跑。照實記它的狀態(跟 os.stat 探測同一種項,重算端現查)。
                import stat as _stat
                _real_stat = getattr(os.stat, "__wrapped_probe__", os.stat)
                try:
                    _m = _real_stat(path).st_mode
                except OSError:
                    _emit({"ch": "missing", "path": os.path.abspath(path)})
                    return
                if _stat.S_ISDIR(_m):
                    _emit({"ch": "unmeasured", "why": "listdir-error"})      # 是目錄卻列不出來(權限之類):量不到
                else:
                    _emit({"ch": "probe", "path": os.path.abspath(path), "state": "file" if _stat.S_ISREG(_m) else "other"})
                return
            digest = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
            ev = {"ch": "listdir", "path": os.path.abspath(path), "names_sha256": digest}
            # 10-07(外部倉 django-taggit 實踩):測試自己在倉裡新建、跑完又刪掉的檔,列目錄當下在名單裡,
            #   重算時已不在 ⇒ 每次必重跑。另記「不含本行程新建的名字」的雜湊與那些名字;用哪一份由分析端看結束時還在不在決定。
            made = [n for n in names if os.path.join(ev["path"], n) in _CREATED]
            if made:
                ev["made"] = made
                ev["names_sha256_wo_made"] = hashlib.sha256("\n".join(n for n in names if n not in made).encode("utf-8")).hexdigest()
            try:
                real = os.path.realpath(ev["path"])
                if real != ev["path"]:
                    ev["real"] = real   # 10-03:經過 symlink 列目錄(例:假 HOME 底下指回倉的捷徑)⇒ 分析端改記倉內路徑
            except (OSError, ValueError):
                pass
            _emit(ev)
        elif event == "subprocess.Popen":
            # sys.audit("subprocess.Popen", executable, args, cwd, env)
            argv = []
            if len(args) >= 2 and args[1] is not None:
                argv = [_argstr(x) for x in args[1]] if not isinstance(args[1], (str, bytes)) else ["sh", "-c", _as_path(args[1]) or ""]
            elif args:
                argv = [str(args[0])]
            _child(argv, args[2] if len(args) > 2 else None, args[3] if len(args) > 3 else None)
            _LAST_POPEN[_get_ident()] = argv
        elif event in ("os.exec", "os.posix_spawn"):
            # os.exec(path, args, env) / os.posix_spawn(path, argv, env):同 Popen,cwd 是本行程的
            argv = [_argstr(x) for x in (args[1] or [])] if len(args) > 1 and args[1] else [_argstr(args[0])]
            # 10-08:Linux 上 Python 3.13 起 Popen 改用 posix_spawn(glibc 有 closefrom)⇒ 同一個子行程先有 subprocess.Popen、
            #   再有 os.posix_spawn 兩筆 ⇒ 分析端期望兩個 inherit 只看到一個 ⇒ 整列誤判量不到(python-child-no-inherit:2<3)。
            #   只在 Popen.__init__ 裡面、argv 跟剛記的那一筆一樣時略過這筆;其他情況照記(多記只會偏向量不到,不會藏紅)。
            #   10-08 晚:改成「緊接在 subprocess.Popen 之後的下一個事件、argv 相同」才略過 —— 以前靠包 Popen.__init__ 標記,
            #   但那個包裝只裝在測試本身的行程(不改子行程的 sys.modules),子行程、孫行程照樣記兩次(b18b 5<6 實測)。
            #   實測:走 posix_spawn 時兩個事件之間沒有別的事件;不走(給了 cwd 等)⇒ 根本沒有 os.posix_spawn。
            if event == "os.posix_spawn" and _after_popen is not None and _after_popen == argv:
                return
            _child(argv, None, args[2] if len(args) > 2 else None)
        elif event == "os.spawn":
            # os.spawn(mode, path, args, env)
            argv = [_argstr(x) for x in (args[2] or [])] if len(args) > 2 and args[2] else [_argstr(args[1])]
            _child(argv, None, args[3] if len(args) > 3 else None)
        elif event == "os.system":
            # ★ 96(09-13):以前 os.system 根本不記 ⇒ 子行程讀什麼一片空白卻標 measured。記成 sh -c,由政策判 untracked。
            _child(["sh", "-c", _as_path(args[0] if args else None) or ""], None, None)
        elif event in ("os.rename", "os.link"):
            # 96:原子寫(寫暫存名再 rename/replace/link 成正式名)——正式名從沒有 write 事件,之後自己讀它會被當成輸入
            #   而且路徑帶隨機名 ⇒ 鑰匙每輪不同。自己寫過的來源改名/硬連結 ⇒ 目的地也算自己寫的(social_gateway 用 os.link)。
            #   ★ 2026-09-29 a seat(R7 第一形):atomic_json 用 dir_fd 寫(os.open(temp, dir_fd=d) + os.replace(…, src_dir_fd=d,
            #   dst_dir_fd=d))。"open" 審計事件不帶 dir_fd ⇒ 暫存名被接在 cwd 底下記進 _WRITTEN;rename 事件**有** dir_fd
            #   (args[2]/args[3])⇒ 這裡把目的地解到真正的目錄。來源兩種寫法都認:接 cwd 的(open 事件記的樣子)與接 dir_fd 的。
            #   解不出目錄(非 macOS/Linux、fd 已關)⇒ 照舊行為,只是那一支比不到,不會多認。
            _src = _as_path(args[0] if args else None)
            _dst = _as_path(args[1] if len(args) > 1 else None)
            _sfd = args[2] if len(args) > 2 and isinstance(args[2], int) and args[2] >= 0 else None
            _dfd = args[3] if len(args) > 3 and isinstance(args[3], int) and args[3] >= 0 else None
            if _src and _dst:
                try:
                    _src_a = os.path.abspath(_src); _dst_a = os.path.abspath(_dst)
                except (OSError, ValueError, TypeError):
                    _src_a = _src; _dst_a = _dst
                _src_fd_a = _dirfd_join(_sfd, _src)
                _dst_fd_a = _dirfd_join(_dfd, _dst)
                if _dst_fd_a:
                    _dst_a = _dst_fd_a
                if _src_a in _WRITTEN or (_src_fd_a and _src_fd_a in _WRITTEN):
                    _WRITTEN.add(_dst_a)
                    _emit({"ch": "write", "path": _dst_a, "real": _realpath(_dst_a)})
        elif event in ("mmap.__new__", "mmap.mmap", "mmap"):
            _emit({"ch": "unmeasured", "why": "mmap"})
        elif event == "sqlite3.connect":
            # ★ 96(09-13):以前整個 sqlite3.* 都 unmeasured(live40 影響 50 列)。改成把 db 檔(與 -wal/-journal)
            #   在 connect 當下的內容記成 open:;:memory: 沒有檔就不是輸入。之後 sqlite 自己的讀寫不經 open() 審計,
            #   所以這是「連線當下的內容」——測試自己後續寫進去的是產物不是輸入(與 open-then-write 同一套判準)。
            _sqlite_connect(args[0] if args else None)
        elif event == "sqlite3.load_extension":
            _emit({"ch": "unmeasured", "why": "sqlite-load-extension"})
        elif event in ("os.putenv", "os.unsetenv"):
            # ★ 96(09-13):以前 putenv/unsetenv ⇒ unmeasured "env"(live40 影響 99 列)。測試改自己的環境是
            #   「由輸入決定的行為」不是「沒量到的輸入」:它讀到的每個變數都在環境指紋裡(整份受控環境雜湊),
            #   它給子行程的環境=受控環境+自己的確定性修改;python 子行程的讀取另有追蹤,非 python 子行程由
            #   child 政策判 untracked。刀:tests/test-verify-measurable-96.py 讀環境變數改一字 ⇒ 鑰匙必變。
            pass
        elif event == "socket.bind":
            if args:
                _note_bind(args[0])
        elif event == "socket.connect":
            if not _to_own_listener(args):
                # 10-12 P17:順便記連到哪(只多記資料,沿用判斷照舊:external)
                _emit(dict({"ch": "external", "why": "network"}, **_net_dest(args)))
    except Exception:
        _emit({"ch": "unmeasured", "why": "hook-error"})
    finally:
        _IN_HOOK = False


# ---- T0-1(09-14):存在探測(os.stat / os.lstat / os.access)沒有審計事件 --------------------------------
# 病:測試靠「某個檔在不在」分岔(os.path.exists/isfile/isdir、Path.exists、os.stat、os.access)卻從沒 open 它
#   ⇒ 鉤子看不到這個輸入 ⇒ 那個檔從無到有(或反過來),沿用判斷仍放行——失效是**安靜**的(該跑的沒跑)。
# 修法:鉤子裝上時把 os.stat/os.lstat/os.access 換成會記 `probe:<絕對路徑>:<file|dir|missing>` 的包裝
#   (與 bash 側 `[ -f x ]` 的 probe: 同一種項,重算端 `_probe_state` 現查)。狀態直接從 stat 結果讀
#   (S_ISDIR/S_ISREG),不多一次系統呼叫;同一路徑每個行程只記第一次(importlib 走 posix 模組不經 os.stat,
#   不會被算進來;stdlib 的 genericpath/posixpath/pathlib/shutil/glob 都是呼叫時查 os.stat 屬性,會被算進來)。
# ★ 觀測者不得改變被觀測物:包裝一律回傳原函式的結果、原封不動地丟出原例外;`os.supports_dir_fd` 等集合裡
#   有原函式的就把包裝也放進去(shutil 在 import 時拿 `os.stat in os.supports_dir_fd` 決定 rmtree 走哪條路)。
#   沒有 EZ2_VERIFY_TRACE 時什麼都不裝(兩臂逐位元組相同的前提)。
# 量不到、具名留著(見 docs/spec/VERIFY-SHADOW-v1.md v3.6 節):fd / dir_fd 相對路徑的 stat;不是 ENOENT/ENOTDIR 的
#   OSError(EACCES 之類);只看 st_size/st_mtime 不看在不在的分岔;islink 的分岔(記的是跟過 symlink 的狀態);
#   直接呼叫 posix.stat 的 C 擴充。
_PROBED = set()          # 絕對路徑:記過的不再記
_PROBED_RAW = set()      # 呼叫端給的原字串:熱路徑先用它去重,連 abspath 都不算(chdir 之後同一個相對名指到別處的那種
                         #   探測會漏——具名留著;abspath 每次都算的代價是每個 stat 多一次 getcwd)
_PROBED_RAW_B = set()    # 10-06(click 實踩):bytes 路徑另放一格 —— 跟字串放同一個集合,bytes 與 str 比較會發 BytesWarning,
                         #   在 python -bb(或 -W error::BytesWarning)下直接變成例外,測試被我們弄紅(觀察者改了被觀察的結果)
_PROBE_SKIP_PARTS = frozenset(("__pycache__", ".git") + TOOL_CACHE_DIRS)


def _probe_state_from_stat(st):
    import stat as _stat
    mode = st.st_mode
    if _stat.S_ISDIR(mode):
        return "dir"
    if _stat.S_ISREG(mode):
        return "file"
    if _stat.S_ISLNK(mode):
        return None          # lstat 到 symlink 本身:狀態要跟過去看(重算端 _probe_state 是跟過去的)
    return "missing"         # socket/fifo/裝置:重算端 isdir/isfile 都 False ⇒ 會判 missing,這裡記同一個字


def _probe_state_now(ap):
    if os.path.isdir(ap):
        return "dir"
    if os.path.isfile(ap):
        return "file"
    return "missing"


def _probe_record(path, state):
    """第一次探到的路徑記一筆 probe;衍生路徑與 trace 檔本身不記。呼叫端已把 _IN_HOOK 設好。"""
    try:
        p = _as_path(os.fspath(path))     # Path 物件也會進來(pathlib 把自己交給 os.stat)
    except TypeError:
        return
    if not p:
        return
    try:
        ap = os.path.abspath(p)
    except (OSError, ValueError, TypeError):
        return
    if ap in _PROBED:
        return
    _PROBED.add(ap)
    parts = ap.split("/")
    if any(part in _PROBE_SKIP_PARTS for part in parts) or ap.endswith((".pyc", ".pyo")) or parts[-1] == ".DS_Store":
        return
    trace = _trace_path()
    if trace and ap == os.path.abspath(trace):
        return
    if state is None:
        state = _probe_state_now(ap)
    _emit({"ch": "probe", "path": ap, "state": state})


def _probe_first_sight(path):
    """熱路徑:這個原字串第一次出現才回 True。不可雜湊的 PathLike 當成每次都是第一次(不能讓它變成我們的例外)。"""
    seen = _PROBED_RAW_B if isinstance(path, bytes) else _PROBED_RAW
    try:
        if path in seen:
            return False
        seen.add(path)
    except TypeError:
        pass
    return True


def _probe_note(path, state):
    global _IN_HOOK
    _IN_HOOK = True
    try:
        _probe_record(path, state)
    except Exception:
        _emit({"ch": "unmeasured", "why": "hook-error"})
    finally:
        _IN_HOOK = False


class _ProbeWrapper:
    """os.stat / os.lstat / os.access 的包裝。

    ★ 第 183 項(09-15):必須是**可呼叫物件,不是函式**。原生的 os.stat 是 builtin,當類別屬性不會綁 self;
      Python 函式是 descriptor,放進類別就變成方法。Python ≤3.10 的 pathlib 在 import 時做
      `class _NormalAccessor: stat = os.stat`,之後 `self._accessor.stat(path)` ⇒ 包裝被綁成方法、
      收到 (accessor, path) ⇒ TypeError ⇒ Path.is_dir()/exists()/glob() 直接炸(只接 OSError)。
      實例:追蹤模式下 open-fleet6 步3 用系統 /usr/bin/python3(3.9)跑 appoint,glob 席位檔炸 ⇒ 任命失敗
      (test-captain-appointment-b15 有墊片紅、沒墊片綠;環境變數逐一加回全 0,因為變因是「直譯器版本 + 包裝的型別」)。
      沒有 __get__ 的物件放進類別仍是原物件,與 builtin 同一種行為。刀:test-verify-bash-child-96b B13。
    """
    # ★ 不用 __slots__:類別本體已經有 __doc__(說明字串)與 __qualname__,放進 __slots__ 會在建類別時 ValueError,
    #   而那發生在 sitecustomize 載入當下 ⇒ 整個鉤子靜靜不裝(09-15 本項第一版實踩,B13a 的「trace 要存在」那一句抓到)。

    def __init__(self, orig, kind):
        import functools
        # 與舊的函式版一樣走 functools.wraps 那組屬性(__wrapped__/__name__/__qualname__/__doc__/__module__):
        # inspect.signature 跟 __wrapped__ 回原函式的簽名——T0-1 P8 兩臂逐位元組比對會看到它。
        functools.update_wrapper(self, orig)
        self.__wrapped_probe__ = orig
        self._kind = kind

    def __repr__(self):
        return "<probe %r>" % (self.__wrapped_probe__,)

    def __call__(self, path, *args, **kwargs):
        orig = self.__wrapped_probe__
        # ★ 原函式的結果與例外一律原封不動地回去;我們的事只在旁邊做,而且只在第一次看到這個路徑時做。
        if _IN_HOOK or isinstance(path, int) or kwargs.get("dir_fd") is not None or not _trace_path():
            return orig(path, *args, **kwargs)
        try:
            result = orig(path, *args, **kwargs)
        except (FileNotFoundError, NotADirectoryError):
            if _probe_first_sight(path):
                _probe_note(path, "missing")
            raise
        if _probe_first_sight(path):
            _probe_note(path, None if self._kind == "access" else _probe_state_from_stat(result))
        return result


def _wrap_probe(orig, kind):
    return _ProbeWrapper(orig, kind)


class _DirFdOpenWrapper:
    """os.open 的包裝:只在帶 dir_fd 的呼叫期間,把 dir_fd 的目錄路徑交給審計鉤子(2026-09-29 a seat,R7 第一形)。

    實例:lifecycle 的 checkpoints 追加、src/cockpit/atomic_json.py 的暫存檔都是 os.open(相對名, dir_fd=d);審計事件看不到 d
    ⇒ 寫入被記在 cwd 底下 ⇒ 之後讀正式名被當成先存在的暫存輸入、隨機路徑進鑰匙 ⇒ 兩輪永遠比不到
    (v0.69.4 兩輪實量:不穩定 59 支裡最大宗)。與 _ProbeWrapper 同一套規矩:可呼叫物件不是函式、結果與例外原封不動、
    放進 os.supports_* 集合(shutil.rmtree 看 `os.open in os.supports_dir_fd` 決定走哪條路)。
    """

    def __init__(self, orig):
        import functools
        functools.update_wrapper(self, orig)
        self.__wrapped_dirfd__ = orig

    def __repr__(self):
        return "<dirfd-open %r>" % (self.__wrapped_dirfd__,)

    def __call__(self, path, flags, mode=0o777, *, dir_fd=None):
        orig = self.__wrapped_dirfd__
        if dir_fd is None or _IN_HOOK or not _trace_path():
            return orig(path, flags, mode, dir_fd=dir_fd)
        tid = _get_ident()
        prev = _DIRFD_CTX.get(tid)
        try:
            d = _dirfd_join(dir_fd, ".")
            if d:
                _DIRFD_CTX[tid] = os.path.normpath(d)
        except Exception:
            pass
        try:
            return orig(path, flags, mode, dir_fd=dir_fd)
        finally:
            if prev is None:
                _DIRFD_CTX.pop(tid, None)
            else:
                _DIRFD_CTX[tid] = prev


def _install_dirfd_open_wrapper():
    orig = getattr(os, "open", None)
    if orig is None or getattr(orig, "__wrapped_dirfd__", None) is not None:
        return
    wrapped = _DirFdOpenWrapper(orig)
    for setname in ("supports_dir_fd", "supports_effective_ids", "supports_fd", "supports_follow_symlinks"):
        s_ = getattr(os, setname, None)
        if isinstance(s_, set) and orig in s_:
            s_.add(wrapped)
    os.open = wrapped


def _install_probe_wrappers():
    for name, kind in (("stat", "stat"), ("lstat", "stat"), ("access", "access")):
        orig = getattr(os, name, None)
        if orig is None or getattr(orig, "__wrapped_probe__", None) is not None:
            continue
        wrapped = _wrap_probe(orig, kind)
        for setname in ("supports_dir_fd", "supports_effective_ids", "supports_fd", "supports_follow_symlinks"):
            s = getattr(os, setname, None)
            if isinstance(s, set) and orig in s:
                s.add(wrapped)
        setattr(os, name, wrapped)


# ── 修法 A 第二版(10-04;第一版 983dbecb 於 10c4ac52 撤回)──────────────────────────────────────────────
# 量到的:第二輪卡點排行第二「python-child-no-sitecustomize」52 支 360 秒 —— 測試自己組一份全新的 env 去起 python 子行程
#   (例:bridge-persona-pack 給 claudebot_hook.py 的 env 只有 PATH/HOME/…)⇒ 子行程沒鉤子 ⇒ 整列量不到。
# 第一版撤回的兩個原因,這版各自擋掉:
#   ① 測試自己的 sitecustomize 被蓋:第一版在「env 已有 PYTHONPATH」時也把追蹤器目錄插到最前面
#      ⇒ 這版只在 env **完全沒有** PYTHONPATH / BASH_ENV / EZ2_VERIFY_TRACE 三個鍵時才補(測試從頭組的新 env)。
#   ② witness-defaults-187 D6:驗證腳本刻意洗掉追蹤變數再起子行程,第一版又補回去 = 外層追蹤器漏進沒開驗證的樹
#      ⇒ 這版只在**測試本身那一個行程**補(它的父行程 = aib test 執行器,執行器在 EZ2_VERIFY_RUNNER_PID 交代自己的 pid);
#        驗證腳本之類更深的子孫一律不補。
#   另:只對 python 子行程補(PYTHONPATH + EZ2_VERIFY_TRACE),不補 BASH_ENV —— 巢狀跑 aib test(bash)不會變追蹤器套追蹤器。
#   驗收:受影響的測試兩臂(有/沒有追蹤器)rc 逐支相同。
#
# 第三版(10-04 晚):全套第二輪仍有 47 支卡這裡,最常見的形狀是 `env = dict(os.environ); env["PYTHONPATH"] = 自己的`
#   —— 複製了整份環境(EZ2_VERIFY_TRACE 還在、就是本追蹤),只把 PYTHONPATH 換掉 ⇒ 追蹤器目錄被擠掉。
#   這種補法不同:只附 py-site/(裡面只有一支 sitecustomize,不把 tools/verify 加進 sys.path),而且**附在最後** ——
#   測試自己的路徑全在前面;測試自己若有 sitecustomize 照樣先被載到,那時子行程不會回報 ⇒ 分析端照舊誠實記量不到。
#   條件:同樣只在測試本身那一個行程、只對 python 子行程;env 的 EZ2_VERIFY_TRACE 正是本追蹤(=複製來的,不是刻意洗掉)。
PY_SITE_DIR = "py-site"
#
# 第四版(10-04 晚):「測試本身」也包括**測試把自己(複本樹裡的同一支測試檔)再跑一次**的那個行程。
#   形狀統計(73 支):只卡這一個原因的裡,9 支是 fresh/deep —— 刀的複本裡的測試再用全新 env 起 python
#   (bridge-persona-pack 174 秒、dm-header-unify、grokbot ×2、handoff ×2、persona-pack…)。
#   認法:sys.argv[0] 是 tests/ 底下的 test-*.py / test_*.py。驗證腳本(witness D6 那種)是 bash、而且是「洗掉」不是「從頭組」,
#   幫手腳本(test-verify-popen-inject-v2 I3 的 helper.py)不是測試檔 ⇒ 都照舊不補。
_TEST_SCRIPT_RE = re.compile(r"^test[-_][^/]*\.py$")


def _is_test_process():
    if os.environ.get("EZ2_VERIFY_RUNNER_PID") == str(os.getppid()):
        return True
    try:
        a0 = os.path.abspath(sys.argv[0]) if getattr(sys, "argv", None) and sys.argv[0] else ""
    except (TypeError, ValueError, OSError):
        return False
    return bool(a0) and os.path.basename(os.path.dirname(a0)) == "tests" and bool(_TEST_SCRIPT_RE.match(os.path.basename(a0)))


_POPEN_INIT_ORIG = None
_POPEN_ENV_POS = 10          # subprocess.Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds, shell, cwd, env, ...)


def _is_python_cmd(args):
    if isinstance(args, (str, bytes)) or not args:
        return False
    try:
        exe = os.path.basename(str(os.fspath(args[0])))
    except TypeError:
        return False
    return exe == os.path.basename(sys.executable) or bool(re.match(r"^python(\d+(\.\d+)*)?$", exe))


def _washed_copy(env):
    """10-04 晚(witness-defaults D2/D6 抓到的):三個追蹤鍵都不在的 env 有兩種 —— 從頭組的新 dict,或**複製本行程環境再刻意洗掉**
    (D6 的 _wash_tracer:pop BASH_ENV/PYTHONPATH/EZ2_VERIFY_TRACE)。後者帶著執行器塞的 EZ2_VERIFY_RUNNER_PID(沒人會手寫它)⇒ 認得出來。
    洗過的 = 刻意脫離追蹤 ⇒ 不補。"""
    rp = os.environ.get("EZ2_VERIFY_RUNNER_PID")
    return bool(rp) and env.get("EZ2_VERIFY_RUNNER_PID") == rp


def _family_filtered(env):
    """第六版:追蹤檔與執行器 pid 都不在(測試自己組的新 env 或整族濾掉 EZ2_*),但帶著 PYTHONPATH。"""
    if "EZ2_VERIFY_TRACE" in env or "EZ2_VERIFY_RUNNER_PID" in env:
        return False
    pp = env.get("PYTHONPATH")
    return isinstance(pp, str) and bool(pp.strip(os.pathsep))


def _is_bash_cmd(args):
    if isinstance(args, (str, bytes)) or not args:
        return False
    try:
        return os.path.basename(str(os.fspath(args[0]))) == "bash"
    except TypeError:
        return False


def _with_trace_vars(args, env):
    """回補好追蹤變數的**新** dict;不該補時回原物件(條件見上方註解,全部成立才補)。"""
    trace = _trace_path()
    if not trace or env is None or not hasattr(env, "items") or not hasattr(env, "get"):
        return env
    if not _is_test_process():
        return env                                   # 不是測試本身(包裝也只裝在那裡;這裡再擋一次)
    if _is_bash_cmd(args):
        # 第五版(10-04 晚):bash 子行程 + 從頭組的新 env(三個鍵都沒有)⇒ 跟追蹤器起測試時同一套:BASH_ENV、追蹤檔、
        #   PATH 最前面放 bash-shim。全套第二輪「只卡 bash 沒鉤子」38 支 190 秒,最慢的三支都是這個形狀。
        #   巢狀 aib test:它會用白名單重組自己測試的環境(外層 BASH_ENV 帶不進去),它的 python 部分沒鉤子 ⇒ 外層照實量不到。
        if any(k in env for k in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")) or _washed_copy(env):
            return env
        here = os.path.dirname(os.path.abspath(__file__))
        out = {k: v for k, v in env.items()}
        out["BASH_ENV"] = os.path.join(here, "bash-shim", "_trace.bash")
        out["EZ2_VERIFY_TRACE"] = trace
        out["EZ2_VERIFY_PY"] = sys.executable      # 墊片算雜湊用這支真的 python(見 shadow_tracer.child_env)
        out["PATH"] = os.path.join(here, "bash-shim") + os.pathsep + str(env.get("PATH") or os.defpath)
        out["PYTHONPATH"] = os.path.join(here, PY_SITE_DIR)   # bash 再起的 python 也掛得上(只有一支 sitecustomize,不露出模組)
        return out
    if not _is_python_cmd(args):
        return env
    if env.get("EZ2_VERIFY_TRACE") == trace:
        # 第三版:複製來的環境,只有 PYTHONPATH 被換掉/拿掉
        here = os.path.dirname(os.path.abspath(__file__))
        site = os.path.join(here, PY_SITE_DIR)
        pp = env.get("PYTHONPATH")
        if pp is not None and not isinstance(pp, str):
            return env
        parts = (pp or "").split(os.pathsep) if pp else []
        if here in parts or site in parts:
            return env                               # 本來就掛著
        out = {k: v for k, v in env.items()}
        out["PYTHONPATH"] = os.pathsep.join(parts + [site])
        return out
    if _family_filtered(env):
        # 第六版(10-06):測試自己組的新 env(沒有本追蹤的追蹤檔、也沒有執行器 pid ⇒ 不是「複製後刻意洗掉」的 D6 形狀),
        #   卻帶著自己的 PYTHONPATH ⇒ 第二版「三個鍵都沒有才補」放過它 ⇒ 子行程沒鉤子 ⇒ 整列量不到。
        #   兩種常見寫法:整族濾掉 EZ2_*(goal-card-69 的 clean_env,PYTHONPATH 還指著本追蹤器)、
        #   從頭組 {"PATH": …, "PYTHONPATH": 倉根, …}(line-disaster-recovery)。10-06 實測只卡這個原因的 42 支 643 秒。
        #   補法同第三版:py-site 附在**最後**(測試自己的路徑在前,自己的 sitecustomize 照樣先載 ⇒ 照實量不到),
        #   另補追蹤檔一個變數;不補執行器 pid(子行程不會被當成測試本身)。
        here = os.path.dirname(os.path.abspath(__file__))
        site = os.path.join(here, PY_SITE_DIR)
        parts = [x for x in env.get("PYTHONPATH").split(os.pathsep) if x]
        out = {k: v for k, v in env.items()}
        if not ({here, site} & {os.path.abspath(x) for x in parts}):
            out["PYTHONPATH"] = os.pathsep.join([*parts, site])
        out["EZ2_VERIFY_TRACE"] = trace
        out["EZ2_VERIFY_PY"] = sys.executable      # 墊片算雜湊用這支真的 python(見 shadow_tracer.child_env)
        _add_bash_vars(out, env)
        return out
    if any(k in env for k in ("PYTHONPATH", "BASH_ENV", "EZ2_VERIFY_TRACE")):
        return env                                   # 不是從頭組的新 env(有人刻意設過/洗過)
    if os.environ.get("EZ2_VERIFY_RUNNER_PID") != str(os.getppid()) and _washed_copy(env):
        return env                                   # 第四版(更深一層)的:洗過的複本不補;第一層維持第二版原樣(已過兩臂驗收)
    out = {k: v for k, v in env.items()}
    out["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
    out["EZ2_VERIFY_TRACE"] = trace
    out["EZ2_VERIFY_PY"] = sys.executable      # 墊片算雜湊用這支真的 python(見 shadow_tracer.child_env)
    _add_bash_vars(out, env)
    return out


def _add_bash_vars(out, env):
    """第七版(10-07):補了 python 鉤子的子行程,也一起補 bash 那一套(BASH_ENV + PATH 最前面放 bash-shim,同第五版)。
    以前只補 python 的:那支 python 再用 os.environ.copy() 起 bash ⇒ 帶著 PYTHONPATH/追蹤檔、卻沒有 BASH_ENV ⇒ 第五版不補
    (有鍵 = 有人設過)、它也不是測試本身(包裝不在那裡)⇒ bash 沒鉤子 ⇒ 整列量不到(10-07 實測 child-untracked:bash 27 支的
    主要形狀:aib-report.py 叫 report-lint.sh)。測試自己給了 BASH_ENV ⇒ 不動。"""
    if "BASH_ENV" in env:
        return
    if _washed_copy(env):
        # 10-07 第 11 包實踩(tracer-slowdown-t02 K0):複製本行程環境再刻意洗掉追蹤變數 ⇒ 測試要的是「不帶外層追蹤」
        #   (例:在複本裡自己再開一層追蹤)。第一層的 python 照第二版原樣補,bash 這一套不補 ⇒ 不會追蹤套追蹤而逾時。
        return
    shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bash-shim")
    out["BASH_ENV"] = os.path.join(shim, "_trace.bash")
    path = str(env.get("PATH") or os.defpath)
    if shim not in path.split(os.pathsep):
        out["PATH"] = shim + os.pathsep + path




def _popen_init(self, *args, **kwargs):
    try:
        cmd = args[0] if args else kwargs.get("args")
        if "env" in kwargs:
            kwargs["env"] = _with_trace_vars(cmd, kwargs["env"])
        elif len(args) > _POPEN_ENV_POS:
            args = list(args)
            args[_POPEN_ENV_POS] = _with_trace_vars(cmd, args[_POPEN_ENV_POS])
    except Exception:                                # 鉤子出錯絕不准讓測試失敗:照原樣起
        pass
    return _POPEN_INIT_ORIG(self, *args, **kwargs)


# ── 10-07:C 寫的函式庫自己讀檔(不經過 Python 的 open ⇒ 沒有審計事件)—— 讀的檔改了照樣沿用 = 紅藏成綠 ───────────────
#   (同一天 libpq 自己連線的漏洞,見 _NATIVE_NET_MODULES。)名單上的模組載入完,把「拿路徑讀檔」的函式包起來:
#   呼叫前把那個路徑記成輸入(檔 ⇒ 內容雜湊;不存在 ⇒ missing;目錄 ⇒ 量不到)。類別(Python 寫的)包它的 __init__。
#   整套都用 C 讀檔、包不完的(h5py、GDAL…)⇒ 一載入就標量不到。名單外的 C 擴充仍然看不到(已知限制,寫在說明)。
_NATIVE_FILE_FUNCS = {
    "lxml.etree": (("parse", 0, "source"), ("iterparse", 0, "source")),
    "cv2": (("imread", 0, "filename"), ("imreadmulti", 0, "filename")),
    "pyarrow.parquet": (("read_table", 0, "source"), ("read_metadata", 0, "where"), ("read_schema", 0, "where"),
                        ("ParquetFile", 0, "source"), ("ParquetDataset", 0, "path_or_paths")),
    "pyarrow.csv": (("read_csv", 0, "input_file"), ("open_csv", 0, "input_file")),
    "pyarrow.feather": (("read_table", 0, "source"), ("read_feather", 0, "source")),
    "pyarrow.dataset": (("dataset", 0, "source"),),
}
_NATIVE_FILE_UNMEASURED = frozenset(("h5py", "netCDF4", "tables", "osgeo.gdal", "rasterio", "fiona", "soundfile"))


def _note_native_read(arg):
    """C 函式庫要讀的路徑 ⇒ 記成輸入。不是路徑(已開的檔、緩衝區)⇒ 不記(那些是 Python 這邊開的,已經記過)。"""
    global _IN_HOOK
    if _IN_HOOK or not _trace_path():
        return
    try:
        p = os.fspath(arg)
    except TypeError:
        if isinstance(arg, (list, tuple)):
            for x in arg:
                _note_native_read(x)
        return
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not isinstance(p, str) or "://" in p:
        if isinstance(p, str):
            _emit({"ch": "external", "why": "native-file-reader-url"})
        return
    _IN_HOOK = True
    try:
        if os.path.isdir(p):
            _emit({"ch": "unmeasured", "why": "native-file-reader-dir"})
        else:
            _record_path(p, "open")
    except Exception:  # noqa: BLE001 - 觀測者不得讓被觀測物出錯;量不到就說
        _emit({"ch": "unmeasured", "why": "native-file-reader-error"})
    finally:
        _IN_HOOK = False


def _wrap_native_reader(fn, pos, kw):
    def wrapper(*args, **kwargs):
        if kw in kwargs:
            _note_native_read(kwargs[kw])
        elif len(args) > pos:
            _note_native_read(args[pos])
        return fn(*args, **kwargs)
    try:
        wrapper.__name__ = getattr(fn, "__name__", "wrapper")
        wrapper.__doc__ = getattr(fn, "__doc__", None)
        wrapper.__wrapped__ = fn
    except (AttributeError, TypeError):
        pass
    return wrapper


def _native_sub_new(base, pos, kw):
    def __new__(cls, *args, **kwargs):
        if kw in kwargs:
            _note_native_read(kwargs[kw])
        elif len(args) > pos:
            _note_native_read(args[pos])
        if base.__new__ is object.__new__:
            return object.__new__(cls)
        return base.__new__(cls, *args, **kwargs)
    return __new__


def _patch_native_module(name):
    mod = sys.modules.get(name)
    if mod is None:
        return
    if name in _NATIVE_FILE_UNMEASURED:
        _emit({"ch": "unmeasured", "why": "native-file-reader:%s" % name})
        return
    for attr, pos, kw in _NATIVE_FILE_FUNCS.get(name, ()):
        obj = getattr(mod, attr, None)
        if obj is None or getattr(obj, "_aib_native_wrapped", False):
            continue
        try:
            if isinstance(obj, type):
                init = obj.__dict__.get("__init__")
                if init is None or not hasattr(init, "__code__"):
                    # C 寫的類別(lxml 的 iterparse):改成它的子類別,建構時先記路徑。做不了子類別 ⇒ 載入就標量不到
                    try:
                        sub = type(obj.__name__, (obj,), {"__new__": _native_sub_new(obj, pos, kw), "_aib_native_wrapped": True,
                                                          "__module__": getattr(obj, "__module__", name)})
                    except TypeError:
                        _emit({"ch": "unmeasured", "why": "native-file-reader:%s.%s" % (name, attr)})
                        continue
                    setattr(mod, attr, sub)
                    continue
                w = _wrap_native_reader(init, pos + 1, kw)
                obj.__init__ = w
                obj._aib_native_wrapped = True
            else:
                w = _wrap_native_reader(obj, pos, kw)
                w._aib_native_wrapped = True
                setattr(mod, attr, w)
        except (AttributeError, TypeError):
            _emit({"ch": "unmeasured", "why": "native-file-reader:%s.%s" % (name, attr)})


class _NativeReaderLoader:
    def __init__(self, loader, name):
        self._loader, self._name = loader, name

    def create_module(self, spec):
        return self._loader.create_module(spec) if hasattr(self._loader, "create_module") else None

    def exec_module(self, module):
        self._loader.exec_module(module)
        _patch_native_module(self._name)

    def __getattr__(self, attr):
        return getattr(self._loader, attr)


class _NativeReaderFinder:
    """只攔名單上的模組名;找真正的 spec 交給後面的找法,把 loader 包一層(載入完才包函式)。"""
    def find_spec(self, name, path=None, target=None):
        if name not in _NATIVE_FILE_FUNCS and name not in _NATIVE_FILE_UNMEASURED:
            return None
        for f in sys.meta_path:
            if f is self or not hasattr(f, "find_spec"):
                continue
            try:
                spec = f.find_spec(name, path, target)
            except Exception:  # noqa: BLE001
                spec = None
            if spec is not None:
                if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                    spec.loader = _NativeReaderLoader(spec.loader, name)
                return spec
        return None


def _record_pth_files():
    """10-07(安全網試驗抓到):site-packages 的 .pth 在鉤子掛上之前就被 site 讀過(可以改 sys.path、甚至執行程式,
    例:editable 安裝的 flask.pth 指到哪棵樹)⇒ 沒有審計事件。啟動時把 sys.path 上 site/dist-packages 裡的 .pth、
    虛擬環境的 pyvenv.cfg 記成輸入。"""
    global _IN_HOOK
    _IN_HOOK = True
    try:
        _cfg = os.path.join(sys.prefix, "pyvenv.cfg")       # 虛擬環境的設定(啟動時讀,同樣早於鉤子)
        if os.path.isfile(_cfg):
            _record_path(_cfg, "open")
        seen = set()
        for d in list(sys.path):
            if not d or d in seen or not str(d).rstrip("/").endswith(("site-packages", "dist-packages")):
                continue
            seen.add(d)
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for f in names:
                if f.endswith(".pth"):
                    _record_path(os.path.join(d, f), "open")
    except Exception:  # noqa: BLE001
        _emit({"ch": "unmeasured", "why": "pth-record-error"})
    finally:
        _IN_HOOK = False


def _install_native_readers():
    if not any(isinstance(f, _NativeReaderFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _NativeReaderFinder())
    for name in list(_NATIVE_FILE_FUNCS) + sorted(_NATIVE_FILE_UNMEASURED):
        if name in sys.modules:           # 鉤子裝上之前就載入了(極少)⇒ 現在補包
            _patch_native_module(name)


def _install_popen_env_injection():
    global _POPEN_INIT_ORIG
    import subprocess
    if _POPEN_INIT_ORIG is None:
        _POPEN_INIT_ORIG = subprocess.Popen.__init__
        subprocess.Popen.__init__ = _popen_init


# ── 10-06 晚 gettext 以字串為單位(開發檔 §15.3c;給所有 aib 用戶:Django、Flask-Babel、babel 都走 gettext)──────────────
#   GNUTranslations 讀完 .mo 之後:寫一筆 i18nload(哪個 .mo、這個行程),目錄表換成會記帳的 dict ——
#   查一個字串 ⇒ i18nkey;列舉、長度、複製、轉字串、比較、序列化 ⇒ "@whole"(用到整份)。分析端的規則與 aib 自己的
#   翻譯字典相同(shadow_tracer.i18n_narrow):每一次開檔都對得上 i18nload、沒有 @whole,才改成只看用到的字串。
#   不主動 import gettext(觀測者不改被觀測物的 sys.modules):第一次開 .mo 檔時 gettext 一定已經載入,那時才換類別上的
#   _parse(開檔之後才呼叫 ⇒ 這一次就生效)。
_I18N_SEEN = set()
_GETTEXT_DONE = False


def _i18n_key(key):
    k = key if isinstance(key, str) else json.dumps([str(x) for x in key] if isinstance(key, tuple) else repr(key),
                                                    ensure_ascii=True)
    if k in _I18N_SEEN:
        return
    _I18N_SEEN.add(k)
    _emit({"ch": "i18nkey", "key": k})


class _KeyedCatalog(dict):
    def __getitem__(self, k):
        _i18n_key(k)
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        _i18n_key(k)
        return dict.get(self, k, default)

    def __contains__(self, k):
        _i18n_key(k)
        return dict.__contains__(self, k)

    def pop(self, k, *default):
        _i18n_key(k)
        return dict.pop(self, k, *default)

    def setdefault(self, k, default=None):
        _i18n_key(k)
        return dict.setdefault(self, k, default)


def _whole_view(name):
    orig = getattr(dict, name)

    def method(self, *a, **kw):
        _i18n_key("@whole")
        return orig(self, *a, **kw)
    method.__name__ = name
    return method


for _name in ("__iter__", "__len__", "__repr__", "__eq__", "__ne__", "__reduce__", "__reduce_ex__", "keys", "items",
              "values", "copy", "__or__", "__ror__", "popitem", "__reversed__", "__sizeof__"):
    setattr(_KeyedCatalog, _name, _whole_view(_name))
_KeyedCatalog.__hash__ = None


def _install_gettext_keys():
    global _GETTEXT_DONE
    _gt = sys.modules.get("gettext")
    if _GETTEXT_DONE or _gt is None or not hasattr(getattr(_gt, "GNUTranslations", None), "_parse"):
        return
    _GETTEXT_DONE = True
    orig = _gt.GNUTranslations._parse

    def _parse(self, fp):
        orig(self, fp)
        name = getattr(fp, "name", None)
        if isinstance(name, str):
            _emit({"ch": "i18nload", "path": os.path.abspath(name)})
        if type(getattr(self, "_catalog", None)) is dict:
            self._catalog = _KeyedCatalog(self._catalog)
    _gt.GNUTranslations._parse = _parse


# ── 10-06 晚 宣告過的 JSON 字典以鍵為單位(§15.3c 第 2 項:自己寫翻譯函式、字典放 JSON 的專案)────────────────────────
#   用戶在 .aib/ship.toml 寫 [test] keyed_files = ["locales/*.json"];執行器把它交給 AIB_KEYED_FILES(根目錄 + 樣式)。
#   開到宣告過的檔 ⇒ 記下它的內容 sha;之後 json.loads(json.load 也走它)解出的內容 sha 對得上 ⇒ 這份是那個字典:
#   寫一筆 i18nload,最外層換成會記帳的 dict(取一個鍵 ⇒ 記那個鍵;列舉、比較、複製 ⇒ 整份)。對不上(換行被轉、
#   先改過內容、用別的解析器)⇒ 不換、不記 ⇒ 分析端照整檔(安全側)。只看最外層:取到的鍵底下整棵值都算進摘要。
_KEYED_SPEC = None
_KEYED_SHAS = {}
_KEYED_AUTO = False      # 10-12:自動偵測到的(沒宣告)⇒ 只認 json.load(檔) 這條,見下
_KEYED_TLS = _ThreadLocal()   # .fp_path:這個執行緒正在 json.load(真檔案) 裡面、那個檔的實體路徑(a seat 審查低 3:每執行緒各一份)
_LOOSE_KW = ("strict",)   # 只有這個不影響「看過整份沒有」;其餘任何參數有值(cls、object_hook、array_hook…)= 嚴格解析


def _keyed_declared(path):
    try:
        import fnmatch
        root, globs = _KEYED_SPEC
        rel = os.path.relpath(os.path.realpath(str(path)), root)
        return not rel.startswith("..") and any(fnmatch.fnmatch(rel, g) for g in globs)
    except Exception:  # noqa: BLE001
        return False


def _aib_count(f):
    if getattr(_KEYED_TLS, "fp_path", None) is None:   # None = 不在 json.load 裡;load 外面的讀才算
        f._aib_n = getattr(f, "_aib_n", 0) + 1


class _CountedText(io.TextIOWrapper):
    """字典檔(文字模式)的讀取計數。用子類別不用實例屬性:for line in f 走型別的 __next__,實例上掛不到(a seat 23:3x);
    也不會有「實例 → 閉包 → 綁定方法 → 實例」的循環,檔照常即時關。"""

    def read(self, *a, **k):
        _aib_count(self)
        return io.TextIOWrapper.read(self, *a, **k)

    def readline(self, *a, **k):
        _aib_count(self)
        return io.TextIOWrapper.readline(self, *a, **k)

    def readlines(self, *a, **k):
        _aib_count(self)
        return io.TextIOWrapper.readlines(self, *a, **k)

    # 不覆寫 __next__:C 的 TextIOWrapper.__next__ 對子類別一律改叫 self.readline() ⇒ 逐行已經經由上面的 readline 計數
    #   (審查 r8 實測;刀因此落在 readline)。


class _CountedBuffered(io.BufferedReader):
    """字典檔(二進位模式)的讀取計數;peek / read1 / readinto 也算(peek 拿到原文卻不動位置)。"""


for _name in ("read", "read1", "readline", "readlines", "readinto", "readinto1", "peek", "__next__"):
    def _counting(self, *a, _base=getattr(io.BufferedReader, _name), **k):
        _aib_count(self)
        return _base(self, *a, **k)
    _counting.__name__ = _name
    setattr(_CountedBuffered, _name, _counting)


def _counted_file(f, mode, a, kw):
    """open 剛開好的檔 → 同一個底層、會計數的子類別;換不上 ⇒ 原檔(先建新的、成功才拆舊的,失敗不弄壞原檔)。"""
    if type(f) is io.TextIOWrapper:
        args = dict(zip(("buffering", "encoding", "errors", "newline", "closefd", "opener"), a))
        args.update(kw)
        g = _CountedText(f.buffer, encoding=f.encoding, errors=f.errors, newline=args.get("newline"),
                         line_buffering=f.line_buffering)
        g.mode = mode                         # open() 在文字檔上設的屬性
        f.detach()
        return g
    if type(f) is io.BufferedReader:
        g = _CountedBuffered(f.raw)
        f.detach()
        return g
    return f


def _install_keyed_json():
    global _KEYED_SPEC
    raw = os.environ.get("AIB_KEYED_FILES") or ""
    if not raw:
        return
    global _KEYED_AUTO
    try:
        spec = json.loads(raw)
        _KEYED_SPEC = (os.path.realpath(str(spec["root"])), [str(g) for g in spec["globs"]])
        _KEYED_AUTO = bool(spec.get("auto"))
    except Exception:  # noqa: BLE001
        return
    orig = json.loads
    orig_load = json.load

    # 10-12 a seat(a seat 裁 B):自動偵測等於所有用戶預設都開 ⇒ 不能帶著「錯沿用」的洞出去。
    #   測試自己 read() 出原文再 json.loads ⇒ 原文在它手上,可能拿去比快照、交給 node、寫出去 —— 那些用到的是整份,
    #   而這裡只看得到解析後取了哪些鍵 ⇒ 會被縮成只看那幾個鍵,改別的鍵時錯沿用。
    #   自動模式只認 json.load(檔):原文只在 json.load 裡經 fp.read() 交給解析器,測試拿不到 ⇒ 縮成看鍵才安全;
    #   其餘(json.loads(原文))⇒ 不換、不記 ⇒ 照整檔。宣告制(.aib/ship.toml)是用戶自己選的,照舊兩條都認。
    #   10-12 a seat 審查(中/中低/低,每條有刀):
    #   · 嚴格解析(cls、object_hook、object_pairs_hook、parse_*)在解析當下看過整份(例:拒絕重複鍵)⇒ 不換、不記;
    #     json.load 轉呼 loads 時這幾個一律帶著(值 None)⇒ 看值不看有沒有帶;反過來寫成白名單(審查 r2:3.15 多了 array_hook,
    #     以後 Python 再加參數也不漏);
    #   · json.load 只認「真檔案」而且就是開到的那個檔(StringIO 之類原文在測試手上 ⇒ 整份);
    #     load 完同一個 fp 又 seek(要重讀就得先倒回去)⇒ 記 @whole;
    #   · 同內容的檔不只一個:在 load 裡照 fp 的路徑認;不在 load 裡(宣告制的 loads(原文))認不出是哪個 ⇒ 不換。
    #   · load **之前**同一個 fp 已經讀過(raw = f.read(); f.seek(0)、for line in f 再 seek(0))、或讀取位置不在開頭
    #     ⇒ @whole(a seat 23:2x / 23:3x:這兩種寫法都不少見)。開宣告/偵測到的字典檔(純讀模式)時交回會計數的
    #     子類別(_CountedText / _CountedBuffered,見 _counted_file),load 裡的讀不算。
    #   已知沒擋到(很少見,寫明):文字檔的 fp.buffer.*(底下那層沒換)、os.lseek(fp.fileno())、os.read(fd) 繞過 fp;
    #     用 bytes 路徑開、buffering=0 開的檔不計數。
    #   副作用(寫明):追蹤中的測試行程裡 open 是這裡的區域函式 ⇒ pickle.dumps(open) 會失敗(把 open 當參數交給
    #     spawn 子行程才會碰到);字典檔的 type(f) 是子類別(isinstance 照舊成立,type(f) is io.TextIOWrapper 不成立;
    #     repr / ResourceWarning 裡的型別名是 _CountedText / _CountedBuffered)。
    def load(fp, *a, **kw):
        prev = getattr(_KEYED_TLS, "fp_path", None)
        name = getattr(fp, "name", None)
        real = isinstance(fp, io.IOBase) and not isinstance(fp, (io.StringIO, io.BytesIO)) and isinstance(name, str)
        read_before = True       # 不是真檔案 ⇒ 不去碰它(mock_open 會記下多出來的 tell 呼叫,審查 r4);被換了也算整份
        if real:
            try:
                read_before = getattr(fp, "_aib_n", 0) > 0 or fp.tell() != 0
            except Exception:  # noqa: BLE001 —— 量不到位置 ⇒ 當作讀過(安全側)
                pass
        _KEYED_TLS.fp_path = os.path.realpath(name) if real else ""
        try:
            obj = orig_load(fp, *a, **kw)
        finally:
            _KEYED_TLS.fp_path = prev
        if type(obj) is _KeyedCatalog and read_before:
            _i18n_key("@whole")                    # load 之前原文已經到過測試手上 ⇒ 整份
        if type(obj) is _KeyedCatalog:
            try:
                # seek 鉤子可以只弱參照:能呼叫 fp.seek 就表示有人拿著 fp(load 前後同一個物件)。極罕見的例外:
                #   某函式交出 fp 時自己已放掉、又在運算式裡直接 .seek ⇒ wfp() 為 None(寫明,不處理)。
                inner_seek = fp.__dict__.get("seek")
                wfp = _weakref(fp)

                def _seek_whole(*sa, **skw):
                    _i18n_key("@whole")            # load 完又倒回去 ⇒ 要重讀原文 ⇒ 整份
                    if inner_seek is not None:
                        return inner_seek(*sa, **skw)
                    obj = wfp()
                    return type(obj).seek(obj, *sa, **skw)
                fp.seek = _seek_whole
            except (AttributeError, TypeError):
                return dict(obj)                    # 掛不上 ⇒ 不縮(安全側)
        return obj

    def loads(s, *a, **kw):
        obj = orig(s, *a, **kw)
        if not _KEYED_SHAS or type(obj) is not dict or a or any(v is not None for k, v in kw.items() if k not in _LOOSE_KW):
            return obj
        in_load = getattr(_KEYED_TLS, "fp_path", None)
        if _KEYED_AUTO and not in_load:
            return obj
        try:
            b = s.encode("utf-8") if isinstance(s, str) else bytes(s)
            paths = _KEYED_SHAS.get(hashlib.sha256(b).hexdigest()) or set()
        except Exception:  # noqa: BLE001
            paths = set()
        if in_load:
            path = in_load if in_load in paths else None
        else:
            path = next(iter(paths)) if len(paths) == 1 else None
        if path:
            _emit({"ch": "i18nload", "path": path})
            return _KeyedCatalog(obj)
        return obj
    json.loads = loads
    json.load = load

    import builtins
    orig_open = builtins.open

    def keyed_open(file, mode="r", *a, **kw):
        f = orig_open(file, mode, *a, **kw)
        try:
            m = str(mode)
            if isinstance(file, (str, os.PathLike)) and "r" in m and set(m) <= set("rtb") \
                    and _keyed_declared(os.fspath(file)):
                return _counted_file(f, m, a, kw)
        except Exception:  # noqa: BLE001 —— 換不上就用原本的檔:load 時量不到讀取次數,還有 tell 一道
            pass
        return f
    builtins.open = keyed_open
    # 10-13:Python 3.10 的 pathlib 在類別裡存 `open = io.open`(_NormalAccessor)⇒ 換成一般函式會被綁成方法,
    #   Path.read_text 把路徑塞進 mode(公開倉 CI smoke 3.10 紅)。functools.partial 不是描述器,不會被綁。
    import functools
    io.open = functools.partial(keyed_open)


def start():
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    if not _trace_path():
        return
    # 10-04:追蹤檔寫不進去(降權的子行程、唯讀目錄)⇒ 什麼都不裝、什麼都不印(與 bash 側 _trace.bash 同一條規矩);
    #   分析端會因為少了這個行程的 inherit 判量不到(安全側)。以前這裡丟 PermissionError ⇒ 「Error in sitecustomize」污染被測物。
    try:
        with open(_trace_path(), "a", encoding="utf-8"):
            pass
    except OSError:
        return
    _emit_t0()                 # 鉤子掛上之前:自己列暫存根不會變成事件;「追蹤檔還是空的」才判得準
    sys.addaudithook(_audit)
    _install_probe_wrappers()
    _install_dirfd_open_wrapper()
    _install_keyed_json()
    _install_native_readers()
    _record_pth_files()
    if _is_test_process():
        _install_popen_env_injection()   # 只有測試本身才裝(見修法 A 第二版、第四版註解)
    import atexit

    def _flush_selfwrites(via=None):
        """結束時記自己寫過的檔的最終內容:同名不同內容的治具檔會讓 selfwrite: 不同 ⇒ 鑰匙不同 ⇒ 必重跑。
        寫了又刪的不記(不存在);目錄不記。"""
        global _IN_HOOK
        _IN_HOOK = True
        try:
            for _p in sorted(_WRITTEN):
                try:
                    if not os.path.isfile(_p):
                        continue
                    digest, missing = _sha_file(_p)
                    if digest and not missing:
                        _emit({"ch": "selfwrite", "path": os.path.abspath(_p), "sha256": digest})
                except (OSError, ValueError, TypeError):
                    continue
            _emit(dict({"ch": "exit", "pid": os.getpid()}, **({"via": via} if via else {})))
            # ↑ 完成證據:沒有這筆(信號、致命錯誤)⇒ 這個行程量不齊
        finally:
            _IN_HOOK = False

    atexit.register(_flush_selfwrites)
    # 10-07(外部倉 pytest-xdist 實踩):os._exit 不跑 atexit ⇒ 沒有結束紀錄 ⇒ 整支量不到、每次必重跑。
    #   os._exit 是程式自己決定的結束(multiprocessing 的 fork 子行程、xdist 測試故意弄掛工作行程都走這條),
    #   每筆讀寫紀錄都是當下寫進追蹤檔的(_emit 每筆開檔附加)⇒ 到這裡為止記的就是全部,補記結束紀錄再真的結束。
    #   被信號殺掉、直譯器崩潰仍然沒有結束紀錄,照舊量不到。
    if not getattr(os._exit, "_aib_flushing", False):
        _real_os_exit = os._exit

        def _os_exit_flushing(status):
            try:
                _flush_selfwrites("os._exit")
            except BaseException:  # noqa: BLE001 - 觀測者不得讓被觀測物結束不了
                pass
            _real_os_exit(status)
        _os_exit_flushing._aib_flushing = True
        os._exit = _os_exit_flushing
    # 10-04 晚:ppid/argv 只供診斷(對出「哪個 python 子行程沒回報」),分析端只用 pid
    try:
        _a = [str(x)[-120:] for x in (getattr(sys, "argv", None) or [])[:3]]
    except Exception:  # noqa: BLE001
        _a = []
    _emit({"ch": "inherit", "pid": os.getpid(), "ppid": os.getppid(), "argv": _a})


start()
