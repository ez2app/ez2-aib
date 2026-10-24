#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# 回報格式檢查。用法:report-lint.sh <檔案|->
# 硬擋(rc=1)= 機器真的驗得了的;警告(印出但 rc=0)= 只是代理指標。
# ★ 兩者必須分開,否則會假裝驗得了白話。
set -u
F="${1:?用法: report-lint.sh <檔案|->}"
T=$(cat "$F")
# 這支腳本的真實位置往上兩層 = repo 根(供代號閘找模組;`$0` 可能是相對路徑)
LINT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

# ★ SCRIPT_DIR 上提到這裡:下面的 ⇒ 段劃分要用它,而最早的內容閘在第 14 行就要讀劃分結果。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# ── ★ 裁定 R4-b:機器輸出不是作者宣稱 ⇒ 判定母體分兩半。
#   內容閘讀 $TC(剝除 ⇒ 段後的本文);R4 形狀閘讀 $T(它要同時看到作者段與 ⇒ 段)。
#   劃分只有一份,住在 aib-report.py 的 arrow_segment_lines()/split_report_for_lint()
#   —— 這裡**不重寫**一份(共用判斷式而每支都用它,是兩件事)。
_r4split() {   # $1 = --content | --arrows
  # ★ 這是**唯一**的那道結構縫(裁定 R4-b;走 B18-b closed_fence_lines 先例)。
  #   兩類閘共用它,不得各自再寫一份判定。
  # ★ 為什麼住在 lint 而不是產品裡:劃分只有 lint 用得到,而
  #   test-report-send-forcepoint-second-party 會把渲染器複製成 aib-report-real.py、
  #   留一個 shim 當 aib-report.py —— 以模組載入產品會把整支產品跑起來(實測 argparse 吃到 --content)。
  #   ★ 「單一來源」不等於「住在被測物裡」。
  # ★★ 承重前提,具名:剝除 ⇒ 段之所以不開後門,**只因為輸入端拒收含 ⇒ 的作者文字**
  #   (aib-report.py validate() 的具名 InputError)。沒有它,作者可以自己寫一段假 ⇒,
  #   把任何字面搬出 lint 的判定母體。改動那一條之前先讀這一段。臂 I0 釘它。
  printf '%s' "$T" | /usr/bin/env python3 -c '
import sys
ARROW = "\u21d2"      # 用**裸 ⇒** 判:⇒ 段超寬時 ⇒ 會落在行尾,帶空白的分隔字串會零命中而安靜失效
want = sys.argv[1]
lines = sys.stdin.read().splitlines()
marked, inside = set(), False
for i, line in enumerate(lines):
    if inside:
        marked.add(i); inside = not line.rstrip().endswith(")")
    elif ARROW in line:
        marked.add(i); inside = not line.rstrip().endswith(")")
content, arrows = [], []
for i, line in enumerate(lines):
    if i not in marked:
        content.append(line); continue
    if ARROW in line:
        head, _, tail = line.partition(ARROW)
        content.append(head.rstrip() + ")")   # 還原成 R4 之前那一行的樣子
        arrows.append(tail.strip())
    else:
        arrows.append(line.strip())
out = content if want == "--content" else arrows
sys.stdout.write("\n".join(out) + "\n")
' "$1"
}
TC="$(_r4split --content)"
# ★ fail-closed:劃不出來就不放行。劃分失敗時 $TC 會是空的,而空的本文會讓
#   每一道內容閘都「零命中」= 全部通過 —— 失效值等於安全值,正是今晚一直在擋的那個形狀。
if [ -n "$T" ] && [ -z "$TC" ]; then
  echo "🔴 硬擋:讀不到 aib-report.py 的 ⇒ 段劃分(arrow_segment_lines)—— 內容閘沒有母體可用,不得放行" >&2
  exit 1
fi
HARD=0; WARN=0
say(){ printf '%s\n' "$1"; }
HDR_TOOL="$SCRIPT_DIR/../hdr/aib-hdr.py"

# ── 硬擋一:腳註要有時間
if ! printf '%s' "$TC" | grep -qaE '20[0-9]{2}-[01][0-9]-[0-3][0-9][^0-9]+[0-9]{1,2}:[0-9x]{2}'; then
  say "🔴 硬擋:找不到時間戳(格式 YYYY-MM-DD HH:MM)"; HARD=1
fi
# 第一行的來源是收據,不是「長得像」的 regex。
FIRST_LINE=$(printf '%s\n' "$TC" | sed -n '1p')
if [ ! -f "$HDR_TOOL" ] \
    || ! /usr/bin/python3 "$HDR_TOOL" --verify "$FIRST_LINE" >/dev/null 2>&1; then
  say "🔴 硬擋:第一行不是 aib-hdr.py 可驗證的標頭"; HARD=1
fi
# ── 硬擋二:腳註要有真人判斷欄。判結構,不枚舉欄內句型。
DECL=$(printf '%s\n' "$TC" | grep -aE '^[[:space:]]*(真人判斷|需人判斷|真人)[::]' | tail -1)
if [ -z "$DECL" ]; then
  say "🔴 硬擋:腳註沒有「真人判斷:」那一行"; HARD=1
