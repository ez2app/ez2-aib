#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib ship 的專案設定:版號在哪些檔、哪些檔只有發版能碰、推前檢查用哪種模式(10-06,開發檔 §4)。

三種來源,先找到的算:
  1. 專案裡的 `.aib/ship.toml`(人可改;改了要推一包)
  2. aib 自己的倉(有 src/cockpit/aib 與 tools/release/ship.py)⇒ AIB(等同 10-05 寫死的規則)
  3. 自動偵測(detect):VERSION → pyproject.toml → package.json → setup.cfg → 唯一一個 __version__
     pyproject 寫 dynamic(flit 之類)⇒ 照樣往下找 __version__;都找不到 ⇒ version_files 空:發版停用、推一包照常(只查 release_only)

版號規格字串:`路徑` = 整檔就是版號(也一併列為發版專用檔);`路徑:鍵` = 檔裡某個值(一包可以改這檔的其他內容,
不准改這個值)。鍵的讀法依副檔名:.json/.toml 用點分路徑;.cfg/.ini 用「段.鍵」;.py 用 `名字 = "..."`。
"""
from __future__ import annotations

import configparser
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CONFIG = ".aib/ship.toml"
SKIP_DIRS = {".git", ".hg", ".venv", "venv", "env", ".env", "node_modules", "build", "dist", ".tox", ".nox",
             "__pycache__", "site-packages", ".mypy_cache", ".pytest_cache", ".eggs"}


@dataclass
class Profile:
    name: str                                   # "aib" | "generic"
    version_files: list = field(default_factory=list)   # 第一個 = 主版號(它變了 ⇒ 這顆是發版)
    release_only: list = field(default_factory=list)    # 一包碰到 ⇒ 違規
    release_extra: list = field(default_factory=list)   # 發版提交另外能碰的檔
    pinned_files: list = field(default_factory=list)    # 上一版之後改過 ⇒ 拒發(要人重新確認)
    changelog: str | None = None
    policy_start_file: str = CONFIG               # 規則從加進這個檔的那一顆起算
    gate_mode: str = "generic"                    # generic | aib
    jobs: int = 0                                 # 0 ⇒ CPU 核心數 - 1(最少 1)
    always_run: list = field(default_factory=list)
    source: str = "detect"                        # config | aib | detect

    def paths(self, specs):
        return [s.split(":", 1)[0] for s in specs]

    def release_allowed(self):
        return set(self.release_only) | set(self.paths(self.version_files)) | set(self.release_extra)

    def gate_jobs(self):
        return self.jobs if self.jobs > 0 else max(1, (os.cpu_count() or 2) - 1)


AIB = Profile(
    name="aib",
    version_files=["VERSION", "src/dataguard/pins.py:PINNED_AT_VERSION", "deploy/line/manifest-v1.json:product.version"],
    release_only=["VERSION", "CHANGELOG.md", "docs/reports/RELEASE-TAG-BASELINE.md"],
    release_extra=["README.md", "docs/EZ2-AIB-DEV-OUTLINE.md"],
    pinned_files=["themes/aib-default.json", "src/messaging/notify-rules.json"],
    changelog="CHANGELOG.md",
    policy_start_file="tools/release/ship.py",
    gate_mode="aib",
    source="aib",
)


# ---- 讀 toml(3.11 起用 tomllib;更舊的 python(Mac 內建 3.9)用下面這個只認簡單寫法的小讀法)------------------
def _toml_scalar(v):
    v = v.strip()
    if v.startswith(("'", '"')) and len(v) >= 2 and v[-1] == v[0]:
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_toml_scalar(x) for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^,\s][^,]*', inner)] if inner else []
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    raise ValueError(v)


def _mini_toml(text):
    out, cur = {}, None
    cur = out
    for raw in text.splitlines():
        ln = raw.split(" #", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not ln:
            continue
        m = re.fullmatch(r"\[([A-Za-z0-9_.\-]+)\]", ln)
        if m:
            cur = out
            for k in m.group(1).split("."):
                cur = cur.setdefault(k, {})
            continue
        m = re.fullmatch(r'([A-Za-z0-9_\-]+|"[^"]+")\s*=\s*(.+)', ln)
        if not m:
            continue
        try:
            cur[m.group(1).strip('"')] = _toml_scalar(m.group(2))
        except ValueError:
            continue                                  # 認不得的寫法(多行陣列、行內表)⇒ 略過,不猜
    return out


def parse_toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:
        return _mini_toml(text)


def _dotted(d, key):
    for k in key.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d if isinstance(d, str) else None


def read_value(path, key, text):
    """從檔案內容讀出版號值;讀不到 ⇒ None。"""
    if text is None:
        return None
    if not key:
        return text.strip()
    try:
        if path.endswith(".json"):
            return _dotted(json.loads(text), key)
        if path.endswith(".toml"):
            return _dotted(parse_toml(text), key)
        if path.endswith((".cfg", ".ini")):
            cp = configparser.ConfigParser(interpolation=None)
            cp.read_string(text)
            sec, k = key.split(".", 1)
            return cp.get(sec, k, fallback=None)
    except (ValueError, configparser.Error, Exception):  # noqa: BLE001 - 壞檔 ⇒ 讀不到
        return None
    m = re.search(r'^%s\s*=\s*["\']([^"\']*)["\']' % re.escape(key), text, re.M)
    return m.group(1) if m else None


def split_spec(spec):
    path, _, key = spec.partition(":")
    return path, key or None


# ---- 偵測 ----------------------------------------------------------------------------------------------
def _read(repo, rel):
    try:
        return (Path(repo) / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _walk_py(repo, limit=4000):
    n = 0
    for dp, dns, fns in os.walk(repo):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith(".") and d not in ("tests", "test", "docs"))
        for f in sorted(fns):
            if f in ("__init__.py", "__about__.py", "_version.py", "version.py"):
                yield os.path.relpath(os.path.join(dp, f), repo)
            n += 1
            if n > limit:
                return


def detect_version_files(repo):
    """回 (版號規格清單, 說明)。找不到 ⇒ ([], 原因)。"""
    if _read(repo, "VERSION") is not None and (_read(repo, "VERSION") or "").strip():
        return ["VERSION"], "VERSION"
    py = _read(repo, "pyproject.toml")
    if py is not None:
        for key in ("project.version", "tool.poetry.version"):
            if read_value("pyproject.toml", key, py):
                return ["pyproject.toml:" + key], "pyproject.toml " + key
    pj = _read(repo, "package.json")
    if pj is not None and read_value("package.json", "version", pj):
        return ["package.json:version"], "package.json version"
    sc = _read(repo, "setup.cfg")
    if sc is not None and read_value("setup.cfg", "metadata.version", sc):
        if not (read_value("setup.cfg", "metadata.version", sc) or "").startswith(("attr:", "file:")):
            return ["setup.cfg:metadata.version"], "setup.cfg [metadata] version"
    hits = [rel for rel in _walk_py(repo) if read_value(rel, "__version__", _read(repo, rel))]
    if len(hits) == 1:
        return [hits[0] + ":__version__"], hits[0] + " __version__"
    if len(hits) > 1:
        return [], "many-__version__:" + ",".join(hits[:5])
    # pyproject 寫 dynamic、又沒有 __version__ 字面值(setuptools-scm 之類從 git tag 算)⇒ 標出來,不猜
    if py is not None and re.search(r'^\s*dynamic\s*=.*"version"', py, re.M):
        return [], "dynamic-version"
    return [], "none"


def detect(repo):
    vf, why = detect_version_files(repo)
    ro = []
    if vf and ":" not in vf[0]:
        ro.append(vf[0])
    cl = "CHANGELOG.md" if (Path(repo) / "CHANGELOG.md").exists() else None
    if cl:
        ro.append(cl)
    p = Profile(name="generic", version_files=vf, release_only=ro, changelog=cl, source="detect")
    p.detected = why
    return p


def is_aib_repo(repo):
    r = Path(repo)
    return (r / "src" / "cockpit" / "aib").is_file() and (r / "tools" / "release" / "ship.py").is_file()


def from_config(text):
    d = parse_toml(text)
    rel, push, gate = d.get("release", {}), d.get("push", {}), d.get("gate", {})
    mode = gate.get("mode", "generic")
    base = AIB if mode == "aib" else Profile(name="generic")
    p = Profile(
        name=mode,
        version_files=list(rel.get("version_files", base.version_files)),
        release_only=list(rel.get("release_only", base.release_only)),
        release_extra=list(rel.get("release_extra", base.release_extra)),
        pinned_files=list(rel.get("pinned_files", base.pinned_files)),
        changelog=rel.get("changelog", base.changelog),
        policy_start_file=base.policy_start_file if mode == "aib" else CONFIG,
        gate_mode=mode,
        jobs=int(gate.get("jobs", 0) or 0),
        always_run=list(gate.get("always_run", [])),
        source="config",
    )
    for spec in p.version_files:                  # 整檔版號一定是發版專用檔
        if ":" not in spec and spec not in p.release_only:
            p.release_only.append(spec)
    p.also_branches = list(push.get("also_branches", []))
    return p


def load(repo):
    text = _read(repo, CONFIG)
    if text is not None:
        return from_config(text)
    if is_aib_repo(repo):
        return AIB
    return detect(repo)


def render(p):
    """把偵測結果寫成 .aib/ship.toml(第一次 aib ship package 時加進那一包)。"""
    q = lambda xs: "[" + ", ".join(json.dumps(x) for x in xs) + "]"  # noqa: E731
    return "\n".join([
        "# aib ship: detected settings; edit and push a package to change them",
        "[release]",
        "version_files = %s" % q(p.version_files),
        "release_only = %s" % q(p.release_only),
        "changelog = %s" % json.dumps(p.changelog or ""),
        "[push]",
        "also_branches = []",
        "[gate]",
        'mode = "%s"' % p.gate_mode,
        "jobs = %d" % p.jobs,
        "always_run = %s" % q(p.always_run),
        "",
    ])


JS_TEST_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx")
_JS_TEST_RE = re.compile(r"\.(test|spec)\.[mc]?[jt]sx?$")


def pytest_testpaths(repo):
    """10-07(Flask 實踩):pytest 自己設定的 testpaths(只跑這些路徑底下的測試)。照 pytest 找設定檔的順序:
    pytest.ini > pyproject.toml([tool.pytest.ini_options])> tox.ini([pytest])> setup.cfg([tool:pytest]);
    第一個有那一節的檔說了算。沒設 ⇒ None(整個倉都找)。"""
    for name, section in (("pytest.ini", "pytest"), ("pyproject.toml", None), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        text = _read(repo, name)
        if text is None:
            continue
        if section is None:
            try:
                opts = ((parse_toml(text).get("tool") or {}).get("pytest") or {}).get("ini_options")
            except Exception:  # noqa: BLE001 - 壞的 pyproject ⇒ 當沒設
                opts = None
            if not isinstance(opts, dict):
                continue
            tp = opts.get("testpaths")
            paths = [tp] if isinstance(tp, str) else [str(x) for x in (tp or [])]
        else:
            cp = configparser.ConfigParser()
            try:
                cp.read_string(text)
            except configparser.Error:
                continue
            if not cp.has_section(section):
                continue
            paths = cp.get(section, "testpaths", fallback="").split()
        paths = [os.path.normpath(x).replace(os.sep, "/").strip("/") for x in paths if x.strip()]
        return paths or None
    return None


def _uses_phpunit(repo):
    try:
        data = json.loads(_read(repo, "composer.json") or "")
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return any(isinstance(data.get(k), dict) and "phpunit/phpunit" in data[k] for k in ("require", "require-dev"))


def _js_framework(repo):
    try:
        data = json.loads(_read(repo, "package.json") or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    deps = {}
    for k in ("dependencies", "devDependencies"):
        if isinstance(data.get(k), dict):
            deps.update(data[k])
    return next((n for n in ("vitest", "jest", "mocha") if n in deps), "node")


def declared_tests(repo):
    """10-07(任何語言的通用第一層):`.aib/ship.toml` 的 [test] files(樣式清單,可用 **)與 command(`{file}` 換成那支的相對路徑)。
    回 (相對路徑清單或 None, 命令或 None);沒宣告 files ⇒ (None, …)(照內建找法)。"""
    text = _read(repo, CONFIG)
    if text is None:
        return None, None
    try:
        t = parse_toml(text).get("test") or {}
    except Exception:  # noqa: BLE001 - 壞的設定檔 ⇒ 當沒宣告(推包時 load() 會照實報錯)
        return None, None
    files = t.get("files")
    cmd = t.get("command") if isinstance(t.get("command"), str) and t.get("command").strip() else None
    if isinstance(files, str):
        files = [files]
    if not files:
        return None, cmd
    from pathlib import Path as _P
    root = _P(repo)
    out = set()
    for pat in files:
        for f in root.glob(str(pat)):
            if f.is_file() and not (set(f.relative_to(root).parts) & SKIP_DIRS):
                out.add(f.relative_to(root).as_posix())
    return sorted(out), cmd


def discover_tests(repo):
    """通用模式的測試清單。
    Python:test_*.py、*_test.py、tests/ 下的 test-*.py;pytest 設了 testpaths ⇒ 只算那些路徑底下的
      (10-07 Flask:examples/ 底下是另外安裝的範例專案,pytest 本來就不跑,以前被算進來 ⇒ 5 支假紅、整套對答案跑不起來)。
    JS/TS(倉根有 package.json 才找;10-07):jest/vitest 的預設找法 —— *.test.*、*.spec.*、__tests__/ 底下的 JS/TS 檔;
      mocha 的預設 —— test/ 這一層的 .js/.mjs/.cjs。
    Go(倉根有 go.mod 才找;10-07):*_test.go(testdata/、vendor/ 底下不算)。
    PHP(composer.json 的 require/require-dev 有 phpunit/phpunit;10-07):tests/ 底下的 *Test.php。
    `.aib/ship.toml` 的 [test] files 有宣告 ⇒ 完全照宣告,不用上面的找法。
    略過虛擬環境、node_modules 等。"""
    declared, _cmd = declared_tests(repo)
    if declared is not None:
        return declared          # 10-07:設定檔宣告了 ⇒ 完全照宣告(任何語言)
    out = []
    tps = pytest_testpaths(repo)
    jsfw = _js_framework(repo)
    gomod = os.path.isfile(os.path.join(repo, "go.mod"))
    phpunit = _uses_phpunit(repo)
    for dp, dns, fns in os.walk(repo):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = os.path.relpath(dp, repo)
        parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        in_tests = bool(parts) and parts[0] in ("tests", "test")
        for f in sorted(fns):
            rel = os.path.normpath(os.path.join(rel_dir, f)).replace(os.sep, "/")
            if f.endswith(".py"):
                if f == "conftest.py":
                    continue
                if not (f.startswith("test_") or f.endswith("_test.py") or (in_tests and f.startswith("test-"))):
                    continue
                if tps is not None and not any(rel == t or rel.startswith(t + "/") for t in tps):
                    continue
                out.append(rel)
            elif phpunit and f.endswith("Test.php") and parts and parts[0] == "tests":
                out.append(rel)          # 10-07 PHP:composer.json 用 phpunit ⇒ tests/ 底下的 *Test.php
            elif f.endswith("_test.go") and gomod and "testdata" not in parts and "vendor" not in parts:
                out.append(rel)          # 10-07 Go:倉根有 go.mod 才找;testdata/、vendor/ 底下不算
            elif jsfw and f.endswith(JS_TEST_SUFFIXES):
                if _JS_TEST_RE.search(f) or "__tests__" in parts:
                    out.append(rel)
                elif jsfw == "mocha" and parts == ["test"] and f.endswith((".js", ".mjs", ".cjs")):
                    out.append(rel)
    return sorted(out)
