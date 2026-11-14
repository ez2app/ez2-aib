# aib-slice sweep — 介面(S73a,1ced7)

`aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--fast] [--out FILE] [--due-out FILE [--max N]] [--gate] [--hours N] [--gh PROGRAM | --no-gh]`

只讀:fetch、讀 git、用 `gh api` GET 讀開著的 PR、讀 pending 夾的審查 / 收據 / ack / 建置中登記。不推、不開 PR、不刪樹。
輸出一個 JSON 物件 `aib-slice-sweep.v1`,正本是 `schema/sweep.v1.json`;下面的欄位表跟它逐欄對照,`tests/iface.rs` 比對兩邊欄位集合,不一致就紅。

## 結束碼
- 0 掃完;2 掃不完整(fetch / gh / 某支分支讀不到),JSON 照印、`complete=false`,`--gate` 也是 2 —— mergewatch 把 2 當不合;3 只在 `--gate`:有類 1 / 2 / 3;64 用法錯。

## 六類(由上往下,先中先算)
5 merged(獨有 patch 全在 main 或開著的 PR)→ 6 ack(sweep-ack.txt)→ 6 可放棄(PR 關掉沒合、關在現在這個頭;s33)→ 3 fix(開著的 PR 衝突 / PR checks FAILURE / 頭上審查 H/M > 0 / 頭上收據封印不符 / 頭上收據紅)→ 1 merge_first(頭上審過 + 收據綠 + 已推 + 不衝突 + PR 開著)→ 2 push_or_pr(頭上有審查或收據,但沒推或沒開 PR)→ 6 stale(超過 --hours 沒 commit 且沒開著的 PR)→ 4 in_progress。`--gate` 擋 1 / 2 / 3。

## pending 夾讀的檔
- `REVIEW-*.md` 第一行:`結論: X 頭: <40> H/M/L/N: a/b/c/d …`。
- `TEST-*.txt`、`RECEIPT-*.txt|.log`:前 5 行的 40 碼,或 `== HEAD=` / `HEAD ` / `HEAD-END ` 行;`xxx_rc=N`、`STEP xxx rc=N`、`RC xxx=N`(`KNIFE` 行、`head_rc` / `base_rc` 不算;`RC knife_X=N` 咬到(非 0、後面不是 `INVALID`)不列,沒咬(0)、`INVALID`、`anchor-missing` 或沒有數字 ⇒ 紅鍵 `knife_X`,含 `ANCHOR-MISSING` 的行 ⇒ 紅鍵 `anchor_missing`,receipt-run.sh 的 `KNIVES-RC N` / `MISSES-COUNT N`(沒有等號)不是 0 ⇒ 紅鍵 `knives_rc` / `misses`:刀沒咬的收據不算綠(S79,519d0 11-15);後面帶 `(want N)` 的以 N 判、`(want non-0)` 是 0 才紅、`(report only)` 不算紅);`HEAD-END <頭>` 完成記號;最後一行 `RECEIPT-SHA256 <64>`。
- `REVIEW-*.md` 內 `非閘理由: …` 一行(S73j)。
- pending 夾或檔讀不到 ⇒ errors(`pending …`)。
- `sweep-ack.txt`:`分支<TAB>原因`。
- `build-claims.tsv`:`分支<TAB>席<TAB>開始時間<TAB>說明`;時間沒寫時區 = 台北;超過 6 小時不算。

這些都是同帳號可改的檔:讀來分類,不是防線。

