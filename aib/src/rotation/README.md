# 艦長交接:把協定做成機器閘門(E6.3 + 08-15 延伸)

the maintainer 直令(2026-08-15):「新舊艦長交接、顧問職責都應該寫成 hook,而不是只有靠 ai 記憶。」

本目錄三支:

| 檔 | 做什麼 | rc 分層 |
|---|---|---|
| `rotation.py` | checkpoint / handoff / 兩階段驗收 / 顧問期 | 0 成立、2 形狀或紀律不合、3 待確認或該輪替 |
| `handover_probe.py` | 偵測「宣稱是艦長但拿不出驗收紀錄」 | 0 都有背書、2 有宣稱無紀錄、3 只到待確認 |
| `lifecycle.py` | 席位生命週期:STAGES v2 六級 + 機器 CTX 唯一來源(`read_machine_ctx`/`rotation_mode`) | stage 0 / 2 拿不到 ctx |

---

## 一、★ 這道閘擋的是什麼、不擋什麼(先讀這節,不要跳)

**擋的是「無紀錄的交接」。不擋「偽造的交接」。兩者差很多。**

08-15 上線的兩階段驗收,把門檻從

```
一次呼叫、一方、零紀錄            →   兩次呼叫、兩筆紀錄、且偽造者要寫得出對方的 sig
```

**但寫得出對方 sig 的人,照樣可以單方走完兩階段**,產出一份 `handover_probe.py`
查得到、看起來完全合格的驗收紀錄。

原因是 **codex 審 E6.3 高1 仍然開著**:`confirmed_by` 至今只驗格式與 seat 前綴,
**沒有簽章、session binding 或呼叫者身分驗證**。真正關死要 session binding
(`src/binding/`),不在本模組,已列為獨立未結項。

> **加防線的同一輪要問:它會遮住什麼?**
> 這裡的答案很具體——**這道閘會遮住高1**。上線後一定會有人把「有閘門了」
> 讀成「偽造交接已經擋住了」。**本節存在的唯一目的就是擋掉那個誤讀。**

### 這一輪關掉/沒關掉什麼

| 審單發現 | 狀態 |
|---|---|
| **高5** 驗收不落檔,帳本只剩反查不回去的 hash | **已關**:`accept` 落檔,原文可從持久資料重建 |
| **高3** 一次呼叫即 accepted,提交者與確認者同一人 | **已提高**(兩階段;非「已關」,見高1) |
| **高1** `confirmed_by` 無簽章/session binding | **仍開**,獨立未結項 |
| 高4 讀端無共享鎖 | 08-14 已修(`LOCK_SH`),本輪逐條實查確認 |
| 中1~中4、低1 | **本輪未查**,現況不明 |

---

## 二、兩階段驗收怎麼跑

```
交棒者                                     接棒者
  |                                          |
  |-- checkpoint(含陷阱節,至少一條) ------>|
  |-- handoff --out h.json ---------------->|
  |                                          |
  |                     accept --root R --handoff h.json \
  |                       --understanding u.json --by-seat <接棒席>
  |                                    -> pending_confirm(rc=3),三句在此凍結
  |                                          |
  |<-- confirm --root R --seat-to <接棒席> \  |
  |      --acceptance-uid acc_... --confirmed-by <交棒者 sig>
  |                                    -> accepted(rc=0)
```

查狀態:`acceptance-status --root R --seat-to <席> [--task T]`。

**為什麼一定要兩次呼叫**:同一個呼叫者同時控制三句與確認人時,「交棒者背書」
只是這個呼叫者自己寫的一個字串——三句理解驗收從來沒有形成雙方協定。

**三句在第一階段凍結**:`confirm` 讀既有 pending 紀錄再轉態,不是憑參數重建,
所以背書者只能背書「已經寫下的那三句」,不能順手換掉內容。

**存放**:`<root>/<接棒席>/acceptances.jsonl`,append-only,同 uid 以最新行為準。
放在接棒席目錄下是因為這份紀錄的消費者是「這個席位可不可以就任」,問的人手上
有的就是接棒席名。

---

## 二之後:偵測器的「艦長」定義與面板同源

面板(`src/cockpit/pane-right-ai.sh`)的判準是
**`role in CAPTAIN_ROLES` 或 `captain_assigned`** —— 也就是**只要 role 落在
艦長角色集就算艦長,不必寫 `captain_assigned`**。

`handover_probe.py` 第一版只認 `captain_assigned`,於是**畫面上正戴著皇冠的
那一席完全不在覆蓋範圍內**。後果不是漏一席:

