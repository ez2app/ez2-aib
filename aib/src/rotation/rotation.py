#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E6.3 艦長級輪替(draft):worker 任務內 checkpoint+接棒協定。

背景:CTX 46% 實例(E6.3 條目)+第二棒輪替三紀律(大綱決策 8,已生效):
    1. 交接檔必含「陷阱節」(traps)—— 沒有陷阱節的交接檔不准產生
    2. 原窗 24h 顧問期 —— 交棒者原窗保留 24 小時可諮詢,期滿才算完全離手
    3. 接棒三句理解驗收 —— 接棒者用恰三句話回述(做了什麼/沒做什麼與陷阱/
       下一步),由交棒者(顧問)確認才算接棒成立
本模組把三紀律全部做成機器閘門,不靠 AI 記憶(the maintainer 08-08 令的同一精神)。

## 物件與存放

checkpoint(任務內,隨時可打;<root>/<seat>/checkpoints.jsonl append-only):
    {"schema_version": "0.1.0-draft", "seat": "dev2", "task": "E9.9",
     "ctx_pct": 46, "at_ms": ..., "done": [...], "not_done": [...],
     "next_step": "...", "traps": ["..."(至少一條)], "evidence_refs": ["ev_..."]}

handoff(交棒時從最新 checkpoint 鍛造;--out 落檔或 stdout):
    {..., "advisor": {"seat": 交棒席, "until_ms": at_ms+24h, "note": 原窗諮詢},
     "acceptance_spec": {"sentences_required": 3,
                         "slots": ["done", "not_done_traps", "next_step"]}}

acceptance(接棒驗收紀錄;08-15 起**落檔**於 <root>/<接棒席>/acceptances.jsonl,
    append-only,同 acceptance_uid 以最新行為準):sentences 恰 3 句對應三 slot。
    **兩階段**(E6.3 高3):接棒者 accept -> pending_confirm(rc=3,三句在此凍結);
    交棒者另跑 confirm 背書 -> accepted(rc=0)。一次呼叫同時提交與確認時,
    「交棒者背書」只是同一個呼叫者自己寫的字串,不構成雙方協定。
    ★ 誠實邊界:confirmed_by 仍只驗格式與 seat 前綴(高1 未關),所以這是
    「無紀錄的交接」擋得住,「偽造的交接」擋不住——詳見 README §一。

## CLI(真實 exit code:0=成功,2=來源/參數/紀律錯誤,3=待確認或該輪替)

  rotation.py checkpoint --root R --seat dev2 --task E9.9 --ctx-pct 46 \
      --next-step "..." --trap "..." [--trap ...] [--done ...] [--not-done ...] \
      [--now-ms N] [--emit-event]
  rotation.py check --ctx-pct 46 [--threshold 90]      # rc=3 表示該交接了
  rotation.py handoff --root R --seat dev2 --task E9.9 [--trap 補充陷阱] \
      [--now-ms N] [--out F]
  rotation.py accept --root R --handoff F --understanding U.json --by-seat cl2 \
      [--now-ms N] [--emit-event]              # -> pending_confirm(rc=3)
  rotation.py confirm --root R --seat-to cl2 --acceptance-uid acc_... \
      --confirmed-by "a seat/Claude" [--now-ms N] [--emit-event]  # -> rc=0
  rotation.py acceptance-status --root R --seat-to cl2 [--task E9.9]
  rotation.py advisor-status --handoff F [--uuid U] [--manual-release] [--now-ms N]
      # G1-07:顧問期 CTX 制——machine ctx≥96 才 released_by=ctx96;--ctx-pct 不解除
  # ---- G1(艦長生命週期 v2)五本帳:全部 append-only、同一條硬化寫入路徑 ----
  rotation.py scout    --root R --seat S --successor-seat T --successor-role pm \
      --reason "..." [--ctx-pct N|--uuid U] [--now-ms N]        # 立儲 scout.jsonl
  rotation.py delegate --root R --seat S --to-seat T --work "..." \
      [--evidence-ref ev_..]* [--ctx-pct N|--uuid U]              # 交辦 delegation.jsonl
  rotation.py patrol   --root R --seat S --visited T [--visited T2..] --finding "..." \
      [--delegated "..."]* [--ctx-pct N|--uuid U]                # 巡查 patrol.jsonl
  rotation.py testament --root R --seat S --file PATH.md [--receipt ID] \
      [--ctx-pct N|--uuid U]        # 遺詔 testament/<at_ms>.md + testament-receipts.jsonl
  rotation.py testament-status --root R --seat S    # {testament, receipt, compact_ready}
  rotation.py checkpoint ... [--kind checkpoint|full_summary] [--uuid U]  # 75% 硬性總結
  rotation.py check --ctx-pct 46 [--uuid U]  # 有 uuid 只吃機器值;拿不到=manual_only

(--root 對 accept 是**必填**:選填等於「省略就沒有紀錄」,而那正是要關掉的
繞過路徑——可以被省略的閘不是閘。)

決定性:--now-ms 注入;同輸入必同輸出。
反重造(E8.3):schema 驗證複用 src/ledger/schema_validator.py;
事件形狀對齊 schemas/event.schema.json 的 memory.checkpoint;
CTX 90% 交接門檻沿用 docs/HANDOFF.md 輪替協定,46% 是「該打 checkpoint」的實例
而非門檻,故 check 門檻可調且預設 90。
"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat as stat_mod
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "policy"))
import tempfile
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC / "ledger"))

from schema_validator import SchemaValidator, SchemaValidationError  # noqa: E402
import ctx_policy  # noqa: E402  (EH.28 門檻單一資料源)

ROTATION_VERSION = "0.1.0-draft"
ADVISOR_PERIOD_MS = 24 * 3600 * 1000  # 決策 8:原窗 24h 顧問期
SENTENCES_REQUIRED = 3                # 決策 8:接棒三句理解驗收
# EH.28:門檻改讀 policy/ctx-thresholds.json 單一資料源(艦長 09:07/09:16 裁)。
# ★ 這裡**不留預設值**:policy 缺鍵時 ctx_policy 會 raise,而那正是要的 ——
#   給預設值等於把「資料檔壞了」翻譯成「一切正常」。
# 10-15 維護者「只保留 A 一張表」(policy/ctx-thresholds.json 只剩各席的 7 格):B 的 must_handoff(90)刪了;
#   20:4x 裁:輪替預設門檻與交接地板改讀表上「要求交接」那一格(85),防繞過地板照留。
DEFAULT_ROTATE_THRESHOLD = ctx_policy.trigger_pct("handoff_now")
EXPECTED_SLOTS = ["done", "not_done_traps", "next_step"]  # 決策 8 三 slot 名釘死
# E6.3 高5(codex 08-14 審,08-15 關):accept 原本只印 stdout,驗收紀錄零持久化
# ——事件裡宣稱可用「驗收指紋」稽核 preimage,而 preimage 從來沒落過盤,
# 帳本最後只剩反查不回去的 hash。存放與 checkpoints 同席目錄、同一套硬化。
CHECKPOINTS_NAME = "checkpoints.jsonl"
ACCEPTANCES_NAME = "acceptances.jsonl"
# G1(艦長生命週期 v2,the maintainer 2026-08-17 拍板 PLAN §2.4):五本帳,全部 append-only,
# 全部走同一條 _append_jsonl/_read_jsonl 硬化路徑(不得另開寫入路徑)。
SCOUT_NAME = "scout.jsonl"                    # 立儲:ctx≥50 物色同艦主管當繼任
DELEGATION_NAME = "delegation.jsonl"          # 交辦:共治期/巡查後派工
PATROL_NAME = "patrol.jsonl"                  # 巡查:巡了哪些席/發現/交辦
TESTAMENT_RECEIPTS_NAME = "testament-receipts.jsonl"  # 遺詔投遞收據(平鋪於席位目錄,
#   不放 testament/ 子目錄下:openat 鏈只錨定一層 name,子目錄段會回到可競態路徑)
TESTAMENT_DIR = "testament"                   # 遺詔 md 本體:<seat>/testament/<at_ms>.md
_LEDGER_NAMES = (CHECKPOINTS_NAME, ACCEPTANCES_NAME, SCOUT_NAME,
                 DELEGATION_NAME, PATROL_NAME, TESTAMENT_RECEIPTS_NAME)
# 顧問期改 CTX 制(PLAN §2.3):machine ctx ≥ 此值才解除;24h 保留為後備天花板
# (到了只標 needs_human,不自動解除)。兩者都是常數,不吃 CLI 參數(防偽造精神)。
# 10-15 單表:B 的 advisor_stop(96)刪了 ⇒ 顧問期不再因 CTX 自動解除(只剩 manual 與 24h 天花板 needs_human)。
ADVISOR_STOP_CTX = None
# 巡查節奏(PLAN §4-Q1 預設):上次巡查以來 ctx +10% 或 6h,取先到者;從未巡=逾期。
PATROL_EVERY_PCT = 10
PATROL_EVERY_MS = 6 * 3600 * 1000
# 巡查紀錄 at_ms 比 now 晚超過此毫秒=無效紀錄(qa O3:一筆未來時戳會讓「永不逾期」);
# 容忍量與 lifecycle.CTX_FUTURE_SLACK_S 同 5 分鐘。
PATROL_FUTURE_SLACK_MS = 300 * 1000
CTX_SOURCES = ("machine", "self_reported", "none")
CHECKPOINT_KINDS = ("checkpoint", "full_summary")

# 中5(08-14 四輪):seat 曾允許底線而 sig 的 seat 段不允許——dev_2 這種
# 席位永遠給不出合法 confirmed_by,只能卡 pending。對齊 worktree schema
# 的 owner_sig 字元集([a-z0-9-]),嚴的那邊是對的。
_SEAT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_SIG_RE = re.compile(r"^[a-z0-9-]{1,32}@[0-9a-f]{4,8}/[A-Za-z0-9._-]{1,32}$")
# 低2(08-14 六輪):task=節點 id,空白/控制字元=不可辨識,收可見字元集
_TASK_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# 中1(九輪):三個 regex 一律 fullmatch——match()+$ 會收尾端換行,
# "dev2\n" 過閘會把換行帶進路徑與事件摘要。
# 中4(08-14 七輪):控制字元(含 C1 區單字元 CSI/OSC)=日誌注入面。
# 中1(八輪)追加 U+2028/2029:Unicode 行分隔符在 JSON 字串內合法、
# ensure_ascii=False 原樣落盤,而讀端 splitlines() 會在字串中間拆行——
# 首寫回報成功,之後整檔永久判損毀(U+0085 已在 C1 區涵蓋)。
_CTRL_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]")


def _pread_all(fd, size):
    """pread 讀滿 size(中2 七輪:單次 pread 不保證讀滿,短讀會漏最新
    checkpoint 或壞掉時戳檢查)。"""
    chunks, off = [], 0
    while off < size:
        chunk = os.pread(fd, size - off, off)
        if not chunk:
            break
        chunks.append(chunk)
        off += len(chunk)
    return b"".join(chunks)
MAX_SOURCE_BYTES = 1_000_000  # 低1(四輪):特製巨檔=記憶體 DoS,來源檔設上限

