# SPDX-License-Identifier: Apache-2.0
"""aib — many AIs, one workspace: AI seats from any vendor side by side, each with its own persona and memory;
only the affected tests rerun, and nothing ships while something new is red.

    aib test <test files…>      run tests; reuse earlier green results whose inputs did not change
    aib test --all              run the whole suite that way
    aib ship <subcommand> …     pre-push check, package, release (see `aib ship --help`)
    aib support [--confirm]     collect a de-identified bug report pack (nothing is sent; you attach it to an issue)
    aib tmp [--apply]           list (or remove) what aib and its tests left in the temp folder; nothing else is touched
    aib lease                   who holds this machine's test lease, until when, and who is queued (changes neither)
    aib update [--tools]        update this copy to the latest version (same as re-running the one-line install)
    aib uninstall [--all] [--yes]   remove aib from this computer (lists first; --yes removes; --all also records)
    aib <provider> <dgrs|safe> <role>   open an AI seat in the cockpit (macOS + tmux; see `aib --help-cockpit`)
    aib <other commands> …      everything else goes to the cockpit (seats, fleet, handoff, new project / role)
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.realpath(__file__))     # 套件資料夾本身 = 原本倉根的版面(tools/、src/、i18n/)
TARGETS = {"test": os.path.join(HERE, "tools", "verify", "test_entry.py"),
           "ship": os.path.join(HERE, "tools", "release", "ship.py"),
           "support": os.path.join(HERE, "tools", "support", "collect.py"),
           "tmp": os.path.join(HERE, "tools", "verify", "tmp_sweep.py"),
           "uninstall": os.path.join(HERE, "tools", "support", "uninstall.py")}


def _locale():
    """System-language hint from LC_ALL / LC_MESSAGES / LANG (zh_TW / ja / ko; everything else is English).
    10-14 語系盤查第 8 條:只當 i18n_lib.resolve_locale 的最低一層 EZ2_AIB_LOCALE_SYSTEM(排在 EZ2_AIB_LOCALE、
    艙檔、人檔之後)—— 原本寫進 EZ2_AIB_LOCALE,env 排在設定檔前面,`aib lang ja` 之後開著的艙仍吃 LANG 值。"""
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").lower()
    for prefix, loc in (("zh_tw", "zh-TW"), ("zh_hk", "zh-TW"), ("ja", "ja"), ("ko", "ko")):
        if lang.startswith(prefix):
            return loc
    return "en"


INSTALL_URLS = ("https://aib.ez2.app/install.sh", "https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh")
PUBLIC_MARK = "github.com/ez2app/ez2-aib.git"   # 公開版安裝程式的預設來源;舊的私有版安裝程式沒有這一串


def _update(args):
    """aib update(10-09 the maintainer「aib update 指令也做」):下載最新的一行安裝程式,裝在這份 aib 所在的 checkout。
    = 重跑 curl -fsSL https://aib.ez2.app/install.sh | bash:只快轉、不蓋本機改動、不再問 AI 工具(--tools 再問)。
    AIB_INSTALL_URL:自架/測試用的安裝程式網址(curl 讀得到的都行,含 file://);可以給好幾個(空白分隔),依序試。
    10-09(the maintainer「第一種有就用第一種,沒有自動第二種備用」):預設先試網站,下載不到就用公開倉裡的 install.sh。
    10-10(Mac a seat 實測):網站還在回舊的私有版安裝程式 ⇒「下載得到」不夠,還要是公開版(內含 PUBLIC_MARK);
    不是 ⇒ 當成這個網址不能用,換下一個。"""
    root = os.path.dirname(HERE)          # 從 checkout 跑(bin/aib):HERE = <checkout>/aib
    if not os.path.isdir(os.path.join(root, ".git")):
        sys.stderr.write("aib update: this aib (%s) is not a git checkout, so it was not installed by the one-line "
                         "installer; update it the way you installed it (pipx upgrade ez2-aib, or pip install --upgrade ez2-aib)\n" % root)
        return 2
    urls = os.environ.get("AIB_INSTALL_URL", " ".join(INSTALL_URLS))
    # 先下載完整再跑(下載不完整不會跑半截);每個網址都下載不到要講清楚,不能被管線吃掉
    script = ('m="$1"; shift; s=""; for u in $0; do s="$(curl -fsSL "$u")" && case "$s" in *"$m"*) break;; esac; '
              'if [ -n "$s" ]; then echo "aib update: $u is not the open-source installer; trying the next one" >&2; fi; '
              's=""; done; '
              '[ -n "$s" ] || { echo "aib update: could not download the installer from: $0" >&2; exit 5; }; '
              'exec bash -c "$s" aib-install "$@"')
    os.execv("/bin/bash", ["bash", "-c", script, urls, PUBLIC_MARK, "--dir", root] + list(args))


def _lease(args):
    """aib lease(10-14 新用戶走查:aib test 說「大批要排隊拿機器租約」,用戶卻查不到租約在誰手上、等到幾點)。
    不帶參數 = 只看:host_lease.py status(誰拿著、從幾點到幾點)+ queue list(誰在排、等多久),不改租約也不改排隊名單
    (queue list 會照它平常的做法建好狀態資料夾與空的 .lock 檔,沒有別的);rc 跟 status 一樣
    (0 機器空、4 被佔、3 讀不到)。帶參數 = 原樣交給 host_lease.py(acquire / release / renew / queue …,給腳本用)。"""
    tool = os.path.join(HERE, "tools", "governance", "host_lease.py")
    if args:
        os.execv(sys.executable, [sys.executable, tool] + list(args))
    import subprocess
    rc = subprocess.run([sys.executable, tool, "status"], stdin=subprocess.DEVNULL).returncode
    subprocess.run([sys.executable, tool, "queue", "list"], stdin=subprocess.DEVNULL)
    return rc


def main():
    """pip / pipx 裝好之後的 `aib` 指令進到這裡(pyproject.toml 的 project.scripts)。"""
    # 最外層那次 aib 寫的值被艙內繼承;艙內 LANG 被 cockpit-full.sh 強制成 en_US 也不會蓋掉(setdefault)
    os.environ.setdefault("EZ2_AIB_LOCALE_SYSTEM", _locale())
    # 10-12 新用戶全面檢查:Python 編譯快取不寫進安裝資料夾(看起來像被改過、也會擋 aib update);放 ~/.cache/ez2-aib
    os.environ.setdefault("PYTHONPYCACHEPREFIX", os.path.join(os.path.expanduser("~"), ".cache", "ez2-aib", "pycache"))
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        sys.stdout.write(__doc__)
        return 2
    if sys.argv[1] == "update":
        return _update(sys.argv[2:])
    if sys.argv[1] == "lease":
        return _lease(sys.argv[2:])
    if sys.argv[1] == "license":
        # 授權(10-08):status / device-id / activate / deactivate —— entitlement 是套件(相對 import),用 run_module
        sys.path.insert(0, os.path.join(HERE, "src"))
        sys.argv = ["aib license"] + sys.argv[2:]
        runpy.run_module("entitlement.cli", run_name="__main__", alter_sys=True)
        return 0
    if sys.argv[1] not in TARGETS:
        # 外掛新增的子指令(裝在 ~/.ez2-aib/plugins 的外掛,說明檔 commands 欄)優先;要加購的沒授權 ⇒ 說清楚、不跑
        sys.path.insert(0, os.path.join(HERE, "tools", "verify"))
        import aib_plugins
        script, plugin, why = aib_plugins.find_command(sys.argv[1])
        if script is not None:
            runner = [sys.executable] if str(script).endswith(".py") else ["/bin/bash"]
            os.execv(runner[0], runner + [str(script)] + sys.argv[2:])
        if plugin is not None and not aib_plugins.overrides_builtin(plugin):
            sys.stderr.write("aib: '%s' comes from plugin '%s', which cannot run here (%s)\n" % (sys.argv[1], plugin, why))
            return 3
        # 10-12:外掛蓋過的是內建指令(例如 `aib room`)⇒ 外掛不能跑就照舊跑內建的
        # 駕駛艙(開源內建,10-08):其他子指令都交給它的啟動器 —— 跟私有倉的 aib 指令同一支
        cockpit = os.path.join(HERE, "src", "cockpit", "aib")
        args = ["--help"] if sys.argv[1] == "--help-cockpit" else sys.argv[1:]
        os.execv("/bin/bash", ["bash", cockpit] + args)
    target = TARGETS[sys.argv[1]]
    sys.argv = [target] + sys.argv[2:]
    sys.path.insert(0, os.path.dirname(target))
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
