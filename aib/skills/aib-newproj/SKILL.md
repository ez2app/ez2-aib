---
name: aib-newproj
description: >-
  In an Ez2 AIB seat, create a new project (project card, the "second brain")
  with `aib newproj`; only when the user names a seat, bind it (or an existing
  project) to that seat, deliver the handoff pack and confirm the seat took it
  over (no seat named = create the card only, open no seat, bind nothing). Every one of these means `aib newproj`: 新增專案／新建專案／
  創專案／建專案／建立專案／開專案／開新專案／新專案／綁專案／換專案／
  幫某一席綁上 XX 專案／解綁專案／newproj／create project／new project／
  bind project. A project is its card plus the folder it lives in. Not for
  personas (that is aib-newrole) and not for `aib project` (status and
  scheduling of the plan tree). Same folder works for Claude, Codex, Grok,
  and Antigravity.
---

# aib newproj — 建專案卡(有指名席位才綁)(四家通用)

使用者說「新增專案」「創專案」「開專案」「綁專案」,指的都是這一支:`aib newproj`。

**一個專案 = 一張專案卡 + 它對應的資料夾。** 專案卡記四層東西:目標與紅線、專案記憶、工作狀態、接手包;
資料夾是這個專案的筆記放的地方。一支指令做完:
建專案卡(記下資料夾)→ 從那一席綁的人格產生「目標與紅線」初稿 → 綁到席位(資料夾一起綁)→ 記進帳本。
加 `--deliver` 會再把接手包寄給那一席。
**沒指定席位時只建專案卡**,不綁任何席、不開新席(the maintainer 2026-10-01,見下方「沒說綁哪一席」)。

程式正本:`tools/project/new_project.py`(右欄選單的「新開專案」也是同一支)。

## 先確認這台的 AIB 有這個指令

```bash
aib newproj --help
```

印出 `usage: aib newproj` 才往下做。印的是開席說明(`aib <provider> …`)代表這台的 AIB 版本還沒有
`newproj`:停下來告訴使用者要先更新 AIB,不要改用別的方法硬做。

## 名稱對照

| 名稱 | 是什麼 | 用什麼做 |
|---|---|---|
| 專案 | 專案卡 + 對應的資料夾 | **`aib newproj`(本技能)** |
| 右欄 `🧠` 那一行 | 綁了專案卡就顯示專案標題,否則顯示資料夾 | `[綁專案]` `[換專案]` 用滑鼠只換資料夾 |
| `aib project` | 計畫樹的狀態與排程 | 不是本技能 |

## 先弄清楚五件事(能從對話推就不要再問)

1. **專案標題**:例「我的部落格 SEO」。同標題的專案已經有一張時,會沿用那一張,不另開。
2. **綁到哪一席**:使用者有指名時,抄右欄或 dm 抬頭的地址,寫成 `<艙位碼>/<席碼>`,例 `abcd/12345`。
   **沒指名就不要綁、也不要開新席、不要問**:不帶 `--seat` 只建卡(見下方「沒說綁哪一席」)。
3. **專案的工作目錄**:那個專案的程式或網站所在的資料夾(用來記倉庫指紋)。不給就是你現在所在的資料夾。
4. **你自己的署名**(要綁席位時才需要):`<角色>@<你的席碼>/<引擎>`,例 `root@54321/Claude`。這是帳本署名,不是目標席。
5. **對應的資料夾**:這個專案的筆記要放哪個資料夾。可以給路由代號(`core.handoff`)或資料夾名稱
   (`系統記憶交接`)。不給就沿用那一席現在綁的資料夾;兩邊都沒有,專案卡就不記資料夾(指令會明講)。
   資料夾只能從既有清單選,這支指令不會新開資料夾。

**先綁人格再建專案**:初稿的紅線是從那一席綁的人格卡抽出來的,資料夾也要綁了人格才綁得上。
那一席還沒綁人格:初稿是空的,帶 `--folder` 會被拒。先用技能 aib-newrole 綁人格。

## 做法

```bash
# 1. 先試跑:不寫任何檔,看會綁到哪一席、是新建還是沿用
aib newproj --title "我的部落格 SEO" --seat abcd/12345 --cwd /path/to/site \
  --folder "系統記憶交接" --actor "root@54321/Claude" --deliver --dry-run

# 2. 輸出的 seat、created、folder、delivery.address 都對,拿掉 --dry-run 真跑
aib newproj --title "我的部落格 SEO" --seat abcd/12345 --cwd /path/to/site \
  --folder "系統記憶交接" --actor "root@54321/Claude" --deliver
```

