# EU02 再灌一次(LVM-thin 版)runbook — 4542e 11-08

目的：把 installimage 預設「整顆 ext4」換成「root 40G + 其餘 LVM-thin」,PVE 才有 snapshot 備份與每 CT 獨立卷(4.4.1 §5.7 / §7.2 / §10.1)。
性質：**L2、不可回滾**(兩顆 NVMe 全格式化;機器現在是空的)。恢復路徑 = 再灌。

## 誰做什麼
| 步 | 誰 | 動作 | 驗 |
|---|---|---|---|
| 0 | Andy | **二選一**:A 一次性建 Robot webservice user(Settings → Web service and app settings),`aib secret ask hetzner_robot_user` / `hetzner_robot_pass` 彈窗填入 → 之後 4542e 用 API `POST /boot/<server#>/rescue os=linux authorized_key[]=<Robot 的 fingerprint>` 自己啟用;B 自己在 Robot Rescue 頁勾 andyimkt 金鑰按 Activate | A:`GET /boot/<server#>/rescue` active=true;B:頁面顯示 active。**60 分鐘內要重開,只供一次 Rescue 開機** |
| 1 | 4542e | `systemctl reboot`(已准過一次,這次再問) | 1–3 分鐘後 ssh 進 Rescue |
| 2 | 4542e | Rescue 內唯讀：`lsblk`、`ls /root/images | grep -i trixie`、`ls /sys/firmware/efi`(已知 BIOS)、對 installimage 分割語法;核對新 Rescue host key | 兩顆 nvme 都在、映像檔名對、legacy BIOS |
| 3 | 4542e | 把 conf 貼到 /root,`installimage -a -c /root/installimage-eu02-lvmthin.conf` | 結束碼 0、無 ERROR |
| 4 | 4542e | `reboot`;等新系統;Mac known_hosts 換新指紋 | Debian 13、hostname eu02、`vgs` 有 vg0、`lvs` root 64G、vg free ≈365G、RAID [UU] 或 resync、兩顆都有 GRUB(`dd` 看 MBR 或 grub-install 記錄)、initramfs 含 mdadm |
| 5 | 4542e | 第 0 / 1 / 4 步(hostname + /etc/hosts 公網 IP、ssh drop-in、fail2ban + unattended;不裝 ufw)+ 重開一次確認能回來 | sshd -T 六項、fail2ban active、hostname eu02 |
| 6 | 4542e | 裝 PVE 9(§7.2):加 no-subscription 庫 + release key → `apt install proxmox-default-kernel` → **重開、`uname -r` 是 pve 核心** → `apt install proxmox-ve postfix open-iscsi`(postfix 選 local only)→ `apt purge linux-image-amd64 'linux-image-6.*' os-prober` → 拿 ceph / enterprise 庫 → 重開。ifupdown→ifupdown2 置換時**不要** `ifreload -a` | `pveversion`、pve 核心、pveproxy 8006 先只放 management(7.3) |
| 7 | 4542e | `vgs` 核對 → `lvcreate --type thin-pool -L <可用-25G> --poolmetadatasize 2G --poolmetadataspare y -n data vg0` → `pvesm add lvmthin local-lvm --vgname vg0 --thinpool data --content rootdir,images` | `pvesm status` 有 local-lvm;`lvs` Data% / Meta% 進監控 |

## 諮詢結論(11-08 22:0x,c477a / Codex / Antigravity 一致)
- **不進 Rescue 線上改 LVM-thin 的三條路(拆鏡像、md0 當臨時系統、kexec)都不做**:沒有帶外救援,第一次重開就是失聯切點。
- Robot API:webservice user + HTTP Basic;`authorized_key[]` 是 Robot 回傳的 fingerprint 不是公鑰;Rescue 只排下一次開機、60 分鐘內要重開;主機還活著就 ssh `reboot`,失聯才 `POST /reset type=hw`;憑證放 aib secret、回覆要遮 root 密碼;**進 Rescue 後預設只診斷,不自動接 installimage**(installimage 另一道確認)。空機時先完整演練一次當自救路。
- 磁碟:/boot 2G、root 64G、thin pool 顯式 -L、metadata 2G + spare、VG 留 ~25G;BIOS+GPT 由 installimage 自建 bios_grub;md metadata 1.2 現代 GRUB 可讀,真正要驗的是兩顆都有 GRUB + 單碟開機演練。
- 首次換 PVE 核心重開也是失聯風險點 ⇒ **先有 Robot API 自救再裝 PVE**。

步 1、3、4、5、6 的重開 / 寫入各自回報;步 3 前再向 Andy 確認一次「准 installimage」。
