#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席檔主任務四欄:型別＋讀／驗證／寫(只碰四欄;永不碰 reports_to)。

設計:docs/DESIGN-persona-autoload-0926.md §10.14。
四欄:
  task_uid            —— 主任務(計畫樹 Task uid)
  task_started_at     —— 主任務指派到這一席的時刻(不是開席時刻);ISO-8601 或 MM-DD HH:MM
  approval_event_uid  —— 真人核准事件 uid(命名對齊 DESIGN;展開成 the maintainer HH:MM「同意」)
  report_to           —— **角色**(第一艦長／整合席…),渲染時再查冠位席碼

★★ 明確區隔:本欄 `report_to`(角色) ≠ 既有席位檔／派工裡的 `reports_to`(席碼)。
   本模組**不讀、不寫、不覆蓋** `reports_to`;也不呼叫 `write_seat_record`。
   寫入只走 `update_task_fields` → `atomic_json.update_json`,合併 FIELD_KEYS。

CLI(給 aib task 入口 a seat / fc45d5bb 分派):
  python3 …/seat_task_fields.py show --seat-file PATH | --seat-id ID [--aib-home H]
  python3 …/seat_task_fields.py set  --seat-file PATH | --seat-id ID [--aib-home H]
       [--task-uid U] [--task-started-at T] [--approval-event-uid E] [--report-to ROLE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

FIELD_KEYS = (
    "task_uid",
    "task_started_at",
    "approval_event_uid",
    "report_to",
)

# 與計畫樹 uid／事件 uid 同形鬆綁;空字串＝未派／未核准。
UID_RE = re.compile(r"^[A-Za-z0-9:._@-]{4,128}$")
# 時刻:ISO 或回報慣例 MM-DD HH:MM / MM-DD HH:Mx
TIME_RE = re.compile(
    r"^("
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([Zz]|[+-]\d{2}:?\d{2})?"
    r"|"
    r"\d{2}-\d{2} \d{2}:[\dXx]{2}"
    r")$"
)
# 角色:小寫短名或中文角色名(第一艦長／整合席);不是席碼五碼。
ROLE_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}$")
SEAT_CODE_RE = re.compile(r"^[0-9a-f]{5}$")


class SeatTaskFieldsError(ValueError):
    """四欄形狀不合法。"""


_ERR_TR: dict = {}


def _err(key: str, **params: Any) -> str:
    """錯誤訊息走 i18n(10-15:英文語系不再印中文錯誤);鍵在 aib.task.err.*。
    字典讀不到 ⇒ 鍵名(可見即可抓),不擋指令。"""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _ERR_TR.get(loc)
        if tr is None:
            tr = _ERR_TR[loc] = i18n_lib.Translator(loc)
        return tr.t("aib.task.err." + key, params or None)
    except Exception:  # noqa: BLE001
        return "aib.task.err." + key


def _opt_uid(name: str, raw: Any) -> str:
    if raw is None or raw == "":
        return ""
    text = str(raw).strip()
    if not UID_RE.fullmatch(text):
        raise SeatTaskFieldsError(_err("uid_bad", name=name, value=repr(raw)))
    return text


def _opt_time(raw: Any) -> str:
    if raw is None or raw == "":
        return ""
    text = str(raw).strip()
    if not TIME_RE.fullmatch(text):
        raise SeatTaskFieldsError(_err("time_bad", value=repr(raw)))
    return text


def _opt_role(raw: Any) -> str:
    """report_to 存角色。拒絕五碼席碼,以免與 reports_to 混淆寫入。"""
    if raw is None or raw == "":
        return ""
    text = str(raw).strip()
    if SEAT_CODE_RE.fullmatch(text):
        raise SeatTaskFieldsError(_err("role_is_seat_code", value=repr(raw)))
    if not ROLE_RE.fullmatch(text):
        raise SeatTaskFieldsError(_err("role_bad", value=repr(raw)))
    return text


