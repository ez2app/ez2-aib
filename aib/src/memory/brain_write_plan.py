#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""F 寫回第二大腦第一刀(L6-01)——守門鏈 + write-plan 產生器。**零真寫**。

    python3 src/memory/brain_write_plan.py plan --seat <seat_id> --items <交易.json>
                                                [--contract F|git:...] [--state-root DIR]
                                                [--modules-dir DIR] [--ez2-ai REPO] [--rev main]
                                                [--receipt-key-file F] [--out F]
    python3 src/memory/brain_write_plan.py materialize [--ez2-ai REPO] [--rev main] [--out DIR]

## 這支在解決什麼

總綱 §二-F:把 C 萃煉核准的知識,依 D 綁的大腦,用 note-format 落成筆記。最危險的一格是
「綁定變成繞過審核直寫大腦的捷徑」——所以 `plan` 那條**只產計畫不寫檔**,`would_write`
在那條路徑上恆為 False。

★ **但本模組會真寫 vault**:L6-05 之後 `apply_plan()` 就在同一支檔裡(`os.replace` 原子
換入),`undo_apply()` 負責退回。這一段檔頭原本寫著「本模組沒有任何能寫 vault 的路徑」,
那句話**在 2026-08-18 19:00:27 被自己證偽**——那天真的寫了一個檔進真 vault
(`C10-共用暫存/3gk0jc-F 寫回第一刀測試筆記.md`,a seat 執行,the maintainer 授權)。
留著它的代價不是抽象的:`docs/DOD-outline-0818.md:252` 有一筆 F3「永不寫 vault ✅」
就是拿 `grep would_write` 對著這句話驗出來的——**判準被一句散文騙過一次而且落了檔**。

## 五道閘(方案 `docs/plans/PLAN-f-writeback-0818.md` §2;全部 fail-closed)

- **閘二 approved-only**:`review_status ∈ {approved, edited-approved}` 才進投影;其餘六態
  一律擋,且被擋的條目**只留 item_uid 與拒因碼**,標題/內文/hash 一律不進 plan
  (SOP v3.2 §14:pending/rejected 結構上不外流)。`user_persona_signal` 不是「被拒」而是
  **不屬於 F**(E5.6 §1.2 風格軌),單獨記在 `skipped_persona_track`,不靜默丟。
- **閘三 人核准收據**:approved 必附人類 HMAC 收據且可機器驗;reviewer≠maker;reviewer
  必須是人(`reviewer_kind == "human"`)。**缺收據的 approved 在投影層視同 pending**
  (SOP v3.2 §12 明文)。金鑰拿不到=驗不了=全部視同缺收據(不是放行);「沒配置」與
  「配置了但壞了」是兩個碼(`receipt_key_status()`,E5.3 接縫規格 9daffe7 §3.2)。
- **閘四 隱私不降級**:落檔的 privacy_level 取「條目自稱」與「路由層級」**較嚴者**;
  `secret` 一律擋。理由:gateway 的 privacy_level 只取自路由,一條 restricted 條目寫進
  內部夾會落成 internal,連 `ai_readable: false` 都不會生成(方案 §5-T2 實證)。
- **閘五 契約現查**:寫入當下重跑 `check_against_contract()`,不信 bindings.json 的快取值
  ——綁定當下合法不等於寫入當下合法(契約會升版)。
- **閘一 note-format**:`validateEz2NoteRecord()` 回空陣列、routeKey 過嚴格正則、分類身份
  不是造字 fallback、review_status 映射後在四態內。`buildEz2Note()` 不會自己跑 validate
  (方案 §5-T1),所以這裡一定要跑。

## 兩件刻意不做的事

- **不搬規則**:note-format 的值域/正則/必填全在 ez2-ai 正本,本模組只呼叫(經
  `note_format_runner.js`)。ez2-ai 在本機是 bare repo,所以先 `git show` materialize 到
  快取目錄,並把 rev 與逐檔 sha256 記進 plan 的 provenance。
- **不代人核准**:收據只驗不產(同 L5 F2 裁)。簽發者是核可端 `src/binding/decision_inbox`
  (E5.3 接縫,規格 9daffe7 甲案:核可端補齊、寫入端不放寬;`reviewer_kind` 只在那裡被寫下),
  金鑰與核可端共用一把(`receipt_key_status()`);真權威翻轉候 owner adapter(E5.5)。
