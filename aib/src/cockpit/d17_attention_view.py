# SPDX-License-Identifier: Apache-2.0
"""D17-C2b read-only attention view.

Reads C1 once and projects a closed display.  create_intent emits one C3
request.  Delivery, ack, click, and AI text are never authority.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from identity.persona_candidates import AUTO_BIND  # noqa: E402
from fleet_identity.bind_attention_outbox import (  # noqa: E402
    REQUEST_SCHEMA as C1_REQUEST_SCHEMA,
)

UI_REQUEST_SCHEMA = "ez2-aib/d17-attention-ui-request/v1"
UI_RESULT_SCHEMA = "ez2-aib/d17-attention-ui-result/v1"
UI_VIEW_SCHEMA = "ez2-aib/d17-attention-view/v1"
C3_REQUEST_SCHEMA = "ez2-aib/d17-human-intent-request/v1"

_REQUEST_KEYS = frozenset({"schema", "operation", "reminder_id", "action"})
_ACTIONS = ("bind_once", "enable_scoped_auto", "snooze", "mute")
_MODES = frozenset({"remind_only", "confirm_each", "scoped_auto"})
_C1_DATA_KEYS = frozenset({
    "reminder_id", "source_key_sha256", "logical_key_sha256", "state",
    "selected_channel", "attempt_generation", "lease_generation",
    "lease_until_ms", "route_generation", "mode_generation",
    "notification_generation", "created_ms", "updated_ms",
    "trigger_operation", "trigger_reason", "source_kind", "display_scope",
    "mode",
})
_SCOPE_KEYS = frozenset({
    "cabin_id", "fleet_id", "fleet_generation", "project_id",
    "project_generation", "seat_id", "session_uuid", "provider", "role",
    "member_lease_generation",
})
_TRIGGER_REASONS = frozenset({
    "content_explicit", "content_domain_relevance",
    "content_quoted_or_uncertain", "dedup_suppressed",
    "reminder_emitted",
})
_C1_STATES = frozenset({
    "ready", "leased", "transport_delivered", "recipient_acknowledged",
    "retryable_zero_delivery", "delivery_unknown", "suppressed", "snoozed",
    "expired", "cancelled",
})
_LABEL_BAD = re.compile(r"[\n\r\x1b\u202e<>]")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_HEX64 = set("0123456789abcdef")


def _exact_int(value, minimum=None):
    if type(value) is not int:
        return False
    if minimum is not None and value < minimum:
        return False
    return True


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


#: 巡查排程收據裡「這席明確沒綁」的理由字面(fleet_patrol._ctx_scheduler_tick 回的)。
UNBOUND_REASON = "ctx_scheduler_patrol_seat_unbound"


def patrol_attention_line(ctx_pct, ctx_source, threshold, scheduler_reason=None):
    """巡查列上的 10% 提醒字。只認機器值(畫面後備不算)。

    門檻是「剛用掉多少就提」,不是「剩多少才提」。**threshold 必填,由呼叫端從
    bind_attention.CTX_USED_THRESHOLD 讀來傳進**:本檔是畫面層,分層規則禁止它 import
    bind_attention(test-d17-work-ui-adapter-second-party 釘著);數字仍只住在那一處,
    這裡不另寫一個,也不給預設值 —— 預設值就是第二份數字。

    ★ 09-10 複驗補的:**只在這席明確未綁時提**(scheduler_reason == UNBOUND_REASON)。
      第一版沒看綁定狀態 ⇒ 已綁的席過 10% 也印「該綁人格」,對所有席恆真的提醒等於沒提醒。
      綁定狀態解不開(unresolved / holder_mismatch)也**不提** —— 那是「量不到」,不是「沒綁」;
      失效值不得等於「該綁」這個安全值。scheduler_reason=None 視為沒量到綁定狀態,同樣不提。
    """
    if scheduler_reason != UNBOUND_REASON:
        return None
    if ctx_source != "machine":
        return None
    if type(ctx_pct) is not int or ctx_pct < threshold:
        return None
    return "用量已過 %d%%,該綁人格" % threshold


def _plain(value):
    return isinstance(value, str) and bool(value) and _LABEL_BAD.search(value) is None


def _closed(reason, *, operation=""):
    return {
        "schema": UI_RESULT_SCHEMA,
        "ok": False,
        "rc": 2,
        "reason": reason,
        "operation": operation,
        "view": None,
        "intent": None,
        "effect_count": 0,
    }


def _ok(reason, operation, *, view=None, intent=None):
    return {
        "schema": UI_RESULT_SCHEMA,
        "ok": True,
        "rc": 0,
        "reason": reason,
        "operation": operation,
        "view": view,
        "intent": intent,
        "effect_count": 0,
    }


def _c1_data_ok(data):
    if not isinstance(data, dict) or set(data) != _C1_DATA_KEYS:
        return False
    if data.get("trigger_operation") != "evaluate_content_source":
        return False
    if data.get("trigger_reason") not in _TRIGGER_REASONS:
        return False
    if data.get("source_kind") != "work_item":
        return False
    if data.get("mode") not in _MODES:
        return False
    if data.get("state") not in _C1_STATES:
        return False
    for key in (
            "attempt_generation", "lease_generation", "route_generation",
            "mode_generation", "notification_generation", "created_ms",
            "updated_ms"):
        if not _exact_int(data.get(key), 0):
            return False
    until = data.get("lease_until_ms")
    if until is not None and not _exact_int(until, 0):
        return False
    if not _hex64(data.get("reminder_id")):
        return False
    scope = data.get("display_scope")
    if not isinstance(scope, dict) or set(scope) != _SCOPE_KEYS:
        return False
    if not _plain(scope.get("provider")) or not _plain(scope.get("role")):
        return False
    if not isinstance(scope.get("session_uuid"), str) \
            or _UUID.fullmatch(scope["session_uuid"]) is None:
        return False
    for key in ("cabin_id", "fleet_id", "project_id", "seat_id"):
        if not _plain(scope.get(key)):
            return False
    for key in (
            "fleet_generation", "project_generation",
            "member_lease_generation"):
        if not _exact_int(scope.get(key), 0):
            return False
    return True


def _c1_result_ok(value):
    if not isinstance(value, dict):
        return False
    if type(value.get("ok")) is not bool:
        return False
    if not _exact_int(value.get("rc")):
        return False
    if not _exact_int(value.get("effect_count")) or value["effect_count"] != 0:
        return False
    if not _c1_data_ok(value.get("data")):
        return False
    return True


def _project(data):
    ack_state = data["state"]
    return {
        "schema": UI_VIEW_SCHEMA,
        "reminder_id": data["reminder_id"],
        "closed_reason": data["trigger_reason"],
        "scope": copy.deepcopy(data["display_scope"]),
        "mode": {
            "value": data["mode"],
            "generation": data["mode_generation"],
            "notification_generation": data["notification_generation"],
            "snapshot": True,
        },
        "delivery": {
            "state": data["state"],
            "selected_channel": data["selected_channel"],
            "attempt_generation": data["attempt_generation"],
            "lease_generation": data["lease_generation"],
            "lease_until_ms": data["lease_until_ms"],
            "route_generation": data["route_generation"],
        },
        "acknowledgement": {
            "acknowledged": ack_state == "recipient_acknowledged",
            "state": ack_state,
        },
        "actions": list(_ACTIONS),
    }


def execute(request, *, state_root, now_ms, c1_read, c3_request):
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS \
            or request.get("schema") != UI_REQUEST_SCHEMA:
        operation = request.get("operation") if isinstance(request, dict) else ""
        if not isinstance(operation, str):
            operation = ""
        return _closed("ui_request_invalid", operation=operation)

    operation = request.get("operation")
    reminder_id = request.get("reminder_id")
    action = request.get("action")
    if operation == "view":
        if action is not None:
            return _closed("ui_request_invalid", operation=operation)
        fetched = c1_read(
            {
                "schema": C1_REQUEST_SCHEMA,
                "operation": "read_reminder",
                "reminder_id": reminder_id,
            },
            state_root=state_root,
            now_ms=now_ms,
        )
        if not _c1_result_ok(fetched):
            return _closed("c1_result_invalid", operation=operation)
        return _ok("attention_view", operation, view=_project(fetched["data"]))

    if operation == "create_intent":
        if action not in _ACTIONS:
            return _closed("ui_request_invalid", operation=operation)
        fetched = c1_read(
            {
                "schema": C1_REQUEST_SCHEMA,
                "operation": "read_reminder",
                "reminder_id": reminder_id,
            },
            state_root=state_root,
            now_ms=now_ms,
        )
        if not _c1_result_ok(fetched):
            return _closed("c1_result_invalid", operation=operation)
        emitted = c3_request(
            {
                "schema": C3_REQUEST_SCHEMA,
                "operation": "create_intent",
                "reminder_id": reminder_id,
                "action": action,
            },
            now_ms=now_ms,
        )
        if not isinstance(emitted, dict) \
                or not _exact_int(emitted.get("effect_count")) \
                or emitted.get("effect_count") != 0 \
                or "authority_granted" in emitted:
            return _closed("c3_result_invalid", operation=operation)
        intent_id = emitted.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            return _closed("c3_result_invalid", operation=operation)
        return _ok(
            "human_intent_created", operation,
            intent={"intent_id": intent_id, "action": action})

    return _closed("ui_request_invalid", operation=operation if isinstance(operation, str) else "")
