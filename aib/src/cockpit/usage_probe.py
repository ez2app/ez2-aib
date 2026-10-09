#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Provider/tier usage records and isolated active probes for the cockpit.

The renderer receives only ``rows_for_render`` output. File and CLI access stay
here, active probes are throttled, and a missing measurement is a named row --
never a fabricated zero.
"""

from __future__ import annotations

import argparse
import datetime
import errno
import json
import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path
import sys
import tempfile
import time

from model_label import display_model

SCHEMA = "ez2-usage-v1"
THROTTLE_S = 60
AGY_LIVE_MIN_S = 1800   # v0.70.0 #14:agy 單家真探針(EZ2_USAGE_PROBE_LIVE_ENGINES=agy)最多 30 分鐘一次;檔還新就只讀檔
# 2 s 同幀小窗 < 5 s 面板幀距 < 60 s 探針節流窗。
SAME_FRAME_S = 2.0
STALE_S = 60 * 60
OK_TTL_S = 30 * 60
FAIL_RETRY_S = 60 * 60
DAILY_PROBE_CAP = 24
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
SUPPORTED = ("claude", "codex", "agy", "copilot", "grok")
WINDOWS = ("5h", "7d")
PROBE_ENGINES = ("agy", "copilot", "grok")
_CODEX_WEEK_MIN = 10080
_CODEX_FIVE_H_MIN = 300
#: the maintainer 09-06 12:1x 裁:右下角 Claude 只留三列 = Current session(5h)/ All models(週共用)/ Fable(週)。
#: Opus、Sonnet 兩列砍掉(claude.ai 用量頁只列這三項)。
ROW_GRID_KEYS_CLAUDE = (
    ("5h", "shared"), ("7d", "shared"), ("7d", "fable"),
)


def usage_dir(override=None):
    path = override or os.environ.get("EZ2_USAGE_DIR") \
        or os.path.expanduser("~/.ez2/usage")
    try:
        path = os.fspath(path)
    except TypeError:
        return None
    return path if os.path.isabs(path) else None


def ctx_dir():
    return os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")


def codex_sessions_dir():
    return os.environ.get("EZ2_CODEX_SESSIONS_DIR") \
        or os.path.expanduser("~/.codex/sessions")


def _mtime_utc_z(path):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(os.path.getmtime(path)))


def _observed_now(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() if now is None else now))


def _atomic_write(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = "%s.tmp-%s-%s" % (path, os.getpid(), int(time.time() * 1000))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.rename(tmp, path)


def _int_pct(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = int(round(float(value)))
    if 0 <= n <= 100:
        return n
    return 100 if n > 100 else None


def _epoch_s(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = int(value)
    if n > 10**12:
        n //= 1000
    return n if n >= 1_000_000_000 else None


def _model_label(raw):
    if isinstance(raw, dict):
        raw = raw.get("display_name") or raw.get("id")
    return display_model(raw)


def valid_reason_code(value):
    """Return a closed-set reason code, or None for an unknown value."""
    return value if value in REASON_CODES else None


def ok_record(engine, window, tier, used_pct, resets_at, source,
              observed_at=None, model=None):
    pct = _int_pct(used_pct)
    if engine not in SUPPORTED or window not in WINDOWS or not tier or pct is None:
        return None
    if not isinstance(source, str) or not source:
        return None
    rec = {
        "schema": SCHEMA, "engine": engine, "window": window, "tier": tier,
        "status": "ok", "used_pct": pct, "resets_at": _epoch_s(resets_at),
        "reason": None, "reason_code": None, "tried": [], "source": source,
        "observed_at": observed_at or _observed_now(),
    }
    label = _model_label(model)
    if label:
        rec["model"] = label
    return rec


def _record(engine, window, used_pct, resets_at, source, observed_at,
            model=None, tier="shared"):
    """Compatibility name retained for the file readers."""
    if resets_at is None:
        return None
    return ok_record(engine, window, tier, used_pct, resets_at, source,
                     observed_at=observed_at, model=model)


def _reason_text(code, tried=None):
    messages = {
        "collect_exception": "採集例外",
        "no_interface": "廠商無介面",
        "no_cap": "來源沒有上限",
        "shared_only": "只有共用配額",
        "probe_failed": "探針失敗",
        "cli_missing": "CLI 不在 PATH",
        "skip_flag": "探針已停用",
        "throttle_stale": "等待節流更新",
        "probe_pending": "等待探針",
        "stale": "資料過期",
    }
    text = messages.get(code, "量不到")
    attempts = [str(item) for item in (tried or []) if str(item)]
    if attempts:
        text += " 試過:" + ",".join(attempts)[:100]
    return text


def unmeasurable_record(engine, window, tier, reason_code, tried=None,
                        reason=None, source="probe:none", observed_at=None):
    code = valid_reason_code(reason_code)
    if code is None:
        return None
    attempts = [str(item) for item in (tried or []) if str(item)]
    return {
        "schema": SCHEMA, "engine": engine, "window": window, "tier": tier,
        "status": "unmeasurable", "used_pct": None, "resets_at": None,
        "reason": str(reason or _reason_text(code, attempts)),
        "reason_code": code, "tried": attempts, "source": source,
        "observed_at": observed_at or _observed_now(),
    }


def record_path(dest, engine, window, tier):
    return os.path.join(os.fspath(dest), "%s-%s-%s.json" % (
        engine, window, tier))


def _ok_is_fresh(rec, now=None):
    now = time.time() if now is None else now
    raw = rec.get("observed_at")
    observed = None
    if isinstance(raw, str):
        try:
            observed = datetime.datetime.strptime(
                raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            pass
    if observed is None and rec.get("_path"):
        try:
            observed = os.path.getmtime(rec["_path"])
        except OSError:
            pass
    resets_at = _epoch_s(rec.get("resets_at"))
    return observed is not None and 0 <= now - observed <= STALE_S \
        and (resets_at is None or now < resets_at)


def write_record(dest, rec, preserve_ok=False):
    if not isinstance(rec, dict):
        raise ValueError("usage record must be a dict")
    engine = rec.get("engine")
    window = rec.get("window")
    tier = rec.get("tier") or "shared"
    status = rec.get("status") or "ok"
    if engine not in SUPPORTED or window not in WINDOWS or not tier:
        raise ValueError("invalid usage identity")
    if status == "unmeasurable":
        if valid_reason_code(rec.get("reason_code")) is None:
            raise ValueError("invalid usage reason_code")
    elif status != "ok" or _int_pct(rec.get("used_pct")) is None:
        raise ValueError("invalid usage status/pct")
    path = record_path(dest, engine, window, tier)
    if preserve_ok and status != "ok":
        old = _load_one(path, engine, window, tier)
        if old is None and tier == "shared":
            old = _load_one(
                os.path.join(os.fspath(dest), "%s-%s.json" % (engine, window)),
                engine, window, tier)
        if old is None and tier == "shared" and window == "7d":
            old = _load_one(
                os.path.join(os.fspath(dest), engine + ".json"),
                engine, window, tier)
        if old and old.get("status") == "ok" and _ok_is_fresh(old):
            return path
    body = dict(rec)
    body["tier"] = tier
    body["status"] = status
    _atomic_write(path, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    return path


def _newest_file(paths):
    newest = None
    newest_m = -1.0
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= newest_m:
            newest_m = mtime
            newest = path
    return newest


def _limit_record(path, data, key, window, tier, mtime):
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    block = limits.get(key) if isinstance(limits, dict) else None
    if not isinstance(block, dict):
        return None
    rec = _record(
        "claude", window, _int_pct(block.get("used_percentage")),
        _epoch_s(block.get("resets_at")), path, _mtime_utc_z(path),
        model=data.get("model"), tier=tier)
    return (mtime, rec) if rec else None


def read_claude(root=None):
    """Read every input file and choose the newest value for each real key."""
    root = os.fspath(root or ctx_dir())
    try:
        names = os.listdir(root)
    except OSError:
        return None
    chosen = {}
    for name in names:
        if not name.endswith(".input.json"):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            mtime = os.path.getmtime(path)
        except (OSError, ValueError, TypeError):
            continue
        specs = (
            ("five_hour", "5h", "shared"),
            ("seven_day", "7d", "shared"),
            ("seven_day_fable", "7d", "fable"),
        )
        for key, window, tier in specs:
            candidate = _limit_record(path, data, key, window, tier, mtime)
            if candidate and candidate[0] >= chosen.get((window, tier), (-1,))[0]:
                chosen[(window, tier)] = candidate
        limits = data.get("rate_limits") if isinstance(data, dict) else None
        shared = limits.get("seven_day") if isinstance(limits, dict) else None
        shared_pct = _int_pct(shared.get("used_percentage")) \
            if isinstance(shared, dict) else None
        if isinstance(shared, dict):
            for tier in ("fable",):
                nested = shared.get(tier)
                if not isinstance(nested, dict):
                    continue
                nested_pct = _int_pct(nested.get("used_percentage"))
                if nested_pct is None or nested_pct == shared_pct:
                    continue
                synthetic = {"rate_limits": {"nested": nested},
                             "model": data.get("model")}
                candidate = _limit_record(
                    path, synthetic, "nested", "7d", tier, mtime)
                if candidate and candidate[0] >= chosen.get(("7d", tier), (-1,))[0]:
                    chosen[("7d", tier)] = candidate
    out = [chosen[key][1] for key in ROW_GRID_KEYS_CLAUDE if key in chosen]
    return out or None


def _codex_day_dirs(root, now):
    out = []
    for back in range(3):
        day = time.localtime(now - back * 86400)
        out.append(os.path.join(root, "%04d" % day.tm_year,
                                "%02d" % day.tm_mon, "%02d" % day.tm_mday))
    return out


def _newest_rollout(root, now):
    candidates = []
    for folder in _codex_day_dirs(root, now):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        candidates.extend(os.path.join(folder, name) for name in names
                          if name.startswith("rollout-") and name.endswith(".jsonl"))
    return _newest_file(candidates)


def _usable_codex_limit_block(block):
    """True when a primary/secondary block still has window + percent."""
    if not isinstance(block, dict):
        return False
    minutes = block.get("window_minutes")
    if minutes not in (_CODEX_FIVE_H_MIN, _CODEX_WEEK_MIN):
        return False
    return _int_pct(block.get("used_percent")) is not None


def _usable_codex_rate_limits(limits):
    """Skip post-limit snapshots where primary/secondary were nulled out."""
    if not isinstance(limits, dict):
        return False
    return (_usable_codex_limit_block(limits.get("primary"))
            or _usable_codex_limit_block(limits.get("secondary")))


def _last_rate_limits(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for line in chunk.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        payload = obj.get("payload") if isinstance(obj, dict) else None
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if _usable_codex_rate_limits(limits):
            last = limits
    return last


def _model_from_pct(engine):
    root = ctx_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return None
    newest = None
    newest_m = -1.0
    for name in names:
        if not name.endswith(".pct"):
            continue
        path = os.path.join(root, name)
        try:
            mtime = os.path.getmtime(path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("engine") == engine and mtime >= newest_m:
            newest_m = mtime
            newest = data.get("model")
    return newest


def read_codex(root=None, now=None):
    root = os.fspath(root or codex_sessions_dir())
    now = time.time() if now is None else now
    path = _newest_rollout(root, now)
    limits = _last_rate_limits(path) if path else None
    if not isinstance(limits, dict):
        return None
    try:
        observed = _mtime_utc_z(path)
    except OSError:
        return None
    out = []
    for key in ("primary", "secondary"):
        block = limits.get(key)
        if not isinstance(block, dict):
            continue
        minutes = block.get("window_minutes")
        window = "5h" if minutes == _CODEX_FIVE_H_MIN else \
            "7d" if minutes == _CODEX_WEEK_MIN else None
        if window:
            rec = _record(
                "codex", window, _int_pct(block.get("used_percent")),
                _epoch_s(block.get("resets_at")), path, observed,
                model=_model_from_pct("codex"))
            if rec:
                out.append(rec)
    return out or None


def read_copilot(_root=None):
    """No passive source currently exposes the plan denominator."""
    return None


def _parse_quota_reset_date(raw):
    """Parse GitHub Copilot ``quota_reset_date`` (YYYY-MM-DD) to UTC midnight epoch.

    Day-level only — never invent a clock time. Missing/invalid → None.
    """
    if not isinstance(raw, str):
        return None
    stamp = raw.strip()[:10]
    if len(stamp) != 10 or stamp[4] != "-" or stamp[7] != "-":
        return None
    try:
        year, month, day = int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10])
        dt = datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp())


def parse_copilot_quota_reset_payload(payload):
    """Extract resets_at from copilot_internal/user JSON. No percentage here."""
    if not isinstance(payload, dict):
        return None
    return _parse_quota_reset_date(payload.get("quota_reset_date"))


def fetch_copilot_quota_reset_at(runner=None, timeout_s=20):
    """Read quota_reset_date via ``gh api`` when logged in. Fail closed → None."""
    run = runner or _run
    try:
        proc = run(
            ["gh", "api", "https://api.github.com/copilot_internal/user"],
            timeout=max(5, int(timeout_s)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = getattr(proc, "stdout", None) or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    try:
        payload = json.loads(out)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parse_copilot_quota_reset_payload(payload)




AGY_FORBIDDEN_SLASHES = frozenset(("/usage", "/stats", "/quota", "/statusline"))
AGY_PRINT_BINS = ("agy", "antigravity")  # never gemini for quota print


def _agy_cli_home():
    return os.environ.get("GEMINI_CLI_HOME") or os.path.expanduser("~/.gemini")


def _parse_iso_epoch(raw):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return _epoch_s(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    stamp = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _agy_remaining_fraction(bucket):
    if not isinstance(bucket, dict):
        return None
    for key in ("remaining_fraction", "remainingFraction", "remaining_percent",
                "remainingPercent"):
        raw = bucket.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        if key in ("remaining_percent", "remainingPercent"):
            if 0.0 <= value <= 100.0:
                return value / 100.0
            continue
        if 0.0 <= value <= 1.0:
            return value
        if 1.0 < value <= 100.0:
            return value / 100.0
    return None


def _agy_groups(payload):
    if not isinstance(payload, dict):
        return []
    for path in (
            ("command", "data", "groups"),
            ("data", "groups"),
            ("groups",),
            ("response", "groups"),
            ("result", "groups"),
    ):
        cur = payload
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok and isinstance(cur, list):
            return cur
    return []


def parse_agy_quota_payload(payload, now=None, source="agy:print:/usage"):
    """Parse agy -p /usage JSON into ok records (one row per group tier)."""
    now = time.time() if now is None else now
    groups = _agy_groups(payload)
    if not groups:
        return None
    by_key = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or group.get("displayName") or "")
        tier = "shared" if ("gemini" in name.lower()) or (not name) \
            else "claude_gpt"
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            label = " ".join(str(bucket.get(k) or "") for k in (
                "name", "window", "id", "description"))
            lowered = label.lower()
            if "week" in lowered or "7d" in lowered or "weekly" in lowered:
                window = "7d"
            elif "5h" in lowered or "5-hour" in lowered or "five hour" in lowered:
                window = "5h"
            else:
                continue
            frac = _agy_remaining_fraction(bucket)
            if frac is None:
                continue
            used = _int_pct(100.0 * (1.0 - frac))
            if used is None:
                continue
            resets = None
            for key in ("reset_time", "resetAt", "resets_at", "resetTime"):
                resets = _parse_iso_epoch(bucket.get(key))
                if resets is not None:
                    break
            if resets is None:
                continue
            candidate = {
                "window": window, "used_pct": used, "resets_at": resets,
                "evidence": "%s rem=%.4f" % (name or "group", frac),
            }
            prev = by_key.get((window, tier))
            if prev is None or used > prev["used_pct"]:
                by_key[(window, tier)] = candidate
    out = []
    for (window, tier), item in by_key.items():
        rec = ok_record(
            "agy", window, tier, item["used_pct"], item["resets_at"],
            source, observed_at=_observed_now(now))
        if rec:
            rec["evidence"] = item["evidence"][:240]
            out.append(rec)
    return out or None


def _extract_json_blobs(text):
    text = text if isinstance(text, str) else ""
    text = text.strip()
    if not text:
        return []
    try:
        return [json.loads(text)]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    blobs = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except (ValueError, json.JSONDecodeError):
            idx = start + 1
            continue
        blobs.append(obj)
        idx = end
    return blobs


def parse_agy_print_output(text, now=None, source="agy:print:/usage"):
    for blob in _extract_json_blobs(text):
        got = parse_agy_quota_payload(blob, now=now, source=source)
        if got:
            return got
    return None


def _run_agy_print_usage(binary, timeout_s=90):
    """Headless print-mode quota; never drives interactive TUI slash input."""
    if not binary or not os.path.isfile(binary):
        return None, "missing_binary"
    cwd = tempfile.mkdtemp(prefix="ez2-agy-quota-")
    try:
        try:
            os.chmod(cwd, 0o700)
        except OSError:
            pass
        proc = subprocess.run(
            [binary, "-p", "/usage", "--output-format", "json"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(5, int(timeout_s)),
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        if proc.returncode != 0 and not out.strip():
            return None, "rc=%s:%s" % (proc.returncode, err[:120])
        return out, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, "%s:%s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def _passive_agy_quota_files(root=None):
    base = root or _agy_cli_home()
    candidates = [
        os.path.join(base, "antigravity-cli", "quota.json"),
        os.path.join(base, "antigravity-cli", "cache", "quota.json"),
        os.path.join(base, "antigravity-cli", "user_quota_summary.json"),
        os.path.join(base, "antigravity", "quota.json"),
    ]
    xdg = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    candidates.append(os.path.join(xdg, "agy-usage", "quota.json"))
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                blob = fh.read(1024 * 1024)
        except OSError:
            continue
        parsed = parse_agy_print_output(blob, source="file:" + path)
        if parsed:
            return parsed
    return None


def read_agy(_root=None, allow_cli=True):
    """Prefer passive cache, else native ``agy -p /usage --output-format json``.

    the maintainer 09-22: interactive TUI ``/usage`` mutates statusLine on Antigravity
    1.2.8 — never send it via IsolatedSession. Individual gemini is ineligible.
    allow_cli=False is the right-pane default: EZ2_USAGE_PROBE_LIVE is not 1,
    so this frame may read files but must not start the agy process.
    """
    passive = _passive_agy_quota_files(_root)
    if passive:
        return passive
    if not allow_cli:
        return None
    binary = which_cli(AGY_PRINT_BINS)
    if not binary:
        return None
    text, _err = _run_agy_print_usage(binary)
    if not text:
        return None
    return parse_agy_print_output(text, source="agy:print:/usage")



def read_grok(_root=None):
    return None


READERS = {
    "claude": read_claude, "codex": read_codex, "agy": read_agy,
    "copilot": read_copilot, "grok": read_grok,
}


# Parser absorbed from the preparation commit. URL escapes, context tokens and
# MCP fractions must never become account quota percentages.
PCT_RE = re.compile(r"(?P<n>\d{1,3}(?:\.\d+)?)\s*%(?![0-9A-Fa-f]{2})")
FRAC_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*/\s*(?P<b>\d+(?:\.\d+)?)")
TOKEN_UNIT_FRAC = re.compile(
    r"\d+(?:\.\d+)?\s*[KMB]\s*/\s*\d+(?:\.\d+)?\s*[KMB]", re.I)
MCP_FRAC = re.compile(r"MCP\s*\(\s*\d+\s*/\s*\d+\s*\)", re.I)
LEFT_PCT_RE = re.compile(
    r"(?:left|remaining|剩)\s*[:：]?\s*(?P<n>\d{1,3}(?:\.\d+)?)\s*%", re.I)
RESET_RE = re.compile(
    r"Resets?:\s*(?P<mon>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*"
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2})", re.I)
CTX_HINTS = ("context window", "contextwindow", "context tokens", "ctx",
             "context:", "context usage", "window tokens", "context-window")
QUOTA_HINTS = ("credit", "quota", "limit", "budget", "plan", "用量", "配額",
               "上限", "billing", "premium request", "supergrok")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def line_is_ctx(line):
    text = line if isinstance(line, str) else ""
    text = text.lower()
    return bool(text.strip()) and any(h in text for h in CTX_HINTS) \
        and not any(h in text for h in QUOTA_HINTS)


def line_is_quota(line):
    text = line if isinstance(line, str) else ""
    return any(h in text.lower() for h in QUOTA_HINTS)


def _pct_from_frac(a, b):
    try:
        den = float(b)
        value = 100.0 * float(a) / den
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return max(0, min(100, int(round(value))))


def _pct_from_match(match):
    try:
        value = int(round(float(match.group("n"))))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))


def parse_resets_at(text, now=None, year=None):
    match = RESET_RE.search(text if isinstance(text, str) else "")
    if not match or match.group("mon").lower() not in _MONTHS:
        return None
    now_dt = datetime.datetime.fromtimestamp(
        time.time() if now is None else now, TAIPEI)
    try:
        value = datetime.datetime(
            year or now_dt.year, _MONTHS[match.group("mon").lower()],
            int(match.group("day")), int(match.group("hh")),
            int(match.group("mm")), tzinfo=TAIPEI)
    except ValueError:
        return None
    if value.timestamp() + 86400 < now_dt.timestamp():
        try:
            value = value.replace(year=value.year + 1)
        except ValueError:
            pass
    return int(value.timestamp())


def extract_pcts(text, require_quota=False, reject_ctx=True):
    out = []
    for raw in (text if isinstance(text, str) else "").splitlines():
        line = TOKEN_UNIT_FRAC.sub("", MCP_FRAC.sub("", raw.strip()))
        if not line or (reject_ctx and line_is_ctx(line)):
            continue
        if require_quota and not line_is_quota(line):
            continue
        left = LEFT_PCT_RE.search(line)
        if left:
            remaining = _pct_from_match(left)
            if remaining is not None:
                out.append((100 - remaining, line))
            continue
        out.extend((_pct_from_match(m), line) for m in PCT_RE.finditer(line))
        out.extend((_pct_from_frac(m.group("a"), m.group("b")), line)
                   for m in FRAC_RE.finditer(line))
    return [(pct, line) for pct, line in out if pct is not None]


def looks_like_ctx_only(text):
    has_ctx = False
    for line in (text if isinstance(text, str) else "").splitlines():
        if PCT_RE.search(line) or FRAC_RE.search(line):
            if line_is_ctx(line):
                has_ctx = True
            elif line_is_quota(line):
                return False
    return has_ctx


def parse_copilot(text):
    hits = extract_pcts(text, reject_ctx=True)
    if not hits:
        hits = extract_pcts(text, require_quota=True, reject_ctx=True)
    if not hits:
        return None
    pct, evidence = hits[0]
    if looks_like_ctx_only(text) and not line_is_quota(evidence):
        return None
    if "https://" in evidence.lower() or "oauth" in evidence.lower():
        return None
    rec = {"used_pct": pct, "evidence": evidence, "kind": "account"}
    resets = parse_resets_at(text)
    if resets:
        rec["resets_at"] = resets
    return rec


def parse_agy(text):
    return parse_copilot(text)


def parse_grok_status(text):
    if looks_like_ctx_only(text):
        for line in (text if isinstance(text, str) else "").splitlines():
            if line_is_ctx(line):
                hits = extract_pcts(line, reject_ctx=False)
                if hits:
                    return {"used_pct": hits[0][0], "evidence": hits[0][1],
                            "kind": "ctx"}
        return {"used_pct": None, "evidence": None, "kind": "ctx"}
    return parse_copilot(text) or {
        "used_pct": None, "evidence": None, "kind": "ctx"}


def parse_grok_usage(text):
    account = parse_copilot(text)
    if account:
        return account
    if looks_like_ctx_only(text):
        return {"used_pct": None, "evidence": None, "kind": "ctx"}
    return None


SOCKET_PREFIX = "ez2-usage-probe"
FORBIDDEN_EXACT = frozenset(("default", "aihub"))
BINS = {
    # the maintainer 09-22:個人版 Gemini CLI 已停用(IneligibleTier/UNSUPPORTED_CLIENT)；agy 列改優先走 Antigravity 二進位。
    "copilot": ("copilot",), "agy": ("agy", "antigravity", "gemini"),
    "grok": ("grok",),
}
SLASH = {
    # the maintainer 09-22: Antigravity TUI /usage|/stats 會改 statusLine，禁止互動探針送出。
    "copilot": ("/usage",), "agy": (),
    # the maintainer 09-22: Grok /status 會被補成 /statusline；改走 /usage → Enter(show) 面板。
    "grok": ("/usage",),
}
TRIED = {
    # the maintainer 09-22: CLI /usage 無 Resets；重置日另讀 gh api:quota_reset_date。
    "copilot": ("/usage", "--usage-output-file", "api:quota_reset_date"),
    "agy": ("print:/usage",), "grok": ("/usage", "/usage:show"),
}
QUIT = {"copilot": "/exit", "agy": "/quit", "grok": "/quit"}


class IsolationError(RuntimeError):
    pass


def socket_forbidden(name):
    if not isinstance(name, str) or not name or name in FORBIDDEN_EXACT:
        return True
    if name.startswith("aihub-") or name.startswith("aib-"):
        return True
    return name != SOCKET_PREFIX and not name.startswith(SOCKET_PREFIX + "-")


# 第 62 項(09-12):探針的 tmux 要**解成絕對路徑**,不靠子行程的 PATH 去找「tmux」這個名字。
# 09-12 01:41 三家(agy/copilot/grok)全 unmeasurable,理由「env: tmux: No such file」——
# 起探針的行程 PATH 是 `<治具>/bin:/usr/bin:/bin`(test-provider-launch-direct 經 provider-launch 的綁定探針漏進真 usage 目錄),
# 這台機器的 tmux 在 /opt/homebrew/bin。找法與 seat_onboard._tmux_bin 同形:EZ2_AIB_TMUX_BIN > PATH 上的 which >
# 幾個常見安裝目錄;都沒有 ⇒ IsolationError 具名(理由裡帶 PATH 與查過的目錄),紀錄 used_pct 仍是 None,不寫 0。
TMUX_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


def resolve_tmux_bin(path_env=None, fallback_dirs=None):
    """回 (絕對路徑或 None, 說明)。path_env=None 用本行程 PATH;fallback_dirs=None 讀模組常數(呼叫時才讀,治具可 patch)。"""
    if fallback_dirs is None:
        fallback_dirs = TMUX_FALLBACK_DIRS
    named = os.environ.get("EZ2_AIB_TMUX_BIN") or ""
    if named:
        if os.path.isabs(named) and os.access(named, os.X_OK):
            return named, "env:EZ2_AIB_TMUX_BIN"
        found = shutil.which(named, path=path_env)
        if found:
            return found, "env:EZ2_AIB_TMUX_BIN"
    found = shutil.which("tmux", path=path_env)
    if found:
        return found, "PATH"
    for d in fallback_dirs:
        cand = os.path.join(d, "tmux")
        if os.access(cand, os.X_OK):
            return cand, "fallback:" + d
    return None, "tmux 不在 PATH:%s(也查過 %s)" % (
        path_env if path_env is not None else os.environ.get("PATH", ""), ",".join(fallback_dirs))


def tmux_argv(sock, *args, path_env=None):
    if socket_forbidden(sock):
        raise IsolationError("socket forbidden: %r" % (sock,))
    binary, how = resolve_tmux_bin(path_env)
    if not binary:
        raise IsolationError(how)
    return ["env", "-u", "TMUX", binary, "-L", sock] + list(args)


def _run(argv, timeout, check=False):
    return subprocess.run(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, timeout=timeout, check=check)


class IsolatedSession:
    def __init__(self, sock=SOCKET_PREFIX, timeout_s=60, width=120, height=40,
                 session_name=None, path_env=None):
        session_name = session_name or "probe-%s" % os.getpid()
        self.path_env = path_env      # 第 62 項:tmux 也照這段 PATH(加 fallback)解成絕對路徑
        if socket_forbidden(sock):
            raise IsolationError("socket forbidden: %r" % (sock,))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(session_name or "")):
            raise IsolationError("session name forbidden: %r" % (session_name,))
        self.sock = sock
        self.session_name = session_name
        self.timeout_s = max(1, int(timeout_s))
        self.width = int(width)
        self.height = int(height)
        self.cwd = None
        self._deadline = None
        self.socket_path = None

    def __enter__(self):
        self.cwd = tempfile.mkdtemp(prefix="ez2-usage-probe.")
        if "ez2-aib" in os.path.realpath(self.cwd):
            raise IsolationError("temp cwd landed inside repo: %s" % self.cwd)
        socket_root = os.environ.get("TMUX_TMPDIR") or "/tmp"
        if os.path.isabs(socket_root):
            self.socket_path = os.path.join(
                socket_root, "tmux-%s" % os.getuid(), self.sock)
        self._deadline = time.time() + self.timeout_s
        return self

    def remaining(self):
        return max(0.1, (self._deadline or time.time()) - time.time())

    def tmux(self, *args, timeout=None):
        return _run(tmux_argv(self.sock, *args, path_env=self.path_env),
                    timeout=timeout if timeout is not None else min(15, self.remaining()))

    def remember_socket_path(self):
        result = self.tmux(
            "display-message", "-p", "#{socket_path}", timeout=5)
        candidate = (result.stdout or "").strip() if result.returncode == 0 else ""
        if os.path.isabs(candidate) and os.path.basename(candidate) == self.sock:
            self.socket_path = candidate
        return self.socket_path

    def remove_dead_socket(self):
        socket_path = self.socket_path
        if not socket_path:
            return

        def unlink_exact_socket():
            try:
                if stat.S_ISSOCK(os.lstat(socket_path).st_mode):
                    os.unlink(socket_path)
                    return True
            except OSError:
                pass
            return False

        quiet_checks = 0
        for _attempt in range(40):
            server_alive = self.tmux(
                "list-sessions", timeout=5).returncode == 0
            if server_alive:
                self.tmux("kill-server", timeout=5)
            socket_alive = False
            try:
                socket_alive = stat.S_ISSOCK(os.lstat(socket_path).st_mode)
                if socket_alive and not server_alive:
                    socket_alive = not unlink_exact_socket()
            except OSError:
                pass
            if server_alive or socket_alive:
                quiet_checks = 0
            else:
                quiet_checks += 1
                if quiet_checks >= 10:
                    return
            time.sleep(0.05)
        unlink_exact_socket()

    def start(self, binary, extra_args=None):
        if not binary or not os.path.isfile(binary) or not os.access(binary, os.X_OK):
            raise IsolationError("cli missing or not executable: %r" % binary)
        result = self.tmux(
            "new-session", "-d", "-s", self.session_name,
            "-x", str(self.width),
            "-y", str(self.height), "-c", self.cwd, "--", binary,
            *(extra_args or []), timeout=min(20, self.remaining()))
        self.remember_socket_path()
        if result.returncode:
            raise IsolationError("tmux new-session rc=%s stderr=%s" % (
                result.returncode, (result.stderr or "").strip()[:300]))

    def send(self, keys, enter=True):
        result = self.tmux(
            "send-keys", "-t", self.session_name, "--", keys)
        if enter:
            self.send_enter()
        return result

    def send_enter(self):
        return self.tmux(
            "send-keys", "-t", self.session_name, "Enter")

    def capture(self):
        result = self.tmux(
            "capture-pane", "-t", self.session_name,
            "-p", "-S", "-", "-E", "-")
        return result.stdout if result.returncode == 0 else ""

    def wait_text(self, predicate, every=0.4):
        last = ""
        while self.remaining() > 0.2:
            last = self.capture()
            try:
                if predicate(last):
                    return last
            except Exception:
                pass
            time.sleep(every)
        return last

    def quit(self, slash):
        if slash:
            self.send(slash)
            time.sleep(0.4)
        self.tmux("send-keys", "-t", self.session_name, "C-c")

    def __exit__(self, _exc_type, _exc, _tb):
        try:
            self.tmux("kill-server", timeout=5)
        except Exception:
            pass
        try:
            self.remove_dead_socket()
        except Exception:
            pass
        if self.cwd and os.path.isdir(self.cwd):
            shutil.rmtree(self.cwd, ignore_errors=True)
            for _try in range(8):
                if not os.path.isdir(self.cwd):
                    break
                time.sleep(0.05)
                shutil.rmtree(self.cwd, ignore_errors=True)
        return False


def which_cli(names, path_env=None):
    for name in ((names,) if isinstance(names, str) else names):
        path = shutil.which(name, path=path_env)
        if path:
            return path
    return None


def _trust_dialog(text):
    lowered = (text or "").lower()
    return "do you trust" in lowered or "confirm folder trust" in lowered


def _dismiss_trust(session):
    text = session.capture()
    for _ in range(4):
        if not _trust_dialog(text):
            break
        session.send_enter()
        time.sleep(0.2)
        text = session.capture()
    return text


def _auth_wall(text):
    lowered = (text or "").lower()
    return any(item in lowered for item in (
        "authentication page", "waiting for authentication",
        "accounts.google.com/o/oauth", "please log in", "please login"))


def _safe_evidence(text):
    value = re.sub(r"https?://\S+", "[url]", str(text or ""))
    value = re.sub(r"(?i)(token|key|secret)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value[:240]


def _capture_owned_by(engine, text):
    """Fail closed when a shared tmux target returned another CLI's frame."""
    lowered = str(text or "").lower()
    if engine == "copilot":
        return "copilot" in lowered or "ai credits" in lowered \
            or re.search(r"(?m)^\s*plan\b", lowered) is not None
    if engine == "grok":
        return "grok" in lowered or "supergrok" in lowered \
            or "weekly limit" in lowered
    if engine == "agy":
        return any(marker in lowered for marker in (
            "antigravity", "gemini", "google ai")) \
            or ("/stats" in lowered and "session tokens" in lowered)
    return False


