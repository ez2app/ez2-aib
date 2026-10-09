#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Platform and cockpit side-effect boundaries for AIB notifications.

Classification and switches live in notify_policy.  This module is the only
place allowed to invoke a desktop notification backend or temporarily alter
the four cockpit pane styles.  Callers may inject every runner and adapter;
tests never contact the real desktop or a live tmux server.
"""
import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from notify_policy import (
    RULES_PATH, RulesError, StateError, StateStore, append_audit, classify,
    load_rules, plan_actions,
)


def _host_release():
    try:
        return Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return ""


def detect_backend(platform_name=None, os_release=None, which=None):
    """Return macos, powershell or unsupported from injectable host facts."""
    platform_name = (platform_name or sys.platform).lower()
    os_release = _host_release() if os_release is None else str(os_release)
    which = which or shutil.which

    if platform_name.startswith("darwin"):
        return "macos" if which("osascript") else "unsupported"

    windows = (platform_name.startswith("win") or
               platform_name.startswith("cygwin") or
               platform_name.startswith("msys"))
    wsl = (platform_name.startswith("linux") and
           ("microsoft" in os_release.lower() or
            bool(os.environ.get("WSL_DISTRO_NAME"))))
    if windows or wsl:
        for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
            if which(name):
                return "powershell"
    return "unsupported"


class MacOSAdapter:
    name = "macos"

    _SCRIPT = """on run argv
set bodyText to item 1 of argv
set titleText to item 2 of argv
set soundText to item 3 of argv
if soundText is "" then
  display notification bodyText with title titleText
else
  display notification bodyText with title titleText sound name soundText