"""
import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("memory", "identity", "persona_route"):
    sys.path.insert(0, str(_SRC / _p))

from route_register import check_against_contract, load_contract, contract_digest  # noqa: E402
from note_uid_aliases import assert_markdown_uid_in_aliases  # noqa: E402
from note_forward_links import (  # noqa: E402
    assert_markdown_has_forward_links, extract_wikilinks,
    targets_from_fields, write_new_body, write_update_body,
)

PLAN_RECORD = "brain-write-plan-v1"
RUNNER = Path(__file__).resolve().parent / "note_format_runner.js"
DEFAULT_EZ2_AI = "~/ez2-ai"
DEFAULT_REV = "main"

# materialize 清單:note-format 正本 + 它 require 的解析器 + 解析器預設吃的分類登記表。
# 路徑要與 ez2-ai 的相對結構一致(resolver 用 __dirname/../samples/… 找 registry)。
MODULE_FILES = (
    "shared/ez2-note-format.js",
    "shared/ez2-route-category-resolver.js",
    "samples/ez2-metadata/category-registry.valid.example.json",
)

# 十四類語意契約(SOP v3.2 §0.5)。F 不發明新值,看到表外值一律擋。
PERSONA_CATEGORY = "user_persona_signal"
KNOWLEDGE_CATEGORIES = frozenset({
    "fact", "outcome", "decision", "completed", "pending", "risk", "quote",
    "contradiction", "handoff", "expert_knowledge", "event",
})
BLOCKED_CATEGORIES = frozenset({"customer_event", "unknown"})
ALL_CATEGORIES = KNOWLEDGE_CATEGORIES | BLOCKED_CATEGORIES | {PERSONA_CATEGORY}

# 八態 → note-format 四態(方案 §4.1)。表裡沒有的一律不入夾。
REVIEW_STATUS_MAP = {"approved": "approved", "edited-approved": "approved"}
APPROVED_STATES = frozenset(REVIEW_STATUS_MAP)

# essence privacy_class → note-format privacy_level;secret 沒有對應值(擋)。
PRIVACY_CLASS_TO_LEVEL = {"internal": "internal", "restricted": "restricted"}
PRIVACY_ORDER = {"public": 0, "internal": 1, "restricted": 2, "sensitive": 3}
# 契約 dataLevel(中文)→ note-format 值域。★ 中文語感「機密 > 敏感」而英文值域
# sensitive > restricted,順序是反的(方案 §5-T2);敏感/機密兩級本來就被閘五擋掉,
# 這張表只是把 fail-closed 的理由寫清楚,不是放行路徑。
DATA_LEVEL_TO_LEVEL = {"一般": "internal", "內部": "internal",
                       "敏感": "sensitive", "機密": "restricted"}

SOURCE_KIND = "ai_chat"   # 萃煉產物;輕量筆記(案 B)才會用到 manual,不在第一刀


class WritePlanError(Exception):
    """來源不可用/守門鏈拒絕 —— fail-closed,呼叫端回 rc 2,一個位元組都不寫。"""


# ---- 閘〇:落檔路徑不得在 vault 之下(艦長 08-18 12:42 觀察、12:43 升級)------------
#
# 為什麼這條是「零真寫」的一部分而不是小待補:第一刀對外的保證是「本模組沒有能寫 vault
# 的路徑」,而 `--out` 收任意路徑時,plan JSON 可以落進 vault——實測 rc=0、檔案真的在
# vault 裡,而同一支 CLI 的 stderr 還印「未寫任何 vault 檔」。**訊息自證清白而判準不存在**,
# 比多寫一個檔更糟:沒有人會回頭查一個 rc=0 又自稱乾淨的執行。

DEFAULT_VAULT = "~/Desktop/Obsidian/Ez2"   # 與 cockpit-env.sh:185 EZ2_VAULT_DIR 預設同源
DEFAULT_STATE_ROOT = "~/.ez2-aib"
RECEIPTS_REL = ("memory", "apply-receipts")


def state_root(explicit=None):
    """狀態根:顯式 > `EZ2_AIB_HOME` > `~/.ez2-aib`。回 `(Path, 怎麼決定的)`。

    ★ 這支檔**只有這一把尺**。改這個之前先看清楚:`materialize` 的快取目錄與收據
    落點都走它,兩邊分岔的話「快取在 A、收據在 B」而兩邊都 rc=0。

    ★ 全 repo 目前有**兩把尺**:本檔與 `role_model`/`bind_one` 認 `EZ2_AIB_HOME`,
    而 `binding_projection`/`card_registry`/doctor 多認一個 `EZ2_AIB_STATE_ROOT`。
    本檔**刻意不跟進那個變數**——理由不是哪把對,是這支檔的快取目錄(:204)本來就
    只認 HOME,加進來會讓同一支檔的兩個落點被不同變數決定。收斂成一把尺是全 repo
    的事(`binding_projection.resolve_state_root` 的註解記著同一筆待裁),不是這裡。
    **代價寫在收據裡**:每張收據都記 `state_root_from`,不一致當場看得見。
    """
    if explicit:
        return Path(os.path.expanduser(str(explicit))), "explicit"
    value = os.environ.get("EZ2_AIB_HOME")
    if value:
        return Path(os.path.expanduser(value)), "env:EZ2_AIB_HOME"
    return Path(os.path.expanduser(DEFAULT_STATE_ROOT)), "default:%s" % DEFAULT_STATE_ROOT


def receipts_dir_for(explicit=None, root=None):
    """收據落點:顯式 > `<狀態根>/memory/apply-receipts`。回 `(Path, 怎麼決定的)`。

    ★ **不再預設跟著 plan 走。** 舊預設是 `<plan 所在>/apply-receipts`,而 plan 幾乎
    一定在治具或暫存目錄——08-18 那次真寫的收據就是這樣蒸發的,結果 vault 裡留下一個
    `undo` 撤不掉的檔(SPEC-writeback-vs-autocommit-0822 §二)。**收據的壽命不可以
    綁在計畫的壽命上**:計畫是用完即丟的中間產物,收據是那次不可逆動作唯一的退路。
    """
    if explicit:
        return Path(os.path.expanduser(str(explicit))), "explicit"
    base, how = state_root(root if isinstance(root, (str, os.PathLike)) else None)
    return base.joinpath(*RECEIPTS_REL), "state_root(%s)" % how


def vault_root():
    """vault 根;回 Path 或 None(這台機器上沒有 vault=沒有可污染的目標)。

    `EZ2_VAULT_DIR` 是 repo 正本名(`cockpit-env.sh:185`、`card_registry.resolve_vault`
    同源);`EZ2_VAULT_ROOT` 是艦長 12:43 令文裡的名字,repo 裡目前沒有這個變數——兩個
    都收,免得「令文指一個、程式讀另一個」變成一道指著空氣的閘。"""
    for var in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
        value = os.environ.get(var)
        if value:
            return Path(os.path.expanduser(value))
    default = Path(os.path.expanduser(DEFAULT_VAULT))
    return default if default.is_dir() else None


def refuse_vault_output(path):
    """落檔前的閘:realpath 解完不得等於 vault 根、也不得在它之下。違者 raise。

    ★ 一定要 realpath:比對字面前綴只擋得住老實人——vault 外放一條 symlink 指進 vault,
    字面看起來在外面,寫下去卻在裡面。兩邊都解,連 symlink 一起擋。"""
    root = vault_root()
    if root is None:
        return
    target = Path(os.path.expanduser(str(path))).resolve()
    try:
        root_real = root.resolve()
    except OSError:
        root_real = root
    if target == root_real or root_real in target.parents:
        raise WritePlanError(
            "--out 不得落在 vault 之下(vault=%s):%s —— 第一刀對外的保證是零真寫,"
            "落檔路徑也算" % (root_real, target))


# ---- materialize:從 bare repo 取 note-format 正本 --------------------------------

def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


PINS_FILE = Path(__file__).resolve().parent / "note-format-pins.json"
PINS_DEFAULT = "__committed__"    # sentinel:用 repo 內釘好的那份(存在才用)


def load_pins(spec=PINS_DEFAULT):
    """回 {相對路徑: sha256} 或 None(不釘)。

    ★ 為什麼要釘(a seat 08-18 12:47 對抗審 punch4):`rev=main` 是一個會動的標籤。
    provenance 記了「這次拿到的 sha」,那只夠**事後**看出漂移;上游 force-push 或改版時,
    F 這一側只會安靜地拿到新版照跑。釘 sha 是把它變成**事前**的拒絕。"""
    if spec is None:
        return None
    path = PINS_FILE if spec == PINS_DEFAULT else Path(spec)
    if not path.is_file():
        if spec == PINS_DEFAULT:
            return None          # repo 裡還沒釘 → 不假裝有釘(provenance 會誠實寫 false)
        raise WritePlanError("--pins 檔不存在:%s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as err:
        raise WritePlanError("--pins 讀不出來:%s" % err)
    pins = doc.get("sha256") if isinstance(doc, dict) else None
    if not isinstance(pins, dict) or not pins:
        raise WritePlanError("--pins 形狀不對:要 {\"sha256\": {相對路徑: sha}}")
    return pins


def check_pin(rel, got, pins):
    """釘不上就停,不是警告。缺一條 pin 也算不上——半套的釘等於沒釘。"""
    if pins is None:
        return
    want = pins.get(rel)
    if not want:
        raise WritePlanError("pins 裡沒有 %s 的指紋——半套的釘等於沒釘,先更新 %s"
                             % (rel, PINS_FILE.name))
    if want != got:
        raise WritePlanError(
            "note-format 正本漂移:%s\n  釘的是 %s\n  現在是 %s\n"
            "  上游改了(或被 force-push)。先看 diff 再決定重釘:"
            "git -C ~/ez2-ai diff <舊> <新> -- %s" % (rel, want, got, rel))


def materialize(ez2_ai=None, rev=DEFAULT_REV, dest=None, pins=PINS_DEFAULT):
    """把 note-format 正本從 ez2-ai(本機是 bare repo,無工作樹)取到 dest。
    回 (dest, {相對路徑: sha256}, rev)。任一檔取不到、或指紋對不上 = raise(不半套)。"""
    repo = os.path.expanduser(ez2_ai or os.environ.get("EZ2_AI_REPO") or DEFAULT_EZ2_AI)
    if not os.path.isdir(repo):
        raise WritePlanError("ez2-ai repo 不存在:%s(note-format 正本在那裡,沒有它 F 不能動)" % repo)
    dest = Path(dest or (state_root()[0] / "cache" / "note-format" / rev))
    # 閘〇同樣管這裡:materialize 也是「拿使用者給的路徑落檔」,三個模組檔一樣能落進 vault。
    # 只補 plan 的 --out 而漏掉這個,等於補了門沒補窗。
    refuse_vault_output(dest)
    pin_table = load_pins(pins)
    shas = {}
    for rel in MODULE_FILES:
        try:
            proc = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, rel)],
                                  capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as err:
            raise WritePlanError("git show 失敗(%s):%s" % (rel, err))
        if proc.returncode != 0:
            raise WritePlanError("ez2-ai %s 取不到 %s(rc=%d):%s"
                                 % (rev, rel, proc.returncode, proc.stderr.decode("utf-8", "replace")[:160]))
        got = _sha256_bytes(proc.stdout)
        # 指紋先對,對不上就不落檔——落了再說「漂移了」是事後,那正是 punch4 指的問題。
        check_pin(rel, got, pin_table)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp.%d" % os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(proc.stdout)
        os.replace(str(tmp), str(target))
        shas[rel] = got
    return dest, shas, rev


# ---- 閘三:人核准收據 --------------------------------------------------------------

def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def receipt_payload(item_uid, receipt):
    """簽章涵蓋範圍:誰核准、核准誰、**以什麼身分**核准、核准的是哪一版內容。

    ★ `reviewer_kind` 一定要在簽章裡(a seat 08-18 12:45 對抗審 punch2 實證):
    閘三靠 `reviewer_kind == "human"` 判「這是人審不是 AI 審」,但它原本不進 payload
    ——只要簽章方曾用同一把金鑰簽過任何 `reviewer_kind=ai` 的收據,把那張的 kind 欄
    改成 human、hmac 不重算,verify_receipt 仍回 (True, receipt_ok):**AI 審查被洗成
    人核准,而簽章完好如初**。授權判準的關鍵欄不進簽章,等於那個欄位沒有保護。"""
    return _canonical({
        "item_uid": item_uid,
        "receipt_id": receipt.get("receipt_id"),
        "reviewer": receipt.get("reviewer"),
        "reviewer_kind": receipt.get("reviewer_kind"),
        "maker": receipt.get("maker"),
        "approved_at_ms": receipt.get("approved_at_ms"),
        "content_sha256": receipt.get("content_sha256"),
    })


# ---- 金鑰:一把、兩碼(E5.3 接縫規格 9daffe7 §3.2;判準 tests/test-e53-approval-seam.py P2)----

KEY_OK = "receipt_key_ok"
KEY_UNCONFIGURED = "receipt_key_unconfigured"   # 從來沒人配置:要「裝」
KEY_UNAVAILABLE = "receipt_key_unavailable"     # 配置了但拿不到:要「查」


def _inbox_key_path():
    """核可端(decision_inbox)的金鑰路徑與其 env 名——**同一把鑰匙**,路徑由核可端定義,
    這裡只引用不重造(第二份常數就是第二份真相)。延後匯入:核可端在簽收據時也會
    匯入本模組,兩端都延後就不繞圈。"""
    b = str(_SRC / "binding")
    if b not in sys.path:
        sys.path.insert(0, b)
    import decision_inbox  # noqa: E402
    return decision_inbox.key_path(), decision_inbox.KEY_ENV


def _read_key_file(p):
    """回 (key|None, code):檔在且非空=ok;在而空/讀不到=unavailable。
    「不存在」要不要算沒配置由呼叫端依「誰指的路徑」決定。"""
    try:
        with open(os.path.expanduser(p), "rb") as fh:
            key = fh.read().strip()
    except OSError:
        return None, KEY_UNAVAILABLE
    return (key, KEY_OK) if key else (None, KEY_UNAVAILABLE)


def receipt_key_status(path=None):
    """回 (key|None, code),code ∈ receipt_key_ok | receipt_key_unconfigured | receipt_key_unavailable。

    ★ 「從來沒人配置」與「配置了但壞了」處置相反(前者要裝、後者要查),而舊碼兩者同一個
    碼——一個永遠拒絕的閘與一個沒裝好的閘在 rc 上長得一樣(規格 9daffe7 §3.2)。
    順序:明示 path / EZ2_F_RECEIPT_KEY_FILE → EZ2_F_RECEIPT_KEY → 核可端同一把
    (EZ2_APPROVAL_HMAC_KEY_PATH / ~/.ez2-persona-runtime/.approval-hmac-key)。
    ★ 寫入端**只讀不建**:首用自建是核可端的事;這裡自建等於寫入端自己發鑰匙給自己。
    """
    p = path or os.environ.get("EZ2_F_RECEIPT_KEY_FILE")
    if p:
        return _read_key_file(p)            # 明示指了檔:不存在/空/讀不到=配置了但壞了
    env = os.environ.get("EZ2_F_RECEIPT_KEY")
    if env:
        return env.encode("utf-8"), KEY_OK
    inbox_path, inbox_env = _inbox_key_path()
    if not os.path.exists(inbox_path):
        # 核可端路徑有被設定而檔不在=配置了但壞了;連設定都沒有、預設檔也不在=從未配置
        return None, (KEY_UNAVAILABLE if os.environ.get(inbox_env) else KEY_UNCONFIGURED)
    return _read_key_file(inbox_path)


def load_receipt_key(path=None):
    """HMAC 金鑰。拿不到就回 None——呼叫端一律當「驗不了」處理,不是「不用驗」。
    要分「沒配置」與「壞了」用 receipt_key_status();本函式只回鑰匙(相容舊呼叫端)。"""
    return receipt_key_status(path)[0]


# ---- NW6:撤核准是**自己的家族** ------------------------------------------
#
# 判準 `qa3/criteria-nw6-revoke-0828` = `ae84c121`(`X0`–`X8` + 修訂一);
# 登記顆(零行產品碼、時戳早於本檔)`dev3/nw6-revoke-registration-0828` = `6b94b0cb`。
#
# ★★★ 登記的命題逐字:**一張核准收據被撤銷之後,不存在任何輸入能讓 `verify_receipt`
#   對帶著那張收據的條目回 `ok`。** 那是**不可能**,不是行為。
#
# ★★ 為什麼撤銷**不得建在簽章上**(`X2`):若撤銷實際上只是「簽章還有效嗎」,
#   **持鑰者重簽一張就繞過去了** ⇒ 撤銷必須是**狀態查詢**(這張收據的身分在不在名單上),
#   不是**憑證屬性**。⇒ 本族釘 `receipt_id`,**不是** `item_uid`、**不是** `hmac`、**不是**時戳。
REVOCATION_REL = ("memory", "apply-receipts", "revoked-receipts.json")

REVOKED_CODE = "approval_receipt_revoked"            # 這張收據真的被撤了
REVLIST_UNREADABLE = "revocation_list_unreadable"     # 名單壞了 —— **探測失敗**
REVLIST_ABSENT = "revocation_list_absent"             # 名單被拿掉了 —— 也是探測失敗

# ★★ `X3(c)` + 修訂一:**探測失敗的值不得等於它表示安全的那個值。**
#   讀不到 ⇒ 一律拒,而**字樣必須與「已撤銷」不同** —— 兩者的下一步相反
#   (去修探測器 / 這張真的被撤了),而收據要回答的是**下一步**。
REVLIST_FAILURE_CODES = frozenset((REVLIST_UNREADABLE, REVLIST_ABSENT))


def revocation_list_path(root=None, explicit=None):
    if explicit:
        return Path(os.path.expanduser(str(explicit)))
    base, _ = state_root(root)
    return base.joinpath(*REVOCATION_REL)


def revoked_receipt_ids(root=None, explicit=None):
    """回 `(frozenset | None, code)`。

    ★ **拿不到一律回 `None`,不得回空集合** —— 空集合的意思是「我查過了,沒有人被撤」,
      而那正是探測失敗時**最容易被誤讀成安全**的那個值。
    """
    path = revocation_list_path(root, explicit)
    if not path.exists():
        # ★★ 「沒配置」與「被刪掉」是兩件事,而**只有後者是探測失敗**:
        #   收據目錄是寫入路徑自己建的(`receipts_dir_for`)。目錄在而名單不在
        #   = 有人把它拿掉了 ⇒ **拒**(刪一個檔比弄壞一個檔容易,那才是真正的攻擊面)。
        #   目錄不在 = 這個狀態根從來沒跑過寫入路徑 ⇒ 沒有可撤的東西,不是探測失敗。
        # ★ 沿用本檔既有做法(`receipt_key_status` 分「沒配置」與「壞了」),不自寫第二套語意。
        #   **分界點寫在這裡,是為了讓它可以被人反對。**
        if path.parent.is_dir():
            return None, REVLIST_ABSENT
        return frozenset(), "revocation_list_unconfigured"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, REVLIST_UNREADABLE
    if not isinstance(data, dict):
        return None, REVLIST_UNREADABLE
    ids = data.get("revoked_receipt_ids")
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
        return None, REVLIST_UNREADABLE
    return frozenset(ids), "revocation_list_ok"


def receipt_revocation_status(item, root=None, explicit=None):
    """回 `(可用嗎, code)`:`(True, ...)` 才代表這張收據**沒有**被撤。

    ★ `X3(a)`:這一步住在 `verify_receipt` **裡面**,不是包在外面的 wrapper ——
      閘的生效範圍不是「行號比它小」,是「同一串敘述裡它後面的」。
    """
    ids, code = revoked_receipt_ids(root, explicit)
    if ids is None:
        return False, code                      # 探測失敗 ⇒ 拒,且字樣自成一類
    receipt = item.get("approval_receipt") or {}
    if str(receipt.get("receipt_id") or "") in ids:
        return False, REVOKED_CODE
    return True, code


def verify_receipt(item, key, revocation_root=None):
    """回 (ok, code)。任一條不成立都不是「小瑕疵」——SOP v3.2 §12:缺收據的 approved
    在投影層視同 pending。"""
    if key is None:
        _, code = receipt_key_status()
        # 環境其實配好了而呼叫端沒帶鑰匙=這一次拿不到,仍是「壞」不是「沒配置」
        return False, (KEY_UNAVAILABLE if code == KEY_OK else code)
    receipt = item.get("approval_receipt")
    if not isinstance(receipt, dict):
        return False, "receipt_missing"
    # NW6:撤銷是**狀態查詢**,在簽章之前就問 —— 放在簽章之後會讓「重簽一張」
    # 在到達這裡之前先過了簽章那關,而那正是 X2 要擋的形狀。
    # ★ 釘的是 receipt_id,所以「同一張收據換掛另一個 item_uid」擋得住(X1 第 2 類)。
    usable, rev_code = receipt_revocation_status(item, root=revocation_root)
    if not usable:
        return False, rev_code
    for field in ("receipt_id", "reviewer", "maker", "approved_at_ms", "content_sha256", "hmac"):
        if not receipt.get(field):
            return False, "receipt_field_missing:%s" % field
    if receipt.get("reviewer_kind") != "human":
        # AI 不得代按核准(SOP v3.2 §12);沒具名說是人,就不是人。
        return False, "reviewer_not_human"
    if receipt["reviewer"] == receipt["maker"]:
        return False, "maker_equals_checker"
    expected = hmac.new(key, receipt_payload(item.get("item_uid"), receipt), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(receipt["hmac"])):
        return False, "receipt_hmac_mismatch"
    content_sha = item.get("content_sha256")
    if not content_sha:
        return False, "item_content_sha_missing"
    if receipt["content_sha256"] != content_sha:
        # 核准的是舊版內容,現在要寫的是另一版(SOP v3.2 §12:content hash 變=重審)。
        return False, "receipt_content_sha_mismatch"
    return True, "receipt_ok"


def _approval_chain(item):
    """NW3:從已驗過的核准收據導出**庚族**核准鏈。純函式,好突變也好測。

    ★ 只在 `verify_receipt` 已回 ok 的路徑上被呼叫 —— 這裡**不重驗**,
      重驗會變成第二個真相源(兩份實作一定分岔,而分岔的方向是「帳上說有」)。
    ★ 不放 `verified: true`:那是宣稱不是證據。放的是重驗需要的原料。
    """
    receipt = item.get("approval_receipt") or {}
    return {
        "approved_by": receipt.get("reviewer"),            # 誰核准
        "approved_at_ms": receipt.get("approved_at_ms"),   # 何時
        "approval_receipt_id": receipt.get("receipt_id"),  # 哪一張收據
        # 那張收據可機器驗:重驗要的原料(item_uid + 這三欄 → receipt_payload → hmac)
        "receipt_content_sha256": receipt.get("content_sha256"),
        "receipt_hmac": receipt.get("hmac"),
        "reviewer_kind": receipt.get("reviewer_kind"),
        "maker": receipt.get("maker"),
    }


# ---- 閘二 / 閘四:純函式,好突變也好測 ---------------------------------------------

def gate_approved_only(item):
    """回 (verdict, code):verdict ∈ accept|skip_persona|reject。"""
    category = item.get("category")
    if category not in ALL_CATEGORIES:
        return "reject", "category_not_in_contract"
    if category == PERSONA_CATEGORY:
        # 不是被拒,是不屬於 F(E5.6 §1.2 風格軌 → B lane 的候選包/watchlist)。
        return "skip_persona", "persona_track"
    if category in BLOCKED_CATEGORIES:
        # customer_event 只能產候審卡永不直寫(E5.6 §1.2);unknown 一律候審(§1.3-3)。
        return "reject", "category_blocked:%s" % category
    status = item.get("review_status")
    if status not in APPROVED_STATES:
        return "reject", "not_approved:%s" % (status or "missing")
    if status == "edited-approved" and not item.get("edited_content_sha256"):
        # 編修後核准必回爐(SOP v3.2 §12):沒有編修版指紋就證明不了守門跑過哪一版。
        return "reject", "edited_approved_without_edit_sha"
    return "accept", "approved"


def gate_privacy(item, route):
    """回 (ok, code, privacy_level)。取兩者較嚴;secret 一律擋。"""
    klass = item.get("privacy_class")
    if klass == "secret":
        return False, "privacy_secret_never_written", None
    item_level = PRIVACY_CLASS_TO_LEVEL.get(klass)
    if item_level is None:
        return False, "privacy_class_unknown:%s" % (klass or "missing"), None
    route_level = DATA_LEVEL_TO_LEVEL.get(route.get("dataLevel"))
    if route_level is None:
        return False, "route_data_level_unknown:%s" % (route.get("dataLevel") or "missing"), None
    stricter = item_level if PRIVACY_ORDER[item_level] >= PRIVACY_ORDER[route_level] else route_level
    return True, "privacy_ok", stricter


# ---- 閘一:note-format(經 runner 呼叫 ez2-ai 正本)---------------------------------

STRICT_ROUTE_KEY = __import__("re").compile(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+")


def call_runner(modules_dir, payload):
    """跑 node runner。**不接 pipe 吃 rc**:rc 與 stdout 一起回,由呼叫端判。"""
    proc = subprocess.run([os.environ.get("EZ2_NODE", "node"), str(RUNNER), str(modules_dir)],
                          input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                          text=True, timeout=60)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise WritePlanError("note-format runner rc=%d:%s" % (proc.returncode, proc.stderr[-200:]))
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise WritePlanError("note-format runner 輸出不是 JSON(rc=%d):%s"
                             % (proc.returncode, (proc.stdout or proc.stderr)[-200:]))


def gate_note_format(runner_out, route_key):
    """回 (ok, code)。四條都要過:routeKey 嚴格正則 / 分類非造字 / build 沒炸 / validate 空。

    ★ 拒因**一律是本模組自己的固定碼**,不把 runner 回來的錯誤字串接進去
    (a seat 08-18 12:47 對抗審 punch3):那些字串來自另一支程式,只要它哪天在訊息裡
    回顯欄位值(title/text),內容就從拒因這條**間接管道**漏進 plan——而 plan 是要外送的
    產物,契約上是 content-free。直接測「內文有沒有出現在 plan」測不到這條,因為洩漏
    路徑是「別人的錯誤訊息」。要診斷細節就對那一條單獨重跑 runner,不要讓它搭 plan 的便車。"""
    if not STRICT_ROUTE_KEY.fullmatch(route_key or ""):
        # normalizeRouteKey 收底線但 validateEz2NoteRecord 不收,而 buildEz2Note 不跑 validate
        # ——寫得出去、驗不過(方案 §5-T1,契約 40 條裡 6 條踩到)。
        return False, "route_key_fails_validator_regex"
    if not runner_out.get("ok"):
        return False, "note_build_failed"
    if runner_out.get("category", {}).get("fallback_used"):
        # category registry 查無 → fallbackCategoryUid() 依 routeKey 造字;那個身份沒人登記過,
        # 日後補登記就分裂(方案 §5-T5:26 條可寫路由只有 2 條登記過)。
        return False, "category_uid_is_fabricated"
    errors = runner_out.get("validate_errors")
    if errors:
        # 只帶「幾條」不帶「是哪幾條的原文」:數量是本模組自己數的,不是別人給的字串。
        return False, "note_validate_failed:%d" % len(errors)
    return True, "note_format_ok"


# ---- 席位 → 大腦地址 ---------------------------------------------------------------

def resolve_binding(state_root, seat_id):
    """從 D lane 的投影讀這席綁了什麼。回 dict;讀不到 raise。
    **只讀不信**:route_key 拿到後仍要過閘五(契約現查)。"""
    path = Path(state_root) / "identity" / "bindings.json"
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as err:
        raise WritePlanError("綁定投影讀不到 %s(%s)——先跑 binding_projection.py project" % (path, err))
    entry = (doc.get("bindings") or {}).get(seat_id)
    if not isinstance(entry, dict):
        raise WritePlanError("投影裡沒有 seat_id=%s 的綁定(這席還沒綁人格/大腦)" % seat_id)
    route_key = entry.get("route_key")
    if not route_key:
        raise WritePlanError("seat %s 綁了人格但沒有大腦(route_key 空)——先 [綁定大腦]" % seat_id)
    return entry


def route_of(contract, route_key):
    for route in (contract or {}).get("routes", []):
        if route.get("routeKey") == route_key:
            return route
    return None


# ---- plan --------------------------------------------------------------------------

PROBE_SYNTHETIC_TITLE = "route-writability-probe"
PROBE_SYNTHETIC_BODY = "probe body — routeKey→category resolution only; never written"


def _resolve_modules_dir(modules_dir, ez2_ai, rev, pins):
    """與 build_plan 同一段:給了 --modules-dir 就對指紋(讀的那一刻核 pins,不因走快取跳過);
    沒給就 materialize(git show)。回 (Path, {rel: sha}, rev)。★ 抽成函式=「規則只寫一份」,
    build_plan 與探針共用;probe 只取第一個(不需要 shas/rev 進 provenance)。"""
    pin_table = load_pins(pins)
    if modules_dir is None:
        return materialize(ez2_ai=ez2_ai, rev=rev, pins=pins)
    md = Path(modules_dir)
    shas = {}
    for rel in MODULE_FILES:
        f = md / rel
        if not f.is_file():
            raise WritePlanError("--modules-dir 缺 %s(先跑 materialize)" % rel)
        with open(f, "rb") as fh:
            shas[rel] = _sha256_bytes(fh.read())
        check_pin(rel, shas[rel], pin_table)
    return md, shas, rev


def probe_route_writability(route_key, *, contract=None, contract_spec=None,
                            modules_dir=None, ez2_ai=None, rev=DEFAULT_REV,
                            pins=PINS_DEFAULT, category_registry_path=None, now_ms=None):
    """綁定時就問:這條路由 **F 寫得進去嗎**?(L6-06)

    **「契約 aiWritable=true」≠「F 寫得進去」**——契約 40 條裡,綁定當下沒有任何一格會
    告訴你分類沒登記(category_uid 造字)或 routeKey 踩到 note-format 的嚴格正則(底線)。
    本探針把那個發現點從「寫入時」提前到「綁定時」。

    回 {"ok": bool, "codes": [...], "checked": {...}}。**唯讀**:不碰 vault、不落任何檔、
    不寫帳本、不需要收據金鑰(它問路由能不能落檔,不問某筆條目准不准寫)。

    ★ 規則只寫一份(工單 §4.1):route-level 兩關都走 **build_plan 走的同一個函式**——
      閘五 `check_against_contract()`、閘一 `gate_note_format()`(它內含 STRICT_ROUTE_KEY 與
      category fallback 判定)。**探針不重抄任何正則或登記表查法**;因此它回的 code 與
      build_plan 同路徑的 rejected code **逐字相同**(工單 §4.2,測試釘死)。

    ★ 只保留 route-level 的拒因:build/validate 是**內容層**的失敗(取決於某筆條目的正文),
      不是「這條路由能不能寫」。探針餵一則合法的合成 note,所以正常情形下這兩類不會觸發;
      萬一觸發(runner 壞了)也不併進 route 判定,改記進 checked.runner 供診斷。
    """
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    codes, checked = [], {"route_key": route_key}

    if contract is None:
        contract = load_contract(contract_spec)
    if contract is None:
        return {"ok": False, "codes": ["contract_unavailable"],
                "checked": {"route_key": route_key, "contract": "unreadable"}}

    # 閘五:契約可寫性(aiWritable + 非敏感/機密)。與 build_plan 逐字同源。
    # ★ 短路與 build_plan 對齊(工單 §4.2):build_plan 在此關 raise 就停,不會再跑 note-format;
    #   所以契約不過時探針**只回這一碼**、不追加 note-format 的碼(否則 code 與 build_plan 分岔),
    #   順帶省掉 runner 的 git show 成本(§4.5)。
    ok_c, code_c, _detail = check_against_contract(route_key, contract)
    checked["contract"] = code_c
    if not ok_c:
        return {"ok": False, "codes": [code_c], "checked": checked}
    route = route_of(contract, route_key)

    # 閘一(route-level 部分):STRICT_ROUTE_KEY + category 是否造字。跑同一支 runner,
    # 只用來解析 routeKey→category(合成 note,永不落檔)。
    try:
        modules, _shas, _rev = _resolve_modules_dir(modules_dir, ez2_ai, rev, pins)
        runner_out = call_runner(modules, {
            "route": {"routeKey": route_key,
                      "folder": (route or {}).get("folder"),
                      "dataLevel": (route or {}).get("dataLevel"),
                      "aiReadable": (route or {}).get("aiReadable"),
                      "aiWritable": (route or {}).get("aiWritable")},
            "note": {"title": PROBE_SYNTHETIC_TITLE, "body": PROBE_SYNTHETIC_BODY,
                     "privacy_level": "internal", "source_kind": SOURCE_KIND,
                     "review_status": "approved", "created_at_ms": now_ms,
                     "extra_frontmatter": {"created_by_ai": True}},
            "used_uids": [],
            "category_registry_path": category_registry_path,
        })
    except WritePlanError as err:
        # runner 拿不到/壞掉:route-level 判不了 = fail-closed(不給看起來像綠的答案)。
        checked["runner"] = "unavailable:%s" % str(err)[:120]
        codes.append("note_format_runner_unavailable")
        return {"ok": False, "codes": codes, "checked": checked}

    ok_n, code_n = gate_note_format(runner_out, route_key)
    checked["note_format"] = code_n
    # route-level 拒因才併進 codes;內容層(build/validate)不是路由的錯,只記進 checked。
    ROUTE_LEVEL_NOTE_CODES = {"route_key_fails_validator_regex", "category_uid_is_fabricated"}
    if not ok_n and code_n in ROUTE_LEVEL_NOTE_CODES:
        codes.append(code_n)
    checked["category_uid"] = (runner_out.get("category") or {}).get("category_uid")
    checked["fallback_used"] = bool((runner_out.get("category") or {}).get("fallback_used"))

    return {"ok": not codes, "codes": codes, "checked": checked}


def build_plan(state_root, seat_id, items, contract_spec=None, modules_dir=None,
               receipt_key=None, now_ms=None, ez2_ai=None, rev=DEFAULT_REV,
               category_registry_path=None, pins=PINS_DEFAULT, notes_out=None):
    """產 write-plan。**永遠不寫 vault**:回傳的 dict 裡 would_write 恆為 False。"""
    if not isinstance(items, list):
        raise WritePlanError("items 要是 list(一筆一條 essence item)")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)

    binding = resolve_binding(state_root, seat_id)
    route_key = binding["route_key"]

    # 閘五:契約現查(不吃投影裡的快取判定)。
    contract = load_contract(contract_spec)
    if contract is None:
        raise WritePlanError("路由契約讀不到(--contract 或 EZ2_ROUTE_REGISTRY);拒絕在看不到規則的情況下產計畫")
    ok, code, detail = check_against_contract(route_key, contract)
    if not ok:
        raise WritePlanError("大腦 %s 過不了契約現查(%s):%s" % (route_key, code, detail))
    route = route_of(contract, route_key)

    # ★ 指紋核查(讀的那一刻,不是取的那一刻)與探針共用同一段(L6-06,規則只寫一份)。
    modules_dir, module_shas, rev = _resolve_modules_dir(modules_dir, ez2_ai, rev, pins)

    accepted, rejected, skipped, used_uids = [], [], [], []
    for item in items:
        item_uid = item.get("item_uid") or "?"

        verdict, code = gate_approved_only(item)
        if verdict == "skip_persona":
            # 記帳,不靜默丟(E5.6 §1.3-1:風格軌正本不在 F 這側)。
            skipped.append({"item_uid": item_uid, "gate": "approved_only", "code": code})
            continue
        if verdict == "reject":
            rejected.append({"item_uid": item_uid, "gate": "approved_only", "code": code})
            continue

        # NW6:撤銷名單要問**這次操作的那個狀態根**,不是環境裡的那個 ——
        # 少了這一格,治具在暫存根上跑,而撤銷查的是跑的人的家目錄。
        ok, code = verify_receipt(item, receipt_key, revocation_root=state_root)
        if not ok:
            # 缺收據的 approved 視同 pending —— 拒因只留碼,不留內容。
            rejected.append({"item_uid": item_uid, "gate": "human_receipt", "code": code})
            continue

        ok, code, privacy_level = gate_privacy(item, route)
        if not ok:
            rejected.append({"item_uid": item_uid, "gate": "privacy_no_downgrade", "code": code})
            continue

        note_status = REVIEW_STATUS_MAP[item["review_status"]]
        # ★ 自訂欄位一律 `custom.` 前綴(schema 正本 ez2-ai 38d26087 §3.4:
        #   pattern `^custom\.[a-z][a-z0-9_]*(\.…)+$`,且不可覆蓋核心欄位)。
        #   08-18 18:03 寫進真 vault 的第一個檔踩了四條:ez2_persona_uid / ez2_seat_id /
        #   ez2_essence_item_uid 沒有前綴,ez2_essence_review_status 還跟正本「導入治理」
        #   群既有的 `review_status` 重複。**正解是改名,不是把違規欄位登記合法化。**
        extra = {"created_by_ai": True,
                 "custom.ez2.essence_item_uid": item_uid,
                 "custom.ez2.seat_id": seat_id,
                 "custom.ez2.persona_uid": binding.get("persona_uid") or ""}
        if item["review_status"] == "edited-approved":
            # 八→四態是有損映射;`review_status` 是核心欄位(只有四態),所以「被人改過」
            # 這件事改掛在自訂命名空間的指紋欄上——不重複核心欄位,也不丟掉那個訊號。
            extra["custom.ez2.essence_edited_sha256"] = item["edited_content_sha256"]
        _assert_custom_prefixed(extra)

        body = item.get("text") or ""
        targets = targets_from_fields(item)
        existing_body = item.get("existing_body")
        if existing_body:
            body = write_update_body(existing_body, body, targets)
        elif targets:
            body = write_new_body(body, targets)
        runner_out = call_runner(modules_dir, {
            "route": {"routeKey": route_key, "folder": route.get("folder"),
                      "dataLevel": route.get("dataLevel"),
                      "aiReadable": route.get("aiReadable"), "aiWritable": route.get("aiWritable")},
            "note": {"title": item.get("title") or "", "body": body,
                     "privacy_level": privacy_level, "source_kind": SOURCE_KIND,
                     "review_status": note_status, "created_at_ms": now_ms,
                     "extra_frontmatter": extra},
            "used_uids": used_uids,
            "category_registry_path": category_registry_path,
            # 要 staging 才要正文;不要 staging 時 plan 維持 content-free。
            "want_content": notes_out is not None,
        })
        ok, code = gate_note_format(runner_out, route_key)
        if not ok:
            rejected.append({"item_uid": item_uid, "gate": "note_format", "code": code})
            continue

        note = runner_out["note"]
        need = list(targets)
        if existing_body:
            need = []
            seen = set()
            for uid in extract_wikilinks(existing_body) + list(targets):
                if uid not in seen:
                    need.append(uid)
                    seen.add(uid)
        markdown = runner_out.get("markdown")
        if need and markdown is not None:
            try:
                assert_markdown_has_forward_links(markdown, need)
            except ValueError:
                rejected.append({"item_uid": item_uid, "gate": "forward_links",
                                 "code": "forward_link_missing"})
                continue
        used_uids.append(note["uid"])
        staged = None
        if notes_out is not None:
            # staging 也是「拿路徑落檔」,一樣過閘〇:計畫與正文都不准落在 vault 裡。
            staged = _stage_note(notes_out, note, runner_out.get("markdown"))
        accepted.append({
            "item_uid": item_uid,
            "note_uid": note["uid"],
            "file_name": note["file_name"],
            "would_write_rel": str(Path(route.get("folder") or "") / note["file_name"]),
            "markdown_sha256": note["markdown_sha256"],
            "markdown_bytes": note["markdown_bytes"],
            "privacy_level": note["privacy_level"],
            "review_status": note["review_status"],
            "category_uid": runner_out["category"]["category_uid"],
            # 落檔實際帶了哪些 frontmatter key——給稽核用(正本 §3.4 只認 core 或 custom.)
            "frontmatter_keys": runner_out["note"].get("frontmatter_keys"),
            "staged_path": str(staged) if staged else None,
            # ---- NW3 庚族:核准鏈,**自己的欄位** ------------------------------
            # ★ 判準 `PV2`:不得塞進甲族 `provenance`(工具鏈血統:用什麼做的)或
            #   乙族 `.provenance.json`(產物內容指紋:用什麼材料做的)。
            #   塞進去它會**繼承那一族的語意**,而讀的人會因為「那個欄位一直都在」
            #   而以為這件事早就做完了 —— `PV0` 量到的是:六族沒有一族在回答
            #   「**誰批准它出來**」,那一族在本庫是 0。
            # ★ 四欄逐欄各自可查(判準 `PV1`,四欄不得合成一格):
            #   誰核准 / 何時 / 哪一張收據 / 那張收據可機器驗。
            #   最後一項不放一個 `verified: true` 的旗標 —— 旗標是宣稱不是證據;
            #   放的是**重驗需要的原料**,任何人可以自己再跑一次 `verify_receipt`。
            "approval_chain": _approval_chain(item),
        })

    return {
        "record": PLAN_RECORD,
        "generated_wall_ms": now_ms,
        # ★ 第一刀的天花板:本模組沒有真寫路徑,這兩欄是常數不是開關(L6-05 才解鎖)。
        "dry_run_only": True,
        "would_write": False,
        "ceiling": "第一刀零真寫:真寫路徑候 L6-05(the maintainer 具名授權 + owner adapter)",
        "seat_id": seat_id,
        "persona_uid": binding.get("persona_uid"),
        "route_key": route_key,
        "route_folder": route.get("folder"),
        "route_data_level": route.get("dataLevel"),
        "provenance": {
            "ez2_ai_rev": rev,
            # 誠實欄:沒釘就寫沒釘。rev=main 是會動的標籤,provenance 記到的 sha 只夠
            # 事後對帳;讀 plan 的人要一眼看得出這份是「被釘過的」還是「當時剛好長這樣」。
            "pins_enforced": load_pins(pins) is not None,
            "note_format_modules": module_shas,
            "route_contract": {"spec": contract_spec, "sha256": contract_digest(contract_spec),
                               "route_count": len(contract.get("routes", []))},
            "planner": {"path": str(Path(__file__).resolve()),
                        "sha256": _sha256_file(__file__)},
        },
        "accepted": accepted,
        "rejected": rejected,
        "skipped_persona_track": skipped,
        "counts": {"in": len(items), "accepted": len(accepted),
                   "rejected": len(rejected), "skipped_persona": len(skipped)},
    }


# ---- L6-05 真寫:plan → apply → undo(the maintainer 08-18 15:5x 授權;艦長 15:22 定範圍)------
#
# 這一段是整套系統**唯一會動 vault 真檔**的路徑,所以每一條規矩都寫成程式而不是註解:
#
# 1. **apply 只吃 plan 的產物,不重算**:正文在 plan 階段就落成 staging 檔,apply 只核
#    sha 然後搬。重算等於在 plan 與 apply 之間開一道 TOCTOU——中間任何輸入變了
#    (契約升版、卡片改版、條目被改),算出來的會是另一篇,而收據卻指著舊的判準。
# 2. **先備份再寫**:目標已存在一律拒(與 gateway 同規矩:v1 沒有覆寫語意);拒之前
#    先把現況指紋記進收據,讓「為什麼拒」也留得下來。
# 3. **可回滾**:每次 apply 產 undo 資訊(寫了哪些檔+落檔當下的 sha);`undo` 只刪
#    sha 仍相符的檔——有人動過就拒,寧可留下也不要刪掉別人的東西。
# 4. **具名**:--actor 必填且入帳,append-only。
# 5. **目標必須在契約登記的 folder 之下**:plan 記的是 routeKey→folder,apply 再解一次
#    realpath 並確認落點真的在那個 folder 裡(symlink 逃逸在這裡擋死)。
# 具名要求:沿用 bind_one 的署名形狀(role@四到八碼十六進位/引擎),不接受自由文字。
ACTOR_SIG_RE = __import__("re").compile(r"[a-z0-9-]{1,32}@[0-9a-f]{4,8}/[A-Za-z0-9._-]{1,32}")
APPLY_RECORD = "brain-write-apply-v1"


def _stage_note(notes_out, note, markdown):
    """把正文落成 staging 檔並核 sha;回路徑。staging 一樣不准落在 vault 裡(閘〇)。"""
    if markdown is None:
        raise WritePlanError("runner 沒給正文——staging 需要 want_content=true")
    try:
        assert_markdown_uid_in_aliases(markdown, note.get("uid"))
    except ValueError as err:
        raise WritePlanError("D1 uid 未進 aliases:%s" % err)
    out = Path(notes_out)
    refuse_vault_output(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / ("%s.md" % note["uid"])
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(markdown)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))
    got = _sha256_file(path)
    if got != note["markdown_sha256"]:
        raise WritePlanError("staging 落檔後 sha 不符(%s != %s)" % (got, note["markdown_sha256"]))
    return path


def _under(path, root):
    """realpath 後 path 是否在 root 之下(含等於)。symlink 逃逸在這裡擋死。"""
    p = Path(path).resolve()
    r = Path(root).resolve()
    return p == r or r in p.parents


def apply_plan(plan_path, actor, vault=None, now_ms=None, receipts_dir=None,
               dry_run=False, state_root_arg=None):
    """把 plan 的 accepted 逐條寫進 vault。回 apply 收據 dict。

    **這是不可逆動作的入口**:任何一條檢查不過就整批不寫(不半套),寫到一半失敗
    會用已收集的 undo 資訊自動回滾。"""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    plan_path = Path(plan_path)
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as err:
        raise WritePlanError("計畫讀不到:%s" % err)
    if plan.get("record") != PLAN_RECORD:
        raise WritePlanError("這不是 %s(拿到 %r)" % (PLAN_RECORD, plan.get("record")))
    if not ACTOR_SIG_RE.fullmatch(actor or ""):
        raise WritePlanError("--actor 必填且需 role@hex/Engine 形狀:%r" % (actor,))
    accepted = plan.get("accepted") or []
    if not accepted:
        raise WritePlanError("計畫沒有 accepted 條目,沒有東西可寫")

    root = Path(vault) if vault else vault_root()
    if root is None:
        raise WritePlanError("找不到 vault 根(EZ2_VAULT_DIR/EZ2_VAULT_ROOT)——不猜")
    folder = plan.get("route_folder") or ""
    try:
        import route_register as _RR
        _idx = _RR.vault_index_folder(plan.get("route_key") or "", str(root))
        if _idx:
            folder = _idx                   # uid 版面:真路徑在 vault 索引,不在契約字面
        _uid_layout = _RR.vault_is_uid_layout(str(root))
    except Exception:
        _uid_layout = False
    folder_abs = (root / folder)
    if not folder_abs.is_dir():
        raise WritePlanError("契約登記的 folder 不存在:%s(不代建;建夾是 E lane 的事)" % folder_abs)

    # ---- 前置全查完才動手(不半套)----
    jobs = []
    for row in accepted:
        staged = row.get("staged_path")
        if not staged:
            raise WritePlanError("%s 沒有 staged_path——plan 要用 --notes-out 產正文"
                                 % row.get("item_uid"))
        staged = Path(staged)
        if not staged.is_file():
            raise WritePlanError("staging 檔不見了:%s" % staged)
        got = _sha256_file(staged)
        if got != row.get("markdown_sha256"):
            raise WritePlanError("staging 內容與計畫不符(%s):%s != %s"
                                 % (row.get("item_uid"), got, row.get("markdown_sha256")))
        target = folder_abs / ((row["note_uid"] + ".md") if _uid_layout else row["file_name"])
        if not _under(target.parent, folder_abs):
            raise WritePlanError("落點跑出契約 folder:%s" % target)
        if not _under(target.parent, root):
            raise WritePlanError("落點跑出 vault:%s" % target)
        if target.exists():
            # 與 gateway 同規矩:v1 沒有覆寫語意。世代更新要新檔+反向連結。
            raise WritePlanError("目標已存在,拒絕覆寫:%s(現況 sha=%s)"
                                 % (target, _sha256_file(target)))
        jobs.append({"item_uid": row["item_uid"], "note_uid": row["note_uid"],
                     "staged": staged, "target": target, "sha256": got,
                     "backup": None, "was_new": True,
                     # NW3/PV3 反向可查:由**產物**問得出它是由哪一次核准授權的,
                     # 不只由核准問得出產物。所以核准鏈要跟著落進 apply 收據。
                     "approval_chain": row.get("approval_chain")})

    receipt = {"record": APPLY_RECORD, "at_ms": now_ms, "actor": actor,
               "plan_path": str(plan_path.resolve()),
               "plan_sha256": _sha256_file(plan_path),
               "vault_root": str(root), "route_key": plan.get("route_key"),
               "route_folder": folder, "dry_run": bool(dry_run),
               "status": "planned",
               "planned": [{"target": str(j["target"]), "sha256": j["sha256"],
                            "item_uid": j["item_uid"], "note_uid": j["note_uid"],
                            "approval_chain": j.get("approval_chain")}
                           for j in jobs],
               "written": [], "undo_hint": "brain_write_plan.py undo --receipt <本檔>"}
    if dry_run:
        receipt["would_write"] = [str(j["target"]) for j in jobs]
        return receipt

    # ---- 意圖先落地:收據先寫,再碰 vault ------------------------------------
    # ★ 順序就是這條修法的全部。舊版是「先寫 vault 再寫收據」,所以收據那步炸掉時
    #   檔案已經在 vault 裡了,而且拋的是沒人接的 OSError——不可逆動作做完了,退路
    #   沒建起來,rc 還是個看起來像普通失敗的 1。
    #   現在:收據建不起來就**一個字都不寫**;真的崩在中間時,磁碟上已經有一張
    #   status=in-flight 的收據,undo 照著 planned 就能收拾。
    rpath_dir, rdir_how = receipts_dir_for(receipts_dir, state_root_arg)
    refuse_vault_output(rpath_dir)               # 收據自己也不准落進 vault(閘〇)
    rpath = rpath_dir / ("apply-%d.json" % now_ms)
    receipt["receipts_dir_from"] = rdir_how
    receipt["state_root_from"] = state_root(state_root_arg)[1]
    receipt["receipt_path"] = str(rpath)
    receipt["status"] = "in-flight"
    try:
        rpath_dir.mkdir(parents=True, exist_ok=True)
        _write_receipt(rpath, receipt)
    except OSError as err:
        raise WritePlanError(
            "收據寫不進去(%s):%s —— 沒有收據的 apply 等於不可逆,所以 vault "
            "一個字都不寫" % (rpath, err))

    written = []
    try:
        for job in jobs:
            tmp = job["target"].with_name(job["target"].name + ".tmp.%d" % os.getpid())
            with open(job["staged"], "rb") as src, open(tmp, "wb") as dst:
                data = src.read()
                dst.write(data)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(str(tmp), str(job["target"]))
            written.append({"target": str(job["target"]), "sha256": job["sha256"],
                            "item_uid": job["item_uid"], "note_uid": job["note_uid"],
                            "was_new": True, "backup": None,
                            "approval_chain": job.get("approval_chain")})
    except OSError as err:
        # 寫到一半失敗:用已收集的 undo 資訊自己退回去,不留半套。
        _rollback(written)
        _try_mark(rpath, receipt, "rolled-back", str(err), written)
        raise WritePlanError("寫入失敗已回滾:%s" % err)

    receipt["status"] = "done"
    receipt["written"] = written
    try:
        _write_receipt(rpath, receipt)
    except OSError as err:
        # 定稿寫不進去 = 這批寫入沒有退路。已經寫進去的退回來,再把失敗說出口。
        _rollback(written)
        raise WritePlanError(
            "收據定稿寫不進去(%s):%s —— 已把剛寫進 vault 的 %d 個檔退回"
            % (rpath, err, len(written)))

    # ---- 回查:收據要在它自己說的那個位置,而且內容對得上 --------------------
    # ★ 為什麼回查的是 `receipt["receipt_path"]` 而不是本地變數 rpath:回報給呼叫端的
    #   是前者。驗本地變數等於驗自己記得什麼,驗回報值才驗得到「你叫別人去拿的那個
    #   路徑上真的有東西」。08-18 那次的失效形狀正是「收據在報告裡、不在磁碟上」。
    ok, why = _verify_receipt_on_disk(receipt["receipt_path"], receipt)
    if not ok:
        _rollback(written)
        raise WritePlanError(
            "收據回查失敗(%s):%s —— 沒有可回查的收據就沒有退路,已把剛寫進 "
            "vault 的 %d 個檔退回" % (receipt["receipt_path"], why, len(written)))
    return receipt


def _write_receipt(path, receipt):
    """收據落檔:先寫 tmp 再 os.replace(半截的收據比沒有收據更難處置)。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def _rollback(written):
    """把這一批已寫入的檔退回去。只刪 sha 仍相符的——有人動過就留著(誤刪不可逆)。"""
    for row in reversed(written):
        try:
            if _sha256_file(row["target"]) == row["sha256"]:
                os.unlink(row["target"])
        except OSError:
            pass


