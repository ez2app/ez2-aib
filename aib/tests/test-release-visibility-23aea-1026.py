#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fault-focused tests for preflight, process identity and release evidence."""
import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aib/src/cockpit"))
sys.path.insert(0, str(ROOT / "aib/tools/release"))
import release_preflight as PF
import release_status as RS
import update_status as US
import version_probe as VP


class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "aib").mkdir()
        (self.repo / "aib/VERSION").write_text("1.2.3\n")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "ez2-aib"\nversion = "1.2.3"\n')
        for name in ["CHANGELOG.md", "CHANGELOG.zh-TW.md", "CHANGELOG.ja.md", "CHANGELOG.ko.md"]:
            (self.repo / name).write_text("# Notes\n\n## 1.2.3\n\nDone.\n")

    def wheel(self, name="ok.whl", ver="1.2.3", extra=None):
        path = self.repo / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("aib/VERSION", ver)
            archive.writestr("ez2_aib-1.2.3.dist-info/METADATA", "Name: ez2-aib\nVersion: %s\n" % ver)
            if extra:
                archive.writestr(extra, "not part of the package")
        return path

    def test_clean_metadata_and_wheel_are_read_only(self):
        wheel = self.wheel()
        before = {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}
        self.assertTrue(PF.collect(self.repo, [wheel])["ok"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()})

    def test_runtime_mismatch_and_one_stale_translation_fail(self):
        (self.repo / "aib/VERSION").write_text("1.2.2")
        (self.repo / "CHANGELOG.ko.md").write_text("## 1.2.2\n")
        result = PF.collect(self.repo)
        self.assertFalse(result["ok"])
        self.assertEqual({r["check"] for r in result["checks"] if not r["ok"]}, {"runtime-version", "CHANGELOG.ko.md"})

    def test_tool_table_version_cannot_substitute_for_project_version(self):
        (self.repo / "pyproject.toml").write_text('[project]\nname="ez2-aib"\n[tool.fake]\nversion="1.2.3"\n')
        self.assertFalse(PF.collect(self.repo)["ok"])

    def test_archive_metadata_wrong_version_and_appledouble_fail(self):
        for path in [self.wheel("wrong.whl", "1.2.2"), self.wheel("apple.whl", extra="aib/._VERSION")]:
            self.assertFalse(PF.collect(self.repo, [path])["ok"])

    def test_tar_traversal_is_rejected_without_extracting(self):
        path = self.repo / "bad.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            entry = tarfile.TarInfo("../outside")
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"bad"))
        self.assertFalse(PF.collect(self.repo, [path])["ok"])
        self.assertFalse((self.repo.parent / "outside").exists())

    def test_ci_preflight_runs_before_install_and_long_suite(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        fast = workflow.index("- name: fast release metadata preflight")
        self.assertLess(fast, workflow.index("- name: install (pip)"))
        self.assertLess(fast, workflow.index("- name: core safety tests"))


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / "checkout"
        (self.repo / "bin").mkdir(parents=True)
        (self.repo / "aib").mkdir()
        (self.repo / "aib/VERSION").write_text("1.2.3")
        (self.repo / "bin/aib").write_text("placeholder")
        self.launcher = self.home / "launcher"
        self.launcher.symlink_to(self.repo / "bin/aib")
        self.state = self.home / ".ez2-aib"
        self.room = self.state / "plugins/captain-room"
        self.room.mkdir(parents=True)
        (self.room / "aib-ext.json").write_text(json.dumps(dict(version="1.2.4", token="SHOULD-NOT-LEAK")))
        self.record = self.state / "state/captain-room/server.json"
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps(dict(pid=12345, room_version="1.2.3", token="SHOULD-NOT-LEAK")))

    def probe(self, command):
        with patch.object(VP, "run", return_value=command):
            return VP.collect(self.home, self.state, self.launcher)

    def test_reused_pid_never_reports_room_running_version(self):
        data = self.probe("/usr/bin/another-process serve")
        self.assertEqual(data["room"]["state"], "stale-record")
        self.assertIsNone(data["room"]["reported_start_version"])
        self.assertNotIn("SHOULD-NOT-LEAK", json.dumps(data))

    def test_real_script_identity_keeps_installed_and_startup_distinct(self):
        data = self.probe("/usr/bin/python3 %s serve --port 8770" % (self.room / "captain_room.py"))
        self.assertEqual(data["room"]["state"], "process-verified")
        self.assertFalse(data["room"]["versions_match"])
        self.assertEqual(data["room"]["health"], "unmeasured")
        self.assertIsNone(data["room"]["runtime_core_version"])
        US.validate_probe(data)

    def test_missing_startup_version_is_not_filled_from_disk(self):
        self.record.write_text(json.dumps(dict(pid=12345)))
        data = self.probe("python3 %s serve" % (self.room / "captain_room.py"))
        self.assertIsNone(data["room"]["reported_start_version"])
        self.assertIsNone(data["room"]["versions_match"])

    def test_unrelated_program_with_script_as_an_argument_is_stale(self):
        data = self.probe("another-program %s serve" % (self.room / "captain_room.py"))
        self.assertEqual(data["room"]["state"], "stale-record")

    def test_macos_python_app_executable_is_recognised(self):
        data = self.probe("/some/Python.app/Contents/MacOS/Python -u %s serve" % (self.room / "captain_room.py"))
        self.assertEqual(data["room"]["state"], "process-verified")

    def test_remote_extra_nested_fields_are_not_echoed(self):
        data = self.probe("other-process")
        data["core"]["token"] = "SHOULD-NOT-LEAK"
        reply = subprocess.CompletedProcess([], 0, json.dumps(data), "")
        with patch.object(US.subprocess, "run", return_value=reply):
            result = US.remote("known-host")
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("SHOULD-NOT-LEAK", json.dumps(result))

    def test_verified_ssh_and_invalid_alias(self):
        for name in ["-oProxyCommand=bad", "host;touch bad", "host\nsecond", "host/../../bad"]:
            self.assertFalse(US.valid_host(name))
        data = self.probe("other-process")
        reply = subprocess.CompletedProcess([], 0, json.dumps(data), "")
        with patch.object(US.subprocess, "run", return_value=reply) as call:
            self.assertEqual(US.remote("known-host")["state"], "observed")
            self.assertIn("StrictHostKeyChecking=yes", call.call_args[0][0])
            self.assertIn("BatchMode=yes", call.call_args[0][0])


