#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Installed and running versions; remote probes require explicit SSH aliases."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_lib
import version_probe


def T(key):
    return i18n_lib.t("aib.update_status." + key.replace("-", "_"), None, locale=i18n_lib.resolve_locale())


def valid_host(value):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", value))


def validate_probe(data):
    if not isinstance(data, dict) or set(data) != {"schema", "observed_at", "core", "plugins", "room"}:
        raise ValueError("invalid-probe")
    if data["schema"] != "aib.version-probe/v1" or type(data["observed_at"]) is not int or data["observed_at"] < 0:
        raise ValueError("invalid-probe")
    core = data["core"]
    if not isinstance(core, dict) or set(core) != {"installed_version", "source", "git_sha"}:
        raise ValueError("invalid-core")
    if core["installed_version"] is not None and version_probe.version(core["installed_version"]) is None:
        raise ValueError("invalid-version")
    if core["source"] not in ("unmeasured", "installed-version-file", "installed-cli"):
        raise ValueError("invalid-source")
    if core["git_sha"] is not None and not re.fullmatch(r"[0-9a-f]{40}", str(core["git_sha"])):
        raise ValueError("invalid-sha")
    plugins = data["plugins"]
    if not isinstance(plugins, dict) or len(plugins) > 200:
        raise ValueError("invalid-plugins")
    for name, row in plugins.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name) or not isinstance(row, dict):
            raise ValueError("invalid-plugin")
        if set(row) != {"installed_version", "source"} or row["source"] != "installed-manifest":
            raise ValueError("invalid-plugin")
        if row["installed_version"] is not None and version_probe.version(row["installed_version"]) is None:
            raise ValueError("invalid-plugin-version")
    room = data["room"]
    if not isinstance(room, dict) or set(room) != {"state", "reported_start_version", "source", "installed_version",
                                                "versions_match", "health", "runtime_core_version"}:
        raise ValueError("invalid-room")
    if room["state"] not in ("not-recorded", "invalid-record", "stopped-or-unreadable", "stale-record", "process-verified"):
        raise ValueError("invalid-room-state")
    if room["source"] not in ("unmeasured", "pid-verified-startup-record") or room["health"] != "unmeasured":
        raise ValueError("invalid-room-source")
    if room["runtime_core_version"] is not None or not (room["versions_match"] is None or type(room["versions_match"]) is bool):
        raise ValueError("unverified-runtime")
    for key in ("reported_start_version", "installed_version"):
        if room[key] is not None and version_probe.version(room[key]) is None:
            raise ValueError("invalid-room-version")
    if room["state"] != "process-verified" and (room["reported_start_version"] is not None or room["versions_match"] is not None):
        raise ValueError("unverified-room-version")
    if room["installed_version"] != plugins.get("captain-room", {}).get("installed_version"):
        raise ValueError("inconsistent-room-installation")
    if room["state"] == "process-verified":
        a, b = room["reported_start_version"], room["installed_version"]
        expected = a == b if a and b else None
        if room["source"] != "pid-verified-startup-record" or room["versions_match"] is not expected:
            raise ValueError("inconsistent-room-evidence")
    elif room["source"] != "unmeasured":
        raise ValueError("unverified-room-source")
    return data


def remote(host):
    if not valid_host(host):
        raise ValueError("invalid-ssh-alias")
    source = Path(version_probe.__file__).read_text(encoding="utf-8")
    try:
        p = subprocess.run(["ssh", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
                            "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                            "-o", "StrictHostKeyChecking=yes", "-o", "RemoteCommand=none",
                            host, "python3", "-"], input=source, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=30)
        if p.returncode != 0:
            return dict(target=host, state="unavailable", why="ssh-probe-failed")
        data = validate_probe(json.loads(p.stdout))
        # Only the probe's closed output is accepted; never include SSH output,
        # environment variables, server tokens or remote paths in the report.
        return dict(target=host, state="observed", **data)
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(target=host, state="unavailable", why="ssh-probe-unavailable")


def collect(hosts=()):
    rows = [dict(target="local", state="observed", **version_probe.collect())]
    rows.extend(remote(host) for host in dict.fromkeys(hosts))
    return dict(schema="aib.update-status/v1", targets=rows,
                all_core_versions_observed=all(row["state"] == "observed" and row["core"]["installed_version"] for row in rows))


def render(result, out):
    print(T("columns"), file=out)
    for row in result["targets"]:
        if row["state"] != "observed":
            print("%s | %s" % (row["target"], T("unavailable")), file=out)
            continue
        room = row["room"]
        print("%s | %s | %s | %s | %s" % (
            row["target"], row["core"]["installed_version"] or T("unmeasured"),
            room["installed_version"] or T("unmeasured"),
            room["reported_start_version"] or T("unmeasured"), T(room["state"])), file=out)
    print(T("boundary"), file=out)


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="aib update status", description=T("description"))
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if any(not valid_host(host) for host in args.host):
        parser.error("invalid SSH alias")
    if len(args.host) > 10:
        parser.error("at most 10 SSH aliases")
    result = collect(args.host)
    if args.json:
        print(json.dumps(result, ensure_ascii=False), file=out)
    else:
        render(result, out)
    return 0 if result["all_core_versions_observed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