def validate_seat_task_fields(data: Mapping[str, Any]) -> dict[str, str]:
    """驗證並正規化四欄;缺欄視為空字串(未派)。不碰 reports_to。"""
    if not isinstance(data, Mapping):
        raise SeatTaskFieldsError(_err("fields_not_object"))
    return {
        "task_uid": _opt_uid("task_uid", data.get("task_uid", "")),
        "task_started_at": _opt_time(data.get("task_started_at", "")),
        "approval_event_uid": _opt_uid(
            "approval_event_uid", data.get("approval_event_uid", "")
        ),
        "report_to": _opt_role(data.get("report_to", "")),
    }


def read_seat_task_fields(seat_record: Mapping[str, Any]) -> dict[str, str]:
    """從席位檔紀錄讀四欄(投影)。

    ★ 不呼叫 write_seat_record;★ 不讀、不改 `reports_to`。
    未知／缺席的鍵 → 空字串。
    """
    if not isinstance(seat_record, Mapping):
        raise SeatTaskFieldsError(_err("seat_not_object"))
    slim = {k: seat_record.get(k, "") for k in FIELD_KEYS}
    return validate_seat_task_fields(slim)


def has_primary_task(fields: Mapping[str, str]) -> bool:
    return bool((fields or {}).get("task_uid"))


def empty_seat_task_fields() -> dict[str, str]:
    return {k: "" for k in FIELD_KEYS}


def merge_task_fields(
    seat_record: Mapping[str, Any], patch: Mapping[str, Any]
) -> dict[str, Any]:
    """回傳席位檔副本,只覆寫 FIELD_KEYS(經驗證)。不碰 reports_to。"""
    if not isinstance(seat_record, Mapping):
        raise SeatTaskFieldsError(_err("seat_not_object"))
    out = dict(seat_record)
    # 以現況四欄為底,再套 patch(只收 FIELD_KEYS)
    base = {k: out.get(k, "") for k in FIELD_KEYS}
    for k in FIELD_KEYS:
        if k in patch and patch[k] is not None:
            base[k] = patch[k]
    validated = validate_seat_task_fields(base)
    for k, v in validated.items():
        out[k] = v
    return out


def update_task_fields(
    seat_path: str | os.PathLike,
    patch: Mapping[str, Any],
) -> dict[str, str]:
    """原子合併四欄進席位檔。★ 不呼叫 write_seat_record;★ 永不寫 reports_to。"""
    path = str(seat_path)
    # 延遲匯入:測試環境可能只測純函式
    import atomic_json  # noqa: E402  同目錄

    def _mutate(rec: dict) -> dict:
        return merge_task_fields(rec if isinstance(rec, dict) else {}, patch)

    new_rec = atomic_json.update_json(path, _mutate, default={})
    return read_seat_task_fields(new_rec)


def find_seat_file(
    *,
    seat_id: str = "",
    seat_file: str = "",
    aib_home: str = "",
) -> Path:
    """解析席位檔路徑。給 --seat-file 直接用;否則依 seat_id 在**本艙**的席位目錄裡找。

    ★ 席位目錄一律走單源 seats_dir_lib.seats_dir(HUB 導出 > EZ2_AIB_INSTANCE_HOME > 狀態根下的席位目錄)。
      原本自己拼 <home>/instances/*/seats 加主艙席位目錄全掃:多艙下同檔名同窗號的主艙檔是「格式正確的錯答案」,
      解錯不報錯(v0.69.0 B8 test-seats-dir-resolution 紅,艦長 09-27 04:48 派)。
      --aib-home 給了就當狀態根(EZ2_AIB_STATE_ROOT),HUB 仍照單源導出。跨艙找席不是本指令的事。"""
    if seat_file:
        p = Path(seat_file).expanduser()
        if not p.is_file():
            raise SeatTaskFieldsError(_err("seat_file_missing", path=p))
        return p
    sid = (seat_id or "").strip().lstrip("@").lower()
    if not sid:
        raise SeatTaskFieldsError(_err("need_seat"))
    import seats_dir_lib  # noqa: E402  同目錄單源
    env = dict(os.environ)
    if aib_home:
        env["EZ2_AIB_STATE_ROOT"] = str(Path(aib_home).expanduser())
        env.pop("EZ2_AIB_INSTANCE_HOME", None)  # 明給的根優先於殼帶進來的艙家
    home = Path(seats_dir_lib.seats_dir(environ=env))
    cands: list[Path] = sorted(home.glob("w*.json")) if home.is_dir() else []
    hits: list[Path] = []
    for path in cands:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        if str(rec.get("seat_id") or "").lower() == sid:
            hits.append(path)
            continue
        su = str(rec.get("session_uuid") or "")
        if len(su) >= 5 and su[-5:].lower() == sid:
            hits.append(path)
    if not hits:
        raise SeatTaskFieldsError(_err("seat_not_found", sid=sid, home=home))
    if len(hits) > 1:
        # 同一個席位目錄裡兩份都對得上 = 席位檔本身重複,報錯不猜
        raise SeatTaskFieldsError(
            _err("seat_ambiguous", sid=sid, paths=", ".join(str(h) for h in hits))
        )
    return hits[0]


