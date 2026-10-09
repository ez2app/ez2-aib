#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格卡現況摘要——把「寫」接上「讀」(the maintainer 08-18 19:3x 直述;設計稿
docs/reports/DESIGN-persona-digest-20260818.md @ 3e89f28)。

## 這扇門是什麼

綁定是單向 seat→route;新席繼承一張卡拿到的是**卡的內容**,不是**卡累積
的知識**。本模組讓每張人格卡有一份「現況 md」:時間倒序、每條一行人事
時地物+[[連結]]指向真正的內容檔;滿 24 小時整份搬進備存(檔名不改,只
換資料夾),新份頭部 [[上一份]] 接回。下一個繼承這張卡的 AI 讀一份檔就
知道它最近在幹嘛,而且能一路往回翻。

## 陷阱一的抉擇:選 (b) 變體——真相源 append-only,現況 md 是投影

F 的不覆寫閘**一個字都沒動**(本模組完全不 import brain_write_plan,也
不經它寫任何東西)。結構:

    <root>/<persona_uid>/entries.jsonl        ★真相源:append-only,永不覆寫
    <root>/<persona_uid>/<uid>-知識累積-<日期>.md 投影:由 entries 重生成
    <root>/<persona_uid>/備存/<同名>.md         滿 24h 原檔名搬入

「倒序、最新在上」發生在**投影**:投影可以覆寫,因為它隨時可從真相源
逐位元組重建(冪等測試就是這麼釘的)——這與右欄 rowmap/seat-display
同一套哲學:單一真相源+可重建視圖。不選 (a) 獨立可覆寫通道的理由:
(a) 要另造版本+undo+收據三件套,而「可重建」天然就是版本與 undo(重跑
即復原),少一套要維護的機制=少一套會壞的機制。

## 陷阱二:摘要綁人格卡(persona_uid),不綁路由

8 席共用 core.refine、9 席共用 core.handoff——綁路由=八個 AI 同時改同
一個檔。目錄鍵=persona_uid,誰寫的記在條目的 custom.ez2.seat_id 欄。

## 兩條硬約束(the maintainer 08-18 19:45 更正令)

1. **與 `10-現況快照.md` 明確分工**:那是 desktop-shell 的接線狀態機
   (expert-ingress-events.js,管「席位有沒有接上」),本檔管「這張卡
   累積了什麼知識」。檔名因此**不用「現況」二字**(<uid>-知識累積-<日期>
   .md),頭部明寫分工;反向指認由 the maintainer 另單,不動別人的檔。
2. **假設同資料夾有第二個寫入者**:本模組寫的每一份投影都帶機器標記
   (MARKER);覆寫前驗標記,**無標記=別人的檔=拒寫**(DigestError);
   刪除同理(半切自癒只清有標記的殘檔,外人檔案標 blocked_foreign 留在
   原地)。真相源 entries.jsonl 本來就 append-only。陷阱一因此更硬。

## 陷阱三:檔名永不改變

日檔**出生就帶日期**(<uid>-現況-<YYYYMMDD>.md),封存=os.replace 進
備存/,檔名一個字元都不動,[[連結]] 全部只用檔名(Obsidian 按檔名解析,
搬資料夾不斷)。

## 陷阱四:切割冪等+半切偵測

rollover 只處理「日期 < 今天」的日檔;目標已在備存=跳過(重跑不切兩
次)。半切(投影在、備存也在同名檔)= 兩邊都是投影,以真相源重生成後
覆蓋備存並清掉現場那份——資料零風險,因為 md 從來不是真相源。

## 欄位=正本既有(schema 正本 ez2-ai 38d26087),不發明:
custom.ez2.seat_id / custom.ez2.persona_uid / expires_at /
time_sensitivity / related_uids / canonical_uid / memory_type

## 寫入面

獨立模組;**不改 brain_write_plan.py 的閘**(要改閘另開支交 F 擁有者
主審)。root 由呼叫端傳入——真正落到 vault 哪裡、由誰在真寫後呼叫
record(),是 F 擁有者(a seat)的接線決策,不在本檔。

CLI:
    persona_digest.py record  --root R --stdin      # 一行一個 JSON 條目
    persona_digest.py render  --root R --uid U [--now-ms N]
    persona_digest.py rollover --root R [--uid U] [--now-ms N]
