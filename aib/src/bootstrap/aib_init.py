#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ED.2 `aib init` 首跑建置:建狀態目錄鏈並逐層 chmod。

完成定義(大綱):全新 checkout + 目標 umask + 零手動修正跑完全套。

為什麼要「逐層」:`os.makedirs(mode=...)` 的 mode 只作用於最末一層,
中間層吃 umask(記憶 makedirs-mode-only-leaf);而 `mkdir(mode=)` 本身
也會被 umask 剝位。所以每一層都是 mkdir 之後**再 chmod**——chmod 不受
umask 影響,是權限的唯一權威;chmod 完 re-stat 驗證,不宣稱沒驗過的成功。

邊界(誰的權限歸誰):
  * STATE_TREE 是目錄正本,資料驅動,唯一清單——要加目錄改表,不散落硬編。
  * 正本內的目錄:缺就建,權限不對就修(fixed)。
  * init 自己新建的祖先目錄:同樣釘 0700。
  * **既有的祖先目錄一律不碰**(例如 $HOME)——它們的權限不是 init 的財產。
  * **既有的帳本檔一 byte 不動、一個權限位不動**;只在缺席時建空檔
    (空帳本過 E1.1 鏈驗證:ok count=0)。
  * 鏈上任何一層是 symlink 即 fail(fail-closed,E2.9 同族教訓)。

唯寫己域:本工具寫狀態根鏈,絕不動 repo 與 vault。
桌面捷徑是唯一例外:在 Desktop 放「AIB 第二大腦」指向狀態根的 user/ 入口,
讓人管理第二大腦與設定;桌面不存在就略過,不擋 init。

狀態根解析順序:--state-root > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib
(doctor 讀 EZ2_AIB_STATE_ROOT、claimtruth 讀 EZ2_AIB_HOME,兩者都認)。

exit: 0 全部就緒(含冪等重跑) · 1 有 fail · 2 用法錯
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

DEFAULT_STATE_ROOT = "~/.ez2-aib"

DIR_MODE = 0o700
FILE_MODE = 0o600


def _T(key, params=None):
    """使用者看得到的字一律走 i18n(aib/i18n/<語系>.json);語系照 aib lang / EZ2_AIB_LOCALE。"""
    cockpit = str(Path(__file__).resolve().parents[1] / "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    import i18n_lib  # noqa: E402
    return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale())

# 目錄正本:相對狀態根的路徑 -> 釘死的權限。"" 是狀態根自身。
# 清單依 2026-08-13 現行 ~/.ez2-aib 實際結構收錄;現場觀察到的較鬆權限
# (0750/0755/0770)是歷次 umask 007/022 產物,不是設計,init 統一釘 0700。
class InitError(Exception):
    """建置前置檢查失敗:一個檔都不動就退出(fail-closed 且零副作用)。"""


STATE_TREE = (
    ("", DIR_MODE),
    ("seats", DIR_MODE),
    ("logs", DIR_MODE),
    ("state", DIR_MODE),
    ("state/dm-queue", DIR_MODE),
    ("ledger", DIR_MODE),
    ("cockpit", DIR_MODE),
    ("instances", DIR_MODE),
    ("rotation", DIR_MODE),
    ("memory", DIR_MODE),
    ("secrets", DIR_MODE),
    ("secrets/social", DIR_MODE),
    ("secrets/live", DIR_MODE),
    ("secrets/games", DIR_MODE),
    ("identity", DIR_MODE),
    ("user", DIR_MODE),
)

# 檔案正本:缺席才建,既有一律不碰(內容與權限都不碰)。
STATE_FILES = (
    ("ledger/aib-ledger.jsonl", FILE_MODE),
)

CREATED, FIXED, OK, FAIL = "created", "fixed", "ok", "fail"


def _item(path, action, mode, detail="", remedy=""):
    return {"path": str(path), "action": action,
            "mode": format(mode, "04o") if mode is not None else None,
            "detail": detail, "remedy": remedy}


def _mode_of(path):
    return stat.S_IMODE(os.lstat(str(path)).st_mode)


def ensure_dir(path, mode, must_create=False):
    """建/修一層目錄並驗證。回傳單一結果 item。"""
    path = Path(path)
    if os.path.islink(str(path)):
        return _item(path, FAIL, mode, "是 symlink,fail-closed 不跟隨",
                     "移除該 symlink 後重跑 aib init")
    if path.exists():
        if not path.is_dir():
            return _item(path, FAIL, mode, "已存在但不是目錄",
                         "移走該檔後重跑 aib init")
        if must_create:
            # 理論上不會到:呼叫方只對 missing 祖先設 must_create。
            return _item(path, FAIL, mode, "預期新建卻已存在", "重跑一次")
        if _mode_of(path) == mode:
            return _item(path, OK, mode)
        try:
            os.chmod(str(path), mode)
        except OSError as err:
            return _item(path, FAIL, mode, str(err),
                         "確認擁有者後手動 chmod %s" % format(mode, "04o"))
        got = _mode_of(path)
        if got != mode:
            return _item(path, FAIL, mode,
                         "chmod 後仍是 %s" % format(got, "04o"),
                         "檢查檔案系統旗標(chflags/ACL)")
        return _item(path, FIXED, mode, "權限已由鬆修緊")
    try:
        os.mkdir(str(path), mode)
        os.chmod(str(path), mode)  # mkdir 的 mode 被 umask 剝位,chmod 才算數
    except OSError as err:
        return _item(path, FAIL, mode, str(err),
                     "確認上層目錄可寫後重跑 aib init")
    got = _mode_of(path)
    if got != mode:
        return _item(path, FAIL, mode, "建立後權限是 %s" % format(got, "04o"),
                     "檢查檔案系統旗標(chflags/ACL)")
    return _item(path, CREATED, mode)