| 參數 | 說明 |
|---|---|
| `--title` | 專案標題。新建必填;同標題已有一張就沿用 |
| `--seat` | `abcd/12345`(建議)、`@12345`、或席位檔的 seat_id。**不給 ⇒ 只建專案卡,不綁任何席** |
| `--new-seat` | **只有使用者明講「開一個新席來綁」才用**:開一個新席來綁(與 `--seat` 二選一);`--engine` 選引擎,預設 claude。不是預設 |
| `--actor` | 你的署名,綁席位時必填(只建卡不用);要綁卻不給也沒設 `EZ2_OWNER_SIG` 就停手(rc=2),不會亂猜 |
| `--cwd` | 專案的工作目錄(程式或網站所在處);不給=目前資料夾 |
| `--folder` | 專案對應的筆記資料夾:路由代號或資料夾名稱。專案卡會記下來,那一席的資料夾也一起綁上 |
| `--project-uid` | 綁一張既有的專案(`proj:` 開頭那一串),不新建。標題撞名時用它指定。要配 `--seat` |
| `--persona-uid` | 改用另一張人格卡產生初稿;不給=那一席現在綁的人格 |
| `--deliver` | 綁完用 aib dm 把接手包寄給那一席。要配 `--seat` |
| `--unbind` | 把這一席的專案解掉,專案的資料夾也一起從這一席解掉;專案卡留著,不會被刪。要配 `--seat` |
| `--dry-run` | 只試跑 |

## 驗收(每一項都要讀回結果,不要只看 rc=0)

**只建卡(沒給 `--seat`)**:JSON 的 `ok: true`、`bound: false`、`seat: null`、`reply` 是「已創立 <標題> 專案(uid proj:…)」,
`card_path` 那個檔在。把 `reply` 那一句原樣回給使用者,再加一句「目標與紅線是初稿、還沒生效」;下面 1–3 不用做。

**有綁席位時**:

1. 指令最後印的 JSON:`ok: true`、`seat` 是目標席、`created` 符合預期(新建 true/沿用 false)、
   `delivery.ok: true`、`delivery.stage: delivered`。
   - `delivery.ok: null` 加 `delivery.no_retry: true`:aib dm 回 3/4,**字已送出或已進對方框、只是沒能確認**。
     **不要重跑**(會疊兩份),直接跳到第 3 步等對方回覆。
   - `delivery.ok: true` 但 `stage` 是 `queued` / `sort-queued`:對方在忙,排進佇列了,不要重跑。
   - 每跑一次 `expect_reply` 的時間都會變;對方回的是**哪一次**寄的那句就對哪一次。
2. 讀 `card_path` 那個檔:標題對、`folder` 是要的資料夾、`layers.constitution.approved` 是 `false`。
3. **等對方回一行「已接手 <專案標題> 從 <時間>」**,要等於 JSON 裡 `delivery.expect_reply` 那一句。
   寄達只代表字進了對方的框;沒收到這一行,就回報「已綁、對方尚未確認接手」。

## 一定要告訴使用者的事

- **目標與紅線是初稿,還沒生效。** 要本人核准;初稿檔在 JSON 的 `candidate_path`。
  這支指令不會核准,你也不要替使用者核准或自己把 `approved` 改成 true。
- **怎麼核准(告訴使用者這一段):** 本人在**自己的終端機**(駕駛艙外的一般終端機視窗,不是 AI 席)跑
  `aib newproj --approve <專案 uid>`。它會把初稿的目標、紅線、本人裁定印出來,要本人親手再打一次專案 uid 才生效;
  打錯就取消、什麼都不改。生效後卡上會寫 `approved: true`、`approved_by`(登入名)、`approved_at`(時間),
  `human_rulings` 多一筆核准紀錄,帳本也記一筆。
  - **Mac 上可以不換終端機:** 使用者在對話裡明講要核准(「核准」「全核準」),你可以替他跑
    `aib newproj --approve <專案 uid>`:它會印出目標與紅線,並在 Mac 上跳 **Touch ID 視窗**(或登入密碼欄),
    要本人按指紋才生效;本人按取消、等太久、或不在電腦前,就是沒核准(rc 2)。跑之前先把目標與紅線給他看,
    跑完照實回報:成功才說「已核准」,沒過就說沒過、原因照印。卡上會記 `approved_via: touchid`。
  - 不是 Mac(或設了 `AIB_TOUCHID=0`):把上面那行指令給他,請他自己去終端機跑。AI 席跑這行會被擋(rc 2)。
  - 要先改目標或紅線:改 `candidate_path` 那個初稿檔(使用者自己改,或使用者明講要你改),改完再請他核准。
- 專案的「目標」一開始是空的,只有紅線是從人格抽的。請使用者告訴你目標,或提醒他去補。

## 回報給使用者

只建卡時:回 JSON 的 `reply` 那一句(例「已創立 我的部落格 SEO 專案(uid proj:0123456789abcdef)」)加上初稿還沒核准,
不要多加「要綁到哪一席?」的追問。

有綁席位時:第一句講現在能不能用。接著講:綁到哪一席、是新建還是沿用、專案卡放哪裡、初稿還沒核准、
對方有沒有回覆已接手。沒驗證的事要明講沒驗。

## 結束碼

