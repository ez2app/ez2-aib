---
name: aib-cabin-regroup
description: >-
  Pull every AI seat from other Ez2 AIB cabins into the cabin you are sitting
  in, keeping each seat's code, conversation, persona and second brain. Use
  when a new cabin was opened and the previous cabin still holds seats, when
  the maintainer says 把舊艙的 AI 拉回來／全組回來／搬到新艙／舊艙的人都接過來／
  regroup cabin, or after a machine restart left seats scattered across
  several hubs. An AI whose conversation can still be resumed comes back on
  its own seat code; one that cannot is opened as a new seat with its old
  persona card and brain route transplanted. Covers the tmux limit that makes
  a live window unmovable, the race that makes a revive be refused, codex's
  missing seat-code registration, and the two bot bindings that do not travel.
  Starts by checking whether the old cabin is really dead: a cabin whose
  cockpit session still exists is re-attached with tmux attach -d, never
  moved. Same folder works for Claude, Codex, Grok, and Antigravity.
---

# aib 艙際歸隊 —— 把舊艙的 AI 全部拉回你坐的這一艙

the maintainer 2026-10-09 01:3x 直令:「把剛才被誤舊的 aib 艙裡的 AI 全拉回你這個新艙」,
並要求「整理一個 skill,在新艙開啟時可以執行把舊艙裡的 AI 全組回來;
如果已死掉的 AI,就套上他的人格與大腦開新席」。

本檔是那一次**實跑完成**之後寫的(9 席全進,逐席有收據),不是設想。

## 第一段 先驗「艙是不是真的死」—— 能接回來就接回來,不要搬(the maintainer 2026-10-09 18:5x 直令)

```bash
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh check     # 只驗,不動;plan / apply 也一定先跑這段
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh attach    # 能拉回來就直接拉回來:替「沒有人接著」的活艙開新 Terminal 視窗跑 tmux attach -d
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh attach --only 4f8d   # 畫面卡住的艙(有客戶端但卡死)要點名才接,-d 會踢掉舊視窗
```

`attach`(the maintainer 2026-10-09 19:1x 直令「能拉回來就用這個拉回來」):沒點名時只接沒有人接著的艙,
已經有人在看的艙不碰;接完會回讀 `list-clients` 確認真的接上才印 ✅。`apply` 也會先做這一步;`plan` 只印會開哪些視窗。
10-09 19:1x 實跑:艙 2f45 沒人接著 → 開新視窗 /dev/ttys007 接上;4f8d 有人在看,沒碰。

2026-10-09 18:47 實例:艙 4f8d 的畫面整頁重複印狀態列「`[Ez2 AIB v0:cockpit* 1:war-room`」、右欄時鐘停在 18:48,
看起來像艙關掉了。實查:外層座艙 session `aib-20261009-011921-4f8d` 還在、hub 13 窗全活、
a seat/a seat/a seat/a seat 行程全活、對話檔都在;**壞的只是 Terminal.app 那個視窗的顯示**
(當時正在用 Squirrel 打中文)。the maintainer 另開了新艙 2f45,差一點就走到本 skill 的殺窗+resume。

**為什麼會誤判:** 從別艙的席上跑 `tmux ls` 只看得到自己 hub 的 socket,舊艙看起來「不在」。要分三層驗:

| 層 | 怎麼驗 | 在的話怎麼辦 |
|---|---|---|
| ① 外層座艙 | `tmux -L default has-session -t aib-<艙id>` | 艙活著,只是畫面卡 ⇒ `regroup.sh attach`(或自己開一個**新的** Terminal 視窗 `tmux attach -d -t aib-<艙id>`)(`-d` 踢掉卡住的舊客戶端;`refresh-client` 實測沒用)。**不搬。** |
| ② hub | `tmux -L aihub-<艙id> list-windows` | 座艙死了、AI 還在 ⇒ 在該艙開任一席寫完整艙號(例 `aib claude dgrs root <艙id>`)會跑 `cockpit-full.sh --ensure` 重組座艙。**不搬。** |
| ③ 行程 | `kill -0 <席位檔 pid>`;`seat_liveness.py holders <uuid>` | 只有 ①② 都不在才走下面的同碼復活 |

`regroup.sh` 的 `check` 就是逐艙做 ①②,印出該打的指令;`plan`/`apply` 會把 ① 還在的艙從名單拿掉
(要硬搬加 `--even-alive`)。接回來之後,新開的空艙留不留由 the maintainer 裁。

