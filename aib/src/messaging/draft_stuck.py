#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""卡稿探針(車道 4 第 3 件):框內有佔位符 **且編號沒變** 才叫 STUCK。

## 為什麼要多一個狀態

巡查現在只印「有沒有草稿」。**「有草稿」與「卡住」是兩個狀態**,處置相反:
有草稿 = 對方正在打字,別碰;卡住 = 我們自己貼進去的稿 Enter 被吃掉了
(a seat 09-03 兩次實例,一次卡 9 分鐘、一次卡 26 分鐘),要有人處理。
把兩者印成同一個字,等於把「要處理的事」藏在一個看起來正常的欄位裡。

## 判準(兩條腿,任一條不成立都不得印 STUCK)

1. **同一枚**:這一輪解析出來的佔位符清單,與上一輪**逐字相同**(含 `#N` 編號)。
   編號變了 = 對方又貼了新的一份 = 不是卡住。**這是必落空端**:沒有它
   STUCK 會退化成「有草稿」的同義詞,而那正是現在的病。
2. **連續**:與上一次觀測的間隔 ≤ `--max-gap-sec`(預設等於門檻)。
   中斷 20 分鐘再看到同一枚,我知道的只有兩個端點,不知道中間;
   **不知道不得寫成 STUCK**。

## 只讀輸入框那一行,不讀整張畫面(09-04 誘餌對照抓到,原版是錯的)

第一版解析**整張畫面**,於是畫面上任何地方出現的佔位符字面都被算成草稿——
而 `aib-dm` 自己每次都會印 `貼前佔位符=[Pasted text #N …]`,所以一個**完全閒置**的席
會被判成 HASDRAFT,連續兩輪變 STUCK。失效方向=恆有(誘餌對照:字面只在框外 ⇒ 仍回 HASDRAFT)。
修法是用產品既有的 `composer_state.find_composer_line`(它已經守著「最後一個提示符行、
且必須在畫面尾端」以及 grok 的畫框式輸入行),**不自己再發明一個定位器**。
定不出輸入框 ⇒ `UNKNOWN`,不是 `NODRAFT`:那是 `composer_state` 自己的紀律
(「我看到的」不等於「那裡有的」)。

## 失效值不得等於它表示安全的那個值

- 畫面取不到(`--capture-rc` 非 0)→ `UNKNOWN` rc=3,**且不動狀態檔**
  (讀不到畫面就把時鐘歸零,等於讓一次失敗的觀測洗掉真的卡稿)。
- 狀態目錄寫不進去 → `UNKNOWN` rc=3。理由:寫不進去 = 下一輪沒有歷史
  = 永遠到不了 STUCK,而那在版面上與「一切正常」逐字相同。
- 狀態檔壞掉 → `UNKNOWN` rc=3,同時**重寫**一份乾淨的,下一輪自己好。

佔位符文法**不自己寫一份**,直接用 `dm_paste.parse`(同一鏈上「文法三份」的疤
還在該檔檔頭)。

## 用法

    python3 src/messaging/draft_stuck.py probe --key <窗> --capture-rc 0 < 畫面
    python3 src/messaging/draft_stuck.py window --socket aihub-… --window @11

stdout 預設**只有一個字**(給巡查當欄位);`--json` 印整包讀數。
rc:0=判得出來(STUCK/HASDRAFT/NODRAFT)、3=判不出來(UNKNOWN)、2=用法錯。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cockpit"))
import dm_paste  # noqa: E402  佔位符文法唯一來源
import composer_state  # noqa: E402  輸入框定位唯一來源(不自己再發明一個)

STUCK = "STUCK"
HASDRAFT = "HASDRAFT"
NODRAFT = "NODRAFT"
UNKNOWN = "UNKNOWN"

DEFAULT_THRESHOLD_SEC = 300          # 艦長裁定的「5 分鐘沒變」
_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9_.@=-]")


