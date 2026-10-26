#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only, bounded checks before building or starting the full release suite."""
import argparse
import email.parser
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

VERSION = re.compile(r"\d+\.\d+\.\d+\Z")
MAX_ENTRY = 4 * 1024 * 1024


def read(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_ENTRY + 1)
    if len(data) > MAX_ENTRY:
        raise ValueError("file-too-large")
    return data.decode("utf-8")


def inspect_artifact(path, expected):
    """Inspect metadata without extracting or executing the package."""
    values = {}
    names = set()
    total = 0

    def accept(name, size, loader, regular=True):
        nonlocal total
        total += 1
        if total > 20000:
            raise ValueError("too-many-entries")
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith("/") or "\\" in name or ".." in parts
                or any(p.startswith("._") or p == "__MACOSX" for p in parts)):
            raise ValueError("unsafe-or-macos-metadata-entry")
        if name in names:
            raise ValueError("duplicate-entry")
        names.add(name)
        wanted = name.endswith(".dist-info/METADATA") or name.endswith("/PKG-INFO") or name.endswith("aib/VERSION")
        if wanted:
            if not regular or size > MAX_ENTRY:
                raise ValueError("invalid-metadata-entry")
            values[name] = loader().decode("utf-8")

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                mode = info.external_attr >> 16
                if (mode & 0o170000) == 0o120000:
                    raise ValueError("archive-link")
                accept(info.filename, info.file_size, lambda i=info: archive.read(i))
    else:
        with tarfile.open(path, "r:*") as archive:
            for info in archive:
                if not (info.isfile() or info.isdir()):
                    raise ValueError("archive-link-or-special-file")
                accept(info.name, info.size, lambda i=info: archive.extractfile(i).read(MAX_ENTRY + 1), info.isfile())
    metadata = [email.parser.Parser().parsestr(text) for name, text in values.items()
                if name.endswith("/PKG-INFO") or name.endswith(".dist-info/METADATA")]
    runtime = [text.strip() for name, text in values.items() if name.endswith("aib/VERSION")]
    if not metadata or not runtime or any(v != expected for v in runtime):
        raise ValueError("missing-or-mismatched-package-version")
    for item in metadata:
        if item.get("Name", "").replace("_", "-").lower() != "ez2-aib" or item.get("Version") != expected:
            raise ValueError("mismatched-package-metadata")
    return dict(entries=total, version=expected)


def collect(repo, artifacts=(), plugins=()):
    repo = Path(repo)
    checks = []

    def check(name, action):
        try:
            value = action()
            checks.append(dict(check=name, ok=True, value=value))
            return value
        except (OSError, ValueError, KeyError, UnicodeError, tarfile.TarError, zipfile.BadZipFile):
            checks.append(dict(check=name, ok=False, why="missing-invalid-or-mismatched"))
            return None

    def project_version():
        # Match the project table, not a dependency or tool table's version.
        text = read(repo / "pyproject.toml")
        table = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
        match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', table.group(1), re.M) if table else None
        if not match or not VERSION.fullmatch(match.group(1)):
            raise ValueError("invalid-version")
        return match.group(1)

    version = check("project-version", project_version)

    def equal(value):
        if version is None or value != version:
            raise ValueError("version-mismatch")
        return value

    check("runtime-version", lambda: equal(read(repo / "aib/VERSION").strip()))
    for name in ("CHANGELOG.md", "CHANGELOG.zh-TW.md", "CHANGELOG.ja.md", "CHANGELOG.ko.md"):
        def notes(n=name):
            match = re.search(r"(?m)^## (\S+)", read(repo / n))
            return equal(match.group(1) if match else None)
        check(name, notes)
    for n, artifact in enumerate(artifacts, 1):
        check("artifact-%d" % n, lambda p=artifact: inspect_artifact(p, version))
    if plugins:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/cockpit"))
        import aib_ext
        for n, plugin in enumerate(plugins, 1):
            def inspect_plugin(p=plugin):
                manifest, errors = aib_ext.inspect(Path(p), allow_unsigned=False)
                if errors or not aib_ext.signed_ok(manifest):
                    raise ValueError("unsigned-or-invalid-plugin")
                return dict(version=manifest["version"], signed=True)
            check("signed-plugin-%d" % n, inspect_plugin)
    return dict(schema="aib.release-preflight/v1", ok=all(c["ok"] for c in checks),
                version=version, checks=checks, scope="version-and-package-integrity; full-tests-still-required")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--plugin", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = collect(args.repo, args.artifact, args.plugin)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for row in result["checks"]:
            print(("PASS" if row["ok"] else "FAIL") + " " + row["check"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
