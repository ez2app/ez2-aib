#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""執行 aib verify 的排序跑單，產生綁定 git write-tree 的收據。

第一版只接受 AIB 自己的 Python 測試檔。快驗使用 P2 挑選器、沿用索引與計畫器；
嚴格模式跑全庫。任何無法安全挑選的狀況都退回全跑。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "governance"))

import planner
import receipt
import resume_policy
import reuse_index
import select_changed
import shadow_tracer as st

# ── 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py;runner.py 就是 `aib verify --changed`,是客戶介面,
#    該棘輪註解明定「搬進 aib verify 時必須同顆接四語」)。順序照 reuse_index.py:本樹 insert(0)、fallback append。
_COCKPIT = ROOT / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))
_COCKPIT_FALLBACK = Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_COCKPIT_FALLBACK) not in sys.path:
    sys.path.append(str(_COCKPIT_FALLBACK))
import i18n_lib  # noqa: E402
try:
    import memguard  # noqa: E402  10-24:每支測試的記憶體上限(TreeCap)
except ImportError:  # 只複製 tools/verify 的夾具(而 fallback 主樹還沒這支)⇒ 不掛上限,其餘照舊
    memguard = None

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t("aib.verify.runner." + key, params, locale=LOC)

# 09-29 a seat(docs/DESIGN-shadow-resume-complete-0929.md 零節 11、12):
#   缺口 1:以前跑測試不掛追蹤器 ⇒ 只讀沿用池、從不寫入,池子永遠是空的,快驗實際上一直全跑。
#   缺口 2:判沿用用本行程自己的環境、跑測試用乾淨環境 ⇒ 環境指紋對不上。
#   ⇒ 每支實跑都掛追蹤、跑完寫一列進沿用池(source=aib-verify);判沿用傳入跑測試那一份乾淨環境。
ROW_SOURCE = "aib-verify"
TAIL_CHARS = 800          # 沿用時給席位看的上次輸出尾巴長度
DEFAULT_TIMEOUT_S = 1800   # 每支上限;呼叫端(aib test、package_trial --timeout)可給自己的

RAN_RE = re.compile(r"\bRan\s+(\d+)\s+tests?\b", re.I)
FAIL_RE = re.compile(r"^(FAIL|ERROR):\s*(.+?)\s*$", re.M)
LEASE_TTL = 3 * 60 * 60
# 暫存根:macOS 用 /private/tmp(倉內歷史測試一致),Linux 沒有它就用 /tmp(同 tests/aib_tmp_root.py 的順序)。
#   雲端 09-28 說修過「寫死 /private/tmp 在 Linux 壞掉」,但那份修正沒推上任何分支;這裡重做。
TMP_ROOT = "/private/tmp" if os.path.isdir("/private/tmp") and os.access("/private/tmp", os.W_OK) else "/tmp"
PROBE_HOME = os.path.join(TMP_ROOT, ".aib-verify-probe-home")   # HOME 在環境指紋裡本來就排除(VOLATILE_ENV),值不影響鑰匙


def _is_core_tree(root: Path) -> bool:
    """這棵是核心倉(倉根 = aib/ 或它的上一層 ez2-aib)。"""
    return any((r / "src" / "cockpit" / "aib").is_file() for r in (root, root / "aib"))


def clean_test_env(home: str, repo_root: str | Path = ROOT) -> dict[str, str]:
    """以 env -i 語意組出明列白名單的測試環境。"""
    path = "/opt/homebrew/opt/flock/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    root = Path(repo_root).resolve()
    pythonpath = os.pathsep.join(str(p) for p in (
        root, root / "tests", root / "src" / "cockpit", root / "src" / "ledger",
        root / "tools" / "governance",
    ))
    env = {
        "HOME": home,
        "PATH": path,
        "TMPDIR": TMP_ROOT,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        # 10-03:tmux 的 socket 目錄也跟著假 HOME 走。沒有這行時,測試(或它叫的產品腳本)叫真 tmux 又沒帶 -L/-S,
        #   會連到系統的 /tmp/tmux-<uid>/default —— 在 Mac / Fish 上那就是正在用的駕駛艙(多機分片的前提;
        #   Mac 在開發官的 Fish 腳本上抓到同一類)。值等於 HOME ⇒ 環境指紋裡正規化成 <HOME>,不會讓鑰匙每跑都變。
        "TMUX_TMPDIR": home,
    }
    # 10-04 晚(外部倉實測):專案自己的虛擬環境 —— 明講的 AIB_TEST_PYTHON、已啟用的 VIRTUAL_ENV 照抄(shadow_tracer.test_python
    #   依它選 python;兩個值都進環境指紋)。VIRTUAL_ENV 等於「已啟用」:它的 bin 放 PATH 最前面,測試裡叫 python/pytest 也用它。
    # 10-18(開發文件第 37.1 節):AIB_TEST_NOSTR_VENV 也照抄 —— 以前被清掉,連 Nostr 的格在全套裡只能跳過,
    #   大家改成「直接跑測試」,那條路沒有假 HOME(10-17 test-chat-roster 就讀到 Pro 真的設定)。它只是一個 venv 的路徑。
    for k in ("AIB_TEST_PYTHON", "VIRTUAL_ENV", "AIB_TEST_NOSTR_VENV"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if os.environ.get("VIRTUAL_ENV") and os.path.isdir(os.path.join(os.environ["VIRTUAL_ENV"], "bin")):
        env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]
    # 10-23(@74e8b 回報、@abb89 派):外掛倉(aib-plugins)的測試靠 AIB_CORE_DIR 找核心(seat_address、roles…),
    #   以前一起被洗掉 ⇒ 38/135 紅 No module named。只對「不是核心倉」的樹放這一個:明講的值是資料夾就照抄,
    #   沒講就帶正在跑的核心(ROOT)。核心倉自己照舊不帶(環境指紋不變);其他 AIB_* 照舊不放行。
    if not _is_core_tree(root):
        core = os.environ.get("AIB_CORE_DIR") or ""
        env["AIB_CORE_DIR"] = core if os.path.isabs(core) and os.path.isdir(core) else str(ROOT)
    # ★ review r13(畫面向)撤回 r12 的放行:真 tmux 開關**不**帶進來。aib test 就地在測試檔所在的樹跑(test_entry
    #   不抄副本),而有真 tmux 測試的刀直接改寫 ROOT 下的產品檔(test-cabin-door-whitelist-c37be.py 把艙門拆掉、
    #   逾時被強殺時 finally 不跑 ⇒ 正在用的產品樹停在沒有門的狀態)。真 tmux 測試維持「直接執行、自己負責」,
    #   不從唯一合法入口放行;要放行得先把那些刀改成落在副本樹上。
    return env


