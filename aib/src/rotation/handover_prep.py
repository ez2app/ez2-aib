#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交棒前置(the maintainer 08-18 21:1x 原話:「如果 clear,有什麼是下一個 AI 不知道
的?自行最後一次更新多記憶,然後執行 compact」;21:3x 補充:做成**獨立
功能,他說了才觸發**——不進 STAGES,不綁門檻,任何 CTX 都能跑)。

## 三步

1. **自問落檔**:走 `rotation.write_testament` **本尊**(G1-06 遺詔協定,
   全艦目前 0 本——這一刀啟用它,不另造第二套 jsonl;the maintainer 訊息裡寫的
   落點 testament.jsonl 即指這套「寫好從沒人用過」的協定,實際正本形狀
   =`rotation/<seat>/testament/<at_ms>.md` + receipts,沿用不重寫,同
   20:13 直令;若 the maintainer 要的字面就是 jsonl,一行可改,候裁)。
   本模組在協定六必含節之上**多要求一節**:`## 下一個 AI 不會知道的事`
   且正文非空——沒有它,自問等於沒發生(rc=2,一 byte 不落盤)。
2. **三記憶驗新**:AI 記憶 / AIB 記憶 / Obsidian 三個路徑逐一驗最近
   mtime(視窗 --memory-window-min,預設 45 分)。本工具**不代寫記憶**
   (寫的是席位 AI 自己),只做機器收據:fresh / stale / UNRESOLVED
   (路徑不存在=查不到,不猜)。任一不 fresh → 印出「先更新哪一個再
   重跑」,rc=3,**不進第三步**。
3. **compact 交代**:引擎條件**機器判**(讀席位檔 provider,不寫在註解
   靠人記得):claude → 印「現在請執行 compact」(本工具**自己不執行**
   ——compact 是 CLI 動作,印不出來就是沒交代);其他引擎 → 明確印
   「第三步 compact:本引擎(<provider>)不適用」——**不得靜默跳過**。

## 生命週期:只建議不執行

`suggest(ctx_pct)` 是純函式:>=85 回提示句,否則 None。lifecycle 那邊
(a seat 的檔,本模組**不改 STAGES 一個字**)要掛就 import 這支;
按下去的永遠是人。

CLI:
    handover_prep.py --seat <seat_id> --root <rotation根> \\
        (--file 遺詔.md | --stdin) [--now-ms N] [--receipt R] \\
        [--mem-ai P] [--mem-aib P] [--mem-obsidian P] [--memory-window-min 45]
rc:0=三步齊(或第三步明確不適用) / 3=遺詔已落但三記憶未齊(不進第三步)
   / 2=用法錯、自問空、席位查不到(引擎不猜)
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "cockpit"))
sys.path.insert(0, os.path.join(_HERE, "..", "ledger"))

import rotation as R                                        # noqa: E402
import ctx_policy                                           # noqa: E402

ASK_SECTION = "下一個 AI 不會知道的事"
UNRESOLVED = "UNRESOLVED"
MEMORY_KEYS = (("ai", "AI 記憶"), ("aib", "AIB 記憶"), ("obsidian", "Obsidian"))
# ★ 「共用面」:整個艦隊寫進同一個目錄(AI 記憶 = ~/.claude/projects/<proj>/memory/)。
#   結構事實(量到的,非命名推論):AI 記憶根**零子目錄**(56 席平鋪在同一層),
#   AIB rotation 有 **18 個 per-seat 子目錄**。→ AI 面在結構上**沒有 per-seat 目錄層**,
#   歸屬在**檔**;所以 --mem-ai 必須指向『這一席自己那份檔』,指向目錄工具無從得知是誰的 → UNRESOLVED。
SHARED_MEMORY_KEYS = frozenset(["ai"])

# 記憶產物:只有這些副檔名算「記憶」。per-seat 目錄裡量的是這類檔的最新一份。
MEMORY_ARTIFACT_SUFFIXES = (".md",)
# ★ 共享索引:每一席寫記憶都會動到它(兩個面都有),所以它的新鮮度**不是任何單一席的**
#   ——量到它 = 「艦上有人在寫記憶」就算你有寫。第一段過濾一律排除它(艦長 02:18)。
SHARED_INDEX_NAMES = frozenset(["memory.md"])