### 第一段的另一半:hub 真的死掉的艙(💀),10-11 起腳本自己會列

①② 都不在的艙(AI 行程全沒了)以前 `plan/apply` 一席都列不出來——watch 每 300 秒掃一次會把那些席位檔補上
`exited_at_epoch`(原因 `pid-gone`),舊規則「有退出痕就當收工」就把它們當墓碑丟掉(10-10 艙 4f8d 實踩,
只好一席一席手動復活)。現在 `check/plan/apply` 都會先印一段 💀:

```
── 最近 24 小時內死掉的艙(hub 不在,AI 行程沒了)──
💀 艙 20261009-011921-4f8d:2 席,最後見於 10-10 12:51,退出原因 pid-gone,席碼 2a900,ae3cf
   ⇒ 接不回來,要同碼復活:regroup.sh plan 看名單,regroup.sh apply 真搬
```

算「死艙裡要救的席」要同時符合三條,少一條都不列:
1. 那艙的 hub socket 打不到(`tmux -L aihub-<艙id> list-windows` 失敗);
2. 席位檔的退出原因是被掃到死(`pid-gone` / `window-gone` / `unknown`)或根本沒留痕;自己關的
   (`cli-exit` / `captain-close` / `window-killed` / `usage-gate`)不算;
3. 死的時間在 `--dead-within 小時`(預設 24)之內,而且同一個 uuid 沒有在任何活艙(含本艙)坐著——
   已經同碼復活過的席,舊艙那份只是殘影。

10-04~10-07 的墓碑超過時窗自然落在外面,所以不用 `--all-cabins`。要看更久以前的:`--dead-within 72`。
plan 表上這種席「走哪條」會標 `(艙已死)`;apply 對它們一樣走 §2 四步,只是 ① 關舊窗會因為 hub 不在而跳過。
10-11 01:5x 實跑(931e 席上):check 列出 4f8d(2 席)、873f(1 席)、1f01(1 席);plan 列 4 席,
4f8d 原本 5 席裡已在 931e 復活的 a seat/a seat/a seat 正確沒列。apply 沒跑(那 4 席要不要救由 the maintainer 裁)。

### 停電/重開機(所有艙都死了)改用 `aib restart`(10-09 起)

本 skill 的 `plan/apply` 只認 hub 還活著的艙。停電/重開機後 tmux 全滅,改用
`aib restart`(列出上次開機時的艙)→ `aib restart --restore-all`(原艙號開回、每席同碼復活,一艙對一艙)。
它靠 watch 每輪寫的快照知道停電前有哪些艙、每艙有誰;詳見 `src/cockpit/README.md` 的 aib restart 一節。
(要等那一包進主線才有這個指令。)

## 0 先懂一個結構限制,不然你會去找一個不存在的指令

**一個活著的 AI 不能被搬。** 每一艙是一台獨立的 tmux 伺服器,tmux 沒有辦法把視窗
從一台搬到另一台。AI 的對話活在那個視窗底下的行程裡。

所以「拉回」真正的做法只有一條:

> 在舊艙**關掉**那個視窗 → 在新艙用**同一個 session uuid** 重新開席,
> CLI 帶 `--resume`,把同一段對話接回來。

對話紀錄落在**家目錄**不在艙裡(claude 在 `~/.claude/projects/<cwd 轉碼>/<uuid>.jsonl`、
grok 在 `~/.grok/sessions/<cwd 轉碼>/<uuid>`、codex 在 `~/.codex/sessions/<年>/<月>/<日>/rollout-*-<uuid>.jsonl`),
所以關掉視窗**不會**弄丟對話。這就是整件事能成立的理由。

同理,**人格與第二大腦也不在艙裡**:綁定存在狀態根
(`~/.ez2-aib/identity/bindings.json`、`identity/seat-display.json`),鍵是**席碼**。
⇒ 只要席碼不變,人格與大腦自動跟著回來,一個字都不用重綁。
這也是為什麼要走「同碼復活」而不是「分出新席」——分出新席會給新碼,
那全艦 dm 位址、席位記憶、文件裡寫的 `a seat` 全部指不到人。

## 1 一句話怎麼跑

```bash
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh check    # 第一段:艙是不是真的死(活的印接回指令)
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh attach   # 活艙直接接回來(開新 Terminal 視窗)
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh plan     # 只看,不動
bash ~/.agents/skills/aib-cabin-regroup/regroup.sh apply    # 真搬
```