def _record_evidence_owned(rec):
    engine = rec.get("engine")
    declared = rec.get("evidence_engine")
    if declared is not None:
        return declared == engine
    evidence = str(rec.get("evidence") or "")
    if engine == "grok" and rec.get("resets_at") is not None \
            and ("█" in evidence or "weekly limit" in evidence.lower()):
        return True
    return _capture_owned_by(engine, evidence)


def _explicit_no_interface(text):
    """Only affirmative command-missing output can name no_interface."""
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in (
        "unknown command", "unrecognized command", "invalid command",
        "command not found", "no such command", "no usage command",
        "無此指令", "未知指令", "找不到這個指令", "找不到該指令",
    ))


_CAPTURE_SPINNER = re.compile(
    r"(^|[ \t])(?:[\u2800-\u28ff◐◓◑◒|/\\-]+)(?=[ \t]|$)",
    re.MULTILINE)


def _normalize_capture_animation(text):
    return _CAPTURE_SPINNER.sub(r"\1[spinner]", str(text or ""))


def _capture_unchanged(before, after):
    previous = _normalize_capture_animation(before)
    current = _normalize_capture_animation(after)
    if current == previous:
        return True
    if len(current) != len(previous):
        return False
    previous_lines = set(previous.splitlines())
    return not any(line not in previous_lines for line in current.splitlines())