def _is_memory_artifact(name):
    n = name.lower()
    return n.endswith(MEMORY_ARTIFACT_SUFFIXES) and n not in SHARED_INDEX_NAMES


def _newest_memory_artifact(d):
    """per-seat 目錄裡最新的『記憶產物』(.md,排除共享索引);沒有就回 None。
    ★ 這是契約式設計裡**唯一的鑑別器**(dev5 §二):比對器已刪,歸屬由呼叫端宣告,
      只剩「這是不是我要量的那類產物」要在目錄粒度回答。"""
    newest = None
    for cur, _sub, files in os.walk(d):
        for f in files:
            if not _is_memory_artifact(f):
                continue
            fp = os.path.join(cur, f)
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            if newest is None or mt > newest[1]:
                newest = (fp, mt)
    return newest[0] if newest else None


def _mem_unresolved(key, label, path, remedy):
    """UNRESOLVED 收據。★ 補救寫在同一格(dev5 R2):fail-closed 訊息若不自證要怎麼修,
    會被讀成它守護的東西壞了——改成契約式後今天所有人的舊叫法都會拿到 UNRESOLVED。"""
    return {"label": label, "path": path, "status": UNRESOLVED,
            "age_min": UNRESOLVED, "measured": UNRESOLVED,
            "declared_granularity": UNRESOLVED, "remedy": remedy}


def suggest(ctx_pct):
    """生命週期**建議**(不執行):過 `stages.should_converge` 提示考慮交棒前置。

    掛進 lifecycle 與否是它擁有者的事;本函式純、無副作用、不碰 STAGES。

    ★ EH.28:原本這裡是**寫死字面 `85`**(不是常數),而同一個 85 在
      `lifecycle.STAGES` 與 `actions.captain_compact` 各有一份。三份今天碰巧同值,
      所以全綠;資料化之後只要有人改其中一份,這裡**不會跟**,而且沒有紅燈。
    ★ 綁的是 `stages.should_converge`(收斂階段)而**不是**
      `actions.captain_compact` —— 本函式給的是「該考慮交棒前置」的建議,
      不是「艦長該送 /compact」的動作。兩者今天同值,語意不同。
      **此處鍵的選擇是實作者的判斷,已在交付說明裡點名候確認。**
    """
    if isinstance(ctx_pct, bool) or not isinstance(ctx_pct, int):
        return None
    if ctx_pct >= ctx_policy.stage("should_converge"):
        return ("CTX %d%%:該考慮跑交棒前置了(handover_prep;"
                "按下去的是人,不是這行字)" % ctx_pct)
    return None


def provider_of_seat(seat_id):
    """引擎條件的機器判:掃席位檔取 provider。查不到=None(不猜)。"""
    import seat_who
    for _inst, _path, rec in seat_who._iter_seats():
        if str(rec.get("seat_id") or "").lower() == (seat_id or "").lower():
            return rec.get("provider") or None
    return None


