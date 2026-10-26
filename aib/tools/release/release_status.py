#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Observe release stages; no tags, approvals, installs or credentials are changed."""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/cockpit"))
import i18n_lib
import update_status


def T(key):
    return i18n_lib.t("aib.release_status." + key.replace("-", "_"), None, locale=i18n_lib.resolve_locale())


def github(path):
    try:
        p = subprocess.run(["gh", "api", path], stdin=subprocess.DEVNULL, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if p.returncode:
            return None, "absent" if "HTTP 404" in p.stderr else "unavailable"
        return json.loads(p.stdout), "observed"
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, "unavailable"


def pypi(version):
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/ez2-aib/%s/json" % version, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return "unavailable"
        data = json.loads(raw)
        return "passed" if data.get("info", {}).get("version") == version else "unavailable"
    except urllib.error.HTTPError as error:
        return "pending" if error.code == 404 else "unavailable"
    except (OSError, ValueError):
        return "unavailable"


def checks_state(checks):
    if not isinstance(checks, list) or not checks:
        return "unavailable", 0, 0
    passed = sum(c.get("status") == "completed" and c.get("conclusion") == "success" for c in checks)
    if any(c.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required", "startup_failure") for c in checks):
        state = "failed"
    elif passed == len(checks):
        state = "passed"
    else:
        state = "pending"
    return state, passed, len(checks)


def collect(repo, version, pr=None, hosts=(), api=github, index=pypi, devices=None):
    stages = []
    prefix = "repos/" + repo
    head = None
    if pr is not None:
        pull, state = api(prefix + "/pulls/" + str(pr))
        if state != "observed" or not isinstance(pull, dict):
            stages.append(dict(stage="candidate-ci", state="unavailable", source="github"))
        else:
            head = pull.get("head", {}).get("sha", "")
            checks, found = api(prefix + "/commits/" + head + "/check-runs?per_page=100")
            cs, passed, total = checks_state(checks.get("check_runs") if found == "observed" and isinstance(checks, dict) else None)
            stages.append(dict(stage="candidate-ci", state=cs, passed=passed, total=total, source="github-head-checks"))
            stages.append(dict(stage="merge", state="passed" if pull.get("merged") is True else
                               "failed" if pull.get("state") == "closed" else "pending", source="github"))
    tag, ts = api(prefix + "/git/ref/tags/v" + version)
    sha = None
    if ts == "observed" and isinstance(tag, dict):
        obj = tag.get("object", {})
        for _ in range(3):
            if obj.get("type") == "commit" and re.fullmatch(r"[0-9a-f]{40}", obj.get("sha", "")):
                sha = obj["sha"]
                break
            if obj.get("type") != "tag" or not re.fullmatch(r"[0-9a-f]{40}", obj.get("sha", "")):
                break
            nested, ns = api(prefix + "/git/tags/" + obj["sha"])
            if ns != "observed" or not isinstance(nested, dict):
                break
            obj = nested.get("object", {})
    release, rs = api(prefix + "/releases/tags/v" + version)
    published = rs == "observed" and isinstance(release, dict) and release.get("draft") is False
    stages.append(dict(stage="git-release", state="passed" if sha and published else
                       "pending" if ts == "absent" or rs == "absent" else "unavailable", source="github", sha=sha))
    if head and sha:
        lineage, ls = api(prefix + "/compare/" + head + "..." + sha)
        included = ls == "observed" and isinstance(lineage, dict) and lineage.get("status") in ("ahead", "identical")
        stages.append(dict(stage="candidate-included", state="passed" if included else
                           "failed" if ls == "observed" else "unavailable", source="github-ancestry"))
    precheck, upload, verified = "pending", "pending", "pending"
    if sha:
        runs, state = api(prefix + "/actions/workflows/release.yml/runs?per_page=30")
        if state == "observed" and isinstance(runs, dict):
            matches = [r for r in runs.get("workflow_runs", []) if r.get("head_sha") == sha]
            if matches:
                run = matches[0]
                jobs, js = api(prefix + "/actions/runs/%s/jobs?per_page=100" % run["id"])
                if js == "observed" and isinstance(jobs, dict):
                    rows = jobs.get("jobs", [])
                    def group(names):
                        selected = [j for j in rows if j.get("name") in names]
                        if {j.get("name") for j in selected} != names:
                            return "pending"
                        return checks_state(selected)[0]
                    precheck = group({"precheck (ubuntu-latest)", "precheck (macos-latest)"})
                    upload = group({"pypi"})
                    verified = group({"verify (ubuntu-latest)", "verify (macos-latest)"})
                    if run.get("conclusion") in ("failure", "cancelled", "timed_out") and verified != "passed":
                        verified = "failed"
                else:
                    precheck = upload = verified = "unavailable"
        else:
            precheck = upload = verified = "unavailable"
    stages.extend([dict(stage="package-precheck", state=precheck, source="release-workflow"),
                   dict(stage="upload", state=upload, source="release-workflow"),
                   dict(stage="pypi", state=index(version), source="pypi-index"),
                   dict(stage="install-upgrade-verification", state=verified, source="release-workflow")])
    targets = (devices or update_status.collect)(hosts)
    matches = bool(targets["targets"]) and all(t.get("state") == "observed" and t.get("core", {}).get("installed_version") == version
                  for t in targets["targets"])
    stages.append(dict(stage="target-installations", state="passed" if matches else
                       "unavailable" if any(t.get("state") != "observed" for t in targets["targets"]) else "pending",
                       source="version-probes"))
    complete = all(s["state"] == "passed" for s in stages)
    return dict(schema="aib.release-status/v1", observed_at=int(time.time()), version=version,
                release_verified_and_installed=complete, stages=stages, targets=targets["targets"],
                boundary="target-installations checks installed core versions; room runtime/health are separate probe fields")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aib ship status", description=T("description"))
    parser.add_argument("--github", required=True, help="owner/repository")
    parser.add_argument("--version", required=True)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", args.github):
        parser.error("invalid GitHub repository")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version) or (args.pr is not None and args.pr <= 0):
        parser.error("invalid version or PR")
    if len(args.host) > 10 or any(not update_status.valid_host(h) for h in args.host):
        parser.error("invalid SSH aliases")
    result = collect(args.github, args.version, args.pr, args.host)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(T("title") + " " + args.version)
        for stage in result["stages"]:
            count = " (%s/%s)" % (stage["passed"], stage["total"]) if "total" in stage else ""
            print(T(stage["stage"]) + " | " + T(stage["state"]) + count)
        print(T("boundary"))
    return 1 if any(s["state"] == "failed" for s in result["stages"]) else 3 if any(
        s["state"] == "unavailable" for s in result["stages"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