> **任何人只要不寫 `captain_assigned`、靠 `role=root` 當艦長,就自動繞過這道
> 治理閘,而且畫面上看起來完全正常。那不需要偽造,只需要什麼都不做。**

已修:讀同一個環境變數 `EZ2_AIB_CAPTAIN_ROLES`(預設 `root`)、用同一條規則,
並記 `claim_via`(自己宣告的 vs 靠角色推定的)。兩邊不能 import(對面是
shell 內嵌 python),所以由測試釘住對面那條規則沒變——**釘「兩邊同一條規則」
這件事,不釘某一種寫法**。

修正後對實艦隊的覆蓋:**1 席 → 7 席**。

**面板只有單一判定點**(2026-08-15 由指揮席 a seat 獨立複查:
`pane-right-ai.sh:402` 定義、`:443` 唯一判定,其餘 `captain_assigned` 出現處
只是解析)。本檔作者當時只查了 `CAPTAIN_ROLES` 這個符號的出現處,不敢下
「只有一處」的全稱結論;該未知由指揮席查完關掉。

★ **前瞻風險(還沒發生,但已看得到)**:pm2 的 v0.20 分支會改到那一段,
把判定式擴成

```python
and (r[2] in CAPTAIN_ROLES or r[3]['captain_assigned'])
and (not r[3].get('unassigned')
     or (r[3].get('role_from_live') and r[2] in CAPTAIN_ROLES))
```

那是**收窄「誰顯示為艦長」**(未編制席要靠活窗角色才留在艦長段),
**沒有放寬「誰算宣稱」**,所以本模組的判準仍然正確。
但要注意:**本檔的釘子在那個改動下不會紅**(它斷言的是「有一行同時提到
`CAPTAIN_ROLES` 與 `captain_assigned`」,v0.20 之後那行仍在)。
釘子擋的是「規則整條消失或換來源」,**擋不住「規則被加上額外條件」**——
v0.20 合入後應重看一次兩邊是否仍等價。

## 三之前:偵測器的治具取自實況,而實況比「有人混進來」更值得擔心

```
席位檔 w15.json:seat_id=b7227、role=root、captain_assigned=true
交棒來源:a seat 於 CTX 95% 正當交棒、pid 活著
全機器 acceptance 紀錄:0 份
```

**這是一個經正當交棒、活著的艦長席。** 偵測器要紅的原因**不是這個席位有問題**,
是**這條路徑本來就不留痕**——一次完全合規的交棒,在機器裡沒有留下任何可查的
東西。那不是意外,那是預設行為。

## 三、★ 已知缺口:兩套席位命名接不起來

實查 2026-08-15 兩邊資料:

```
席位檔(captains-ctl --assign 寫的)   role=root    seat_id=b7227   window=@15
輪替存放(rotation.py 寫的)           cl3  dev1  flt2  pm  root2
```

**`root2` 不是 `root`,也不是 `b7227`。** 但問題比「詞彙不同」更深——
**兩者的「範圍」不同**:

```
席位身分  = per-instance(~/.ez2-aib/instances/<N>/seats/;08-15 實查 19 個 instance,只有 5/6/7 有 seats)
輪替存放  = 全域(~/.ez2-aib/rotation/,沒有 instance 維度)
```

所以 `window_id` **不是唯一的 key**。08-15 實測同一個 `@4` 是兩個不同的席:

```
instances/7/seats/w4.json   @4  seat_id=15368  role=pm    captain_assigned=true
~/.ez2-aib/seats/w4.json    @4  seat_id=b288f  role=dev2
```

**這不是「還沒建對照表」,是一個 per-instance 的 key 在結構上索引不到一個
全域的 store——建了對照表也會錯**,除非 key 帶上 instance。

`handover_probe.py` 的處理是 fail-closed 並且**明說是哪一種紅**:
對照不出來 = `unresolved`,計入未背書。**不猜對照規則**——猜錯會給出
「已背書」的假綠,比紅燈難發現得多。

**`role` 一律不可用來對照**(多個窗共用同一 role,拿它對照等於讓任何一次
交接替所有同 role 的窗背書)。★ 這條 08-15 出過事:README 這樣寫,而
`resolve_seat` 的查表迴圈裡有 role,`--map root=root2` 讓每個 root 窗都變
backed。已修——**role 移出查表**,並改以「對照鍵沒對到任何席位就報錯」偵測
(形狀分不出 role 與 seat_id:`root`/`dev1`/`flt2` 都符合 `[0-9a-z]{4,8}`,
**分不出來的判準就不要假裝在分**)。

