#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E5.5 `owner adapter` —— **模組惰性落地**(`PKG-CONSUMER-P1-ADAPTER`)。

本件只落這一支模組:**`src/` 下沒有任何呼叫端**,三處天花板一個位元組都不動
(天花板翻轉是 `PKG-CEILING-FLIP` 的三件,判準另出)。

判準正本 `qa3/criteria-owner-adapter-0827` = `bc6f7b51`,
臂表在 `tests/test-owner-adapter-inert.py`。

★ 本檔頭**不宣稱任何行為成立**。判準 `S9` 逐字:「原始碼裡寫著 X」不構成「X 成立」——
註解、docstring、常數名、函式名都不是證據。本模組的性質由臂表在**執行後**量,
承重的是 `E1`(零呼叫端,AST 判)、`G1`(import 零副作用,拋棄式 TMPDIR 前後比指紋)、
`G2`(頂層無可執行敘述,AST 判)這三支;其餘臂全綠只證明「模組存在且形狀合理」。

★ **本模組不重用天花板那五個常數字面。** `owner_authority_status` 等鍵住在
`src/binding/approval-receipt.schema.json`(正本)與 `decision_inbox.py` 的 `truth_ceiling`
裡,那一段行內註解自陳「正本 schema 核對,**勿改字**」。在這裡再抄一份就是第二份真相,
而兩份真相的失效形狀是**兩者不一致時沒有格子會紅**。本模組用自己的詞彙回答自己的問題;
把它接到天花板詞彙上,是翻轉件要連著 schema 一起裁的事。

用法(由測試直接呼叫;本件無產品呼叫端):

    inspect_owner_source({"kind": "file",
                          "receipt_path": "<path>",
                          "subject_hash": "<64 hex>"})
      -> {"status": STATUS_USABLE, "owner_subject_hash": ..., "receipt_sha256": ...}
      前提不足一律 raise OwnerAdapterError(reason=<具名碼>),**不回一個看起來合理的預設值**。
"""

import hashlib
import json
import os

SOURCE_KIND_FILE = "file"

STATUS_USABLE = "owner_source_usable"

REASON_SOURCE_MALFORMED = "owner_source_malformed"
REASON_SOURCE_KIND_UNSUPPORTED = "owner_source_kind_unsupported"
REASON_RECEIPT_UNREADABLE = "owner_receipt_unreadable"
REASON_RECEIPT_MALFORMED = "owner_receipt_malformed"
REASON_SUBJECT_MISSING = "owner_subject_missing"
REASON_SUBJECT_MISMATCH = "owner_subject_mismatch"

_REQUIRED_SOURCE_KEYS = ("kind", "receipt_path", "subject_hash")
_HEX = "0123456789abcdef"
_SUBJECT_HASH_LEN = 64


class OwnerAdapterError(Exception):
    """具名拒絕。

    ★ 帶 `reason` 而不是只帶一句話:呼叫端要能**機器判定**是哪一種拒絕。
    一個只有訊息字串的例外,在下游只剩「有錯」與「沒錯」兩態。
    """

    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
        super(OwnerAdapterError, self).__init__(
            "%s%s" % (reason, (": %s" % detail) if detail else ""))


def _is_subject_hash(value):
    if not isinstance(value, str) or len(value) != _SUBJECT_HASH_LEN:
        return False
    return all(ch in _HEX for ch in value.lower())


def inspect_owner_source(source):
    """判定呼叫端明給的 owner authority 來源**當下可不可用**,回可斷言的 dict。

    只讀呼叫端指定的那一個檔;不寫任何東西、不建目錄、不碰 vault、不發子行程。
    前提不足一律拋 `OwnerAdapterError`。
    """
    if not isinstance(source, dict):
        raise OwnerAdapterError(REASON_SOURCE_MALFORMED, "source 不是 dict")
    missing = [k for k in _REQUIRED_SOURCE_KEYS if k not in source]
    if missing:
        raise OwnerAdapterError(REASON_SOURCE_MALFORMED, "缺鍵 %s" % ",".join(missing))
    if source["kind"] != SOURCE_KIND_FILE:
        raise OwnerAdapterError(REASON_SOURCE_KIND_UNSUPPORTED, str(source["kind"]))
    if not _is_subject_hash(source["subject_hash"]):
        raise OwnerAdapterError(REASON_SOURCE_MALFORMED, "subject_hash 不是 64 位十六進位")

    path = source["receipt_path"]
    if not isinstance(path, str) or not path:
        raise OwnerAdapterError(REASON_SOURCE_MALFORMED, "receipt_path 不是非空字串")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as err:
        raise OwnerAdapterError(REASON_RECEIPT_UNREADABLE,
                                "%s(%s)" % (os.path.basename(path), err.__class__.__name__))
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise OwnerAdapterError(REASON_RECEIPT_MALFORMED, err.__class__.__name__)
    if not isinstance(receipt, dict):
        raise OwnerAdapterError(REASON_RECEIPT_MALFORMED, "頂層不是物件")

    subject = receipt.get("owner_subject_hash")
    if subject is None:
        raise OwnerAdapterError(REASON_SUBJECT_MISSING, "收據沒有 owner_subject_hash")
    if not _is_subject_hash(subject):
        raise OwnerAdapterError(REASON_RECEIPT_MALFORMED,
                                "owner_subject_hash 不是 64 位十六進位")
    if subject.lower() != source["subject_hash"].lower():
        raise OwnerAdapterError(REASON_SUBJECT_MISMATCH,
                                "收據的主體與來源宣稱的不同")

    return {
        "status": STATUS_USABLE,
        "owner_subject_hash": subject,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "receipt_bytes": len(raw),
    }