rc:0 成功 / 2 用法或條目形狀錯
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time

# ---- 日界線機器=沿用 src/distill/daily-digest.py 本尊(E6.2),不重寫 ----
# the maintainer 08-18 20:13:「24 小時切割、日界線、時區固定的骨架已經有人寫好了,
# 能沿用就沿用」。它檔名帶連字號不能直 import,走 importlib 載同一份碼:
# parse_tz(驗 -12:00~+14:00 不對稱範圍)/local_date(固定偏移,無 DST)/
# local_hms。同一個日界線定義全 repo 只有一份,兩個蒸餾器不會各切各的。
_DD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "distill", "daily-digest.py")
_dd_spec = importlib.util.spec_from_file_location("aib_daily_digest", _DD_PATH)
_dd = importlib.util.module_from_spec(_dd_spec)
_dd_spec.loader.exec_module(_dd)
parse_tz, local_date, local_hms = _dd.parse_tz, _dd.local_date, _dd.local_hms
DEFAULT_TZ_MIN = parse_tz("+08:00")     # 與 daily-digest 同預設:台北日界線

REQUIRED = ("custom.ez2.persona_uid", "ts_ms")
OPTIONAL = ("custom.ez2.seat_id", "action", "route_key", "summary", "link",
            "expires_at", "time_sensitivity", "related_uids",
            "canonical_uid", "memory_type")
ARCHIVE_DIR = "備存"
# 機器標記:本模組認領自己投影的唯一依據(Obsidian 預覽不顯示 HTML 註解)。
# 覆寫/刪除前驗它——無標記=別人的檔=不碰(同夾第二寫入者硬約束)。
MARKER = "<!-- persona-digest/v1:本檔=知識累積;接線狀態看 10-現況快照 -->"
UID_RE = re.compile(r"[0-9A-Za-z._-]{1,64}$")


class DigestError(Exception):
    pass


def _day(ts_ms, tz_min=DEFAULT_TZ_MIN):
    # 舊版用 time.localtime=隨主機時區飄——正是不該自己重寫的那段。
    return local_date(int(ts_ms), tz_min).replace("-", "")


def _card_dir(root, uid):
    if not UID_RE.fullmatch(uid or ""):
        raise DigestError("persona_uid 形狀不合:%r" % (uid,))
    return os.path.join(root, uid)


def _entries_path(root, uid):
    return os.path.join(_card_dir(root, uid), "entries.jsonl")


def digest_name(uid, day):
    # 不用「現況」二字:與 desktop-shell 的 10-現況快照(接線狀態機)分工
    return "%s-知識累積-%s.md" % (uid, day)


def load_entries(root, uid):
    path = _entries_path(root, uid)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue          # 壞行跳過不擋(真相源 append-only,不改寫)
                if isinstance(rec, dict) and all(k in rec for k in REQUIRED):
                    out.append(rec)
    except OSError:
        pass                          # 全新的卡:零條目是誠實狀態,不是錯
    return out


def record(root, entry):
    """追加一條(append-only;真相源永不覆寫),然後重生成當日投影。"""
    if not isinstance(entry, dict):
        raise DigestError("條目必須是 dict")
    for k in REQUIRED:
        if k not in entry:
            raise DigestError("條目缺必要欄 %s(欄位=正本既有,不發明)" % k)
    if not isinstance(entry["ts_ms"], (int, float)):
        raise DigestError("ts_ms 必須是數字")
    unknown = [k for k in entry if k not in REQUIRED + OPTIONAL]
    if unknown:
        raise DigestError("未知欄位 %s——欄位用 schema 正本既有的,不發明" % unknown)
    uid = str(entry["custom.ez2.persona_uid"])
    d = _card_dir(root, uid)
    os.makedirs(d, exist_ok=True)
    line = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(_entries_path(root, uid),
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)            # 單次 write+O_APPEND:行不互插
    finally:
        os.close(fd)
    render(root, uid, now_ms=int(entry["ts_ms"]))   # 預設台北界線;讀端可另 tz
    return uid


