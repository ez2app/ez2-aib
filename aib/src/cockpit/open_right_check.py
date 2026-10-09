#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席成功判準(the maintainer 09-14 18:0x 定案:「右方顯示**正確**的席位資訊 = 開席成功,最快最精簡」)。

右欄自己落的 ai-rowmap.json 裡,這一席那一列要**五項同時**對,才算「右方開席顯示完成」:
  ① 席碼:列的 name 是這一席位檔算出來的 @五碼(seat_address.label_of,同右欄那一支)
  ② 角色:列的 role 等於開席時給的角色
  ③ 引擎/模型:列的 engine 是這家 provider 的三字碼。模型名**不列入必要**:右欄只在 CLI 起來、
     探到之後才知道模型(claude 以外都在 5 秒之後;copilot 結構上讀不到),the maintainer 18:3x 實測五席四席
     因此被判未確認——「量不到」不等於「不正確」。有模型就是加分,沒有不扣。
  ④ 人格名正確:列的 persona 是 well-formed 的名字(括號閉合、不是描述文);沒綁人格=空字串=正確
  ⑤ 狀態活:列的 label 沒有 `!`、沒有「開席失敗 / 等待逾時 / 視窗已死」,status 不是 failed
缺任一項 = 未確認(回 missing 清單),不是綠。

純函式 check_row() 給尺;CLI 給 aib:
  open_right_check.py --rowmap F --window @N --seat-file F --role R --provider P
  stdout 一行 JSON {"found": bool, "missing": [...], "row": {...}};rc 0(找不到列也是 0:那是「還沒畫」不是錯)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ITEMS = ("address_code", "role", "engine_model", "persona", "alive")
FAIL_PHRASES = ("開席失敗", "等待逾時", "視窗已死", "席位已結束")


def _label_of(seat_rec):
    try:
        import seat_address
        return seat_address.label_of(seat_rec)
    except Exception:
        return None


def _engine_letters():
    """三字碼唯一正本在 render/render_lib.ENGINE_LETTERS(右欄畫的就是它);import 不到就回空表 ⇒ ③ 必缺,不猜。"""
    try:
        sys.path.insert(0, os.path.join(HERE, "render"))
        from render_lib import ENGINE_LETTERS
        return dict(ENGINE_LETTERS)
    except Exception:
        return {}


def _well_formed(name):
    try:
        import seat_persona
        return seat_persona.well_formed_name(name)
    except Exception:
        return name


def find_row(rows, window):
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("window") == window and (r.get("detail") or {}).get("kind") == "main":
            return r.get("detail") or {}
    return None


def check_row(detail, seat_rec, role, provider):
    """一列的 detail → 缺的判準名清單(空=五項全對)。純函式:不讀檔、不看時鐘。"""
    missing = []
    if not isinstance(detail, dict):
        return list(ITEMS)
    expect_label = _label_of(seat_rec) or ""
    name = str(detail.get("name") or detail.get("label") or "")
    if not expect_label or not expect_label.startswith("@") or name != expect_label:
        missing.append("address_code")
    if not role or str(detail.get("role") or "") != str(role):
        missing.append("role")
    letters = _engine_letters().get(str(provider or "").lower())
    engine_ok = bool(letters) and str(detail.get("engine") or "") == letters
    if not engine_ok:
        missing.append("engine_model")
    persona = detail.get("persona")
    if persona is not None and not isinstance(persona, str):
        missing.append("persona")
    elif persona and _well_formed(persona) != persona:
        missing.append("persona")
    label = str(detail.get("label") or "")
    status = str(detail.get("status") or "")
    if "!" in label or any(p in label for p in FAIL_PHRASES) or status == "failed":
        missing.append("alive")
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rowmap", required=True)
    ap.add_argument("--window", required=True)
    ap.add_argument("--seat-file", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--provider", required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.rowmap, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        rows = []
    try:
        with open(a.seat_file, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        rec = {}
    detail = find_row(rows, a.window)
    if detail is None:
        print(json.dumps({"found": False, "missing": list(ITEMS), "row": None}, ensure_ascii=False))
        return 0
    missing = check_row(detail, rec, a.role, a.provider)
    print(json.dumps({"found": True, "missing": missing, "row": detail}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