end if
end run"""

    def __init__(self, binary="osascript", runner=None):
        self.binary = binary
        self.runner = runner or subprocess.run

    def send(self, payload):
        argv = [self.binary, "-e", self._SCRIPT, "--",
                str(payload.get("message") or ""),
                str(payload.get("title") or "Ez2 AIB"),
                str(payload.get("sound") or "")]
        proc = self.runner(argv, capture_output=True, text=True, check=False)
        return {
            "backend": self.name,
            "exit_status": int(proc.returncode),
            "stderr": (proc.stderr or "").strip(),
        }


class PowerShellAdapter:
    name = "powershell"

    # Message content never enters this program string.  It travels through
    # the child environment, so quotes, newlines and command syntax are data.
    _SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$title = [System.Security.SecurityElement]::Escape($env:EZ2_NOTIFY_TITLE)
$message = [System.Security.SecurityElement]::Escape($env:EZ2_NOTIFY_MESSAGE)
$sound = switch ($env:EZ2_NOTIFY_SOUND) {
  'Basso' { 'ms-winsoundevent:Notification.Looping.Alarm2' }
  'Tink' { 'ms-winsoundevent:Notification.Default' }
  default { $null }
}
$audio = if ($sound) { "<audio src='" + $sound + "'/>" } else { "" }
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>" + $title + "</text><text>" + $message + "</text></binding></visual>" + $audio + "</toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Ez2 AIB').Show($toast)
""".strip()

    def __init__(self, binary="powershell.exe", runner=None):
        self.binary = binary
        self.runner = runner or subprocess.run

    def send(self, payload):
        env = dict(os.environ)
        env.update({
            "EZ2_NOTIFY_TITLE": str(payload.get("title") or "Ez2 AIB"),
            "EZ2_NOTIFY_MESSAGE": str(payload.get("message") or ""),
            "EZ2_NOTIFY_SOUND": str(payload.get("sound") or ""),
        })
        argv = [self.binary, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", self._SCRIPT]
        proc = self.runner(argv, env=env, capture_output=True, text=True, check=False)
        return {
            "backend": self.name,
            "exit_status": int(proc.returncode),
            "stderr": (proc.stderr or "").strip(),
        }


class UnsupportedAdapter:
    name = "unsupported"

    def send(self, payload):
        return {"backend": self.name, "exit_status": None,
                "stderr": "desktop notification backend unavailable"}


def make_adapter(backend=None, which=None, runner=None):
    """Build an adapter without ever guessing a vendor-specific executable."""
    which = which or shutil.which
    backend = backend or detect_backend(which=which)
    if backend == "macos":
        binary = which("osascript")
        return MacOSAdapter(binary=binary, runner=runner) if binary else UnsupportedAdapter()
    if backend == "powershell":
        for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
            binary = which(name)
            if binary:
                return PowerShellAdapter(binary=binary, runner=runner)
    return UnsupportedAdapter()


_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PANE_RE = re.compile(r"%[0-9]+\Z")
_STYLE_RE = re.compile(
    r"fg=colour([0-9]{1,3}),bg=colour([0-9]{1,3})"
    r"(?:,(?:bold|dim|italics|underscore))*\Z")
_COCKPIT_ROLES = ("left", "hub", "right", "bottom")


class TmuxFlashAdapter:
    """Apply one generation-guarded flash to the four-region cockpit.

    Restore values come from the theme contract already recorded on each pane,
    never from the currently flashing style.  A later notification replaces
    the window token, so an older delayed restore becomes a no-op instead of
    erasing the newer flash.
    """

    name = "tmux"

    def __init__(self, session, binary="tmux", runner=None, token_factory=None,
                 python=None, script=None):
        if not isinstance(session, str) or not _SESSION_RE.fullmatch(session):
            raise ValueError("invalid AIB tmux session name")
        self.session = session
        self.target = session + ":cockpit"
        self.binary = binary
        self.runner = runner or subprocess.run
        self.token_factory = token_factory or (lambda: secrets.token_hex(16))
        self.python = python or sys.executable
        self.script = str(script or Path(__file__).resolve())

    def _run(self, argv):
        return self.runner(argv, capture_output=True, text=True, check=False)

    def _window_option(self, name):
        proc = self._run([self.binary, "show-options", "-w", "-qv", "-t",
                          self.target, name])
        if proc.returncode != 0:
            raise OSError("tmux option read failed: %s" %
                          ((proc.stderr or "").strip() or name))
        return (proc.stdout or "").strip()

    @staticmethod
    def _valid_style(value):
        match = _STYLE_RE.fullmatch(value or "")
        return bool(match and int(match.group(1)) <= 255
                    and int(match.group(2)) <= 255)

    def _theme_rows(self):
        if self._window_option("@aib_cockpit") != "1":
            raise OSError("tmux target is not an AIB cockpit")
        fmt = ("#{pane_id}|#{@aib_role}|#{@aib_theme_normal_style}|"
               "#{@aib_theme_focused_style}")
        proc = self._run([self.binary, "list-panes", "-t", self.target,
                          "-F", fmt])
        if proc.returncode != 0:
            raise OSError("tmux pane inventory failed: %s" %
                          (proc.stderr or "").strip())
        rows = []
        for raw in (proc.stdout or "").splitlines():
            parts = raw.split("|")
            if len(parts) != 4:
                raise OSError("invalid AIB cockpit pane contract")
            pane, role, normal, focused = parts
            if (not _PANE_RE.fullmatch(pane) or role not in _COCKPIT_ROLES
                    or not self._valid_style(normal)
                    or not self._valid_style(focused)):
                raise OSError("invalid AIB cockpit pane contract")
            rows.append((pane, role, normal, focused))
        if (len(rows) != 4 or sorted(row[1] for row in rows)
                != sorted(_COCKPIT_ROLES)):
            raise OSError("cockpit must contain one pane for each of four regions")
        return rows

    def _set_pane_styles(self, rows, flash_style=None):
        for pane, _role, normal, focused in rows:
            values = (("window-style", flash_style or normal),
                      ("window-active-style", flash_style or focused))
            for option, value in values:
                proc = self._run([self.binary, "set-option", "-p", "-t", pane,
                                  option, value])
                if proc.returncode != 0:
                    raise OSError("tmux pane style update failed: %s" %
                                  (proc.stderr or "").strip())

    def _set_window_option(self, name, value):
        proc = self._run([self.binary, "set-option", "-w", "-t", self.target,
                          name, value])
        if proc.returncode != 0:
            raise OSError("tmux notification marker update failed: %s" %
                          (proc.stderr or "").strip())

    def send(self, payload):
        level = payload.get("level")
        duration = payload.get("duration_ms")
        background = payload.get("flash_background")
        foreground = payload.get("flash_foreground")
        if level not in {"red", "yellow", "green"}:
            raise ValueError("cockpit flash level must be red, yellow or green")
        if (isinstance(duration, bool) or not isinstance(duration, int)
                or duration <= 0 or duration > 60000):
            raise ValueError("cockpit flash duration is invalid")
        for value in (background, foreground):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0 or value > 255):
                raise ValueError("cockpit flash colour is invalid")
        token = self.token_factory()
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ValueError("cockpit flash token is invalid")

        rows = self._theme_rows()
        style = "fg=colour%d,bg=colour%d,bold" % (foreground, background)
        self._set_window_option("@aib_notify_token", token)
        self._set_window_option("@aib_notify_level", level)
        try:
            self._set_pane_styles(rows, flash_style=style)
            delay = "%.3f" % (duration / 1000.0)
            restore = "%s %s restore-flash --session %s --token %s" % (
                shlex.quote(self.python), shlex.quote(self.script),
                shlex.quote(self.session), shlex.quote(token))
            command = "sleep %s; %s" % (delay, restore)
            proc = self._run([self.binary, "run-shell", "-b", "-t",
                              self.target, command])
            if proc.returncode != 0:
                raise OSError("tmux flash restore scheduling failed: %s" %
                              (proc.stderr or "").strip())
        except Exception:
            # A scheduling or style failure must not strand the cockpit in a
            # half-flashed state.  This restore is synchronous and uses the
            # already validated theme contract.
            self._set_pane_styles(rows)
            self._set_window_option("@aib_notify_token", "")
            self._set_window_option("@aib_notify_level", "")
            raise
        return {"backend": self.name, "exit_status": 0, "token": token,
                "duration_ms": duration}

    def restore(self, token):
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ValueError("cockpit flash restore token is invalid")
        if self._window_option("@aib_notify_token") != token:
            return {"backend": self.name, "exit_status": 0,
                    "status": "superseded"}
        rows = self._theme_rows()
        self._set_pane_styles(rows)
        self._set_window_option("@aib_notify_token", "")
        self._set_window_option("@aib_notify_level", "")
        return {"backend": self.name, "exit_status": 0,
                "status": "restored"}


