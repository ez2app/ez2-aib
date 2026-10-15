---
name: aib-newrole
description: >-
  In an Ez2 AIB seat, create a new persona (role card) with `aib newrole`; only
  when the user names a seat, bind it to that seat in the same step, deliver the
  persona pack and confirm it loaded (no seat named = create the card only, open
  no seat, bind nothing). Every one of these means `aib newrole`: 創角色／創建角色／
  建角色／建立角色／新增角色／新角色／加一個角色／新增人格／新建人格／建立人格／
  創人格／綁人格／綁角色／換人格／幫某一席加一個 XX 人格／newrole／
  create persona／new role／bind persona. 「角色」與「人格」是同一件事。Not for
  distilling a persona from chat logs (that is ez2-persona) and not for opening
  a new AI seat (that is `aib <provider> …` or Ez2-NewAI). Same folder works
  for Claude, Codex, Grok, and Antigravity.
---

# aib newrole — 建人格卡(有指名席位才綁)(四家通用)

使用者說「創角色」「新增角色」「新增人格」「建人格」「綁人格」,指的都是這一支:`aib newrole`。
「角色」與「人格」是同一件事(卡片檔叫「角色.md」,畫面上叫「人格」)。

有指定席位時,一支指令做完五件事:寫人格卡 → 提交到角色庫 → 更新卡片索引 → 綁到席位 → 寫席位記憶。
加 `--deliver` 會再把人格包寄給那一席,讓正在跑的對話立刻知道自己是誰。
**沒指定席位時只做前三件**(建卡、提交、索引),不綁任何席、不開新席(the maintainer 2026-10-01,見下方「沒說綁哪一席」)。

程式正本:`src/identity/create_and_bind_persona.py`。`aib newrole` 與 `aib-create-persona` 是同一支。

## 先確認這台的 AIB 有這個指令

```bash
aib newrole --help
```

印出 `usage: aib newrole` 才往下做。印的是開席說明(`aib <provider> …`)代表這台的 AIB 版本還沒有
`newrole`:停下來告訴使用者要先更新 AIB,不要改用別的方法硬做。

## 先弄清楚四件事(能從對話推就不要再問)

1. **人格名稱**:畫面上要顯示的名字,例「SEO-GEO大師」。
2. **綁到哪一席**:使用者有指名時,抄右欄或 dm 抬頭的地址,寫成 `<艙位碼>/<席碼>`,例 `abcd/12345`。
   **沒指名就不要綁、也不要開新席、不要問**:不帶 `--seat` 只建卡(見下方「沒說綁哪一席」)。
3. **人設內容**:使用者只給一句話(「他是全球 SEO 大師」)時,由你把它寫成下面的四段。
4. **你自己的署名**(要綁席位時才需要):`<角色>@<你的席碼>/<引擎>`,例 `root@54321/Claude`。
   角色只能小寫英數與連字號,席碼是你自己抬頭 `@` 後面那五碼。這是帳本署名,不是目標席。

## 人設檔怎麼寫

寫成一個檔(放暫存目錄),開頭一句定位,接四段。**前三個標題缺一段就會被擋(rc=4)**:

```markdown
你是「<名稱>」,<一句話定位>,在 Ez2 AIB 做事。

## 職責
(編號列出要做的事,最後一條寫交件要附什麼證據)

## 紅線
- 第一句交代現在能不能用;沒檢查的項目要跟檢查過的一樣顯眼。
- (這個角色絕對不做的事)

## 語氣與口癖
(怎麼說話。不要寫死語言:回答語言跟著使用者的 AIB 語系,人格包會自動加上那一行)

## 知識域
(懂哪些領域;要先讀哪些技能或文件;缺乏依據時明示未知)
```

- **整份控制在 3,000 位元組以內**。人格包上限 4,000,超過時會從「知識域」開始丟。
- 不放金鑰、密碼、私鑰。不寫要對方無視既有規則或隱瞞回報的句子(會被擋)。
- 知識很多就放知識庫,卡上只寫指路。

## 做法

```bash
# 1. 先試跑:不寫任何檔,看會綁到哪一席、卡會放哪裡
aib newrole --name "SEO-GEO大師" --code seogeo --emoji "🌐" --category "行銷/SEO" \
  --prompt-file /path/to/persona.md --seat abcd/12345 \
  --actor "root@54321/Claude" --deliver --dry-run

# 2. 輸出的 seat_bound、card_file、delivery.address 都對,拿掉 --dry-run 真跑
aib newrole --name "SEO-GEO大師" --code seogeo --emoji "🌐" --category "行銷/SEO" \
  --prompt-file /path/to/persona.md --seat abcd/12345 \
  --actor "root@54321/Claude" --deliver
```