def _unchanged_commands(captures, commands):
    unchanged = []
    previous = captures.get("ready", "")
    for command in commands:
        current = captures.get(command, "")
        if _capture_unchanged(previous, current):
            unchanged.append(command)
        previous = current
    return unchanged


def _probe_failure_detail(unchanged, stuck, auth, tried):
    both_walls = "卡在信任對話框且在等登入"
    if unchanged:
        detail = "指令沒被執行"
        if not stuck and not auth:
            detail += "(逾時內畫面未變)"
        if stuck and auth:
            detail += "(" + both_walls + ")"
        elif stuck:
            detail += "(卡在信任對話框)"
        elif auth:
            detail += "(卡在登入頁)"
    elif stuck and auth:
        detail = both_walls
    elif stuck:
        detail = "卡在信任對話框"
    elif auth:
        detail = "卡在登入頁"
    else:
        detail = "送了 %s 但逾時內沒有可解析輸出" % ",".join(tried)
    return "%s 試過:%s" % (detail, ",".join(tried))



def _grok_usage_menu(text):
    """True when /usage opened the show/manage picker instead of the quota panel."""
    lowered = (text or "").lower()
    return ("view usage" in lowered and "manage" in lowered)         or ("show" in lowered and "manage billing" in lowered)


def _open_grok_usage_show(session, captures):
    """Send /usage; if picker appears, Enter to open Usage limit panel (has Resets:)."""
    session.send("/usage")
    time.sleep(1.2)
    frame = session.capture()
    if _trust_dialog(frame):
        frame = _dismiss_trust(session)
        session.send("/usage")
        time.sleep(1.2)
        frame = session.capture()
    captures["/usage"] = frame
    if _grok_usage_menu(frame):
        session.send_enter()
        time.sleep(1.5)
        frame = session.capture()
        captures["/usage:show"] = frame
    return frame