class Progress(unittest.TestCase):
    def fixture(self, verify=True, include=True):
        head, tag = "a" * 40, "b" * 40
        def api(path):
            if "/pulls/" in path:
                return dict(merged=True, state="closed", head=dict(sha=head)), "observed"
            if "/check-runs?" in path:
                return dict(check_runs=[dict(status="completed", conclusion="success")]), "observed"
            if "/git/ref/" in path:
                return dict(object=dict(type="commit", sha=tag)), "observed"
            if "/releases/tags/" in path:
                return dict(draft=False), "observed"
            if "/compare/" in path:
                return dict(status="ahead" if include else "diverged"), "observed"
            if "/workflows/" in path:
                return dict(workflow_runs=[dict(id=99, head_sha=tag)]), "observed"
            if "/jobs?" in path:
                names = ["precheck (ubuntu-latest)", "precheck (macos-latest)", "pypi"]
                if verify:
                    names += ["verify (ubuntu-latest)", "verify (macos-latest)"]
                return dict(jobs=[dict(name=n, status="completed", conclusion="success") for n in names]), "observed"
            raise AssertionError(path)
        return api

    def devices(self, hosts):
        return dict(targets=[dict(state="observed", core=dict(installed_version="1.2.3"))])

    def test_all_real_stages_required_for_verified_release(self):
        result = RS.collect("owner/repo", "1.2.3", 1, api=self.fixture(), index=lambda v: "passed", devices=self.devices)
        self.assertTrue(result["release_verified_and_installed"])
        for verify, pypi in [(False, "passed"), (True, "pending"), (True, "unavailable")]:
            result = RS.collect("owner/repo", "1.2.3", 1, api=self.fixture(verify), index=lambda v: pypi, devices=self.devices)
            self.assertFalse(result["release_verified_and_installed"])

    def test_wrong_candidate_or_old_device_prevents_completion(self):
        result = RS.collect("owner/repo", "1.2.3", 1, api=self.fixture(include=False), index=lambda v: "passed", devices=self.devices)
        self.assertFalse(result["release_verified_and_installed"])
        self.assertIn(dict(stage="candidate-included", state="failed", source="github-ancestry"), result["stages"])
        result = RS.collect("owner/repo", "1.2.4", 1, api=self.fixture(), index=lambda v: "passed", devices=self.devices)
        self.assertFalse(result["release_verified_and_installed"])

    def test_pypi_404_is_pending_but_network_failure_is_unmeasured(self):
        with patch.object(RS.urllib.request, "urlopen", side_effect=RS.urllib.error.HTTPError("url", 404, "missing", {}, None)):
            self.assertEqual(RS.pypi("1.2.3"), "pending")
        with patch.object(RS.urllib.request, "urlopen", side_effect=OSError("PRIVATE ERROR DETAIL")):
            self.assertEqual(RS.pypi("1.2.3"), "unavailable")

    def test_cli_json_works_without_header_or_network_side_effects(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(ROOT / "bin/aib"), "ship", "preflight", "--repo", str(ROOT), "--json"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