| 參數 | 說明 |
|---|---|
| `--name` | 人格名稱,必填 |
| `--prompt-file` | 人設檔。也可用 `--prompt "文字"`,但有引號、反引號、多行時一律用檔案 |
| `--seat` | `abcd/12345`(建議)、`@12345`、或席位檔的 seat_id。**不給 ⇒ 只建卡,不綁任何席** |
| `--new-seat` | **只有使用者明講「開一個新席來綁」才用**:開一個新席來綁(與 `--seat` 二選一);`--engine` 選引擎,預設 claude。不是預設 |
| `--actor` | 你的署名,綁定必填(只建卡不用);要綁卻不給也沒設 `EZ2_OWNER_SIG` 就停手(rc=2),不會亂猜 |
| `--code` | 英文代號,小寫英數;不給會從名稱抽 |
| `--emoji` / `--category` | 圖示(預設 🎭)/ 分類(預設「客製/專家」) |
| `--brain` | 順便綁的資料夾,預設 `core.handoff`;**那一席已綁專案時預設不動資料夾**(等於 `none`)。`--brain none` = 只綁人格。專案與資料夾請用 `aib newproj` |
| `--deliver` | 綁完用 aib dm 把人格包寄給那一席;沒給 `--seat` 時會被拒(rc=2,什麼都沒寫) |
| `--overwrite` | 同名卡已存在時重寫內容;不給就一個位元組都不動舊卡,只重綁 |
| `--dry-run` | 只試跑 |

## 驗收(每一項都要讀回結果,不要只看 rc=0)

**只建卡(沒給 `--seat`)**:JSON 的 `ok: true`、`seat_bound: null`、`reply` 是「已創立好 <名稱> 人格(uid <六碼>)」,
`card_file` 那個檔在。把 `reply` 那一句原樣回給使用者就好,下面 1–5 不用做。

**有綁席位時**:

1. 指令最後印的 JSON:`ok: true`、`seat_bound` 是目標席、`delivery.ok: true`、`delivery.stage: delivered`。
   - `delivery.ok: null` 加 `delivery.no_retry: true`:aib dm 回 3/4,**字已送出或已進對方框、只是沒能確認**。
     **不要重跑**(會疊兩份),直接跳到第 4 步等對方回覆。
   - `delivery.ok: true` 但 `stage` 是 `queued` / `sort-queued`:對方在忙,排進佇列了,不要重跑,等它空下來會送到。
2. `python3 "$EZ2_AIB_ROOT/src/identity/bind_one.py" status --seat <seat_bound>`:`persona_card` 是新卡的 uid。
3. 右欄那一席底下出現 `🎭 <人格名稱>`。
4. **等對方回一行「人格已載入 <八碼>」**,八碼要等於 JSON 裡 `delivery.expect_reply` 那一組。
   寄達只代表字進了對方的框;沒收到這一行,就回報「已綁、對方尚未確認載入」。
5. **(要確定它真的照人格做事時)人格抽查**:`python3 "$EZ2_AIB_ROOT/src/identity/persona_check.py" ask --seat <艙位碼>/<席碼>`
   寄三題(你是誰、紅線、做事方式),等它回「人格抽查回覆」後跑同一支的 `show`,卡與回答並排,紅線逐條由人看。
   ★ 對話被壓縮或 /clear 之後,Claude 席會由 SessionStart hook(src/identity/persona_reinject_hook.py)自動補回人格包;
     其他家還沒有這個機制,長對話後要抽查一次。

## 回報給使用者

只建卡時:回 JSON 的 `reply` 那一句(例「已創立好 SEO-GEO大師 人格(uid ab12cd)」),不要多加「要綁到哪一席?」的追問。

有綁席位時:第一句講現在能不能用。接著講:綁到哪一席、人格卡放哪裡、有沒有順便綁專案、對方有沒有回覆已載入。
沒驗證的事(例如對方實際回話像不像這個角色)要明講沒驗。

## 結束碼

| rc | 意思 | 已經發生了什麼 |
|---|---|---|
| 0 | 全部完成 | 有 `--seat`:卡、索引、綁定、記憶(與寄送)都做了;沒 `--seat`:卡與索引做了,沒綁任何席 |
| 2 | 席位認不出來、沒署名、人設檔讀不到、`--deliver` 沒配 `--seat`、**新卡要綁席卻沒給人設** | 席位、人設檔、`--deliver`、新卡沒人設的問題:什麼都沒寫。沒署名:真跑時卡已建、沒綁;**試跑時什麼都沒寫(卡也沒建)** |
| 4 | 人設內容被擋(缺段落、超過 20KB、含禁用句或金鑰) | 什麼都沒寫 |
| 5 | 卡寫了但角色庫提交失敗 | 卡在磁碟上、沒綁 |
| 6 | 綁好了但人格包沒寄到(aib dm 回 2 等:一個字都沒進對方框) | 人格已綁;看 `delivery.reason`,修好後重跑同一行(舊卡不會被改)。aib dm 回 3/4 **不是** rc=6:整支回 0、`delivery.no_retry: true`,不要重跑 |

