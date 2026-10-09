#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席選專案 stub(包 B 寫集補丁;完整 30s 選單屬 a seat N1)。

讀序:
  1. env AIB_PROJECT_UID / AIB_PROJECT_CARD_JSON
  2. 席位檔 project_uid / project_card(若有)
  3. 否則回 None(呼叫端視為未選 → 接手包不送、open_ack.delivered=false)

★ DELIVERY 註:a seat 寫集原列 seat_fork 交遞;真注入在 seat_load。
   本檔是最小 pick API,供 insert_handoff_pack 取專案卡。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _env_state_root(env: Mapping[str, str]) -> str:
    """environ 裡**明給**的狀態根,順位走單源 seats_dir_lib.state_root(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME);
    兩個都沒給 ⇒ 空字串(不退 ~/.ez2-aib:「沒給就不去猜家目錄」是本檔的契約)。"""
    cockpit = str(HERE.parent / "cockpit")
    if cockpit not in sys.path:
        sys.path.append(cockpit)
    import seats_dir_lib
    root, source = seats_dir_lib.state_root(env)
    return "" if source.startswith("default:") else str(root).strip()


def _real_card(state_root, uid: str) -> Optional[dict[str, Any]]:
    """<狀態根>/projects/<uid>/card.json;不在、讀不到、形狀不合、uid 對不上 ⇒ None(呼叫端退回替身卡)。"""
    if not state_root or not uid:
        return None
    try:
        from project_card import validate_project_card, validate_project_uid  # noqa: E402
        path = Path(str(state_root)).expanduser() / "projects" / validate_project_uid(uid) / "card.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("project_uid") != uid:
            return None
        return validate_project_card(raw)
    except Exception:  # noqa: BLE001  開席鏈上:讀不到不擋開席
        return None


def pick_project(
    *,
    seat_record: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
    state_root: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    """回專案卡 dict,或 None(未選)。不擋開席。

    09-29 a seat:席位檔只記 project_uid 與標題;以前這裡一律回一張**空的替身卡**,
    於是開席接手包裡的紅線、目標、工作狀態永遠是空的。現在先找專案卡正本
    (`state_root` 明給,或 environ 裡的 EZ2_AIB_HOME);找不到才退回替身卡。
    兩個都沒給 ⇒ 不去猜家目錄,行為與從前相同。"""
    env = environ if environ is not None else os.environ
    card_json = (env.get("AIB_PROJECT_CARD_JSON") or "").strip()
    if card_json:
        try:
            if card_json.startswith("{"):
                raw = json.loads(card_json)
            else:
                raw = json.loads(Path(card_json).expanduser().read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("project_uid"):
                if raw.get("schema") != "aib-project-card/v1":
                    raw = dict(raw)
                    raw["schema"] = "aib-project-card/v1"
                return raw
        except (OSError, ValueError):
            pass
    uid = (env.get("AIB_PROJECT_UID") or "").strip()
    seat = seat_record if isinstance(seat_record, Mapping) else {}
    if not uid:
        uid = str(seat.get("project_uid") or "").strip()
    if seat.get("project_card") and isinstance(seat.get("project_card"), Mapping):
        card = dict(seat["project_card"])
        if card.get("project_uid"):
            if card.get("schema") != "aib-project-card/v1":
                card["schema"] = "aib-project-card/v1"
            return card
    if not uid:
        return None
    real = _real_card(state_root or _env_state_root(env), uid)
    if real is not None:
        return real
    from project_card import make_project_card  # noqa: E402

    return make_project_card(
        uid,
        title=str(seat.get("project_title") or uid),
        repo_fingerprints={"stub": "0123456789abcdef"},
    )
