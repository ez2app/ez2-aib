[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 臨時金鑰(`aib cf grant`)

不在伺服器上留長期、大權限的 Cloudflare 金鑰:`aib cf grant` 每次都先要 Touch ID,再現開一把 token ——
**只限一個網域**、**只限你點名的權限**、**只能從目的伺服器的 IP 用**、**1–10 分鐘後自動失效**(預設 10 分鐘)。
token 只在記憶體裡,經 ssh 的 stdin 直接寫到伺服器(權限 600),不落本機磁碟、不印、不寫 log、不當參數。

```
aib secret ask CF_TOKEN_MINTER                      # once: seal the minter key into this Mac's Secure Enclave
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

## 防線是什麼

- **母鑰只封在這台 Mac 的 Secure Enclave。** 在 Cloudflare 後台建一把只有 **API Tokens Write** 的 token(要核發的網域另加
  **Zone Read**,才查得到網域),由本人跑 `aib secret ask CF_TOKEN_MINTER`。值會用 Secure Enclave 的金鑰(CryptoKit
  `SecureEnclave.P256`,存取控制 `biometryCurrentSet`)加密封存到 `~/.ez2-aib/secrets/se/CF_TOKEN_MINTER.sealed`。
- **每次要用母鑰,系統本身彈 Touch ID。** 解封必須讓 Secure Enclave 做一次金鑰協商,而那把金鑰設了「每次使用都要生物辨識」——
  這是 macOS / Secure Enclave 強制的,不是 aib 自己的檢查。封存檔被複製走也沒用:只有這台機器的 Secure Enclave 加本人指紋解得開。
  換了指紋(新增 / 刪除)後封存失效,要重新 `aib secret ask CF_TOKEN_MINTER`。
- **視窗那句話由 aib 組**:網域、權限、TTL、目的主機都寫進去。看清楚再按。一次核准只開一把。
- **母鑰不准放在檔案裡。** 金鑰櫃(`cloudflare.env`,含 LINE 補填)拒寫 `CF_TOKEN_MINTER`;如果檔案裡已經有這一行,
  `grant` / `list` / `revoke` 一律拒絕(`minter_in_file`),請本人改用上面那行封進 Secure Enclave,再把檔案裡那行刪掉。
- **稽核紀錄只是紀錄,不是防線。** 每次走到解封那一步(成功或被拒)都追加一行到 `~/.ez2-aib/audit/cf-grant.jsonl`(不含值),方便事後查。

## 其他規則

- **權限**(白名單):`dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `routes-write` `workers-write` `redirect-read` `redirect-write` `bulk-redirect-read` `rule-lists-read`。
- **IP 鎖**:ssh 連的那個位址 + 伺服器自己對 Cloudflare 的出口 IPv4/IPv6 + 你另給的 `--ip CIDR`。一個都找不到就不建。
- 沒有任何略過 Touch ID 的旗標或環境變數;不是 Mac ⇒ 不建。`AIB_TOUCHID=0` 只會把它關掉。
- `--dry-run` 只檢查參數和 IP 鎖、印出計畫;要查網域就得用母鑰(= 要指紋),所以 dry-run 不打 Cloudflare。
- 送不到伺服器 ⇒ 立刻把剛建的 token 刪掉。
- 等不了指紋的自動工作(續憑證、清快取)請另給一把限網域、鎖 IP 的小 token;這個指令是給「有人核准」的變更用的。
- 第一次使用會用 Command Line Tools 的 `swiftc` 編一支小程式(`~/.ez2-aib/bin/aib-sehelper`,原始碼在
  `aib/src/cockpit/sehelper/`),原始碼沒變就不重編。沒有 `swiftc` 會提示安裝 Command Line Tools。

## 剩餘風險(照實寫)

- **同帳號的程式改得了使用者可寫的程式碼。** `~/.ez2-aib/bin/aib-sehelper` 與 aib 本身都在本人帳號可寫的位置;
  同帳號的 AI 若把它們改掉,可以在本人**核准一次合法請求**時,順便把那次解出來的母鑰帶走。Secure Enclave 擋得住「沒有本人按指紋就拿到」,
  擋不住「本人按了、但跑的程式已被換掉」。後續建議:把輔助程式裝到 root 擁有的位置,執行前比對簽章清單的雜湊(這一版沒做,待整合席裁)。
- 母鑰本身能開任何權限的 token;它的安全完全靠上面那道 Secure Enclave 防線。Cloudflare 後台請替母鑰設 IP 限制(只准你的 Mac / 辦公室出口)當第二道。

## 上線手冊

整個流程分三段。第 ① 段只由本人操作;第 ③ 段每一項都屬正式環境，逐項等本人點頭。

### ① 建母鑰並封進 Secure Enclave(本人自己做，AI 不經手)
1. Cloudflare 後台 → My Profile → API Tokens → Create Custom Token:
   - 權限：User → **API Tokens: Edit**;Zone → **Zone: Read**(要核發的網域，或 All zones)。
   - 建議加 Client IP Address Filtering = 這台 Mac 的出口 IP(IP 不固定就不加)。
2. 在終端機自己跑(Claude Code 裡用 `!`,一整行、前面不要空格):`!aib secret ask CF_TOKEN_MINTER`
   遮字框貼上 → 指紋。值只從輸入框進 Secure Enclave 封存檔，不進對話、不寫任何 `.env`。
3. `aib cf list` 會跳指紋、列得出帳號 token ⇒ 母鑰可用。

### ② 真機實測(本人在旁按指紋)
1. `aib cf grant <zone> zone-read --push <host>:/root/cf-grant-test.env --ttl 2m --why "上線實測" --dry-run`:看計畫，IP 鎖要含目的主機的 IPv4 / IPv6。
2. 拿掉 `--dry-run` 再跑：視窗要寫明網域、權限、2m、目的主機;按下。
3. 在目的主機用那把 token:`/user/tokens/verify` 是 active;讀該網域成功;讀 DNS 被拒。
4. 2 分鐘後再 verify ⇒ 失效;`aib cf list` 看得到它過期。
5. 再跑一次 grant,視窗按「取消」⇒ 不建 token,稽核紀錄多一筆被拒。
6. 刪掉目的主機上的測試檔(單一目標)，結果記錄並回報。

### ③ 撤掉舊的大鑰匙(正式環境，逐項等本人點頭)
先把等不了指紋的自動工作(續憑證、清快取…)換成限網域、鎖 IP 的小 token,再撤：
1. 本機金鑰櫃若還有母鑰舊行 ⇒ 本人刪掉那一行。
2. 伺服器上的 global key 與大範圍 token:先備份，再從設定檔移除;同目錄的舊備份檔也含同一把，要一起處理。
3. Cloudflare 後台撤銷大範圍 token;global key 由本人 **Change / Roll**(global key 不能只停用)。
4. 撤完 24 小時內看憑證續期、快取清除、網域稽核等排程紀錄都正常。