要消掉 `unresolved`:用 `--map @15=root2` 顯式宣告(★ 左側**現在就有歧義**
——哪一個 instance 的 @15?本輪不改介面,用的人要知道 `--map` 只在單一
instance 情境下有意義),或由上游把兩套命名收斂成一套(治理決定,不在本模組)。

---

## 四、繼任者判準(hook 只產候選,不指派)

★ **指派是人的動作。** 本節的判準只用來產候選名單與提問,**不自動指派**——
與「自動化只推到卡片產生為止」同一條紀律。

### 主判準:兩個數字(機器可量)

> **對任一結論,要能回答:「你看了幾個?」「你的結論涵蓋幾個?」**
> **兩個數字差距越大可信度越低;差距大而沒有主動講出來,是紅旗。**

**接在既有的「讀取量三行」上,不另造指標**——三行量的是「我看了多少」,
這條只是把它推廣成「**我看的量撐不撐得起我說的話**」。

一條分辨規則,實務上最常用到:

- **存在性主張**(「有過期條目」「至少有一個 X」)—— 一個反例即足,取樣小也撐得住。
- **全稱主張**(「其餘六條仍成立」「全庫沒有」)—— 要全查,否則正確講法是
  「**我在這 N 個位置沒找到**」,不是「不存在」。

### 三條訊號的機器可量性(逐條誠實標註)

| 訊號 | 機器量得出來嗎 | 怎麼量 / 為什麼不行 |
|---|---|---|
| 讀取量誠實 | **可量** | 交件有沒有三行(讀取/依據/未讀);分母是不是真的(對得上實際檔數) |
| 取樣 vs 涵蓋的差距 | **可量** | 兩個數字都在交件裡;差距大而未主動聲明 = 紅旗 |
| 主動說「我沒做什麼」 | **半可量** | 只能量「有沒有未讀/未驗證段落,且具體到檔名或範圍」。**量不到它是否誠實**——具體到檔名這件事可以學會表演 |
| 推翻過派工單的前提 | **量不出來,退回人判** | 要判斷「這句話是不是推翻了前提」需要理解兩段文字的語意關係,不是字面比對。**寧可少一個誠實的指標,不要多一個假的** |

### 指派前的強制一問

**「你目前的結論裡,哪一條你沒有親自驗證過?」**

候選人的答案本身就是判準。這一題存在的理由是一次實例:選人十分鐘後,另一席
送來一則推翻該次根因判斷的訊息——**做的是同一件事的更強版本**(不只標邊界,
是去查了、發現指揮席錯了、而且在動手前講)。原判斷是在資訊不完整下做的,
而這一題會把那個差異在指派前逼出來。

---

## 五、驗收怎麼跑(逐支獨立取 rc,不接管線)

```
python3 tests/test-rotation.py                        ; echo $?   # 164 checks
python3 tests/test-lifecycle.py                       ; echo $?
bash    tools/probes/e6.3-acceptance-probe.sh         ; echo $?   # 12 條分級 rc
python3 tools/probes/e6.3-handover-mutation.py        ; echo $?   # 12 個突變
python3 src/rotation/handover_probe.py                ; echo $?   # 對實資料(rc=2=判準)
```

★★ **突變全綠不等於驗透了**(b7227 覆審點名):突變做在**已經被寫進斷言的
行為**上,證明的是「現有斷言承重」,**不是「斷言蓋得夠廣」**。08-15 實例:
突變 10/10 全綠的同時,外部覆審打出兩個地基洞(掃到零筆回綠、艦長定義比
面板窄)——**兩個突變都抓不到,因為當時根本沒有那兩條斷言。**

★ 探針的判準一律 `-eq <期望碼>`,**不准 `-ne 0`**:`accept` 的「待確認」
成功碼正好是 3,`-ne 0` 在守衛壞掉時仍然成立(08-15 覆審抓到的 P0,
實證:拿掉空白句守衛 → rB 由 2 變 3 → 探針仍綠)。
**檔頭寫「0/2/3 三態不得混為一談」,判準自己要先做到。**

**突變證明**每道新閘拿掉必須轉紅,而且比對 `FAIL <斷言名>` 字面——
不看 rc 就下結論(rc=1 可以來自任何一條紅),錨點取不到一律當失敗
(漂掉而靜默跳過的突變測試會一直回報「全過」,那是最貴的一種假綠)。