def ensure_file(path, mode):
    """缺席才建空檔;既有檔一 byte 不動、一個權限位不動。"""
    path = Path(path)
    if os.path.islink(str(path)):
        return _item(path, FAIL, mode, "是 symlink,fail-closed 不跟隨",
                     "移除該 symlink 後重跑 aib init")
    if path.exists():
        if not path.is_file():
            return _item(path, FAIL, mode, "已存在但不是一般檔案",
                         "人工裁決該路徑後重跑")
        return _item(path, OK, None, "既有檔,init 不碰")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.fchmod(fd, mode)  # open 的 mode 同樣被 umask 剝位
        finally:
            os.close(fd)
    except OSError as err:
        return _item(path, FAIL, mode, str(err), "檢查目錄權限後重跑 aib init")
    got = _mode_of(path)
    if got != mode:
        return _item(path, FAIL, mode, "建立後權限是 %s" % format(got, "04o"),
                     "檢查檔案系統旗標(chflags/ACL)")
    return _item(path, CREATED, mode)


# 終審異質審(Codex 08-13)高2/高3 當場修:
#   1. 既有祖先若是 symlink,原版會靜默穿透到 link 目標裡建樹——「鏈上
#      symlink fail-closed」只擋了 state root 本身。現在整條既有祖先鏈逐層驗。
#   2. state root 落在系統共享路徑(/、/tmp、$HOME 本身…)時,chmod 0700 是
#      災難(例如 /tmp 失去 01777)。拒絕清單+必須位於 $HOME 之下。
_FORBIDDEN_ROOTS = ("/", "/tmp", "/private/tmp", "/var", "/private/var",
                    "/usr", "/etc", "/private", "/opt", "/Users", "/home")


def reject_dangerous_root(root):
    # 只拒「共享目錄本身」(chmod 0700 會炸掉全機共用語意,如 /tmp 失去 01777);
    # 不強制必須在 $HOME 下——ED.10 可攜性允許備選根,測試治具也用 /private/tmp/<專屬子目錄>。
    rp = str(Path(root))
    if rp in _FORBIDDEN_ROOTS or rp == str(Path.home()):
        raise InitError("state root 不可為系統共享路徑或 HOME 本身:%s" % rp)


# macOS 的 /tmp /var /etc 本身就是指向 /private/* 的 symlink——那是 OS 拓樸不是
# 攻擊,一律擋會讓任何 /tmp 下的治具與備選根全滅(終審實踩)。白名單只收
# 「連結與目標都由 OS 擁有」的正典三件,其餘祖先 symlink 照舊 fail-closed。
_OS_CANONICAL_LINKS = {
    ("/tmp", "/private/tmp"),
    ("/var", "/private/var"),
    ("/etc", "/private/etc"),
}


def check_existing_ancestors(root):
    """既有祖先鏈逐層驗 symlink——穿透 link 建樹=把狀態根放進別人指定的地方。"""
    cur = Path(root).parent
    while str(cur) != cur.root and cur != cur.parent:
        if cur.is_symlink():
            pair = (str(cur), os.path.realpath(str(cur)))
            if pair not in _OS_CANONICAL_LINKS:
                raise InitError("祖先是 symlink,fail-closed:%s" % cur)
        cur = cur.parent


def missing_ancestors(root):
    """狀態根之上、尚不存在的祖先(由淺到深)。既有祖先不入列=不碰(但已驗非 symlink)。"""
    chain = []
    cur = Path(root).parent
    while not cur.exists() and str(cur) != cur.root and cur != cur.parent:
        chain.append(cur)
        cur = cur.parent
    return list(reversed(chain))


YES_WORDS = ("y", "yes", "是", "好", "はい", "네", "예")


def ask_tty(question):
    """終端機問一句,預設不要(只有明確答是才放)。讀不到(EOF、中斷)⇒ 不要。"""
    try:
        ans = input(question).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in YES_WORDS