# 08-14 CI 修(root3 實測 + 設計席 b288f 提修法):macOS 上多程序同時以
# O_CREAT 開**同一個**檔時,openat 會偶發回 ENOENT——那不是「檔不在」的
# 真相,是 create-or-open 把「查找」與「建立」混在同一次系統呼叫裡,建立
# 進行中的那一瞬對併發者的瞬時態。實測 8 並行 1200 次開檔中 9 次(0.75%)。
#
# **對照實驗排除了 O_NOFOLLOW**(每組 1200 次:不帶 3 次、帶 4 次,同一
# 量級)——安全旗標是被冤枉的,不要為了消紅把它拿掉。
#
# 修法取「兩態開檔」而非「ENOENT 重試」。兩者實測同樣有效(各 1200 次
# 零 ENOENT),取兩態的理由是**語意**:重試等於教程式把 ENOENT 吞掉,而
# ENOENT 在這條路徑上是安全相關訊號;兩態之後,不帶 O_CREAT 的那次開檔
# 回 ENOENT 就是明確的真異常,fail-closed 才有意義。
def _open_create_or_append(name, dir_fd):
    """並行安全的 create-or-append:建立與開啟分兩態,只給 write 路徑用。

    態一 O_CREAT|O_EXCL=原子建立,EEXIST=別人先建好(正常,不是錯);
    態二不帶 O_CREAT=「必須已存在」,此後 ENOENT 就是真異常照舊往上拋。
    兩態都保 O_NOFOLLOW,安全語意零放寬;hard link 由呼叫端 st_nlink 閘擋。

    **dev/ino 對照的射程要說準**(08-14 主管 0a5b3 對抗式覆審發現三:原註解
    寫「開檔期間被替換由 dev/ino 對照擋」是過度宣稱,會讓下一棒以為那道閘
    比實際強):它比對的是「我們手上的 fd」與「當下的目錄項」,**只擋得住
    最後一次 open 之後的替換**;open 之前、以及態一與態二之間的目錄項替換
    都不在射程內,那一段靠的是目錄權限而不是這道閘。
    **但拆兩態沒有讓安全等級退化**——覆審以對照組實測:攻擊者若能控制目錄
    項,對原版單次 `O_CREAT` 做「open 前預先放置普通檔」同樣得逞。兩版能力
    相同,兩態只是把窗口從「open 前」變成「open 前 ∪ 兩態之間」。
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        os.close(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                         0o600, dir_fd=dir_fd))
    except FileExistsError:
        pass
    return os.open(name, os.O_RDWR | os.O_APPEND | nofollow,
                   0o600, dir_fd=dir_fd)

_STR_LIST = {"type": "array", "maxItems": 64,
             "items": {"type": "string", "minLength": 1, "maxLength": 500}}

CHECKPOINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "seat", "task", "ctx_pct", "at_ms",
                 "done", "not_done", "next_step", "traps"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,31}$"},
        "task": {"type": "string", "minLength": 1, "maxLength": 64},
        "ctx_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "at_ms": {"type": "integer", "minimum": 0},
        "done": _STR_LIST,
        "not_done": _STR_LIST,
        "next_step": {"type": "string", "minLength": 1, "maxLength": 500},
        # 決策 8 紀律 1:陷阱節必有料(minItems 1),空交接檔不准產生
        "traps": {"type": "array", "minItems": 1, "maxItems": 64,
                  "items": {"type": "string", "minLength": 1, "maxLength": 500}},
        "evidence_refs": {"type": "array", "maxItems": 32,
                          "items": {"type": "string",
                                    "pattern": "^ev_[0-9a-f]{64}$"}},
        # G1-03:75% 硬性完整總結沿用 checkpoint 機制加 kind 欄;缺欄=checkpoint
        # (舊行零變更;新寫入一律明寫)。
        "kind": {"type": "string", "enum": list(CHECKPOINT_KINDS)},
        # G1-01:ctx_pct 從哪來。machine=EZ2_CTX_DIR/<uuid>.pct 非 stale;
        # self_reported=--ctx-pct;缺欄=舊紀錄(視同 self_reported)。
        "ctx_source": {"type": "string", "enum": list(CTX_SOURCES)},
    },
}

_SEAT_PATTERN = "^[a-z0-9][a-z0-9-]{0,31}$"
_ROLE_PATTERN = "^[a-z0-9][a-z0-9-]{0,31}$"
_TEXT = {"type": "string", "minLength": 1, "maxLength": 500}
_CTX_FIELDS = {
    "ctx_pct": {"type": "integer", "minimum": 0, "maximum": 100},
    "ctx_source": {"type": "string", "enum": list(CTX_SOURCES)},
}

SCOUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "seat", "at_ms", "successor_seat",
                 "successor_role", "reason", "ctx_source"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "at_ms": {"type": "integer", "minimum": 0},
        "successor_seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "successor_role": {"type": "string", "pattern": _ROLE_PATTERN},
        # G3:這一筆憑什麼過閘(role 白名單 or 機器可驗的艦長段位)。
        # optional:舊 scout 行沒有這欄,照樣合法(append-only 帳不回頭改)。
        "eligible_via": {"type": "string", "enum": ["role", "captain_assigned"]},
        # optional(舊行無此欄照樣合法):交叉檢查結果——role_match 或
        # skipped:<原因>(root2 §7.3 (ii),沒跑要說為什麼沒跑)
        "cross_check": {"type": "string",
                        "pattern": r"^(role_match|skipped:[a-z_]+)$"},
        "reason": _TEXT,
        **_CTX_FIELDS,
    },
}

DELEGATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "seat", "at_ms", "to_seat", "work",
                 "ctx_source"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "at_ms": {"type": "integer", "minimum": 0},
        "to_seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "work": _TEXT,
        "evidence_refs": {"type": "array", "maxItems": 32,
                          "items": {"type": "string",
                                    "pattern": "^ev_[0-9a-f]{64}$"}},
        **_CTX_FIELDS,
    },
}

PATROL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "seat", "at_ms", "visited", "finding",
                 "delegated", "ctx_source"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "at_ms": {"type": "integer", "minimum": 0},
        "visited": {"type": "array", "minItems": 1, "maxItems": 64,
                    "items": {"type": "string", "pattern": _SEAT_PATTERN}},
        "finding": _TEXT,
        "delegated": {"type": "array", "maxItems": 64, "items": _TEXT},
        **_CTX_FIELDS,
    },
}

TESTAMENT_RECEIPT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "seat", "at_ms", "testament_file",
                 "testament_sha256", "receipt", "ctx_source"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": _SEAT_PATTERN},
        "at_ms": {"type": "integer", "minimum": 0},
        "testament_file": {"type": "string", "pattern": "^[0-9]{1,16}\\.md$"},
        "testament_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "receipt": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{6,128}$"},
        **_CTX_FIELDS,
    },
}

HANDOFF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "seat", "task", "at_ms",
                 "from_checkpoint_at_ms", "ctx_pct_at_checkpoint",
                 "done", "not_done", "next_step", "traps", "advisor",
                 "acceptance_spec"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "seat": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,31}$"},
        "task": {"type": "string", "minLength": 1, "maxLength": 64},
        "at_ms": {"type": "integer", "minimum": 0},
        "from_checkpoint_at_ms": {"type": "integer", "minimum": 0},
        "ctx_pct_at_checkpoint": {"type": "integer", "minimum": 0,
                                  "maximum": 100},
        "done": _STR_LIST,
        "not_done": _STR_LIST,
        "next_step": {"type": "string", "minLength": 1, "maxLength": 500},
        "traps": {"type": "array", "minItems": 1, "maxItems": 64,
                  "items": {"type": "string", "minLength": 1,
                            "maxLength": 500}},
        # 中6(08-14 四輪):checkpoint 的證據鏈要跟著棒走,不得在交接時蒸發
        "evidence_refs": {"type": "array", "maxItems": 32,
                          "items": {"type": "string",
                                    "pattern": "^ev_[0-9a-f]{64}$"}},
        "advisor": {
            "type": "object", "additionalProperties": False,
            "required": ["seat", "until_ms", "note"],
            "properties": {
                "seat": {"type": "string",
                         "pattern": "^[a-z0-9][a-z0-9-]{0,31}$"},
                "until_ms": {"type": "integer", "minimum": 0},
                "note": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        },
        "acceptance_spec": {
            "type": "object", "additionalProperties": False,
            "required": ["sentences_required", "slots", "note"],
            "properties": {
                "sentences_required": {"const": SENTENCES_REQUIRED},
                "slots": {"type": "array", "minItems": 3, "maxItems": 3,
                          "items": {"type": "string", "minLength": 1}},
                "note": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        },
    },
}


def validate_handoff(handoff):
    """外部 handoff 的完整驗證(codex 審 E6.3 高1)。

    只做 json.load 等於任何人都能提交偽 handoff:沒有陷阱、自訂超長顧問期、
    advisor 指向別人,照樣 accepted。schema 驗形狀,這裡再驗三條內部一致性
    ——形狀對但語意矛盾的檔案同樣是偽造。"""
    _validate(handoff, HANDOFF_SCHEMA, "handoff")
    # 中2(08-14 審):slots 原本只限「三個非空字串」,外部 handoff 改名照過。
    # slot 名是驗收語意的一部分,恰為三名不多不少不換字。
    if handoff["acceptance_spec"]["slots"] != EXPECTED_SLOTS:
        raise RotationError(
            "acceptance_spec.slots 必須恰為 %s,得到 %s"
            % (EXPECTED_SLOTS, handoff["acceptance_spec"]["slots"]))
    advisor = handoff["advisor"]
    if advisor["seat"] != handoff["seat"]:
        raise RotationError(
            "advisor.seat %r must equal handoff seat %r (顧問期屬於交棒者)"
            % (advisor["seat"], handoff["seat"]))
    expected_until = handoff["at_ms"] + ADVISOR_PERIOD_MS
    if advisor["until_ms"] != expected_until:
        raise RotationError(
            "advisor.until_ms %d must be at_ms+24h (%d) —— 自訂顧問期不算數"
            % (advisor["until_ms"], expected_until))
    if handoff["from_checkpoint_at_ms"] > handoff["at_ms"]:
        raise RotationError("handoff 早於其 checkpoint,時序不合")
    # 高2(08-14 六輪):門檻只擋 CLI 分支=library 呼叫或手製 46% handoff
    # 照樣過驗收。協定地板(HANDOFF「輪替協定」:CTX 90% 才交接)進 validate_handoff,
    # 產生端與驗收端同一道地板;CLI --threshold 只能在地板之上加嚴。
    if handoff["ctx_pct_at_checkpoint"] < DEFAULT_ROTATE_THRESHOLD:
        raise RotationError(
            "ctx_pct_at_checkpoint %d%% 低於協定門檻 %d%%(HANDOFF「輪替協定」)——"
            "地板不是參數,放寬請修訂正本"
            % (handoff["ctx_pct_at_checkpoint"], DEFAULT_ROTATE_THRESHOLD))
    # 中3(08-14 二輪):原本只要「至少一條非空白」,["真陷阱", "   "] 照過
    # ——外部 handoff 的尺與產生端對齊,逐條非空白。
    _reject_blank_content(handoff, "handoff")
    return handoff


UNDERSTANDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentences"],
    "properties": {
        "sentences": {"type": "array",
                      "minItems": SENTENCES_REQUIRED,
                      "maxItems": SENTENCES_REQUIRED,
                      "items": {"type": "string", "minLength": 1,
                                "maxLength": 300}},
    },
}

# E6.3 高3+高5(codex 08-14 審,08-15 關):驗收落檔且改為兩階段狀態轉移。
# 舊行為=`accept --confirmed-by` 一次呼叫直接產出 accepted,**同一個呼叫者
# 同時控制三句與確認人**,三句理解驗收從未形成雙方協定;而且什麼都不落盤,
# 事後查不到「這次交接完成了沒」。
# 新行為=接棒者先 accept 落 pending_confirm(三句在此凍結),交棒者再以
# 另一次 confirm 背書轉 accepted。
# ★ 誠實邊界(高1 仍開著,勿誤讀):confirmed_by 至今只驗格式與 seat 前綴,
#   **沒有簽章、session binding 或呼叫者身分驗證**。所以兩階段擋的是
#   「無紀錄的交接」,不是「偽造的交接」——寫得出對方 sig 的人照樣能單方
#   走完兩階段。真正關死要 session binding(src/binding/,不在本模組)。
ACCEPTANCE_STATUSES = ["pending_confirm", "accepted"]
ACCEPTANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "acceptance_uid", "task", "seat_from",
                 "seat_to", "at_ms", "from_checkpoint_at_ms", "handoff_sha256",
                 "sentences", "confirmed_by", "status", "advisor_until_ms"],
    "properties": {
        "schema_version": {"const": ROTATION_VERSION},
        "acceptance_uid": {"type": "string", "pattern": "^acc_[0-9a-f]{32}$"},
        "task": {"type": "string", "minLength": 1, "maxLength": 64},
        "seat_from": {"type": "string",
                      "pattern": "^[a-z0-9][a-z0-9-]{0,31}$"},
        "seat_to": {"type": "string",
                    "pattern": "^[a-z0-9][a-z0-9-]{0,31}$"},
        "at_ms": {"type": "integer", "minimum": 0},
        "from_checkpoint_at_ms": {"type": "integer", "minimum": 0},
        "handoff_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "sentences": {"type": "array",
                      "minItems": SENTENCES_REQUIRED,
                      "maxItems": SENTENCES_REQUIRED,
                      "items": {"type": "string", "minLength": 1,
                                "maxLength": 300}},
        "confirmed_by": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": ACCEPTANCE_STATUSES},
        "advisor_until_ms": {"type": "integer", "minimum": 0},
    },
}


class RotationError(Exception):
    """來源檔不可信或紀律不滿足 —— fail-closed。"""


def die(message):
    print("rotation: %s" % message, file=sys.stderr)
    sys.exit(2)


def _validate(instance, schema, label):
    try:
        SchemaValidator(schema).validate(instance)
    except SchemaValidationError as err:
        raise RotationError("%s rejected: %s" % (label, err))


def _load_json(path, label):
    file_path = Path(path)
    if not file_path.is_file():
        raise RotationError("%s not found: %s" % (label, path))
    try:
        # 低1(四輪+六輪)+中2(十輪):大小檢查在同一 fd 上;O_NONBLOCK 防
        # is_file 與 open 之間被換成 FIFO 卡死;S_ISREG 拒特殊檔;讀取帶
        # 上限——fstat 之後檔案仍可被擴充,json.load 讀到 EOF 就破上限,
        # 所以「讀多少」也要封頂,不只「檢查時多大」。
        fd = os.open(str(file_path),
                     os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            st = os.fstat(fh.fileno())
            if not stat_mod.S_ISREG(st.st_mode):
                raise RotationError("%s 不是一般檔案(FIFO/裝置?)——拒讀"
                                    % label)
            if st.st_size > MAX_SOURCE_BYTES:
                raise RotationError("%s 過大(%d bytes > %d)——拒讀"
                                    % (label, st.st_size, MAX_SOURCE_BYTES))
            data = fh.read(MAX_SOURCE_BYTES + 1)
            # 低2(十一輪):上限以位元組計——read 回傳字元數,多位元 UTF-8
            # 的 1M「字元」可以是 3-4MB,與 DoS 上限的名實要一致。
            if len(data.encode("utf-8")) > MAX_SOURCE_BYTES:
                raise RotationError("%s 讀取超過上限(檢查後被擴充?)——拒讀"
                                    % label)
            return json.loads(data)
    except (OSError, ValueError) as err:
        # 低1(08-14 二輪):is_file 之後檔案消失/權限變動屬 I/O 競態,
        # 與壞 JSON 同類=來源不可信,一律 rc=2 不裸 traceback。
        raise RotationError("%s unreadable: %s" % (label, err))


def _reject_blank_content(entry, label):
    """高3+中3(08-14):空白不是內容——產生端與外部來源同一把尺。

    schema 的 minLength:1 收得下 " ";產生端擋了而外部檔不擋,等於
    「自己不做的事接受別人做」,偽 handoff 就從那個縫進來。"""
    blank = [i + 1 for i, t in enumerate(entry["traps"]) if not t.strip()]
    if blank:
        raise RotationError("%s 陷阱節第 %s 條為純空白 —— 空白不是陷阱(決策 8)"
                            % (label, ", ".join(map(str, blank))))
    if not entry["next_step"].strip():
        raise RotationError("%s next_step 為純空白 —— 沒有下一步不叫交接鏈"
                            % label)
    # 低2(08-14 六輪):done/not_done 的空白項=假內容,同尺;task 允許
    # 空白/控制字元=不可辨識的任務 ID,收緊為可見字元集。
    for _field in ("done", "not_done"):
        _blank = [i + 1 for i, t in enumerate(entry.get(_field) or [])
                  if not t.strip()]
        if _blank:
            raise RotationError("%s %s 第 %s 項為純空白 —— 空白不是內容"
                                % (label, _field, ", ".join(map(str, _blank))))
    if not _TASK_RE.fullmatch(entry.get("task") or ""):
        raise RotationError("%s task %r 非法(限 [A-Za-z0-9._-]{1,64})"
                            % (label, entry.get("task")))
    # 中1(08-14 八輪):內容欄拒控制字元與 Unicode 行分隔符——這些字元
    # 寫得進去讀不回來(splitlines 在字串內拆行),等於埋一顆讀端地雷。
    if _CTRL_RE.search(entry["next_step"]):
        raise RotationError("%s next_step 含控制/行分隔字元 —— 拒收" % label)
    for _field in ("traps", "done", "not_done"):
        _dirty = [i + 1 for i, t in enumerate(entry.get(_field) or [])
                  if _CTRL_RE.search(t)]
        if _dirty:
            raise RotationError(
                "%s %s 第 %s 項含控制/行分隔字元 —— 拒收"
                % (label, _field, ", ".join(map(str, _dirty))))


def seat_file(root, seat, name=CHECKPOINTS_NAME):
    """唯一路徑入口(codex 審 E6.3 高4:checkpoint 驗 seat,handoff 卻沒驗,
    ../../foo 可讀到別的 root)。所有呼叫端共用同一道門。

    E6.3 高5(08-15 落 acceptance):本席目錄下自此有兩個存放,name 參數化
    ——**逃逸閘與席位字元閘只此一道**,新存放不得自己拼路徑繞過去。
    name 只收本檔的字面常數(白名單),不吃呼叫端自由字串:否則
    name="../../x" 就把剛擋掉的逃逸從另一個參數放回來。"""
    if name not in _LEDGER_NAMES:
        raise RotationError("未知的存放名:%r(只收本模組常數)" % (name,))
    if not isinstance(seat, str) or not _SEAT_RE.fullmatch(seat):
        raise RotationError("invalid seat: %r" % (seat,))
    # 中5(十二輪):resolve 對 symlink loop 拋 OSError/RuntimeError,
    # 不收就是裸 traceback rc=1。
    try:
        base = Path(root).resolve()
        target = (base / seat).resolve()
    except (OSError, RuntimeError) as err:
        raise RotationError("路徑解析失敗(symlink loop/權限?):%s" % err)
    if target != base and base not in target.parents:
        raise RotationError("seat path escapes root: %r" % (seat,))
    return target / name


def checkpoints_file(root, seat):
    """相容名(既有呼叫端與 tests/test-rotation.py 沿用);語意同 seat_file。"""
    return seat_file(root, seat, CHECKPOINTS_NAME)


def _open_checkpoints(root, seat, write, name=CHECKPOINTS_NAME):
    """openat 鏈開該席的 append-only JSONL(高3 末輪:resolve 與 open 之間父目錄
    可被換成 symlink——縱使換後的 resolve 落點仍在 root 內,寫讀的也已經
    不是本席的檔。root resolve 一次後全程 fd 錨定:seat 段
    O_DIRECTORY|O_NOFOLLOW,檔案再以 dir_fd 相對開(openat 語意),
    路徑只走一次,之後誰換目錄都換不到手上的 fd)。

    回傳 (seat_fd, fd);讀模式下任一段 ENOENT 回傳 None(無 checkpoint 是
    正常初始態不是錯)。平台不支援 dir_fd=誠實拒絕,不靜默退化為可競態路徑。
    呼叫端負責 close 兩個 fd。
    """
    # 08-14 CI 修:能力檢查與呼叫用 os.stat(follow_symlinks=False) 而非
    # os.lstat——**os.lstat 的 dir_fd 支援在 py3.9=False、py3.14=True**
    # (實測),用 lstat 探能力會讓系統 Python 全鏈 fail-closed(CI run
    # 31737404912 紅因:Ran 0)。os.stat(...,follow_symlinks=False) 語意
    # 等同 lstat 且 py3.9 即支援 dir_fd,安全性零降級。
    if not ({os.open, os.mkdir, os.stat} <= os.supports_dir_fd):
        raise RotationError(
            "平台不支援 dir_fd(openat)—— 拒絕退化為可競態的路徑操作")
    try:
        root_str = str(Path(root).resolve())
    except (OSError, RuntimeError) as err:
        raise RotationError("路徑解析失敗(symlink loop/權限?):%s" % err)
    if write:
        try:
            os.makedirs(root_str, exist_ok=True)
        except OSError as err:
            raise RotationError("checkpoint 目錄建不起來:%s" % err)
    try:
        root_fd = os.open(root_str, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as err:
        if not write and err.errno == errno.ENOENT:
            return None
        raise RotationError("root 目錄拒開:%s" % err)
    seat_fd = -1
    try:
        if write:
            try:
                os.mkdir(seat, dir_fd=root_fd)
            except FileExistsError:
                pass
            except OSError as err:
                raise RotationError("checkpoint 目錄建不起來:%s" % err)
        try:
            # 目錄側本來就是兩態(上面 mkdir 負責建立、這裡只負責開已存在
            # 的),不帶 O_CREAT 故不踩 create-or-open 競態,維持單次開。
            seat_fd = os.open(seat,
                              os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=root_fd)
        except OSError as err:
            if not write and err.errno == errno.ENOENT:
                return None
            raise RotationError("席位目錄拒開(symlink 不跟隨?):%s" % err)
        try:
            # write=兩態開檔(見 _open_create_or_append);read 不建檔,
            # ENOENT=無 checkpoint 的正常初始態,由下面收成 None。
            fd = (_open_create_or_append(name, seat_fd)
                  if write
                  else os.open(name,
                               os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                               0o600, dir_fd=seat_fd))
        except OSError as err:
            if not write and err.errno == errno.ENOENT:
                os.close(seat_fd)
                seat_fd = -1
                return None
            raise RotationError(
                "%s 拒開(symlink 不跟隨):%s" % (name, err))
        return seat_fd, fd
    except BaseException:
        if seat_fd >= 0:
            os.close(seat_fd)
        raise
    finally:
        os.close(root_fd)


def write_checkpoint(root, seat, task, ctx_pct, at_ms, next_step, traps,
                     done=None, not_done=None, evidence_refs=None,
                     kind="checkpoint", ctx_source="self_reported"):
    """schema(含陷阱節紀律)驗過才 append;被拒一 byte 不落盤。

    G1-03:kind ∈ CHECKPOINT_KINDS,預設 "checkpoint"(舊呼叫端零變更);
    "full_summary" = 75% 硬性完整總結(PLAN §2.4 列 2)。落盤一律明寫 kind;
    讀端對**缺欄的舊行**視為 "checkpoint"(二擇一在此釘死,測試看守)。
    G1-01:ctx_source 記 ctx_pct 從哪來;預設 self_reported(舊呼叫端語意不變)。"""
    if kind not in CHECKPOINT_KINDS:
        raise RotationError("kind 只收 %s,得到 %r" % (list(CHECKPOINT_KINDS), kind))
    if ctx_source not in CTX_SOURCES:
        raise RotationError("ctx_source 只收 %s,得到 %r" % (list(CTX_SOURCES), ctx_source))
    entry = {"schema_version": ROTATION_VERSION, "seat": seat, "task": task,
             "ctx_pct": ctx_pct, "at_ms": at_ms, "done": done or [],
             "not_done": not_done or [], "next_step": next_step,
             "traps": traps or [], "kind": kind, "ctx_source": ctx_source}
    if evidence_refs:
        entry["evidence_refs"] = list(evidence_refs)
    _validate(entry, CHECKPOINT_SCHEMA, "checkpoint")
    _reject_blank_content(entry, "checkpoint")
    return _append_jsonl(root, seat, CHECKPOINTS_NAME, entry, at_ms,
                         CHECKPOINT_SCHEMA, "seat", "task", task,
                         line_check=_reject_blank_content)


def _append_jsonl(root, seat, name, entry, at_ms, schema, seat_field,
                  scope_key, scope_value, line_check=None):
    """該席 append-only JSONL 的**唯一**寫入路徑。

    E6.3 高5(08-15)落 acceptance 時從 write_checkpoint 抽出:兩個存放共用
    同一份硬化,**不是複製一份**——複製的那份會在下一次修補時漏掉,而漏掉
    的那半沒有人會發現(本檔十二輪的修補史就是這條的證據)。

    參數化的只有「什麼算一筆」:檔名 / schema / 席位欄名 / 單調範圍鍵。
    十二輪換來的那些一律共用不參數化:openat 鏈、鎖內單調、dev/ino 對照、
    hard link 閘、來源上限、末行完整性、部分寫入回滾、父目錄 fsync。
    """
    seat_file(root, seat, name)  # seat 字元閘+逃逸閘(名義檢查,沿用同一道門)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"
    # 中4:多 worker 同時 checkpoint 會交錯寫壞 JSONL,而讀端(修正前)
    # 又靜默跳過壞行——兩個 bug 疊起來就是「悄悄用舊狀態交棒」。
    # 高5(08-14 審):checkpoints.jsonl 本身是 symlink 時,O_APPEND 會跟隨
    # 連結寫到 root 外任意可寫檔。O_NOFOLLOW 擋最後一段;高3(末輪):
    # 父目錄(seat 段)的替換窗由 _open_checkpoints 的 openat 鏈關死。
    # O_RDWR 而非 O_WRONLY:單調檢查要在鎖內讀同一個 fd(見下)。
    seat_fd, fd = _open_checkpoints(root, seat, write=True, name=name)
    cur_size = None
    try:
        # 中4(二輪+三輪):stat/flock/write/fsync 任何一步 OSError 都要
        # 收成 RotationError rc=2——寫入鏈斷在哪一步都不是呼叫者的錯格式。
        try:
            st_fd = os.fstat(fd)
            # dev/ino 對照改以 seat_fd 相對 lstat——錨定後的目錄項才是
            # 「我們開的那個檔」的正身,全路徑 lstat 已不承重(高3)。
            st_ln = os.stat(name, dir_fd=seat_fd,
                            follow_symlinks=False)
        except OSError as err:
            raise RotationError("%s 狀態不可讀,拒寫:%s" % (name, err))
        if (st_fd.st_dev, st_fd.st_ino) != (st_ln.st_dev, st_ln.st_ino):
            raise RotationError("%s 在開檔期間被替換,拒寫" % name)
        # 高2(08-14 四輪):O_NOFOLLOW 擋 symlink 擋不了 hard link——
        # 連結數 >1 表示同一 inode 還掛在別的路徑上,append 會污染那個
        # 路徑的內容。輪替檔沒有任何正當理由被 hard link。
        if st_fd.st_nlink > 1:
            raise RotationError(
                "%s 連結數 %d>1(hard link)—— 拒寫"
                % (name, st_fd.st_nlink))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            # 中5(二輪)+中1(三輪):at_ms 不回退,且檢查在**鎖內**同 fd 讀
            # ——鎖外先讀再鎖是寫寫競態,兩個 worker 可同時讀到舊 max 後
            # 交錯寫出回退順序,註解宣告的保證就成了假話。
            payload_len = len(line.encode("utf-8"))
            cur_size = os.fstat(fd).st_size
            # 中1(08-14 五輪):append 可以把檔案推過 MAX_SOURCE_BYTES 還
            # 回報成功,下一次 handoff 才因超限永久拒讀——寫端守同一條上限,
            # 讀端的「≤上限」就成為不變量而非祈禱。
            if cur_size + payload_len > MAX_SOURCE_BYTES:
                raise RotationError(
                    "%s 將超過上限(%d+%d > %d)—— 拒寫;"
                    "請歸檔舊紀錄後再續"
                    % (name, cur_size, payload_len, MAX_SOURCE_BYTES))
            # 中2(08-14 六輪):寫端對既有內容從寬(略過壞行)再 append,
            # 等於在損毀檔上加深損毀——缺換行的末行會把新 JSON 黏成一行,
            # 整檔從此不可讀。寫端從嚴:任何損毀=拒寫,先修檔再續。
            data = _pread_all(fd, cur_size)
            if len(data) != cur_size:
                raise RotationError(
                    "%s 實讀 %d != 預期 %d(並行截斷?)——拒寫"
                    % (name, len(data), cur_size))
            if data and not data.endswith(b"\n"):
                raise RotationError(
                    "%s 末行不完整(缺換行)—— 拒寫,"
                    "append 會把新行黏上去加深損毀" % name)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as err:
                raise RotationError("%s 壞編碼——拒寫:%s" % (name, err))
            prev = None
            # 中1(08-14 七輪):寫端與讀端同一把尺——{"at_ms":1} 這種
            # 「合法 JSON 但 schema 不合」的行,寫端放行=rc 0 假成功,
            # 讀端之後必拒整檔。逐行過 schema+內容檢+席位歸屬。
            # 八輪:split("\n") 不用 splitlines——後者會在 JSON 字串內的
            # U+2028/2029 拆行,行界只認我們自己寫的 \n。
            for _ln, _line in enumerate(text.split("\n")):
                if not _line.strip():
                    continue
                try:
                    _entry = json.loads(_line)
                    _validate(_entry, schema, "既有行")
                except (ValueError, RotationError) as err:
                    raise RotationError(
                        "%s 第 %d 行損毀/不合 schema——"
                        "拒絕在損毀檔上加寫:%s" % (name, _ln + 1, err))
                if line_check is not None:
                    line_check(_entry, "%s 第 %d 行" % (name, _ln + 1))
                if _entry[seat_field] != seat:
                    raise RotationError(
                        "%s 第 %d 行 %s=%r 非本席 %r ——"
                        "檔案被污染,拒寫"
                        % (name, _ln + 1, seat_field, _entry[seat_field], seat))
                # 中1(十一輪):單調只比同 task——檔案級單調會讓 A 任務的
                # 未來時戳連 B 任務都封鎖,懲罰無辜任務不是 fail-closed。
                if _entry[scope_key] == scope_value and (
                        prev is None or _entry["at_ms"] > prev):
                    prev = _entry["at_ms"]
            if prev is not None and at_ms < prev:
                raise RotationError(
                    "at_ms %d 早於檔內最新 %d —— 時戳不回退;若真的是時鐘"
                    "問題,請人工檢視 %s" % (at_ms, prev, name))
            payload = line.encode("utf-8")
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except OSError as err:
            # 中2(十二輪):部分寫入不回滾=重試會黏行永久損毀。
            # 盡力 ftruncate 回原長;回滾也失敗就留給讀端 fail-closed。
            if cur_size is not None:
                try:
                    os.ftruncate(fd, cur_size)
                except OSError:
                    pass
            raise RotationError("%s 寫入失敗:%s" % (name, err))
    except BaseException:
        os.close(fd)
        os.close(seat_fd)
        raise
    os.close(fd)
    # 中3(十輪):新建檔只 fsync(fd) 不同步父目錄=當機後目錄項可能遺失
    # 而命令已報成功。高3(末輪):同一把 seat_fd 錨定,不再重開路徑。
    try:
        os.fsync(seat_fd)
    except OSError as err:
        raise RotationError("席位目錄同步失敗(%s):%s" % (name, err))
    finally:
        os.close(seat_fd)
    return entry


def checkpoint_kind(entry):
    """讀端對 kind 的唯一解讀:缺欄=舊行=一般 checkpoint(G1-03 二擇一,釘死)。"""
    return entry.get("kind", "checkpoint")


def latest_checkpoint(root, seat, task, kind=None):
    """該席該任務的最新 checkpoint;任何壞行=整檔拒用(fail-closed),無則 None。
    kind 給定時只看該 kind(G1-03/G1-09:「75% 有沒有 full_summary」)。

    (低3:早期版本壞行靜默跳過,docstring 也這麼寫;實作 08-14 已改
    fail-closed,文件跟上——文件與行為相反會讓呼叫端做錯誤的復原假設。)"""
    latest = None
    for entry in _read_jsonl(root, seat, CHECKPOINTS_NAME, CHECKPOINT_SCHEMA,
                             "seat", line_check=_reject_blank_content):
        if kind is not None and checkpoint_kind(entry) != kind:
            continue
        if entry["task"] == task and (
                latest is None or entry["at_ms"] >= latest["at_ms"]):
            latest = entry
    return latest


def checkpoints_of_kind(root, seat, kind):
    """該席**跨任務**某 kind 的全部 checkpoint(probe 用:75% 閘不分任務)。"""
    if kind not in CHECKPOINT_KINDS:
        raise RotationError("kind 只收 %s,得到 %r" % (list(CHECKPOINT_KINDS), kind))
    return [e for e in _read_jsonl(root, seat, CHECKPOINTS_NAME, CHECKPOINT_SCHEMA,
                                   "seat", line_check=_reject_blank_content)
            if checkpoint_kind(e) == kind]


# ---- G1 五本帳:寫入端 ----

def _ctx_fields(ctx_pct, ctx_source):
    """五本帳共用的 ctx 欄:ctx_source 必填(none=當下拿不到任何 ctx);
    ctx_pct 有值才寫;source 與值的搭配要合理(none 不得帶值、machine/self 必帶值)。"""
    if ctx_source not in CTX_SOURCES:
        raise RotationError("ctx_source 只收 %s,得到 %r" % (list(CTX_SOURCES), ctx_source))
    if ctx_source == "none":
        if ctx_pct is not None:
            raise RotationError("ctx_source=none 不得附 ctx_pct(%r)" % (ctx_pct,))
        return {"ctx_source": "none"}
    if not isinstance(ctx_pct, int) or isinstance(ctx_pct, bool) \
            or not 0 <= ctx_pct <= 100:
        raise RotationError("ctx_pct 須為 0..100 整數:%r" % (ctx_pct,))
    return {"ctx_pct": ctx_pct, "ctx_source": ctx_source}


def _text_ok(value, label):
    """一行文字欄的統一尺:非空白、無控制/行分隔字元、≤500。"""
    if not isinstance(value, str) or not value.strip():
        raise RotationError("%s 不得為空白" % label)
    if len(value) > 500:
        raise RotationError("%s 超過 500 字" % label)
    if _CTRL_RE.search(value):
        raise RotationError("%s 含控制/行分隔字元 —— 拒收" % label)


def _check_scout_line(entry, label):
    _text_ok(entry["reason"], label + " reason")
    if entry["successor_seat"] == entry["seat"]:
        raise RotationError("%s 儲君不得是自己(%s)" % (label, entry["seat"]))


def seat_records(seats_dir=None):
    """本艦席位檔的精簡表 [{seat_id, role, window_id, captain_assigned}]。

    讀不到目錄/壞檔 → 該筆略過(回得出多少算多少);**呼叫端不得把「空表」讀成
    「查過了沒有」**——G3 的閘因此在拿不到憑據時一律不通過(fail-closed)。"""
    import json as _json
    out = []
    try:
        names = sorted(os.listdir(seats_dir))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("w") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(seats_dir, name), "r", encoding="utf-8") as fh:
                rec = _json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
            continue
        out.append({"file": name, "seat_id": rec.get("seat_id"),
                    "role": rec.get("role"), "window_id": rec.get("window_id"),
                    "captain_assigned": rec.get("captain_assigned") is True})
    return out


def _seat_by_id(successor_seat, seats_dir=None):
    """seat_id 精確命中一席=回 (rec, "ok");否則 (None, 原因)。交叉檢查
    只在拿得到唯一事實時做,拿不到不猜——但**為什麼沒查到要落帳**
    (root2 §7.3 裁 (ii):skipped 不寫原因=靜默跳過的另一張臉)。"""
    if seats_dir is None:
        try:
            import handover_probe  # noqa: WPS433
            seats_dir = handover_probe.default_seats_dir()
        except Exception:  # pragma: no cover
            return None, "probe_unavailable"
    if not seats_dir or not os.path.isdir(seats_dir):
        return None, "seats_dir_missing"
    hit = [r for r in seat_records(seats_dir)
           if r.get("seat_id") == successor_seat]
    if not hit:
        return None, "no_seat_file"
    if len(hit) > 1:
        return None, "ambiguous_seat_id"
    return hit[0], "ok"


def captain_rank_backing(successor_seat, seats_dir=None):
    """G3:successor_seat 有沒有**機器可驗的艦長段位**(席位檔 captain_assigned=true)。

    回 dict{backed, why, seat_file, matched_by}。

    ★ 為什麼要有這支(艦長 08-18 實測):`scout --successor-role dev4` 被拒 rc=2,
      因為閘只認 role 字面在主管白名單裡;但 dev4 **是機器可驗的第二艦長**
      (席位檔 captain_assigned=true、右欄 ⚓、已分擔終審)。
      **用名字判身份 vs 用機器可驗事實判身份**——本 repo 今天第三次踩到同一型。
      修法一律:認落檔的事實,不認字面。

    ★ 對照鍵的紀律(handover_probe.py:322 已付過的錢):**role 不是安全的對照鍵**——
      拿 role 查會讓「某一席是艦長」擴散成「所有同 role 的窗都是艦長」。
      所以:先比 seat_id(唯一);退而比 role 時,**命中超過一席即歧義 → 不通過**,
      不挑第一個、不猜。查不到任何席位檔同樣不通過(fail-closed:拿不到憑據
      不等於憑據成立)。
    """
    if seats_dir is None:
        try:
            import handover_probe  # noqa: WPS433 (延遲 import:避免循環)
            seats_dir = handover_probe.default_seats_dir()
        except Exception:  # pragma: no cover
            # ★ EH.2(2026-08-21):這裡原本退回主艙 ~/.ez2-aib/seats。
            #   **主艙不是回退來源,是衝突**——退回去會拿到別艙的席位檔,
            #   而它格式正確、不報錯,於是「查不到憑據」被換成「查到了錯的憑據」。
            #   本函式的 docstring 自己寫著 fail-closed(拿不到憑據不等於憑據成立),
            #   同檔 _seat_by_id 的同一個 except 也是回 probe_unavailable。
            #   兩處對齊:拿不到解析器就是拿不到,不猜。
            return {"backed": False, "why": "probe_unavailable",
                    "seat_file": None, "matched_by": None}
    # ★ 三種「沒有」要分得開(handover_probe 檔頭同一條:指錯原因的紅會先耗光信任,
    #   然後整道閘被拿掉)。三者都拒,但拒的理由必須指得出真正的原因。
    if not os.path.isdir(seats_dir):
        return {"backed": False, "why": "seats_dir_missing:%s" % seats_dir,
                "seat_file": None, "matched_by": None}
    recs = seat_records(seats_dir)
    if not recs:
        return {"backed": False, "why": "seats_dir_has_no_valid_seat_file:%s" % seats_dir,
                "seat_file": None, "matched_by": None}
    hit = [r for r in recs if r.get("seat_id") == successor_seat]
    matched_by = "seat_id"
    if not hit:
        hit = [r for r in recs if r.get("role") == successor_seat]
        matched_by = "role"
    if not hit:
        return {"backed": False, "why": "no_seat_file_for:%s" % successor_seat,
                "seat_file": None, "matched_by": None}
    if len(hit) > 1:
        return {"backed": False,
                "why": "ambiguous_%s:%s 命中 %d 席(%s)——不挑第一個"
                       % (matched_by, successor_seat, len(hit),
                          ",".join(r["file"] for r in hit)),
                "seat_file": None, "matched_by": matched_by}
    rec = hit[0]
    return {"backed": rec["captain_assigned"],
            "why": ("captain_assigned=true" if rec["captain_assigned"]
                    else "席位檔在但 captain_assigned 未設"),
            "seat_file": rec["file"], "matched_by": matched_by}


def scout_gate(successor_seat, successor_role, lead_roles=None, seats_dir=None):
    """G3 繼任閘:**role 在主管白名單 或 該席有機器可驗的艦長段位**。

    白名單那一半原封不動(向後相容);新增的那一半只認落檔的事實。
    回 dict{ok, by_role, by_captain_flag, lead_roles, backing}——兩個條件各自的結果
    都要回得出來,被拒時才印得出「role 不合 **且** 無 captain_assigned」而不是只說一半。
    """
    if lead_roles is None:
        lead_roles = _lead_roles()
    # durable(同刀):successor_role 與席位檔**真 role** 交叉檢查——帳上寫
    # 的 role 若與落檔事實不同,直接拒(權宜帳=被窄清單逼出來的謊,清單
    # 修好後謊也不准再寫)。席位檔查不到(跨艙/沙箱)=無從交叉,維持
    # 原行為只驗字面(probe G1-09 仍會對同艦性)。
    true_rec, cc_why = _seat_by_id(successor_seat, seats_dir)
    cross_check = "role_match" if true_rec is not None else "skipped:%s" % cc_why
    if true_rec is not None:
        true_role = true_rec.get("role")
        if true_role and successor_role != true_role:
            return {"ok": False, "by_role": False, "by_captain_flag": False,
                    "role_mismatch": {"declared": successor_role,
                                      "actual": true_role},
                    "lead_roles": sorted(lead_roles),
                    "backing": {"backed": False,
                                "why": "declared_role != seat_file_role",
                                "seat_file": None, "matched_by": "seat_id"}}
    # 唯一解析函式(與 handover_probe (f) 共用,不得各自 in 集合)
    by_role, _via = _is_eligible_lead(successor_role, False,
                                      extra_roles=lead_roles)
    backing = captain_rank_backing(successor_seat, seats_dir=seats_dir) if not by_role \
        else {"backed": False, "why": "role 已通過,未查席位檔", "seat_file": None,
              "matched_by": None}
    ok, _via2 = _is_eligible_lead(successor_role, backing["backed"],
                                  extra_roles=lead_roles)
    return {"ok": ok, "by_role": by_role,
            "by_captain_flag": backing["backed"], "lead_roles": sorted(lead_roles),
            "cross_check": cross_check, "backing": backing}


def write_scout(root, seat, at_ms, successor_seat, successor_role, reason,
                ctx_pct=None, ctx_source="none", lead_roles=None, seats_dir=None):
    """立儲(scout.jsonl):登記一位繼任者。

    閘(G3,the maintainer 08-18 直令):繼任者 != 自己;reason 非空;且
    **successor_role 在主管角色集 或 successor_seat 的席位檔 captain_assigned=true**。
    後者讓「機器可驗的第二艦長」過得了繼任閘,而不是被 role 字面擋下(艦長實測)。
    同艦驗證(seats-dir 對照)仍歸 probe(G1-09),本函式只記欄位。"""
    gate = scout_gate(successor_seat, successor_role, lead_roles=lead_roles,
                      seats_dir=seats_dir)
    if not gate["ok"]:
        if gate.get("role_mismatch"):
            rm = gate["role_mismatch"]
            raise RotationError(
                "繼任閘不通過:帳上 role %r 與席位檔真 role %r 不同——"
                "清單已修為單一真相源,權宜寫法不再需要也不再允許;"
                "請用真 role 重寫(真 role 在主管集或有 captain_assigned 都過得了)"
                % (rm["declared"], rm["actual"]))
        # 錯誤訊息要能自證:兩個條件各印各的結果,看得出是「都不成立」而不是只驗了一半
        raise RotationError(
            "繼任閘不通過(兩條各自的結果如下,兩條都不成立才拒):\n"
            "  (1) successor_role %r 在主管角色集 %s → False\n"
            "  (2) successor_seat %r 的席位檔 captain_assigned → False(%s)"
            % (successor_role, gate["lead_roles"], successor_seat,
               gate["backing"]["why"]))
    entry = {"schema_version": ROTATION_VERSION, "seat": seat, "at_ms": at_ms,
             "successor_seat": successor_seat, "successor_role": successor_role,
             # 認的是哪一條事實要留在帳上:事後才查得出這一筆是憑什麼過的
             "eligible_via": "role" if gate["by_role"] else "captain_assigned",
             # root2 §7.3 (ii):交叉檢查沒跑也要留痕+原因——"skipped:no_seat_file"
             # 與 "role_match" 在帳上分得開,不然「查過了」與「查不了」同長相
             "cross_check": gate["cross_check"],
             "reason": reason}
    entry.update(_ctx_fields(ctx_pct, ctx_source))
    _validate(entry, SCOUT_SCHEMA, "scout")
    _check_scout_line(entry, "scout")
    return _append_jsonl(root, seat, SCOUT_NAME, entry, at_ms, SCOUT_SCHEMA,
                         "seat", "seat", seat, line_check=_check_scout_line)


def _lead_roles():
    """主管角色集,與 handover_probe.captain_roles() 同源(EZ2_AIB_CAPTAIN_ROLES,
    預設 root)+ 固定的主管席 role 名(pm/pm2/pm3:艦長候選人 = 現任主管)。
    延遲 import:handover_probe 匯入 rotation,反向在模組頂層 import 會循環。"""
    try:
        import handover_probe  # noqa: WPS433
        roles = set(handover_probe.captain_roles())
    except Exception:  # pragma: no cover - probe 不可用時退回常數
        roles = {"root"}
    roles |= set(LEAD_ROLES_DEFAULT)
    return roles


# ★ 單一真相源(艦外 21:33 裁 (b)+):字面清單廢除,改匯 policy/lead_roles
#   正本(root2 之病:這裡比 appoint 入口窄,逼出五筆 role 說謊的權宜帳)。
from lead_roles import LEAD_ROLES as LEAD_ROLES_DEFAULT  # noqa: E402
from lead_roles import is_eligible_lead as _is_eligible_lead  # noqa: E402


def _check_delegation_line(entry, label):
    _text_ok(entry["work"], label + " work")
    if entry["to_seat"] == entry["seat"]:
        raise RotationError("%s 不得交辦給自己(%s)" % (label, entry["seat"]))


def write_delegation(root, seat, at_ms, to_seat, work, evidence_refs=None,
                     ctx_pct=None, ctx_source="none"):
    """交辦(delegation.jsonl):共治期/巡查後把工作派給誰。閘:對象 != 自己;work 非空。"""
    entry = {"schema_version": ROTATION_VERSION, "seat": seat, "at_ms": at_ms,
             "to_seat": to_seat, "work": work}
    if evidence_refs:
        entry["evidence_refs"] = list(evidence_refs)
    entry.update(_ctx_fields(ctx_pct, ctx_source))
    _validate(entry, DELEGATION_SCHEMA, "delegation")
    _check_delegation_line(entry, "delegation")
    return _append_jsonl(root, seat, DELEGATION_NAME, entry, at_ms,
                         DELEGATION_SCHEMA, "seat", "seat", seat,
                         line_check=_check_delegation_line)


def _check_patrol_line(entry, label):
    _text_ok(entry["finding"], label + " finding")
    for i, d in enumerate(entry["delegated"]):
        _text_ok(d, "%s delegated 第 %d 項" % (label, i + 1))
    if len(set(entry["visited"])) != len(entry["visited"]):
        raise RotationError("%s visited 有重複席位" % label)


def write_patrol(root, seat, at_ms, visited, finding, delegated=None,
                 ctx_pct=None, ctx_source="none"):
    """巡查(patrol.jsonl):巡了哪些席/發現/交辦了什麼。閘:visited ≥1;finding 非空。"""
    entry = {"schema_version": ROTATION_VERSION, "seat": seat, "at_ms": at_ms,
             "visited": list(visited or []), "finding": finding,
             "delegated": list(delegated or [])}
    entry.update(_ctx_fields(ctx_pct, ctx_source))
    _validate(entry, PATROL_SCHEMA, "patrol")
    _check_patrol_line(entry, "patrol")
    return _append_jsonl(root, seat, PATROL_NAME, entry, at_ms, PATROL_SCHEMA,
                         "seat", "seat", seat, line_check=_check_patrol_line)


def read_scouts(root, seat):
    """讀端核形狀+儲君≠自己;**不核 role∈主管集**(寫端才核)——主管集會隨
    EZ2_AIB_CAPTAIN_ROLES 變,讀端核會讓舊帳因環境不同整檔拒讀。同艦/角色是否
    仍成立交 handover_probe (f) 對照席位檔判。"""
    return _read_jsonl(root, seat, SCOUT_NAME, SCOUT_SCHEMA, "seat",
                       line_check=_check_scout_line)


def read_delegations(root, seat):
    return _read_jsonl(root, seat, DELEGATION_NAME, DELEGATION_SCHEMA, "seat",
                       line_check=_check_delegation_line)


def read_patrols(root, seat):
    return _read_jsonl(root, seat, PATROL_NAME, PATROL_SCHEMA, "seat",
                       line_check=_check_patrol_line)


def patrol_overdue(entries, now_ms, ctx_pct):
    """巡查逾期判定(純函式,PLAN §4-Q1 預設):
    從未巡查=逾期;at_ms 比 now 晚 >PATROL_FUTURE_SLACK_MS 的紀錄忽略(qa O3);
    否則以**最後一筆有效**巡查為基準,ctx 比它高 ≥PATROL_EVERY_PCT
    **或** 距它 ≥PATROL_EVERY_MS,任一成立=逾期。
    ctx_pct=None(拿不到 ctx)時只看時間。上一筆若無 ctx_pct 也只看時間。
    常數不吃參數:the maintainer 改 Q1 只動常數。"""
    if not isinstance(now_ms, int) or now_ms < 0:
        raise RotationError("now_ms must be a non-negative integer: %r" % (now_ms,))
    if ctx_pct is not None and (not isinstance(ctx_pct, int)
                                or isinstance(ctx_pct, bool)
                                or not 0 <= ctx_pct <= 100):
        raise RotationError("ctx_pct 須為 0..100 整數或 None:%r" % (ctx_pct,))
    # qa O3:未來時戳的紀錄(at_ms > now+300s)不作基準——否則一筆寫成明年的
    # 巡查會讓判定永遠 False;過濾後沒剩=從未巡=逾期。
    valid = [e for e in (entries or [])
             if int(e["at_ms"]) <= now_ms + PATROL_FUTURE_SLACK_MS]
    if not valid:
        return True
    last = max(valid, key=lambda e: e["at_ms"])
    if now_ms - int(last["at_ms"]) >= PATROL_EVERY_MS:
        return True
    last_pct = last.get("ctx_pct")
    if ctx_pct is not None and last_pct is not None \
            and ctx_pct - int(last_pct) >= PATROL_EVERY_PCT:
        return True
    return False


def _read_jsonl(root, seat, name, schema, seat_field, line_check=None):
    """該席某存放的**唯一**讀取路徑;回傳逐筆 entry 的 list(無檔=空 list)。

    與 _append_jsonl 同課(E6.3 高5,08-15 抽出):讀端的硬化也只有一份。
    任何壞行=整檔拒用(fail-closed),不靜默跳過——跳過最新那筆壞行等於
    默默退回更舊的狀態,那不是容錯是降級。"""
    seat_file(root, seat, name)  # seat 字元閘+逃逸閘(名義檢查,沿用同一道門)
    entries = []
    # 讀端同樣不跟隨 symlink(高5):寫端擋住不夠,讀到 root 外的假
    # checkpoint 一樣是被接管的交棒鏈。高3(末輪):父目錄段同走 openat 鏈。
    opened = _open_checkpoints(root, seat, write=False, name=name)
    if opened is not None:
        seat_fd, fd = opened
        try:
            # 低2(末輪):讀端共享鎖——與寫端 LOCK_EX(write+fsync 全程持鎖)
            # 互斥。無鎖讀會拿到 append 中的半行(誤報整檔損毀)或 fsync 前
            # 的舊快照(以舊 checkpoint 交棒)。fstat/size 檢一併入鎖內,
            # 看的是寫端離手後的終態;鎖掛同一 fd,close 即釋放。
            # 低1(十輪):fstat 本身的 OSError 也要收——不收=fd 洩漏+rc=1
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                st = os.fstat(fd)
            except OSError as err:
                raise RotationError("%s 狀態不可讀:%s" % (name, err))
            if st.st_nlink > 1:
                raise RotationError("%s 連結數 %d>1(hard link)"
                                    "—— 拒讀" % (name, st.st_nlink))
            if st.st_size > MAX_SOURCE_BYTES:
                raise RotationError("%s 過大(%d bytes > %d)——"
                                    "拒讀" % (name, st.st_size,
                                             MAX_SOURCE_BYTES))
            # 中2(08-14 五輪):壞 UTF-8 在 for line in fh 的下一行才炸,
            # 不包就是裸 traceback rc=1——壞編碼=檔案損毀,同壞行一路 rc=2。
            try:
                raw = _pread_all(fd, st.st_size)
                # 中3(九輪):實讀長度必須等於 fstat 時的大小——鎖內此為
                # 不變量,破了=fs 異常,同樣拒用。
                if len(raw) != st.st_size:
                    raise RotationError(
                        "%s 實讀 %d != 預期 %d(並行寫入/截斷?)"
                        "—— 拒用,請重試" % (name, len(raw), st.st_size))
                raw_text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as err:
                raise RotationError(
                    "%s 讀取/解碼失敗(檔案損毀?):%s" % (name, err))
        finally:
            os.close(fd)
            os.close(seat_fd)
        # 八輪:行界只認 \n(splitlines 會在字串內 U+2028/2029 拆行)
        for lineno, line in enumerate(raw_text.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                _validate(entry, schema, "entry")
            except (ValueError, RotationError) as err:
                # 中1:原本靜默跳過——最新那筆壞掉時會退回更舊的
                # checkpoint 繼續交棒,那不是 fail-closed 是默默降級。
                raise RotationError(
                    "%s 第 %d 行損毀(%s)—— 拒絕以更舊狀態"
                    "交棒,請人工檢視" % (name, lineno + 1, err))
            # 中3(08-14 二輪):外部來源與產生端同一把尺——
            # 別人寫進來的空白陷阱和自己寫的一樣不算數。
            if line_check is not None:
                line_check(entry, "%s 第 %d 行" % (name, lineno + 1))
            # 中1(08-14 四輪):檔在 dev2/ 底下不代表行屬於 dev2——
            # 塞一行 seat=admin 的合法資料就能借檔交棒成別席。
            if entry[seat_field] != seat:
                raise RotationError(
                    "%s 第 %d 行 %s=%r 非本席 %r ——"
                    "檔案被污染,拒用"
                    % (name, lineno + 1, seat_field, entry[seat_field], seat))
            entries.append(entry)
    return entries


def build_handoff(checkpoint, at_ms, extra_traps=None):
    """checkpoint -> 交接檔。陷阱節再驗一次(雙保險),24h 顧問期就地起算。"""
    traps = list(checkpoint["traps"]) + list(extra_traps or [])
    if not traps:
        raise RotationError("handoff without traps is forbidden (決策 8)")
    handoff = {
        "schema_version": ROTATION_VERSION,
        "seat": checkpoint["seat"],
        "task": checkpoint["task"],
        "at_ms": at_ms,
        "from_checkpoint_at_ms": checkpoint["at_ms"],
        "ctx_pct_at_checkpoint": checkpoint["ctx_pct"],
        # 低1(十二輪):防禦性複製——library 呼叫者事後改原 checkpoint 的
        # list,不得暗中改變已建立 handoff 的內容與指紋。
        "done": list(checkpoint["done"]),
        "not_done": list(checkpoint["not_done"]),
        "next_step": checkpoint["next_step"],
        "traps": traps,
        "advisor": {
            "seat": checkpoint["seat"],
            "until_ms": at_ms + ADVISOR_PERIOD_MS,
            "note": "原窗保留 24h 可諮詢(決策 8);期滿前不關交棒者視窗",
        },
        "acceptance_spec": {
            "sentences_required": SENTENCES_REQUIRED,
            "slots": list(EXPECTED_SLOTS),
            "note": "接棒者恰三句回述,交棒者(顧問)confirmed_by 才算成立",
        },
    }
    if checkpoint.get("evidence_refs"):
        handoff["evidence_refs"] = list(checkpoint["evidence_refs"])
    # 高4(08-14 審):產生端絕不落出非法交接檔。--now-ms 可為負、可早於
    # checkpoint,原本都以 rc=0 輸出、直到 accept 才被拒——把驗證搬到出口,
    # 產生與接收兩端用同一套 validate_handoff,不會漂移。
    return validate_handoff(handoff)


def build_acceptance(handoff, understanding, by_seat, at_ms, confirmed_by=None):
    """三句理解驗收:形狀機器驗,語意由**交棒者本人**確認才算成立。"""
    validate_handoff(handoff)
    _validate(understanding, UNDERSTANDING_SCHEMA, "understanding")
    if not isinstance(at_ms, int) or at_ms < handoff["at_ms"]:
        # 中1(08-14 審):「交接前已驗收」的紀錄破壞稽核時序。
        raise RotationError(
            "驗收時刻 %r 早於交棒時刻 %d —— 時序不合" % (at_ms, handoff["at_ms"]))
    # 高3:minLength=1 讓 [" ", "x", "y"] 過關;空白不是理解
    blanks = [i for i, sentence in enumerate(understanding["sentences"])
              if not sentence.strip()]
    if blanks:
        raise RotationError(
            "理解句第 %s 句為空白 —— 三句驗收不是三個空格(決策 8)"
            % ", ".join(str(i + 1) for i in blanks))
    # 中4(08-14 七輪):理解句會原樣入 Ledger summary——控制字元
    # (含換行/ANSI/C1 區)=日誌注入與終端顯示欺騙面,拒收。
    dirty = [i + 1 for i, sentence in enumerate(understanding["sentences"])
             if _CTRL_RE.search(sentence)]
    if dirty:
        raise RotationError(
            "理解句第 %s 句含控制字元 —— 句子要能安全入帳,不收換行/ANSI"
            % ", ".join(map(str, dirty)))
    if not _SEAT_RE.fullmatch(by_seat or ""):
        raise RotationError("invalid seat_to: %r" % (by_seat,))
    # 中5(08-14 三輪):自己接自己的棒,原本只在帶 confirmed_by 時被擋——
    # 不帶確認就能給自己留一筆 pending_confirm 接棒紀錄。席位不同是輪替
    # 的定義,不是確認階段才要滿足的條件。
    if by_seat == handoff["advisor"]["seat"]:
        raise RotationError("接棒席與交棒席相同 —— 自己接自己的棒不算輪替")
    # 中3(08-14 七輪):期滿後產 pending_confirm=永遠不可能轉 accepted 的
    # 懸置狀態,自動化拿 rc=3 會無限空等。期滿=本 handoff 驗收通道關閉,
    # 有無確認都拒(先前「期滿可留 pending」的裁定由此更正——誠實的紅
    # 勝過永懸的黃)。
    if at_ms >= handoff["advisor"]["until_ms"]:
        raise RotationError(
            "顧問期已滿(until_ms %d <= at_ms %d)—— 本 handoff 已不可能"
            "完成驗收,請重新交棒" % (handoff["advisor"]["until_ms"], at_ms))
    # 高3(08-15 關):確認不再與提交同一次呼叫——同一個呼叫者同時控制三句
    # 與確認人時,「交棒者背書」只是這個呼叫者自己寫的一個字串。本階段一律
    # 產 pending_confirm,背書走 confirm_acceptance(另一次呼叫、另一方)。
    if confirmed_by is not None:
        raise RotationError(
            "accept 不再收 --confirmed-by(E6.3 高3)—— 接棒者先 accept 落"
            "pending_confirm,交棒者再跑 `confirm` 背書;一次呼叫同時提交"
            "與確認,三句驗收不構成雙方協定")
    status = "pending_confirm"
    # 高4(08-14 二輪):驗收要綁「哪一份」handoff——同 task/seat 下換掉
    # handoff 內容,舊驗收不得仍然像在背書新內容。指紋=正規化 handoff 全文。
    # (高2 設計邊界:三句與三 slot 的語意對應機器驗不了,那正是 confirmed_by
    # 承擔的判斷;機器驗形狀+綁內容,語意由交棒者背書——把這條寫死在這裡,
    # 免得後人以為是漏了。)
    handoff_sha256 = hashlib.sha256(json.dumps(
        handoff, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    # acceptance_uid 綁「哪一份 handoff、誰接、何時、哪三句」——決定性導出
    # 而非隨機:同一次提交重跑得同一個 uid,不會在存放裡長出兩筆孿生紀錄。
    acceptance_uid = "acc_" + hashlib.sha256(json.dumps(
        [handoff_sha256, by_seat, at_ms, list(understanding["sentences"])],
        ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    return {
        "schema_version": ROTATION_VERSION,
        "acceptance_uid": acceptance_uid,
        "task": handoff["task"],
        "seat_from": handoff["seat"],
        "seat_to": by_seat,
        "at_ms": at_ms,
        "from_checkpoint_at_ms": handoff["from_checkpoint_at_ms"],
        "handoff_sha256": handoff_sha256,
        # 末輪 fix-forward:防禦性複製(與 build_handoff 低1 十二輪同課)——
        # library 呼叫者事後改 understanding 的 list,不得暗中改變已建立
        # 驗收紀錄的三句與其入帳指紋。
        "sentences": list(understanding["sentences"]),
        "confirmed_by": confirmed_by,
        "status": status,
        "advisor_until_ms": handoff["advisor"]["until_ms"],
    }


def write_acceptance(root, acceptance):
    """驗收紀錄落檔(E6.3 高5)。

    存放在**接棒席**目錄下,不是交棒席:這份紀錄的唯一消費者是「這個席位
    可不可以就任」那道問題,而問的人手上有的是接棒席名。存在提問者會去看
    的地方,才不必為了回答它去掃全部席位目錄(掃描本身會變成另一個要驗的
    路徑面)。"""
    _validate(acceptance, ACCEPTANCE_SCHEMA, "acceptance")
    return _append_jsonl(root, acceptance["seat_to"], ACCEPTANCES_NAME,
                         acceptance, acceptance["at_ms"], ACCEPTANCE_SCHEMA,
                         "seat_to", "acceptance_uid",
                         acceptance["acceptance_uid"])


def acceptance_states(root, seat_to, task=None):
    """該接棒席每個 acceptance_uid 的**最新**狀態,依 at_ms 排序。

    存放 append-only,轉態靠加新行——所以「現在是什麼狀態」一律是同 uid
    的最新那一行,不是第一行。讀端與寫端同一把尺由 _read_jsonl 保證。"""
    latest = {}
    for entry in _read_jsonl(root, seat_to, ACCEPTANCES_NAME,
                             ACCEPTANCE_SCHEMA, "seat_to"):
        if task is not None and entry["task"] != task:
            continue
        prev = latest.get(entry["acceptance_uid"])
        if prev is None or entry["at_ms"] >= prev["at_ms"]:
            latest[entry["acceptance_uid"]] = entry
    return sorted(latest.values(), key=lambda e: (e["at_ms"],
                                                  e["acceptance_uid"]))


def confirm_acceptance(root, seat_to, acceptance_uid, confirmed_by, at_ms):
    """第二階段:交棒者背書,pending_confirm -> accepted(E6.3 高3)。

    刻意讀既有 pending 紀錄再轉態,而不是憑參數重建一份:三句在第一階段就
    凍結在存放裡,背書者只能背書「已經寫下的那三句」,不能順手換掉內容。"""
    if not _SIG_RE.fullmatch(confirmed_by or ""):
        raise RotationError("confirmed_by must be a valid sig, got %r"
                            % (confirmed_by,))
    if not isinstance(at_ms, int) or at_ms < 0:
        raise RotationError("at_ms must be a non-negative integer: %r"
                            % (at_ms,))
    states = acceptance_states(root, seat_to)
    record = next((e for e in states
                   if e["acceptance_uid"] == acceptance_uid), None)
    if record is None:
        # fail-closed:查無提交紀錄就沒有東西可背書。這正是「手工訊息交接」
        # 會落到的分支——不是背書失敗,是根本沒有人提交過理解。
        raise RotationError(
            "查無 acceptance_uid %r(席位 %s)—— 沒有提交過的三句理解就沒有"
            "可背書的對象;接棒者要先跑 accept" % (acceptance_uid, seat_to))
    if record["status"] == "accepted":
        raise RotationError(
            "acceptance %s 已於 at_ms %d 由 %r 背書 —— 不重複背書"
            % (acceptance_uid, record["at_ms"], record["confirmed_by"]))
    # 高2 沿用:確認人必須是交棒席本人——顧問期的意義就在「交棒者背書」。
    if confirmed_by.split("@", 1)[0] != record["seat_from"]:
        raise RotationError(
            "confirmed_by %r 不是交棒席 %r —— 別人不能替交棒者背書"
            % (confirmed_by, record["seat_from"]))
    if at_ms < record["at_ms"]:
        raise RotationError(
            "背書時刻 %d 早於提交時刻 %d —— 時序不合" % (at_ms, record["at_ms"]))
    # 中3(七輪)沿用:期滿=驗收通道關閉,有無確認都拒;誠實的紅勝過永懸的黃。
    if at_ms >= record["advisor_until_ms"]:
        raise RotationError(
            "顧問期已滿(until_ms %d <= at_ms %d)—— 本次驗收已不可能完成,"
            "請重新交棒" % (record["advisor_until_ms"], at_ms))
    accepted = dict(record)
    accepted["at_ms"] = at_ms
    accepted["confirmed_by"] = confirmed_by
    accepted["status"] = "accepted"
    return write_acceptance(root, accepted)


def advisor_status(handoff, now_ms, machine_ctx=None, manual_release=False):
    """顧問期(G1-07,PLAN §2.3:改 CTX 制)。

    解除條件只有兩個,且都不是「時間到」:
      - machine_ctx(lifecycle.read_machine_ctx 形狀:{pct, stale, ...})非 stale
        且 pct ≥ ADVISOR_STOP_CTX(96)→ released_by="ctx96"
      - operator 明示 manual_release=True → released_by="manual"(人工流程,
        --ctx-pct 自報值**不得**觸發 ctx96——自報 96 就是「AI 自己決定何時退場」)
    24h(handoff.advisor.until_ms,validate_handoff 釘死不可自訂)只是**後備天花板**:
    到了仍 in_advisory=True,加標 needs_human=True 提示人裁,不自動解除。
    stale 的機器值等同沒有機器值。"""
    validate_handoff(handoff)
    if not isinstance(now_ms, int) or now_ms < 0:
        raise RotationError("now_ms must be a non-negative integer: %r"
                            % (now_ms,))
    # 低1(08-14 三輪):交棒前的時刻沒有「顧問期」可言——查得到
    # in_advisory=true+remaining>24h 是時序謊言,拒答。
    if now_ms < handoff["at_ms"]:
        raise RotationError("查詢時刻 %d 早於交棒時刻 %d —— 時序不合"
                            % (now_ms, handoff["at_ms"]))
    until = handoff["advisor"]["until_ms"]
    machine_pct = None
    if isinstance(machine_ctx, dict) and not machine_ctx.get("stale", True) \
            and machine_ctx.get("source") == "machine":
        pct = machine_ctx.get("pct")
        if isinstance(pct, int) and not isinstance(pct, bool):
            machine_pct = pct
    released_by = None
    if machine_pct is not None and ADVISOR_STOP_CTX is not None \
            and machine_pct >= ADVISOR_STOP_CTX:
        released_by = "ctx96"
    elif manual_release:
        released_by = "manual"
    in_advisory = released_by is None
    ceiling_passed = now_ms >= until
    return {"advisor_seat": handoff["advisor"]["seat"],
            "in_advisory": in_advisory,
            "released_by": released_by,
            "machine_ctx_pct": machine_pct,
            "advisor_stop_ctx": ADVISOR_STOP_CTX,
            "ceiling_ms": until,
            "ceiling_passed": ceiling_passed,
            "needs_human": in_advisory and ceiling_passed,
            # 相容欄(舊呼叫端):until_ms/remaining_ms 語意改為「天花板」
            "until_ms": until,
            "remaining_ms": max(0, until - now_ms)}


# ---- G1-06 遺詔:格式契約 docs/testament-format.md(pm 出稿,Q2 裁六節)----
TESTAMENT_REQUIRED_SECTIONS = ("身份與任期", "完成與未竟", "陷阱節", "交辦清單",
                               "致新艦長一句", "我做錯的事")
# ★ 97 段(the maintainer 08-18 21:1x 常設規則)專屬的第七節:clear 之前那個「自問」的落點。
#   原話:「自問『如果 clear,有什麼是下一個 AI 不知道的?』」——
#   **沒落檔的自問等於沒發生**,所以它必須是一個機器驗得到的節,不是一個心法。
#   ★ 刻意**只在 --final-clear 這條路徑上必填**:既有六節的契約一字未動,
#     96 段寫的遺詔照舊合法(加必含節=把全艦既有遺詔一次判死,那是換一種方式毀帳)。
FINAL_CLEAR_SECTION = "下一個 AI 不會知道的事"
TESTAMENT_MAX_BYTES = 20480
_TESTAMENT_FILE_RE = re.compile(r"^[0-9]{1,16}\.md$")
_RECEIPT_RE = re.compile(r"^[A-Za-z0-9._:-]{6,128}$")


def validate_testament_text(text, final_clear=False):
    """遺詔本文的機器驗(契約 R1-R3):≤20480 bytes、六必含節 `## 標題` 逐字各恰一次
    (順序不限)、每必含節正文非空。回 {"sections": [...出現順序], "bytes": n}。
    R4(指標不貼內容)第一刀不驗——契約義務,機器濾毒屬第二刀。

    final_clear=True(97 段,限 claude):**多要求一節** `## 下一個 AI 不會知道的事`,
    且正文非空。既有六節的契約不動——這一節只在這條路徑上必填。"""
    if not isinstance(text, str):
        raise RotationError("遺詔必須是 UTF-8 文字")
    raw = text.encode("utf-8")
    if len(raw) > TESTAMENT_MAX_BYTES:
        raise RotationError("遺詔 %d bytes 超過上限 %d(契約 R1:指標不貼內容)"
                            % (len(raw), TESTAMENT_MAX_BYTES))
    if not raw.strip():
        raise RotationError("遺詔為空")
    sections = []      # (title, body_lines)
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = [line[3:].strip(), []]
            sections.append(current)
        elif line.startswith("#") and not line.startswith("##"):
            current = None  # 一級標題:文件題名,不屬任何節
        elif current is not None:
            current[1].append(line)
    titles = [t for t, _ in sections]
    required = TESTAMENT_REQUIRED_SECTIONS + ((FINAL_CLEAR_SECTION,) if final_clear else ())
    for need in required:
        n = titles.count(need)
        if n == 0:
            raise RotationError("遺詔缺必含節「## %s」(契約 R2)" % need)
        if n > 1:
            raise RotationError("遺詔必含節「## %s」重複 %d 次(契約 R2)" % (need, n))
    for title, body in sections:
        if title in required and not "".join(body).strip():
            raise RotationError("遺詔必含節「## %s」正文為空(契約 R3)" % title)
    return {"sections": titles, "bytes": len(raw),
            "final_clear": FINAL_CLEAR_SECTION in titles}


def _testament_dir_fd(root, seat, create):
    """開 <root>/<seat>/testament/ 目錄 fd(逃逸閘沿 seat_file;不跟隨 symlink)。"""
    seat_dir = seat_file(root, seat, TESTAMENT_RECEIPTS_NAME).parent
    if create:
        try:
            os.makedirs(str(seat_dir / TESTAMENT_DIR), mode=0o700, exist_ok=True)
        except OSError as err:
            raise RotationError("testament 目錄建不起來:%s" % err)
    try:
        return os.open(str(seat_dir / TESTAMENT_DIR),
                       os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as err:
        if not create and err.errno == errno.ENOENT:
            return None
        raise RotationError("testament 目錄拒開:%s" % err)


def write_testament(root, seat, at_ms, text, receipt=None, ctx_pct=None,
                    ctx_source="none", final_clear=False):
    """遺詔落盤(G1-06):驗契約 → <seat>/testament/<at_ms>.md(O_EXCL 不覆寫)→
    有 receipt 才 append 一行收據到 testament-receipts.jsonl(綁本檔 sha256)。
    不合契約=一 byte 不落盤;receipt 格式錯=md 也不落(先驗後寫)。
    ★ 誠實邊界(qa O2):receipt **只驗格式** `^[A-Za-z0-9._:-]{6,128}$`,不反查任何
      通道——第一刀擋的是「忘了投遞就 compact」(沒填收據 compact_ready 就 false),
      **不擋「填了假收據」**。可反查的收據(aib dm msg_id / 帳本 ev_ 事件)列第二刀。"""
    if not isinstance(at_ms, int) or isinstance(at_ms, bool) or at_ms < 0:
        raise RotationError("at_ms must be a non-negative integer: %r" % (at_ms,))
    info = validate_testament_text(text, final_clear=final_clear)
    if receipt is not None and not _RECEIPT_RE.fullmatch(receipt):
        raise RotationError("receipt 格式不合 ^[A-Za-z0-9._:-]{6,128}$:%r" % (receipt,))
    fname = "%d.md" % at_ms
    if not _TESTAMENT_FILE_RE.fullmatch(fname):
        raise RotationError("at_ms 太大,檔名超界:%r" % (at_ms,))
    payload = text.encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    ctx = _ctx_fields(ctx_pct, ctx_source)  # 先驗,錯了 md 不落
    dfd = _testament_dir_fd(root, seat, create=True)
    try:
        try:
            fd = os.open(fname, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dfd)
        except FileExistsError:
            raise RotationError("testament/%s 已存在,不覆寫(換 --now-ms)" % fname)
        except OSError as err:
            raise RotationError("遺詔落檔失敗:%s" % err)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except OSError as err:
            os.close(fd)
            try:
                os.unlink(fname, dir_fd=dfd)
            except OSError:
                pass
            raise RotationError("遺詔寫入失敗:%s" % err)
        os.close(fd)
        try:
            os.fsync(dfd)
        except OSError:
            pass
    finally:
        os.close(dfd)
    out = {"seat": seat, "at_ms": at_ms, "testament_file": fname,
           "testament_sha256": sha, "bytes": info["bytes"],
           "sections": info["sections"], "receipt": None}
    if receipt is not None:
        entry = {"schema_version": ROTATION_VERSION, "seat": seat, "at_ms": at_ms,
                 "testament_file": fname, "testament_sha256": sha,
                 "receipt": receipt}
        entry.update(ctx)
        _validate(entry, TESTAMENT_RECEIPT_SCHEMA, "testament receipt")
        _append_jsonl(root, seat, TESTAMENT_RECEIPTS_NAME, entry, at_ms,
                      TESTAMENT_RECEIPT_SCHEMA, "seat", "seat", seat)
        out["receipt"] = receipt
    return out


def read_testament_receipts(root, seat):
    return _read_jsonl(root, seat, TESTAMENT_RECEIPTS_NAME,
                       TESTAMENT_RECEIPT_SCHEMA, "seat")


def _seat_testament_dir(root, seat):
    return os.path.join(str(seat_file(root, seat, CHECKPOINTS_NAME).parent), TESTAMENT_DIR)


def testament_status(root, seat):
    """{"testament": bool, "receipt": bool, "compact_ready": bool, ...}。
    ★ compact_ready 只有「遺詔已落盤 **且** 至少一筆收據綁著一份**存在的**遺詔檔」
      才 true(PLAN §2.4 列 5:ctx≥96 無投遞收據=🔴 且不得 compact)。"""
    seat_file(root, seat, TESTAMENT_RECEIPTS_NAME)  # 席位字元閘+逃逸閘
    files = {}   # name -> 現在磁碟上的 sha256
    dfd = _testament_dir_fd(root, seat, create=False)
    if dfd is not None:
        try:
            for name in os.listdir(dfd):
                if not _TESTAMENT_FILE_RE.fullmatch(name):
                    continue
                try:
                    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                 dir_fd=dfd)
                except OSError:
                    continue
                try:
                    st = os.fstat(fd)
                    if not stat_mod.S_ISREG(st.st_mode) or st.st_size == 0 \
                            or st.st_size > TESTAMENT_MAX_BYTES * 4:
                        continue
                    files[name] = hashlib.sha256(_pread_all(fd, st.st_size)).hexdigest()
                except OSError:
                    continue
                finally:
                    os.close(fd)
        finally:
            os.close(dfd)
    receipts = read_testament_receipts(root, seat)
    # F1(艦長 22:07 終審):收據綁的是「那一份內容」不是檔名——落盤後被改
    # (`echo TAMPERED >>`)的遺詔,收據投遞的不是它;sha 不符=不算綁定。
    bound = [r for r in receipts
             if files.get(r["testament_file"]) == r["testament_sha256"]]
    tampered = sorted({r["testament_file"] for r in receipts
                       if r["testament_file"] in files
                       and files[r["testament_file"]] != r["testament_sha256"]})
    has_t = bool(files)
    has_r = bool(bound)
    # 97 段:最新那份遺詔有沒有「下一個 AI 不會知道的事」節。
    # ★ 只看**最新**一份:自問問的是「現在 clear 會漏掉什麼」,舊的那份答的是舊的狀態。
    final_clear = False
    if files:
        newest = sorted(files)[-1]
        try:
            with open(os.path.join(_seat_testament_dir(root, seat), newest),
                      encoding="utf-8") as fh:
                final_clear = validate_testament_text(fh.read()).get("final_clear", False)
        except (OSError, RotationError):
            final_clear = False
    return {"seat": seat, "testament": has_t, "receipt": has_r,
            "compact_ready": has_t and has_r,
            "final_clear": final_clear,
            # 97 段(限 claude)要三者齊:遺詔 + 投遞收據 + 自問已落檔
            "final_clear_ready": has_t and has_r and final_clear,
            "testament_files": sorted(files), "receipt_count": len(bound),
            "unbound_receipts": len(receipts) - len(bound),
            "tampered_files": tampered}


# ---- G1-01:CTX 真值解析(CLI 共用)----

def resolve_ctx(uuid=None, ctx_pct=None, now_ms=None, ctx_dir=None):
    """回 {"ctx_pct", "ctx_source", "rotation_mode", "machine": ctx_or_None}。
    ★ 有 uuid 且機器值非 stale → **只吃機器值**(自報值即使給了也不用,並標
      ignored_self_report=True);拿不到/stale → manual_only,此時才輪到自報值
      (ctx_source=self_reported);兩者皆無 → ctx_pct=None, ctx_source=none。
    自動轉換(check 的 auto_rotate、advisor 的 ctx96)只認 rotation_mode=auto。"""
    import lifecycle  # 延遲 import:lifecycle 頂層 import rotation,反向會循環
    if ctx_pct is not None and (not isinstance(ctx_pct, int)
                                or isinstance(ctx_pct, bool)
                                or not 0 <= ctx_pct <= 100):
        raise RotationError("--ctx-pct 須在 0..100:%r" % (ctx_pct,))
    machine = None
    mode = "manual_only"
    if uuid:
        mode, machine = lifecycle.rotation_mode(uuid, ctx_dir=ctx_dir,
                                                now_ms=now_ms)
    out = {"rotation_mode": mode, "machine": machine,
           "ignored_self_report": False}
    if mode == "auto":
        out.update({"ctx_pct": machine["pct"], "ctx_source": "machine",
                    "ignored_self_report": ctx_pct is not None})
    elif ctx_pct is not None:
        out.update({"ctx_pct": ctx_pct, "ctx_source": "self_reported"})
    else:
        out.update({"ctx_pct": None, "ctx_source": "none"})
    return out


def checkpoint_event_draft(entry, kind_note, sig, ai_id):
    zh = "輪替%s:%s/%s(ctx %s%%)" % (
        kind_note, entry.get("seat", entry.get("seat_to")),
        entry["task"],
        entry.get("ctx_pct", entry.get("ctx_pct_at_checkpoint", "—")))
    refs = entry.get("evidence_refs") or []
    if refs:
        # 末輪 fix-forward:checkpoint 的證據鏈要進事件可稽核。event schema
        # 頂層 evidence_refs 收的是 evd_(evidence 帳,E1.1 未落地),rotation
        # 的是 ev_(帳本事件)——pattern 分叉(SWEEP 已點名),硬塞=Ledger
        # 必拒=rc 0 假成功。落 summary,走「封閉 body 塞不下就進 summary」
        # 的中3 先例;超量截斷並明示筆數。
        joined = ",".join(refs)
        if len(joined) > 400:
            joined = ",".join(refs[:5]) + "…(共 %d 筆)" % len(refs)
        zh += ";證據=%s" % joined
    if "sentences" in entry:
        # 中3(08-14 審):原本事件只剩 task 摘要,無法稽核「誰確認了哪份
        # 理解」。event schema 的 body 是封閉形(summary/subject/data_ref/
        # verdict/approval_ref),塞不進自由欄——所以把可稽核的最小集合寫進
        # summary:交棒鏈、確認人、狀態,加整份 acceptance 的正規化指紋,
        # 指紋綁的就是「哪三句、誰確認」那份紀錄本體。
        digest = hashlib.sha256(json.dumps(
            entry, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        # 中3(08-14 五輪):三句經 JSON escaping(反斜線/引號)可能把
        # summary 推破 i18n_text 的 2000 上限——rc=0 而 Ledger 拒收=假成功。
        # 超長就截斷三句並明示,稽核完整性由驗收指紋承擔(preimage=驗收紀錄)。
        sent = json.dumps(entry["sentences"], ensure_ascii=False)
        if len(sent) > 1200:
            sent = sent[:1200] + "…(截斷,全文以驗收指紋稽核)"
        # 中3(三輪):只有 hash 沒有 preimage 稽核不了「哪份理解」——
        # 三句原文直接進事件(3×300 字上限+前綴,仍在 i18n_text 2000 內)。
        # 中4(四輪):補 handoff 指紋——事件要答得出「背書的是哪份交接檔」;
        # 低2(四輪):→ 換 ->,對齊全域輸出格式 SOP(禁裝飾性符號)。
        zh += ";%s->%s;確認人=%s;狀態=%s;handoff=sha256:%s;三句=%s;驗收指紋=sha256:%s" % (
            entry["seat_from"], entry["seat_to"],
            entry.get("confirmed_by") or "(未確認)",
            entry["status"], entry.get("handoff_sha256") or "?",
            sent, digest)
    return {
        "kind": "memory.checkpoint",
        "actor": {"ai_id": ai_id, "sig": sig, "kind": "system", "engine": "AIB"},
        "body": {
            "summary": {
                "zh-TW": zh,
                "en": "rotation %s: %s" % (kind_note, entry["task"]),
            },
            "subject": {"kind": "memory",
                        "id": "rotation:%s" % entry["task"]},
        },
    }


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="rotation",
        description="Ez2 AIB 艦長級輪替:checkpoint+接棒協定(E6.3)")
    parser.add_argument("command",
                        choices=["checkpoint", "check", "handoff", "accept",
                                 "confirm", "acceptance-status",
                                 "advisor-status", "scout", "delegate",
                                 "patrol", "testament", "testament-status"])
    parser.add_argument("--root", help="輪替存放根目錄")
    parser.add_argument("--seat", help="席位名(交棒方)")
    parser.add_argument("--task", help="任務節點 id(如 E6.3)")
    parser.add_argument("--ctx-pct", type=int, help="目前 CTX 百分比")
    parser.add_argument("--threshold", type=int, default=DEFAULT_ROTATE_THRESHOLD,
                        help="check / handoff 用:輪替門檻(預設讀 policy 表上要求交接那格;handoff 只准更嚴)")
    parser.add_argument("--next-step", help="checkpoint 用:下一步(必填)")
    parser.add_argument("--trap", action="append", default=[],
                        help="陷阱節條目(可重複;checkpoint 至少一條)")
    parser.add_argument("--done", action="append", default=[],
                        help="已完成項(可重複)")
    parser.add_argument("--not-done", action="append", default=[],
                        help="未完成項(可重複)")
    parser.add_argument("--evidence-ref", action="append", default=[],
                        help="佐證事件 ev_...(可重複)")
    parser.add_argument("--now-ms", type=int, default=None,
                        help="時刻注入(測試用;預設牆鐘)")
    parser.add_argument("--out", help="handoff 用:交接檔輸出路徑(省略=stdout)")
    parser.add_argument("--handoff", help="accept/advisor-status 用:交接檔路徑")
    parser.add_argument("--understanding", help="accept 用:三句理解 JSON 路徑")
    parser.add_argument("--by-seat", help="accept 用:接棒席位名")
    parser.add_argument("--confirmed-by", help="confirm 用:交棒者(顧問)背書 sig")
    parser.add_argument("--seat-to",
                        help="confirm/acceptance-status 用:接棒席位名")
    parser.add_argument("--acceptance-uid", help="confirm 用:要背書的驗收 uid")
    parser.add_argument("--emit-event", action="store_true",
                        help="輸出 memory.checkpoint 事件草稿")
    # ---- G1 ----
    parser.add_argument("--uuid", help="席位 session uuid:有則只吃機器 CTX"
                        "($EZ2_CTX_DIR/<uuid>.pct);拿不到/stale=manual_only")
    parser.add_argument("--kind", default="checkpoint",
                        help="checkpoint 用:checkpoint(預設)|full_summary(75%% 硬性總結)")
    parser.add_argument("--seats-dir",
                        help="scout 用:席位檔目錄(G3 艦長段位查驗;預設同 handover_probe)")
    parser.add_argument("--successor-seat", help="scout 用:繼任者席位")
    parser.add_argument("--successor-role", help="scout 用:繼任者角色(須為主管角色)")
    parser.add_argument("--reason", help="scout 用:理由")
    parser.add_argument("--to-seat", help="delegate 用:交辦對象席位")
    parser.add_argument("--work", help="delegate 用:交辦工作")
    parser.add_argument("--visited", action="append", default=[],
                        help="patrol 用:巡到的席位(可重複,至少一個)")
    parser.add_argument("--finding", help="patrol 用:發現")
    parser.add_argument("--delegated", action="append", default=[],
                        help="patrol 用:巡查後交辦了什麼(可重複)")
    parser.add_argument("--file", help="testament 用:遺詔 md 路徑")
    parser.add_argument("--receipt", help="testament 用:aib dm 投遞收據 id")
    parser.add_argument("--final-clear", action="store_true",
                        help="testament 用:97 段(限 claude)——多驗一節"
                             "「## 下一個 AI 不會知道的事」,沒落檔的自問等於沒發生")
    parser.add_argument("--manual-release", action="store_true",
                        help="advisor-status 用:operator 明示人工解除顧問期(留痕)")
    parser.add_argument("--sig", default="rotation@0e63/AIB")
    parser.add_argument("--ai-id", default="aib:localhost:rotation-protocol")
    args = parser.parse_args(argv)

    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    # 中3(08-14 六輪):--sig/--ai-id 任意值會讓 --emit-event 以 rc=0 產出
    # Ledger 拒收的草稿——假成功。actor 欄在出口驗,壞值 rc=2。
    if args.emit_event:
        if not _SIG_RE.fullmatch(args.sig or ""):
            die("--sig 不是合法 sig(seat@hex/Engine):%r" % args.sig)
        ai_id = args.ai_id or ""
        # 低1(08-14 八輪):只擋 <32 放過 DEL 與 C1 區——與 _CTRL_RE 同一把尺
        if not 1 <= len(ai_id) <= 256 or _CTRL_RE.search(ai_id):
            die("--ai-id 非法(1..256 可見字元):%r" % args.ai_id)

    if args.command == "check":
        if args.ctx_pct is None and not args.uuid:
            die("check 需要 --ctx-pct 或 --uuid")
        # 中5(08-14 審):--ctx-pct -1 --threshold -2 曾回報該輪替,101 也收。
        if args.ctx_pct is not None and not 0 <= args.ctx_pct <= 100:
            die("--ctx-pct 須在 0..100:%r" % args.ctx_pct)
        if not 1 <= args.threshold <= 100:
            die("--threshold 須在 1..100:%r" % args.threshold)
        # G1-01:有 uuid 只吃機器值;拿不到/stale → manual_only,自報值只當諮詢,
        # auto_rotate 永遠 false(不得回「可自動轉換」)。
        try:
            ctx = resolve_ctx(args.uuid, args.ctx_pct, now_ms)
        except RotationError as err:
            die(str(err))
        pct = ctx["ctx_pct"]
        # F2(艦長 22:07):「不知道」不得印成「不用轉」——無機器值且無自報 → rc=2。
        if pct is None:
            die("check 拿不到 ctx(uuid 無機器值/stale 且無 --ctx-pct;rotation_mode=%s)"
                % ctx["rotation_mode"])
        rotate = pct >= args.threshold
        sys.stdout.write(_dump({"ctx_pct": pct,
                                "ctx_source": ctx["ctx_source"],
                                "rotation_mode": ctx["rotation_mode"],
                                "ignored_self_report": ctx["ignored_self_report"],
                                "threshold": args.threshold,
                                "rotate": rotate,
                                "auto_rotate": rotate and ctx["rotation_mode"] == "auto"}))
        return 3 if rotate else 0

    if args.command == "checkpoint":
        for name, val in (("--root", args.root), ("--seat", args.seat),
                          ("--task", args.task), ("--next-step", args.next_step)):
            if not val:
                die("checkpoint 需要 %s" % name)
        if args.ctx_pct is None and not args.uuid:
            die("checkpoint 需要 --ctx-pct 或 --uuid")
        if not _SEAT_RE.fullmatch(args.seat):
            die("壞席位名:%r" % args.seat)
        if args.kind not in CHECKPOINT_KINDS:
            die("--kind 只收 %s:%r" % ("|".join(CHECKPOINT_KINDS), args.kind))
        try:
            ctx = resolve_ctx(args.uuid, args.ctx_pct, now_ms)
            if ctx["ctx_pct"] is None:
                die("checkpoint 拿不到 ctx(uuid 無機器值/stale 且無 --ctx-pct)")
            entry = write_checkpoint(args.root, args.seat, args.task,
                                     ctx["ctx_pct"], now_ms, args.next_step,
                                     args.trap, done=args.done,
                                     not_done=args.not_done,
                                     evidence_refs=args.evidence_ref,
                                     kind=args.kind, ctx_source=ctx["ctx_source"])
        except RotationError as err:
            die(str(err))
        sys.stdout.write(_dump(
            checkpoint_event_draft(entry, "檢查點", args.sig, args.ai_id)
            if args.emit_event else entry))
        return 0

    if args.command == "handoff":
        for name, val in (("--root", args.root), ("--seat", args.seat),
                          ("--task", args.task)):
            if not val:
                die("handoff 需要 %s" % name)
        if not 1 <= args.threshold <= 100:
            die("--threshold 須在 1..100:%r" % args.threshold)
        # 高3(08-14 二輪):交棒者自傳 --threshold 1 就繞過 90 閘——可以被
        # 執行者關掉的閘不是閘。handoff 的門檻只准比協定嚴;放寬要修訂的是
        # HANDOFF「輪替協定」 正本(the maintainer 的),不是命令列參數。check 保留彈性,那是
        # 諮詢訊號不是閘門。
        if args.threshold < DEFAULT_ROTATE_THRESHOLD:
            die("handoff 門檻不得低於協定值 %d(HANDOFF「輪替協定」)——要放寬請修訂"
                "正本,不是改參數" % DEFAULT_ROTATE_THRESHOLD)
        # 中4(十二輪):handoff 分支的 task 也要過字元閘——帶換行/ANSI 的
        # task 會原樣進 stderr(die 用 %r 已 repr,這裡擋在源頭更乾淨)。
        if not _TASK_RE.fullmatch(args.task or ""):
            die("壞任務名:%r" % args.task)
        # 末輪 fix-forward:seat 同 checkpoint 分支走 CLI 出口 fullmatch——
        # checkpoints_file 內雖有同閘,錯格式要與 checkpoint 分支一致
        # (壞席位名),不讓壞 seat 的錯誤讀起來像路徑問題。
        if not _SEAT_RE.fullmatch(args.seat):
            die("壞席位名:%r" % args.seat)
        try:
            # 中4:latest_checkpoint 原在 try 外,損毀 JSONL 裸 traceback rc=1。
            checkpoint = latest_checkpoint(args.root, args.seat, args.task)
            if checkpoint is None:
                die("無 checkpoint 不得交棒(先打 checkpoint):%s/%s"
                    % (args.seat, args.task))
            # 高2(08-14 審):handoff 原本完全不看 ctx,跳過 check 即可任意
            # 交棒。門檻同 check 共用 --threshold,預設 90 的出處=docs/HANDOFF.md
            # §6 輪替協定(the maintainer 08-08 修訂:CTX 到 90% 才交接)。
            if checkpoint["ctx_pct"] < args.threshold:
                die("ctx %d%% 未達交接門檻 %d%%(預設 = policy 表上要求交接那格;--threshold 只准更嚴)"
                    % (checkpoint["ctx_pct"], args.threshold))
            handoff = build_handoff(checkpoint, now_ms, extra_traps=args.trap)
        except RotationError as err:
            die(str(err))
        if args.out:
            # 低2(08-14 審)+高5(二輪):固定名 .tmp 可被預埋 symlink 讓
            # write_text 跟隨連結截斷別的檔,並行交棒也互踩同一暫存檔。
            # mkstemp=同目錄+隨機名+O_EXCL 不跟隨,replace 原子換名。
            out = Path(args.out)
            tmp_name = None
            try:
                tmp_fd, tmp_name = tempfile.mkstemp(
                    prefix=out.name + ".", dir=str(out.parent))
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                    fh.write(_dump(handoff))
                    # 低2(三輪):replace 前不 fsync=當機後拿到空檔或舊版
                    # 還回報成功;檔內容與目錄項都要落盤。
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, str(out))
                dfd = os.open(str(out.parent), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError as err:
                if tmp_name is not None:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                die("handoff 落檔失敗:%s" % err)
            print("wrote %s" % args.out, file=sys.stderr)
        else:
            sys.stdout.write(_dump(handoff))
        return 0

    if args.command == "advisor-status":
        if not args.handoff:
            die("advisor-status 需要 --handoff")
        try:
            # 中4:advisor_status 原在 try 外,非法 handoff 裸 traceback rc=1。
            handoff = _load_json(args.handoff, "handoff file")
            ctx = resolve_ctx(args.uuid, args.ctx_pct, now_ms)
            status = advisor_status(handoff, now_ms, machine_ctx=ctx["machine"],
                                    manual_release=args.manual_release)
            status["ctx_source"] = ctx["ctx_source"]
            status["rotation_mode"] = ctx["rotation_mode"]
        except RotationError as err:
            die(str(err))
        sys.stdout.write(_dump(status))
        return 0

    if args.command in ("scout", "delegate", "patrol", "testament",
                        "testament-status"):
        for flag, val in (("--root", args.root), ("--seat", args.seat)):
            if not val:
                die("%s 需要 %s" % (args.command, flag))
        if not _SEAT_RE.fullmatch(args.seat):
            die("壞席位名:%r" % args.seat)
        try:
            if args.command == "testament-status":
                status = testament_status(args.root, args.seat)
                sys.stdout.write(_dump(status))
                # rc 分層:0=可 compact、3=有遺詔無收據(待投遞)、2=無遺詔
                return 0 if status["compact_ready"] else (3 if status["testament"] else 2)
            ctx = resolve_ctx(args.uuid, args.ctx_pct, now_ms)
            pct, src = ctx["ctx_pct"], ctx["ctx_source"]
            if args.command == "scout":
                for flag, val in (("--successor-seat", args.successor_seat),
                                  ("--successor-role", args.successor_role),
                                  ("--reason", args.reason)):
                    if not val:
                        die("scout 需要 %s" % flag)
                if not _SEAT_RE.fullmatch(args.successor_seat):
                    die("壞繼任者席位名:%r" % args.successor_seat)
                entry = write_scout(args.root, args.seat, now_ms,
                                    args.successor_seat, args.successor_role,
                                    args.reason, ctx_pct=pct, ctx_source=src,
                                    seats_dir=args.seats_dir)
            elif args.command == "delegate":
                for flag, val in (("--to-seat", args.to_seat), ("--work", args.work)):
                    if not val:
                        die("delegate 需要 %s" % flag)
                if not _SEAT_RE.fullmatch(args.to_seat):
                    die("壞交辦對象席位名:%r" % args.to_seat)
                entry = write_delegation(args.root, args.seat, now_ms,
                                         args.to_seat, args.work,
                                         evidence_refs=args.evidence_ref,
                                         ctx_pct=pct, ctx_source=src)
            elif args.command == "patrol":
                if not args.visited:
                    die("patrol 需要至少一個 --visited")
                if not args.finding:
                    die("patrol 需要 --finding")
                for v in args.visited:
                    if not _SEAT_RE.fullmatch(v):
                        die("壞巡查席位名:%r" % v)
                entry = write_patrol(args.root, args.seat, now_ms, args.visited,
                                     args.finding, delegated=args.delegated,
                                     ctx_pct=pct, ctx_source=src)
            else:  # testament
                if not args.file:
                    die("testament 需要 --file")
                try:
                    src_path = Path(args.file)
                    if not src_path.is_file():
                        die("--file 不是檔案:%s" % args.file)
                    if src_path.stat().st_size > TESTAMENT_MAX_BYTES:
                        die("遺詔 %d bytes 超過上限 %d" % (src_path.stat().st_size,
                                                        TESTAMENT_MAX_BYTES))
                    text = src_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as err:
                    die("--file 讀取失敗:%s" % err)
                entry = write_testament(args.root, args.seat, now_ms, text,
                                        receipt=args.receipt, ctx_pct=pct,
                                        ctx_source=src,
                                        final_clear=args.final_clear)
        except RotationError as err:
            die(str(err))
        sys.stdout.write(_dump(entry))
        return 0

    if args.command == "confirm":
        for flag, val in (("--root", args.root), ("--seat-to", args.seat_to),
                          ("--acceptance-uid", args.acceptance_uid),
                          ("--confirmed-by", args.confirmed_by)):
            if not val:
                die("confirm 需要 %s" % flag)
        if not _SEAT_RE.fullmatch(args.seat_to):
            die("壞接棒席位名:%r" % args.seat_to)
        try:
            accepted = confirm_acceptance(args.root, args.seat_to,
                                          args.acceptance_uid,
                                          args.confirmed_by, now_ms)
        except RotationError as err:
            die(str(err))
        sys.stdout.write(_dump(
            checkpoint_event_draft(accepted, "接棒背書", args.sig, args.ai_id)
            if args.emit_event else accepted))
        return 0

    if args.command == "acceptance-status":
        for flag, val in (("--root", args.root), ("--seat-to", args.seat_to)):
            if not val:
                die("acceptance-status 需要 %s" % flag)
        if not _SEAT_RE.fullmatch(args.seat_to):
            die("壞接棒席位名:%r" % args.seat_to)
        try:
            states = acceptance_states(args.root, args.seat_to,
                                       task=args.task)
        except RotationError as err:
            die(str(err))
        accepted = [e for e in states if e["status"] == "accepted"]
        sys.stdout.write(_dump({
            "seat_to": args.seat_to,
            "task": args.task,
            "accepted_count": len(accepted),
            "pending_count": len(states) - len(accepted),
            "acceptances": states,
        }))
        # rc 分層沿 E6.3 既有語意:0=有成立的接棒、3=只有待確認、
        # 2=查無任何紀錄。三者混為一談就等於沒有協定。
        if accepted:
            return 0
        if states:
            return 3
        print("rotation: 席位 %s 查無任何驗收紀錄(task=%s)"
              % (args.seat_to, args.task or "*"), file=sys.stderr)
        return 2

    # accept
    for flag, val in (("--root", args.root), ("--handoff", args.handoff),
                      ("--understanding", args.understanding),
                      ("--by-seat", args.by_seat)):
        if not val:
            # --root 自此為必填(E6.3 高5):選填=省略即不落檔,而「省略就
            # 沒有紀錄」正是這次要關掉的繞過路徑。可被省略的閘不是閘。
            die("accept 需要 %s" % flag)
    if not _SEAT_RE.fullmatch(args.by_seat):
        die("壞接棒席位名:%r" % args.by_seat)
    try:
        handoff = _load_json(args.handoff, "handoff file")
        understanding = _load_json(args.understanding, "understanding file")
        acceptance = build_acceptance(handoff, understanding, args.by_seat,
                                      now_ms, confirmed_by=args.confirmed_by)
        write_acceptance(args.root, acceptance)
    except RotationError as err:
        die(str(err))
    sys.stdout.write(_dump(
        checkpoint_event_draft(acceptance, "接棒", args.sig, args.ai_id)
        if args.emit_event else acceptance))
    return 0 if acceptance["status"] == "accepted" else 3


if __name__ == "__main__":
    sys.exit(main())