---

## 五之後:兩條通則(這個 repo 08-15 一天內各付了不只一次錢)

### 通則一:**為了錯誤理由而紅的閘會被關掉,那跟 fail-open 的下場一樣**

fail-closed 只保證**方向**,不保證那個紅指得出真正的原因。指錯原因的紅會先
耗光人的信任,然後整道閘被拿掉——結局與一開始就放行相同,只是多花了時間。

本輪實例:`default_rotation_root()` 曾吃 `EZ2_AIB_INSTANCE_HOME`,於是在
cockpit 真實環境下每一席都判 `unbacked`——**紅的方向對,理由是錯的**。

### 通則二:**「文件說不可以、碼允許」沒有任何東西在檢查**

同一份交付裡,散文寫下的禁令與程式碼實際允許的行為之間,**沒有機器在對帳**。
這種不一致比單純的 bug 貴,因為**它讀起來像已經擋住了**——審查者讀到那句
禁令就不會再去看碼。

2026-08-15 一天內四個實例(不同交付、不同作者):

1. workflow 註解指名一道**不存在**的閘;
2. 發版閘檔頭 L45 說 `core.hooksPath` 指向 A,L43 的安裝指令寫 B(裝到 git 不看的目錄);
3. 本檔 §三 寫「`role` 一律不可用來對照」,而 `resolve_seat` 的查表迴圈裡有 role;
4. 一支渲染腳本所有 `note()` 的 fail-visible 留痕,在**成功渲染路徑上會被刪掉**。

**可機械化的形狀**:凡是散文裡出現「一律不可 / 禁止 / 絕不」而指的是本模組
自己的行為,就該有一條會紅的斷言指著它。本輪對第 3 條補了(`對照鍵不吃 role`
+ 突變 M10);其餘三條屬別的交付。

## 六、本輪沒有做的(候裁,不要當成漏掉)

- **交接門檻改用「剩餘 token」不用百分比** —— **停手候 the maintainer**。
  `DEFAULT_ROTATE_THRESHOLD = 90` 目前同時當兩種東西用:`check` 的觸發線(上限)
  與 `validate_handoff` 的地板(防太早交)。換 token 制會讓兩者分家:1M 視窗上
  「餘裕<5 萬」≈95%(比 90 嚴),200k 視窗上 ≈75%(以百分比看比 90 鬆)。
  且與下一條在數線上互相擠壓。
- ~~**顧問期改 CTX 96/97** —— 停手候 the maintainer~~ → **已裁(the maintainer 2026-08-17,PLAN
  §2.3):96,CTX 制**。實作見 §七 G1-07:解除只認 `machine ctx ≥ ADVISOR_STOP_CTX(96)`
  或 operator `--manual-release`;`ADVISOR_PERIOD_MS` 24h 降為**後備天花板**(到了
  只標 `needs_human`,不自動解除);`validate_handoff` 拒自訂顧問期照舊——96 與 24h
  都是常數,不吃參數,防偽造那條沒有鬆。
- **`captains-ctl.sh --assign` 的前置閘** —— 落點在 `src/cockpit/nav/`,不在本
  模組範圍,另派。本模組交的是那道閘要查的真相源與偵測器。
- **33 道未被任何斷言釘住的守衛(繼承來的)** —— 08-15 機械掃描:把
  `rotation.py` 的 64 處 `raise RotationError` 逐一換成 no-op 跑全套,31 道
  被釘住、33 道拿掉之後測試仍全綠。多數是難測的 I/O 路徑,但**不是全部**
  ——例如 `seat path escapes root` 未被釘住(測試裡那支叫「高4 路徑逃逸」的
  餵的樣本**先被席位名字元閘攔掉,根本走不到逃逸檢查**)。
  ★ 判準:**繼承來的未釘守衛可以排期,本輪新加的不能沒有釘子出貨**——
  前者是舊債,後者是新的假綠。本輪新加的兩道(存放名白名單、rotation base)
  已各配釘子與突變(M7/M8)。
- **codex 審 E6.3 高1**(`confirmed_by` 無簽章/session binding)—— 仍開著,
  獨立未結項;它是 §一 那條誠實邊界的成因。


---

## 七、G1 艦長生命週期 v2:五本帳與 CLI(the maintainer 2026-08-17 拍板;PLAN-captain-lifecycle-0817)

