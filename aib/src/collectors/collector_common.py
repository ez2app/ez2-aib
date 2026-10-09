# SPDX-License-Identifier: Apache-2.0
"""E1.2 五源收集器共用件:canonical 雜湊、HLC、actor 推導、事件組裝。

收集器是純轉換器:讀源(唯讀)-> 正規化成 schemas/event.schema.json 形狀 -> print JSONL。
不落檔、不簽章(alg=unsigned_draft)、不入鏈(prev_event_hash=null,入鏈是 E1.1 帳本 append 的職責)。
event_id 內容定址(ev_+sha256(canonical)):同一源紀錄重跑必得同 id,帳本端可安全去重。
"""
import fcntl
import hashlib
import json
import os
import re
import socket
from pathlib import Path

SCHEMA_VERSION = "0.1.0-draft"
HOSTNAME = socket.gethostname()

# 與 event.schema.json 的 actor.sig pattern 同義;守在組裝咽喉,不等 schema 驗才爆
SIG_RE = re.compile(r"^[a-z0-9-]{1,32}@[0-9a-f]{4,8}/[A-Za-z0-9._-]{1,32}$")

# 跨執行 HLC 狀態檔目錄(可用環境變數改道;測試一律用 tmpdir)
STATE_DIR = Path(os.environ.get("EZ2_AIB_STATE_DIR", str(Path.home() / ".ez2-aib"))) / "hlc"