fi
# ── ★★ 硬擋三:自相矛盾 —— 宣告是結構不是字串(design 08-23 修)
#    ★ 舊版看「文中有沒有那四個字」→ 兩個方向都錯:
#      談論它就等於宣告它(誤擋);紅燈與它同一行時逐行排除把整行剔掉(漏擋)。
#    ★ 新版只讀腳註「真人:」那一行的宣告欄位;內文怎麼引用都不影響。
if printf '%s' "$DECL" | grep -qa '不需回應'; then
  # 內文 = 全文扣掉腳註四行(時間行/真人/更新/下步/巡查)
  # ★ 只扣掉「結構欄位」四行;時間戳那一行的「已執行:」是內文,必須掃
  #   (前一版把整條時間戳行排除 → 紅燈藏在那一行就逃掉,同行繞過只是換了位置)
  BODY=$(printf '%s\n' "$TC" | grep -avE '^[[:space:]]*(真人判斷|需人判斷|等待更新|再下一步|下一步|下次巡查|真人問|真人|更新|下步|巡查)[::]')
  # 宣告行自己也是可分類的母體:不能因為它是腳註就整行排除,
  # 否則「真人:不需回應 🔴 待裁」會把紅燈藏在同一行。
  BAD=$(printf '%s\n%s\n' "$BODY" "$DECL" \
        | grep -aE '🔴|⚠️|判不出|不確定|未驗|待裁|候你|卡住' | head -3)
  if [ -n "$BAD" ]; then
    say "🔴 硬擋:腳註宣告「不需回應」,但內文有下列需要人的東西 ——"
    printf '%s\n' "$BAD" | sed 's/^/        /'
    say "        ★ 一個不誠實的「不需回應」比沒有腳註更糟:它讓人在該介入時不介入。"
    HARD=1
  fi