def _try_mark(path, receipt, status, error, written):
    """把收據標成某個狀態;**這一步失敗不得蓋掉原本的失敗原因**,所以吞掉 OSError。"""
    try:
        marked = dict(receipt)
        marked["status"] = status
        marked["error"] = error
        marked["written"] = written
        _write_receipt(path, marked)
    except OSError:
        pass


def _verify_receipt_on_disk(path, expected):
    """回 (ok?, 原因)。讀不到/不是收據/寫入清單對不上,三者都算失敗。"""
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError) as err:
        return False, "讀不回來:%s" % err
    if got.get("record") != APPLY_RECORD:
        return False, "磁碟上那個檔不是 %s" % APPLY_RECORD
    if got.get("at_ms") != expected.get("at_ms"):
        return False, "at_ms 對不上(%r != %r)" % (got.get("at_ms"), expected.get("at_ms"))
    if got.get("status") != "done":
        return False, "status=%r(要 done)" % (got.get("status"),)
    a = sorted(r.get("target") for r in got.get("written") or [])
    b = sorted(r.get("target") for r in expected.get("written") or [])
    if a != b:
        return False, "寫入清單對不上(磁碟 %d 筆 vs 本次 %d 筆)" % (len(a), len(b))
    return True, "收據在 %s,內容相符" % path


