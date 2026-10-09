#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pane 頂端出口 —— 五家／bot 同一支 top_line 渲染(薄封裝)。

設計:DISPATCH-1857a-evening-0926 包 B;docs/DESIGN-persona-autoload-0926.md §10.14 / §10.16.6。
不碰 i18n/*.json、不改 render_lib 標籤字面(a seat 寫集)。

用法(五家 CLI 同一出口,rc 0=印出;2=用法錯):
  python3 tools/hdr/pane_top.py [--width N] [--json-fields PATH] [--report-seat CODE]
  python3 tools/hdr/pane_top.py --from-seat-file PATH [--width N]
  echo '{"fields":{…},"approval_meta":{…}}' | python3 tools/hdr/pane_top.py --stdin-json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import top_line as TL  # noqa: E402

COCKPIT = HERE.parent.parent / "src" / "cockpit"
if str(COCKPIT) not in sys.path:
    sys.path.insert(0, str(COCKPIT))


def _load_flags() -> tuple[int, int]:
    """讀 A 的 count_flags(若已合入);否則誠實 stub 0。

    ★ 本 tip 的 tools/project/flags.py 尚無 count_flags(包 A 未合);
      drift_n/orphan_n 先回 0;測延後到 A 合入後對接。
    """
    flags_path = HERE.parent / "project" / "flags.py"
    if not flags_path.is_file():
        return 0, 0
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("aib_flags", str(flags_path))
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        if not hasattr(mod, "count_flags"):
            return 0, 0
        events_path = os.environ.get("AIB_DRIFT_EVENTS_JSON", "")
        events = None
        if events_path and Path(events_path).is_file():
            raw = json.loads(Path(events_path).read_text(encoding="utf-8"))
            if isinstance(raw, list):
                events = raw
            elif isinstance(raw, dict):
                events = raw.get("events") or raw.get("items") or []
        counts = mod.count_flags(events, writeset=None)
        return int(counts.get("detour_unreturned", 0)), int(counts.get("orphan", 0))
    except Exception:
        return 0, 0


def _resolve_report_seat(role: str, explicit: str = "") -> str:
    if (explicit or "").strip():
        return explicit.strip().lstrip("@")
    role = (role or "").strip()
    if not role:
        return ""
    if role in ("第一艦長", "第一舰长", "captain", "captain_seat"):
        try:
            import cooldown  # noqa: E402
            code = cooldown.captain_seat()
            if code:
                return str(code).lstrip("@")
        except Exception:
            pass
    return ""


def render_from_payload(payload: Mapping[str, Any], *, width: int = TL.DEFAULT_WIDTH) -> str:
    fields = payload.get("fields") or {}
    if not isinstance(fields, Mapping):
        fields = {}
    task_meta = payload.get("task_meta") if isinstance(payload.get("task_meta"), Mapping) else {}
    approval_meta = (
        payload.get("approval_meta")
        if isinstance(payload.get("approval_meta"), Mapping)
        else {}
    )
    report = str(payload.get("report_code") or "")
    if not report:
        report = _resolve_report_seat(str(fields.get("report_to") or ""))
    drift_n = payload.get("drift_n")
    orphan_n = payload.get("orphan_n")
    if drift_n is None or orphan_n is None:
        d, o = _load_flags()
        if drift_n is None:
            drift_n = d
        if orphan_n is None:
            orphan_n = o
    return TL.render_top_line_from_fields(
        fields,
        task_meta=task_meta,
        approval_meta=approval_meta,
        report_code=report,
        drift_n=int(drift_n or 0),
        orphan_n=int(orphan_n or 0),
        width=int(width),
    )


def render_from_seat_file(path: str | os.PathLike, *, width: int = TL.DEFAULT_WIDTH) -> str:
    import seat_task_fields as STF  # noqa: E402

    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = STF.read_seat_task_fields(rec if isinstance(rec, dict) else {})
    return render_from_payload({"fields": fields}, width=width)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pane_top.py")
    ap.add_argument("--width", type=int, default=TL.DEFAULT_WIDTH)
    ap.add_argument("--json-fields", default="", help="JSON 檔:{fields,task_meta,approval_meta,…}")
    ap.add_argument("--from-seat-file", default="", help="席位檔路徑")
    ap.add_argument("--report-seat", default="", help="覆寫回報席碼")
    ap.add_argument("--stdin-json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.stdin_json:
            payload = json.loads(sys.stdin.read() or "{}")
            if args.report_seat:
                payload["report_code"] = args.report_seat
            line = render_from_payload(payload, width=args.width)
        elif args.json_fields:
            payload = json.loads(Path(args.json_fields).read_text(encoding="utf-8"))
            if args.report_seat:
                payload["report_code"] = args.report_seat
            line = render_from_payload(payload, width=args.width)
        elif args.from_seat_file:
            import seat_task_fields as STF
            rec = json.loads(Path(args.from_seat_file).read_text(encoding="utf-8"))
            fields = STF.read_seat_task_fields(rec if isinstance(rec, dict) else {})
            line = render_from_payload(
                {"fields": fields, "report_code": args.report_seat or ""},
                width=args.width,
            )
        else:
            line = render_from_payload({"fields": {}}, width=args.width)
    except Exception as exc:  # noqa: BLE001
        print("pane_top: %s" % exc, file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
