# aib-cloud-host

The host-side **I/O shell** of Ez2 Managed Cloud (4.4.1 §12). `aib-cloud` plans and judges
with no I/O (`#![no_std]`); this binary executes on the Proxmox host (or a public vantage point)
and hands the results back.

```text
aib-cloud-host root-password apply <file>   # 忘了 root 密碼:Mac 上 aib secret ask HOST_ROOT_PASSWORD_<HOST> --remote-only --generate --push → 這裡套用
aib-cloud-host probe run   <plan.json> --host <public ip> [--out report.json] [--external]
aib-cloud-host probe merge <report.json> <report.json>… --out merged.json
aib-cloud-host self-update <version>        # 從 GitHub release 換自己(cloud-host-v<version>,驗 SHA256SUMS + --version 才換)
aib-cloud-host site verify [<code>] [--json]  # 被動檢查:宿主 H02–H05 / H08 / H09 + 每站 S00–S05;只讀;結束碼 0 綠 / 1 紅 / 3 不明 / 2 黃
aib-cloud-host --version                    # aib-cloud-host <version> <os>-<arch>
```

`plan.json` = `aib_cloud::isolation::probe_plan()`. The whole plan is validated against a
whitelist before anything runs; `from = ct:<id>` cells re-run this same binary under
`nsenter -t <init pid> -n` (pid from `lxc-info -n <id> -p -H`) after proving the namespace
it lands in is `/proc/<pid>/ns/net` and not the host's. TCP is a 3 s `connect`, DNS one UDP
query to `<ip>:53`, ICMP one `ping -c 1 -W 2`. No shell strings anywhere. `plan_fnv` is
`aib_cloud::isolation::plan_fingerprint` of the plan as loaded, so `judge()` accepts only the
report of exactly that plan on that host.

Replaced the Python runner `ez2-isolation-probe` (Andy 11-09: every new tool is Rust). That
file was deleted on 11-09 after this binary re-ran the full EU02 matrix (sites 110 + 112) with
the same `plan_fnv` and cell-for-cell identical results; the Python canonical-JSON FNV is pinned
by `aib-cloud` test `i9` so old receipts stay valid.

## Getting it onto the host

Releases, not artifacts (大綱 r2 #26). Tag `cloud-host-v<version>` (`<version>` = the workspace
version in `Cargo.toml`) makes workflow `cloud-host` build the static `x86_64-unknown-linux-musl`
file, smoke it, write `SHA256SUMS`, create a **draft** pre-release, download from that draft and
run the file again (including `self-update` against the draft's files over `file://`), and only
then publish it as a pre-release (never "latest"). Release notes
`docs/cloud-host/CLOUD-HOST-RELEASE-NOTES-<version>.md` must exist (a human reads them before tagging).
A push or PR touching the crates still uploads a 14-day CI artifact, for trying a build that is
not a release.

First install on a host (one line each, as root; the file is checked against `SHA256SUMS` before
it is run):

```sh
V=0.2.0-preview.4; F=aib-cloud-host-$V-x86_64-unknown-linux-musl
B=https://github.com/ez2app/ez2-aib/releases/download/cloud-host-v$V
cd /root && curl -fsSL --proto '=https' --tlsv1.2 -O "$B/$F" && curl -fsSL --proto '=https' --tlsv1.2 -O "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | sha256sum -c - && install -m 0755 "$F" /usr/local/sbin/aib-cloud-host \
  && aib-cloud-host --version
```

Every later version: `aib-cloud-host self-update <version>` (fetches the release's
`SHA256SUMS` and file with `curl`, compares `sha256sum`, runs the new file's `--version`, then
one `rename`; the old file stays at `/usr/local/sbin/aib-cloud-host.prev`). Nothing here is
signed; the checksum comes from the same release page as the file, so it proves the download is
what CI built, not who built it.