def undo_apply(receipt_path, actor, now_ms=None):
    """依收據回滾。只刪 sha 仍與收據相符的檔;有人動過就拒(留著比誤刪安全)。"""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    try:
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
    except (OSError, ValueError) as err:
        raise WritePlanError("收據讀不到:%s" % err)
    if receipt.get("record") != APPLY_RECORD:
        raise WritePlanError("這不是 %s" % APPLY_RECORD)
    if not ACTOR_SIG_RE.fullmatch(actor or ""):
        raise WritePlanError("--actor 必填且需 role@hex/Engine 形狀:%r" % (actor,))
    # ★ 崩在半路的那張收據(status=in-flight)才是最需要 undo 的那一張:vault 裡
    #   可能已經有檔,而 `written` 還是空的。這裡改讀 `planned`,判準與 done 完全
    #   一樣(sha 相符才刪),所以不會因為狀態不同而放寬。
    #   ——「收據存在但 undo 讀不到它」等於沒有收據,那正是這條修法要消掉的東西。
    status = receipt.get("status")
    rows = receipt.get("written") or []
    if not rows and status in ("in-flight", "rolled-back"):
        rows = receipt.get("planned") or []
    removed, kept = [], []
    for row in rows:
        target = Path(row["target"])
        if not target.exists():
            kept.append({"target": row["target"], "why": "already_gone"})
            continue
        got = _sha256_file(target)
        if got != row["sha256"]:
            kept.append({"target": row["target"], "why": "changed_since_apply", "sha256": got})
            continue
        os.unlink(target)
        removed.append(row["target"])
    return {"record": "brain-write-undo-v1", "at_ms": now_ms, "actor": actor,
            "receipt": str(receipt_path), "receipt_status": status,
            "removed": removed, "kept": kept, "ok": not kept}


