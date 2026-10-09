# 快鍵定版(E2.5)

## 開工時發現的實況

說明浮層(E2.12)列了八組快鍵,但 **repo 裡沒有任何腳本綁過它們** ——
那些鍵來自 demo 的 tmux 設定,遷入正本時沒跟著遷。所以本節點同時是「定版」
與「補上一個從未存在的安裝器」。

## 兩份正本,一條對帳

| 東西 | 正本 | 用途 |
|---|---|---|
| 人看的總表 | `i18n/` 的 `aib.keys.*` | 浮層顯示(派工令指定) |
| 真正綁的鍵 | `keys/keymap.json` | 安裝器實際 bind |

`tests/test-keymap.py` 逐條對帳:id 集合必須相等(列了沒綁、綁了沒列都紅),
且顯示字串經機械翻譯後必須等於宣告的 tmux 鍵(浮層說 `{mod}+o`,keymap 就得綁
`M-o`)。負向驗證做過三種漂移:綁 `M-p` 但列 `{mod}+o`、多綁一個沒列的鍵、
把綁定改成裸鍵——三種都紅,各由對應的斷言抓到。

## 三條紀律

1. **全部綁在 `M-` 層,一個裸鍵都不綁。** root 表是全域的,中欄跑著真的 AI 對話;
   裸 `0`/`o`/`m`/`?` 被搶走,使用者就打不出那些字。安裝器另有一道執行期防線:
   遇到非 `M-` 開頭的鍵直接 rc=3 拒裝。
2. **顯示名隨平台變,綁定名永遠是 `M-`。** macOS 顯示 Option、其他顯示 Alt
   (`uname -s`,`EZ2_MODIFIER_LABEL` 可覆寫);把 `Option-o` 寫進 `bind-key`
   會讓整組快鍵靜默失效。
3. **內層 ai-hub 的 `C-a` 不由這裡綁。** hub 由 `cockpit-upgrade.sh` 建立時設定;
   兩處各綁一次會互相覆蓋,而誰贏取決於執行順序——那種 bug 沒人查得動。

## Windows/WSL 的證據強度

**沒有實機。** 平台自適應是用假 `uname` 注入 Darwin / Linux / MINGW64 驗的,
結論只宣稱到這裡:**同一份 spec 在三種 uname 下產生完全相同的綁定,只有顯示名不同**。

以下都**未驗**,不要當成已解:真機終端機(Windows Terminal / WSL 的 tmux)
會不會把 Alt 鍵當 Meta 前綴吃掉、不同 tmux 版本對 `M-Left` 這類鍵名的支援差異、
以及 WSL 下 `run-shell` 的路徑語意。要拿到實機證據,需要有人在那台機器上跑
`install-keys.sh --list` 再逐鍵實按。

## 用法

```
install-keys.sh --list        # 印出 (鍵, 指令) 對照,不動 tmux
install-keys.sh --install     # 真的綁上去
install-keys.sh --install --dry-run   # 只印將執行的 bind-key
```
