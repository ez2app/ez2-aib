#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""L8b:輸入框被別人的稿卡住時,通知**被擋的那一席自己**。

規格 `pending/SPEC-L8b-notify-blocked-seat-b226a-0905.md`(sha16 06df02aae538a141)
+ a seat 09:35 三裁(R1 宿主=`pane-right-ai.sh` 採集端 / 窗號拒投也寫、reason 欄不同 /
檔名帶艙尾碼)。

## 為什麼要有這一支
09-05 04:39–09:09,cap2 的框裡躺著別席 04:39 送進去而沒按 Enter 的稿,守衛因此拒投
**全艙 150+ 次**、持續 4.5 小時。**不對稱才是缺陷**:送件席每次都拿到具名的 rc=2,
被擋席**一個訊號都沒有** —— 他那邊看起來一切正常,只是全艦忽然安靜。

## 三條紅線(每一條都對應一種今早看得到的失效)
1. **一席一檔,不是一次一檔**:150 次會生 150 個檔;而 150 個檔與一個檔帶
   `attempts=150` 在被擋席眼裡是同一件事,前者還會淹掉共用的 `pending/`。
2. **不抄框裡的字**:那是第三席的訊息,而 `pending/` 跨艙共用。只存
   `frame_sha256` 與 `frame_bytes` —— 指紋足以回答「還是同一封嗎」,那正是他要問的。
3. **卡稿主只從投遞帳本反查**,查不到寫 `null`。不得從畫面抬頭推(那要讀內容,
   與第 2 條打架時以不讀內容為準)。

## 介面
    composer_notice.py write  --seat <五碼> --reason <foreign-draft|window-alias|browse-mode> \\
                              [--sender <五碼>] [--frame-sha ..] [--frame-bytes N] \\
                              [--draft-owner <五碼>|--draft-owner-unknown]
    composer_notice.py clear  --seat <五碼>
    composer_notice.py render --seat <五碼>      # 右欄用;沒有檔就印空、rc=0
    composer_notice.py sync-bind --snapshot FILE # 同步 fleet_patrol 的綁定提醒快照
每個子命令都**永不因為自己壞掉而擋住呼叫端**:寫不進去只在 stderr 說一句,rc 仍 0。
理由:這支是**回饋器**。它把 dm 擋住的話,會從「通知被擋的人」變成「多擋一個人」。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

SCHEMA = "aib-composer-blocked/v1"
STALE_S_DEFAULT = 3600
REASONS = ("foreign-draft", "window-alias", "browse-mode", "history-mode")   # browse-mode:第 55 項,TUI 瀏覽模式不是卡稿
BIND_SNAPSHOT_SCHEMA = "aib-fleet-bind-notices/v1"
BIND_NOTICE_KINDS = {
    "bind-attention": "ctx",
    "bind-candidates": "cards",
    "bind-unbound": "unbound",
}
UNBOUND_SUMMARY_SEAT = "cabin"
_SEAT_CODE_RE = re.compile(r"^[0-9a-f]{5}$")
_BIND_TEXT_RE = {
    "bind-attention": re.compile(
        r"^@[0-9a-f]{5} 用量已過 [1-9][0-9]{0,2}%,該綁人格與第二大腦$"),
    "bind-candidates": re.compile(
        r"^@[0-9a-f]{5} 開席建議:(?:[2-9]|[1-9][0-9]+) 張卡候選,右鍵綁定$"),
    "bind-unbound": re.compile(r"^未綁人格:[1-9][0-9]* 席$"),
}


def notice_dir():
    """通知的落點。順序:AIB_COMPOSER_NOTICE_DIR > $EZ2_AIB_HOME/pending > ~/.ez2-aib/pending。

    ★ N1(09-26,a seat;艦長 a seat 派):舊版只看 `AIB_COMPOSER_NOTICE_DIR`,沒設就用
      `expanduser("~/.ez2-aib/pending")` —— **只看 HOME,不看 EZ2_AIB_HOME**。
      測試把 EZ2_AIB_HOME 指進沙盒也擋不住,v0.67.0 證詞期間真實 pending/ 因此冒出
      `NOTICE-composer-blocked-{a0007,abcde}-dmtest.txt`,別支測試又把它們讀回去用。
    ★ 與 dm-ctl.sh 放 RELAY 檔的規則同一條(`${EZ2_AIB_HOME:-$HOME/.ez2-aib}/pending`),不另立第三種。
    ★ 正式艙不設 EZ2_AIB_HOME(產品碼沒有任何地方 export 它)⇒ 正式環境落點不變,仍是跨艙共用的 ~/.ez2-aib/pending。
    """
    explicit = os.environ.get("AIB_COMPOSER_NOTICE_DIR")
    if explicit:
        return explicit
    aib_home = os.environ.get("EZ2_AIB_HOME")
    if aib_home:
        return os.path.join(aib_home, "pending")
    return os.path.expanduser("~/.ez2-aib/pending")