# note-format 機器層 required(schema 正本 §3.1)+ 本模組會填的核心欄位。
# 這一組以外的 frontmatter key 一律要 `custom.` 前綴,否則就是在核心命名空間裡造欄位。
_CORE_FRONTMATTER = frozenset({
    "uid", "uid_scheme", "title", "routeKey", "category_uid", "privacy_level",
    "source_kind", "aliases", "created", "updated", "ez2fmt", "tags",
    "ai_readable", "ai_writable", "created_by_ai", "review_status",
    "parent_category_uid", "canonical_category_uid", "category_registry_ref",
    "legacy_category_path",
})
_CUSTOM_KEY_RE = __import__("re").compile(r"^custom\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _assert_custom_prefixed(extra):
    """自訂欄位閘(schema 正本 §3.4)。**改名的成本現在是一個檔,寫了幾千篇就是遷移。**"""
    for key in sorted(extra):
        if key in _CORE_FRONTMATTER:
            continue
        if not _CUSTOM_KEY_RE.match(key):
            raise WritePlanError(
                "frontmatter 欄位 %r 不合正本 §3.4:自訂欄位要 `custom.<ns>.<name>`,"
                "且不得與核心欄位重名(如 review_status)" % key)


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain-write-plan",
                                 description="F 寫回第二大腦守門鏈(第一刀:只產計畫,永不寫檔)")
    ap.add_argument("command", choices=["plan", "materialize", "apply", "undo", "probe"])
    ap.add_argument("--seat", help="plan 用:seat_id")
    ap.add_argument("--route", help="probe 用:routeKey(問這條路由 F 寫不寫得進去)")
    ap.add_argument("--items", help="plan 用:essence 條目 JSON(list 或 {items:[...]})")
    ap.add_argument("--contract", help="路由契約(檔案或 git:<repo>#<rev>:<rel>;預設 EZ2_ROUTE_REGISTRY)")
    ap.add_argument("--state-root", help="狀態根(預設 EZ2_AIB_HOME > ~/.ez2-aib)")
    ap.add_argument("--modules-dir", help="已 materialize 的 note-format 目錄(不給=現場取)")
    ap.add_argument("--ez2-ai", help="ez2-ai repo(預設 EZ2_AI_REPO > ~/ez2-ai)")
    ap.add_argument("--rev", default=DEFAULT_REV, help="ez2-ai rev(預設 main)")
    ap.add_argument("--receipt-key-file", help="人核准收據 HMAC 金鑰檔(預設 EZ2_F_RECEIPT_KEY_FILE)")
    ap.add_argument("--category-registry", help="分類登記表路徑(預設用 materialize 出來的那份)")
    ap.add_argument("--pins", help="note-format 指紋釘(預設 %s;對不上 rc=2)" % PINS_FILE.name)
    ap.add_argument("--no-pins", action="store_true",
                    help="明說不釘(plan 的 provenance 會記 pins_enforced=false)")
    ap.add_argument("--out", help="計畫落檔路徑(不給=印到 stdout)")
    ap.add_argument("--notes-out", help="plan 用:正文 staging 目錄(apply 只吃它,不重算)")
    ap.add_argument("--plan", help="apply 用:計畫檔路徑")
    ap.add_argument("--actor", help="apply/undo 必填:role@hex/Engine 具名")
    ap.add_argument("--vault", help="apply 用:vault 根(預設 EZ2_VAULT_DIR)")
    ap.add_argument("--receipts-dir",
                    help="apply 收據目錄(預設 <狀態根>/memory/apply-receipts;"
                         "**不再跟著 plan 走**,plan 會在暫存目錄)")
    ap.add_argument("--receipt", help="undo 用:apply 收據路徑")
    ap.add_argument("--dry-run", action="store_true", help="apply 用:只列會寫哪些檔,不動 vault")
    args = ap.parse_args(argv)

    pins = None if args.no_pins else (args.pins or PINS_DEFAULT)
    try:
        if args.command == "materialize":
            dest, shas, rev = materialize(ez2_ai=args.ez2_ai, rev=args.rev,
                                          dest=args.out, pins=pins)
            sys.stdout.write(_dump({"modules_dir": str(dest), "rev": rev, "sha256": shas,
                                    "pins_enforced": load_pins(pins) is not None}))
            return 0

        if args.command == "apply":
            out = apply_plan(args.plan, args.actor, vault=args.vault,
                             receipts_dir=args.receipts_dir, dry_run=args.dry_run,
                             state_root_arg=args.state_root)
            sys.stdout.write(_dump(out))
            return 0

        if args.command == "undo":
            out = undo_apply(args.receipt, args.actor)
            sys.stdout.write(_dump(out))
            return 0 if out["ok"] else 1

        if args.command == "probe":
            # L6-06:唯讀問「這條路由 F 寫得進去嗎」。ok → rc 0;寫不進 → rc 1(告知,不是錯誤);
            # 契約/runner 讀不到等 fail-closed → rc 2。與 plan/apply 的 rc 家族一致。
            if not args.route:
                raise WritePlanError("probe 需要 --route <routeKey>")
            res = probe_route_writability(args.route, contract_spec=args.contract,
                                          modules_dir=args.modules_dir, ez2_ai=args.ez2_ai,
                                          rev=args.rev, pins=pins,
                                          category_registry_path=args.category_registry)
            sys.stdout.write(_dump(res))
            if res["ok"]:
                return 0
            fail_closed = {"contract_unavailable", "note_format_runner_unavailable"}
            return 2 if any(c in fail_closed for c in res["codes"]) else 1

        if not args.seat or not args.items:
            raise WritePlanError("plan 需要 --seat 與 --items")
        # ★ 閘〇跑在最前面:在讀 items、跑守門鏈、產生任何位元組**之前**就把落點判掉。
        # 放到寫檔前一行也「擋得住」,但那時已經花了整條鏈的力氣,而且任何中途 print
        # 都可能先於判準說話——訊息不得早於判準(艦長 12:43 令第三條)。
        if args.out:
            refuse_vault_output(args.out)
        state_root_path = state_root(args.state_root)[0]
        with open(args.items, encoding="utf-8") as fh:
            doc = json.load(fh)
        items = doc.get("items") if isinstance(doc, dict) else doc
        plan = build_plan(state_root_path, args.seat, items, contract_spec=args.contract,
                          modules_dir=args.modules_dir,
                          receipt_key=load_receipt_key(args.receipt_key_file),
                          ez2_ai=args.ez2_ai, rev=args.rev,
                          category_registry_path=args.category_registry, pins=pins,
                          notes_out=args.notes_out)
    except WritePlanError as err:
        print("brain-write-plan: %s" % err, file=sys.stderr)
        return 2
    text = _dump(plan)
    if args.out:
        # 這句話以前印「未寫任何 vault 檔」——而它在 --out 指進 vault 時是假的。
        # 現在它只在閘〇放行後才說得出口,而且改成陳述閘的結果,不是陳述一個沒人驗過的宣稱。
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("brain-write-plan: 計畫已落 %s(已過 --out 閘:不在 vault 之下)" % args.out,
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    # 全被擋下不是錯,是閘在工作;但也不該報成「成功寫了」——rc 1 讓呼叫端看得見。
    return 0 if plan["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
