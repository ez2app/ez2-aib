#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib ext / aib theme:一個入口管所有擴充(10-14 the maintainer「全都要做」;種類見 ext_kinds.py)。

  aib ext list [--json]                       裝了哪些(外掛、佈景、skill、mod、角色卡、設定預設)
  aib ext install <資料夾> [--allow-unsigned]  裝一個建好的擴充包(tools/oss/plugin_kit.py build 做的,有 aib-ext.json)
  aib ext remove <名> [--kind <種類>]          拿掉
  aib theme list | current | use <名> | reset  佈景:列、看現在用哪套、換、換回內建
  aib ext enable <名> [--engine claude,codex,agy] [--trust-hooks]   啟用到各家 AI(install 預設就會做;下週才裝 Codex 時用)
  aib ext disable <名> [--engine …]                                 從各家 AI 撤掉(remove 會先做這一步)
  aib ext auto                                 裝這份 aib 自帶的延伸外掛(extensions/ 裡受信鑰匙簽過的每一個;一行安裝、aib update、aib init 會叫)

mod 本身就是 Claude Code 外掛(根目錄有 .claude-plugin/marketplace.json,10-15 the maintainer「裝上並在下一版自動安裝」):
  install / enable 在複製到 <狀態根>/mods/<名> 之後,接著 claude plugin marketplace add <那個資料夾> + claude plugin install
  <plugin>@<marketplace>(名字讀 marketplace.json 的 name / plugins)。已登記同一個資料夾、已裝同一版 ⇒ 不動;版本不同 ⇒ update。
  包只附 .claude-plugin/plugin.json、沒附 marketplace.json(例 aib-languages;補檔會讓簽章失效)⇒ aib 在
  <狀態根>/ext-engines/<名>/claude-mod/ 自己產生一份 marketplace(ez2-<名>),把 mod 複製到它的 ./plugin 再登記。
  (Claude Code 規定 plugin 位置要 ./ 開頭;用捷徑連過去的話 hooks 會被判「跑出外掛資料夾」不生效 ⇒ 只能複製,10-16 實測。)
  這台沒有 claude ⇒ 照實說、跳過。disable / remove 反做:uninstall 每個 plugin、移除 marketplace(不刪 mod 資料夾以外的東西)。
  看到舊的 aib-seat@ez2-aib-mods(已由 aib-boards 取代)⇒ 只提示怎麼移除,不替你刪。

多家 AI(10-14 the maintainer「plugin_kit 他會做的是 for aib 的外掛,要能包含所有 ai」;雲端 10-14 回覆定的規則;
設計稿 docs/DESIGN-plugin-kit-engines-86ff0-1014.md):擴充包的 aib-ext.json 有 engines(各家接頭)與 core(共用程式一份)。
  install 預設裝完就啟用到這台有的每家 AI(--no-enable 只放不啟用;--engine 只啟用那幾家)。
  啟用 = 先驗包裡每支檔的雜湊,再把 engines/<AI>/ + core/ 複製到 <狀態根>/ext-engines/<名>/<AI>/,用那家自己的指令裝:
    claude:claude plugin marketplace add + install;codex:codex plugin marketplace add + add;agy:agy plugin install
  同名 marketplace 已登記 ⇒ 先移除再登記(只略過會沿用舊資料夾)。這台沒裝那家 AI / 包沒有那家接頭 ⇒ 照實說、跳過。
  Codex 的鉤子要信任才會跑(~/.codex/config.toml):--trust-hooks 才替你寫,而且包要簽章過或當初用 --allow-unsigned 裝
  (不替來路不明的程式開門);寫之前備份,寫了哪幾個記在 <狀態根>/ext-state/<名>.json,disable 只撤那幾個。

裝之前一定檢查(不合就不裝,rc 3):
  1. 說明檔 aib-ext.json 認得、種類是能裝的那幾種
  2. 要哪版 aib(requires_aib):這台的 aib 比它舊 ⇒ 不裝,說請 aib update
  3. 檔案完整:每支檔的 sha256 跟說明檔一致,不多不少
  4. 信任:純資料(佈景、角色卡、設定預設)不帶程式檔就能裝;
     會跑程式的(外掛、skill、mod)要有受信鑰匙的簽章,或使用者自己加 --allow-unsigned(表示「我信這個來源」)。
     受信鑰匙 = aib 內建的發證公鑰 + <狀態根>/trusted-ext-keys 每行一把(ed25519:<hex>)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ext_kinds as K  # noqa: E402
import i18n_lib  # noqa: E402
import seats_dir_lib  # noqa: E402

MANIFEST = "aib-ext.json"
CODE_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".pl", ".exe", ".so", ".dylib")
NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def name_ok(name):
    """包名 / marketplace 名:英數開頭(擋 . / .. 指到上一層把狀態根刪掉、擋 - 開頭被各家 CLI 當參數;審查 10-14)。"""
    return bool(name) and len(name) <= 64 and name[0].isascii() and name[0].isalnum() and not set(name) - NAME_OK


def T(key, params=None):
    return i18n_lib.t("aib.ext." + key, params)


def state_root():
    return Path(seats_dir_lib.state_root()[0])


def aib_version():
    """這台 aib 的版號:pip / pipx 裝的看套件版號;不然看套件根的 VERSION。讀不到 ⇒ None(不擋)。"""
    try:
        from importlib.metadata import version
        return version("ez2-aib")
    except Exception:  # noqa: BLE001
        pass
    for f in (Path(HERE).parents[1] / "VERSION",):
        try:
            return f.read_text(encoding="utf-8").strip() or None
        except OSError:
            continue
    return None


def _num(v):
    return tuple(int(x) for x in str(v).split(".")[:3] if x.isdigit())


