# aib-ledger IFACE(S78 / S97)

本機帳本(aib-idea 的 `ideas.jsonl`,之後 aib-review / aib-council 各自一本)的共用底層。程式在 `src/lib.rs`(鎖、讀、整行附加、`write_if_complete`)與 `src/envelope.rs`(每一行的信封欄位)。

## 每一行的信封(規格 V1.1 §8.2;Andy 11-14 20:46 同意 v7)
- **v 1**(S97 以前寫的每一行):`v`、`ev`、`event_id`,時間是 `at_ms`(1970 起的毫秒整數)。照原樣讀，**舊行永遠不改寫**。
- **v 2**(S97 起新寫的每一行):`v`、`ev`、`event_id`,時間是 `occurred_at`,外加 `aggregate_type`、`aggregate_id`、`sequence`。
  - `occurred_at` 只收一種寫法:`YYYY-MM-DDTHH:MM:SS.mmmZ`(RFC 3339、UTC、`Z`、到毫秒，24 字元)，日期要真的存在。
  - v 2 行不帶 `at_ms`,一行只有一個時鐘。
- `sequence` 是這一行在帳本事件裡的序號，從 1 開始。v 1 行照位置佔號。
  - v 2 行的號碼不是「已讀事件數 + 1」⇒ 整本標不完整，寫入全拒。掉行、重複、被搬動都會被看見。
- **順序以 `sequence`(檔內順序)為準，不看時間。** `occurred_at` 不保證遞增(時鐘會回撥、會被手改)。讀的程式不要假設時間遞增;要檢查先後，只能當警告，不能判不完整。
- 其他 `v` 一律 `unknown v`(不完整)。
- aib-idea 的 aggregate 是 `idea_ledger` / `ideas`,整本帳本一條流。review_batch 一行會動到多條建議，所以不拆成每條建議各一個 aggregate。

## 上線順序與「不能退版」(1ced7 S97 審查 L1)
1. **先換執行檔，才能有 v 2 行。** 舊版讀到 v 2 行會判帳本不完整(`line N: unknown v`)、寫入全拒、footer rc 2。
   - 這台所有席位與艙共用 `~/.ez2-aib/bin/aib-idea` 這一支。PATH 上沒有第二支;各工作樹 `target/` 裡的不算，它們不在 PATH。
   - 換的方法：寫新檔名 → chmod → 讀回比 sha256 → mv。不要 cp 蓋舊檔。
2. **寫進第一條 v 2 之後，就不能退回舊執行檔。** 這次換版是單向的，出事只能往前修，不能退版。
   - binsync 目前沒有管 aib-idea(FOLLOWUP F15)。之後把它加進 binsync 時，如果 binsync 有「退回上一版」的路，要對 aib-idea 標例外。
   - 發版說明要寫上這一句。

## 匯入 aibd 之前一定要解(FOLLOWUP F17)
- `aggregate_id` 現在每台都是固定的 `ideas`,每台帳本都從 `sequence` 1 開始。
- 兩台的帳本匯入同一個 `events` 表時，會撞 `UNIQUE(aggregate_id, sequence)`(ADR 0003)。
- 所以匯入片開工時，`aggregate_id` 要先加上機器碼。這是匯入的前提，不是之後再說。

## 動作事件(`src/events.rs`,S107-product 片 1;IFACE-s107-product-a9136-1117 r1 §1)
- 做動作的程式當下寫一行:`<狀態根>/state/<程式名>/events/<YYYY-MM>.jsonl`(UTC 月),同夾 `events.lock` 用本 crate 的鎖,一次寫完整行。
- 共同欄 `v` `id` `kind` `at` `seat` `cards` `status_item` `host` `outcome`;各種事件自己的欄放旁邊,同名的共同欄不會被蓋。
- `id` = `<host>:<毫秒>:<pid>:<序號>`;沒設主機名時 `null-<8 碼亂數>:…`(每個行程取一次)。`host` 只讀主機登記檔頂層 `"self"`,沒有寫 null。
- `seat` 沒給就 null、`cards` 空,不猜;`cards` 只收進度樹裡有的卡號。
- 寫失敗:`append_or_warn` 只在 stderr 說一行,不改呼叫端的結束碼。
- 保留:`expire(root, prog, keep_days, now)` 刪整月都過了 `keep_days` 的月檔(預設 90,SPEC §10 `events_keep_days`),回傳刪了哪些給呼叫端寫 clean 事件。
- 狀態根由呼叫端給(aib-stateroot 合進主線後改用它),本模組不自己找家目錄。