def _canon(obj):
    """canonical JSON:排序鍵+緊湊分隔+不轉義非 ASCII;雜湊一律打在這個形狀上。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitize_sig_name(name):
    """sig 的 name 段只容 [a-z0-9-]{1,32};源欄位髒(大寫/底線/空)一律消毒不硬爆。"""
    return re.sub(r"[^a-z0-9-]", "-", str(name).lower())[:32] or "x"


def sanitize_engine(engine):
    """sig 的 engine 段只容 [A-Za-z0-9._-]{1,32}。"""
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(engine))[:32] or "Unknown"


def derive_sig(name, ai_id, engine):
    """源頭沒有 uuid 時的顯示簽名:name@sha256(ai_id) 前五碼/Engine。
    確定性推導(同 ai_id 永遠同 sig),並非自報身份;承重的是 ai_id 全字串。
    name/engine 在此咽喉消毒,保證產出必過 schema 的 sig pattern。"""
    return f"{sanitize_sig_name(name)}@{sha256_text(ai_id)[:5]}/{sanitize_engine(engine)}"


def system_actor(name, engine="system"):
    """源頭本身不含行為者資訊時,誠實記收集器所觀測的系統面向為 actor(kind=system),
    絕不憑空捏造某個 AI 當事件主人(原理 1:不信自報,更不自己編)。"""
    ai_id = f"system:{HOSTNAME}:{name}"
    return {
        "ai_id": ai_id,
        "sig": derive_sig(name, ai_id, engine),
        "kind": "system",
        "engine": engine,
    }


class Hlc:
    """單次執行內的 HLC 發配器(回填型):同 wall_ms 依讀入順序遞增 counter。
    源是 append-only 或穩定快照時,重跑順序不變 => counter 不變 => event_id 不變(去重前提)。"""

    _COUNTER_MAX = 65535  # 對齊 schema hlc.counter maximum

    def __init__(self, node_id):
        self.node_id = f"{node_id}@{HOSTNAME}"[:64]
        self._last_ms = -1
        self._counter = 0

    def stamp(self, wall_ms):
        if wall_ms <= self._last_ms:
            # 同毫秒(或源時戳倒退時的持久地板)靠 counter 排;溢位就進位到下一毫秒
            wall_ms = self._last_ms
            if self._counter + 1 > self._COUNTER_MAX:
                wall_ms += 1
                self._counter = 0
            else:
                self._counter += 1
        else:
            self._counter = 0
        self._last_ms = wall_ms
        return {"wall_ms": wall_ms, "counter": self._counter, "node_id": self.node_id}


class PersistentHlc(Hlc):
    """跨執行持久化的 HLC 發配器(發行型;艦長修正令 2,Gemini 跨審採納項):
    last-hlc 落狀態檔+fcntl 排他鎖,行程重啟/併發發配皆維持嚴格遞增不倒退。

    邊界(重要):回填型收集器刻意**不用**這個 —— 持久地板會讓重跑改變 wall_ms/counter,
    內容定址 event_id 隨之改變,帳本端去重全毀。只有「記當下動作」的發行型時戳適用。
    詳見 COLLECTORS-SPEC.md 開放問題 5。

    用法(鎖跨越整段發配期):
        with PersistentHlc("my-issuer") as hlc:
            stamp = hlc.stamp(now_ms)
    """

    def __init__(self, node_id, state_dir=None):
        super().__init__(node_id)
        d = Path(state_dir) if state_dir else STATE_DIR
        d.mkdir(parents=True, exist_ok=True)
        self._state_path = d / (re.sub(r"[^A-Za-z0-9._-]", "-", self.node_id) + ".json")
        self._fh = None

    def __enter__(self):
        self._fh = open(self._state_path, "a+", encoding="utf-8")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        self._fh.seek(0)
        raw = self._fh.read().strip()
        if raw:  # 狀態檔壞掉=fail-loud(讓呼叫端決定),不靜默歸零重發
            state = json.loads(raw)
            self._last_ms = int(state["wall_ms"])
            self._counter = int(state["counter"])
        return self

    def stamp(self, wall_ms):
        # 地板:絕不早於已發配的時刻;倒退的 wall_ms 併入同毫秒靠 counter 排
        return super().stamp(max(wall_ms, self._last_ms))

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._fh.seek(0)
                self._fh.truncate()
                json.dump({"wall_ms": self._last_ms, "counter": self._counter}, self._fh)
                self._fh.flush()
                os.fsync(self._fh.fileno())
        finally:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        return False


def make_event(kind, hlc_stamp, actor, body, correlation_id=None, causation_id=None):
    """組出**草稿**(ADR-0002 修正,codex 審 E1.2-高 2026-08-10)。

    舊版在此鑄造 event_id/hlc/integrity/schema_version——全是帳本專屬欄位,
    Ledger.append() 明文拒收,五源因此從未真正接通帳本。修正後:
      - 草稿只含 {kind, actor, body, causation};
      - 重跑穩定性改由 dedup 鍵承擔(ADR-0002:body_sha256+correlation_id):
        correlation_id 未給時自動導出=源內容指紋,同源紀錄重跑必得同鍵;
      - 源時戳以 source_wall_ms 旁載,入帳時傳 Ledger.append(wall_ms=…),
        HLC 由帳本發配(單一發鐘者,分叉不可能)。
    函式名保留 make_event 以最小化呼叫端 diff;語意=make_draft。"""
    causation = {"causation_id": causation_id}
    # 指紋必須含源時戳(codex 審中1):否則「不同時間但轉換後 body 相同」的
    # 兩筆真實事件會共用 correlation,被 dedup 誤吞成一筆。
    core = {"kind": kind, "actor": actor, "body": body,
            "causation_id": causation_id,
            "source_wall_ms": int(hlc_stamp["wall_ms"])}
    causation["correlation_id"] = correlation_id or (
        "src:" + sha256_text(_canon(core))[:32])
    return {
        "kind": kind,
        "actor": actor,
        "body": body,
        "causation": causation,
        "source_wall_ms": int(hlc_stamp["wall_ms"]),
    }


def seal_standalone(draft, hlc_stamp):
    """草稿 -> **不入鏈的獨立事件**(policy/lineage 判定用)。

    與 ingest 的分工要說清楚,否則就會回到 E1.2 那個矛盾:
      - ingest:要進帳本 -> 帳本發 HLC/event_id/鏈雜湊(唯一發鐘者)。
      - seal_standalone:只是「一份可驗形狀的判定紀錄」,prev_event_hash=null,
        event_id 為內容定址,刻意不接鏈。
    這類事件日後若要入帳,**必須改走 ingest 重新發配 id**,不得把此處的
    id 當帳本 id 用(ADR-0002:event_id 綁鏈位置)。
    """
    core = {"kind": draft["kind"], "hlc": hlc_stamp, "actor": draft["actor"],
            "body": draft["body"], "causation": draft["causation"]}
    return {
        "schema_version": SCHEMA_VERSION,
        # st_ 前綴=獨立判定事件,與帳本 ev_ 分屬不同 id namespace
        # (codex 審中3:資料本身要能區分,不能只靠文件禁止)
        "event_id": "st_" + sha256_text(_canon(core)),
        "kind": draft["kind"],
        "hlc": hlc_stamp,
        "actor": draft["actor"],
        "body": draft["body"],
        "causation": draft["causation"],
        "integrity": {
            "body_sha256": sha256_text(_canon(draft["body"])),
            "prev_event_hash": None,
            "signature": {"alg": "unsigned_draft",
                          "key_id": hlc_stamp["node_id"], "value": ""},
        },
    }


def ingest(draft, ledger):
    """草稿 -> 帳本:唯一合法入帳通道(閉環的後半段)。

    dedup=True=回填型收集器的預設(同源重跑吃掉);source_wall_ms 交由
    帳本 backfill 模式;帳本擁有 HLC/event_id/integrity。"""
    payload = {k: draft[k] for k in ("kind", "actor", "body", "causation")}
    return ledger.append(payload, wall_ms=draft.get("source_wall_ms"),
                         dedup=True)


def emit(event):
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))


def clip(text, limit=300):
    """summary 進 i18n_text(maxLength 2000);源欄位可能很長,收斂到可讀長度。"""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