`plan` 會列出別艙每一席、它還活不活、會走哪一條路(同碼復活 / 新席移植人格)。
**先跑 plan 給 the maintainer 看過再 apply**(基因 1:計畫先報再動)。

一席一席搬:`apply --only 3d156,05f69`。

## 2 一席的四步(腳本做的就是這個;手動時照抄)

```bash
OLD=aihub-<舊艙 id>;  NEW=aihub-<新艙 id>
# ① 關掉舊窗(等同面板的 [X];鐵則四:[X] 殺的是窗)
tmux -L $OLD kill-window -t @18
# ② 讓舊席位檔留下退出痕(不留痕 ⇒ 登記簿上兩艙同指一席)
python3 src/cockpit/seat_exit.py sweep --instance-home ~/.ez2-aib/instances/<舊艙 id> --hub $OLD
# ③ 等舊行程**真的死透**,持有者必須為空(見 §3 第一坑)
python3 src/cockpit/seat_liveness.py holders <舊 uuid>     # 要等到 describe 為空
# ④ 在新艙同碼復活(10-10 起不用帶 --model:aib 會讀對話紀錄最後用的模型,退舊席位檔,再退預設,並印出用了哪一層)
AIB_REVIVE_SESSION_UUID=<舊 uuid> EZ2_AIHUB_SOCKET=$NEW \
  aib <provider> <mode> <role> [--again]
```

`--again` 的判準:新艙已經有**同 role + 同 provider** 的窗就要帶(窗名會自動加號:
`root-claude` → `root2-claude`)。不帶會撞名。provider 不同不需要帶
(`root-claude` 與 `root-codex` 可以共存)。

`<mode>`、`<role>`、`<model>` 一律抄舊席位檔的 `mode` / `role` / `model_requested`,
不要自己決定。

## 3 五個坑(每一個都是 2026-10-09 實踩,不是設想)

**一、關了窗不等於行程死了 —— grok 踩到。**
`kill-window` 之後 2 秒就去復活,守衛會判「它還活著」而拒絕。
⇒ 等到 `seat_liveness.py holders <uuid>` 的 `describe` 為空才動;腳本最多等 30 秒。

**二、那個拒絕訊息以前是壞的(已修於主線 a55b890ba,10-09)。**
`src/cockpit/aib` 第 1554 行的 die 訊息裡有 `$AIB_REVIVE_SESSION_UUID——`,
緊接著一個全形破折號。macOS 內建 bash 3.2 會把破折號的第一個位元組吃進變數名,
於是 `set -u` 噴 `AIB_REVIVE_SESSION_UUID<亂碼>: unbound variable`。
**擋是擋住了(fail-closed,沒有開錯席),但訊息完全沒說出真正的理由。**
看到那行亂碼請讀成「它還活著,再等一下」。
同一個毛病全樹還有第二處:`src/cockpit/dm-ctl.sh:1308`(`改投 @$line。`,檔頭開 `set -u`)——
私訊要轉投給接班席時會在那一行死掉。全倉共 9 處 / 8 檔,a seat 修(加大括號),
10-09 04:57 隨 aa42fa284 進主線(提交 a55b890ba)。若你跑的是更舊的版本,看到亂碼仍讀成「它還活著,再等一下」。
★ 驗「修好沒」要用固定字串比對 `grep -cF '改投 @${line}。'`;不加 `-F` 時 grep 會把 `$` 當行尾,回 0 —— 看起來像沒修,其實是查法錯(a seat、a seat 10-09 都踩過)。
最小重現:`LANG=zh_TW.UTF-8 /bin/bash -c 'set -u; X=abc; echo "$X——"'` ⇒ rc=127。

**三、codex 的席碼要等一下才會出現。**
`aib` 裡 `AIB_SESSION_ID_PROVIDERS` 沒有 codex ⇒ 剛開起來時席位檔 `session_uuid` 是空的。
這**不是**壞掉:watch 窗每 300 秒會自動回填(`idle_reaper` 的 `uuid_backfill_tick`)。
等不及就自己補一道:

```bash
EZ2_AIHUB_SOCKET=$NEW EZ2_AIB_INSTANCE=<新艙 id> \
EZ2_AIB_INSTANCE_HOME=~/.ez2-aib/instances/<新艙 id> \
  python3 src/cockpit/seat-detect.py --window @<新窗> --apply
```

**四、codex 可能當場自我更新然後死掉。**
pane 會印 `Please restart Codex` 接著 `Pane is dead (status 0)`。
不是搬壞了,是它更新完要重啟:`tmux -L $NEW kill-window -t @<死窗>` 再復活一次即可。