def _fmt_entry(e):
    tz = e.get("_tz_min", DEFAULT_TZ_MIN)
    ts = "%s %s" % (local_date(int(e["ts_ms"]), tz),
                    local_hms(int(e["ts_ms"]), tz)[:5])
    seat = e.get("custom.ez2.seat_id") or "?"
    head = "- %s | @%s(%s) | %s | %s" % (
        ts, seat, e["custom.ez2.persona_uid"],
        e.get("action") or "?", e.get("route_key") or "?")
    lines = [head]
    if e.get("summary"):
        lines.append("  %s" % e["summary"])
    extra = []
    for k in ("memory_type", "time_sensitivity", "expires_at", "canonical_uid"):
        if e.get(k):
            extra.append("%s=%s" % (k, e[k]))
    for r in (e.get("related_uids") or []):
        extra.append("related=[[%s]]" % r)
    if extra:
        lines.append("  %s" % " ".join(extra))
    if e.get("link"):
        lines.append("  → [[%s]]" % e["link"])
    return "\n".join(lines)


def _days_of(entries, tz_min=DEFAULT_TZ_MIN):
    return sorted({_day(e["ts_ms"], tz_min) for e in entries})


def _prev_day(root, uid, day, entries, tz_min=DEFAULT_TZ_MIN):
    older = [d for d in _days_of(entries, tz_min) if d < day]
    return older[-1] if older else None


def render_day(root, uid, day, entries=None, tz_min=DEFAULT_TZ_MIN):
    """由真相源重生成某一天的投影內容(純函式性:同輸入同位元組)。"""
    entries = load_entries(root, uid) if entries is None else entries
    for e in entries:
        e["_tz_min"] = tz_min          # 顯示與分日同一個偏移,不各走各的
    todays = sorted((e for e in entries if _day(e["ts_ms"], tz_min) == day),
                    key=lambda e: -e["ts_ms"])      # 倒序:最新在最上面
    head = ["# %s 知識累積 %s-%s-%s" % (uid, day[:4], day[4:6], day[6:]),
            MARKER,
            "本檔=這張人格卡累積的知識(倒序);接線狀態看 10-現況快照。",
            ""]
    prev = _prev_day(root, uid, day, entries, tz_min)
    body = [_fmt_entry(e) for e in todays] or ["(本日尚無條目)"]
    tail = ["", "上一份:[[%s]]" % digest_name(uid, prev)[:-3] if prev
            else "上一份:(這是第一份)"]
    return "\n".join(head + body + tail) + "\n"


def _write_md(path, content):
    # 同夾第二寫入者硬約束:目標已存在且**沒有本模組標記**=別人的檔,
    # 整筆拒(絕不整檔覆寫別人的東西;檔名撞上就是要人來裁,不是搶)。
    if os.path.exists(path):
        try:
            head = open(path, encoding="utf-8", errors="replace").read(4096)
        except OSError as err:
            raise DigestError("目標讀不到,拒寫:%s(%s)" % (path, err))
        if MARKER not in head:
            raise DigestError("目標存在但不是本模組的投影(無標記),拒絕覆寫:%s" % path)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)             # 投影可覆寫:可由真相源逐位元組重建


def render(root, uid, now_ms=None, tz_min=DEFAULT_TZ_MIN):
    """重生成「今天」的現況 md;全新的卡=誠實空檔,不假裝有。回路徑。"""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    day = _day(now_ms, tz_min)
    d = _card_dir(root, uid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, digest_name(uid, day))
    _write_md(path, render_day(root, uid, day, tz_min=tz_min))
    return path