def _message_text(record):
    if record.get("text") is not None:
        return str(record["text"])
    summary = (record.get("body") or {}).get("summary") or {}
    if isinstance(summary, dict):
        return str(summary.get("zh-TW") or summary.get("en") or "")
    return str(summary or "")


def dispatch(record, state, adapter, flash_adapter=None, rules=None,
             decision=None):
    """Classify, gate and optionally call one injected desktop adapter.

    Backend failures are structured receipts and never crash the sentinel or
    projector.  Blue and green cannot reach ``adapter.send`` by construction.
    """
    decision = decision or classify(record, rules=rules)
    plan = plan_actions(decision, state, rules=rules)
    receipt = {
        "level": decision["level"],
        "reason_codes": decision["reason_codes"],
        "plan": plan,
        "delivered": False,
        "backend_status": "suppressed" if not plan["system_notification"] else "pending",
        "backend": getattr(adapter, "name", "unknown"),
        "exit_status": None,
        "flash_status": "suppressed" if not plan["flash"] else "pending",
        "flash_backend": (getattr(flash_adapter, "name", "unavailable")
                          if flash_adapter else "unavailable"),
        "flash_exit_status": None,
    }
    if plan["system_notification"]:
        payload = {
            "title": "Ez2 AIB - %s" % decision["level"].upper(),
            "message": _message_text(record),
            "sound": plan["sound"],
            "level": decision["level"],
        }
        try:
            backend_receipt = adapter.send(payload)
            status = backend_receipt.get("exit_status")
            receipt.update({
                "backend": backend_receipt.get("backend", receipt["backend"]),
                "exit_status": status,
                "backend_status": ("delivered" if status == 0 else "failed"),
                "delivered": status == 0,
            })
            if backend_receipt.get("stderr"):
                receipt["error"] = backend_receipt["stderr"]
        except Exception as err:  # feedback must not stop the control plane
            receipt["backend_status"] = "failed"
            receipt["error"] = str(err)

    if plan["flash"]:
        if flash_adapter is None:
            receipt["flash_status"] = "unavailable"
        else:
            flash_payload = {
                "level": decision["level"],
                "duration_ms": plan["duration_ms"],
                "flash_background": plan["flash_background"],
                "flash_foreground": plan["flash_foreground"],
            }
            try:
                flash_receipt = flash_adapter.send(flash_payload)
                status = flash_receipt.get("exit_status")
                receipt.update({
                    "flash_backend": flash_receipt.get(
                        "backend", receipt["flash_backend"]),
                    "flash_exit_status": status,
                    "flash_status": "delivered" if status == 0 else "failed",
                })
                if flash_receipt.get("token"):
                    receipt["flash_token"] = flash_receipt["token"]
                if flash_receipt.get("stderr"):
                    receipt["flash_error"] = flash_receipt["stderr"]
            except Exception as err:
                receipt["flash_status"] = "failed"
                receipt["flash_error"] = str(err)
    return receipt