| rc | 意思 | 已經發生了什麼 |
|---|---|---|
| 0 | 全部完成 | 有 `--seat`:卡、席位綁定、帳本(與寄送)都做了;沒 `--seat`:只建了卡(同標題已有就什麼都沒動) |
| 1 | 建卡失敗(資料夾或卡的形狀有問題) | 看 JSON 的 `error`;綁定沒做 |
| 2 | 寫檔前就拒絕:席位認不出來、沒署名、標題撞名、指定的專案不存在、資料夾不在清單或綁不上、沒有帳本、`--unbind`/`--deliver`/`--project-uid` 沒配 `--seat` | 什麼都沒寫 |
| 6 | 綁好了但接手包沒寄到(aib dm 回 2 等:一個字都沒進對方框) | 專案已綁;看 `delivery.reason`,修好後重跑同一行(會沿用同一張卡)。aib dm 回 3/4 **不是** rc=6:整支回 0、`delivery.no_retry: true`,不要重跑 |

## 常見狀況

- **綁自己這一席**:不要加 `--deliver`(不寄信給自己)。綁完讀 `card_path` 照做。
- **同一個專案要給第二席**:同一行指令換 `--seat`,標題照舊;會沿用同一張卡,卡不會被改。
- **標題撞名**(同標題有兩張以上):指令會列出每一張的 `proj:` 編號並停手;改用 `--project-uid` 指定。
- **換專案**:直接對同一席再跑一次、給新的標題或編號;不用先解綁。
- **一個專案一個資料夾**:專案卡已經記了資料夾,再給不同的 `--folder` 會被拒。專案卡還沒記資料夾時,
  帶 `--folder` 重跑一次就會補記。
- **資料夾名稱對到不只一個**:指令會列出每一個的路由代號並停手;改用路由代號指定。
- **同一個席碼在兩個艙都活著**:會被拒並列出兩席,改寫完整地址 `<艙位碼>/<席碼>`。

## 沒說綁哪一席 ⇒ 只建卡,不綁、不開席(the maintainer 2026-10-01,取代 09-30「沒說就開新席」)

使用者只說「建一個 XX 專案」、沒指名哪一席時,**不要問、不要綁到你自己這一席、也不要開新席**,不帶 `--seat` 就好:

```bash
aib newproj --title "我的部落格 SEO" --cwd /path/to/site [--folder "系統記憶交接"]
# 印出:已創立 我的部落格 SEO 專案(uid proj:0123456789abcdef)
```

- 只寫專案卡與「目標與紅線」初稿;不綁任何席、不改席位檔、不記帳本、不寄接手包、不需要 `--actor`。
  沒有席位就沒有人格可抽,初稿的紅線是空的(要用某張人格就加 `--persona-uid`)。
- 同標題的卡已經有了 ⇒ 什麼都不動,回「<標題> 專案本來就有了(uid …),沒有另開、卡沒動」。
- 之後使用者指名要綁哪一席,再跑同一行加 `--seat <艙位碼>/<席碼> --actor "<你的署名>" --deliver`(會沿用同一張卡)。
- `--new-seat` 仍在,但**只有使用者明講「開一個新席來綁」才用**:
  `aib newproj --title "<標題>" --new-seat [--engine grok] --actor "<你的署名>" --deliver`。
  **不能配 `--folder`**,同標題舊卡記了資料夾也不行:新開的席還沒綁人格,綁不上資料夾,會在開席前被擋(rc=2,不會留下新席)。
  要資料夾就先開席綁人格,再用 `--seat` 綁專案。
  它會在你所在的艙開一個新席(不切換任何人的畫面),等 AI 啟動完成(通常一兩分鐘,最多 300 秒)再綁;
  等太久會停下來說明,等右欄出現那一席之後改用 `--seat <艙位碼>/<席碼>` 再跑一次。開新席會用那個引擎的額度。

## 只是想試試看,不要用別人的正式席位

綁定、解綁都會永久寫進帳本(帳本只能增加,不能刪),也會換掉那一席的專案與資料夾。
只是要驗證指令、練習、或寫測試時:

- 開一個測試用的艙:`aib claude safe test new`(或其他引擎),在那一艙裡的席位上試。
- 試完用 `[X]` 關掉那一艙。
- 真的要在某一席上試,先問使用者,並在試完後換回原本的專案與資料夾。

## 這份技能怎麼裝

正本只有 AIB 倉內的 `skills/aib-newproj/` 這一份,各家 AI 的技能資料夾放的都是指向它的連結:

```bash
"$EZ2_AIB_ROOT/tools/aib-install-skills" aib-newproj
```

## 不要做

- 不要手改 `~/.ez2-aib/projects/` 底下的專案卡,也不要手改席位檔來綁專案。
- 不要自己建立或修改「已核准」的目標與紅線;核准權在本人。
- 使用者沒明講要核准,不要自己跑 `aib newproj --approve`;也不要手改卡上的 `approved` / `constitution_status` 代替它。
  Mac 上跑它只會跳 Touch ID 視窗等本人按,AI 不能替本人按、不能叫別的程式代按;不是 Mac 就請他自己去終端機跑。
- 不要用 `--unbind` 之後再去刪專案卡資料夾;專案記憶是只增不改的。
- 不要直接對別席的視窗貼字;寄送一律走 `--deliver`(它走 aib dm)。
- 不要在沒有 `--dry-run` 看過結果之前,對別人的席位真跑。
