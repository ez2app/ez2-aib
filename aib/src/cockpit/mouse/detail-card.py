#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render one rowmap-backed AI detail card and safely close its popup."""

import argparse
import base64
import json
import os
import re
import select
import sys
import termios
import time
import tty
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from i18n_lib import I18nError, Translator  # noqa: E402


DETAIL_KEYS = ("kind", "label", "engine", "context", "status")
SGR_MOUSE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
# 10-07:右欄列表的 window 早已改成 tmux 視窗編號(@2),舊規則只收視窗名 ⇒ 右鍵選單從此沒有「詳情/開啟對話」。兩種都收,
#   仍只准英數與 - / 開頭的 @ ⇒ 帶引號或分號的值照樣在組 tmux 指令前擋下。
WINDOW_ID = re.compile(r"^(?:@[0-9]{1,9}|[a-z0-9][a-z0-9-]{0,127})$")
MOUSE_TRACKING_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_TRACKING_OFF = "\x1b[?1006l\x1b[?1000l"


def _clean(value, limit=96):
    text = str(value or "")
    text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in text)
    return " ".join(text.split())[:limit]


def decode_payload(encoded):
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not set(value).issubset(DETAIL_KEYS):
        return None
    detail = {key: _clean(value.get(key)) for key in DETAIL_KEYS}
    if not detail["label"]:
        return None
    return detail


def encode_payload(detail):
    raw = json.dumps(detail, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def menu_metadata(action_text, translator=None):
    try:
        action = json.loads(action_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(action, dict) or action.get("action") != "show-ai-menu":
        return None
    source = action.get("detail")
    if not isinstance(source, dict):
        return None
    detail = {key: _clean(source.get(key)) for key in DETAIL_KEYS}
    window = _clean(action.get("window"), 128)
    if not detail["label"] or not WINDOW_ID.fullmatch(window):
        return None
    translator = translator or Translator()
    title = translator.t("aib.context.ai_title", detail)
    # tmux expands #{} formats in menu titles. Doubling # preserves registry
    # labels as text instead of letting them become a format expression.
    title = title.replace("#", "##")[:220]
    return {"title": title, "payload": encode_payload(detail), "window": window}


def render_card(detail, translator=None):
    translator = translator or Translator()
    kind_key = "aib.context.kind.%s" % detail["kind"]
    kind = translator.t(kind_key)
    if kind.startswith("!!"):
        kind = translator.t("aib.context.kind.unknown")
    return "\n".join((
        translator.t("aib.context.card.title"),
        translator.t("aib.context.card.name", {"value": detail["label"]}),
        translator.t("aib.context.card.kind", {"value": kind}),
        translator.t("aib.context.card.engine",
                     {"value": detail["engine"] or "--"}),
        translator.t("aib.context.card.status",
                     {"value": detail["status"] or "--"}),
        translator.t("aib.context.card.context",
                     {"value": detail["context"] or "--"}),
        "",
        translator.t("aib.context.card.close_hint"),
    ))


def _sgr_down(data):
    """Return (button_code, end_offset) when input starts with SGR Down."""
    match = SGR_MOUSE.match(data)
    if match is None or match.group(4) != b"M":
        return None
    button = int(match.group(1))
    # SGR wheel events end in M but have no paired release event.
    if button & 64:
        return None
    return button, match.end()


def _has_matching_sgr_up(data, button, offset):
    for match in SGR_MOUSE.finditer(data, offset):
        if match.group(4) == b"m" and int(match.group(1)) == button:
            return True
    return False


def drain_fd(fd, first_timeout=None, quiet_timeout=0.075, total_timeout=0.5):
    """Consume one close input plus all bytes in its trailing mouse sequence.

    SGR mouse press and release are two escape sequences. Reading one byte, or
    even one sequence, leaves the rest to click the pane under a closing popup.
    An SGR Down waits for its matching Up; an ordinary key drains until quiet.
    Both trailing paths have a hard bound so noisy input cannot hold shutdown.
    """
    consumed = bytearray()
    try:
        ready, _, _ = select.select([fd], [], [], first_timeout)
    except (OSError, ValueError):
        return bytes(consumed)
    if not ready:
        return bytes(consumed)
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        return bytes(consumed)
    if not chunk:
        return bytes(consumed)
    consumed.extend(chunk)

    # Waiting for the user's first close input is intentionally unbounded in
    # the popup. The hard bound applies only to bytes trailing that input, so
    # the card stays readable but a noisy terminal cannot hold shutdown open.
    started = time.monotonic()
    down = _sgr_down(consumed)
    if down is not None:
        button, offset = down
        if _has_matching_sgr_up(consumed, button, offset):
            return bytes(consumed)
        # A press and release can be separated by much more than the normal
        # quiet interval. Once an SGR Down is recognised, wait for its matching
        # Up for the entire bounded tail budget instead of leaking a late Up
        # into the pane revealed when the popup closes.
        while True:
            remaining = total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                return bytes(consumed)
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                return bytes(consumed)
            if not ready:
                return bytes(consumed)
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return bytes(consumed)
            if not chunk:
                return bytes(consumed)
            consumed.extend(chunk)
            if _has_matching_sgr_up(consumed, button, offset):
                return bytes(consumed)

    # A normal key has no paired release. Drain only until the fd is quiet.
    while True:
        remaining = total_timeout - (time.monotonic() - started)
        if remaining <= 0:
            break
        timeout = max(0.0, min(quiet_timeout, remaining))
        try:
            ready, _, _ = select.select([fd], [], [], timeout)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        consumed.extend(chunk)
    return bytes(consumed)


def drain_terminal(stream, output=None):
    try:
        fd = stream.fileno()
    except (AttributeError, OSError):
        return b""
    if not os.isatty(fd):
        return b""
    try:
        previous = termios.tcgetattr(fd)
        tty.setraw(fd)
        tracking = False
        if output is not None:
            try:
                if os.isatty(output.fileno()):
                    output.write(MOUSE_TRACKING_ON)
                    output.flush()
                    tracking = True
            except (AttributeError, OSError):
                tracking = False
        try:
            return drain_fd(fd)
        finally:
            if tracking:
                try:
                    output.write(MOUSE_TRACKING_OFF)
                    output.flush()
                except (AttributeError, OSError):
                    pass
    except (OSError, termios.error):
        return b""
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        except (OSError, termios.error, UnboundLocalError):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload")
    source.add_argument("--action")
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        translator = Translator()
    except I18nError:
        return 0
    if args.action is not None:
        metadata = menu_metadata(args.action, translator)
        if metadata is not None:
            print(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))
        return 0
    detail = decode_payload(args.payload)
    if detail is None:
        return 0
    sys.stdout.write(render_card(detail, translator) + "\n")
    sys.stdout.flush()
    if not args.render_only:
        drain_terminal(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