def memory_status(paths, window_min, seat=None, now_s=None):
    """三記憶機器收據:每個 {path, status, age_min, measured, declared_granularity[, remedy]}。
    status ∈ fresh | stale | UNRESOLVED。

    ★ pm2 08-19 假綠單起、五席五洞、修法方向被真環境數字否掉四次,最後的答案是**把比對器刪掉**:
      舊版對目錄取 max(mtime)→ 共用目錄裡別席剛寫的檔讓這一席變綠(量『有人新』宣稱『你新』)。
      而「這是不是你的」這件事**不在任何可觀察的性質裡**——命名(seat_id 兩套且與 hex 重疊)、
      路徑(AI 面沒有 per-seat 層)、結構(per-seat 目錄自己就有扁平/巢狀兩形)三把尺各在自己的
      實例上看起來合理,卻都在別的實例上失敗。**角色不在被觀察的東西裡,所以不去推論它。**

    **契約式(歸屬由呼叫端宣告,工具不猜)**:
      - `--mem-ai`(SHARED_MEMORY_KEYS)**只收檔**:AI 面結構上沒有 per-seat 目錄層,
        傳目錄工具無從得知那 56 席的檔哪個是你的 → UNRESOLVED(不猜、不取最新)。
      - `--mem-aib` / `--mem-obsidian` 收**呼叫端明確指名的那一席的目錄**:量最新的
        『記憶產物』(.md,排除共享索引 MEMORY.md)——**第一段(產物種類)是唯一的鑑別器**;
        沒有記憶產物 → UNRESOLVED。**不判那個目錄是不是共用的**(那正是工具物理上不知道的)。
      - 傳**檔**:直接量它——歸屬是呼叫端宣告的,**本工具不驗證你的宣告為真,只保證量到的是你宣告的那個**。
    收據:`declared_granularity` ∈ file|dir(呼叫端宣告的粒度,不是『已驗證屬於該席』);
      UNRESOLVED 一律帶 `remedy`(補救寫在同一格)——改成契約式後今天所有舊叫法(6/6 傳目錄)
      都會拿到 UNRESOLVED,fail-closed 訊息不自證會被讀成它守護的東西壞了。"""
    now_s = time.time() if now_s is None else now_s
    out = {}
    for key, label in MEMORY_KEYS:
        p = paths.get(key)
        want = "檔" if key in SHARED_MEMORY_KEYS else "目錄"
        if not p or not os.path.exists(p):
            out[key] = _mem_unresolved(key, label, p or UNRESOLVED,
                                       "路徑不存在(查不到≠沒更新);請把 --mem-%s 指向這一席的記憶%s"
                                       % (key, want))
            continue
        gran = "dir" if os.path.isdir(p) else "file"
        if os.path.isdir(p):
            if key in SHARED_MEMORY_KEYS:
                # ★ 契約:AI 面在結構上沒有 per-seat 目錄層(共用目錄含 56 席的檔),
                #   工具在物理上無法知道哪個是你的 → 不猜、不取最新 → UNRESOLVED。
                #   歸屬由呼叫端宣告:請把 --mem-ai 指向『這一席自己那份記憶檔』,不是目錄。
                out[key] = _mem_unresolved(key, label, p,
                                           "AI 面是共用目錄(含多席的檔),工具不猜是誰的;"
                                           "請把 --mem-ai 指向『這一席自己那份記憶檔』,不是目錄")
                continue
            # per-seat 目錄:量最新『記憶產物』(.md,排除共享索引 MEMORY.md)=唯一鑑別器。
            measured = _newest_memory_artifact(p)
            if measured is None:
                out[key] = _mem_unresolved(key, label, p,
                                           "目錄裡沒有記憶產物(.md,已排除共享索引 MEMORY.md);"
                                           "請確認 --mem-%s 指向的目錄有這一席的記憶檔" % key)
                continue
        else:
            measured = p          # 傳檔:量它——歸屬是呼叫端宣告的,工具不驗證宣告為真
        age_min = (now_s - os.path.getmtime(measured)) / 60.0
        out[key] = {"label": label, "path": p,
                    "status": "fresh" if age_min <= window_min else "stale",
                    "age_min": round(age_min, 1),
                    "measured": os.path.basename(measured),
                    # ★ declared_granularity(取代 seat_specific):呼叫端宣告的粒度(檔/目錄),
                    #   不是『已驗證屬於該席』——本工具不驗證你的宣告為真,只保證量到的是你宣告的那個。
                    "declared_granularity": gran}
    return out