class NotificationRuntime:
    """State, audit and both feedback adapters for one product invocation."""

    def __init__(self, state_path, audit_path, rules_path=RULES_PATH,
                 adapter=None, flash_adapter=None):
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path)
        self.rules_path = Path(rules_path)
        self.rules = load_rules(self.rules_path)
        self.adapter = adapter or make_adapter()
        self.flash_adapter = flash_adapter

    def handle(self, record, decision=None):
        decision = decision or classify(record, rules=self.rules)
        audit_recorded = False
        if decision.get("audit_required"):
            # Escalation accounting is part of the anti-wolf contract.  If
            # its owner-only append fails, fail closed instead of delivering
            # an unaccountable red/yellow AI escalation.
            append_audit(self.audit_path, record, decision)
            audit_recorded = True
        state = StateStore(self.state_path).show()
        receipt = dispatch(record, state, self.adapter,
                           flash_adapter=self.flash_adapter, rules=self.rules,
                           decision=decision)
        receipt["audit_recorded"] = audit_recorded
        return receipt


def _main(argv):
    parser = argparse.ArgumentParser(description="AIB notification side-effect adapter")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("dispatch")
    run.add_argument("--state")
    run.add_argument("--audit")
    run.add_argument("--rules", default=str(RULES_PATH))
    run.add_argument("--session", default=os.environ.get("EZ2_TMUX_SESSION"))
    run.add_argument("--backend", choices=("macos", "powershell", "unsupported"))
    restore = sub.add_parser("restore-flash")
    restore.add_argument("--session", required=True)
    restore.add_argument("--token", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "restore-flash":
            receipt = TmuxFlashAdapter(args.session).restore(args.token)
        else:
            state_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR")
            state = args.state or (str(Path(state_dir) / "notify-state.json")
                                   if state_dir else None)
            audit = args.audit or (str(Path(state_dir) / "notify-audit.jsonl")
                                   if state_dir else None)
            if not state or not audit:
                raise StateError("dispatch requires --state/--audit or EZ2_COCKPIT_STATE_DIR")
            record = json.load(sys.stdin)
            desktop = make_adapter(args.backend)
            flash = TmuxFlashAdapter(args.session) if args.session else None
            runtime = NotificationRuntime(state, audit, args.rules,
                                          adapter=desktop,
                                          flash_adapter=flash)
            receipt = runtime.handle(record)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")))
    except (RulesError, StateError, OSError, ValueError) as err:
        print(str(err), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