def cabin_suffix():
    """艙尾碼(裁定三)。`pending/` 跨艙共用,只帶五碼會被別艙同碼席位覆寫。"""
    sock = os.environ.get("EZ2_AIHUB_SOCKET") or ""
    tail = sock.rsplit("-", 1)[-1] if sock else ""
    return tail if tail and tail != sock else (tail or "nohub")


def notice_path(seat, directory=None):
    return os.path.join(directory or notice_dir(),
                        "NOTICE-composer-blocked-%s-%s.txt" % (seat, cabin_suffix()))


def _now():
    override = os.environ.get("AIB_COMPOSER_NOTICE_NOW")
    return int(override) if override and override.isdigit() else int(time.time())


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def write(seat, reason, sender=None, frame_sha=None, frame_bytes=None,
          draft_owner=None, directory=None):
    """累積帳:同一席只有一個檔;`first_seen` 只在建檔時寫一次。"""
    if reason not in REASONS:
        raise ValueError("reason 只認 %s" % (REASONS,))
    path = notice_path(seat, directory)
    now = _now()
    cur = load(path) or {}
    senders = list(cur.get("distinct_senders") or [])
    if sender and sender not in senders:
        senders.append(sender)
    data = {
        "schema": SCHEMA,
        "blocked_seat": seat,
        "reason": reason,
        "first_seen": cur.get("first_seen") or _iso(now),
        "first_seen_epoch": cur.get("first_seen_epoch") or now,
        "last_seen": _iso(now),
        "last_seen_epoch": now,
        "attempts": int(cur.get("attempts") or 0) + 1,
        "distinct_senders": sorted(senders),
        # ★ 只存指紋。框裡的字一個位元組都不進這個檔。
        "frame_sha256": frame_sha if frame_sha else cur.get("frame_sha256"),
        "frame_bytes": frame_bytes if frame_bytes is not None else cur.get("frame_bytes"),
        # ★ 查不到就是 null,不編一個看起來合理的五碼。
        "draft_owner": draft_owner if draft_owner else cur.get("draft_owner"),
    }
    tmp = path + ".tmp.%d" % os.getpid()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return data


def clear(seat, directory=None):
    """對該席投遞成功(rc_contract=0)⇒ 框已可用,刪檔。

    ★ 只有**寫的那一端**刪。讀取端(右欄)只讀不刪 —— 兩端都以為對方會清,
      就會變成「沒有人清」或「剛寫好就被清掉」,而兩者都沒有一格會紅。
    """
    path = notice_path(seat, directory)
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _bind_notice_seat(kind, seat):
    return "zbind-%s-%s" % (BIND_NOTICE_KINDS[kind], seat)


def _validate_bind_snapshot(snapshot):
    """整份驗完才准同步；壞一筆時既有右欄提醒原封不動。"""
    if not isinstance(snapshot, dict) or set(snapshot) != {"schema", "notices"}:
        raise ValueError("bind snapshot 形狀不合")
    if snapshot.get("schema") != BIND_SNAPSHOT_SCHEMA:
        raise ValueError("bind snapshot schema 不合")
    raw = snapshot.get("notices")
    if not isinstance(raw, list):
        raise ValueError("bind snapshot notices 不是 list")
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"seat", "kind", "text"}:
            raise ValueError("bind notice 形狀不合")
        seat = item.get("seat")
        kind = item.get("kind")
        value = item.get("text")
        if kind not in BIND_NOTICE_KINDS:
            raise ValueError("bind notice kind 不合")
        if kind == "bind-unbound":
            if seat != UNBOUND_SUMMARY_SEAT:
                raise ValueError("bind notice seat 不合")
        elif not isinstance(seat, str) or _SEAT_CODE_RE.fullmatch(seat) is None:
            raise ValueError("bind notice seat 不合")
        if not isinstance(value, str) or len(value) > 160 \
                or re.search(r"[\x00-\x1f\x7f-\x9f]", value):
            raise ValueError("bind notice text 不安全")
        if kind == "bind-unbound":
            if _BIND_TEXT_RE[kind].fullmatch(value) is None:
                raise ValueError("bind notice text 與 seat/kind 不合")
        elif not value.startswith("@%s " % seat) \
                or _BIND_TEXT_RE[kind].fullmatch(value) is None:
            raise ValueError("bind notice text 與 seat/kind 不合")
        key = (kind, seat)
        if key in seen:
            raise ValueError("bind notice 重複")
        seen.add(key)
        out.append({"seat": seat, "kind": kind, "text": value})
    return out


def _bind_notice_paths(directory=None):
    directory = os.fspath(directory or notice_dir())
    suffix = glob.escape(cabin_suffix())
    paths = []
    for short in BIND_NOTICE_KINDS.values():
        pattern = os.path.join(
            glob.escape(directory),
            "NOTICE-composer-blocked-zbind-%s-*-%s.txt" % (short, suffix))
        paths.extend(glob.glob(pattern))
    return set(paths)


