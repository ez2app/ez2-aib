# aib-slice sweep — 介面(S73a,1ced7)

`aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--fast] [--gate] [--hours N] [--gh PROGRAM | --no-gh]`

只讀:fetch、讀 git、用 `gh api` GET 讀開著的 PR、讀 pending 夾的審查 / 收據 / ack / 建置中登記。不推、不開 PR、不刪樹。
輸出一個 JSON 物件 `aib-slice-sweep.v1`,正本是 `schema/sweep.v1.json`;下面的欄位表跟它逐欄對照,`tests/iface.rs` 比對兩邊欄位集合,不一致就紅。

## 結束碼
- 0 掃完;2 掃不完整(fetch / gh / 某支分支讀不到),JSON 照印、`complete=false`,`--gate` 也是 2 —— mergewatch 把 2 當不合;3 只在 `--gate`:有類 1 / 2 / 3;64 用法錯。

## 六類(由上往下,先中先算)
5 merged(獨有 patch 全在 main 或開著的 PR)→ 6 ack(sweep-ack.txt)→ 6 可放棄(PR 關掉沒合、關在現在這個頭;s33)→ 3 fix(開著的 PR 衝突 / PR checks FAILURE / 頭上審查 H/M > 0 / 頭上收據封印不符 / 頭上收據紅)→ 1 merge_first(頭上審過 + 收據綠 + 已推 + 不衝突 + PR 開著)→ 2 push_or_pr(頭上有審查或收據,但沒推或沒開 PR)→ 6 stale(超過 --hours 沒 commit 且沒開著的 PR)→ 4 in_progress。`--gate` 擋 1 / 2 / 3。

## pending 夾讀的檔
- `REVIEW-*.md` 第一行:`結論: X 頭: <40> H/M/L/N: a/b/c/d …`。
- `TEST-*.txt`、`RECEIPT-*.txt|.log`:前 5 行的 40 碼,或 `== HEAD=` / `HEAD ` / `HEAD-END ` 行;`xxx_rc=N`、`STEP xxx rc=N`、`RC xxx=N`(`KNIFE` 行、`RC knife_*`、`head_rc` / `base_rc` 不算;後面帶 `(want N)` 的以 N 判、`(want non-0)` 是 0 才紅、`(report only)` 不算紅);`HEAD-END <頭>` 完成記號;最後一行 `RECEIPT-SHA256 <64>`。
- `REVIEW-*.md` 內 `非閘理由: …` 一行(S73j)。
- pending 夾或檔讀不到 ⇒ errors(`pending …`)。
- `sweep-ack.txt`:`分支<TAB>原因`。
- `build-claims.tsv`:`分支<TAB>席<TAB>開始時間<TAB>說明`;時間沒寫時區 = 台北;超過 6 小時不算。

這些都是同帳號可改的檔:讀來分類,不是防線。

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
- `branches[].head` — 頭 40 碼(本機優先)
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
- `branches[].pr.checks` — 頭的 check runs 彙總 SUCCESS / FAILURE / PENDING;沒讀或沒有 = null
- `branches[].pr.red_checks` — 紅的 check run 名;null = 沒讀(不代表沒紅)
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
- `branches[].stacked_on` — 頭落在我獨有 commit 裡的別支分支
- `branches[].stack_top` — 沒有別支疊在我上面
- `branches[].conflicts_with` — 跟哪些開著的 PR 衝突(只算類 1 / 2 / 3 候選)
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
- `branches[].class` — 1–6
- `branches[].class_name` — merge_first / push_or_pr / fix / in_progress / merged / stale
- `branches[].why` — 為什麼是這一類
- `branches[].abandonable` — 可放棄(s33,Andy 11-14):PR 關掉沒合、關的時候就是現在這個頭、還有自己的 commit 不在 main 也不在開著的 PR;類 6,不算「沒驗」也不算「要處理」,留或丟仍由人定。關掉後又有新 commit 的不算
- `branches[].py_files` — merge base..頭 動到的 `.py` 檔(s35,只能 Rust 之後一眼看到還帶 Python 的未合分支)
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