def probe_engine(engine, timeout_s=60, sock=SOCKET_PREFIX, path_env=None,
                 session_factory=IsolatedSession, session_name=None):
    if engine not in PROBE_ENGINES:
        raise ValueError("unsupported active probe: %s" % engine)
    tried = list(TRIED[engine])
    binary = which_cli(BINS[engine], path_env=path_env)
    if not binary:
        return _cli_missing_record(engine)
    # the maintainer 09-22: agy 改走 read_agy(print)；互動 SLASH 為空時不要再開 TUI。
    if engine == "agy" and not SLASH.get("agy"):
        return unmeasurable_record(
            engine, "7d", "shared", "no_interface",
            tried=list(TRIED.get("agy") or ("print:/usage",)),
            reason="agy 改走 print:/usage，互動探針已停用",
            source="agy:print:/usage")
    captures = {}
    parsed = None
    error = None
    try:
        target = session_name or "probe-%s-%s" % (os.getpid(), engine)
        with session_factory(
                sock=sock, timeout_s=timeout_s,
                session_name=target, path_env=path_env) as session:
            # the maintainer 09-22:優先 agy(Antigravity)。若仍落到 gemini 才帶 --skip-trust（agy 無此旗標）。
            if engine == "copilot":
                extra_args = ["--usage-output-file", "usage-output.json"]
            elif engine == "agy" and os.path.basename(binary).lower() in ("gemini", "gemini.js"):
                extra_args = ["--skip-trust"]
            else:
                extra_args = None
            session.start(binary, extra_args=extra_args)
            ready = session.wait_text(lambda text: bool((text or "").strip()))
            ready = _dismiss_trust(session)
            if engine == "copilot":
                session.send_enter()
                ready = _dismiss_trust(session)
            captures["ready"] = ready
            # Footer often already shows Weekly limit left — seed parse before slash.
            if engine == "grok":
                ready_got = parse_grok_usage(ready)
                if ready_got and ready_got.get("kind") == "account":
                    parsed = dict(
                        ready_got, slash="footer", frame=ready,
                        owned=_capture_owned_by(engine, ready))
                frame = _open_grok_usage_show(session, captures)
                got = parse_grok_usage(frame)
                if got and got.get("kind") == "account":
                    # Prefer panel parse (has Resets:) over footer-only.
                    slash_name = "/usage:show" if "/usage:show" in captures else "/usage"
                    parsed = dict(
                        got, slash=slash_name, frame=frame,
                        owned=_capture_owned_by(engine, frame))
            else:
                for slash in SLASH[engine]:
                    if engine == "agy" and slash in AGY_FORBIDDEN_SLASHES:
                        continue
                    session.send(slash)
                    time.sleep(1.2)
                    frame = session.capture()
                    if _trust_dialog(frame):
                        frame = _dismiss_trust(session)
                        session.send(slash)
                        time.sleep(1.2)
                        frame = session.capture()
                    captures[slash] = frame
                    if engine == "copilot":
                        got = parse_copilot(frame)
                    elif engine == "agy":
                        got = parse_agy(frame)
                    else:
                        got = parse_grok_usage(frame)
                    if got and got.get("kind") == "account":
                        parsed = dict(got, slash=slash, frame=frame,
                                      owned=_capture_owned_by(engine, frame))
            if engine == "copilot" and getattr(session, "cwd", None):
                usage_output = os.path.join(session.cwd, "usage-output.json")
                try:
                    with open(usage_output, encoding="utf-8") as fh:
                        output_text = fh.read(64 * 1024)
                except OSError:
                    output_text = ""
                got = parse_copilot(output_text)
                if got and got.get("kind") == "account":
                    parsed = dict(got, slash="--usage-output-file",
                                  frame=output_text, owned=True)
            session.quit(QUIT[engine])
    except Exception as exc:
        error = "%s:%s" % (type(exc).__name__, exc)
    if error:
        return unmeasurable_record(
            engine, "7d", "shared", "probe_failed", tried,
            reason="探針失敗:" + error[:100], source="cli:" + tried[0])
    slash_keys = list(SLASH[engine])
    if engine == "grok" and "/usage:show" in captures:
        slash_keys = list(slash_keys) + ["/usage:show"]
    unchanged = _unchanged_commands(captures, slash_keys)
    stuck = any(_trust_dialog(value) for value in captures.values())
    auth = any(_auth_wall(value) for value in captures.values())
    if parsed and parsed.get("owned") and not unchanged \
            and not stuck and not auth:
        resets_at = parsed.get("resets_at")
        source = "cli:" + parsed.get("slash", tried[0])
        # the maintainer 09-22: Copilot CLI /usage 無 Resets；有 gh 登入才補 quota_reset_date。
        if engine == "copilot" and resets_at is None:
            api_reset = fetch_copilot_quota_reset_at()
            if api_reset is not None:
                resets_at = api_reset
                source = source + "+api:quota_reset_date"
        rec = ok_record(
            engine, "7d", "shared", parsed.get("used_pct"),
            resets_at, source)
        if rec:
            rec["tried"] = tried
            rec["evidence"] = _safe_evidence(parsed.get("evidence"))
            rec["evidence_engine"] = engine
            return rec
    if parsed and not parsed.get("owned"):
        return unmeasurable_record(
            engine, "7d", "shared", "probe_failed", tried,
            reason="探針失敗:畫面歸屬不符 試過:%s" % ",".join(tried),
            source="cli:" + ",".join(tried))
    if unchanged or stuck or auth:
        return unmeasurable_record(
            engine, "7d", "shared", "probe_failed", tried,
            reason="探針失敗:" + _probe_failure_detail(
                unchanged, stuck, auth, tried),
            source="cli:" + ",".join(tried))
    command_captures = (captures.get(command, "") for command in SLASH[engine])
    if any(_explicit_no_interface(value) for value in command_captures):
        return unmeasurable_record(
            engine, "7d", "shared", "no_interface", tried,
            source="cli:" + ",".join(tried))
    return unmeasurable_record(
        engine, "7d", "shared", "probe_failed", tried,
        reason="探針失敗:" + _probe_failure_detail(
            unchanged, stuck, auth, tried),
        source="cli:" + ",".join(tried))


def _stamp_path(stamp_dir):
    return os.path.join(os.fspath(stamp_dir), "usage-probe.stamp") \
        if stamp_dir else None


