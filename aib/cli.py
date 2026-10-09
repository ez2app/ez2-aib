# SPDX-License-Identifier: Apache-2.0
"""aib — run only the tests your change affects, and ship only when nothing new is red.

    aib test <test files…>      run tests; reuse earlier green results whose inputs did not change
    aib test --all              run the whole suite that way
    aib ship <subcommand> …     pre-push check, package, release (see `aib ship --help`)
    aib support [--confirm]     collect a de-identified bug report pack (nothing is sent; you attach it to an issue)
    aib license status|device-id|activate|deactivate   paid plugins: show / bind / activate this machine
    aib tmp [--apply]           list (or remove) what aib and its tests left in the temp folder; nothing else is touched
    aib update [--tools]        update this copy to the latest version (same as re-running the one-line install)
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
           "tmp": os.path.join(HERE, "tools", "verify", "tmp_sweep.py")}


def _locale():
    """EZ2_AIB_LOCALE wins; otherwise follow the system locale (zh_TW / ja / ko); everything else is English."""
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").lower()
    for prefix, loc in (("zh_tw", "zh-TW"), ("zh_hk", "zh-TW"), ("ja", "ja"), ("ko", "ko")):
        if lang.startswith(prefix):
            return loc
    return "en"


INSTALL_URLS = ("https://aib.ez2.app/install.sh", "https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh")


def _update(args):
    """aib update(10-09 the maintainer「aib update 指令也做」):下載最新的一行安裝程式,裝在這份 aib 所在的 checkout。
    = 重跑 curl -fsSL https://aib.ez2.app/install.sh | bash:只快轉、不蓋本機改動、不再問 AI 工具(--tools 再問)。
    AIB_INSTALL_URL:自架/測試用的安裝程式網址(curl 讀得到的都行,含 file://);可以給好幾個(空白分隔),依序試。
    10-09(the maintainer「第一種有就用第一種,沒有自動第二種備用」):預設先試網站,下載不到就用公開倉裡的 install.sh。"""
    root = os.path.dirname(HERE)          # 從 checkout 跑(bin/aib):HERE = <checkout>/aib
    if not os.path.isdir(os.path.join(root, ".git")):
        sys.stderr.write("aib update: this aib (%s) is not a git checkout, so it was not installed by the one-line "
                         "installer; update it the way you installed it (for example: pipx upgrade aib)\n" % root)
        return 2
    urls = os.environ.get("AIB_INSTALL_URL", " ".join(INSTALL_URLS))
    # 先下載完整再跑(下載不完整不會跑半截);每個網址都下載不到要講清楚,不能被管線吃掉
    script = ('s=""; for u in $0; do s="$(curl -fsSL "$u")" && [ -n "$s" ] && break; s=""; done; '
              '[ -n "$s" ] || { echo "aib update: could not download the installer from: $0" >&2; exit 5; }; '
              'exec bash -c "$s" aib-install "$@"')
    os.execv("/bin/bash", ["bash", "-c", script, urls, "--dir", root] + list(args))


def main():
    """pip / pipx 裝好之後的 `aib` 指令進到這裡(pyproject.toml 的 project.scripts)。"""
    os.environ.setdefault("EZ2_AIB_LOCALE", _locale())
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 2
    if sys.argv[1] == "update":
        return _update(sys.argv[2:])
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
        if plugin is not None:
            sys.stderr.write("aib: '%s' comes from plugin '%s', which cannot run here (%s)\n" % (sys.argv[1], plugin, why))
            return 3
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
