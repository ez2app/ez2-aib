---
name: aib-handoff
description: >-
  MANDATORY wrap-up for every Ez2 AIB seat (the maintainer 2026-10-01): when the
  conversation's context reaches 85%, or the maintainer says 準備交接／準備 clear／收尾／
  要 clear 了／交接一下, first `aib memory tidy` and `aib summary write`
  (summary for the maintainer, the maintainer 2026-10-13), then run `aib handoff` — collect machine facts, then write
  the three steps (一、沒想到/想建議 → 二、下一個 AI 會不知道的 → 三、怎麼重新開始),
  each step updating seat memory and the second brain, the last one writing
  the handoff file and the receipt. It cannot be skipped: the next round
  checks for the receipt and, if missing, auto-collects facts and tells the
  next AI to report the skip to the maintainer. Triggers: 85%, context 快滿, 準備交接,
  準備 clear, 收尾, 交接, handoff, wrap up, before /clear. Same folder works for
  Claude, Codex, Grok, and Antigravity.
---

# aib handoff — 收尾三步交接(必做,不可跳過)

the maintainer 2026-10-01 令:**對話用量到 85%(或 the maintainer 說「準備 clear」)時,每一個 AI 都必須跑這三步**,
程式化、強制、不能跳過。程式正本:`src/cockpit/aib_handoff.py`(`aib handoff` 就是它)。

## 什麼時候做

- 對話用量(右欄 CTX%、或你自己的 context 指示)到 **85%**。
- the maintainer 說:準備交接、準備 clear、收尾、要 clear 了、交接一下。
- 你自己判斷這一輪要結束(換席、收班)。

不確定到了沒有 ⇒ 做。做了多一份交接不會壞事;沒做,下一輪會被機器抓到。

## 先整理、再總結,最後交接(the maintainer 2026-10-13「收尾也先總結再交接」)

總結(寫給 the maintainer)與交接(寫給下一個 AI)分開,兩個都做。85% 與 the maintainer 說收尾 / 準備 clear 一樣照這個順序:

```bash
# A. 整理記憶(專案卡本席列、席位記憶精簡、進度筆記、人格卡 12 維;第一艦長卡大改這次跳窗給 the maintainer 按)
aib memory tidy collect --out /private/tmp/tidy-<席碼>.md      # 照草稿說明填
aib memory tidy apply --file /private/tmp/tidy-<席碼>.md

# B. 給 the maintainer 的總結(七段總回報;本輪沒整理會 rc 2)
aib summary template --out /private/tmp/sum-<席碼>.json        # 過得了格式檢查的最小範本,照著改
aib summary write --file /private/tmp/sum-<席碼>.json
```

整理或總結失敗**不擋交接**:照下面三步交接,交接檔與收據會記下「本輪整理 / 總結有沒有做」,
沒做的寫成下一輪開頭補。95% 時間不夠 ⇒ 先交接,有餘裕再補總結。

## 交接三步(照順序,一步都不能少)

```bash
# 0. 量機器事實,得到草稿(倉、分支、worktree、沒合的分支、租約、待審框、review 帳、背景程式)
aib handoff collect --out /private/tmp/handoff-<席碼>.md

# 1. 問自己「還有什麼沒想到?我建議什麼?」→ 寫進草稿第一段 → 寫入
aib handoff write --step 1 --file /private/tmp/handoff-<席碼>.md

# 2. 問自己「下一個 AI 會不知道什麼?」→ 寫進第二段 → 寫入
aib handoff write --step 2 --file /private/tmp/handoff-<席碼>.md

# 3. 「下一輪怎麼從上面這些重新開始?」→ 第三段寫:最後總結 + 可直接貼的交接文字 + 我會如何開始 → 寫入
aib handoff write --step 3 --file /private/tmp/handoff-<席碼>.md

# 確認收據
aib handoff status
```

每一步都會:更新**席位記憶**(`~/.ez2-aib/memory/<席碼>/context.md`,人格包告訴你的那一份)與
**第二大腦**(同 `aib memory write --kind handoff`)。第三步再寫交接檔
`~/.ez2-aib/pending/HANDOFF-<席碼>-<MMDD-HHMM>.md` 與收據 `~/.ez2-aib/state/handoff/<席碼>.json`。
第三步印出「三步完成,可以 /clear」之後才可以 clear。