def run(seat, root, text, now_ms=None, receipt=None, mem_paths=None,
        window_min=45, provider=None, out=None):
    """三步主流程。回 rc(印出即交代;out 供測試截流)。"""
    say = (out or sys.stdout).write
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    # 引擎先判(機器,不猜):查不到就不動任何檔——連遺詔都不落,
    # 免得「身份不明的席」留下掛在別席名下的遺詔。
    prov = provider or provider_of_seat(seat)
    if not prov:
        say("handover-prep: 席位 %s 查不到 provider(引擎不猜),中止\n" % seat)
        return 2
    # 第一步:自問落檔——先驗本模組的加嚴節,再交協定本尊(六必含節)
    try:
        sections = (R.validate_testament_text(text)["sections"]
                    if text and text.strip() else [])
    except R.RotationError as err:
        say("handover-prep: 遺詔不合 G1-06 契約:%s\n" % err)
        return 2
    if ASK_SECTION not in sections:
        say("handover-prep: 遺詔缺「## %s」節——自問沒有落檔就等於沒發生"
            "(rc=2,一 byte 不落盤)\n" % ASK_SECTION)
        return 2
    body = _section_body(text, ASK_SECTION)
    if not body.strip():
        say("handover-prep: 「## %s」正文為空——空自問=沒自問\n" % ASK_SECTION)
        return 2
    try:
        t = R.write_testament(root, seat, now_ms, text, receipt=receipt)
    except R.RotationError as err:
        say("handover-prep: 遺詔落檔失敗:%s\n" % err)
        return 2
    say("第一步 ✓ 遺詔已落檔:%s(sha %s…)\n"
        % (t["testament_file"], t["testament_sha256"][:12]))
    # 第二步:三記憶驗新
    mems = memory_status(mem_paths or {}, window_min, seat=seat)
    not_ready = []
    for key, _label in MEMORY_KEYS:
        m = mems[key]
        # measured=實際量到哪個檔、declared_granularity=呼叫端宣告的粒度(檔/目錄)。
        # ★ UNRESOLVED 一律把補救印在同一格(dev5 R2):不是安靜地回 UNRESOLVED——
        #   「這一格沒有被回答」要說出來,否則 fail-closed 會被讀成它守護的東西壞了。
        say("第二步 %s %s:%s(path=%s measured=%s granularity=%s age=%s)%s\n"
            % ("✓" if m["status"] == "fresh" else "✗",
               m["label"], m["status"], m["path"], m["measured"],
               m["declared_granularity"], m["age_min"],
               ("\n    ↳ 這一格沒有被回答:%s" % m["remedy"]) if m.get("remedy") else ""))
        if m["status"] != "fresh":
            not_ready.append(m)
    if not_ready:
        say("handover-prep: 三記憶未齊(%s)——請先更新上列 ✗ 項再重跑;"
            "不進第三步\n" % "、".join(m["label"] for m in not_ready))
        return 3
    # 第三步:compact 交代(引擎條件機器判;本工具自己不執行)
    if prov == "claude":
        say("第三步:現在請執行 compact(本工具不代按;交棒前置已齊)\n")
    else:
        say("第三步 compact:本引擎(%s)不適用——不是跳過,是不適用;"
            "前兩步已齊,交棒前置到此完成\n" % prov)
    return 0


def _section_body(text, title):
    lines, keep = [], False
    for ln in text.split("\n"):
        if ln.startswith("## "):
            keep = (ln[3:].strip() == title)
            continue
        if keep:
            lines.append(ln)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="handover-prep",
                                 description="交棒前置:自問落檔→三記憶驗新→compact 交代")
    ap.add_argument("--seat", required=True)
    ap.add_argument("--root", required=True, help="rotation 根目錄")
    ap.add_argument("--file", help="遺詔全文 md")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--receipt")
    ap.add_argument("--mem-ai", default=os.environ.get("EZ2_MEM_AI_DIR"))
    ap.add_argument("--mem-aib", default=os.environ.get("EZ2_MEM_AIB_DIR"))
    ap.add_argument("--mem-obsidian",
                    default=os.environ.get("EZ2_MEM_OBSIDIAN_DIR"))
    ap.add_argument("--memory-window-min", type=int, default=45)
    args = ap.parse_args(argv)
    if args.file:
        text = open(args.file, encoding="utf-8").read()
    elif args.stdin:
        text = sys.stdin.read()
    else:
        print("需要 --file 或 --stdin(遺詔全文)", file=sys.stderr)
        return 2
    try:
        return run(args.seat, args.root, text, now_ms=args.now_ms,
                   receipt=args.receipt,
                   mem_paths={"ai": args.mem_ai, "aib": args.mem_aib,
                              "obsidian": args.mem_obsidian},
                   window_min=args.memory_window_min)
    except R.RotationError as err:
        print("handover-prep: %s" % err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
