status: 開發中 · rev: 1 · supersedes: 無 · node: S78 r-1 · owner: 043dd · 11-15(台北)

# aib-review IFACE(S78 r-1 第一段)

規格正本:`~/.ez2-aib/pending/DEV-S78-review-gate-519d0-1114.md` §2–§7。**這是席位之間的紀律檢查,不是安全防線**(同帳號下席碼與本機帳都能改)。

## 指令
| 指令 | 做什麼 | rc |
|---|---|---|
| `submit --pr X --author <作者席> --file <審查稿> [--verifies R1-M1 …] [--seat X] [--repo D]` | 記一份審查稿:驗第一行、驗新發現條數、頭要是倉裡的 commit;`--verifies` 確認作者 claimed 的修正 | 0 / 3 規則拒 / 2 帳不完整 / 4 / 64 |
| `fix --pr X --ids R1-M1 … --commit <40> [--seat X] [--repo D]` | 作者記「這個 commit 修了」;**只到 claimed** | 同上 |
| `status [--pr X] [--json]` | 每張 PR 的輪次與每條發現的狀態 | 0 / 2 |
| `gate --pr X --head <40> [--receipt <收據>] [--repo D]` | 能不能往下走 | 0 過 / 3 擋(每條理由印出)/ 2 帳不完整(印 `gate: unknown`,不能當成安靜的擋)/ 4 / 64 |

## 審查稿格式(submit 檢查)
- 第一行固定:`結論: 通過|不通過 頭: <40 碼小寫> H/M/L/N: a/b/c/d 範圍: <非空>`。
- **新發現**一條一行,寫成 `- [H1] …`、`- [M1] …`、`- [L1] …`、`- [N1] …`;每級從 1 連號,條數要等於第一行。程式碼區塊(```)裡的不算。
- 講上一輪發現的行(例如 `- **M1 已修**`)不是新發現,不算數;要確認修好用 `--verifies`。
- 「通過」又有嚴重 ⇒ 拒。同一份檔(sha256)不能記兩次。作者不能審自己的 PR;同一張 PR 的作者固定是第一份審查記的那一席。

## 狀態與閘
- `open` → `claimed`(作者 `fix`,commit 要在倉裡)→ `verified`(下一份非作者審查 `--verifies`,claimed 的 commit 要是這份審查頭的祖先)。
- gate 擋:沒有任何一份審查在這個頭上;任何嚴重或中度不是 verified;或 verified 的頭不是這個頭的祖先(舊頭不含修正就不算)。輕度、備註不擋。
- `--receipt`:收據最後一行 `RECEIPT-SHA256` 要等於前面全文的 sha256(抓半寫入,不是防線);要有 `HEAD-END` 行(有跑完);每一行 HEAD 都要等於 `--head`。收據裡各步的 RC 不在這裡看(測試狀態與審查狀態分開記,DEV-S78 §6)。

## 帳本
- `<root>/state/review/review.jsonl`,鎖 `review.lock`;root = `EZ2_AIB_HOME` 或 `~/.ez2-aib`。
- 每行 v 2 信封(aib-ledger `envelope`),aggregate `review_ledger` / `reviews`。這本帳生於 S97 之後,**沒有 v 1 行**;讀到 v 1 判不完整。
- 重放時每條規則都再查一次,手改壞一行 ⇒ 整本不完整、寫入全拒、gate rc 2。

## 第二段(還沒做)
- `dismiss`(非作者反證)、`reclassify`(嚴重降級要第二位非作者席)、`waive`(中度預算用完,非作者非共同作者;綁頭碼;同 PR 最多 2 條;碰資料 / 安全 / 平台 / 閘刀不准)。
- 只接 main 沒手解的 `carried` 列(兩個 tree 碼),讓 gate 對新頭不必重審。
- 中度預算(初審 + 2 次修正複審)、每條證據可信度、共同作者用 git log 判定、Codex 輪「pending seat review」。
- 匯入 aibd 前 `aggregate_id` 要加機器碼(同 aib-ledger IFACE F17)。