**五、機器人席的綁定值:復活前先讀,帶回去。**
綁定值住在 `~/.ez2-aib/bridge/<席碼>/status.json` 的 `agent_id`(grokbot 每席都有)。
哪個環境變數帶它,看正本 `bot_caps.caps(<provider>).bind_env`
(grokbot=`AIB_GROKBOT_AGENT_ID`、musebot=`AIB_MUSEBOT_CONVERSATION_ID`、claudebot=`AIB_CLAUDEBOT_SESSION_ID`)。
★ **一定要在關舊窗、復活之前讀。** 不帶值復活,橋會把這一欄重寫成空的 —— 10-09 19474 就是這樣被蓋掉,
後來只好請 the maintainer 手給編號。`regroup.sh` 已經會自動讀、自動帶,plan 表上會寫「帶回綁定 xxxxxxxx…」。
讀不到(欄位是 None)才標「⚠ 要問人」—— 這時候**一律問真人,不要猜**,猜錯的症狀是機器人接到別人的對話。
★ musebot 在 10-09 以前**帶了也綁不上**(`aib` 沒把它接進席位環境,缺陷 D7;已修於主線 ee4ae8ecb,10-09 04:57)。
  另外 1a58c9e0c(D8)起:右鍵開 musebot 沒帶編號會**自動沿用上一次綁的對話**;那段正被活席占著就不沿用、照開不綁並點名占用者。
驗法:復活後 `status.json` 的 `agent_id` 要等於你帶的值;是 None 就是沒綁上。
grokbot 帶值重開不會跳出貼金鑰的視窗(10-09 實測)。

## 4 已經死掉的 AI 怎麼辦(the maintainer 指定的那一條)

先分清楚兩種「死」:

| 情況 | 做法 | 為什麼 |
|---|---|---|
| 行程死了,**對話檔還在** | 照 §2 同碼復活 | 席碼、人格、大腦、記憶全部自動回來,最省 |
| 對話檔也不在了(或那家 CLI 不支援 resume) | 開**新席** + 移植人格與大腦 | 對話救不回來,但身分與知識可以 |

第二種的移植用現成的唯一合法寫入器(不要自己改 bindings.json):

```bash
# 1. 讀舊席綁了什麼(bindings.json 查不到要退到 seat-display.json —— 只綁人格沒綁專案的席只在後者)
python3 - <<'PY'
import json,pathlib
h=pathlib.Path.home()/'.ez2-aib'/'identity'; old='3d156'
b=(json.loads((h/'bindings.json').read_text()).get('bindings') or {}).get(old) or {}
if not b.get('persona_uid'):
    b=(json.loads((h/'seat-display.json').read_text()).get('display') or {}).get(old) or {}
print(b.get('persona_uid') or '量不到', b.get('route_key') or '')
PY
# 2. 開新席(同 provider/mode/role),拿到它的 seat_id
# 3. 綁上去
python3 src/identity/bind_one.py bind --seat <新 seat_id> --card <persona_uid> \
  [--brain <route_key>] --actor regroup@<你的席碼>/aib --actor-kind system \
  --state-root ~/.ez2-aib
```

★ 前身沒綁人格就**跳過,不要拿別本充數**(`seat_fork.py:carry_binding` 的原話)。
2026-10-09 實測:9 席裡只有 musebot a seat 有人格 + 大腦路由(`e0dba6` / `core.handoff`),
另外 3 席(含 root 本席)只有人格 `71c97a`,其餘 6 席本來就沒綁 ——
**「未綁」是它們的原狀,不是搬丟的。** 搬完看到「未綁@xxxxx」先查原狀再喊壞。

## 5 搬完一定要驗這四格

```bash
tmux -L $NEW list-windows -a -F '#{window_id} #{window_name}'      # 人到了
python3 src/cockpit/seat_liveness.py dups                          # 必須回 []
# 每一席:新席位檔的 session_uuid 尾五碼 == 舊席碼
tmux -L $NEW capture-pane -p -t @<窗> | tail -3                    # CTX% 不是 0 ⇒ 對話真的接回來了
```

`dups` 不是 `[]` 就是出現同碼雙活(2026-09-24 那次事故的形狀:同一段對話被兩個活行程共用、
`aib dm <碼>` 回「座位碼不唯一」)。**當場停手報人,不要繼續搬。**