協定:50% 立儲 → 60% checkpoint → 75% 硬性完整總結+共治 → 85% 收斂 → 90% 退位成顧問
→ 96% 顧問停止(遺詔→投遞→compact→轉世)。**第一刀=制度可執法**(狀態機+帳+閘+偵測),
自動執行(自動 compact/晉升/提醒投遞)是第二刀。

### 機器 CTX 是唯一合法的自動來源

`lifecycle.read_machine_ctx(uuid)` 只讀 `$EZ2_CTX_DIR/<uuid>.pct`(hook 產):schema=
`ez2-context-gauge-v1`、`session_id==uuid`、絕對路徑、regular file、≤64KB;`observed_at`
只收 UTC(`Z`/`+00:00`),無尾碼/其他 offset **不猜本地**→stale;≥6h stale;比 now 晚 >300s
視為無效。**拿不到或 stale → `rotation_mode=manual_only`(fail-closed,the maintainer 明示):
不進自動輪替、只能手動;自報 `--ctx-pct` 只走人工流程並標 `ctx_source=self_reported`,
不得驅動任何自動轉換。**
誠實邊界:`.pct` 與席位同 uid 可寫——這些檢查是防誤用/防混入,不是硬隔離(第二刀)。

### 五本帳(`~/.ez2-aib/rotation/<seat>/`,全部 append-only,全部走 `_append_jsonl` 同一條硬化路徑)

| 帳 | CLI | 閘(rc=2) | 偵測(probe code) |
|---|---|---|---|
| `scout.jsonl` 立儲 | `rotation.py scout --root R --seat S --successor-seat T --successor-role pm --reason … [--ctx-pct N\|--uuid U]` | T≠S、reason 非空、role ∈ 主管集(`captain_roles()`+root/pm/pm2/pm3) | `no_scout_at_50` 🔴、`successor_not_same_fleet` 🔴 |
| `checkpoints.jsonl` `kind=full_summary` | `rotation.py checkpoint … --kind full_summary` | kind ∈ {checkpoint,full_summary};缺欄=checkpoint | `no_full_summary_at_75` 🔴 |
| `delegation.jsonl` 交辦 | `rotation.py delegate --root R --seat S --to-seat T --work …` | T≠S、work 非空 | (共治期零交辦=第二刀) |
| `patrol.jsonl` 巡查 | `rotation.py patrol --root R --seat S --visited T… --finding … [--delegated …]` | visited ≥1 不重複、finding 非空 | `patrol_overdue` ⚠️:上次巡起 ctx +10% 或 6h 先到者;從未巡=逾期(常數 `PATROL_EVERY_PCT/MS`,不吃參數) |
| `testament/<at_ms>.md` + `testament-receipts.jsonl` 遺詔 | `rotation.py testament --root R --seat S --file X.md [--receipt ID]`;`testament-status` | 契約 `docs/testament-format.md`:≤20480B、六必含節逐字各一、正文非空;O_EXCL 不覆寫;收據 `^[A-Za-z0-9._:-]{6,128}$` | `advisor_96_no_receipt` 🔴;`compact_ready` = 遺詔在 ∧ 收據綁著存在且 sha256 相符的檔(status rc 0/3/2)。★ 收據**只驗格式**:第一刀擋「忘了投遞就 compact」,**不擋「填了假收據」**(可反查的 dm msg_id/ev_ 收據=第二刀) |

`check --uuid U`:機器值優先,`auto_rotate` 只在 `rotation_mode=auto` 才可能 true。
`advisor-status --handoff F [--uuid U|--manual-release]`:`released_by` ∈ {ctx96, manual, null};
`needs_human` = 仍在顧問期且 24h 天花板已過。`lifecycle.py stage --uuid U [--ctx-pct N]` 同一套來源規則。

`handover_probe.py` 每筆艦長宣稱附 `lifecycle:{rotation_mode, ctx_pct, codes[]}`;
`manual_only` 是 info **不計紅**(fail-closed 是不自動轉,不是把人工席判死);red→rc 2、warn→rc 3。

### 驗收

```
python3 tests/test-lifecycle-ledgers.py   ; echo $?   # 五本帳+CTX 接線+顧問 CTX 制+probe (a)-(f)
python3 tests/test-lifecycle.py           ; echo $?   # STAGES v2 + read_machine_ctx 硬化
python3 tests/test-rotation.py            ; echo $?   # 既有 164 checks(一條「期滿」改 CTX 制語意)
```
每道閘的突變證明列在對應 commit 訊息(714d55e、c148d40 及後續);qa 釘子在 `tests/test-rotation-v2.py`。