- `--step` 一定要給,三步一步一步做;沒有「一次做完」。
- **每一段寫真的、這一輪的、和別段不同的內容**。機器會擋(rc 2,什麼都不寫):段落空的、還留著 `> TODO-HANDOFF`
  待填字、太短(每段至少 2 行有字、40 個字以上)、湊字數(不同的字不到 12 種,或同一個字佔一半以上)、
  只是把草稿的提示字(待填的問題、段標題)貼回來(刪掉提示字後每段至少要剩 20 個自己寫的字)、
  和已經填了的別段幾乎一樣(相似度 80% 以上,第 1 步就比)、三段和上一輪交接檔一字不差(拿舊稿換 nonce 重交)。
  這一輪真的沒變(例如還在等 the maintainer 回覆)就照實寫「沒變、在等什麼」,用這一輪的話寫,會過。
- 草稿要用**這一席、這一輪最後一次** `collect` 印的那一份(草稿第二行的 `<!-- handoff:draft … nonce=… -->` 不要刪);
  重新 collect 過就改填新的那份。機器事實段原樣留著,不要刪也不要改(collect 記了它的雜湊)。
- 三步用**同一份草稿**:第 1 步寫進記憶的第一段,第 2、3 步不能再改(第二段同理);真的要改就重新 collect、從第 1 步重寫。
- 同一輪沒做第 1、2 步就做第 3 步 ⇒ rc 2。
- 收據要在這一輪最後一次 collect 之後寫才算(寫完又 collect,就要再寫一次)。
- 幫別席寫(例如真人替 API 席代跑)可以,會提醒並記在收據裡。
- 交接檔開頭會自動插入 `handoff-fields` 六格圍欄(主線 sha、在飛候選、等誰、可砍樹、三記憶、監視),
  doctor 與右欄任務區讀它;機器量不到的格寫「量不到:為什麼」。
- 不在 AIB 席裡(認不出席)⇒ 加 `--seat <艙位碼>/<席碼>`。
- 第二大腦寫不進去(這台沒有 Vault 等)⇒ 照樣寫其他三樣,收據裡照實記 `brain.ok=false`,回報時要講。

## 草稿長相

```markdown
## 一、沒想到/想建議 <!-- handoff:s1 -->
(你寫)
## 二、下一個 AI 會不知道的 <!-- handoff:s2 -->
(你寫)
## 三、怎麼重新開始(交接文字 + 我會如何開始) <!-- handoff:s3 -->
(你寫:最後總結;一段可以原樣貼給下一個 AI 的交接文字;「我會如何開始」第一步到第三步)
## 機器事實(collect 自動量,不是判斷) <!-- handoff:facts -->
(collect 填好的,不要改)
```

`<!-- handoff:… -->` 標記不要刪,程式靠它找段落。

## 寫的規矩

- 對 the maintainer 一律**繁體中文、白話**;第一句就是「現在能不能用」。
- **沒檢查的要跟檢查過的一樣顯眼**:collect 的「沒量到的」那一段原樣保留,自己沒驗的也列出來。
- 第二段一定要寫:**在跑的子代理**(子代理綁在派它的對話,clear 之後收不到它的回報)、
  它們的**工作樹與分支**、在等誰回覆、拿著的租約。
- 要人裁的事,每一件都附「**你不回我就走哪一條**」。
- 不用內部代號當內容;代號第一次出現就附白話。

## 跳過會怎樣(機器在抓)

- Claude 席:`/clear` 時 SessionStart hook 先補回人格與基因,再查上一輪有沒有收據;沒有 ⇒ 記缺漏旗標
  `state/handoff/<席碼>.missed.json`、在背景補抓一份 `HANDOFF-<席碼>-AUTO-<時刻>.md`(幾秒內寫好),
  新對話裡就有「[AIB 交接缺漏] …」,要求新的 AI 告訴 the maintainer。連按兩次 /clear(中間沒講話)不算缺漏。
  `/resume` 接回別的對話不是新的一輪:接回的那份對話也算進這一輪,下一次 `/clear` 一樣查;對話檔找不到 ⇒ 照查。
  接回的舊對話只算**接回之後**真人講的話(接回後什麼都沒講就 /clear,不算缺漏)。
- API 席、橋接 bot 自己不能跑指令:提醒會請真人代跑 `aib handoff`(加 `--seat <艙位碼>/<席碼>`)。
- 其他家(codex、agy、grok、copilot、gemini、kimi、API 席):載入舊席位、接回(分出新席)時查前一席;
  沒收據 ⇒ 一樣自動補抓、記旗標,提醒接在開場說明或開席單裡。
- 看到「[AIB 交接缺漏]」:先讀那份 AUTO 檔接手,然後**主動告訴 the maintainer 上一輪跳過了交接**。

## 安裝(給維護者)

技能正本在倉內 `skills/aib-handoff/`。裝到各家讀技能的地方:

```bash
tools/aib-install-skills aib-handoff
```