def default_state_dir():
    root = os.environ.get("EZ2_AIB_HOME") or os.path.join(
        os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(root, "state", "draft-stuck")


def state_path(state_dir, key):
    """狀態檔路徑。key 先過白名單再落檔:窗號帶 `@`、socket 帶 `-`,其餘一律換掉。

    ★ 用白名單不用黑名單:黑名單擋得住今天想得到的那幾個字元,
      而 `../` 只需要一個沒想到的組合就穿出去。
    """
    safe = _KEY_SAFE_RE.sub("_", key or "")
    if not safe:
        raise ValueError("draft_stuck:--key 不得為空(它是狀態檔的鍵)")
    return os.path.join(state_dir, safe + ".json")


def read_state(path):
    """→ (dict 或 None, 壞掉沒)。檔不存在=沒有歷史(不是壞掉)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        return None, False
    except (OSError, ValueError):
        return None, True
    if not isinstance(obj, dict) or not isinstance(obj.get("raws"), list):
        return None, True
    return obj, False


def write_state(path, obj):
    """→ None=寫成功;字串=失敗理由。寫完**讀回來比**,不拿「呼叫沒丟例外」當寫成功。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        return "狀態檔寫不進去(%s):%s" % (path, exc)
    back, broken = read_state(path)
    if broken or back is None:
        return "狀態檔寫了但讀不回來(%s)" % path
    return None


def clear_state(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        return "狀態檔刪不掉(%s):%s" % (path, exc)
    return None


def classify(screen, capture_ok, prev, now, threshold_sec, max_gap_sec,
             provider=None):
    """純函式:(畫面, 觀測成功沒, 上一輪狀態, 現在, 門檻, 最大間隔) → 讀數 dict。

    不碰檔案系統——**判準要可以在沒有磁碟的情況下被問**,落檔是呼叫端的事。
    """
    if not capture_ok:
        return {"state": UNKNOWN, "reason": "capture-failed",
                "detail": "畫面取不到:取不到不等於框是空的,不表態、不動狀態檔",
                "raws": None, "elapsed_sec": None, "touch_state": False}
    line, _idx = composer_state.find_composer_line(screen, provider=provider)
    if line is None:
        # 定不出輸入框 ⇒ 不表態。★ 這一格不得回 NODRAFT:
        #   「我沒找到框」與「框是空的」是兩件事,合併就是失效值等於安全值。
        return {"state": UNKNOWN, "reason": "composer-not-located",
                "detail": "定不出輸入框(捲軸上只有舊回顯,或這一席的提示符本模組不認得):"
                          "不表態、不動狀態檔",
                "raws": None, "elapsed_sec": None, "touch_state": False}
    raws = [p["raw"] for p in dm_paste.parse(line)]
    if not raws:
        return {"state": NODRAFT, "reason": "no-placeholder",
                "detail": "框內無佔位符", "raws": [], "elapsed_sec": None,
                "touch_state": "clear"}
    same = bool(prev) and list(prev.get("raws") or []) == raws
    gap = None if not prev else now - int(prev.get("last") or now)
    continuous = same and gap is not None and gap <= max_gap_sec
    if continuous:
        since = int(prev.get("since") or now)
        reason = "same-placeholder"
    else:
        since = now
        if not prev:
            reason = "first-sight"
        elif not same:
            reason = "placeholder-changed"
        else:
            reason = "observation-gap"
    elapsed = now - since
    state = STUCK if elapsed >= threshold_sec else HASDRAFT
    return {"state": state, "reason": reason,
            "detail": "佔位符 %s;連續 %d 秒未變(門檻 %d)"
                      % (" ".join(raws), elapsed, threshold_sec),
            "raws": raws, "elapsed_sec": elapsed, "since": since,
            "gap_sec": gap, "touch_state": True}


def probe(screen, capture_ok, key, state_dir, now,
          threshold_sec=DEFAULT_THRESHOLD_SEC, max_gap_sec=None, provider=None):
    """落檔版:讀狀態 → classify → 寫狀態。儀器失敗一律 UNKNOWN,不退回好看的值。"""
    if max_gap_sec is None:
        max_gap_sec = threshold_sec
    path = state_path(state_dir, key)
    prev, broken = read_state(path)
    out = classify(screen, capture_ok, prev, now, threshold_sec, max_gap_sec,
                   provider=provider)
    if out["touch_state"] == "clear":
        err = clear_state(path)
        if err:
            return {"state": UNKNOWN, "reason": "state-unwritable",
                    "detail": err, "raws": out["raws"], "elapsed_sec": None}
        return out
    if out["touch_state"]:
        err = write_state(path, {"raws": out["raws"], "since": out["since"],
                                 "last": now})
        if err:
            # ★ 寫不進去 = 下一輪沒有歷史 = 永遠到不了 STUCK。
            #   那個「永遠到不了」在版面上與「沒有卡稿」逐字相同,所以要吵。
            return {"state": UNKNOWN, "reason": "state-unwritable",
                    "detail": err, "raws": out["raws"], "elapsed_sec": None}
        if broken:
            # 壞檔已被重寫,但**這一輪的讀數建立在沒有歷史之上**,不宣稱。
            return {"state": UNKNOWN, "reason": "state-corrupt",
                    "detail": "舊狀態檔壞掉,已重寫;本輪無歷史可比,不表態",
                    "raws": out["raws"], "elapsed_sec": None}
    return out


def capture(socket, window, tmux="tmux"):
    """→ (畫面, 取到沒)。rc 緊接著取,中間一行都不放。"""
    # ★ 帶 -e:輸入框定位與 composer_state 同源,而它靠 SGR 分辨 dim 建議與真稿。
    try:
        r = subprocess.run(
            [tmux, "-L", socket, "capture-pane", "-e", "-p", "-t", window],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        return "", False, "tmux 起不來:%s" % exc
    return r.stdout, r.returncode == 0, (r.stderr or "").strip()


def _emit(out, as_json):
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(out["state"])
    return 3 if out["state"] == UNKNOWN else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="draft_stuck.py", add_help=True)
    ap.add_argument("mode", choices=("probe", "window"))
    ap.add_argument("--key", default=None)
    ap.add_argument("--socket", default=None)
    ap.add_argument("--window", default=None)
    ap.add_argument("--capture-rc", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--threshold-sec", type=int, default=DEFAULT_THRESHOLD_SEC)
    ap.add_argument("--max-gap-sec", type=int, default=None)
    ap.add_argument("--now", type=int, default=None)
    ap.add_argument("--tmux", default="tmux")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    now = args.now if args.now is not None else int(time.time())
    state_dir = args.state_dir or default_state_dir()

    if args.mode == "probe":
        if args.capture_rc is None:
            ap.error("probe 需要 --capture-rc:畫面取到沒是**呼叫端**才知道的事,"
                     "本檔不從空字串去猜(空畫面與沒取到同形)")
        key = args.key
        screen = sys.stdin.read()
        capture_ok = str(args.capture_rc) == "0"
    else:
        if not args.socket or not args.window:
            ap.error("window 模式需要 --socket 與 --window")
        key = args.key or ("%s%s" % (args.socket, args.window))
        screen, capture_ok, err = capture(args.socket, args.window, args.tmux)
        if not capture_ok and err:
            print("draft_stuck: capture 失敗:%s" % err, file=sys.stderr)
    try:
        out = probe(screen, capture_ok, key, state_dir, now,
                    args.threshold_sec,
                    args.max_gap_sec if args.max_gap_sec is not None
                    else args.threshold_sec,
                    provider=args.provider)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _emit(out, args.json)


if __name__ == "__main__":
    sys.exit(main())
