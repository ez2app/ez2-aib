# 快速綁定流(E2.6)

大綱原文:hub 新 AI 一律 aib 啟動器開 + BIND 就地輸入 + 綁定佇列入決策收件匣。

## 流程

```
aib <provider> <mode> <role>     啟動器開窗,把「就地執行的那一行」印進中欄
  └─ aib-bind.sh request ...     活著的 session 自己報 uuid 送申請
       └─ decision-inbox/<id>.json   卡進佇列 + 落 decision.requested
            └─ 人核可/駁回            落 decision.resolved,卡改狀態
                 └─ (候 EA.1)         真綁與 registry 收據,現在還做不到
```

## 三條紅線

**1. 啟動器不代填 session uuid。** 開窗的當下那個 AI 還沒起來,uuid 不存在;
代填只能靠猜,而猜出來的身份就是假身份。所以 BIND 是**就地**輸入——由已經
活著的 session 自己報。`aib-bind.sh` 取不到 uuid 時 fail-closed(rc=2),
不編一個出來。

**2. AI 不得核可綁定申請。** `approver` 必須是 `human:<名字>`;AI 的 sig
(`role@hex/Engine`)過不了那條正則。綁定是把一個 AI 從「查無此人」升成
「registry 認得的主AI」,那是身份升權,由 AI 互批等於系統自己給自己發證。
被拒的裁決連事件都不留(它沒發生過)。

**3. 狀態機裡沒有 `bound`。** 核可後停在 `approved_pending_ea1`,
因為真綁需要外部 registry 的收據,而那個收據現在拿不到(EA.1 未完成)。
也**絕不落 `identity.bound` 事件**——那等於宣稱已綁。寧可狀態難看,
也不要一個看起來已綁的假綠燈。三條都有測試釘著。

## 佇列落點與共用

`$EZ2_DECISION_INBOX`(預設 `~/.ez2-aib/decision-inbox`),一卡一個 JSON。
E5.3 設計稿把決策收件匣定為 L7 人層共用基建,綁定佇列是它第一個租戶;
item 形狀刻意保持通用(`kind` / `status` / `created_event_id` / `resolution`),
之後多幾種決策只是多幾個 `kind`,不必另開一個匣。

## 用法

```
aib-bind.sh request --provider claude --role dev1     # 就地申請(uuid 由 env 取)
aib-bind.sh list --status pending                      # 看佇列
aib-bind.sh approve --request-id <id> --approver human:the maintainer
aib-bind.sh reject  --request-id <id> --approver human:the maintainer --reason 重複席位
```

## 現況邊界

- 真綁未接(EA.1);核可只是「人同意讓它綁」,不是「已經綁好」。
- 收件匣目前只有 CLI,沒有 UI;指揮艙徽章與列表是 E5.3 的範圍。
- 申請進佇列不代表那個 session 真的存在——申請內容是它自報的。
  驗證要等 EA.1 的 registry 收據;在那之前,人核可時應自己確認那個窗是真的。