fi
# ── ★ 硬擋四:打了勾卻沒有憑據(舊載體原形是「打勾 PASS (lint Check 10)」)
# ★★ 兩個缺陷一起修(艦長 16:29 指席;S4 是批4 全套四紅之一):
#   (a) 舊版把「這一行有左括號」也當成憑據,而渲染器**永遠**把憑據包在括號裡
#       —— 那個判定式對渲染路徑**恆假**,硬擋四從來沒有打中過任何東西。
#       恆假的閘與沒有閘一樣,而它更糟:版面上有一格看起來守著。
#   (b) 舊版寫死那個符。S1 換符那天,它會去找一個**沒人再產出的符**,
#       零命中 = 綠 —— 延時引信。符改成向單源現查。
#   ★ 單源檔頭逐字:「讀不到本檔要紅,不得有 fallback 預設值:
#     fallback 就是失效值等於安全值。」所以下面讀不到就 HARD=1,不給預設符。
#   ★ 本檔判定面刻意不寫任何狀態符字面 —— 寫了就是第三份寫死的符。
ENUM_JSON="$SCRIPT_DIR/status-enum.json"
# ── ★ 分隔線字面從單源讀,**不得用段身份去組字面**(艦長 5d784 2026-08-29 裁,釘子二/三)。
#   舊版寫 for _seg in 進度 摘要 反思 收尾 然後組 "꧁༺♡~~${_seg}~~♡༻꧂" ——
#   那是「拿鍵當字」,四字版上線當天這道閘會把每一份合法報告都硬擋掉(六道紅)。
#   ★ 下面用的四個是**段身份(key)**不是要印的字;身份改了會取到空值 → HARD=1,不給預設。
SEP_LINES=$(/usr/bin/python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
seps = data["separators"]
for seg in ("進度", "摘要", "反思", "收尾"):
    print(seps[seg])
' "$ENUM_JSON" 2>/dev/null || true)
SEP_PROGRESS=$(printf '%s\n' "$SEP_LINES" | sed -n '1p')
SEP_SUMMARY=$(printf '%s\n' "$SEP_LINES" | sed -n '2p')
SEP_REFLECT=$(printf '%s\n' "$SEP_LINES" | sed -n '3p')
SEP_CLOSING=$(printf '%s\n' "$SEP_LINES" | sed -n '4p')
if [ -z "$SEP_PROGRESS" ] || [ -z "$SEP_SUMMARY" ] || [ -z "$SEP_REFLECT" ] || [ -z "$SEP_CLOSING" ]; then
  say "🔴 硬擋:讀不到單源 $ENUM_JSON 的 separators —— 判定面沒有分隔線字面可用,不得放行"
  HARD=1
fi
# 輸入端 alias(二字版)。★ 只用來**認出**它,不是用來放行它:產出端一律四字。
SEP_ALIASES=$(/usr/bin/python3 -c '
import json, sys
print("\n".join(json.load(open(sys.argv[1])).get("separator_aliases") or {}))
' "$ENUM_JSON" 2>/dev/null || true)
DONE_MARK=$(/usr/bin/python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["marks"]["done"])' "$ENUM_JSON" 2>/dev/null || true)
if [ -z "$DONE_MARK" ]; then
  say "🔴 硬擋:讀不到單源 $ENUM_JSON 的 marks.done —— 判定面沒有符可用,不得放行"
  HARD=1
else
  # ★ 更正十一:憑據住在子項的**續行**(第一行只有符號/編號/標題)。判「有 ✅ 沒憑據」要把
  #   該 ▸ 行與其後的續行(到空行/│ 橋/下一個 ▸/分隔線為止)接起來看;只看第一行=閘還在但看不到東西。
  NOEV=$(printf '%s\n' "$TC" | python3 -c '
import sys,re
done=sys.argv[1]
lines=[l.rstrip("\n") for l in sys.stdin]
ev=re.compile(r"rc=|[0-9]+/[0-9]+|[0-9]{2,}|sha|已推|已合")
out=[]
i=0
while i < len(lines):
    l=lines[i]
    if done in l:
        block=[l]; j=i+1
        while j < len(lines):
            n=lines[j]
            if not n.strip() or "▸" in n or n.strip()=="│" or "~~" in n: break
            block.append(n); j+=1
        if not ev.search(" ".join(block)): out.append(l)
        i=j if j>i+1 else i+1
    else:
        i+=1
for l in out[:3]: print(l)
' "$DONE_MARK")
  [ -n "$NOEV" ] && { say "🔴 硬擋:有 $DONE_MARK 但那一行沒有憑據(數字/rc/sha/比例)——"; printf '%s\n' "$NOEV" | sed 's/^/        /'; say "        ★ 只有 emoji 的那一格,與「我沒查」逐字同形。"; HARD=1; }
fi


# ── ★ 硬擋 R4:憑據指令真執行(規格 SPEC-R4 §一「永不靜默」;判準顆 005f8649 L1/L2)
#   兩條規則,都在**接合後的續行**上判 —— ⇒ 段超寬會被 wrap_cells 折,
#   照實體行判會在產品完全正確時假紅(root3 ★4 裁)。接法沿用硬擋四那一套。
#   ★ 只對「續行裡有 `;指令 `」的子項要求 ⇒。沒有 evidence_command 的舊報一格不動 ——
#     否則 R4 落地當天,每一份既有報告都會變紅,而它們一個字都沒錯。
# ★ R4 未啟用(EZ2_AIB_EVIDENCE_EXEC 未設)時整段規則不生效 —— 否則既有報告
#   每一個有 evidence_command 的子項都會因為「沒有 ⇒」被判紅,而它們一個字都沒錯。
#   文字裡已經有 ⇒ 段時也視為已啟用(獨立跑 lint 的路徑沒有那個環境變數)。
R4MODE="$(printf '%s' "${EZ2_AIB_EVIDENCE_EXEC:-}" | tr 'A-Z' 'a-z')"
if [ "$R4MODE" = "on" ] || [ "$R4MODE" = "off" ] || printf '%s' "$T" | grep -q '⇒'; then
R4BAD=$(printf '%s\n' "$T" | python3 -c '
import re,sys
lines=[l.rstrip("\n") for l in sys.stdin]
out=[]
i=0
while i < len(lines):
    l=lines[i]
    if "\u25b8" in l:
        block=[l]; j=i+1
        while j < len(lines):
            n=lines[j]
            if not n.strip() or "\u25b8" in n or n.strip()=="\u2502" or "~~" in n: break
            block.append(n); j+=1
        joined=" ".join(x.strip() for x in block)
        if ";\u6307\u4ee4 " in joined and "\u21d2" not in joined:
            out.append("NOEXEC|" + l.strip()[:70])
        elif "\u21d2" in joined:
            # c6411 03:4x 裁:\u21d2 段形狀不合【單一形狀】也要紅。
            # 單一形狀的共同尾巴是 ` \u00b7 HH:MM` —— 執行類與非執行類都有。
            # 少了它 = 這一段不是本規格產生的,或是被人手改過。
            tail = joined.split("\u21d2", 1)[1]
            tail = tail[:tail.rindex(")")] if ")" in tail else tail
            if not re.search(r"\u00b7\s*\d{2}:\d{2}\s*$", tail):
                out.append("SHAPE|" + l.strip()[:60] + " \u21d2" + tail.strip()[:40])
        i=j if j>i+1 else i+1
    else:
        i+=1
for l in out[:5]: print(l)
')
if [ -n "$R4BAD" ]; then
  # ★ 「被具名拒絕」只給警告,不硬擋 —— 這一格待 c6411 裁(root2 03:4x 上呈)。
  #   理由:硬擋會讓 stdout 保持空白,而規格 E4 要求讀者【看見】那句「拒絕執行」。
  #   兩半互相看不見:lint 紅的那個東西,正好被 lint 的紅藏起來。
  #   且「拒絕執行」是誠實的:指令走到了牆前面、被具名擋下,版面上說了實話。
  #   把它硬擋等於因為它誠實而懲罰它,代價是讀者連那句誠實都看不到。
  printf '%s\n' "$R4BAD" | grep -q '^REFUSED|' && say "⚠️  警告:憑據指令被拒絕執行 —— 那個數字沒有人複驗過,請把指令改寫成白名單形(唯讀 git 子命令 / python3 tests/ / python3 tools/report/ / bash tools/report/ / grep / ls / cat / tmux 唯讀)"
  # ★ 「沒跑」維持硬擋:規格 §一「永不靜默」。沒跑與跑過但沒印,在版面上逐字相同。
  if printf '%s\n' "$R4BAD" | grep -q '^NOEXEC|'; then
    say "🔴 硬擋:子項有 evidence_command 卻沒有 ⇒ 段 —— 這條指令沒被跑"
    HARD=1
  fi
  # ★ 形狀不合也硬擋(c6411 03:4x 裁):⇒ 段少了 · HH:MM。
  if printf '%s\n' "$R4BAD" | grep -q '^SHAPE|'; then
    say "🔴 硬擋:⇒ 段不合單一形狀(尾巴要有 · HH:MM)—— 不是本工具產生的,就是被人手改過"
    HARD=1
  fi
  printf '%s\n' "$R4BAD" | sed 's/^/        /'
fi
fi

# ── ★ 硬擋五:多層級(the maintainer 08-23 令「一定要多層級」)
#    編號自帶結構:EX -> EX.1 -> T-EX.1。縮排必須跟著編號走。
# ★ 三種樹枝都是同一個母體。舊版只收 `╰▸`,因此第一個 `╭▸` 與
#   中間 `├▸` 的父項完全看不見;合法的 E4 -> E4.3 會被誤判成孤兒。
TREE=$(printf '%s\n' "$TC" | grep -aE '╭▸|├▸|╰▸' || true)
if [ -n "$TREE" ]; then
  BAD5=$(printf '%s\n' "$TREE" | python3 -c '
import sys,re,json,io
lines=[l.rstrip("\n") for l in sys.stdin if l.strip()]
bad=[]
id_re=re.compile(r"(?:[A-Za-z][A-Za-z0-9]*(?:\.[0-9A-Za-z]+)*|T-[A-Za-z0-9.]+)$")
# ★ 符集只有一個來源:status-enum.json。這裡曾經是第二份寫死的拷貝,
#   而兩份拷貝不會互相報錯——碼系一改就會有一份悄悄過期。
marks=set(json.load(io.open(sys.argv[1],encoding="utf-8"))["marks"].values())
parsed=[]
for l in lines:
    m=re.search(r"[╭├╰]▸\s*(.*)$", l)
    if not m: continue
    fields=m.group(1).split()
    if fields and fields[0] in marks: fields=fields[1:]
    if not fields or not id_re.fullmatch(fields[0]): continue
    num=fields[0]
    # F/§10.12: depth by dots (cap 3) or T- prefix → Task layer.
    # 0=大項/總綱, 1=子項, 2=更深子項, 3=Task。舊兩層(大項+子項)行為不變。
    if num.startswith("T-"):
        depth = 3
    else:
        depth = min(num.count("."), 3)
    parsed.append((l,num,depth))
major_ids=[num for _,num,depth in parsed if depth==0]
last_major=major_ids[-1] if major_ids else None
fams=set()
ids_by_depth={0:set(),1:set(),2:set(),3:set()}
for l,num,depth in parsed:
    ids_by_depth[depth].add(num)
    if depth==0: fams.add(num)
    elif depth==1:
        fam=num.split(".")[0]
        if fam not in fams: bad.append("子項目 %s 上面沒有大項目 %s" % (num,fam))
        if fam==last_major and re.match(r"^  │  [├╰]▸", l):
            bad.append("末段子層 %s 已結束，不得繼續畫 │" % num)
        elif fam==last_major and not re.match(r"^\u00a0{3}[├╰]▸", l):
            bad.append("末段子層 %s 缺少可保留的縮排" % num)
        elif fam!=last_major and not re.match(r"^  │  [├╰]▸", l):
            bad.append("子層 %s 前缺少 │ 可見縮排" % num)
    elif depth in (2, 3):
        # 四層:父層 id 必須已出現(A1.1.1 → 父 A1.1 或大項 A1)
        parent=".".join(num.split(".")[:-1]) if not num.startswith("T-") else num[2:]
        if num.startswith("T-"):
            parent_base=num[2:].split(".")[0]
            if parent_base not in fams:
                bad.append("TASK %s 上面沒有大項目 %s" % (num, parent_base))
        else:
            fam=num.split(".")[0]
            if fam not in fams:
                bad.append("深層 %s 上面沒有大項目 %s" % (num, fam))
            # 縮排:末大項分支必須用 NBSP;非末必須帶 │
            if fam==last_major and re.match(r"^  │", l):
                bad.append("末段深層 %s 已結束，不得繼續畫 │" % num)
            elif fam==last_major and not l.startswith("\u00a0"):
                bad.append("末段深層 %s 缺少可保留的縮排" % num)
            elif fam!=last_major and not re.match(r"^  │", l):
                bad.append("深層 %s 前缺少 │ 可見縮排" % num)
    else:
        base=num[2:].split(".")[0] if num.startswith("T-") else num.split(".")[0]
        if base not in fams: bad.append("節點 %s 上面沒有大項目 %s" % (num,base))
for b in bad: print(b)
' "$SCRIPT_DIR/status-enum.json" 2>/tmp/.report-lint-tree.$$ ) ; TREE_RC=$?
  # ★ 這裡曾經是 `2>/dev/null || true`:parser 一掛掉,BAD5 變空,硬擋五就靜默消失。
  #   失效值(空)與「檢查通過」(也是空)逐字同形——讀不到單源時整支 lint 會假綠。
  #   現在把「檢查器自己壞了」與「檢查通過」分成兩件事,前者一律紅。
  if [ "$TREE_RC" -ne 0 ]; then
    say "🔴 硬擋:樹狀檢查器自己失敗(rc=$TREE_RC)——這不是通過,是沒驗到"
    sed 's/^/        /' "/tmp/.report-lint-tree.$$" | tail -3
    say "        ★ 最常見成因:讀不到符集單源 status-enum.json。"
    HARD=1
  fi
  rm -f "/tmp/.report-lint-tree.$$"
  # ── ★ 更正十一:續行對齊與縮排(讀整份文字,不只 ▸ 行)。常數讀渲染器單源。
  BOX_CELLS=$(grep -aE '^BOX_CELLS[[:space:]]*=' "$SCRIPT_DIR/aib-report.py" | head -1 | sed -E 's/.*=[[:space:]]*([0-9]+).*/\1/')
  BAD5B=$(printf '%s\n' "$TC" | python3 -c '
import sys,re,unicodedata
box=int(sys.argv[1] or 1); NB="\u00a0"
def cells(t):
    n=0
    for c in t:
        if unicodedata.combining(c): continue
        n += box if c in "│├╰╭" else (2 if unicodedata.east_asian_width(c) in "WF" else 1)
    return n
lines=[l.rstrip("\n") for l in sys.stdin]
majors=[i for i,l in enumerate(lines) if re.match(r"^  [╭├╰]▸ ", l)]
last_major_start=majors[-1] if majors else -1
bad=[]
for i,l in enumerate(lines):
    m=re.match(r"^(.*?[├╰]▸ \S+ \S+ )(\S.*)$", l)
    if not m or re.match(r"^  [╭├╰]▸ ", l): continue
    title_cell=cells(m.group(1)); in_last = i > last_major_start
    j=i+1
    while j < len(lines):
        n=lines[j]
        if not n.strip() or "▸" in n or n.strip()=="│" or "~~" in n: break
        k=0
        while k < len(n) and n[k] in (" ",NB,"│"): k+=1
        if cells(n[:k]) != title_cell:
            bad.append("續行左緣未對齊標題首字(第 %d 行:%d≠%d 格)" % (j+1, cells(n[:k]), title_cell))
        if in_last and any(c==" " for c in n[:k]):
            bad.append("末段續行縮排必須是 U+00A0(第 %d 行用了普通空白,聊天貼出會塌)" % (j+1))
        if (not in_last) and not n.startswith("  │"):
            bad.append("非末段續行前缺少 │ 可見縮排(第 %d 行)" % (j+1))
        j+=1
for b in bad: print(b)
' "$BOX_CELLS" 2>/tmp/.report-lint-cont.$$ ); CONT_RC=$?
  if [ "$CONT_RC" -ne 0 ]; then
    say "🔴 硬擋:續行檢查器自己失敗(rc=$CONT_RC)——這不是通過,是沒驗到"; sed 's/^/        /' "/tmp/.report-lint-cont.$$" | tail -3; HARD=1
  fi
  rm -f "/tmp/.report-lint-cont.$$"
  if [ -n "$BAD5B" ]; then
    say "🔴 硬擋:續行縮排/左緣不合更正十一"; printf '%s\n' "$BAD5B" | sed 's/^/        /'; HARD=1
  fi
  if [ -n "$BAD5" ]; then
    say "🔴 硬擋:樹狀沒有多層級 —— 編號有結構而縮排沒跟著"
    printf '%s\n' "$BAD5" | sed 's/^/        /'
    say "        ★ 一定要多層級:總綱 > 大項目 > 子項目 > TASK"
    HARD=1
  fi

  BRIDGE5=$(printf '%s\n' "$TC" | python3 -c '
import re,sys
lines=[l.rstrip("\n") for l in sys.stdin]
major_re=re.compile(r"^  [╭├╰]▸\s+[A-Za-z][A-Za-z0-9]*\s+")
major_lines=[i for i,line in enumerate(lines) if major_re.search(line)]
for left,right in zip(major_lines, major_lines[1:]):
    if not any(re.fullmatch(r"  │\s*", line) for line in lines[left+1:right]):
        print("大項 %s 與下一大項之間缺少 │ 連接線" % lines[left].strip())
' 2>/dev/null || true)
  if [ -n "$BRIDGE5" ]; then
    say "🔴 硬擋:大項之間缺少垂直連接線 │ ——"
    printf '%s\n' "$BRIDGE5" | sed 's/^/        /'
    say "        ★ 空行仍在同一棵樹裡,必須以 │ 延續父層。"
    HARD=1
  fi
fi
# ── ★★ 硬擋八:狀態符只吃單源碼系,自創碼一律紅(G2 刀2)
#    ★ 為什麼是「認不得就紅」而不是「認不得就跳過」:
#      硬擋五的 parser 對認不得的符是 `continue`,那一行會**從母體裡消失**。
#      實測(基底 287bc535):同一個縮排缺陷,合法符 ⏳ → rc=1 抓到;
#      換成自創符 🌀 → rc=0 靜默放行。自創符因此是繞過整組樹狀檢查的萬用鑰匙。
#      對一份缺陷普查,保守的方向是讓可疑集合變大,不是變小。
if [ -n "$TREE" ]; then
  BAD8=$(printf '%s\n' "$TREE" | python3 -c '
import sys,re,json,io
_enum=json.load(io.open(sys.argv[1],encoding="utf-8"))
marks=set(_enum["marks"].values())
retired=_enum.get("retired") or {}
id_re=re.compile(r"(?:[A-Za-z][A-Za-z0-9]*(?:\.[0-9A-Za-z]+)*|T-[A-Za-z0-9.]+)$")
for l in (x.rstrip("\n") for x in sys.stdin if x.strip()):
    m=re.search(r"[╭├╰]▸\s*(.*)$", l)
    if not m: continue
    fields=m.group(1).split()
    if not fields: continue
    head=fields[0]
    if head in marks: continue          # 合法狀態符
    if not id_re.fullmatch(head):
        # ★ 母體邊界(校準過,不是猜的):只有「帶得出合法編號」的那些行是被編號的項目。
        #   圖例行如「╰▸ 完成 ✅ (rc=0, sha=…)」與「╰▸ 候產品」沒有編號,
        #   硬擋五本來就不檢查它們,所以那裡不存在可被洗掉的檢查。
        #   反過來,`╰▸ 🌀 E4.3 …` 的第二欄是合法編號 —— 它**是**一個項目,
        #   而它的符位置放了認不得的東西,那就是萬用鑰匙的形狀,一律紅。
        if len(fields) < 2 or not id_re.fullmatch(fields[1]):
            continue
        hint = "(已退役,新產出請用 %s)" % retired[head] if head in retired else ""
        print("不合法狀態符:%s %s ← 項目 %s;唯一碼系只有:%s"
              % (head, hint, fields[1], " ".join(sorted(marks))))
        continue
    if id_re.fullmatch(head):
        # 大項目那一行本來就沒有符;子項目/TASK 沒有符 = 殘格(判準 31cb256e R3)。
        if "." in head or head.startswith("T-"):
            print("缺狀態符:%s ← 子層 %s 沒帶符,SPEC 一-3 要求每個 Task 帶狀態符"
                  % (l.strip()[:56], head))
        continue

' "$SCRIPT_DIR/status-enum.json" 2>/tmp/.report-lint-mark.$$ ) ; MARK_RC=$?
  if [ "$MARK_RC" -ne 0 ]; then
    say "🔴 硬擋:狀態符檢查器自己失敗(rc=$MARK_RC)——這不是通過,是沒驗到"
    sed 's/^/        /' "/tmp/.report-lint-mark.$$" | tail -3; HARD=1
  fi
  rm -f "/tmp/.report-lint-mark.$$"
  if [ -n "$BAD8" ]; then
    say "🔴 硬擋:狀態符不合法(自創/退役/缺符)—— 唯一碼系在 tools/report/status-enum.json"
    printf '%s\n' "$BAD8" | head -3 | sed 's/^/        /'
    say "        ★ 自創符不只是格式問題:硬擋五對認不得的符是跳過,"
    say "          所以那一行的縮排/層級/孤兒檢查會一起失效。"
    HARD=1
  fi
fi

# ── ★ 硬擋六:過程不准進樹(它屬於腳註的「下步」)
PROC=$(printf '%s\n' "$TREE" | grep -aE '跑中|在跑|等待|等他|待收|收.*交件' || true)
if [ -n "$PROC" ]; then
  say "🔴 硬擋:樹裡出現「過程」而不是「項目」——"
  printf '%s\n' "$PROC" | head -3 | sed 's/^/        /'
  say "        ★ 那是你正在做的事,屬於腳註的「下步」,不是一個項目。"
  HARD=1
fi

# ── ★ 硬擋七:四條分隔線要帶標題(the maintainer 08-23 20:1x)
#    進度 / 摘要 / 反思 / 收尾 —— 缺一即紅。★ 反思段是新增的,最容易被漏掉。
printf '%s\n' "$SEP_LINES" | while IFS= read -r _line; do
  [ -n "$_line" ] || continue
  printf '%s' "$TC" | grep -qaF "$_line" || printf '%s\n' "$_line"
done > /tmp/.report-lint-missing.$$
if [ -s /tmp/.report-lint-missing.$$ ]; then
  while IFS= read -r _miss; do say "🔴 硬擋:缺分隔線 $_miss"; done < /tmp/.report-lint-missing.$$
  HARD=1
fi
rm -f /tmp/.report-lint-missing.$$

# ── ★ 硬擋七-b:輸入端 alias(二字版、被取代的舊羽翼定版)不得出現在**產出**裡(艦長 5d784 裁,釘子三;更正九起含舊定版整行)。
#    alias 是輸入端向下相容;產出端一律四字。沒有這一格,「二字仍然被產出」
#    與「已經全面四字」在這道閘上逐字相同 —— 而那正是本次改版要能被抓到的那件事。
if [ -n "$SEP_ALIASES" ]; then
  printf '%s\n' "$SEP_ALIASES" | while IFS= read -r _alias; do
    [ -n "$_alias" ] || continue
    printf '%s' "$TC" | grep -qaF "$_alias" && printf '%s\n' "$_alias"
  done > /tmp/.report-lint-alias.$$
  if [ -s /tmp/.report-lint-alias.$$ ]; then
    while IFS= read -r _hit; do
      say "🔴 硬擋:產出端出現輸入端 alias(二字版或舊羽翼字面)$_hit —— alias 只准在輸入端,產出一律現行定版(status-enum.json separators)"
    done < /tmp/.report-lint-alias.$$
    HARD=1
  fi
  rm -f /tmp/.report-lint-alias.$$
fi

# ── ★ 硬擋八:進度說明固定一行、最多 100 個 Unicode 字元。
#    這裡量的是可驗的結構與字數；「白話」是語意，不能假裝由 regex 證明。
#    整段合併計數，避免手動換行把兩段 60 字偽裝成各自未超標。
PROGRESS8=$(printf '%s\n' "$TC" | python3 -c '
import sys

lines = [line.rstrip("\n") for line in sys.stdin]
start_label = sys.argv[1]
end_label = sys.argv[2]
starts = [i for i, line in enumerate(lines) if line.strip() == start_label]
ends = [i for i, line in enumerate(lines) if line.strip() == end_label]
if len(starts) != 1:
    print("進度分隔線必須恰好一個")
elif len(ends) != 1 or ends[0] <= starts[0]:
    print("進度說明找不到後續摘要分隔線")
else:
    body = [line.strip() for line in lines[starts[0] + 1:ends[0]]
            if line.strip()]
    if body and body[0] == "（進度說明）":
        body = body[1:]
    joined = "".join(body)
    if len(body) != 1:
        print("進度說明必須恰好一個非空白行")
    if len(joined) > 100:
        print("進度說明共 %d 字，最多 100 字" % len(joined))
' "$SEP_PROGRESS" "$SEP_SUMMARY" 2>/dev/null || true)
if [ -n "$PROGRESS8" ]; then
  say "🔴 硬擋:進度說明必須單行且在 100 字內 ——"
  printf '%s\n' "$PROGRESS8" | sed 's/^/        /'
  HARD=1
fi

# ── 反思段:空白與「明說這一輪沒有」是兩種不同的狀態。
# ★ 字面走 -v 傳入並用 index() 逐位元組比,不寫進 awk 樣式:
#   分隔線含 ~ 與多位元組字,當成 regex 會多一層轉義風險。
REFLECTION=$(printf '%s\n' "$TC" | awk -v s="$SEP_REFLECT" -v e="$SEP_CLOSING" '
  index($0, s) { inside=1; next }
  index($0, e) { inside=0 }
  inside { print }
')
REFLECTION_TEXT=$(printf '%s\n' "$REFLECTION" | sed '/^[[:space:]]*$/d')
if [ -z "$REFLECTION_TEXT" ]; then
  say "🔴 硬擋:反思段留白;這一輪沒有新事也必須明說"; HARD=1
fi
if printf '%s' "$REFLECTION_TEXT" | grep -qa '這一輪沒有' \
    && printf '%s\n' "$TREE" | grep -qaE '🔴|待裁|卡住|未驗'; then
  say "🔴 硬擋:反思宣告「這一輪沒有」,但樹狀仍有紅燈或待裁"; HARD=1
fi

# ── 警告(代理指標,不硬擋)
# 反思與樹狀共用兩個以上的識別字,可能只是複述。這只是代理指標,不得硬擋。
REF_IDS=$(printf '%s\n' "$REFLECTION_TEXT" | grep -aEo '[A-Za-z][A-Za-z0-9.]*' | sort -u || true)
TREE_IDS=$(printf '%s\n' "$TREE" | grep -aEo '[A-Za-z][A-Za-z0-9.]*' | sort -u || true)
REF_OVERLAP=$(comm -12 <(printf '%s\n' "$REF_IDS") <(printf '%s\n' "$TREE_IDS") \
              | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
if [ "${REF_OVERLAP:-0}" -ge 2 ]; then
  say "⚠️ 警告:反思段與樹狀重疊 ${REF_OVERLAP} 個識別字,可能只是複述"; WARN=1
fi
if [ -n "$REFLECTION_TEXT" ] \
    && ! printf '%s' "$REFLECTION_TEXT" | grep -qa '這一輪沒有' \
    && ! printf '%s' "$REFLECTION_TEXT" | grep -qaE '因為|由於|所以|原因|你看不到'; then
  say "⚠️ 警告:反思段沒有說明對方為什麼不會知道"; WARN=1
fi
LONG=$(printf '%s\n' "$TC" | python3 -c '
import sys
print(sum(1 for line in sys.stdin if len(line.rstrip("\n")) > 200))
' 2>/dev/null || echo 0)
[ "$LONG" -gt 0 ] && { say "⚠️ 警告:有 $LONG 行超過 200 字元(段落可能過長)"; WARN=1; }
# ★ the maintainer 08-23 21:0x:表格行數不列入佔比。
#   理由:表格有三個正當位置(項目清單 / 安全量表 / 之前-現在-達成對照),
#   而原本「超過 25 行就警告」會對這三種正當用途誤報 ——
#   ★ 一個會誤報的警告,最後會被忽略。
#   保留的只有「整篇都是表格」這一種:非表格內容少於 5 行才提醒。
TB=$(printf '%s\n' "$TC" | grep -ac '^|')
# ★ 只數「散文行」:扣掉表格、樹狀、標題、分隔線、腳註四欄、星數行。
#   前一版把這些骨架也算進去 → 骨架本來就佔 5 行 → 門檻永遠不開火,
#   而「永遠不會開火的上限,與沒有上限逐字同形」(design 08-23)。
NONTB=$(printf '%s\n' "$TC" | grep -av '^|' | grep -av '╰▸\|├▸\|╭▸\|│' \
        | grep -avE '^【.*】$|^꧁|^★|^[0-9]{4}-[0-9]{2}-[0-9]{2}.*·' \
        | grep -avE '^[[:space:]]*(真人判斷|需人判斷|等待更新|再下一步|下一步|下次巡查|真人問|真人|更新|下步|巡查)[::]' | grep -cv '^[[:space:]]*$')
[ "$TB" -gt 0 ] && [ "$NONTB" -lt 5 ] && { say "⚠️ 警告:整篇幾乎只有表格(表格 $TB 行、非表格 $NONTB 行)——表格會把因果壓掉"; WARN=1; }
ST=$(printf '%s' "$TC" | grep -oa '★' | wc -l | tr -d ' ')
[ "$ST" -gt 12 ] && { say "⚠️ 警告:★ 共 $ST 個(密度過高會讓重點失效)"; WARN=1; }

ML=$(printf '%s\n' "$TC" | python3 -c '
import sys,re
lines=[l.rstrip("\n") for l in sys.stdin]
extra=0
for i,l in enumerate(lines[:-1]):
    if re.fullmatch(r"  │\s*", l) and lines[i+1].strip()=="":
        extra+=1
print(extra)
' 2>/dev/null || echo 0)
[ "${ML:-0}" -gt 0 ] && { say "⚠️ 警告:有 $ML 個大項連接線後出現多餘空行(│ 本身就是間隔)"; WARN=1; }

# ── ★ 更正十一:每行 ≤ REPORT_LINE_CELLS 顯示格(常數只住 aib-report.py 一處,這裡讀它的字面)
LINE_CELLS=$(grep -aE '^REPORT_LINE_CELLS[[:space:]]*=' "$SCRIPT_DIR/aib-report.py" | head -1 | sed -E 's/.*=[[:space:]]*([0-9]+).*/\1/')
BOX_CELLS=$(grep -aE '^BOX_CELLS[[:space:]]*=' "$SCRIPT_DIR/aib-report.py" | head -1 | sed -E 's/.*=[[:space:]]*([0-9]+).*/\1/')
if [ -z "$LINE_CELLS" ]; then
  say "🔴 硬擋:讀不到 aib-report.py 的 REPORT_LINE_CELLS —— 行寬判定面沒有尺可用,不得放行"; HARD=1
else
  # ★ 裁定 R4-b 更正二(a seat 2026-08-31 04:1x):**行寬硬擋讀全文**,不讀剝除後的本文。
  #   不對稱:內容閘的注入文字兩個方向都會動(可觸發=雜訊、可滿足=繞過)所以必須排除;
  #   行寬判定式 display_cells(line) > N 對同一行加字**單調遞增** ——
  #   注入只能觸發、不可能滿足 ⇒ 排除它是失去保護而換不到任何東西。
  #   ★ 更強的一格:規格 §二 要求「⇒ 段超寬照 wrap_cells 折」,
  #     行寬閘讀全文時**就是在驗那條**;讀剝除後的本文,那條規格要求在產線上零檢查。
  WIDE=$(printf '%s\n' "$T" | python3 -c '
import sys,unicodedata
lim=int(sys.argv[1]); box=int(sys.argv[2] or 1)
def cells(t):
    n=0
    for c in t:
        if unicodedata.combining(c): continue
        n += box if c in "│├╰╭" else (2 if unicodedata.east_asian_width(c) in "WF" else 1)
    return n
lines=sys.stdin.read().splitlines()
# ★ 範圍=樹狀區塊(第一個 ╭▸ 起、第一條分隔線前):更正十一寫的是「子項兩行/續行左緣」,
#   渲染器只折樹狀行;散文段(摘要/反思/收官)渲染器不折,lint 若硬擋它們,每一份有正常反思段的
#   報告都出不來(cap a seat 16:43 實踩:三行散文 148/496/134 格)。散文行寬另案(乙)。
starts=[i for i,l in enumerate(lines) if l.lstrip(" \u00a0").startswith("╭▸")]
seps=[i for i,l in enumerate(lines) if "~~" in l and "♡" in l]
lo=starts[0] if starts else len(lines); hi=seps[0] if seps else len(lines)
for i,l in enumerate(lines):
    if not (lo <= i < hi): continue
    if not l.strip(): continue
    w=cells(l)
    if w>lim: print("第 %d 行 %d 格:%s" % (i+1, w, l[:40]))
' "$LINE_CELLS" "$BOX_CELLS" | head -5)
  [ -n "$WIDE" ] && { say "🔴 硬擋:行寬超過 REPORT_LINE_CELLS=$LINE_CELLS 顯示格(用 cell 量,不是字元數)"; printf '%s\n' "$WIDE" | sed 's/^/        /'; HARD=1; }
fi

# ── ★ 硬擋:單獨出現的內部代號(2026-09-08,the maintainer 直令「做成程式,不要只靠記憶」)
# `docs/REPORTING-RULES.md` 第二、三條 09-07 就存在、`AGENTS.md` 也指著它,
# 而 09-08 那一席整天沒照做 —— **規則存在、有索引、當事人讀過,仍然擋不住**。
# 判準與必落空端住在 `src/cockpit/jargon_gate.py` 一份(不在這裡再抄一份正規式)。
# ★ 路徑要從**這支腳本的真實位置**往上找,不能用 `$0` 直接接相對路徑:
#   `$0` 可能是相對路徑,`abspath` 會對呼叫端的 cwd 解析,在別的樹裡就指錯地方。
#   2026-09-08 實付:證詞在拋棄式工作樹裡跑時,四支渲染回報的臂**全紅**,
#   而它們在兩棵真工作樹與 main 上都是綠的 —— 差別只有 cwd。
# ★ 載入不了 ⇒ **警告不是硬擋**。第一版寫成硬擋,於是「閘自己載入失敗」會
#   把**所有**回報渲染一起打掉 —— 一道會在自己壞掉時癱瘓主功能的閘,比沒有更糟。
#   它仍然要出聲(不靜默放行),但不擋人。
# ★ 兩個 stdin 重導向只有最後一個生效 —— 第一版寫成 `<<'PY' <<<"$T"`,
#   於是 python 讀到的是**回報內文當程式**,整段靜靜失效(`|| true` 把它吃掉)。
#   改回 `-c` 帶程式、stdin 帶內文、argv 帶根目錄,三條路各一個入口。
JARGON="$(printf '%s' "$T" | "${EZ2_PY3:-python3}" -c '
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "src", "cockpit"))
try:
    import jargon_gate
except Exception as exc:
    print("WARN 代號閘載入不了(%s)——這不是通過,是沒檢查" % type(exc).__name__)
    raise SystemExit(0)
ok, msg = jargon_gate.check(sys.stdin.read())
if not ok:
    print("HARD " + msg)
' "$LINT_HOME" 2>/dev/null || true)"
case "$JARGON" in
  HARD*) say "🔴 硬擋:${JARGON#HARD }"; HARD=1 ;;
  WARN*) say "⚠️ 警告:${JARGON#WARN }"; WARN=$((WARN+1)) ;;
esac

# ── ★ 硬擋:沒標實測、也沒標推估的「省多少」數字(2026-10-08,the maintainer:「不能只靠記憶」「把回報省多少格式也一併程式化」)
# 判準住在 `src/cockpit/savings_claim_gate.py` 一份;載入法、載入不了只警告,全照上面代號閘的做法(理由見那段)。
SAVINGS="$(printf '%s' "$T" | "${EZ2_PY3:-python3}" -c '
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "src", "cockpit"))
try:
    import savings_claim_gate
except Exception as exc:
    print("WARN 省時數字閘載入不了(%s)——這不是通過,是沒檢查" % type(exc).__name__)
    raise SystemExit(0)
ok, msg = savings_claim_gate.check(sys.stdin.read())
if not ok:
    print("HARD " + msg)
' "$LINT_HOME" 2>/dev/null || true)"
case "$SAVINGS" in
  HARD*) say "🔴 硬擋:${SAVINGS#HARD }"; HARD=1 ;;
  WARN*) say "⚠️ 警告:${SAVINGS#WARN }"; WARN=$((WARN+1)) ;;
esac

[ "$HARD" -eq 0 ] && [ "$WARN" -eq 0 ] && say "✅ 通過(硬擋 0、警告 0)"
[ "$HARD" -eq 0 ] && say "── 硬擋 0;警告不擋人" 
exit "$HARD"
