#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read and write the fleet-wide development mode switch.

Path precedence follows the existing cockpit state chain exactly:

    EZ2_AIB_DEV_MODE -> EZ2_AIB_STATE_ROOT -> EZ2_AIB_HOME -> ~/.ez2-aib

Unlike display preferences, an absent or unreadable development-mode file
falls back to the strict ``safe`` mode and always reports the reason on stderr.
"""

import argparse
import datetime
import json
import os
import pathlib
import sys
import tempfile


SCHEMA = "aib-dev-mode/v1"
FILENAME = "dev-mode.json"
OVERRIDE_ENV = "EZ2_AIB_DEV_MODE"
VALID_MODES = ("speed", "balanced", "safe", "repair")
DEFAULT_MODE = "safe"
OK = "ok"
UNSET = "dev_mode_unset"
UNREADABLE = "dev_mode_unreadable"


def config_path(env=None):
    """Return the absolute switch path; ``env`` is injectable for fixtures."""
    env = os.environ if env is None else env
    explicit = (env.get(OVERRIDE_ENV) or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    root = (env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME")
            or "~/.ez2-aib")
    return os.path.join(os.path.abspath(os.path.expanduser(root)), FILENAME)


def _utc_iso8601(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == datetime.timedelta(0)


def _validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("root is not an object")
    if raw.get("schema") != SCHEMA:
        raise ValueError("schema is not %s" % SCHEMA)
    mode = raw.get("dev_mode")
    if mode not in VALID_MODES:
        raise ValueError("dev_mode is outside the closed vocabulary")
    previous = raw.get("previous")
    if previous is not None and previous not in VALID_MODES:
        raise ValueError("previous is outside the closed vocabulary or null")
    if not isinstance(raw.get("set_by"), str):
        raise ValueError("set_by is not a string")
    if not _utc_iso8601(raw.get("set_at")):
        raise ValueError("set_at is not ISO-8601 UTC")
    if not isinstance(raw.get("reason"), str):
        raise ValueError("reason is not a string")
    return mode


def _warn(err, code, path, reason):
    reason = str(reason).replace("\n", " ")
    err.write("dev-mode: %s path=%s reason=%s; using dev-mode:safe\n"
              % (code, path, reason))


def read(path=None, env=None, err=None):
    """Return ``(value, code)`` and name every non-ok result on stderr."""
    err = sys.stderr if err is None else err
    path = config_path(env) if path is None else os.path.abspath(os.path.expanduser(path))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        _warn(err, UNSET, path, "file does not exist")
        return DEFAULT_MODE, UNSET
    except (OSError, ValueError) as exc:
        _warn(err, UNREADABLE, path, exc)
        return DEFAULT_MODE, UNREADABLE
    try:
        return _validate(raw), OK
    except ValueError as exc:
        _warn(err, UNREADABLE, path, exc)
        return DEFAULT_MODE, UNREADABLE


def set(value, set_by, reason, path=None, env=None):
    """Persist a manual selection atomically and return its absolute path."""
    if value not in VALID_MODES:
        raise ValueError("dev_mode must be one of: %s" % " ".join(VALID_MODES))
    if not isinstance(set_by, str):
        raise TypeError("set_by must be a string")
    if not isinstance(reason, str):
        raise TypeError("reason must be a string")
    path = config_path(env) if path is None else os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "schema": SCHEMA,
        "dev_mode": value,
        # SPEC-MODE-dev-mode-switch-1107e-0906.md §九.1:
        # 人手 set X 時 previous 寫成 X 本身(=最後一次人手設定),日後自動切換讀它回去;
        # 自動切換永不寫 previous。
        "previous": value,
        # set_by 是自報欄,不是身分證明
        "set_by": set_by,
        "set_at": now,
        "reason": reason,
    }
    fd, temporary = tempfile.mkstemp(prefix=FILENAME + ".", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path


def _parser():
    parser = argparse.ArgumentParser(prog="dev_mode.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("get")
    setter = commands.add_parser("set")
    setter.add_argument("value", choices=VALID_MODES)
    setter.add_argument("--by", required=True, dest="set_by")
    setter.add_argument("--reason", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "get":
        value, code = read()
    else:
        set(args.value, args.set_by, args.reason)
        value, code = args.value, OK
    print("dev-mode:%s code=%s" % (value, code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