def run_init(state_root, ask=None):
    root = Path(os.path.expanduser(state_root))
    reject_dangerous_root(root)
    check_existing_ancestors(root)
    items = []
    for anc in missing_ancestors(root):
        items.append(ensure_dir(anc, DIR_MODE, must_create=True))
    for rel, mode in STATE_TREE:
        items.append(ensure_dir(root / rel if rel else root, mode))
    for rel, mode in STATE_FILES:
        parent_failed = any(i["action"] == FAIL for i in items)
        if parent_failed:
            items.append(_item(root / rel, FAIL, None, "上游目錄已 fail,跳過",
                               "先解決上面的 fail"))
            continue
        items.append(ensure_file(root / rel, mode))
    items.extend(_ensure_secrets_tree(root))
    from desktop_shortcut import ensure_desktop_hub
    items.extend(ensure_desktop_hub(root, ask=ask))
    return items


def _ensure_secrets_tree(root):
    """金鑰櫃檔案樹:空範本 + 搬遷舊檔。既有不覆寫。"""
    tools = Path(__file__).resolve().parents[2] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import secrets_init
    except ImportError as err:
        return [_item(root / "secrets", FAIL, None,
                      "載入 secrets_init 失敗:%s" % err,
                      "確認 tools/secrets_init.py 在")]
    try:
        return secrets_init.ensure_layout(root)
    except Exception as err:  # noqa: BLE001
        return [_item(root / "secrets", FAIL, None, str(err)[:200],
                      "檢查 secrets/ 後重跑 aib init")]


def _ensure_hooks():
    """10-15(維護者「安裝 / 升級時就裝,不必等開席」):這台有裝的 AI CLI 全部裝上依用量自動精煉的 hook
    (src/identity/auto_distill_hook.py ensure_all:沒裝的略過並說明、各家設定先備份、AIB_AUTO_DISTILL=0 全關)。
    出錯不算 init 失敗。→ [{"brand", "status", "line"}]。"""
    ident = str(Path(__file__).resolve().parents[1] / "identity")
    if ident not in sys.path:
        sys.path.insert(0, ident)
    try:
        import auto_distill_hook
        return [{"brand": b, "status": did, "line": line} for b, did, line in auto_distill_hook.ensure_all()]
    except Exception as err:  # noqa: BLE001
        return [{"brand": "all", "status": "skip", "line": "auto-distill: %s" % str(err)[:200]}]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="aib init", description=_T("aib.init.help.description"))
    parser.add_argument(
        "--state-root",
        default=os.environ.get(
            "EZ2_AIB_STATE_ROOT",
            os.environ.get("EZ2_AIB_HOME",
                           os.path.expanduser(DEFAULT_STATE_ROOT))),
        help=_T("aib.init.help.state_root"))
    parser.add_argument("--json", action="store_true", help=_T("aib.init.help.json"))
    args = parser.parse_args(argv)

    try:
        interactive = not args.json and sys.stdin.isatty() and sys.stdout.isatty()
        items = run_init(args.state_root, ask=ask_tty if interactive else None)
    except InitError as err:
        sys.stderr.write("aib-init: %s\n" % err)
        return 2
    hooks = _ensure_hooks()
    counts = {CREATED: 0, FIXED: 0, OK: 0, FAIL: 0}
    for i in items:
        counts[i["action"]] += 1

    if args.json:
        print(json.dumps({"state_root": str(Path(os.path.expanduser(args.state_root))),
                          "items": items, "counts": counts, "hooks": hooks},
                         ensure_ascii=False, indent=2))
    else:
        mark = {CREATED: _T("aib.init.mark_created"), FIXED: _T("aib.init.mark_fixed"), OK: "OK  ", FAIL: "FAIL"}
        for i in items:
            # 10-12 新用戶全面檢查:金鑰櫃 / 桌面捷徑回的項目不一定有 mode/detail/remedy ⇒ 用 get,不在最後一步崩
            line = "%s %s" % (mark[i["action"]], i["path"])
            if i.get("mode"):
                line += " (%s)" % i["mode"]
            if i.get("detail"):
                line += " — %s" % i["detail"]
            if i["action"] == FAIL and i.get("remedy"):
                line += "\n     -> %s" % i["remedy"]
            print(line)
        print("\ncreated=%d fixed=%d ok=%d fail=%d"
              % (counts[CREATED], counts[FIXED], counts[OK], counts[FAIL]))
        for h in hooks:
            sys.stderr.write(h["line"] + "\n")
        auto_ext()

    return 0 if counts[FAIL] == 0 else 1


def auto_ext(env=None):
    """10-15 the maintainer「裝上並在下一版自動安裝」:aib init 順手裝這份 aib 附的免費、已簽章 mod(aib ext auto)。
    人在終端機打 aib init 才做;腳本 / 測試叫的(輸出不是終端機)要 AIB_EXT_AUTO=1 才做 —— 不讓每支叫 aib init 的測試都去碰 claude。
    AIB_EXT_AUTO=0 全關。裝不成不影響 init 的結果(只印出來)。"""
    env = os.environ if env is None else env
    flag = env.get("AIB_EXT_AUTO", "")
    if flag == "0" or (flag != "1" and not sys.stdout.isatty()):
        return
    try:
        cockpit = str(Path(__file__).resolve().parents[1] / "cockpit")
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import aib_ext
        aib_ext.auto_install(out=sys.stdout, env=env)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("aib init: aib ext auto: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())
