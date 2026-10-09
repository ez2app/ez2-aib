#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""派工前的並行安全閘(the maintainer 2026-08-27 02:0x 裁定程式化)。

原則:所有大綱重整與派工,都必須以「多 AI 同時共同開發、彼此不影響、一同開始」
為前提。本工具把這個前提變成機器判準:給一份派工清單(JSON),逐對檢查——

  1. 寫入範圍相交 → 紅,點名哪兩包、撞在哪個路徑(前綴目錄也算相交:
     A 寫 src/x/ 而 B 寫 src/x/y.py = 相交)。
  2. 分支撞名 → 紅(兩包寫同一分支=必然互蓋)。
  3. 同席兩包 → 警告不紅(同席天生序列執行,不是並行衝突;但要知道它在排隊)。
  4. 清單格式壞 → rc=2 點名壞在哪一格(fail-closed,不靜默跳過)。

  5. --require-live(#337,a seat 09-26):逐席量「收不收得到經佇列的派工」(tools/dispatch/seat_liveness.py);
     收不到 ⇒ 拒派報人。不帶旗標時行為逐字不變。本閘不判斷那一席死了沒有,只照實報量到什麼。

rc:0=可並行一同開始 / 1=有衝突(逐條點名) / 2=清單本身不合法 / 3=寫集可並行但有席收不到派工。
清單格式:{"packages":[{"id","seat","branch","writes":["路徑或目錄前綴", ...]}, ...]}
writes 為空陣列=唯讀包,免相交檢查(仍查分支)。
"""
import pathlib
import os
import json
import sys

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 十三條地基逐字列著「i18n 是地基」。
# ★★ 順序是承重的(33f17 抓到):連續兩次 insert(0) 會讓後 insert 的排前面 ⇒ 主庫贏本樹,
#   而 i18n_lib 的字典根綁 __file__ ⇒ 會讀回主庫舊字典。本樹 insert(0)、fallback append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)



def _norm(p):
    p = p.strip()
    if not p or p.startswith("/"):
        return None
    return p.rstrip("/")


def _overlap(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def live_report(pkgs):
    """--require-live:每個不同的席各量一次。回收不到派工的席數。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import seat_liveness  # noqa: E402

    refused = 0
    for seat in sorted({p["seat"] for p in pkgs}):
        r = seat_liveness.probe(seat)
        why = "; ".join(T("aib.parallel.live.reason." + k, v) for k, v in r["reasons"])
        if r["ok"]:
            print(T("aib.parallel.live.ok", {"seat": seat, "reason": why}))
        else:
            refused += 1
            print(T("aib.parallel.live.refuse", {"seat": seat, "reason": why}))
    return refused


def main():
    args = sys.argv[1:]
    require_live = "--require-live" in args
    args = [a for a in args if a != "--require-live"]
    if len(args) != 1:
        print(T("aib.parallel.usage"), file=sys.stderr)
        return 2
    try:
        data = json.load(open(args[0], encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(T("aib.parallel.unreadable", {"err": e}), file=sys.stderr)
        return 2
    pkgs = data.get("packages")
    if not isinstance(pkgs, list) or not pkgs:
        print(T("aib.parallel.no_packages"), file=sys.stderr)
        return 2
    for i, p in enumerate(pkgs):
        for key in ("id", "seat", "branch", "writes"):
            if key not in p:
                print(T("aib.parallel.missing_key", {"i": i, "key": key}), file=sys.stderr)
                return 2
        if not isinstance(p["writes"], list):
            print(T("aib.parallel.writes_type", {"i": i}), file=sys.stderr)
            return 2
        for w in p["writes"]:
            if _norm(w) is None:
                print(T("aib.parallel.bad_path", {"i": i, "name": p.get("id", "?"), "path": repr(w)}), file=sys.stderr)
                return 2

    reds, warns = [], []
    for i in range(len(pkgs)):
        for j in range(i + 1, len(pkgs)):
            a, b = pkgs[i], pkgs[j]
            if a["branch"] == b["branch"]:
                reds.append("分支撞名:%s 與 %s 都寫 %s"
                            % (a["id"], b["id"], a["branch"]))
            if a["seat"] == b["seat"]:
                warns.append("同席排隊:%s 與 %s 都在 %s(序列執行,非並行)"
                             % (a["id"], b["id"], a["seat"]))
            for wa in a["writes"]:
                for wb in b["writes"]:
                    na, nb = _norm(wa), _norm(wb)
                    if _overlap(na, nb):
                        reds.append("寫入相交:%s(%s)× %s(%s)"
                                    % (a["id"], na, b["id"], nb))
    for w in warns:
        print("⚠ " + w)
    refused = live_report(pkgs) if require_live else 0
    if reds:
        for r in reds:
            print("🔴 " + r)
        print(T("aib.parallel.not_ok"))
        return 1
    if refused:
        print(T("aib.parallel.live.not_ok", {"n": refused}))
        return 3
    print(T("aib.parallel.ok", {"n": len(pkgs)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
