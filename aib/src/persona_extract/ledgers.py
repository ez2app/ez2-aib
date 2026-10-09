# SPDX-License-Identifier: Apache-2.0
"""候選安全庫內的 append-only 帳(evidence.jsonl / accepted.jsonl)。

落點固定在 writeguard.candidate_root()/<persona_code>/,**只經 writeguard.open_for_write**。
硬化取 rotation._append_jsonl 的精神(鎖內寫、末行完整、壞行整檔拒讀、fsync),不 import
rotation(避免耦合艦長輪替模組)。
"""

import fcntl
import json
import os
import re

import writeguard

EVIDENCE_NAME = "evidence.jsonl"
ACCEPTED_NAME = "accepted.jsonl"
_CODE_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,64}$")  # 拒 "."/".."(退化值寫到根層)
MAX_BYTES = 1_000_000


class LedgerError(Exception):
    pass


def ledger_path(persona_code, name):
    if name not in (EVIDENCE_NAME, ACCEPTED_NAME):
        raise LedgerError("unknown ledger %r" % (name,))
    if not isinstance(persona_code, str) or not _CODE_RE.match(persona_code):
        raise LedgerError("bad persona code %r" % (persona_code,))
    return os.path.join(writeguard.candidate_root(), persona_code, name)


def append(persona_code, name, entry):
    """一筆 dict → 一行 canonical JSON;寫前檢既有末行完整、大小上限;鎖內寫+fsync。"""
    if not isinstance(entry, dict):
        raise LedgerError("entry must be dict")
    path = ledger_path(persona_code, name)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with writeguard.open_for_write(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.flush()
        size = os.fstat(fh.fileno()).st_size
        if size + len(line.encode("utf-8")) > MAX_BYTES:
            raise LedgerError("%s over %d bytes" % (name, MAX_BYTES))
        if size:
            with open(path, "rb") as rd:
                rd.seek(size - 1)
                if rd.read(1) != b"\n":
                    raise LedgerError("%s last line incomplete; refuse to append" % name)
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def read(persona_code, name):
    """全部行;任一壞行=整檔拒用(fail-closed);無檔=[]。"""
    path = ledger_path(persona_code, name)
    if not os.path.isfile(path):
        return []
    if os.path.getsize(path) > MAX_BYTES:
        raise LedgerError("%s over %d bytes; refuse to read" % (name, MAX_BYTES))
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, raw in enumerate(fh.read().split("\n"), 1):
            if not raw.strip():
                continue
            try:
                doc = json.loads(raw)
            except ValueError as err:
                raise LedgerError("%s line %d corrupt: %s" % (name, i, err))
            if not isinstance(doc, dict):
                raise LedgerError("%s line %d not an object" % (name, i))
            out.append(doc)
    return out