- `--out FILE`:sweep 的 JSON 寫進檔(先寫旁邊的暫存檔再改名,讀的人只會看到舊檔或新檔,不會看到半份),不印到畫面。`--due-out FILE`:同一輪順便把 due(`aib-slice-due.v1`,沒帶 `--seat` 就是全艦)寫進另一個檔,這個檔預設寫**全部項目**(不是 due 指令預設的 8 筆;8 筆是給回覆尾巴看的,最舊的在前,開著 PR 的新項目會被截掉,519d0 11-15 04:1x 實測 #748 的紅格因此看不到);要限制才給 `--max N`(寫在 `--due-out` 後面)。給保持快取的那一邊用:掃一次、兩個檔都更新(S79,519d0 11-15)。寫不進去 rc 2。

- 收據怎麼認(IFACE-s79 r11 第 5 點):檔名 `TEST-…` / `RECEIPT-…` 結尾 `.txt` `.out` `.log`,或檔名含 receipt 而且帶 8 碼以上的十六進位字(例 `666-receipt-738745fdb-full.out`);是哪個頭的收據仍看檔裡的 HEAD 行。**同一個頭的多份收據合起來算**(拆成 -ws / -knives / -docsonly 的算一份):同一個步驟取最新那份的結果(重跑綠了會蓋掉舊的紅),舊檔只補它獨有的步驟與 HEAD-END;`receipt.file` 寫最新那份的檔名加「(+n)」。PR 只掛在「頭 = PR 頭」的那一列;同名但頭不同的本機分支不算那張 PR。

- 讀 GitHub 的 PR 清單一頁 5 筆(開著的、最近關掉的都是),每次讀失敗會再讀一次才算錯(519d0 11-15:有些網路上一頁 100 筆的回應會被切斷、30 秒逾時,整輪變成 complete=false)。開著的 PR 超過 300 張、或視窗內關掉的超過 300 張 ⇒ 回錯,不默默截斷。讀的途中有新開的 PR,上一頁最後一張會再出現在下一頁:開著的清單用 PR 號去重(5d33a L1;關掉的清單本來就按分支名只留號碼最大的一張)。讀的途中有 PR 關掉,後面那張會往前移、這一輪漏掉它:下一輪就會補上,不另讀第二次(省額度)。

- 開著的 PR 跟 main 衝突(`conflicts_with_main.conflict`)⇒ class 3 fix,why 寫 `conflicts with main: <檔>`,同檔也跟別張開著的 PR 撞時加 `; open PRs on the same files: #N`(f1fae 對照 r2 第 1 項:等 CI 沒有意義,接了 main 才會重跑)。沒開 PR 的分支不適用。

## 整疊頭的審查認定(IFACE-s79 r10 第 3 點,519d0 11-15)

幾片各自審過、再疊成(或 merge 成)一個頭,那個頭上不會有審查稿。規則:

1. 只在「頭底下最近的一份審查是通過、但不在頭上」時判;頭上有審查照舊只看那一份。最近那份不通過 ⇒ 不判(最新的結論算數)。
2. 每個 commit 取最新一份審查,通過(H/M 都 0)才算一份。它涵蓋:
   - 範圍寫 `<a>..<審查頭>` ⇒ a 之後到審查頭的 commit(a 讀不到 ⇒ 只算審查頭那一個)。
   - 審查頭是 merge commit ⇒ 只算那個 merge(審的是手解),不涵蓋底下的 commit;除非有寫上面那種範圍。
   - 寫「增量」或檔名含 `-inc-`、沒寫從哪開始 ⇒ 審查頭往下,扣掉它底下其他通過審查頭已經到得了的 commit;底下沒有別的通過審查 ⇒ 只算審查頭那一個。
   - 其他(「全文」等)⇒ 審查頭往下不在 main 的全部 commit。
3. main..頭 的每個非 merge commit 都被涵蓋,而且每個 merge 不是單純自動合(carried,RULES §20)就是那個 merge commit 上有通過的審查 ⇒ `review_parts.ok = true`、`reviewed = true`。
4. 判不出來的一律往少算。這是分類,不是防線:同帳號的人寫得出審查稿;收據仍然要在頭上。

## 欄位表
- `schema` — 固定 `aib-slice-sweep.v1`
- `generated_at` — unix 秒
- `main` — origin/main 40 碼
- `fast` — 這一輪是 `--fast` 跑的:只讀「有開著 PR 的分支」與「--hours(預設 72)小時內有 commit 的分支」,已關 PR 只認 GitHub 在這段時間內動過的(一次列出,不逐支問)。true = 不是全貌:更舊的分支不在 branches 裡,更早關掉的 PR 看不到(那支分支會顯示成沒有 PR)。完整掃不帶 `--fast`
- `complete` — false = 有東西讀不到,看 errors
- `errors` — 讀不到的原因,一則一行(`no_fetch`、`no_gh` …)
- `open_prs` — 開著的 PR
- `open_prs[].number` — PR 號
- `open_prs[].head_ref` — PR 的分支名
- `open_prs[].head` — PR 頭 40 碼
- `branches` — 每支分支一筆(本機 + origin 的 integ-src/ feat/ fix/ integ/,同名算一支)
- `branches[].branch` — 分支名
- `branches[].seat` — 分支尾碼的 5 碼席碼;認不出 = null
- `branches[].head` — 頭 40 碼(本機優先);這個名字有開著的 PR 而本機分支在別的 commit 時 = **PR 的頭**(整列照 PR 頭判:審查、收據、checks)
- `branches[].local_head` — 上面那種情況下本機分支所在的 commit(本機領先 = 還沒推;本機落後 = PR 從別處推了新頭);平常 null。5d33a M1:原本這種 PR 會從清單消失
- `branches[].local` — 本機有這支
- `branches[].remote` — 同名的 origin 分支;沒有 = null
- `branches[].worktree` — 掛在哪棵工作樹;沒有 = null
- `branches[].dirty` — 工作樹有沒有未提交;沒工作樹或沒獨有 commit = null
- `branches[].last_commit_at` — 頭的 committer 時間(unix 秒)
- `branches[].age_h` — 離現在幾小時
- `branches[].unique` — origin/main..頭 的非合併 commit,扣掉 patch 已在 main 的
- `branches[].unique_not_in_prs` — 再扣掉 patch 已在任一開著 PR 頭的
- `branches[].in_prs` — 收了這支部分 patch 的開著的 PR
- `branches[].pushed_to` — 含這個頭的 origin 分支
- `branches[].pr` — 開著的 PR(head_ref 是這支或它推到的名字),否則最新一張關掉的;沒有 = null
- `branches[].pr.number` — PR 號
- `branches[].pr.state` — open / closed
- `branches[].pr.merged` — 關掉時是不是合了
- `branches[].pr.base` — PR 的 base 分支
- `branches[].pr.author` — PR 作者 gh login
- `branches[].pr.mergeable` — MERGEABLE / CONFLICTING / UNKNOWN;沒讀 = null
- `branches[].pr.merge_state` — GitHub mergeable_state 原字;沒讀 = null
- `branches[].pr.checks` — 頭的 check runs 彙總 SUCCESS / FAILURE / PENDING / INFRA;沒讀或沒有 = null。INFRA = 紅的每一格都是「job 死在 Set up job / Set up runner、後面沒跑」(IFACE-s79 r11 第 4 點):不是程式紅,不算 fix;審過收據綠的那張歸 waiting_ci,due 加一條 `ci_infra`(合併隊重跑)。紅格裡只要有一格是真的跑了才失敗、或 job 讀不到,就是 FAILURE
- `branches[].pr.red_checks` — 紅的 check run 名;null = 沒讀(不代表沒紅)
- `branches[].pr.infra_checks` — 紅格裡屬於基礎設施失敗的那幾格;null = 沒讀
- `branches[].review` — 這支 commit 上最近的一份審查;沒有 = null
- `branches[].review.head` — 審查第一行的頭
- `branches[].review.verdict` — 通過 / 不通過 / 要改 …
- `branches[].review.h` — H 數
- `branches[].review.m` — M 數
- `branches[].review.l` — L 數
- `branches[].review.n` — N 數
- `branches[].review.file` — 審查檔名
- `branches[].review.at_head` — 審查頭 == 分支頭
- `branches[].review.unreviewed_after` — 審查頭到分支頭之間、**不在 origin/main** 的非合併 commit(接 main 帶進來的不算這支分支要審的)
- `branches[].review.scope` — 這份審查涵蓋什麼,從第一行「範圍:」讀:`whole`(到審查頭為止整支)、`from:<a>`(範圍寫了 `<a>..<審查頭>`,7–40 碼)、`increment`(寫了「增量」或檔名含 `-inc-`,但沒寫從哪開始)
- `branches[].review.resolved_after_review` — S78 r-2a:審查頭之後才發生的手解 merge(項目跟 `resolved_merges` 同形狀)。`[]` = 審查之後的 merge 全是單純自動合(carried),審查就在頭上時也是 `[]`;非空 = 這些檔要派非作者審;null = 沒讀到。給合併隊判「審查不在頭上還算不算數」用(f1fae r-2b)
- `branches[].review.resolved_after_review[].commit` — 那個 merge commit
- `branches[].review.resolved_after_review[].parent` — 第 2 個父
- `branches[].review.resolved_after_review[].from_main` — 同 `resolved_merges[].from_main`
- `branches[].review.resolved_after_review[].files` — 跟自動合不一樣的檔
- `branches[].receipt` — 這支 commit 上最近的一份收據;沒有 = null
- `branches[].receipt.head` — 收據寫的頭
- `branches[].receipt.file` — 收據檔名
- `branches[].receipt.nonzero` — 不是 0 的 rc 鍵
- `branches[].receipt.green` — nonzero 空、封印沒壞、有 rc 行、complete
- `branches[].receipt.at_head` — 收據頭 == 分支頭
- `branches[].receipt.steps` — 收據跑過的 rc 鍵
- `branches[].receipt.sealed` — RECEIPT-SHA256 對得上 = true、對不上 = false、沒這行 = null
- `branches[].receipt.complete` — 有 `HEAD-END <該頭>` 行(跑到底);false 或沒有任何 rc 行 ⇒ green=false、類 3「receipt incomplete」
- `branches[].receipt.stacked_at` — 疊合收據(f1fae 對照 r2 第 2 項):開著的 PR 頭上沒有自己的收據,但有一份綠的收據跑在一個「疊合 commit」上,而且那個 commit 在 main 之外只有「開著的 PR 的頭」與「單純自動合的 merge」⇒ 這份收據算同頭收據(`at_head` = true),這裡寫疊合 commit;平常 null。疊合裡有不屬於任何開著 PR 的 commit、或手解過的 merge ⇒ 不算
- `branches[].stacked_on` — 頭落在我獨有 commit 裡的別支分支
- `branches[].stack_top` — 沒有別支疊在我上面
- `branches[].conflicts_with` — 跟哪些開著的 PR 衝突(只算有開著 PR、或頭上有審查 / 收據的候選);只是資訊,不改分類
- `branches[].conflicts_with[].pr` — PR 號
- `branches[].conflicts_with[].files` — 衝突檔
- `branches[].conflicts_with_branches` — 跟哪些類 1 / 2 分支互相衝突
- `branches[].conflicts_with_branches[].branch` — 分支名
- `branches[].conflicts_with_branches[].files` — 衝突檔
- `branches[].conflicts_with_main` — 對 origin/main 試合;沒試 = null
- `branches[].conflicts_with_main.conflict` — 有沒有衝突
- `branches[].conflicts_with_main.files` — 衝突檔
- `branches[].claimed_by` — build-claims.tsv 的建置中登記;沒有或過期 = null
- `branches[].claimed_by.seat` — 登記的席
- `branches[].claimed_by.at` — 登記時間(unix 秒)
- `branches[].new_gate` — gate_files 非空:這支改了檢查,同批排最後(S73j)
- `branches[].not_gate` — S73j 降成非閘(519d0 11-14)
- `branches[].not_gate.label` — PR 有 `not-gate` 標籤
- `branches[].not_gate.reason` — 頭上審查(審查席 ≠ 分支席,由檔名 `REVIEW-…-<席>-<頭8>.md` 認)的 `非閘理由:` 那行;沒有 = null
- `branches[].not_gate.effective` — new_gate 且(label 且 reason 非空,或 by_content)⇒ 排序當非閘
- `branches[].not_gate.by_content` — S73k 看內容判非閘(519d0 / Andy 11-14):閘檔只有一支 workflow,而且只多了一個 step、只跑這支分支自己改的 crate(src/gatecontent.rs 六條全成立);拿不準一律照字面當新閘
- `branches[].not_gate.content` — 判非閘時寫加了什麼 step;不成立時寫哪一條不成立(`rule N: …`);沒判(沒有閘檔,或 git 讀不到)= null
- `branches[].gate_files` — merge base..頭 動到的 `.github/workflows/`、`.github/rulesets/`、`xtask/src/`、`aib/tools/release/docs_only.py`、檔名含 ruleset 的檔
- `branches[].class` — 1–8
- `branches[].class_name` — merge_first / push_or_pr / fix / in_progress / merged / stale / superseded_by_pr / waiting_ci。merge_first(1)要審過 + 收據綠 + 已推 + PR 開著 + **CI 全綠**;CI 還在跑或沒讀到 ⇒ waiting_ci(8),不是可合(IFACE-s79 r11 第 2 點)。跟別張開著的 PR 互撞**不改分類**(r11 第 3 點):清單仍在 `conflicts_with`(= r11 說的 conflicts_with_open),due 在應合未合那一筆加一條「跟 #n 互撞…前面那張合了再接 main」(`conflict_open`)。merged(5)只有「自己的 commit 全在 origin/main」;自己的 commit 全在某張**開著的** PR 裡、自己又沒有開著的 PR ⇒ superseded_by_pr(7,why 寫 PR 號):那張 PR 帶著它,不是已合、也不是要處理(IFACE-s79 r11 第 1 點:#764 疊在 #767 底下曾被判成 merged)。自己有開著的 PR 的分支照那張 PR 判,不走這條
- `branches[].why` — 為什麼是這一類
- `branches[].abandonable` — 可放棄(s33,Andy 11-14):PR 關掉沒合、關的時候就是現在這個頭、還有自己的 commit 不在 main 也不在開著的 PR;類 6,不算「沒驗」也不算「要處理」,留或丟仍由人定。關掉後又有新 commit 的不算
- `branches[].py_files` — merge base..頭 動到的 `.py` 檔(s35,只能 Rust 之後一眼看到還帶 Python 的未合分支)
- `branches[].steps_lost` — RULES §25(S79 C1a):這支分支合進 main(git 自動合)之後,main 原本有、合完卻不見的 workflow step(`- name:` 行),寫成 `檔: step 名`;同名用兩次就要有兩次。`[]` = 沒少;null = 沒檢查(沒動 workflow 檔、跟 main 衝突、或讀不到)。非空 ⇒ due 把它列進「應修未修」(`steps_lost`:合進 main 會少 n 個 workflow step),不列「應合未合」。動了 workflow 檔、跟 main 不衝突、這欄卻是 null(沒比到)⇒ due 也列進「應修未修」(`steps_unchecked`),沒檢查不當成沒少
- `branches[].reviewed` — 算不算審過:頭上有通過的審查,或 `review_parts.ok`。分類(class 1 / 8)與 due 的「有審查」都讀這個
- `branches[].review_parts` — IFACE-s79 r10 第 3 點(整疊併成一個頭):最近一份審查通過、但不在頭上時,把頭底下每個 commit 最新那份**通過**的審查各自涵蓋的範圍加起來。null = 沒判(審查就在頭上、沒有審查、最近那份沒通過、或沒讀到)。規則見下面「整疊頭的審查認定」
- `branches[].review_parts.ok` — files 非空、uncovered 與 unreviewed_merges 都空 ⇒ 視為已審
- `branches[].review_parts.files` — 有涵蓋到東西的通過審查稿
- `branches[].review_parts.uncovered` — main..頭 裡不是 merge、也沒有任何通過審查涵蓋的 commit(要補審的就是這些)
- `branches[].review_parts.unreviewed_merges` — 手解過的 merge(`resolved_merges`)而那個 merge commit 上沒有通過的審查
- `branches[].resolved_merges` — S78 r-2a:main..頭 之間、樹跟「兩個父自動合出來的樹」不一樣的 merge commit(手解過衝突,或在 merge 裡另外改了東西);`[]` = 每個 merge 都是單純自動合(RULES §20 的 carried,不用重審);null = 沒讀到(見 errors)。只顯示,不改分類。需要 git 2.38 以上(merge-tree --write-tree);舊 git 會進 errors,不會誤判
- `branches[].resolved_merges[].commit` — 那個 merge commit
- `branches[].resolved_merges[].parent` — 第 2 個父(被合進來的那一邊);三個父以上的 merge 寫第 2 個
- `branches[].resolved_merges[].from_main` — 第 2 個父**現在**已在 origin/main。不代表當時接的是 main:接了別人的分支、那支後來合進 main 也是 true,所以不能拿它當「這是接 main」來免審
- `branches[].resolved_merges[].files` — 跟自動合不一樣的檔 = 要派非作者審的檔;三個父以上的 merge 重現不了,列它相對第 1 個父動到的全部檔

## 不做(第二輪 / 別片)
報表排版、座艙一行(S73c)、`--gate` 接 mergewatch(S73b)。

## due(S73l,回覆尾巴的「應合未合 / 應修未修」)
`aib-slice due (--seat <5 碼> | --all) [--max N] [--json] [--repo DIR] [--pending DIR] [--no-fetch] [--fast] [--hours N] [--gh PROGRAM | --no-gh]`
- 同一次全掃的結果,不另掃;不寫任何檔。`--seat` 與 `--all` 必須恰好給一個;`--max` 預設 8(1–50),兩段各自算。
- 結束碼:0 掃完整;2 掃描不完整(兩段照印,第一行是「掃描不完整,以下可能有缺:…」,不當成 0 件);64 用法錯。
- 應合未合 = 類 1 + 類 2,每條寫還差什麼:沒推、沒開 PR、沒人審、審查頭不在現在的頭、沒有收據、收據不在現在的頭、沒貼 ready-merge、CI n 格排隊 / 在跑多久(時間從該格建立起算)、CI n 格被取消(等重跑)、等 #n 先合、新閘排同批最後。同一格跑過好幾次只看最新那次;被取消的格不算紅。
- 應修未修 = 類 3 + 類 1 / 2 但跟 main 衝突 + 開著超過 72 小時沒新 commit 的 PR:CI n 格紅、跟 #n 衝突、跟 main 衝突、審查中度 / 嚴重未修、收據不綠、收據封口不符、開著 n 天沒動。
- 可放棄(s33)、已合、單純進行中的不列。同一個頭有兩個名字(分支與推上去的副本)只列一條,取有開著 PR 的那個。兩段都是最舊的在前。
- `--json`:`aib-slice-due.v1`,`{schema, generated_at, complete, errors, seat, merge:{total, items[]}, fix:{total, items[]}}`;item 有 `target pr branch seat head has since`,加 `lacks[]`(merge)或 `problems[]`(fix),每項 `kind text n seconds names`。給 S79 `aib-drive next` 第 1、2 層直接讀。
- 完整約定:~/.ez2-aib/pending/IFACE-s73l-aib-slice-due-1ced7-1114.md r1。

## gc(S82,合完要刪)
`aib-slice gc --seat <5 碼> (--dry-run | --apply) [--root DIR] [--caches] [--json] [--machine NAME]`
- 一次只看一台機器的一個資料夾(預設 ~/wt.noindex),不讀 GitHub;每台測試機各跑一次。`--dry-run` 與 `--apply` 必須恰好給一個(刪除不是預設)。
- 只列、只動名字以這個席碼開頭或結尾的資料夾(`<席>-…`、`…-<席>`、`…-<席>-<4 位日期>`);席碼在中間的(別人的東西、這席只是碰過)、別席的與連結不列。
- 整棵刪要全部成立:是 git 工作樹;`git status` 乾淨(**沒被追蹤的新檔也算有改動**,只有沒被忽略的編譯快取資料夾不算);整棵樹(不含 .git 與編譯快取)2 小時以上沒動;頭已在 main(`git cherry origin/main HEAD` 沒有 `+` 行)**而且這棵樹有過自己的 commit**(從 main 開出來、什麼都還沒 commit 的樹不算已合,留著)。本身就是帶 CACHEDIR.TAG 的編譯快取資料夾也刪。連結工作樹用 `git worktree remove`,倉庫不留紀錄。
- 整套 clone(`.git` 是資料夾):任何本機分支有沒推的 commit、或有 stash ⇒ 標 `backup` 不刪。有別的工作樹掛在這個 clone 上(`git worktree list` 不只它自己)⇒ 留著:那些樹的暫存區、分支與 detached commit 都存在這個 clone 的 `.git` 裡。
- 被 .gitignore 忽略的檔不算工作,會跟著樹一起刪。
- `--caches`:頭還沒進 main 但已在某個遠端分支的樹,只清帶 CACHEDIR.TAG 的 `target` / `target-win` / `target-win.noindex`。
- 一律留:這席有收據在跑(`<席>-*.out` 沒有 `.done`)時全部留;讀不到(git 失敗、沒有 origin/main)留並算 rc 2;有只在這裡的 commit ⇒ 標 `backup`(先 bundle,交 S81 封存),永不刪。PR 關掉沒合的分支這裡不判(要讀 GitHub),留著由 sweep 的「可放棄」給人決定。
- 每筆刪除先在 `<root>/gc-<席>.log` 寫一行(時間、機器、路徑、位元組、做了什麼、為什麼、席),寫不進去就不刪。
- 結束碼:0 做完;2 有刪除失敗或有樹讀不到;64 用法錯。`--json`:`aib-slice-gc.v1`,`{schema, seat, dry_run, rows[{name, path, head, action: remove|caches|keep|backup, why, caches[], bytes, failed}]}`。