def _cmd_show(args: argparse.Namespace) -> int:
    path = find_seat_file(
        seat_id=args.seat_id, seat_file=args.seat_file, aib_home=args.aib_home
    )
    rec = json.loads(path.read_text(encoding="utf-8"))
    fields = read_seat_task_fields(rec if isinstance(rec, dict) else {})
    print(json.dumps({"path": str(path), "fields": fields}, ensure_ascii=False))
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    path = find_seat_file(
        seat_id=args.seat_id, seat_file=args.seat_file, aib_home=args.aib_home
    )
    patch: dict[str, Any] = {}
    if args.task_uid is not None:
        patch["task_uid"] = args.task_uid
    if args.task_started_at is not None:
        patch["task_started_at"] = args.task_started_at
    if args.approval_event_uid is not None:
        patch["approval_event_uid"] = args.approval_event_uid
    if args.report_to is not None:
        patch["report_to"] = args.report_to
    if not patch:
        print(_help_t()("no_fields"), file=sys.stderr)
        return 2
    try:
        fields = update_task_fields(path, patch)
    except SeatTaskFieldsError as exc:
        print("seat_task_fields: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — atomic 失敗也要非 0
        print("aib task: %s" % _help_t()("write_failed", err=exc), file=sys.stderr)
        return 1
    print(json.dumps({"path": str(path), "fields": fields}, ensure_ascii=False))
    return 0


def _help_t():
    """說明文字走 i18n(10-15:英文語系不再印中文 help)。字典讀不到就退回鍵名,不擋指令。"""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
    except Exception:  # noqa: BLE001
        return lambda key, **kw: key

    def T(key, **params):
        return i18n_lib.t("aib.task.help." + key, params or None, locale=loc)
    return T


def main(argv: Optional[list[str]] = None) -> int:
    T = _help_t()
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_TASK_PROG") or "aib task", description=T("description"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_seat_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--seat-file", default="", help=T("seat_file"))
        p.add_argument("--seat-id", default="", help=T("seat_id"))
        p.add_argument("--aib-home", default="", help=T("aib_home"))

    sh = sub.add_parser("show", help=T("show"), description=T("show"))
    add_seat_args(sh)
    st = sub.add_parser("set", help=T("set"), description=T("set"))
    add_seat_args(st)
    st.add_argument("--task-uid", default=None, help=T("task_uid"))
    st.add_argument("--task-started-at", default=None, help=T("task_started_at"))
    st.add_argument("--approval-event-uid", default=None, help=T("approval_event_uid"))
    st.add_argument("--report-to", default=None, help=T("report_to"))

    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            return _cmd_show(args)
        if args.cmd == "set":
            return _cmd_set(args)
    except SeatTaskFieldsError as exc:
        print("seat_task_fields: %s" % exc, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
