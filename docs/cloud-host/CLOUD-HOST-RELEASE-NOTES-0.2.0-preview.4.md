<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- DRAFT(11-09 4542e):對客 / 對維運的字,打 tag 前 Andy 先看。 -->
# aib-cloud-host 0.2.0-preview.4(預覽版)

> Ez2 Managed Cloud 宿主端工具的第一個正式發布。它在 Proxmox 宿主(host A)上跑,是 `aib-cloud` 規劃與判定邏輯的 I/O 殼;一個靜態執行檔,主機不用裝任何工具鏈。

## 有什麼
- **`probe run / merge`**:跑隔離矩陣探測清單(宿主、每個站 CT 的網路命名空間、公網對照),寫出 `aib_cloud::isolation::judge()` 收的報告。整份清單先過白名單才動;進 CT 前先證明落地的命名空間是那個 CT 的。11-09 已在 host A 真站 110 + 112 跑過整張矩陣,與先前的 Python 版逐格相同;Python 版已刪。
- **`site drift`**:每個 CT 的資源限額與漂移偵測(`ez2-host-health` 每日帶 drift 欄位 → host B 告警)。
- **`site suspend / resume / restore`**:停權、恢復與藍綠還原(11-09 SUS-1 / BG-1 演練同邏輯)。
- **`root-password apply`**:套用由 Mac 端 `aib secret ask --generate --push` 送來的 root 密碼檔,套完 shred。
- **新:`self-update <version>`**:只從我們自己的 GitHub release(`ez2app/ez2-aib`,tag `cloud-host-v<ver>`)換自己 —— 抓該版的 `SHA256SUMS` 與檔案、`sha256sum` 比對、跑新檔的 `--version`,三樣都對才換。換檔先把現有檔硬連結成 `/usr/local/sbin/aib-cloud-host.prev.new`,再一次 `rename` 把新檔蓋上,最後把連結改名成 `aib-cloud-host.prev`;任何時刻 `aib-cloud-host` 不是舊檔就是新檔,中斷不留半套。檢查碼不對就不換、不碰現有檔。比現在舊的版本預設拒絕,要回滾加 `--allow-downgrade`;`--base-url` 只收 `file:///`(CI 離線驗用),沒有 `--repo`。
- **`--version`** 現在印 `aib-cloud-host <version> linux-x86_64`(換版流程靠它核對)。

## 檔案
| 平台 | 檔案 |
|---|---|
| Linux x86_64(靜態 musl;Proxmox VE 8 / Debian 12 實測) | `aib-cloud-host-0.2.0-preview.4-x86_64-unknown-linux-musl` |

沒有簽章;`SHA256SUMS` 與檔案在同一個 release 頁,證明的是「下載的就是 CI 建的」。

## 第一次裝(宿主上以 root 整段貼)
```sh
V=0.2.0-preview.4; F=aib-cloud-host-$V-x86_64-unknown-linux-musl
B=https://github.com/ez2app/ez2-aib/releases/download/cloud-host-v$V
cd /root && curl -fsSL --proto '=https' --tlsv1.2 -O "$B/$F" && curl -fsSL --proto '=https' --tlsv1.2 -O "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | sha256sum -c - && install -m 0755 "$F" /usr/local/sbin/aib-cloud-host \
  && aib-cloud-host --version
```
檢查碼不一致時會停下,不會安裝。

## 之後換版
```sh
aib-cloud-host self-update <新版本>
```

## 要知道的
- 需要宿主上有 `curl`、`sha256sum`、`lxc-info`、`nsenter`、`ping`(Proxmox VE 預設都有)。
- 這一版只發 Linux x86_64;其他平台沒有宿主可驗,不發。
- `probe run` 的 `from = ct:<id>` 格要 root(進 CT 的網路命名空間)。