BYTECODE_DIR = "__pycache__"
BYTECODE_EXT = (".pyc", ".pyo")
NO_BYTECODE = shutil.ignore_patterns(BYTECODE_DIR, *("*" + e for e in BYTECODE_EXT))   # 複製時不帶


def _is_bytecode(rel):
    """Python 自己長出來的位元碼(有人直接跑包裡的 .py 就會長):__pycache__/ 底下的、或散落的 .pyc / .pyo"""
    rel = Path(rel)
    return BYTECODE_DIR in rel.parts or rel.suffix.lower() in BYTECODE_EXT


def _hashes(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and p.name != MANIFEST and not _is_bytecode(p.relative_to(folder))}


def bytecode_in(folder):
    """包裡的位元碼:最上層的 __pycache__ 資料夾 + 不在 __pycache__ 裡的 .pyc / .pyo(相對路徑,排好)"""
    folder = Path(folder)
    hits = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if p.name == BYTECODE_DIR and BYTECODE_DIR not in rel.parts[:-1]:
            hits.append(rel.as_posix() + "/")
        elif p.suffix.lower() in BYTECODE_EXT and BYTECODE_DIR not in rel.parts and (p.is_file() or p.is_symlink()):
            hits.append(rel.as_posix())
    return hits


def purge_bytecode(folder, out=sys.stdout):
    """10-17 @d07f9 回報、@abb89 派:位元碼不算進雜湊,但不默默放著 —— 列出來並清掉(避免有人塞 .pyc 被當成程式跑)。
    回清不掉的那幾個(呼叫端決定要不要擋)。"""
    folder = Path(folder)
    hits, left = bytecode_in(folder), []
    for rel in hits:
        p = folder / rel.rstrip("/")
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            left.append(rel)
    if hits:
        out.write(T("pyc_purged", {"n": len(hits), "what": hits[:5], "path": str(folder)}) + "\n")
    if left:
        out.write(T("pyc_left", {"what": left[:5]}) + "\n")
    return left


def trusted_keys():
    keys = []
    try:
        sys.path.insert(0, str(Path(HERE).parents[1] / "src"))
        from entitlement import keys as EK
        keys += list(EK.TRUSTED_PUBLIC_KEYS)
    except Exception:  # noqa: BLE001
        pass
    try:
        keys += [l.strip() for l in (state_root() / "trusted-ext-keys").read_text(encoding="utf-8").splitlines()
                 if l.strip().startswith("ed25519:")]
    except OSError:
        pass
    return tuple(keys)


def signed_ok(manifest):
    try:
        sys.path.insert(0, str(Path(HERE).parents[1] / "src"))
        from entitlement import signing
    except Exception:  # noqa: BLE001
        return False
    return signing.verify_object(manifest, key_list=trusted_keys(), draft=False) == signing.OK


def inspect(folder, allow_unsigned=False, have=None):
    """裝之前的四項檢查。回 (manifest 或 None, 問題清單)。"""
    folder = Path(folder)
    try:
        m = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, [T("bad_manifest", {"path": str(folder / MANIFEST)})]
    kind = K.kind_of_label(str(m.get("kind") or ""))
    name = str(m.get("name") or "")
    probs = []
    if kind not in K.INSTALLABLE:
        probs.append(T("bad_kind", {"kind": m.get("kind"), "ok": ", ".join(K.INSTALLABLE)}))
    if not name_ok(name):
        probs.append(T("bad_name", {"name": name}))
    have = aib_version() if have is None else have
    need = str(m.get("requires_aib") or "")
    if not need:
        probs.append(T("no_requires"))
    elif have and _num(have) < _num(need):
        probs.append(T("too_old", {"need": need, "have": have}))
    files = m.get("files") if isinstance(m.get("files"), dict) else None
    real = _hashes(folder)
    if files is None or files != real:
        extra = sorted(set(real) - set(files or {}))
        lack = sorted(set(files or {}) - set(real))
        changed = sorted(k for k in set(real) & set(files or {}) if real[k] != (files or {})[k])
        probs.append(T("not_intact", {"extra": extra[:5], "missing": lack[:5], "changed": changed[:5]}))
    if kind in K.INSTALLABLE and not K.KINDS[kind]["code"]:
        code = sorted(f for f in real if Path(f).suffix.lower() in CODE_EXT)
        if code:
            probs.append(T("data_has_code", {"what": code[:5]}))
    elif kind in K.INSTALLABLE and not signed_ok(m) and not allow_unsigned:
        probs.append(T("needs_trust", {"kind": K.KINDS[kind]["zh"]}))
    # 帶各家 AI 接頭的包:hooks.json 的 command 不是程式副檔名也會被執行(Claude / agy 鉤子不用信任就跑)⇒ 純資料種類也要簽章或 --allow-unsigned
    if kind in K.INSTALLABLE and not K.KINDS[kind]["code"] and m.get("engines") and not signed_ok(m) and not allow_unsigned:
        probs.append(T("needs_trust", {"kind": K.KINDS[kind]["zh"]}))
    return m, probs


ENGINES = ("claude", "codex", "agy")
ENGINE_CLI = {"claude": "claude", "codex": "codex", "agy": "agy"}


def _which(name):
    hit = shutil.which(name)
    if hit:
        return hit
    for p in (Path.home() / ".local" / "bin" / name, Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name):
        if p.exists():
            return str(p)
    return None


def _state_file(name):
    return state_root() / "ext-state" / ("%s.json" % name)