def _read_stamp(stamp_dir):
    path = _stamp_path(stamp_dir)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return float(fh.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _stamp_is_throttled(stamped, now, throttle_s):
    if stamped is None:
        return False
    return 0 <= now - stamped < throttle_s


def _throttled(stamp_dir, now, throttle_s):
    return _stamp_is_throttled(_read_stamp(stamp_dir), now, throttle_s)


def _stamp_is_same_frame(stamped, now):
    if stamped is None:
        return False
    return 0 <= now - stamped < SAME_FRAME_S


def _touch_stamp(stamp_dir, now):
    path = _stamp_path(stamp_dir)
    if path:
        try:
            _atomic_write(path, "%.3f\n" % now)
        except OSError:
            pass


def _path_fingerprint(path_env=None):
    return os.fspath(path_env) if path_env is not None \
        else os.environ.get("PATH", "")


def _probe_record(dest, engine):
    for rec in load(dest):
        if rec.get("engine") == engine and rec.get("window") == "7d" \
                and (rec.get("tier") or "shared") == "shared":
            return rec
    return None


def _probe_due(rec, now, path_env=None):
    if not rec:
        return True
    if rec.get("status") == "ok" and not _record_evidence_owned(rec):
        return True
    last_status = rec.get("last_probe_status")
    if last_status == "cli_missing" \
            and rec.get("path_fingerprint") == _path_fingerprint(path_env):
        return False
    next_probe = rec.get("next_probe_at")
    if isinstance(next_probe, (int, float)) and not isinstance(next_probe, bool) \
            and now < next_probe:
        return False
    if rec.get("status") == "ok":
        resets_at = _epoch_s(rec.get("resets_at"))
        if resets_at is not None:
            return now >= resets_at
        return _observed_age_s(rec, now) >= OK_TTL_S
    code = rec.get("reason_code")
    if code == "cli_missing":
        return rec.get("path_fingerprint") != _path_fingerprint(path_env)
    if code in ("probe_failed", "no_interface"):
        return _observed_age_s(rec, now) >= FAIL_RETRY_S
    return True


def _mark_probe_timing(rec, now, path_env=None):
    body = dict(rec)
    if body.get("status") == "ok":
        resets_at = _epoch_s(body.get("resets_at"))
        body["next_probe_at"] = resets_at if resets_at is not None \
            else now + OK_TTL_S
    elif body.get("reason_code") in ("probe_failed", "no_interface"):
        body["next_probe_at"] = now + FAIL_RETRY_S
    elif body.get("reason_code") == "cli_missing":
        body["path_fingerprint"] = _path_fingerprint(path_env)
    return body


def _write_probe_result(dest, rec, now=None):
    """Keep a visible old ok, but attach the failed attempt's retry state."""
    now = time.time() if now is None else now
    if rec.get("status") == "ok":
        return write_record(dest, rec)
    old = _probe_record(dest, rec.get("engine"))
    ownership_mismatch = "畫面歸屬不符" in str(rec.get("reason") or "") \
        or (old is not None and old.get("status") == "ok" and old.get("evidence")
            and not _record_evidence_owned(old))
    if old and old.get("status") == "ok" \
            and _ok_is_fresh(old, now=now) and not ownership_mismatch:
        kept = {key: value for key, value in old.items()
                if not key.startswith("_")}
        kept["last_probe_status"] = rec.get("reason_code")
        for key in ("next_probe_at", "path_fingerprint"):
            if key in rec:
                kept[key] = rec[key]
            else:
                kept.pop(key, None)
        return write_record(dest, kept)
    if old and old.get("status") == "ok":
        reason = "上次讀數歸屬不符" if ownership_mismatch else "上次讀數已過期"
        stale = unmeasurable_record(
            rec.get("engine"), "7d", "shared", "stale",
            tried=rec.get("tried"), reason=reason,
            source=old.get("source") or rec.get("source"),
            observed_at=old.get("observed_at"))
        stale["last_used_pct"] = old.get("used_pct")
        stale["last_evidence"] = old.get("evidence")
        stale["last_probe_status"] = rec.get("reason_code")
        for key in ("next_probe_at", "path_fingerprint"):
            if key in rec:
                stale[key] = rec[key]
        return write_record(dest, stale)
    return write_record(dest, rec)


def _probe_day(now):
    return datetime.datetime.fromtimestamp(now, TAIPEI).strftime("%Y-%m-%d")


def _probe_count_path(dest, engine, now):
    return os.path.join(os.fspath(dest), "probe-count-%s-%s" % (
        engine, _probe_day(now)))


def _daily_probe_count(dest, engine, now):
    try:
        with open(_probe_count_path(dest, engine, now), encoding="utf-8") as fh:
            count = int(fh.read().strip())
    except (OSError, ValueError):
        return 0
    return max(0, count)


def _increment_daily_probe_count(dest, engine, now):
    count = _daily_probe_count(dest, engine, now) + 1
    _atomic_write(_probe_count_path(dest, engine, now), "%s\n" % count)
    return count


def _daily_cap_record(engine, now):
    return unmeasurable_record(
        engine, "7d", "shared", "throttle_stale",
        reason="今日探針已達上限", source="probe:daily-cap",
        observed_at=_observed_now(now))


def _validated_probe_result(expected_engine, rec):
    if isinstance(rec, dict) and rec.get("engine") == expected_engine:
        body = dict(rec)
        if body.get("status") == "ok" and not body.get("evidence") \
                and body.get("evidence_engine") is None:
            body["evidence_engine"] = expected_engine
        return body
    got = rec.get("engine") if isinstance(rec, dict) else type(rec).__name__
    return unmeasurable_record(
        expected_engine, "7d", "shared", "probe_failed", TRIED[expected_engine],
        reason="探針失敗:結果歸屬不符:%s" % got,
        source="cli:" + TRIED[expected_engine][0])


def _probe_lock_path(dest):
    return os.path.join(os.fspath(dest), "usage-probe.lock")


def _read_probe_lock(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or isinstance(value.get("pid"), bool) \
            or not isinstance(value.get("pid"), int):
        return None
    return value


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _acquire_probe_lock(dest):
    os.makedirs(dest, exist_ok=True)
    path = _probe_lock_path(dest)
    token = "%s-%s" % (os.getpid(), time.time_ns())
    body = json.dumps({"pid": os.getpid(), "token": token}) + "\n"
    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                before = os.stat(path)
            except OSError:
                continue
            owner = _read_probe_lock(path)
            if owner and _pid_alive(owner.get("pid")):
                return None
            if owner is None and time.time() - before.st_mtime < 10:
                return None
            try:
                after = os.stat(path)
                if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
                    os.unlink(path)
            except OSError:
                pass
            continue
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)
        return path, token
    return None


def _set_probe_lock_pid(path, token, pid):
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            try:
                owner = json.load(fh)
            except (ValueError, TypeError):
                return False
            if not isinstance(owner, dict) or owner.get("token") != token:
                return False
            fh.seek(0)
            fh.truncate()
            json.dump({"pid": int(pid), "token": token}, fh)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError:
        return False


def _release_probe_lock(path, token):
    try:
        before = os.stat(path)
    except OSError:
        return False
    owner = _read_probe_lock(path)
    if not owner or owner.get("token") != token:
        return False
    try:
        after = os.stat(path)
        current = _read_probe_lock(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) \
                or not current or current.get("token") != token:
            return False
        os.unlink(path)
    except OSError:
        return False
    return True


def _records(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _missing_for_engine(engine, code, now):
    return [unmeasurable_record(e, w, t, code, observed_at=_observed_now(now))
            for e, w, t in GRID if e == engine]


def _cli_missing_record(engine, now=None):
    names = ",".join(BINS[engine])
    tried = list(TRIED[engine]) + ["which:" + names]
    return unmeasurable_record(
        engine, "7d", "shared", "cli_missing", tried,
        reason="CLI 不在 PATH:" + names, source="cli:missing",
        observed_at=_observed_now(now))


def _reprobe_passive(engine, dest_dir=None, now=None):
    """the maintainer 09-22:claude/codex 的 [重驗] —— 強制重讀被動來源並寫回(略過 throttle)。"""
    now = time.time() if now is None else now
    engine = str(engine or "").strip().lower()
    dest = usage_dir(dest_dir)
    if dest is None:
        return {"engine": engine, "status": "no_dest"}
    reader = READERS.get(engine)
    if reader is None:
        return {"engine": engine, "status": "unknown_engine"}
    try:
        value = reader(now=now) if engine == "codex" else reader()
        items = _records(value)
        wrote = 0
        for item in items or []:
            if not item:
                continue
            write_record(dest, item, preserve_ok=False)
            wrote += 1
        if wrote == 0:
            return {"engine": engine, "status": "no_source"}
        return {"engine": engine, "status": "refreshed", "wrote": wrote}
    except Exception as exc:
        return {"engine": engine, "status": "refresh_failed",
                "error": "%s:%s" % (type(exc).__name__, str(exc)[:120])}


def reprobe(engine, dest_dir=None, now=None, spawn=None):
    """the maintainer 09-06 13:5x 裁:過期列後面的 [重驗] —— 人點了就只對那一家再探一次。

    活探三家走 _spawn_probe(同 pid lock、同 socket、同每日上限)。
    claude/codex 走 _reprobe_passive:強制重讀來源檔(the maintainer 09-22)。
    回 {"engine", "status"}:started / lock_busy / daily_cap / refreshed /
    no_source / refresh_failed / unknown_engine / no_dest。
    spawn 可注入(治具不起子行程)。
    """
    now = time.time() if now is None else now
    engine = str(engine or "").strip().lower()
    if engine in ("claude", "codex"):
        return _reprobe_passive(engine, dest_dir=dest_dir, now=now)
    if engine not in PROBE_ENGINES:
        return {"engine": engine, "status": "unknown_engine"}
    dest = usage_dir(dest_dir)
    if dest is None:
        return {"engine": engine, "status": "no_dest"}
    if _daily_probe_count(dest, engine, now) >= DAILY_PROBE_CAP:
        try:
            write_record(dest, _daily_cap_record(engine, now), preserve_ok=True)
        except (OSError, ValueError):
            pass
        return {"engine": engine, "status": "daily_cap"}
    _increment_daily_probe_count(dest, engine, now)
    proc = (spawn or _spawn_probe)([engine], dest, timeout_s=60)
    return {"engine": engine, "status": "started" if proc is not None else "lock_busy"}


def bind_probe(engine, dest_dir=None, now=None, spawn=None):
    """綁定時量一次(SPEC USAGE-BIND-PROBE-v1 §1/§2/§5,the maintainer 2026-09-08 直裁)。

    原話:「綁定時,就量一次,額度夠、每次就更新,額度不夠,就記憶並自動倒計數。」

    ## 為什麼掛在綁定,不掛在重繪

    判準是量出來的(2026-09-08 實測):重繪一天數百次 —— 缺陷三十九就是死在那裡,
    每次重繪都真的去起 CLI,一天撞幾百次登入牆;而綁定當天是 **8 個艙 / 24 席**。
    **綁定是離散、稀少、而且正是「這個引擎要開始被用了」的那一刻。**

    ## 為什麼一天一家只探一次

    每日上限 `DAILY_PROBE_CAP` 是 24,而當天開了 24 席 —— **同一個量級**。
    每次綁定都探會把上限吃光,而**上限吃光的失效形狀是「探不到」**,
    跟探針壞掉同形。所以這裡只在**今天這一家一次都還沒探過**時才發動,
    其餘留給人點 `[重驗]`。

    ## 它不擋人

    回 `skipped` / `not_probe_engine` / `no_dest` 都是正常結果,不是錯誤。
    呼叫端(`provider-launch.sh`)一律忽略回值 —— **探針是附帶動作不是前置條件**,
    綁定的成敗不因它而改變(§5)。

    回 {"engine", "status"}:
      started / lock_busy / daily_cap(沿用 reprobe 的碼系)
      skipped_today(今天已經探過)/ not_probe_engine(被動來源的家)/ no_dest
    """
    now = time.time() if now is None else now
    engine = str(engine or "").strip().lower()
    if engine not in PROBE_ENGINES:
        # claude / codex 讀被動來源(ctx json、rollout jsonl),沒有東西要「探」。
        return {"engine": engine, "status": "not_probe_engine"}
    dest = usage_dir(dest_dir)
    if dest is None:
        return {"engine": engine, "status": "no_dest"}
    if _daily_probe_count(dest, engine, now) > 0:
        return {"engine": engine, "status": "skipped_today"}
    return reprobe(engine, dest_dir=dest_dir, now=now, spawn=spawn)


def _spawn_probe(engines, dest, timeout_s=60, path_env=None,
                 sock=SOCKET_PREFIX):
    """Start the isolated CLI worker without making the rendering frame wait."""
    lock = _acquire_probe_lock(dest)
    if lock is None:
        return None
    lock_path, lock_token = lock
    argv = [
        sys.executable, os.path.abspath(__file__),
        "--engines", ",".join(engines),
        "--dest", os.path.abspath(os.fspath(dest)),
        "--timeout", str(max(1, int(timeout_s))),
        "--socket", sock,
        "--lock-path", lock_path,
        "--lock-token", lock_token,
    ]
    env = None
    if path_env is not None:
        env = dict(os.environ)
        env["PATH"] = os.fspath(path_env)
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except Exception:
        _release_probe_lock(lock_path, lock_token)
        raise
    _set_probe_lock_pid(lock_path, lock_token, proc.pid)
    return proc


def _agy_record_fresh(dest, now):
    """dest 裡 agy 有一筆 ok 且 AGY_LIVE_MIN_S 內量到 ⇒ True。量不到/舊/沒有 ⇒ False(該重探;#14 (c) 舊的量不到不再一直藏著)。"""
    try:
        for rec in load(dest):
            if rec.get("engine") != "agy" or rec.get("status") != "ok":
                continue
            seen = _parse_iso_epoch(rec.get("observed_at"))
            if seen is not None and 0 <= now - seen < AGY_LIVE_MIN_S:
                return True
    except Exception:  # noqa: BLE001 —— 讀不到就當不新,讓它重探
        return False
    return False


AGY_ATTEMPT_FILE = ".agy-live-attempt"


def _agy_attempt_due(dest, now):
    """上次起背景 agy 量測的時刻(不管成敗)離現在 ≥ AGY_LIVE_MIN_S 才准再起。讀不到 = 該起。"""
    try:
        last = float((Path(dest) / AGY_ATTEMPT_FILE).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return True
    return not (0 <= now - last < AGY_LIVE_MIN_S)


def _claim_and_spawn_agy(dest, now, spawn=None):
    """A 包 r2 低(codex):「判到期 → 寫嘗試時刻」要原子,兩個刷新同時到期只准一個起 worker。
    用 .agy-live-attempt.lock 的 flock(非阻塞)包住判與寫;拿不到鎖 = 別人正在判,這一幀不起。"""
    import fcntl
    try:
        Path(dest).mkdir(parents=True, exist_ok=True)
        fh = open(Path(dest) / (AGY_ATTEMPT_FILE + ".lock"), "a")
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        if not _agy_attempt_due(dest, now):
            return None
        return (spawn or _spawn_agy_print_refresh)(dest, now)
    finally:
        fh.close()


def _spawn_agy_print_refresh(dest, now, popen=None):
    """先記嘗試時刻(失敗也算,A1),再起脫離的子行程跑 `usage_probe.py --agy-print-refresh --dest <dest>`。"""
    try:
        Path(dest).mkdir(parents=True, exist_ok=True)
        (Path(dest) / AGY_ATTEMPT_FILE).write_text("%d\n" % int(now), encoding="utf-8")
    except OSError:
        return None
    try:
        return (popen or subprocess.Popen)(
            [sys.executable, os.path.abspath(__file__), "--agy-print-refresh", "--dest", str(dest)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except Exception:  # noqa: BLE001 —— 起不來就等下一個 30 分
        return None


def agy_print_refresh(dest):
    """背景子行程本體:headless `agy -p /usage`,量到寫 ok 記錄;量不到不寫(舊記錄照留,下一個 30 分再試)。"""
    items = _records(read_agy(allow_cli=True))
    wrote = 0
    for rec in items:
        if rec and rec.get("status") == "ok":
            write_record(dest, rec)
            wrote += 1
    return wrote


def refresh_all(now=None, stamp_dir=None, throttle_s=THROTTLE_S,
                dest_dir=None, enable_cli=None, readers=None, probe=None,
                skip=None, probe_kwargs=None):
    """Refresh each provider independently; one failure cannot erase old ok data."""
    now = time.time() if now is None else now
    dest = usage_dir(dest_dir)
    if dest is None:
        return False
    stamp_dir = stamp_dir or dest
    if _throttled(stamp_dir, now, throttle_s):
        existing = {(r["engine"], r["window"], r["tier"]) for r in load(dest)}
        for engine, window, tier in GRID:
            if (engine, window, tier) not in existing:
                write_record(dest, unmeasurable_record(
                    engine, window, tier, "throttle_stale",
                    observed_at=_observed_now(now)), preserve_ok=True)
        return False
    # v0.70.0 #14:enable_cli 可以是引擎名集合(只准那幾家起 CLI);True/False 照舊是全部/全不。
    if isinstance(enable_cli, (set, frozenset, tuple, list)):
        cli_engines = frozenset(str(e) for e in enable_cli)
        enable_cli = bool(cli_engines)
    else:
        enable_cli = dest_dir is None if enable_cli is None else bool(enable_cli)
        cli_engines = None      # None = 全部照 enable_cli
    skip = os.environ.get("EZ2_USAGE_SKIP") == "1" if skip is None else bool(skip)
    selected = readers or READERS
    probe_fn = probe or probe_engine
    probe_kwargs = dict(probe_kwargs or {})
    probe_timeout_s = max(1, int(probe_kwargs.pop("timeout_s", 60)))
    path_env = probe_kwargs.get("path_env")
    background = []
    for engine in SUPPORTED:
        probe_result = False
        force_write = False
        if skip:
            items = _missing_for_engine(engine, "skip_flag", now)
        else:
            try:
                reader = selected.get(engine)
                if reader is None:
                    value = None
                elif engine == "codex" and selected is READERS:
                    value = reader(now=now)
                elif engine == "agy" and selected is READERS:
                    _agy_cli = enable_cli and (cli_engines is None or "agy" in cli_engines)
                    if _agy_cli and cli_engines is not None:
                        # 單家模式(A 包 r1 中 A1,a seat 21:52):不在右欄這一幀同步跑 agy(最長 90 秒);
                        #   檔不新且上次**嘗試**(不管成敗)超過 30 分 ⇒ 起背景子行程去量,這一幀只讀檔。
                        if not _agy_record_fresh(dest, now):
                            _claim_and_spawn_agy(dest, now)
                        _agy_cli = False
                    value = reader(allow_cli=_agy_cli)
                else:
                    value = reader()
                items = _records(value)
            except Exception as exc:
                items = _missing_for_engine(engine, "collect_exception", now)
                for item in items:
                    item["reason"] += ":%s:%s" % (type(exc).__name__, str(exc)[:80])
            if engine in PROBE_ENGINES and enable_cli and (cli_engines is None or engine in cli_engines) \
                    and not any(item and item.get("status") == "ok" for item in items):
                stored = _probe_record(dest, engine)
                if not _probe_due(stored, now, path_env=path_env):
                    items = []
                elif _daily_probe_count(dest, engine, now) >= DAILY_PROBE_CAP:
                    items = [_daily_cap_record(engine, now)]
                    force_write = True
                elif probe is None:
                    if which_cli(BINS[engine], path_env=path_env):
                        background.append(engine)
                        items = []
                    else:
                        items = [_mark_probe_timing(
                            _cli_missing_record(engine, now=now), now,
                            path_env=path_env)]
                        probe_result = True
                else:
                    try:
                        _increment_daily_probe_count(dest, engine, now)
                        rec = probe_fn(
                            engine, timeout_s=probe_timeout_s, **probe_kwargs)
                        items = [_mark_probe_timing(
                            _validated_probe_result(engine, rec), now,
                            path_env=path_env)]
                        probe_result = True
                    except Exception as exc:
                        items = [_mark_probe_timing(unmeasurable_record(
                            engine, "7d", "shared", "collect_exception",
                            reason="採集例外:%s:%s" % (
                                type(exc).__name__, str(exc)[:80])), now,
                            path_env=path_env)]
                        probe_result = True
        for item in items:
            if item:
                try:
                    if probe_result:
                        _write_probe_result(dest, item, now=now)
                    else:
                        write_record(
                            dest, item, preserve_ok=not force_write)
                except (OSError, ValueError):
                    continue
    _touch_stamp(stamp_dir, now)
    if background:
        try:
            _spawn_probe(
                background, dest, timeout_s=probe_timeout_s,
                path_env=path_env,
                sock=probe_kwargs.get("sock", SOCKET_PREFIX))
        except Exception as exc:
            for engine in background:
                try:
                    write_record(dest, unmeasurable_record(
                        engine, "7d", "shared", "collect_exception",
                        reason="採集例外:%s:%s" % (
                            type(exc).__name__, str(exc)[:80])), preserve_ok=True)
                except (OSError, ValueError):
                    continue
    return True


# ── L5b(缺陷二十六):每家 × 每層級一列;沒值畫「量不到:原因」,不是不畫 ──────────
# 列身份 (engine, window, tier) 集合對同一份層級表是穩定的;變的是態,不是少一列(SPEC-L5b §三)。
GRID = (
    ("claude", "5h", "shared"), ("claude", "7d", "shared"), ("claude", "7d", "fable"),
    ("codex", "5h", "shared"), ("codex", "7d", "shared"),
    # ★ 2026-09-08 the maintainer 直裁:agy 與 grok 位置對調(grok 量得到、agy 卡在信任對話框)。
    ("grok", "7d", "shared"), ("copilot", "7d", "shared"),
    ("agy", "7d", "shared"), ("agy", "7d", "claude_gpt"),
)
ENGINE_LABEL = {"claude": "Claude", "codex": "Codex", "agy": "Agy",
                "copilot": "Copilot", "grok": "Grok"}
#: reason_code 閉集(SPEC-L5b §2.5 八碼 + 本顆加兩碼:probe_pending=探針未接線/尚無來源檔,
#: stale=讀數過期——cap 460d3 09-06 抓到 codex-7d.json 寫 35% 其實是五小時前的觀測,
#: 格式完全正確而過期十七倍,沒有一格會紅;過期讀數不得畫成百分比)。
REASON_CODES = ("collect_exception", "no_interface", "no_cap", "shared_only",
                "probe_failed", "cli_missing", "skip_flag", "throttle_stale",
                "probe_pending", "stale")
# 觀測超過一小時當過期；常數在檔首,供寫入保鮮與渲染共用。


def _tr(tr, key):
    """i18n 鍵缺時回鍵名本身(不回 None、不讓整塊消失):缺鍵要在畫面上看得到。"""
    try:
        v = tr.t(key)
    except Exception:
        return key
    return key if not isinstance(v, str) or v.startswith("!!") else v


def _grid_reason_text(tr, code, hours=None, tried=None, exc=None):
    key = "aib.usage.reason." + code
    text = _tr(tr, key)
    if text == key:
        text = _reason_text(code, tried)
        if code == "collect_exception" and exc:
            text += ":" + exc
    text = text.replace("{hours}", str(hours if hours is not None else "?"))
    text = text.replace("{tried}", ",".join(tried or []) or "-")
    text = text.replace("{exc}", (exc or "")[:40])
    return text


def _row_label(tr, engine, window, tier, tags):
    return "%s %s" % (ENGINE_LABEL.get(engine, engine), _row_label_short(tr, window, tier, tags))


def _row_label_short(tr, window, tier, tags):
    """the maintainer 09-06 15:2x 第十五裁:左方只留 LOGO,品牌名不印 —— 列上只剩「種類」(5小時 / 週 / Fable 週)。
    完整 label 仍留在資料層(機器、尺讀它);渲染器有徽章時用 label_short。"""
    tag = tags.get(window, window)
    if tier == "shared":
        return tag
    return "%s %s" % (_tr(tr, "aib.usage.tier." + tier), tag)


def _missing_row(engine, window, tier, have, now, throttle_wait=False):
    """Name one absent record; the grid iteration remains in ``_grid_rows``."""
    if engine == "claude" and tier != "shared" \
            and (engine, window, "shared") in have:
        code = "shared_only"
    elif throttle_wait:
        code = "throttle_stale"
    else:
        code = "probe_pending"
    return unmeasurable_record(
        engine, window, tier, code, observed_at=_observed_now(now))


def reset_credit_text(tr, rec):
    """the maintainer 09-06 12:2x 裁:每列要列「有沒有使用重置可用」與到期時刻。

    只認紀錄裡的選配欄 ``reset_credit`` = {"available": bool, "expires_at": epoch|None};
    缺欄回空字串(**不畫 ≠ 無**:來源找不到那顆按鈕就不寫欄,不得拿 rollout 的 credits 冒充)。
    """
    credit = rec.get("reset_credit") if isinstance(rec, dict) else None
    if not isinstance(credit, dict) or not isinstance(credit.get("available"), bool):
        return ""
    if not credit["available"]:
        return _tr(tr, "aib.usage.reset_credit.none")
    text = _tr(tr, "aib.usage.reset_credit.available")
    expires = _epoch_s(credit.get("expires_at"))
    if expires is not None:
        text += " %s %s" % (time.strftime("%m/%d %H:%M", time.localtime(expires)),
                            _tr(tr, "aib.usage.reset_credit.until"))
    return text


def _grid_rows(now, tr, records, tags, reset_text_of, error=None,
               throttle_wait=False):
    """把已讀到的紀錄鋪到列網格上;每一列都在,態是 ok 或 unmeasurable。"""
    by_key = {}
    for rec in records:
        by_key[(rec["engine"], rec.get("window"), rec.get("tier") or "shared")] = rec
    rows = []
    for engine, window, tier in GRID:
        rec = by_key.get((engine, window, tier))
        label = _row_label(tr, engine, window, tier, tags)
        row = {"engine": engine, "vendor": engine, "window": window, "tier": tier,
               "label": label, "label_short": _row_label_short(tr, window, tier, tags),
               "tried": [], "source": None,
               "used_pct": None, "pct": None, "resets_at": None, "reset_text": "",
               "reset_credit_text": "",
               "observed_age_s": 0, "reason": None, "reason_code": None,
               "capped": False,
               # the maintainer 09-22:被動家(claude/codex)的 [重驗]=強制重讀來源檔;活探三家仍走 CLI。
               "reprobe": True}
        code = None
        hours = None
        explicit_reason = None
        if error is not None:
            code = "collect_exception"
            row["source"] = "probe:collect_exception"
        elif rec is None:
            missing = _missing_row(
                engine, window, tier, by_key, now, throttle_wait)
            code = missing["reason_code"]
            row["source"] = missing["source"]
            row["observed_age_s"] = _observed_age_s(missing, now)
        else:
            age = _observed_age_s(rec, now)
            row["observed_age_s"] = age
            row["source"] = rec.get("source")
            row["tried"] = list(rec.get("tried") or [])
            if rec.get("status") == "unmeasurable":
                code = valid_reason_code(rec.get("reason_code")) \
                    or "collect_exception"
                if code == "stale":
                    # D38 把過期/歸屬不符的舊 ok 降級成 reason_code=stale 的紀錄(observed_at 留
                    # 上次 ok 的時刻);小時數原本只在「ok 紀錄過老」那條路算,這裡不算就畫
                    # 「過期 ? 小時」(the maintainer 09-06 13:2x 活艙看到的)。同一把尺:age // 3600。
                    hours = age // 3600
                # 探針失敗／過期檔仍可能帶上次％（上限打滿常見）
                _lup = rec.get("last_used_pct")
                if not (isinstance(_lup, int) and not isinstance(_lup, bool)):
                    _lup = rec.get("used_pct")
                if isinstance(_lup, int) and not isinstance(_lup, bool) and 0 <= _lup <= 100:
                    row["last_used_pct"] = _lup
                if rec.get("source") == "probe:daily-cap":
                    explicit_reason = str(rec.get("reason") or "") or None
            elif age > STALE_S:
                # SPEC USAGE-BIND-PROBE-v1 §4:過期分三態,不是一態。
                code = "stale"
                hours = age // 3600
                _state = stale_state_of(rec, now)
                row["stale_state"] = _state
                row["lower_bound_pct"] = stale_lower_bound(rec, _state)
                # 無重置時刻時下界為 None，但仍保留檔內上次％供灰階「上限／最後讀數」顯示
                _lup = rec.get("last_used_pct")
                if not (isinstance(_lup, int) and not isinstance(_lup, bool)):
                    _lup = rec.get("used_pct")
                if isinstance(_lup, int) and not isinstance(_lup, bool) and 0 <= _lup <= 100:
                    row["last_used_pct"] = _lup
                explicit_reason = stale_reason_text(rec, window, now, hours)
            else:
                pct = rec.get("used_pct")
                if isinstance(pct, bool) or not isinstance(pct, int) \
                        or pct < 0 or pct > 100:
                    code = "collect_exception"
                else:
                    row.update({"status": "ok", "used_pct": pct, "pct": pct,
                                "resets_at": rec.get("resets_at"),
                                "reset_text": reset_text_of(rec, pct),
                                "reset_credit_text": reset_credit_text(tr, rec),
                                "capped": pct >= 100})
        if code is not None:
            row.update({"status": "unmeasurable",
                        "reason_code": code,
                        "reason": explicit_reason or _grid_reason_text(
                            tr, code, hours=hours, tried=row["tried"],
                            exc=(type(error).__name__
                                 if error is not None else None)),
                        "source": row["source"] or "probe:none"})
            # the maintainer 09-22:量到過且檔內有重置時刻——過期列也要能顯示何時重置
            # (Claude 週額 100% 常見:讀數過期但 resets_at 仍在未來)。
            if rec is not None and rec.get("resets_at") is not None:
                row["resets_at"] = rec.get("resets_at")
                _hint = row.get("last_used_pct")
                if not (isinstance(_hint, int) and not isinstance(_hint, bool)):
                    _hint = rec.get("used_pct")
                if not (isinstance(_hint, int) and not isinstance(_hint, bool)):
                    _hint = 0
                row["reset_text"] = reset_text_of(rec, _hint)
                if isinstance(_hint, int) and not isinstance(_hint, bool) and _hint >= 100:
                    row["capped"] = True
        rows.append(row)
    return rows


def rows_when_collect_failed(now, tr, exc, dest_dir=None):
    """採集丟例外時給採集端塞的 dict:整個列網格都在,每列 unmeasurable/collect_exception(§2.4)。"""
    tags = {"5h": _tr(tr, "aib.usage.five_hour"), "7d": _tr(tr, "aib.usage.weekly")}
    return {"title": _tr(tr, "aib.usage.block"),
            "tz_text": _tr(tr, "aib.usage.tz_label"),
            "now_text": now_text(now, _tr(tr, "aib.usage.weekdays").split(",")),
            "unmeasurable": _tr(tr, "aib.usage.unmeasurable"),
            "reprobe": _tr(tr, "aib.usage.reprobe"),
            "rows": _grid_rows(now, tr, [], tags, lambda r, p: "", error=exc)}


def _load_one(path, engine, window, tier="shared"):
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA \
            or rec.get("engine") != engine or rec.get("window") != window:
        return None
    rec_tier = rec.get("tier") or tier
    if rec_tier != tier:
        return None
    status = rec.get("status") or "ok"
    if status == "ok":
        pct = _int_pct(rec.get("used_pct"))
        if pct is None:
            return None
        rec["used_pct"] = pct
        rec["resets_at"] = _epoch_s(rec.get("resets_at"))
        rec["reason"] = None
        rec["reason_code"] = None
        rec["tried"] = list(rec.get("tried") or [])
    elif status == "unmeasurable":
        if valid_reason_code(rec.get("reason_code")) is None:
            return None
        rec["used_pct"] = None
        rec["resets_at"] = None
        rec["reason"] = str(rec.get("reason") or _reason_text(
            rec["reason_code"], rec.get("tried")))
        rec["tried"] = list(rec.get("tried") or [])
    else:
        return None
    rec["tier"] = rec_tier
    rec["status"] = status
    rec.setdefault("source", path)
    rec.setdefault("observed_at", _mtime_utc_z(path))
    rec["_path"] = path
    rec["_legacy"] = False
    return rec


def load(dest_dir=None):
    """Load new three-part files, then legacy shared aliases without duplicates."""
    dest = usage_dir(dest_dir)
    if dest is None:
        return []
    out = []
    for engine, window, tier in GRID:
        rec = _load_one(record_path(dest, engine, window, tier),
                        engine, window, tier)
        legacy = False
        if rec is None and tier == "shared":
            rec = _load_one(os.path.join(dest, "%s-%s.json" % (engine, window)),
                            engine, window, tier)
            legacy = rec is not None
        if rec is None and tier == "shared" and window == "7d":
            rec = _load_one(os.path.join(dest, engine + ".json"),
                            engine, window, tier)
            legacy = rec is not None
        if rec:
            rec["_legacy"] = legacy
            out.append(rec)
    return out


def _observed_age_s(rec, now):
    raw = rec.get("observed_at")
    observed = None
    # the maintainer 09-22:ok_record 會寫 epoch float;只認 ISO 會把年齡算成 0 → 達上限列假新鮮、過期路徑永不觸發。
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        observed = float(raw)
    elif isinstance(raw, str):
        try:
            observed = datetime.datetime.strptime(
                raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            pass
    if observed is None and rec.get("_path"):
        try:
            observed = os.path.getmtime(rec["_path"])
        except OSError:
            pass
    return max(0, int(now - observed)) if observed is not None else 0


def relative_reset(resets_at, now):
    sec = max(0, int(resets_at - now))
    hours, rem = divmod(sec, 3600)
    minutes = rem // 60
    if hours and minutes:
        return "%dh%dm" % (hours, minutes)
    return "%dh" % hours if hours else "%dm" % minutes


def now_text(now, weekdays, tz_label=""):
    """the maintainer 09-06 14:5x 第十三裁:用量標題下第一行=現在的年月日時分秒(週幾)。渲染器不讀時鐘,這裡排好。
    第十六裁(15:4x):尾巴加「 - 台灣台北(UTC+8)」(i18n aib.usage.tz_label;時刻本來就以 TAIPEI 算)。
    第十七裁(the maintainer 2026-09-10 15:0x):**兩行** —— 區域在上、日期時間在下;本函式只回日期時間那行,
    區域那行走 block["tz_text"](渲染器各排一行,不再用「 - 」黏在同一行)。tz_label 參數保留給舊呼叫端,不再拼進字串。"""
    dt = datetime.datetime.fromtimestamp(now, TAIPEI)
    names = weekdays if len(weekdays) == 7 else list("一二三四五六日")
    return "%04d-%02d-%02d %02d:%02d:%02d(%s)" % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, names[dt.weekday()])


def absolute_reset(resets_at, weekdays):
    dt = datetime.datetime.fromtimestamp(resets_at, TAIPEI)
    names = weekdays if len(weekdays) == 7 else list("一二三四五六日")
    # the maintainer 09-06 13:2x 裁:重置時刻要帶月日,像「09/06(日) 15:00」。
    return "%02d/%02d(%s) %02d:%02d" % (dt.month, dt.day, names[dt.weekday()], dt.hour, dt.minute)


WINDOW_SECONDS = {"5h": 5 * 3600, "7d": 7 * 24 * 3600}


def next_reset_after(resets_at, window, now):
    """重置時刻已過時,用週期往前推下一個重置點;推不出來回 None。

    ★ 週期表推不到的窗一律回 None,**不猜**:一個編出來的倒數比沒有倒數更糟。
    """
    period = WINDOW_SECONDS.get(window)
    if not period:
        return None
    # ★ 第二審(Gemini)兩點,都收:
    #   (a) 型別守衛 —— `resets_at` 從 JSON 讀出來可能是字串或 None,
    #       原本直接拿去比大小會丟 TypeError 而炸掉整列的渲染。
    #       **讀不出數字就回 None**,不猜。
    #   (b) 改成 O(1) 算式 —— 原本的 while 迴圈在 period 為正時一定會停,
    #       所以「死迴圈」那句是誤報;但停機很久之後要跑上千圈是真的,
    #       而除法版本一樣處理得了邊界(下面那一行 +1 就是邊界那一格)。
    try:
        base = float(resets_at)
    except (TypeError, ValueError):
        return None
    if base != base or base in (float("inf"), float("-inf")):   # NaN / inf
        return None
    if base > now:
        return base
    steps = int((now - base) // period) + 1
    return base + steps * period


def stale_state_of(rec, now):
    """過期紀錄的三態(SPEC USAGE-BIND-PROBE-v1 §4)。

    `resets_at` 是**絕對時刻,它不會過期**;過期的只有「用了幾 %」——
    舊碼把整列一起降級成量不到,連那個不會過期的倒數也一起丟掉。

    甲 counting     有重置時刻且還沒到 ⇒ 倒數照跑,百分比是**下界**(用量只會往上)
    乙 rolled_over  重置時刻已過 ⇒ 窗已翻頁,舊百分比**確定是錯的**(用量歸零),一律不採用
    丙 no_reset     沒有重置時刻 ⇒ 倒數**不得編造**
    """
    ra = rec.get("resets_at")
    if ra is None:
        return "no_reset"
    return "counting" if ra > now else "rolled_over"


def stale_lower_bound(rec, state):
    """過期百分比只在甲態有意義,而且它是下界不是現值;乙丙一律回 None。"""
    if state != "counting":
        return None
    pct = rec.get("used_pct")
    if isinstance(pct, bool) or not isinstance(pct, int) or pct < 0 or pct > 100:
        return None
    return pct


def stale_reason_text(rec, window, now, hours):
    """三態各自的字樣。三個字樣必須分得開 —— 共用字樣就是缺陷。"""
    state = stale_state_of(rec, now)
    lb = stale_lower_bound(rec, state)
    if state == "counting":
        head = "量於 %sh 前" % hours
        if lb is not None:
            head += "(至少 %d%%)" % lb
        return "%s · 重置倒數 %s" % (head, relative_reset(rec["resets_at"], now))
    if state == "rolled_over":
        nxt = next_reset_after(rec.get("resets_at"), window, now)
        tail = ("下次重置倒數 %s" % relative_reset(nxt, now)) if nxt \
            else "推不出下次重置時刻"
        return "資訊過期(舊讀數不採用) · %s" % tail
    head = "量於 %sh 前" % hours
    if lb is not None:
        head += "(至少 %d%%)" % lb
    return "%s · 無重置時刻" % head


# --- 准入:讓那個數字有消費者(2026-09-08,the maintainer 直裁順序 4)-------------------
#
# ## 它解的是什麼
#
# 2026-09-08 現查:`used_pct` 在渲染以外只有 ctx 那兩支用到,而它們用的是
# **上下文用量**不是帳號額度 —— **帳號額度那份紀錄除了面板沒有任何消費者**。
# 當天實例:codex/7d = 100%、grok = 97%,而系統不會因此少派一件工;
# 同一天下午 `codex-review` 因額度用盡叫不動、四席 dm 卡住,
# **面板早就有那個數字,但它沒有接到任何決策上。**
#
# ## ★★ 危險方向只有一個(SPEC USAGE-BIND-PROBE-v1 §3 同一條)
#
# 額度用完的 CLI 常常根本不回答 ⇒ **「額度不夠」與「探不到」會給出同一個觀測值**。
# 若讓「探不到」也擋人,就會長出一個**會自我維持的錯誤狀態**:
#
#     探針壞掉 → 判成額度滿 → 擋住開席 → 沒人開得了席去修探針
#
# 所以本函式**只在量到的證據上擋人**:`status=ok`、值合法、而且**新鮮**。
# 量不到 / 過期 / 沒有紀錄 ⇒ 一律 `unknown` **放行**,但要說得出來。
# 「說不出來就放行」與「說得出來才擋」是同一句話的兩半,缺一半都不對。
USAGE_BLOCK_PCT = 100          # 只擋「確定沒有額度了」,不擋「快沒了」
BLOCKING_WINDOW = "7d"         # 只有週窗准擋人;5h 用完幾小時就回來


def usage_admission(engine, now, dest_dir=None):
    """回 (verdict, 說明)。verdict ∈ ok / exhausted / unknown。

    ★ `exhausted` 是**唯一**會擋人的答案,而它要三件同時成立:
      量到(status=ok)、值合法、且觀測**沒有過期**(<= STALE_S)。
    ★ 三個 verdict 的說明字樣必須分得開 —— 共用字樣的話,
      「我確定你沒額度」與「我不知道」在版面上會是同一句話。
    """
    # ★ 第二審(Gemini)抓到的真問題:原本對同一家取**所有窗的最大 pct**,
    #   於是 5h 窗滿(那是每天都會發生的正常節奏)就會擋掉開席,而 7d 還很空。
    #   ⇒ 只有 **7d 窗**准擋人:它用完代表這一家好幾天都不能用,開席確實是白工;
    #     5h 用完幾個小時就回來,擋它的代價遠大於省下的白工。
    #   ★ 這是**選出來的政策不是實作意外**,所以底下有一格臂釘著它。
    fresh = None
    for rec in load(dest_dir):
        if rec.get("engine") != engine:
            continue
        if rec.get("window") != BLOCKING_WINDOW:
            continue
        if rec.get("status") != "ok":
            continue
        pct = rec.get("used_pct")
        if isinstance(pct, bool) or not isinstance(pct, int) \
                or pct < 0 or pct > 100:
            continue
        age = _observed_age_s(rec, now)
        if age is None or age > STALE_S:
            continue
        if fresh is None or pct > fresh[0]:
            fresh = (pct, rec, age)
    if fresh is None:
        return "unknown", "%s 沒有新鮮的用量讀數(量不到/過期/沒有紀錄)—— 不擋" % engine
    pct, rec, age = fresh
    if pct >= USAGE_BLOCK_PCT:
        tail = ""
        ra = rec.get("resets_at")
        if ra is not None:
            tail = ",重置倒數 %s" % relative_reset(ra, now)
        return "exhausted", "%s %s 窗用量 %d%%(%d 秒前量到)%s" % (
            engine, rec.get("window") or "?", pct, int(age), tail)
    return "ok", "%s 用量 %d%%(%d 秒前量到)" % (engine, pct, int(age))


def rows_for_render(now, tr, dest_dir=None):
    """給採集端塞進 render_data['usage'] 的純資料;無列回 None。

    時刻字串在這裡排好,渲染器不讀時鐘(黃金幀純度)。
    """
    # L5b:永不回 None。列網格恆在(§2.1),沒值的列畫「量不到:原因」(§2.2);
    # 共用窗的標籤不掛最新席的模型名(§1.4 那個謊:共用 72% 被標成「Fable 5.1 週」)。
    records = load(dest_dir)
    title = _tr(tr, "aib.usage.block")
    reset = _tr(tr, "aib.usage.reset")
    capped_l = _tr(tr, "aib.usage.capped")
    resume = _tr(tr, "aib.usage.resume_after")
    tags = {"5h": _tr(tr, "aib.usage.five_hour"),
            "7d": _tr(tr, "aib.usage.weekly")}
    weekdays = _tr(tr, "aib.usage.weekdays").split(",")
    stamp_dir = usage_dir(dest_dir) or ""
    stamped = _read_stamp(stamp_dir)
    throttle_wait = _stamp_is_throttled(stamped, now, THROTTLE_S) \
        and not _stamp_is_same_frame(stamped, now)

    def reset_text_of(rec, pct):
        if rec.get("resets_at") is None:
            return ""
        if pct >= 100:
            return "%s · %s %s" % (
                capped_l, relative_reset(rec["resets_at"], now), resume)
        return "%s %s" % (
            absolute_reset(rec["resets_at"], weekdays), reset)
    return {"title": title,
            "tz_text": _tr(tr, "aib.usage.tz_label"),
            "now_text": now_text(now, weekdays),
            "unmeasurable": _tr(tr, "aib.usage.unmeasurable"),
            "reprobe": _tr(tr, "aib.usage.reprobe"),
            "rows": _grid_rows(
                now, tr, records, tags, reset_text_of,
                throttle_wait=throttle_wait)}


def dry_run_check():
    expected = {
        "default": True, "aihub": True, "aihub-x": True, "aib-x": True,
        SOCKET_PREFIX: False, SOCKET_PREFIX + "-123": False,
    }
    rows = [{"socket": name, "forbidden": socket_forbidden(name)}
            for name in expected]
    return {"ok": all(row["forbidden"] == expected[row["socket"]]
                      for row in rows), "rows": rows}


def cli_main(argv=None):
    parser = argparse.ArgumentParser(description="isolated usage CLI probes")
    parser.add_argument("--engines", default="copilot,agy,grok")
    parser.add_argument("--dest", default="")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--socket", default=SOCKET_PREFIX)
    parser.add_argument("--lock-path", default="")
    parser.add_argument("--lock-token", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agy-print-refresh", action="store_true",
                        help="v0.70.0 #14:右欄背景起的 agy 單家量測(headless -p /usage),寫進 --dest")
    parser.add_argument("--reprobe", default="",
                        help="the maintainer 13:5x 裁:[重驗] —— 對一家起一次探針後立刻返回(印 JSON)")
    args = parser.parse_args(argv)
    if args.agy_print_refresh:
        if not args.dest:
            return 2
        return 0 if agy_print_refresh(os.path.abspath(args.dest)) else 1
    if args.reprobe:
        result = reprobe(args.reprobe, dest_dir=(args.dest or None))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in ("started", "daily_cap", "lock_busy", "refreshed") else 2
    if args.dry_run:
        result = dry_run_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    dest = os.path.abspath(args.dest or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        ".local-usage"))
    engines = [item.strip() for item in args.engines.split(",") if item.strip()]
    if any(engine not in PROBE_ENGINES for engine in engines):
        return 2
    worker_pid = os.getpid()
    worker_sock = "%s-%s" % (args.socket, worker_pid)
    previous_term = signal.getsignal(signal.SIGTERM)

    def stop_worker(_signum, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop_worker)
    try:
        for engine in engines:
            now = time.time()
            if _daily_probe_count(dest, engine, now) >= DAILY_PROBE_CAP:
                rec = _daily_cap_record(engine, now)
                path = write_record(dest, rec)
            else:
                _increment_daily_probe_count(dest, engine, now)
                session_name = "probe-%s-%s" % (worker_pid, engine)
                result = probe_engine(
                    engine, timeout_s=args.timeout, sock=worker_sock,
                    session_name=session_name)
                rec = _mark_probe_timing(
                    _validated_probe_result(engine, result), now)
                path = _write_probe_result(dest, rec)
            print(json.dumps({
                "engine": rec["engine"], "path": path,
                "status": rec["status"], "used_pct": rec["used_pct"],
                "reason_code": rec["reason_code"], "tried": rec["tried"],
            }, ensure_ascii=False))
        return 0
    finally:
        if args.lock_path and args.lock_token:
            _release_probe_lock(args.lock_path, args.lock_token)
        signal.signal(signal.SIGTERM, previous_term)
        print("D38_PROBE_WORKER_DONE")


if __name__ == "__main__":
    raise SystemExit(cli_main())
