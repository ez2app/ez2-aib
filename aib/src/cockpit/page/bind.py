#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""262(c) `aib page bind`:一次性配對碼換有範圍的助理憑證。

規格 §三。四步裡 AIB 只做第 1、3、4 步(第 2 步是人在 PAGE 後台輸入碼)。

時鐘與 sleep 都是參數:尺要推時間(規格 §七 A13「10 分鐘」那三臂),
不能靠真的等 10 分鐘。★ 這是 `the-instrument-writes-into-what-it-measures`
那族的相反面:要量得到,就別把時間寫死在函式裡。
"""
from __future__ import annotations

import time as _time
import re
from datetime import datetime, timezone, timedelta

import store
from api import ApiError

POLL_SECONDS = 3           # 規格 §3.3 建議值
BOUND = "bound"
PENDING = "pending"
SERIAL_RE = re.compile(r"^EZP-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
SERIAL_SCOPES = {"draft:read", "draft:write", "assets:write"}


class BindError(Exception):
    """配對沒完成。訊息是給人看的一句話,不是錯誤碼。"""


def _parse_iso(text):
    """規格 §2.1:時間全是 ISO 8601 UTC。解不出來就說解不出來,不要當成「沒有逾時」。"""
    if not text:
        raise BindError("伺服器沒有給配對碼的到期時刻(expires_at),不知道要問到什麼時候就停手")
    value = str(text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BindError("配對碼到期時刻格式看不懂:%r" % (text,))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now(clock=None):
    return (clock or (lambda: datetime.now(timezone.utc)))()


# 舊伺服器對 /bind/start 多出來的 machine 鍵回這個碼(exact 檢查那支);只有它才退回不帶 machine(10-21 @9f808)
OLD_SERVER_NO_MACHINE = "page_api_seat_label_invalid"


DEFAULT_CONFIRM_URL = "https://ez2.page/manage/aib"


def _confirm_url(value):
    """伺服器回的 confirm_url 只收 https、不含空白控制字元;不合格就退回 ez2.page(10-21 @abb89)。"""
    if (isinstance(value, str) and value.startswith("https://") and len(value) <= 512
            and not any(c.isspace() or ord(c) < 32 or c in "<>\"'" for c in value)):
        return value
    return DEFAULT_CONFIRM_URL


def run_bind(api, seat_label, out, environ=None, sleep=None, clock=None,
             poll_seconds=POLL_SECONDS):
    """跑完整個配對流程。回 (憑證記錄, 落檔路徑)。

    `out` 是一個吃字串的函式(印給人看的那幾行)。
    """
    sleep = sleep or _time.sleep
    machine = store.machine_identity(environ)
    try:
        try:
            started = api.bind_start(seat_label, machine)
        except ApiError as exc:
            if exc.code != OLD_SERVER_NO_MACHINE:
                raise   # 其他 400(例如 page_api_body_invalid details [machine] = machine.json 本身不合格)照報，不靜靜綁成沒機器的
            started, machine = api.bind_start(seat_label), None   # 舊伺服器還不收 machine ⇒ 照舊綁(手機遠端要等伺服器更新後重綁)
    except ApiError as exc:
        raise BindError(_start_error_text(exc))

    pair_code = started.get("pair_code") or ""
    ticket = started.get("bind_ticket") or ""
    confirm_url = _confirm_url(started.get("confirm_url"))
    if not pair_code or not ticket:
        raise BindError("伺服器沒有給配對碼或 bind_ticket,沒辦法繼續")
    deadline = _parse_iso(started.get("expires_at"))

    out("到 %s 輸入配對碼 %s(10 分鐘內有效)" % (confirm_url, pair_code))
    digits = started.get("confirm_digits")   # 10-21 @abb89 數字配對(@9f808):手機確認頁要本人親手打;只印在這個畫面,不寫檔、不進 log
    if isinstance(digits, str) and re.fullmatch(r"[0-9]{2}", digits):
        out("手機上還要輸入確認數字 %s(只顯示在這台,不要傳給別人)" % digits)
    out("(這個視窗會每 %d 秒問一次,你按完確認就會自己往下走)" % poll_seconds)

    while True:
        try:
            polled = api.bind_poll(ticket)
        except ApiError as exc:
            raise BindError(_poll_error_text(exc))
        status = polled.get("status")
        if status == BOUND:
            if machine and polled.get("machine_id") not in (None, machine["id"]):
                raise BindError("伺服器發的憑證綁的不是這台機器,不存檔;"
                                "請到 %s 把剛綁的那把解除(不然它會佔一個名額到過期)" % confirm_url)
            record = dict(polled)
            record["bound_at"] = _now(clock).isoformat()
            record["seat_label"] = seat_label
            path, _source = store.save_token(record, environ)
            out("已綁 %s,到期 %s"
                % (record.get("slug") or record.get("page_id") or "(未具名頁面)",
                   _human_date(record.get("expires_at"))))
            return record, path
        if status != PENDING:
            raise BindError("伺服器回了看不懂的狀態 %r,停手不猜" % (status,))
        # ★ 先比時間再睡:睡完才發現早就過期會多等一個週期,而那個週期裡
        #   使用者看到的是「還在等」——一個已經死掉的等待。
        if _now(clock) >= deadline:
            raise BindError(
                "配對碼已經過期(10 分鐘),沒有綁成功。重新跑一次 `aib page bind` 會給你新的碼")
        sleep(poll_seconds)


def _human_date(value):
    if not value:
        return "(未給到期日)"
    text = str(value)
    return text.split("T")[0] if "T" in text else text


def run_serial_bind(api, serial, seat_label, out, environ=None, clock=None):
    serial = str(serial).strip().upper()
    if not SERIAL_RE.fullmatch(serial):
        raise BindError("序號格式不對,請使用 EZP-XXXX-XXXX-XXXX")
    if not 1 <= len(seat_label) <= 40 or any(c in seat_label for c in "<>"):
        raise BindError("席位名稱不合規(1-40 字、不能有 HTML)")
    machine = store.machine_identity(environ)
    try:
        answer = api.bind_serial(serial, seat_label, machine)
    except ApiError as exc:
        messages = {
            "page_api_serial_invalid": "序號不對,到 ez2.page 後台「AI 助理」看序號或重發",
            "page_api_serial_stale": "序號已重發過,舊的作廢;用後台新序號",
            "page_api_serial_rotated": _serial_rotated_text(),
            "page_api_token_limit_reached": "這個頁面已綁 3 台(上限),到 ez2.page 後台解綁一台再來",
        }
        raise BindError(messages.get(exc.code) or _start_error_text(exc))
    # Explicit allowlist prevents an echoed serial or arbitrary response fields
    # from becoming local credentials.
    token = answer.get("assistant_token")
    scopes = answer.get("scope")
    generation = answer.get("serial_generation")
    if (not isinstance(token, str) or not re.fullmatch(r"ezp_[A-Za-z0-9_-]{43}", token)
            or not isinstance(scopes, list) or len(scopes) != 3 or set(scopes) != SERIAL_SCOPES
            or answer.get("machine_id") != machine["id"]
            or type(generation) is not int or generation < 1):
        raise BindError("序號換發的助理憑證格式或權限不符,不存檔")
    expires = _parse_iso(answer.get("expires_at"))
    now = _now(clock)
    if not now < expires <= now + timedelta(days=30, minutes=1):
        raise BindError("助理憑證到期時刻不在 30 天有效範圍,不存檔")
    record = {key: answer.get(key) for key in (
        "assistant_token", "token_id", "tenant_id", "page_id", "slug", "scope",
        "expires_at", "machine_id", "serial_generation", "replaced_token_id")}
    record.update(status=BOUND, bound_at=now.isoformat(), seat_label=seat_label)
    saved, _source = store.save_token(record, environ)
    out("已綁 %s,到期 %s" % (record.get("slug") or record["page_id"], _human_date(record["expires_at"])))
    return record, saved


def _serial_rotated_text():
    """遺失裝置撤銷時伺服器換了序號(0.1.42,Andy 10-23);四語,字典讀不到用中文。"""
    try:
        import i18n_lib
        text = i18n_lib.t("aib.page.bind.serial_rotated", None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if text and not text.startswith("!!") else "這個序號已在遺失裝置撤銷時換新,請登入 ez2.page 後台取新序號"


def _start_error_text(exc):
    code = exc.code
    if code == "page_api_token_limit_reached":
        return "這個頁面已經綁了 3 個 AI 助理(上限),先到 ez2.page 後台的「AI 助理」頁解除一個再來"
    if code == "page_api_seat_label_invalid":
        return "席位名稱不合規(1–40 字、不能有 HTML),換一個 --seat-label"
    if code == "page_api_rate_limited":
        return "配對開太多次被限流了,等一分鐘再試"
    if code == "page_api_bind_busy":
        return "PAGE 端現在忙(等待配對的人太多),等一下再試"
    return "開始配對失敗:%s" % (exc.code or exc)


def _poll_error_text(exc):
    if exc.code == "page_api_bind_expired":
        return ("配對碼已經過期、被取走了，或確認數字錯了 3 次。如果不是你取的,先到 ez2.page 後台看「AI 助理」頁;"
                "否則重跑 `aib page bind`")
    if exc.code == "page_api_bind_ticket_unknown":
        return "這張配對憑單伺服器不認得(可能是 PAGE 重啟過),重跑 `aib page bind`"
    return "等待確認時失敗:%s" % (exc.code or exc)