REAL_HOME_OK_MARK = "@needs-real-home"   # 測試檔開頭 2 KB 內寫這個標記 ⇒ 不檢查(要真家目錄的極少數,審查時要看到)


def real_home():
    """執行 aib test 的使用者真正的家目錄:看 passwd,不看 HOME(測試沙盒會改 HOME)。AIB_TEST_REAL_HOME 只給測這個檢查本身用。"""
    import pwd
    return os.environ.get("AIB_TEST_REAL_HOME") or pwd.getpwuid(os.getuid()).pw_dir


def _head(path, n=2048):
    try:
        with open(path, "rb") as f:
            return f.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def isolated_test_env(home: str | Path, repo_root: str | Path) -> dict[str, str]:
    """給乾淨 HOME 一個唯讀專案別名，供工具的 home fallback 找完整快照。"""
    home_path = Path(home)
    home_path.mkdir(parents=True, exist_ok=True)
    root = Path(repo_root).resolve()
    alias = home_path / "ez2-aib"
    if alias.is_symlink():
        if alias.resolve() != root:
            raise RuntimeError(T("home_alias_other_tree"))
    elif alias.exists():
        raise RuntimeError(T("home_alias_exists"))
    else:
        alias.symlink_to(root, target_is_directory=True)
    return clean_test_env(str(home_path), root)


def _create_snapshot(repo: Path, run_id: str) -> tuple[Path, Path]:
    """建立含完整追蹤樹與 git metadata 的暫存 worktree。"""
    parent = Path(tempfile.mkdtemp(prefix=run_id + "-snapshot-", dir=TMP_ROOT))
    snapshot = parent / "repo"
    commit = _git(repo, "rev-parse", "HEAD")
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach",
                        str(snapshot), commit], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        shutil.rmtree(parent, ignore_errors=True)
        raise
    return parent, snapshot


def _remove_snapshot(repo: Path, parent: Path, snapshot: Path) -> None:
    """解除 worktree 註冊並清除暫存完整樹。"""
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force",
                        str(snapshot)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, check=True)
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(T("git_failed", {"args": " ".join(args), "err": (p.stderr or "").strip()}))
    return p.stdout.strip()


def _known_reds(path: str | None) -> set[str]:
    if not path:
        path = str(ROOT / "tests" / "known-reds.json")
    p = Path(path)
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    today = dt.date.today()
    result = set()
    for name, row in (data.get("known_reds") or {}).items():
        try:
            expires = dt.date.fromisoformat(row.get("expires", ""))
        except (AttributeError, TypeError, ValueError):
            continue
        if expires >= today:
            cell_id = name if name.startswith("tests/") else "tests/" + name
            # Legacy known-reds.json is file-scoped. P1 receipts now match only
            # file:cell IDs, so keep legacy entries limited to unparsed whole-file reds.
            if ":" not in cell_id:
                cell_id += ":*"
            result.add(cell_id)
    return result


def _require_clean_snapshot(repo: Path) -> None:
    status = _git(repo, "status", "--porcelain")
    if status:
        raise RuntimeError(T("dirty_tree"))


NODE_TESTS_RE = re.compile(r"^(?:#|ℹ) tests (\d+)\s*$", re.M)
# 10-09(公開倉 CI 實踩):有裝 pytest 時,沒有 main 守衛的測試檔由 pytest 跑,總結是「1 passed in 0.01s」或
#   「=== 2 failed, 3 passed, 1 skipped in 0.12s ===」,沒有 unittest 的「Ran N tests」⇒ 以前格數 None。這台沒裝 pytest 一直沒走到。
PYTEST_SUMMARY_RE = re.compile(r"^=*\s*((?:\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed)(?:, )?)+)"
                               r"(?:, \d+ (?:warnings?|deselected|rerun))*\s+in [\d.]+s\b", re.M)
PYTEST_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")
PYTEST_FAILED_RE = re.compile(r"^(FAILED|ERROR) (\S+::\S+)", re.M)          # 10-04:node:test 的總結(TAP 或 spec 報表)
NODE_NOTOK_RE = re.compile(r"^\s*(?:not ok \d+ - |✖ )(.+?)\s*(?:\(\d[\d.]*ms\))?\s*$", re.M)


def _parse_test_output(output: str) -> tuple[int | None, list[str]]:
    matches = list(RAN_RE.finditer(output))
    ran = int(matches[-1].group(1)) if matches else None
    if ran is None:
        pm = list(PYTEST_SUMMARY_RE.finditer(output))
        if pm:
            ran = sum(int(n) for n, _k in PYTEST_COUNT_RE.findall(pm[-1].group(1)))
            cells = []
            for kind, name in PYTEST_FAILED_RE.findall(output):
                c = "%s:%s" % ("FAIL" if kind == "FAILED" else "ERROR", name)
                if c not in cells:
                    cells.append(c)
            return ran, cells
        nm = list(NODE_TESTS_RE.finditer(output))
        if nm:
            ran = int(nm[-1].group(1))
            cells = []
            for name in NODE_NOTOK_RE.findall(output):
                c = "FAIL:%s" % name
                if c not in cells:
                    cells.append(c)
            return ran, cells
    cells = []
    for kind, name in FAIL_RE.findall(output):
        cell = "%s:%s" % (kind, name)
        if cell not in cells:
            cells.append(cell)
    return ran, cells


_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.M)
# 10-04 晚(外部倉 itsdangerous):pytest 最常見的 `class TestXxx:`(不繼承)與 async def test_ 也算有測試
_HAS_TESTS = re.compile(r"^(?:(?:async\s+)?def\s+test\w*\s*\(|class\s+\w+\s*\([^)]*TestCase|class\s+Test\w*\s*[:(])", re.M)


JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx")