def rollover(root, uid=None, now_ms=None, tz_min=DEFAULT_TZ_MIN):
    """封存:日期<今天的日檔搬進 備存/(檔名不改,只換資料夾)。冪等:
    已在備存=跳過;半切(兩邊同名)=以真相源重生成蓋備存、清現場那份
    (md 是投影不是真相源,資料零風險)。回動作清單。"""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    today = _day(now_ms, tz_min)
    uids = [uid] if uid else sorted(
        n for n in (os.listdir(root) if os.path.isdir(root) else [])
        if os.path.isdir(os.path.join(root, n)) and UID_RE.fullmatch(n))
    actions = []
    for u in uids:
        d = _card_dir(root, u)
        arch = os.path.join(d, ARCHIVE_DIR)
        entries = load_entries(root, u)
        for day in _days_of(entries, tz_min):
            if day >= today:
                continue
            name = digest_name(u, day)
            src, dst = os.path.join(d, name), os.path.join(arch, name)
            content = render_day(root, u, day, entries, tz_min=tz_min)
            os.makedirs(arch, exist_ok=True)
            if os.path.exists(dst):
                if os.path.exists(src):          # 半切:兩邊都在
                    src_head = open(src, encoding="utf-8",
                                    errors="replace").read(4096)
                    if MARKER not in src_head:
                        # 外人檔案佔了我們的名字空間:不刪不蓋,標記留痕
                        actions.append(("blocked_foreign", src))
                        continue
                    _write_md(dst, content)
                    os.remove(src)               # 只清**有標記**的自家殘檔
                    actions.append(("healed", dst))
                continue                          # 已封存:冪等跳過
            _write_md(src, content)               # 沒渲染過的舊日也補齊
            os.replace(src, dst)                  # 原檔名,只換資料夾
            actions.append(("archived", dst))
        render(root, u, now_ms=now_ms, tz_min=tz_min)  # 今天的新份(頭接上一份)
    return actions


def registry_consistency(bindings_doc, registry_doc):
    """驗收第五條(pm2 提、艦長 08-18 裁):digest 可寫席數與 category-
    registry 登記數一致。digest 條目來自 F 真寫成功,而 F 閘一只放「分類
    已登記」的路由——所以「能在 digest 留下條目的席」按定義=綁在已登記
    路由上的席。兩數不一致=有席看起來綁好了、其實它的寫永遠進不了
    digest(L6-06 那顆雷的 digest 版,提前到帳面就看得見)。純函式。"""
    registered = {c.get("routeKey")
                  for c in (registry_doc.get("categories") or [])
                  if isinstance(c, dict) and not c.get("deprecated")}
    binds = bindings_doc.get("bindings") or {}
    capable = sorted(s for s, b in binds.items()
                     if isinstance(b, dict) and b.get("route_key") in registered)
    blocked = sorted(s for s in binds if s not in capable)
    used_registered = {b.get("route_key") for b in binds.values()
                      if isinstance(b, dict)} & registered
    return {"digest_capable": len(capable), "capable_seats": capable,
            "blocked_seats": blocked,
            "registered_routes_in_use": len(used_registered),
            "consistent": not blocked}


def find_links(md_text):
    return re.findall(r"\[\[([^\][]+)\]\]", md_text or "")


def verify_links(md_path, search_dirs):
    """[[連結]] 逐一按檔名解析(Obsidian 語意:檔名唯一,資料夾不算)。
    回解析不到的清單——測試用它證明「搬檔後連結仍走得到」是實測不是宣稱。"""
    text = open(md_path, encoding="utf-8").read()
    missing = []
    for link in find_links(text):
        base = link.split("|", 1)[0].strip()
        found = False
        for root_dir in search_dirs:
            for cur, _dirs, files in os.walk(root_dir):
                if ("%s.md" % base) in files or base in files:
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(base)
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser(prog="persona-digest")
    ap.add_argument("command", choices=["record", "render", "rollover"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--uid")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--tz", default="+08:00",
                    help="日界線固定偏移(同 daily-digest 預設台北)")
    ap.add_argument("--stdin", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.command == "record":
            if not args.stdin:
                print("record 需要 --stdin(一行一個 JSON 條目)", file=sys.stderr)
                return 2
            for ln in sys.stdin:
                if ln.strip():
                    record(args.root, json.loads(ln))
            return 0
        tz_min = parse_tz(args.tz)
        if args.command == "render":
            if not args.uid:
                print("render 需要 --uid", file=sys.stderr)
                return 2
            print(render(args.root, args.uid, now_ms=args.now_ms,
                         tz_min=tz_min))
            return 0
        acts = rollover(args.root, uid=args.uid, now_ms=args.now_ms,
                        tz_min=tz_min)
        print(json.dumps([{"op": a, "path": p} for a, p in acts],
                         ensure_ascii=False))
        return 0
    except (DigestError, ValueError) as err:
        print("persona-digest: %s" % err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