def sync_bind(snapshot, directory=None):
    """以一份完整 patrol 快照同步動態提醒，不碰 L8b 真實卡稿通知。"""
    notices = _validate_bind_snapshot(snapshot)
    directory = os.fspath(directory or notice_dir())
    now = _now()
    wanted = {}
    for item in notices:
        synthetic = _bind_notice_seat(item["kind"], item["seat"])
        path = notice_path(synthetic, directory)
        current = load(path) or {}
        data = {
            "schema": SCHEMA,
            "blocked_seat": synthetic,
            "reason": item["kind"],
            "real_seat": item["seat"],
            "notice_text": item["text"],
            "first_seen": current.get("first_seen") or _iso(now),
            "first_seen_epoch": current.get("first_seen_epoch") or now,
            "updated_at": _iso(now),
            "updated_epoch": now,
        }
        wanted[path] = data

    os.makedirs(directory, exist_ok=True)
    staged = []
    try:
        for index, (path, data) in enumerate(sorted(wanted.items())):
            current = load(path)
            stable = ("schema", "blocked_seat", "reason", "real_seat", "notice_text")
            if current and all(current.get(key) == data.get(key) for key in stable):
                wanted[path] = current
                continue
            tmp = "%s.tmp.%d.%d" % (path, os.getpid(), index)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            staged.append((tmp, path))
        for tmp, path in staged:
            os.replace(tmp, path)
        for path in _bind_notice_paths(directory) - set(wanted):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    finally:
        for tmp, _ in staged:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
    return len(wanted)


def render(seat, directory=None, stale_s=None):
    """右欄要印的字。沒有檔 ⇒ 空字串(呼叫端據此決定要不要佔一行)。

    ★ 訊息寫成**動作**不是狀態:他看得到框裡有字,他不知道的是**全艦因此安靜**。
    """
    data = load(notice_path(seat, directory))
    if not data:
        return ""
    if data.get("reason") in BIND_NOTICE_KINDS:
        text = data.get("notice_text")
        real_seat = data.get("real_seat")
        kind = data.get("reason")
        if kind == "bind-unbound":
            if real_seat != UNBOUND_SUMMARY_SEAT \
                    or not isinstance(text, str) \
                    or _BIND_TEXT_RE[kind].fullmatch(text) is None:
                return ""
            return "⚠ " + text
        if not isinstance(real_seat, str) or _SEAT_CODE_RE.fullmatch(real_seat) is None \
                or not isinstance(text, str) or not text.startswith("@%s " % real_seat) \
                or _BIND_TEXT_RE[kind].fullmatch(text) is None:
            return ""
        return "⚠ " + text
    stale_s = STALE_S_DEFAULT if stale_s is None else stale_s
    age = _now() - int(data.get("last_seen_epoch") or 0)
    owner = data.get("draft_owner") or "不明"
    n = data.get("attempts") or 0
    m = len(data.get("distinct_senders") or [])
    if data.get("reason") == "window-alias":
        head = "⚠ 有人用窗號當位址送給你(不是位址,已拒投)"
    elif data.get("reason") == "browse-mode":
        head = "⚠ 你的 TUI 在瀏覽模式(底列 ←:collapse / Enter:open),dm 送不進來:按 Esc 回輸入列"
    elif data.get("reason") == "history-mode":
        head = "⚠ 你的輸入框停在輸入歷史(框頂線 History n/m),dm 送不進來:按 ↓ 直到 History 消失"
    else:
        head = "⚠ 你的輸入框裡有一封 @%s 送進來、**沒有送出**的稿" % owner
    body = "在你送出或清掉它之前,全艙送不進任何 dm(已被擋 %d 次,來自 %d 席)" % (n, m)
    # ★ 過期**不隱藏**:今早的失效方向就是「沒有訊號」,再加一條會自動關掉訊號的
    #   規則是同一個病的第二種寫法。只加註年紀,讓人自己判斷。
    if age > stale_s:
        body += ";最近一次被擋是 %d 分鐘前,可能已解除" % (age // 60)
    return "%s。%s。" % (head, body)


def main(argv=None):
    ap = argparse.ArgumentParser(description="L8b 被擋席通知")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write"); w.add_argument("--seat", required=True)
    w.add_argument("--reason", required=True, choices=REASONS)
    w.add_argument("--sender"); w.add_argument("--frame-sha")
    w.add_argument("--frame-bytes", type=int); w.add_argument("--draft-owner")
    c = sub.add_parser("clear"); c.add_argument("--seat", required=True)
    r = sub.add_parser("render"); r.add_argument("--seat", required=True)
    s = sub.add_parser("sync-bind"); s.add_argument("--snapshot", required=True)
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "write":
            write(ns.seat, ns.reason, sender=ns.sender, frame_sha=ns.frame_sha,
                  frame_bytes=ns.frame_bytes, draft_owner=ns.draft_owner)
        elif ns.cmd == "clear":
            clear(ns.seat)
        elif ns.cmd == "sync-bind":
            with open(ns.snapshot, encoding="utf-8") as fh:
                sync_bind(json.load(fh))
        else:
            text = render(ns.seat)
            if text:
                print(text)
    except Exception as exc:                     # noqa: BLE001 —— 回饋器永不擋
        sys.stderr.write("composer_notice: %s\n" % exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
