# aib-cloud-host

The host-side **I/O shell** of Ez2 Managed Cloud (4.4.1 §12). `aib-cloud` plans and judges
with no I/O (`#![no_std]`); this binary executes on the Proxmox host (or a public vantage point)
and hands the results back.

```text
aib-cloud-host root-password apply <file>   # 忘了 root 密碼:Mac 上 aib secret ask HOST_ROOT_PASSWORD_<HOST> --remote-only --generate --push → 這裡套用
aib-cloud-host probe run   <plan.json> --host <public ip> [--out report.json] [--external]
aib-cloud-host probe merge <report.json> <report.json>… --out merged.json
aib-cloud-host self-update <version>        # 從 GitHub release 換自己(cloud-host-v<version>,驗 SHA256SUMS + --version 才換)
aib-cloud-host site verify [<code>] [--json]  # 被動檢查:宿主 H01–H10 / H12 / H13 + 每站 S00–S06;只讀;結束碼 0 綠 / 1 紅 / 3 不明 / 2 黃;紀錄夾讀不到、沒有紀錄、參數打錯也是 3(不是 2)
aib-cloud-host edge domain preflight <網域> --ip <Edge 公網 IPv4>   # 上線前查 DNS:A 全指向我們、沒有 AAAA、CAA 不擋;結束碼 0 可上 / 1 還不行
aib-cloud-host edge domain add|rm <站> <網域> --plan              # 只出計畫(只改該站那一行網域);不帶 --plan 拒絕
aib-cloud-host site usage [--json]          # 每站真正用量(從宿主讀 cgroup,不進 CT)+ ARC 建議;只讀、不套用;以紀錄的 owner 合計客戶額度
aib-cloud-host backup check [--json]        # 離站備份檢查(只列清單):B01 新鮮 / B02 每個 CT 都有 / B03 設定包 / B04 沒有未加密檔 / B05 大小合理;結束碼同 site verify
aib-cloud-host ct mask                      # 由容器執行環境在站 CT 啟動時呼叫(lxc.hook.mount):蓋掉 CT 內看得到的實體主機資訊(cpuinfo 型號、DMI、PCI / 磁碟型號、RAID、開機參數);蓋不上只記錄、不擋站啟動
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