CTX% 是最便宜的真憑據:空白新開是 0%,接回來的會是它原本的用量
(實測 a seat 回來顯示 39% / 390k)。

grok 的畫面**不印 CTX%**,而且復活後會停在它自己的條款/啟動頁,看起來像空白新開。
別被騙:看它狀態列右下的 `xxxK / 500K`(實測 021c2 回來是 266K),或用 aib dm 問它
「搬家前你手上最後在做什麼」——答得出具體的事就是接回來了(實測答出「改安裝檔的公開倉庫網址」)。
codex 看輸入框右邊的百分比(實測 6cb6a 回來 62%)。

## 6 不要自己搬的兩種席

- **桌面機器人自己的席**(`cwd` 在 `.../AIB Desktop Bot/desktop-seat/workspaces/…`):
  那是 bot app 管的,搬了會讓 app 找不到它。腳本預設跳過,要搬得明示 `--include-desktop-bot`。
- **`watch` 窗**:那是每一艙自己的基礎設施,不是席位。新艙開的時候就有自己的了。

## 6.5 墓碑:為什麼腳本只認「hub 還活著的艙」

關窗(含面板 `[X]`)不會自動寫退出痕——`seat_exit.py record` 掛在 pane 指令尾巴,窗被殺它沒機會跑。
所以死艙的席位檔會一直看起來是活人。10-09 01:5x 實量:15 個死艙裡 27 席沒有退出痕,已逐艙
`seat_exit.py sweep` 補齊(只加欄不刪檔)。根因已修於主線 e291f82be(10-09 04:57):watch 每 300 秒自動補本艙,
也代補 hub 已不在的死艙。所以在新版上,墓碑最多存在 5 分鐘;舊版或 watch 沒在跑的機器仍要手動 sweep。
在根因修好之前,任何「列出現有席位」的工具都要嘛只看活 hub、要嘛先 sweep。
但 sweep 補上退出痕之後,死艙裡「被掃到死」的席又會被當墓碑——這就是第一段 💀 那半段存在的原因:
看退出**原因**和**時間**分辨「剛死的人」和「早就收工的墓碑」,不再只看有沒有退出痕。

## 6.6 在席裡開席要寫清楚開在哪一艙(主線 5771e2dbf 起)

AI 行程的環境裡**刻意沒有**艙變數(防新席繼承身分)。新版 `aib` 偵測到「人在某艙的窗格裡、只打三個參數」
會**擋下**並印兩種打法:本艙寫完整艙號(例 `20261009-011921-4f8d`,★ 不能寫四碼,四碼會被當成另一艙)
或 `--again`;真要新艙寫 `new`。本 skill 的 `regroup.sh` 一律明帶 `EZ2_AIHUB_SOCKET`,不受影響。

## 6.7 歸隊完要推修正時:共用的推包機器怎麼排隊(10-09 三席實踩)

歸隊常會順手修東西、要推主線。`~/wt-ship-3d156` 是幾席共用的推包 clone,靠 `host_lease.py` 排隊。今晚踩到的四件:

- **租約要綁 ship.py 本體的 pid,不要綁外層殼。** 租約只看「--pid 那個行程還在不在」。08:28 兩個不同對話的背景殼
  同時被外因收掉(rc 144),ship 本體還在跑,租約卻顯示「可接管」—— 別人照字面接就會撞上還在跑的閘。
  做法:`nohup python3 tools/release/ship.py package ... & SP=$!` 之後才 `acquire --pid $SP`;拿不到就 `kill $SP`。
- **「有沒有人在推」只算 python 的 ship.py 本體。** 用指令文字找 `ship.py package` 會把排隊的殼、自己的等待程式都算進去 ⇒ 互等或自己等自己。
  做法:`ps -axo comm=,command= | awk '$1 ~ /[Pp]ython/ && /ship\.py package --branch/'`;等待程式寫成腳本檔再跑,指令文字裡就不會有那幾個字。
- **clone 停在別人的分支時,先 `git checkout` 自己的分支再 `reset --hard`。** 直接 reset 會把別人的包分支蓋掉。
- **先排的先推。** 看 `~/.ez2-aib/state/host-lease.json.log` 的 acquire/release 紀錄;要讓就等對方那一輪的 release 出現再拿鎖。

## 7 收尾

搬完照 `docs/REPORTING-RULES.md` 回報 the maintainer:第一句講「現在能不能用」,
沒量到的(例如兩隻 bot 的綁定值)要跟量到的一樣顯眼。
