# Release checks and version observations

These commands observe existing installations and release evidence. They do not
publish, approve, install, restart, change SSH configuration or copy credentials.
The normal full tests, release prechecks and PyPI approval still apply.

## Installed versions and room startup records

```sh
aib update status
aib update status --host mac-mini --host mac-pro --json
```

`--host` is an SSH alias or hostname explicitly chosen by the caller. It uses
batch mode and strict host-key checking; unknown keys and unavailable hosts are
reported as unavailable. No targets are discovered or contacted by default.
The remote probe only reads version files and manifests and inspects the recorded
server process. It uses the existing SSH access, not new credentials.

The table separates the installed core, installed room plugin, room startup
version and process identity. A live PID belonging to another program is a stale
record, not a running room. Missing startup versions remain unmeasured rather
than being filled from the installed manifest. Startup records do not establish
HTTP health or which core bytes an existing process has loaded; those fields
remain unmeasured. Tokens, command lines and local paths are not returned.

Exit 0 means every requested core version was observed, not that an update or
health check succeeded. Exit 3 means at least one requested version is unavailable.

## Fast checks before the expensive release suite

```sh
aib ship preflight --repo . --json
aib ship preflight --repo . --artifact dist/ez2_aib-0.1.61-py3-none-any.whl
aib ship preflight --repo . --plugin /path/to/prepared/signed-plugin
```

The preflight checks the project version, `aib/VERSION` and the first section of
all four changelogs. Optional wheel/sdist checks inspect metadata without
extracting it, reject traversal, links, duplicate entries and macOS metadata
sidecars, and require the archive's package and runtime versions to agree.
Plugin checks use the existing signature and integrity verifier with unsigned
packages refused. Failure returns 1. Success is not a substitute for full tests.
CI runs this preflight before package installation and the long safety suite.

## Release progress

```sh
aib ship status --github ez2app/ez2-aib --version 0.1.61 --pr 390
aib ship status --github ez2app/ez2-aib --version 0.1.61 --host mac-mini --json
```

Choose the version and PR for the release being observed. Stages distinguish
candidate checks, merge, the release tag and candidate ancestry, package
prechecks, upload, actual PyPI availability, post-upload Linux/macOS install and
upgrade verification, and explicitly requested target installations. An upload
alone never proves verification; a PyPI 404 is pending while network failure is
unavailable. Core target installations and room runtime/health remain separate.
Counts show completed checks, not an estimated percentage or completion time.

The command needs the existing `gh` login to read GitHub and contacts the public
PyPI index. It does not request additional scopes or approve environments.
Known failed stages return 1; unavailable evidence returns 3; valid pending
progress returns 0 and remains explicitly pending in the payload.