def _read_state(name):
    try:
        return json.loads(_state_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(name, st):
    f = _state_file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(argv):
    import subprocess
    try:
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def _activate(pkg, ai, dst):
    """engines/<AI>/ + core/ 每支 → dst(跟 plugin_kit.activate_view 同一個樣子)。"""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(pkg / "engines" / ai, dst)
    core = pkg / "core"
    if core.is_dir():
        for f in core.rglob("*"):
            if f.is_file():
                t = dst / f.relative_to(core)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
    return dst


def _marketplace(d):
    try:
        n = json.loads((d / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return n if isinstance(n, str) and name_ok(n) else None


LEGACY_CLAUDE_PLUGINS = ("aib-seat@ez2-aib-mods",)   # 10-15:已停用、由 aib-boards 取代的舊席位外掛


def _run_json(argv):
    """只讀 stdout 當 JSON;失敗 / 不是 JSON ⇒ None(量不到,不當成「沒有」)。"""
    import subprocess
    try:
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        return json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def mod_plugins(pkg):
    """mod 根目錄 .claude-plugin/marketplace.json ⇒ (marketplace 名, [plugin 名]);不是 Claude 外掛 / 名字不合 ⇒ (None, [])。"""
    try:
        d = json.loads((Path(pkg) / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    mkt = d.get("name") if isinstance(d, dict) else None
    names = [x.get("name") for x in (d.get("plugins") or []) if isinstance(x, dict)] if isinstance(d, dict) else []
    if not (isinstance(mkt, str) and name_ok(mkt)) or not names or not all(isinstance(n, str) and name_ok(n) for n in names):
        return None, []
    return mkt, names


def _folder_version(pkg):
    try:
        return json.loads((Path(pkg) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _legacy_hint(plugins, tool, out):
    for pid in LEGACY_CLAUDE_PLUGINS:
        if pid in plugins:
            out.write(T("legacy_plugin", {"id": pid, "cmd": "claude plugin uninstall %s && claude plugin marketplace remove %s"
                                          % (pid, pid.split("@", 1)[1])}) + "\n")


def _generated_marketplace(name, pkg):
    """包只有 plugin.json、沒有 marketplace.json ⇒ 在 ext-engines/<名>/claude-mod/ 產生一份,mod 複製到 ./plugin。
    回那個資料夾;連 plugin.json 都沒有 / 名字不合 ⇒ None。"""
    try:
        pname = json.loads((Path(pkg) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        return None
    if not (isinstance(pname, str) and name_ok(pname) and name_ok("ez2-" + name)):
        return None
    d = state_root() / "ext-engines" / name / "claude-mod"
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(pkg, d / "plugin", ignore=NO_BYTECODE)
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "ez2-" + name, "owner": {"name": "Ez2 AIB"}, "plugins": [{"name": pname, "source": "./plugin"}]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return d


def claude_register(name, pkg, out, changes=None):
    """mod 資料夾 pkg 向 Claude Code 登記。回 rc(0 = 成功或照實跳過)。changes:有動到就把名字加進去(印 /restart 提示用)。"""
    gen = None
    if not mod_plugins(pkg)[0]:
        gen = _generated_marketplace(name, pkg)
        if not gen:
            out.write(T("mod_not_claude", {"name": name, "path": str(Path(pkg) / ".claude-plugin" / "plugin.json")}) + "\n")
            return 0
        pkg = gen
    mkt, plugins = mod_plugins(pkg)
    tool = _which("claude")
    if not tool:
        out.write(T("eng_no_cli", {"engine": "claude", "cmd": "claude"}) + "\n")
        return 0
    st = _read_state(name)
    own = (st.get("claude_mod") or {}).get("mkt")
    mkts = _run_json([tool, "plugin", "marketplace", "list", "--json"])
    cur = {m.get("name"): m for m in (mkts or []) if isinstance(m, dict)}.get(mkt)
    here = os.path.realpath(str(pkg))
    same = bool(cur) and os.path.realpath(str(cur.get("path") or cur.get("installLocation") or "")) == here
    changed = False
    if cur and not same:
        if mkt != own and mkt != "ez2-" + name:     # 不是 aib 登記的、也不是這個包的固定命名 ⇒ 可能是使用者的,不刪
            out.write(T("eng_mkt_taken", {"mkt": mkt}) + "\n")
            return 1
        _run([tool, "plugin", "marketplace", "remove", mkt])
    if not same:
        rc, msg = _run([tool, "plugin", "marketplace", "add", str(pkg)])
        if rc != 0:
            out.write(T("eng_fail", {"engine": "claude", "name": name}) + "  " + msg[-200:] + "\n")
            return 1
        changed = True
    rows = _run_json([tool, "plugin", "list", "--json"])
    inst = {r.get("id"): r for r in (rows or []) if isinstance(r, dict)}
    want = _folder_version(Path(pkg) / "plugin" if gen else pkg)
    rc = 0
    for p in plugins:
        pid = "%s@%s" % (p, mkt)
        if pid not in inst:
            r, msg = _run([tool, "plugin", "install", pid])
        elif want and inst[pid].get("version") != want:
            _run([tool, "plugin", "marketplace", "update", mkt])
            r, msg = _run([tool, "plugin", "update", pid])
        else:
            continue
        if r != 0:
            out.write(T("eng_fail", {"engine": "claude", "name": pid}) + "  " + msg[-200:] + "\n")
            rc = 1
            continue
        changed = True
    st["claude_mod"] = {"mkt": mkt, "plugins": plugins}
    if gen:
        st["claude_mod"]["dir"] = str(gen)
    _write_state(name, st)
    out.write(T("eng_ok" if changed else "mod_already", {"engine": "claude", "name": name}) + "\n")
    if changed and changes is not None:
        changes.append(name)
    _legacy_hint(inst, tool, out)
    return rc


def claude_unregister(name, out):
    """反做 claude_register:只撤自己登記過的那個 marketplace 與 plugin。"""
    st = _read_state(name)
    rec = st.pop("claude_mod", None)
    if not rec:
        return 0
    tool = _which("claude")
    ok = bool(tool)
    if tool:
        for p in rec.get("plugins") or []:
            ok = _run([tool, "plugin", "uninstall", "%s@%s" % (p, rec.get("mkt"))])[0] == 0 and ok
        _run([tool, "plugin", "marketplace", "remove", str(rec.get("mkt"))])
    gen = state_root() / "ext-engines" / name / "claude-mod"
    if rec.get("dir") and os.path.realpath(rec["dir"]) == os.path.realpath(str(gen)):   # 只刪 aib 自己產生的那一份
        shutil.rmtree(gen, ignore_errors=True)
    if st:
        _write_state(name, st)
    else:
        _state_file(name).unlink(missing_ok=True)
    out.write(T("eng_off" if ok else "eng_off_fail", {"engine": "claude", "name": name}) + "\n")
    return 0


def codex_hooks(codex, plugin_id):
    """問 Codex 自己(app-server hooks/list)這個外掛的鉤子:[(key, currentHash, trustStatus)];量不到回 None。"""
    import select
    import subprocess
    import time
    try:
        p = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    try:
        for m in ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "aib-ext", "version": "0"}}},
                  {"jsonrpc": "2.0", "method": "initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}}):
            p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
        end = time.time() + 20
        while time.time() < end:
            r, _, _ = select.select([p.stdout], [], [], 1)
            if not r:
                continue
            line = p.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 2:
                got = []

                def walk(x):
                    if isinstance(x, dict):
                        if "trustStatus" in x and x.get("pluginId") == plugin_id:
                            got.append((x.get("key"), x.get("currentHash"), x.get("trustStatus")))
                        for v in x.values():
                            walk(v)
                    elif isinstance(x, list):
                        for v in x:
                            walk(v)
                walk(msg)
                return got
        return None
    finally:
        p.kill()


def _codex_cfg():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def _cfg_read(cfg):
    with open(cfg, encoding="utf-8", newline="") as f:     # 不轉換行:CRLF 的設定檔原樣留著
        return f.read()


def _cfg_write(cfg, text):
    """先寫同資料夾暫存檔再 os.replace:寫到一半失敗(磁碟滿)也不會留下只剩一截的 config.toml(審查第 7 輪);權限照原檔。
    config.toml 是符號連結 ⇒ 寫到它指的那支(不把連結換成一般檔);暫存檔用 mkstemp(兩個 aib ext 同時跑不互刪,不跟別人放的連結)。"""
    import tempfile
    real = Path(os.path.realpath(cfg))
    fd, tmp = tempfile.mkstemp(prefix=real.name + ".", suffix=".aib-ext-tmp", dir=str(real.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if real.exists():
            shutil.copymode(real, tmp)
        os.replace(tmp, real)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# 只收可列印 ASCII、不含 " \(TOML 不准控制字元;U+2028 這類會讓切行判法不一);引號 key 裡 [ ] 沒事(Codex 真的 key 帶 hooks[0])
TOML_KEY_OK = re.compile(r'[\x20\x21\x23-\x5b\x5d-\x7e]+')


def _lines(text):
    """只照 \n 切(保留行尾),跟 codex_untrust 的 split("\n") 同一判法。"""
    return [x for x in re.split(r"(?<=\n)", text) if x]


def _toml_all(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        return None


def _norm(doc):
    """hooks / hooks.state 是空表就當沒有(撤完留下空的 [hooks] 不算多改);型別不對 ⇒ None(不寫)。"""
    import copy
    d = copy.deepcopy(doc)
    h = d.get("hooks", {})
    if not isinstance(h, dict):
        return None
    st = h.get("state", {})
    if not isinstance(st, dict):
        return None
    if not st:
        h.pop("state", None)
    if not h:
        d.pop("hooks", None)
    return d


def _same_except(before, after, changed):
    """after 跟 before 比:只有 hooks.state 裡 changed 那幾個 key 不同({key: 新值或 None=刪掉}),其他一字不差 ⇒ True。
    hooks / hooks.state 不是表(hooks = "s"、[[hooks.state]])⇒ False(不寫,不丟例外)。"""
    if before is None or after is None:
        return False
    want = _norm(before)
    if want is None:
        return False
    st = want.setdefault("hooks", {}).setdefault("state", {})
    for k, v in changed.items():
        if v is None:
            st.pop(k, None)
        elif isinstance(st.get(k, {}), dict):
            st.setdefault(k, {})["trusted_hash"] = v
        else:
            return False
    want, got = _norm(want), _norm(after)
    return want is not None and got is not None and got == want


def _backup(cfg):
    import time
    if cfg.exists():
        shutil.copy2(cfg, cfg.with_name("config.toml.bak-aib-ext-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), time.time_ns() % 10 ** 9)))


def _toml_state(text):
    """設定檔裡已有的 hooks.state 表(用 tomllib 判,使用者手寫的段頭變體也認得);解析失敗 ⇒ None。"""
    try:
        import tomllib
        st = tomllib.loads(text).get("hooks", {}).get("state", {})
        return st if isinstance(st, dict) else {}
    except Exception:
        return None


def codex_trust(entries, own=()):
    """讀寫出錯(不是 UTF-8、是資料夾、沒權限)⇒ 回 [],不丟例外(enable / disable 沒接,丟了會卡在一半;審查第 6 輪)。"""
    try:
        return _codex_trust(entries, own)
    except (OSError, UnicodeError, ValueError):
        return []


def _codex_trust(entries, own=()):
    """把還沒信任的鉤子寫進 config.toml(先備份);回寫了哪些 key(disable 只撤這些)。
    key 不是可列印 ASCII 或含 " \\ ⇒ 不寫(免得多寫出別段、或寫壞設定檔)。設定檔本來就解析不了 ⇒ 一段都不寫(不在壞檔上疊東西)。那段已在:是自己寫過的(own)⇒ 換成新的 hash(包更新後舊 hash 會讓鉤子一直沒信任);
    不是自己寫的 ⇒ 不動、也不算進回傳(那是使用者的)。"""
    cfg = _codex_cfg()
    todo = [(k, h) for k, h, st in entries if st != "trusted" and k and h and TOML_KEY_OK.fullmatch(k) and re.fullmatch(r"[A-Za-z0-9:+/=._-]+", h)]
    if not todo:
        return []
    text = _cfg_read(cfg) if cfg.exists() else ""
    have = _toml_state(text)
    if have is None:
        return []
    wrote, hashes = [], dict(todo)
    before = _toml_all(text)
    lines = _lines(text)
    for k, h in todo:
        head = '[hooks.state."%s"]' % k
        at = [i for i, ln in enumerate(lines) if ln.strip() == head]     # 整行比對(註解掉的不算),跟 codex_untrust 同一判法
        if not at and k in have:      # 段頭是使用者手寫的變體(後面有註解、單引號、空白)⇒ 已在,不追加同名段(會變成不合法 TOML)
            continue
        if not at:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            lines += ["\n", head + "\n", 'trusted_hash = "%s"\n' % h]
            wrote.append(k)
        elif k in own:
            for j in range(at[0] + 1, len(lines)):
                st = lines[j].strip()
                if st.startswith("["):        # 到下一段為止(段頭前有空白也算),不碰別段
                    break
                if re.match(r'trusted_hash\s*=', st):
                    eol = lines[j][len(lines[j].rstrip("\r\n")):]
                    lines[j] = 'trusted_hash = "%s"' % h + eol
                    wrote.append(k)
                    break
    text = "".join(lines)
    # 保險:寫完要能解析,而且跟原本比只差這幾段的 trusted_hash(多行字串裡剛好有段頭那種刻意寫法也擋得住)⇒ 才寫;沒東西要寫就不碰檔、不留備份
    if not wrote or not _same_except(before, _toml_all(text), {k: hashes[k] for k in wrote}):
        return []
    if cfg.exists() and text == _cfg_read(cfg):   # 重跑、hash 一樣:不重寫、不多留備份
        return wrote
    _backup(cfg)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    _cfg_write(cfg, text)
    return wrote


def codex_untrust(keys):
    try:
        return _codex_untrust(keys)
    except (OSError, UnicodeError, ValueError):
        return 0


def _codex_untrust(keys):
    """只撤自己寫的那幾段 [hooks.state."<key>"](到下一個 [ 之前);使用者後來改的別段不動。回撤了幾段。"""
    cfg = _codex_cfg()
    if not keys or not cfg.exists():
        return 0
    text = _cfg_read(cfg)
    lines, out, skip, n = text.split("\n"), [], False, 0
    heads = {'[hooks.state."%s"]' % k for k in keys}
    for ln in lines:
        st = ln.strip()
        if st.startswith("["):
            skip = st in heads
            n += skip
        if not skip:
            out.append(ln)
    new = "\n".join(out)
    before = _toml_all(text)
    _st = ((before or {}).get("hooks") or {}) if isinstance((before or {}).get("hooks"), dict) else {}
    _st = _st.get("state") if isinstance(_st.get("state"), dict) else {}
    gone = {k: None for k in keys if k in _st}
    # 撤完要能解析,而且只少了自己那幾段 ⇒ 才寫(不然一段都不撤,Codex 設定檔保持原樣)
    if not n or not _same_except(before, _toml_all(new), gone):
        return 0
    _backup(cfg)
    _cfg_write(cfg, new)
    return n


def _engine_on(ai, d, name, out, own_mkt=None):
    """在那家 AI 裝 d(已啟用的資料夾)。回 (成功?, 補充, marketplace)。"""
    tool = _which(ENGINE_CLI[ai])
    if ai == "agy":
        rc, msg = _run([tool, "plugin", "install", str(d)])
        return rc == 0, msg[-200:], None
    mkt = _marketplace(d)
    if not mkt:
        return False, T("eng_no_marketplace", {"path": str(d)}), None
    add = [tool, "plugin", "marketplace", "add", str(d)]
    rc, msg = _run(add)
    if rc != 0 and "already" in msg.lower():
        # 同名的不是我們上次登記的、也不是這個包在 aib 的固定命名 ez2-<包名>(舊版 aib plugin 登記過的就是這個;不放寬成 ez2-aib-* 免得別的包頂掉)⇒ 可能是使用者的,不刪
        if mkt != own_mkt and mkt != "ez2-" + name:
            return False, T("eng_mkt_taken", {"mkt": mkt}), None
        _run([tool, "plugin", "marketplace", "remove", mkt])
        rc, msg = _run(add)
    if rc != 0:
        return False, msg[-200:], mkt
    rc, msg = _run([tool, "plugin", "install" if ai == "claude" else "add", "%s@%s" % (name, mkt)])
    return rc == 0, msg[-200:], mkt


def _engine_off(ai, name, rec):
    tool = _which(ENGINE_CLI[ai])
    if not tool:
        return False
    if ai == "agy":
        return _run([tool, "plugin", "uninstall", name])[0] == 0
    mkt = rec.get("mkt")
    rc = _run([tool, "plugin", "uninstall" if ai == "claude" else "remove", "%s@%s" % (name, mkt)])[0]
    _run([tool, "plugin", "marketplace", "remove", mkt])
    return rc == 0


def _installed_row(name, kind=None):
    hits = [r for r in installed() if r["name"] == name and (kind is None or r["kind"] == kind)]
    return hits[0] if len(hits) == 1 else None


def enable(name, engines=None, trust_hooks=False, out=sys.stdout, kind=None, changes=None):
    row = _installed_row(name, kind)
    if not row:
        hits = [r["kind"] for r in installed() if r["name"] == name]
        out.write((T("ambiguous", {"name": name, "kinds": hits}) if len(hits) > 1 else T("not_installed", {"name": name})) + "\n")
        return 1 if not hits else 2
    pkg = Path(row["path"])
    try:
        m = json.loads((pkg / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out.write(T("bad_manifest", {"path": str(pkg / MANIFEST)}) + "\n")
        return 3
    if purge_bytecode(pkg, out):   # 裝好的那份被直接跑過也會長位元碼;清不掉 ⇒ 不啟用(不讓多出來的 .pyc 被當程式跑)
        return 3
    if not isinstance(m.get("files"), dict) or m["files"] != _hashes(pkg):     # 啟用前再驗一次:裝好之後被改過就不啟用
        out.write(T("not_intact", {"extra": [], "missing": [], "changed": ["(installed copy)"]}) + "\n")
        return 3
    if row["kind"] == "mod" and not m.get("engines"):       # mod 本身就是 Claude Code 外掛
        if engines and "claude" not in engines:
            return 0
        return claude_register(name, pkg, out, changes)
    st = _read_state(name)
    trusted_src = signed_ok(m) or bool(st.get("allow_unsigned"))
    has = m.get("engines") or {}
    rc = 0
    for ai in (engines or ENGINES):
        if ai not in has:
            out.write(T("eng_none", {"engine": ai, "name": name}) + "\n")
            continue
        if not _which(ENGINE_CLI[ai]):
            out.write(T("eng_no_cli", {"engine": ai, "cmd": ENGINE_CLI[ai]}) + "\n")
            continue
        d = _activate(pkg, ai, state_root() / "ext-engines" / name / ai)
        prev = (st.get("enabled") or {}).get(ai) or {}
        ok, msg, mkt = _engine_on(ai, d, name, out, own_mkt=prev.get("mkt"))
        if not ok:
            out.write(T("eng_fail", {"engine": ai, "name": name}) + "  " + msg + "\n")
            rc = 1
            continue
        rec = {"dir": str(d), "mkt": mkt}
        if prev.get("trusted"):   # 重跑 enable:鉤子已信任 ⇒ 這次 need 空,舊的紀錄要留著,不然 disable 撤不掉(審查 10-14)
            rec["trusted"] = list(prev["trusted"])
        note = ""
        if ai == "codex":
            hooks = codex_hooks(_which("codex"), "%s@%s" % (name, mkt))
            need = [k for k, _h, s_ in (hooks or []) if s_ != "trusted"]
            wrote = []
            if hooks is None:
                note = T("trust_unknown")
            elif need and trust_hooks and trusted_src:
                wrote = codex_trust(hooks, own=prev.get("trusted") or ())
                rec["trusted"] = sorted(set(prev.get("trusted") or []) | set(wrote))
                # 一個都沒寫成(設定檔讀不了、格式認不得、寫了會壞、這台 Python 沒有 tomllib)⇒ 老實說還沒信任,不報「已信任 0 個」
                note = T("trusted", {"n": len(wrote), "path": str(_codex_cfg())}) if wrote else \
                    T("trust_not_written", {"n": len(need), "path": str(_codex_cfg())})
            elif need and trust_hooks:
                note = T("trust_refused")
            elif need:
                note = T("trust_needed", {"n": len(need)})
        st.setdefault("enabled", {})[ai] = rec
        _write_state(name, st)
        out.write(T("eng_ok", {"engine": ai, "name": name}) + (("  " + note) if note else "") + "\n")
    return rc


def _still_trusted(keys):
    """設定檔裡還留著的那幾個 key;檔不在 ⇒ [];讀不了 / 解析不了 ⇒ 全部當還在(寧可留紀錄)。"""
    cfg = _codex_cfg()
    if not cfg.is_file():
        return []
    try:
        st = _toml_state(_cfg_read(cfg))
    except (OSError, UnicodeError, ValueError):
        st = None
    return list(keys) if st is None else [k for k in keys if k in st]


def disable(name, engines=None, out=sys.stdout):
    if _read_state(name).get("claude_mod") and (not engines or "claude" in engines):
        claude_unregister(name, out)
    st = _read_state(name)
    on = st.get("enabled") or {}
    for ai in [a for a in (engines or ENGINES) if a in on]:
        rec = on.pop(ai)
        if ai == "codex" and rec.get("trusted"):
            codex_untrust(rec["trusted"])
            left = _still_trusted(rec["trusted"])
            if left:      # 撤不掉(檔讀不了、格式認不得)⇒ 紀錄留著(下次還能撤),並說出來;不默默丟掉
                on[ai] = {"trusted": left}
                out.write(T("untrust_failed", {"n": len(left), "path": str(_codex_cfg())}) + "\n")
        if not rec.get("dir"):     # 只剩上次撤不掉的信任紀錄(接頭早已撤掉)⇒ 這次只重撤信任
            continue
        ok = _engine_off(ai, name, rec)
        shutil.rmtree(rec.get("dir") or "/nonexistent", ignore_errors=True)
        out.write(T("eng_off" if ok else "eng_off_fail", {"engine": ai, "name": name}) + "\n")
    if on:
        st["enabled"] = on
        _write_state(name, st)
    elif not st.get("allow_unsigned"):      # 沒有要留的紀錄 ⇒ 狀態檔不留(mod 的 Claude Code 登記撤掉時可能已先刪,不重建)
        _state_file(name).unlink(missing_ok=True)
    else:
        st["enabled"] = {}
        _write_state(name, st)
    return 0


def target(kind, name):
    home = state_root() / K.KINDS[kind]["home"]
    p = home / name
    if not name_ok(name) or p.resolve().parent != home.resolve():   # 第二道:無論如何不准指到種類家目錄以外(之後會 rmtree)
        raise ValueError("bad extension name: %r" % name)
    return p


def install(folder, allow_unsigned=False, out=sys.stdout, enable_engines=True, engines=None, trust_hooks=False, changes=None):
    purge_bytecode(folder, out)   # 清不掉的也不會被複製過去(NO_BYTECODE)
    m, probs = inspect(folder, allow_unsigned=allow_unsigned)
    if probs:
        for p in probs:
            out.write("aib ext: %s\n" % p)
        return 3
    kind = K.kind_of_label(m["kind"])
    dst = target(kind, m["name"])
    tmp = dst.with_name(dst.name + ".installing")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(folder, tmp, ignore=NO_BYTECODE)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    if kind == "theme":
        os.chmod(dst / "theme.json", 0o644)      # 配色讀檔要求不可被群組 / 其他人寫
    out.write(T("installed", {"kind": K.KINDS[kind]["zh"], "name": m["name"], "version": m.get("version"),
                              "path": str(dst)}) + "\n")
    if kind == "theme":
        out.write(T("theme_hint", {"name": m["name"]}) + "\n")
    if kind == "plugin":
        reload_room_after_install(m, out)
    if m.get("engines"):
        st = _read_state(m["name"])
        st["allow_unsigned"] = bool(allow_unsigned and not signed_ok(m))   # 記下來:之後 enable --trust-hooks 要看來源有沒有被信任
        _write_state(m["name"], st)
        if enable_engines:
            return enable(m["name"], engines=engines, trust_hooks=trust_hooks, out=out, kind=kind)
        out.write(T("not_enabled", {"name": m["name"]}) + "\n")
    elif kind == "mod":
        if enable_engines:
            return enable(m["name"], engines=engines, out=out, kind=kind, changes=changes)
        out.write(T("not_enabled", {"name": m["name"]}) + "\n")
    return 0


def reload_room_after_install(manifest, out, env=None):
    """裝的是艦長室外掛(commands 有 room)⇒ 跟 aib update 一樣叫 room_reload:還在跑舊碼就送 /api/reload(@abb89 審 #234 ②)。
    沒在跑 / 版本一樣 ⇒ 什麼都不做;出錯只吞掉,不影響安裝結果。"""
    cmds = manifest.get("commands")
    if not (isinstance(cmds, dict) and isinstance(cmds.get("room"), str)):
        return None
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import room_reload
        room_reload.main([], out=out, env=env)
        return True
    except Exception:  # noqa: BLE001
        return False


def bundled_dir():
    """這份 aib 附的擴充:倉庫根的 extensions/(一行安裝 / git checkout 才有;pip 裝的沒有)。"""
    return Path(HERE).parents[2] / "extensions"


def _same_copy(src, dst):
    try:
        a = json.loads((Path(src) / MANIFEST).read_text(encoding="utf-8"))
        b = json.loads((Path(dst) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return a == b and b.get("files") == _hashes(dst)


def auto_install(out=sys.stdout, src=None, env=None):
    """裝 extensions/ 裡 aib 自帶的延伸外掛(mod、skill…,受信鑰匙簽過、沒有 requires_addon);清單讀各包 aib-ext.json,不寫死。
    10-16 the maintainer:extensions/ 是 aib 自己的延伸外掛,不是付費外掛(付費的在 aib-plugins 倉,這裡不碰)、5 個全裝。
    AIB_EXT_AUTO=0 ⇒ 全關。已裝同一份 ⇒ 只補 Claude Code 登記(沒登記才登記)。有動到 ⇒ 最後印一行「每席 /restart 生效」。"""
    env = os.environ if env is None else env
    if env.get("AIB_EXT_AUTO", "") == "0":
        out.write(T("auto_off") + "\n")
        return 0
    base = Path(src) if src else bundled_dir()
    if not base.is_dir():
        return 0
    changes, rc = [], 0
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            m = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = K.kind_of_label(str(m.get("kind") or ""))
        if kind not in K.INSTALLABLE or m.get("requires_addon"):
            continue
        name = str(m.get("name") or "")
        if not signed_ok(m):
            out.write(T("auto_unsigned", {"name": name}) + "\n")
            continue
        try:
            dst = target(kind, name)
        except ValueError:
            continue
        purge_bytecode(d, out)
        if dst.is_dir():
            purge_bytecode(dst, out)
        if _same_copy(d, dst):
            if kind == "mod" and not m.get("engines"):
                rc = claude_register(name, dst, out, changes) or rc
            continue
        before = len(changes)
        r = install(d, out=out, changes=changes)
        if r == 0 and len(changes) == before:
            changes.append(name)
        rc = r or rc
    if changes:
        out.write(T("restart_hint", {"names": ", ".join(changes)}) + "\n")
    return rc


def installed():
    rows = []
    for kind in K.INSTALLABLE:
        base = state_root() / K.KINDS[kind]["home"]
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.endswith(".installing")):
            m = {}
            for f in (MANIFEST, "plugin.json"):
                try:
                    m = json.loads((d / f).read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    continue
            if not isinstance(m, dict):            # manifest 是陣列 / 字串 ⇒ 當沒讀到,不讓整張表炸掉(10-24 aib support 雙審)
                m = {}
            rows.append({"kind": kind, "name": d.name, "version": m.get("version", "?"),
                         "requires_aib": m.get("requires_aib", "?"), "signed": bool(m.get("signature")), "path": str(d)})
    return rows


def remove(name, kind=None, out=sys.stdout):
    hits = [r for r in installed() if r["name"] == name and (kind is None or r["kind"] == kind)]
    if not hits:
        out.write(T("not_installed", {"name": name}) + "\n")
        return 1
    if len(hits) > 1:
        out.write(T("ambiguous", {"name": name, "kinds": [h["kind"] for h in hits]}) + "\n")
        return 2
    if _read_state(name).get("enabled") or _read_state(name).get("claude_mod"):
        disable(name, out=out)                     # 先從各家 AI 撤掉(含自己寫的 Codex 鉤子信任、mod 的 Claude Code 登記),再刪包
    if not _read_state(name).get("enabled"):       # 有撤不掉的信任紀錄 ⇒ 狀態檔留著(之後 aib ext disable <名> 還能撤),包照刪
        _state_file(name).unlink(missing_ok=True)
    shutil.rmtree(hits[0]["path"])
    if hits[0]["kind"] == "theme" and theme_current() == name:
        theme_reset()
    out.write(T("removed", {"kind": K.KINDS[hits[0]["kind"]]["zh"], "name": name}) + "\n")
    return 0


def theme_current():
    try:
        return (state_root() / "theme").read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return None


def theme_reset():
    try:
        (state_root() / "theme").unlink()
    except OSError:
        pass


def theme_use(name, out=sys.stdout):
    f = state_root() / "themes" / name / "theme.json"
    if not name_ok(name) or not f.is_file():
        out.write(T("theme_missing", {"name": name}) + "\n")
        return 1
    try:
        import theme as TH
        TH.load_theme(str(f))
    except Exception as e:  # noqa: BLE001
        out.write(T("theme_bad", {"name": name, "why": str(e)[:200]}) + "\n")
        return 3
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "theme.tmp"
    tmp.write_text(name + "\n", encoding="utf-8")
    os.replace(tmp, root / "theme")
    out.write(T("theme_used", {"name": name}) + "\n")
    return 0


def main(argv=None, out=sys.stdout):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["theme"]:
        sub = argv[1:2]
        if sub in ([], ["current"]):
            out.write((theme_current() or T("theme_builtin")) + "\n")
            return 0
        if sub == ["list"]:
            cur = theme_current()
            out.write(("* " if not cur else "  ") + T("theme_builtin") + "\n")
            for r in installed():
                if r["kind"] == "theme":
                    out.write(("* " if r["name"] == cur else "  ") + "%s %s\n" % (r["name"], r["version"]))
            return 0
        if sub == ["use"] and len(argv) == 3:
            return theme_use(argv[2], out=out)
        if sub == ["reset"]:
            theme_reset()
            out.write(T("theme_used", {"name": T("theme_builtin")}) + "\n")
            return 0
        out.write(T("theme_usage") + "\n")
        return 2
    ap = argparse.ArgumentParser(prog="aib ext", description=T("about"))
    sp = ap.add_subparsers(dest="cmd")
    p = sp.add_parser("list")
    p.add_argument("--json", action="store_true")
    p = sp.add_parser("install")
    p.add_argument("folder")
    p.add_argument("--allow-unsigned", action="store_true")
    p.add_argument("--no-enable", action="store_true", help="put it in place but do not enable it in any AI")
    p.add_argument("--engine", default="", help="comma list: claude,codex,agy (default: every AI on this machine)")
    p.add_argument("--trust-hooks", action="store_true", help="also trust its Codex hooks (signed or --allow-unsigned packages only)")
    for c in ("enable", "disable"):
        p = sp.add_parser(c)
        p.add_argument("name")
        p.add_argument("--engine", default="", help="comma list: claude,codex,agy")
        if c == "enable":
            p.add_argument("--trust-hooks", action="store_true", help="also trust its Codex hooks (signed or --allow-unsigned packages only)")
    sp.add_parser("auto")
    p = sp.add_parser("remove")
    p.add_argument("name")
    p.add_argument("--kind", choices=K.INSTALLABLE)
    a = ap.parse_args(argv[1:] if argv[:1] == ["ext"] else argv)
    eng = [e for e in (getattr(a, "engine", "") or "").split(",") if e]
    bad = [e for e in eng if e not in ENGINES]
    if bad:
        out.write(T("bad_engine", {"what": ", ".join(bad), "values": ", ".join(ENGINES)}) + "\n")
        return 2
    if a.cmd == "install":
        return install(a.folder, allow_unsigned=a.allow_unsigned, out=out, enable_engines=not a.no_enable, engines=eng or None,
                       trust_hooks=a.trust_hooks)
    if a.cmd == "enable":
        return enable(a.name, engines=eng or None, trust_hooks=a.trust_hooks, out=out)
    if a.cmd == "disable":
        return disable(a.name, engines=eng or None, out=out)
    if a.cmd == "remove":
        return remove(a.name, a.kind, out=out)
    if a.cmd == "auto":
        return auto_install(out=out)
    rows = installed()
    if getattr(a, "json", False):
        out.write(json.dumps(rows, ensure_ascii=False) + "\n")
        return 0
    if not rows:
        out.write(T("none") + "\n")
        return 0
    for r in rows:
        out.write("%-8s %-24s %-8s %s %s\n" % (K.KINDS[r["kind"]]["zh"], r["name"], r["version"],
                                             T("needs", {"v": r["requires_aib"]}), T("signed") if r["signed"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