## 常見狀況

- **使用者明說綁你自己這一席**:不要加 `--deliver`(不寄信給自己)。綁完直接讀卡照做。
- **同名卡已經有了,只想綁到另一席**:同一行指令換 `--seat`,不用帶人設;舊卡不動。
  (卡還不存在時要綁席,一定要給人設;不給會被擋 rc=2,不會把預設範本綁進席位。)
- **同一張卡重跑**(補寄人格包、換資料夾):席位記憶裡那份人設不會再貼一次。換專案用 `aib newproj`。
- **要改既有人格的內容**:那是改卡,先跟使用者確認,再加 `--overwrite`。
- **名字開頭是「英數字-」**(例「SEO-GEO大師」):資料夾會自動變成 `SEOGEO-SEO-GEO大師`,
  這是為了讓畫面顯示完整名字,不是錯誤。
- **同一個席碼在兩個艙都活著**:會被拒並列出兩席,改寫完整地址 `<艙位碼>/<席碼>`。

## 沒說綁哪一席 ⇒ 只建卡,不綁、不開席(the maintainer 2026-10-01,取代 09-30「沒說就開新席」)

使用者只說「建一個 XX 人格」、沒指名哪一席時,**不要問、不要綁到你自己這一席、也不要開新席**,不帶 `--seat` 就好:

```bash
aib newrole --name "SEO-GEO大師" --code seogeo --prompt-file /path/to/persona.md
# 印出:已創立好 SEO-GEO大師 人格(uid ab12cd)
```

- 只寫卡、提交角色庫、更新卡片索引;不綁任何席、不寫席位記憶、不寄人格包、不需要 `--actor`。
- 同名卡已經有了 ⇒ 卡不動,回「<名稱> 人格本來就有了(uid …),卡沒動」。
- 之後使用者指名要綁哪一席,再跑同一行加 `--seat <艙位碼>/<席碼> --actor "<你的署名>" --deliver`(舊卡不會被改)。
- `--new-seat` 仍在,但**只有使用者明講「開一個新席來綁」才用**:
  `aib newrole --name "<名稱>" --prompt-file <人設檔> --new-seat [--engine grok] --actor "<你的署名>" --deliver`。
  新卡一定要帶人設;所有檢查都過了才會開席(被擋時不會留下新席),試跑也會跑同一組檢查。
  它會在你所在的艙開一個新席(不切換任何人的畫面),等 AI 啟動完成(通常一兩分鐘,最多 300 秒)再綁;
  等太久會停下來說明,等右欄出現那一席之後改用 `--seat <艙位碼>/<席碼>` 再跑一次。開新席會用那個引擎的額度。

## 只是想試試看,不要用別人的正式席位

綁定、解綁都會永久寫進帳本(帳本只能增加,不能刪),也會換掉那一席的人格。
只是要驗證指令、練習、或寫測試時:

- 開一個測試用的艙:`aib claude safe test new`(或其他引擎),在那一艙裡的席位上試。
- 試完用 `[X]` 關掉那一艙。
- 真的要在某一席上試,先問使用者,並在試完後換回原本的人格。

## 這份技能怎麼裝

正本只有 AIB 倉內的 `skills/aib-newrole/` 這一份,各家 AI 的技能資料夾放的都是指向它的連結:

```bash
"$EZ2_AIB_ROOT/tools/aib-install-skills" aib-newrole
```

要改這份技能就改倉內那一份;不要在 `~/.agents/skills` 或 `~/.claude/skills` 底下另存一份。

## 不要做

- 不要手改 `~/.ez2-aib/identity/` 底下的 cards.json、bindings.json、seat-display.json。
- 不要自己在角色庫建資料夾寫卡再手動綁,少一步索引或提交,畫面與帳本就對不上。
- 不要用 `~/.ez2-aib/bin/create-and-bind-persona.py`,那是舊副本。
- 不要直接對別席的視窗貼字;寄送一律走 `--deliver`(它走 aib dm)。
- 不要在沒有 `--dry-run` 看過結果之前,對別人的席位真跑。