def js_runner(repo: Path):
    """10-07(外部專案 JS 實測):專案用哪個測試框架 ⇒ (名字, 執行檔路徑或 None);都沒有 ⇒ (None, None)。
    看倉根 package.json 的 dependencies/devDependencies;執行檔要真的在 node_modules/.bin/ 底下(沒裝 ⇒ None,
    呼叫端照實說要先安裝,不替使用者改用別的框架)。順序 vitest > jest > mocha;都沒有 ⇒ node 內建 node:test。"""
    try:
        data = json.loads((Path(repo) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    deps = {}
    for k in ("dependencies", "devDependencies"):
        if isinstance(data.get(k), dict):
            deps.update(data[k])
    for name in ("vitest", "jest", "mocha"):
        if name in deps:
            exe = Path(repo) / "node_modules" / ".bin" / name
            return name, (str(exe) if exe.exists() else None)
    return None, None


def _js_command(repo: Path, path: Path) -> list[str]:
    """JS/TS 測試檔怎麼跑:用專案自己的框架、一次只跑這一支(相對倉根;框架自己的設定檔照讀)。
    vitest 用 forks(每個測試檔一個 node 子行程,接頭跟著 NODE_OPTIONS 掛上;執行緒池量不到);jest 用 --runInBand
    (同一個行程跑,不開工作行程)、--no-watchman(找檔改走 node 自己的 fs,追蹤器看得到;watchman 是外部程式)。兩者都關掉覆蓋率:只多插樁、不影響單支紅綠(覆蓋率門檻是整套的事,不在單支裡)。"""
    rel = os.path.relpath(str(path), str(repo))
    name, exe = js_runner(repo)
    if name and not exe:
        msg = "aib: package.json 用 %s,但 node_modules/.bin/%s 不存在:先安裝套件(npm install / pnpm install)" % (name, name)
        return ["node", "-e", "console.error(%s); process.exit(2)" % json.dumps(msg)]
    if name == "vitest":
        return [exe, "run", "--pool=forks", "--coverage.enabled=false", rel]
    if name == "jest":
        return [exe, "--runInBand", "--coverage=false", "--no-watchman", "--runTestsByPath", rel]
    if name == "mocha":
        return [exe, rel]
    return ["node", str(path)]     # 10-04 Node 接頭:node:test 直接執行就會跑;接頭由 NODE_OPTIONS 掛上


def _declared_command(repo: Path, name: str):
    """10-07:`.aib/ship.toml` [test] 宣告了 command,而且這支在宣告的 files 裡 ⇒ 照宣告的命令跑({file} 換成相對路徑)。
    命令的第一個字用 aib 自己的 PATH 找成絕對路徑(測試環境的 PATH 是固定白名單,rbenv/~/.cargo 這類找不到)。"""
    import shlex
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release"))
    try:
        import ship_profile
    except ImportError:          # 只帶 tools/verify 的安裝(治具、精簡版)⇒ 沒有設定檔這一層,照內建
        return None
    files, cmd = ship_profile.declared_tests(str(repo))
    if not cmd or (files is not None and name not in files):
        return None
    argv = shlex.split(cmd.replace("{file}", shlex.quote(name)))
    if argv and "/" not in argv[0]:
        argv[0] = shutil.which(argv[0]) or argv[0]
    return argv


_GO_ENV_CACHE = {}


def go_package_key(name: str):
    """10-08:Go 測試檔 ⇒ 它所在的套件(資料夾,相對倉根);不是 Go ⇒ None。go test 以套件為單位,同一套件只要跑一次。"""
    if not str(name).endswith("_test.go"):
        return None
    return os.path.dirname(str(name)) or "."


def _go_command(repo: Path, path: Path) -> list[str]:
    """10-07:Go 的測試以「套件」(資料夾)為單位 ⇒ `go test ./<這個檔的資料夾>`。
    借 Go 自己的測試快取(它記下每個套件讀了哪些檔、哪些環境變數;沒變就直接回上次的結果,不會錯):
    aib 給每支測試一個假 HOME,Go 的快取預設跟著 HOME 走 ⇒ 每次從頭編譯。所以把這台真正的 GOCACHE / GOMODCACHE
    (用 aib 自己的環境問 `go env`)明確交給它。go 不在測試環境的 PATH ⇒ 用絕對路徑。
    追蹤器看不到 go 讀了什麼 ⇒ aib 這邊照舊每次實跑(量不到),省下的是 Go 快取那一段。"""
    import shutil
    go = shutil.which("go") or "/usr/local/go/bin/go"
    rel = os.path.relpath(str(path.parent), str(repo))
    pkg = "./" + ("" if rel == "." else rel.replace(os.sep, "/") + "/")
    pkg = pkg.rstrip("/") or "."
    if go not in _GO_ENV_CACHE:
        vals = {}
        try:
            out = subprocess.run([go, "env", "GOCACHE", "GOMODCACHE"], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=60).stdout.split("\n")
            vals = {k: v.strip() for k, v in zip(("GOCACHE", "GOMODCACHE"), out) if v.strip()}
        except (OSError, subprocess.SubprocessError):
            pass
        _GO_ENV_CACHE[go] = vals
    pre = ["%s=%s" % kv for kv in sorted(_GO_ENV_CACHE[go].items())]
    return (["env"] + pre if pre else []) + [go, "test", pkg]


def _test_command(repo: Path, name: str, env: dict | None = None) -> list[str]:
    """10-04:沒有 main 守衛的 pytest 寫法直接執行 = 只定義函式就 rc 0 = 一格都沒驗的綠
    (tests/test-ctx-foot-line-0920.py 從 09-20 起就是這樣)。這類檔:有 pytest 用 pytest,沒有用 plain_test_runner。
    有守衛、或沒有任何測試函式/類別(腳本式測試)⇒ 照舊直接執行。
    10-04 晚:python 用 st.test_python(專案自己的虛擬環境優先;與環境指紋同一套規則),pytest 有沒有也問那一支。"""
    py = st.test_python(repo, env)
    path = repo / name
    declared = _declared_command(repo, name)
    if declared:
        return declared
    if path.suffix in JS_SUFFIXES:
        return _js_command(repo, path)
    if path.name.endswith("_test.go"):
        return _go_command(repo, path)
    if path.name.endswith("Test.php"):
        # 10-07 PHP:專案自己的 phpunit(composer 裝在 vendor/bin);php 不一定在測試環境的 PATH ⇒ 用 php 的絕對路徑起它
        import shutil
        exe = repo / "vendor" / "bin" / "phpunit"
        if not exe.exists():
            msg = "aib: 這支是 PHP 測試,但 vendor/bin/phpunit 不存在:先 composer install"
            return ["sh", "-c", "echo %s >&2; exit 2" % json.dumps(msg)]
        return [shutil.which("php") or "php", str(exe), os.path.relpath(str(path), str(repo))]
    if path.suffix in (".sh", ".bash"):
        return ["bash", str(path)]     # 10-04:bash 寫的測試(以前用 python 執行 ⇒ 語法錯誤紅);bash 接頭由 BASH_ENV 掛上
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [py, str(path)]
    if _MAIN_GUARD.search(text) or not _HAS_TESTS.search(text):
        return [py, str(path)]
    if st.python_id(py)[1]:
        return [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path)]
    return [py, str(Path(__file__).resolve().parent / "plain_test_runner.py"), str(path)]


MEMCAP_CELL = "MEMCAP:memory_cap"     # 被記憶體上限砍掉的固定紅格(不會出現在舊紅清單,收據一定算新紅)
TIMEOUT_CELL = "TIMEOUT:timeout"


class _NoCap:
    """memguard import 不到時的空殼(只有夾具會走到)。"""
    hit = None
    root_start = None

    def start(self):
        return self

    def stop(self):
        pass


def _run_one(repo: Path, name: str, env: dict[str, str], timeout: float | None = None) -> dict:
    """timeout=None ⇒ DEFAULT_TIMEOUT_S(舊行為逐字不變)。"""
    limit = DEFAULT_TIMEOUT_S if timeout is None else timeout
    started = time.monotonic()
    cut = None
    try:
        # 10-24(@044e0,20:34 整台卡死實案):每支測試含子孫,單一行程超過記憶體上限就整棵收、判紅並寫明 ——
        #   開發當下就知道「不能超大」,不是等機器凍住。上限 AIB_TEST_MEM_CAP_MB(預設 2048,0 = 關),見 memguard.TreeCap。
        with subprocess.Popen(_test_command(repo, name, env), cwd=str(repo), env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, errors="replace") as proc:
            cap = (memguard.TreeCap(proc.pid, memguard.test_cap_mb()) if memguard else _NoCap()).start()
            try:
                out, err = proc.communicate(timeout=limit)
            except subprocess.TimeoutExpired as e:
                # #322 r2 H1:不能再 communicate() —— 背景孫行程(tmux / http server / cmd &)拿著管線就永遠不回。
                #   照 subprocess.run 的做法 kill + wait、用逾時當下已讀到的輸出;能量到樹就整棵收(先收,根死了子孫就找不到)。
                if memguard:
                    memguard.kill_tree(proc.pid, cap.root_start)
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(proc.args, limit, output=e.stdout, stderr=e.stderr)
            finally:
                cap.stop()
        output = (out or "") + (err or "")
        rc = proc.returncode
        if cap.hit:
            output += "\n" + T("memory_cap", cap.hit) + "\n"
            rc, cut = 137, MEMCAP_CELL
    except OSError as e:
        output, rc = "%s: %s\n" % (type(e).__name__, e), 127
    except subprocess.TimeoutExpired as e:
        out = e.stdout or b""
        err = e.stderr or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        msg = T("timeout_30m") if limit == DEFAULT_TIMEOUT_S else T("timeout_after", {"seconds": limit})
        output, rc, cut = str(out) + str(err) + "\n" + msg + "\n", 124, TIMEOUT_CELL
    ran, cells = _parse_test_output(output)
    # #322 r2 M3:被上限 / 逾時砍掉的,固定加一格 —— 不然被砍前印的紅格若都在舊紅清單,收據照印「新紅 0」。
    if cut and cut not in cells:
        cells.append(cut)
    return {"name": name, "rc": rc, "ran": ran, "fail_cells": cells,
            "elapsed_s": round(time.monotonic() - started, 3), "output": output}


def _pool_file(repo: Path) -> Path:
    rid = reuse_index.repo_identity(str(repo)) or "unknown"
    return Path(st.shadow_dir_default()) / ("verify-%s.rows.jsonl" % rid)


def _append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 10-04:放鎖之前一定要整列寫進檔案(先 flush)。以前寫完就放鎖,緩衝剩下的關檔時才寫,
    # 多份同時跑就把一列切成兩半夾別人的列 ⇒ 隔離工具認不出是哪支 ⇒ 整池作廢(雲端實踩)。
    data = (jsonl_safe.dumps(row) + "\n").encode("utf-8")
    jsonl_safe.append_locked(path, data)   # 同一把鎖;池檔被合池換掉就重開(M1 二審)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def self_state_inputs(items) -> list[str]:
    """輸入項裡指到 aib 自己紀錄池(shadow_dir_default)的路徑。"""
    try:
        roots = {str(Path(st.shadow_dir_default())).rstrip("/"), os.path.realpath(str(st.shadow_dir_default())).rstrip("/")}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items or []:
        kind, _, rest = str(it).partition(":")
        if kind not in ("open", "missing", "listdir", "stat"):
            continue
        if any(rest == r or rest.startswith(r + "/") for r in roots if r):
            path = rest
            for r in roots:
                if r and rest.startswith(r + "/"):
                    path = rest[len(r) + 1:]
                    break
            out.append(path.split(":", 1)[0])
    return out


def _keylog_path(repo: Path, name: str):
    try:
        d = _pool_file(repo).parent / "i18nkeys"
        d.mkdir(parents=True, exist_ok=True)
        p = d / (hashlib.sha256(name.encode("utf-8")).hexdigest()[:16] + ".txt")
        p.write_text("", encoding="utf-8")          # 每次實跑重記
        return p
    except OSError:
        return None


# 10-12 a seat(a seat 派、the maintainer 17:1x 加令「翻譯重跑優化也要能讓其他 aib 用戶在自己的專案上套用」):
#   沒宣告 keyed_files ⇒ 自動認常見翻譯字典位置;aib 自己的倉 i18n/*.json 只是其中一例,不寫特例。
AUTO_KEYED_DIRS = ("locales", "i18n", "lang", "messages", "translations")
AUTO_KEYED_MAX_BYTES = 8 * 1024 * 1024     # 比這大的不當翻譯字典(解析成本;也不像)


_DICT_VERDICT = {}     # (路徑, mtime_ns, 大小) ⇒ 是不是翻譯字典:每支測試都會問一次,檔沒變就不重新解析(審查 10-12)


def is_translation_dict(path) -> bool:
    """「鍵 → 字串」的字典:最外層是非空物件,每個葉子都是字串(可以巢狀物件,像 i18next 的 {"menu": {"a": "A"}})。
    package.json、設定檔(數字、布林、陣列、null)⇒ 不是(§3:誤中不算)。讀不到 / 不是 JSON ⇒ 不是。"""
    try:
        st_ = os.stat(path)
    except OSError:
        return False
    key = (str(path), st_.st_mtime_ns, st_.st_size)
    if key not in _DICT_VERDICT:
        _DICT_VERDICT[key] = _is_translation_dict_uncached(path, st_.st_size)
    return _DICT_VERDICT[key]


def _is_translation_dict_uncached(path, size) -> bool:
    try:
        if size > AUTO_KEYED_MAX_BYTES:
            return False
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not data:
        return False
    stack, leaves = [data], 0
    while stack:
        d = stack.pop()
        for k, v in d.items():
            if not isinstance(k, str):
                return False
            if isinstance(v, dict):
                if not v:
                    return False
                stack.append(v)
            elif isinstance(v, str):
                leaves += 1
            else:
                return False
    return leaves > 0


def _auto_keyed_files(repo: Path):
    """常見翻譯資料夾底下(含一層子資料夾,如 locales/en/*.json)、內容是翻譯字典的 *.json ⇒ 相對倉根的路徑清單。"""
    root = Path(repo)
    out = set()
    for d in AUTO_KEYED_DIRS:
        base = root / d
        if not base.is_dir() or base.is_symlink():
            continue
        for f in list(base.glob("*.json")) + list(base.glob("*/*.json")):
            if f.is_file() and not f.is_symlink() and is_translation_dict(f):
                out.add(f.relative_to(root).as_posix())
    return sorted(out)


def _declared_keyed_files(repo: Path):
    """.aib/ship.toml 的 [test] keyed_files:沒有這個檔 / 沒寫這一項 ⇒ None(交給自動偵測);
    寫了(含 [] = 關掉)⇒ 字串清單,倉外樣式(.. 或絕對路徑)不收;寫壞 ⇒ [](不猜)。"""
    path = Path(repo) / ".aib" / "ship.toml"
    if not path.is_file():
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release"))
        import ship_profile
        test = ship_profile.parse_toml(path.read_text(encoding="utf-8")).get("test") or {}
    except Exception:  # noqa: BLE001
        return []
    if "keyed_files" not in test:
        return None
    val = test.get("keyed_files") or []
    if not isinstance(val, list):
        return []
    return [str(g) for g in val if isinstance(g, str) and g and not g.startswith("/") and ".." not in g]


def keyed_env(repo: Path, globs):
    """AIB_KEYED_FILES 的值。auto = 沒宣告、自動偵測來的 ⇒ 鉤子端只認 json.load(檔)(sitecustomize_audit,a seat 裁 B)。"""
    return json.dumps({"root": str(repo), "globs": list(globs), "auto": _declared_keyed_files(repo) is None})


def _keyed_files(repo: Path):
    """以鍵為單位的 JSON 字典(相對倉根的樣式)。.aib/ship.toml 宣告了就照宣告(蓋過自動、[] = 關掉);
    沒宣告 ⇒ 自動偵測常見翻譯位置。出錯 ⇒ [](照整檔重跑,安全側)。"""
    try:
        declared = _declared_keyed_files(repo)
        if declared is not None:
            return declared
        return _auto_keyed_files(repo)
    except Exception:  # noqa: BLE001
        return []


def _ps_env_pids(marker: str, ps_text: str | None = None, var: str = "EZ2_VERIFY_TRACE") -> list[int]:
    """沒有 /proc 的機器(macOS):`ps -E` 把每個行程的環境變數接在指令後面(只看得到同一個使用者的)。
    一行 = pid + 指令與環境,以空白分;有一段恰好是 EZ2_VERIFY_TRACE=<marker> 就算。追蹤檔路徑沒有空白(暫存根底下的隨機名)。"""
    want = "%s=%s" % (var, marker)
    if ps_text is None:
        try:
            ps_text = subprocess.run(["ps", "-E", "-ww", "-o", "pid=,command="], capture_output=True, text=True,
                                     errors="replace", stdin=subprocess.DEVNULL, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    out, me = [], os.getpid()
    for line in ps_text.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit() and int(parts[0]) != me and want in parts[1:]:
            out.append(int(parts[0]))
    return sorted(out)


def _lsof_dir_pids(dirs, lsof_text: str | None = None) -> list[int]:
    """開著 dirs 底下的檔(含工作目錄)的行程(不含自己)。10-11:macOS 27 起 `ps -E` 與 sysctl procargs2 都不給
    別的行程的環境變數(實測 0 筆)⇒ 照環境認不出殘留;改認「還抓著這支測試專用資料夾裡東西的」。
    dirs 是每支測試隨機建的暫存資料夾 / HOME,碰不到別支、別人的。lsof 跑不了 ⇒ []。"""
    roots = set()
    for d in dirs or ():
        if d:
            roots.update({str(d).rstrip("/"), os.path.realpath(str(d)).rstrip("/")})
    if not roots:
        return []
    if lsof_text is None:
        lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.access(p, os.X_OK)), None)
        if not lsof:
            return []
        try:
            lsof_text = subprocess.run([lsof, "-n", "-P", "-w", "-F", "pn", "-u", str(os.getuid())], capture_output=True,
                                       text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    out, pid, me = set(), None, os.getpid()
    for line in lsof_text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None and pid != me:
            name = line[1:]
            if any(name == r or name.startswith(r + "/") for r in roots):
                out.add(pid)
    return sorted(out)


def leftover_pids(marker: str, proc: str = "/proc", ps_text: str | None = None, var: str = "EZ2_VERIFY_TRACE",
                  dirs=(), lsof_text: str | None = None) -> list[int]:
    """環境變數裡帶著 marker(這支測試的追蹤檔路徑)、而且還活著的行程(不含自己)。
    Linux 讀 /proc;沒有 /proc(macOS,10-08 CI 改到 Mac mini)⇒ 改讀 `ps -E`,
    再加上抓著 dirs 底下東西的(10-11:macOS 27 的 ps -E 不給環境變數)。"""
    if not os.path.isdir(proc):
        return sorted(set(_ps_env_pids(marker, ps_text, var)) | set(_lsof_dir_pids(dirs, lsof_text)))
    want = ("%s=%s" % (var, marker)).encode("utf-8")
    out = []
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    me = os.getpid()
    for n in names:
        if not n.isdigit() or int(n) == me:
            continue
        try:
            with open(os.path.join(proc, n, "environ"), "rb") as fh:
                env = fh.read()
        except OSError:
            continue
        if want in env.split(b"\0"):
            out.append(int(n))
    return sorted(out)


def available_gb():
    """可用記憶體(GB);讀不到(非 Linux)⇒ None。"""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0 / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


MEM_WAIT_GB = 1.0          # 10-08:可用記憶體低於這個 ⇒ 先不接新測試(兩次 OOM 把推前檢查整份砍掉的第二道防線)
MEM_WAIT_MAX_S = 600.0     # 最多等這麼久;等不到照跑(不卡死),系統真的不夠時讓它照實失敗


def wait_for_memory(on_wait=None, min_gb=MEM_WAIT_GB, max_wait_s=MEM_WAIT_MAX_S, avail=available_gb, sleep=time.sleep):
    """跑下一支測試之前:可用記憶體夠就馬上回;不夠就等(每 5 秒看一次),第一次等時呼叫 on_wait(可用 GB)。回等了幾秒。"""
    waited = 0.0
    told = False
    while True:
        a = avail()
        if a is None or a >= min_gb or waited >= max_wait_s:
            return waited
        if not told and on_wait is not None:
            on_wait(a)
            told = True
        sleep(5.0)
        waited += 5.0


REAPED = []          # 10-08:這個行程到目前收掉的殘留 pid(aib test 用前後差算每支測試留了幾個,總表列出)


def reap_leftovers(marker: str, grace_s: float = 2.0, proc: str = "/proc", var: str = "EZ2_VERIFY_TRACE",
                   dirs=()) -> list[int]:
    """測試結束後還活著、帶著它追蹤檔路徑的行程 = 它開出來沒收的(例:tmux 伺服器)⇒ 先 SIGTERM,等 grace_s 再 SIGKILL。
    10-08 a CI machine 實踩:兩組這種殘留跑了 8.5 與 5 小時,一直往已刪掉的追蹤檔寫 ⇒ /tmp(放在記憶體裡)被吃掉 11G + 6.7G。
    回收掉的 pid。只碰帶著這一支追蹤檔路徑的行程,別支、別人的都不碰。Linux 讀 /proc、macOS 讀 ps -E。"""
    import signal
    pids = leftover_pids(marker, proc, var=var, dirs=dirs)
    REAPED.extend(pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + grace_s
    while pids and time.monotonic() < deadline and leftover_pids(marker, proc, var=var, dirs=dirs):
        time.sleep(0.1)
    for pid in leftover_pids(marker, proc, var=var, dirs=dirs):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return pids


NET_TARGETS_MAX = 20     # 10-12 P17:紀錄池每列最多記幾個網路目標


def _repo_roots(repo: Path, snapshot: Path) -> list[str]:
    """被測的倉本身(工作樹、快照、git 共用目錄;各取真路徑和原路徑):這些底下的讀寫不算碰真家目錄。"""
    out = []
    for p in (repo, snapshot):
        out += [str(p), os.path.realpath(str(p))]
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).stdout.strip()
        if common:
            out += [common, os.path.realpath(common)]
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(set(out))


def _traced_run(repo: Path, snapshot: Path, name: str, env: dict[str, str], trace_dir: Path,
                row_source: str = ROW_SOURCE, timeout: float | None = None) -> dict:
    """在追蹤下跑一支,跑完把量到的輸入寫進沿用池。量不到照實寫 measured="unmeasured"(下次必跑)。
    指紋的環境 = 實際給子行程的那一份(child_env 疊在乾淨環境上);路徑正規化用正本倉(追蹤器與墊片住在那裡)。"""
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = trace_dir / ("trace-%s.jsonl" % hashlib.sha256(name.encode("utf-8")).hexdigest()[:16])
    if trace.exists():
        trace.unlink()
    tenv = st.child_env(snapshot, trace, env)
    keylog = _keylog_path(repo, name)                 # 10-06 §15.3:順便記這支用到哪些翻譯鍵(只量,不影響沿用)
    if keylog is not None:
        tenv["AIB_I18N_KEYLOG"] = str(keylog)
    keyed = _keyed_files(snapshot)                    # §15.3c 第 2 項:.aib/ship.toml 宣告的 JSON 字典;10-12 起沒宣告就自動偵測
    if keyed:
        tenv["AIB_KEYED_FILES"] = keyed_env(snapshot, keyed)
    actual = _run_one(snapshot, name, tenv, timeout=timeout)
    reap_leftovers(str(trace))                        # 10-08:測試開出來沒收的背景行程(tmux 伺服器等)一併收掉
    try:
        events = st.load_trace(trace)
        touched = [] if REAL_HOME_OK_MARK in _head(snapshot / name) else st.real_home_touches(events, real_home(), _repo_roots(repo, snapshot))
        if touched:                                   # 第 37.1 節:碰到真家目錄 ⇒ 這支判紅,說是哪個檔(只列前 5 個)
            actual["rc"] = actual["rc"] or 1
            actual["real_home"] = touched[:5]
            actual["output"] = (actual.get("output") or "") + "\n" + T("real_home_touched", {
                "files": ", ".join("%s %s" % t for t in touched[:5]), "n": len(touched)})
        items, input_fp, unmeasured, external, why = st.inputs_from_events(events, repo=str(snapshot))
        own = self_state_inputs(items)
        if own:
            # 10-07 防線(翻譯鍵紀錄檔實踩):輸入指到 aib 自己的紀錄池 ⇒ 那多半是量測自己寫的檔、每次都會變,
            #   默默記成輸入只會讓這支每次都「讀的檔變了」。照實標成量不到(每次重跑,往安全側),原因說得出是哪個檔。
            unmeasured = True
            why = list(why) + ["self-state-input:%s" % own[0]]
        if tenv.get("AIB_LDP_TRACE"):
            # 10-07 安全網:C 層讀了倉內的檔、Python 鉤子卻沒記 ⇒ 量不到(只會多跑,不會錯沿用)
            miss = st.native_unrecorded(events, tenv["AIB_LDP_TRACE"], str(snapshot))
            if miss:
                unmeasured, why = True, list(why) + ["native-read-unrecorded:%s" % m for m in miss[:3]]
        _ldp = tenv.get("AIB_LDP_TRACE")
        if _ldp and not os.path.exists(_ldp):
            # 10-08 雲端實踩:安全網程式庫被清掉(或載入失敗)⇒ 一行紀錄都沒有,native_unrecorded 回空 ⇒ 看起來量得到。
            #   要了安全網卻沒有它的紀錄 = 沒檢查,不是通過 ⇒ 量不到(每次重跑,往安全側)
            unmeasured, why = True, list(why) + ["native-guard-no-trace"]
        env_fp, _raw = st.env_fingerprint(tenv, repo=str(repo))
        import env_parts_store as eps
        parts_file = eps.env_parts_path(env_fp)
        if parts_file is not None and not parts_file.exists():    # 10-06:每個指紋存一次逐項原料(說得出哪項變了)
            eps.save_env_parts(env_fp, st.env_parts(tenv, repo=str(repo)))
        row = {
            "test": name, "rc": actual["rc"], "timed_out": actual["rc"] == 124,
            "measured": True if not unmeasured else "unmeasured", "external": bool(external), "why": why,
            "inputs": items, "input_fp": input_fp, "env_fp": env_fp, "key": st.make_key(name, input_fp, env_fp),
            "duration_s": actual["elapsed_s"], "ran": actual["ran"], "fail_cells": actual["fail_cells"],
            # 09-29 a seat 要:沿用時附上次實跑的輸出尾巴(與 seat_test_reuse.GREEN_TAIL_CHARS 同長)
            "tail": (actual.get("output") or "")[-TAIL_CHARS:],
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime()), "source": row_source,
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        net = st.network_targets(events)
        if net:                       # 10-12 P17:連了哪些網路目標(只多記資料;沿用判斷看 external/why,不看這欄)
            row["net"] = net[:NET_TARGETS_MAX]       # 上限:一支測試連很多埠時這一列不會無限長(會推到共用池 —— 審查)
            if len(net) > NET_TARGETS_MAX:
                row["net_more"] = len(net) - NET_TARGETS_MAX
        _append_row(_pool_file(repo), reuse_index.stamp_row(row, str(repo)))   # M1 合池是另一支一次性指令,不在寫列路上
        actual["recorded"] = True
    except Exception as e:  # 量不到只是下次必跑;不准讓驗證本身失敗
        actual["recorded"] = False
        actual["record_error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        for _f in (trace, Path(str(trace) + ".ldp"), Path(str(trace) + ".t0")):   # .t0:起跑標記(10-09 補,以前每支留一個)
            try:
                _f.unlink()
            except OSError:
                pass
    return actual


def _acquire(owner: str, run_id: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "governance" / "host_lease.py"),
                        "acquire", "--owner", owner, "--what", "aib verify",
                        "--run-id", run_id, "--ttl", str(LEASE_TTL), "--pid", str(os.getpid())],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def _lease_action(action: str, run_id: str) -> tuple[int, str]:
    argv = [sys.executable, str(ROOT / "tools" / "governance" / "host_lease.py"),
            action, "--run-id", run_id]
    if action == "renew":
        argv.extend(["--ttl", str(LEASE_TTL)])
    p = subprocess.run(argv,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def _make_plan(repo: Path, base: str, mode: str, sample_pct: float):
    universe = select_changed.repo_tests(str(repo))
    changed = select_changed.changed_from_git(str(repo), base, "") if base else []
    if mode == "strict":
        rows = [{"test": name, "verdict": "must-run", "reason": "strict-mode",
                 "detail": "", "why": [], "sampled": False} for name in universe]
        return changed, universe, None, {
            "rows": rows, "must_run": list(universe), "reuse": [], "sample": [],
            "to_execute": list(universe), "counts": {"selected": len(universe),
            "must_run": len(universe), "reuse": 0, "sample": 0},
        }, None
    selected_result = None
    fallback_reason = None
    if mode == "quick":
        try:
            selected_result = select_changed.select(str(repo), changed, rows=[])
            reverse = selected_result.get("reverse") or {}
            if reverse.get("degraded") not in (None, "", "not-used"):
                raise select_changed.Unmeasurable(T("reverse_unavailable", {"reason": reverse["degraded"]}))
            idx = reuse_index.build_index([], repo=str(repo))
            if idx.degraded:
                raise select_changed.Unmeasurable(T("reuse_index_unavailable", {"reason": idx.degraded}))
            decisions = {}
            for row in selected_result["selected"]:
                name = row["test"]
                decisions[name] = idx.decide(str(repo), name, changed=changed,
                                             environ=clean_test_env(PROBE_HOME, repo))
            planned = planner.plan_changed(
                selected_result["selected"], decisions, changed=changed,
                pct=sample_pct, tree_sha=selected_result.get("tree_sha", ""))
            # 10-03 審查第 5 條:收尾時另建一份索引再找「被沿用的那一列」,等租約這段時間池子可能變了(作廢、壓縮、停用)
            #   ⇒ 判斷當下用的那一列直接放進計畫;收尾只用它
            for prow in planned.get("rows") or []:
                if prow.get("verdict") == "reuse":
                    used = idx.row_for(prow["test"], prow.get("detail", "")) or {}
                    if str(used.get("key") or "").startswith(prow.get("detail", "") or "\0"):
                        prow["reused_row"] = {k: used.get(k) for k in ("rc", "ran", "fail_cells", "measured", "key")}
            return changed, universe, selected_result, planned, fallback_reason
        except (select_changed.Unmeasurable, OSError, RuntimeError, ValueError) as e:
            fallback_reason = "%s: %s" % (type(e).__name__, e)
    rows = [{"test": name, "verdict": "must-run", "reason": "strict-mode" if mode == "strict" else "selection-unmeasurable",
             "detail": fallback_reason or "", "why": [], "sampled": False} for name in universe]
    return changed, universe, selected_result, {
        "rows": rows, "must_run": list(universe), "reuse": [], "sample": [],
        "to_execute": list(universe), "counts": {"selected": len(universe), "must_run": len(universe),
        "reuse": 0, "sample": 0},
    }, fallback_reason


def run(repo: str | Path, *, base: str = "origin/main", mode: str | None = None,
        sample_pct: float = planner.SAMPLE_PCT_DEFAULT, known_red_path: str | None = None,
        owner: str | None = None) -> tuple[int, dict | None, Path | None]:
    repo = Path(repo).resolve()
    current, _source, _config = resume_policy.current_mode(str(repo))
    mode = mode or current
    if mode not in ("quick", "strict", "off"):
        raise ValueError(T("mode_invalid"))
    if mode == "off":
        print(T("off_nothing_run"))
        return 0, None, None

    _require_clean_snapshot(repo)
    started = time.monotonic()
    changed, universe, selected, plan_out, fallback_reason = _make_plan(repo, base, mode, sample_pct)
    if mode == "strict":
        plan_out = {"rows": [{"test": n, "verdict": "must-run", "reason": "strict-mode",
                              "detail": "", "why": [], "sampled": False} for n in universe],
                    "to_execute": list(universe), "reuse": [], "sample": [],
                    "counts": {"selected": len(universe), "must_run": len(universe), "reuse": 0, "sample": 0}}
    if not base:
        base_sha = _git(repo, "rev-parse", "HEAD")
    else:
        base_sha = _git(repo, "merge-base", base, "HEAD")

    owner = owner or os.environ.get("EZ2_AIB_SEAT") or os.environ.get("AIB_SEAT") or "5e128"
    run_id = "verify-%s-%s" % (owner, secrets.token_hex(5))
    lease_started = time.monotonic()
    lease_rc, lease_msg = _acquire(owner, run_id)
    queue_s = time.monotonic() - lease_started
    if lease_rc != 0:
        print(T("lease_unavailable", {"msg": lease_msg}), file=sys.stderr)
        return lease_rc, None, None

    snapshot_parent: Path | None = None
    snapshot_repo: Path | None = None
    records: dict[str, dict] = {}
    previous_index = reuse_index.build_index([], repo=str(repo))
    planned_by_name = {r["test"]: r for r in plan_out["rows"]}
    try:
        snapshot_parent, snapshot_repo = _create_snapshot(repo, run_id)
        for name in plan_out.get("to_execute", []):
            row = planned_by_name.get(name, {})
            sampled = bool(row.get("sampled"))
            env_home = Path(TMP_ROOT) / run_id
            env_home.mkdir(parents=True, exist_ok=True)
            actual = _traced_run(repo, snapshot_repo, name,
                                 isolated_test_env(env_home, snapshot_repo),
                                 Path(TMP_ROOT) / (run_id + "-trace"))
            # 抽查要比的是**被沿用的那一列**(多台/多樹共用池時不一定是最新一列;reuse_index.row_for)
            previous = row.get("reused_row") if row.get("verdict") == "reuse" else previous_index.row_for(name, "")
            sample_match = (actual["rc"] == previous.get("rc")) if sampled and previous else (True if sampled else None)
            rerun_rc = None
            if actual["rc"] != 0:
                if not actual["fail_cells"]:
                    actual["fail_cells"] = [name]
                print(T("red_rerun", {"test": name, "rc": actual["rc"]}), file=sys.stderr, flush=True)
                rerun_home = Path(TMP_ROOT) / (run_id + "-rerun")
                rerun_home.mkdir(parents=True, exist_ok=True)
                rerun = _run_one(snapshot_repo, name,
                                 isolated_test_env(rerun_home, snapshot_repo))
                rerun_rc = rerun["rc"]
                if actual["output"]:
                    sys.stderr.write(actual["output"])
                    if not actual["output"].endswith("\n"):
                        sys.stderr.write("\n")
                if rerun["output"]:
                    sys.stderr.write("--- " + T("rerun_banner") + " ---\n" + rerun["output"])
                sys.stderr.flush()
            records[name] = {
                "name": name, "rc": actual["rc"], "ran": actual["ran"],
                "fail_cells": actual["fail_cells"], "elapsed_s": actual["elapsed_s"],
                "reused": False, "sampled": sampled, "sample_match": sample_match,
                "rerun_rc": rerun_rc, "measurable": actual["ran"] is not None,
                "recorded": bool(actual.get("recorded")),
            }
            if sample_match is False:
                fallback_reason = T("sample_mismatch")
                # Strict fallback refreshes every result and makes this receipt report zero saved time.
                plan_out["sample_mismatch"] = True
                for remaining in universe:
                    if remaining in records:
                        continue
                    strict_home = Path(TMP_ROOT) / (run_id + "-strict")
                    strict_home.mkdir(parents=True, exist_ok=True)
                    full = _traced_run(repo, snapshot_repo, remaining,
                                       isolated_test_env(strict_home, snapshot_repo),
                                       Path(TMP_ROOT) / (run_id + "-trace"))
                    red_rerun = None
                    if full["rc"] != 0:
                        if not full["fail_cells"]:
                            full["fail_cells"] = [remaining]
                        print(T("strict_red_rerun", {"test": remaining, "rc": full["rc"]}), file=sys.stderr, flush=True)
                        strict_rerun_home = Path(TMP_ROOT) / (run_id + "-strict-rerun")
                        strict_rerun_home.mkdir(parents=True, exist_ok=True)
                        red_rerun = _run_one(snapshot_repo, remaining,
                                             isolated_test_env(strict_rerun_home, snapshot_repo))
                    records[remaining] = {"name": remaining, "rc": full["rc"], "ran": full["ran"],
                        "fail_cells": full["fail_cells"], "elapsed_s": full["elapsed_s"],
                        "reused": False, "sampled": False, "sample_match": None,
                        "rerun_rc": red_rerun["rc"] if red_rerun else None,
                        "measurable": full["ran"] is not None, "recorded": bool(full.get("recorded"))}
                break
            if len(records) % 30 == 0:
                _lease_action("renew", run_id)

        # Reused outcomes are copied from the current per-test index; missing or red rows were never reused by P2.
        for item in plan_out["rows"]:
            name = item["test"]
            if name in records:
                continue
            prev = item.get("reused_row") or {}
            # 找不到判斷當下用的那一列 ⇒ 不准當綠(以前 rc 預設 0)
            rc_prev = prev.get("rc") if isinstance(prev.get("rc"), int) and not isinstance(prev.get("rc"), bool) else 1
            records[name] = {"name": name, "rc": rc_prev, "ran": prev.get("ran"),
                             "fail_cells": prev.get("fail_cells", []), "elapsed_s": 0,
                             "reused": True, "sampled": False, "sample_match": None,
                             "rerun_rc": None, "measurable": prev.get("measured") is True}

        elapsed = max(0.0, time.monotonic() - started - queue_s)
        actual_mode = "strict" if plan_out.get("sample_mismatch") or mode == "strict" or fallback_reason else "quick"
        rec = receipt.build_receipt(
            list(records.values()), repo=str(repo), mode=actual_mode, elapsed_s=elapsed,
            serial_s=elapsed, queue_s=queue_s, base=base_sha, universe=universe,
            known_red=_known_reds(known_red_path),
            reverse_index_added=len((selected or {}).get("reverse", {}).get("added", [])),
            changed_files=len(changed), home=None)
        receipt_path = receipt.record(rec, str(repo))
    finally:
        try:
            if snapshot_parent is not None and snapshot_repo is not None:
                _remove_snapshot(repo, snapshot_parent, snapshot_repo)
        finally:
            shutil.rmtree(Path(TMP_ROOT) / (run_id + "-trace"), ignore_errors=True)
            _lease_action("release", run_id)
    # The receipt owns the exact required savings sentence; it is always the first stdout line.
    lines = receipt.human_lines(rec)
    print(lines[0])
    print(lines[1])
    print(T("receipt_path", {"path": receipt_path}))
    ran_here = [r for r in records.values() if not r.get("reused")]
    kept = sum(1 for r in ran_here if r.get("recorded"))
    # 設計稿 aib test 第三節:宣稱開著卻沒留紀錄 ⇒ 失敗,不是通過(沿用池不長,下一輪就省不到)
    print(T("shadow_recorded", {"ran": len(ran_here), "kept": kept,
                                "missing": "" if kept == len(ran_here) else T("shadow_missing", {"n": len(ran_here) - kept})}))
    print(T("plan_summary", {"selected": rec["selected"], "universe": len(universe), "reused": rec["reused"],
                             "ran": rec["ran"], "sampled": rec["sampled"], "new_red": len(rec["new_red"]),
                             "elapsed": "%.1f" % elapsed, "tail": ("; " + fallback_reason) if fallback_reason else ""}))
    return (1 if rec["new_red"] else 0), rec, receipt_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aib verify --changed")
    ap.add_argument("--changed", action="store_true", help=T("help_changed"))
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--owner", default="")
    ap.add_argument("--sample-pct", type=float, default=planner.SAMPLE_PCT_DEFAULT)
    ap.add_argument("--known-red", default="")
    args = ap.parse_args(argv)
    if not args.changed:
        ap.error(T("only_changed"))
    try:
        rc, _rec, _path = run(args.repo, base=args.base,
                              mode="strict" if args.strict else None,
                              sample_pct=args.sample_pct,
                              known_red_path=args.known_red or None,
                              owner=args.owner or None)
        return rc
    except (OSError, RuntimeError, ValueError) as e:
        print(T("not_completed", {"kind": type(e).__name__, "err": e}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
