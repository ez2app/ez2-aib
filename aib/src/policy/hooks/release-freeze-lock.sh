#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 發版樹凍結 —— 鎖的單一真相源(T10,2026-08-27)
#
# ## 為什麼鎖要單獨一支檔
#
# 這把鎖有三個讀者:發版腳本自己(落鎖/解鎖)、pre-commit 閘(擋 commit)、
# reference-transaction 閘(擋 ref 更新)。三個讀者各寫一份判定 = 三把尺,
# 而「陳舊鎖」這種判定錯一次就是**靜默放行**。所以判定只寫在這裡,
# 三個消費者一律 source 本檔呼叫同一支函式。
#
# ## pid 為什麼不夠
#
# pid 會重用。只記 pid 的鎖在「原持有者已死、pid 被別的行程撿走」時
# 會判成「鎖還有效」——**一個永遠解不開的鎖**,而它看起來完全正常。
# 反向也成立:pid 重用成一個短命行程,鎖會被判成有效而其實早該過期。
# 所以鎖同時記 **pid + 該 pid 的行程啟動時間**;兩者一起才指認得了「同一個行程」。
# ★ 只有兩欄**一起**才擋得住 pid 重用——單獨任何一欄都不行,這是 L2 臂在證的事。
#
# ## 窗要有上限(a seat 08-28 裁,第 3 格)
#
# **一個解不開的鎖比沒有鎖貴得多。** 「發版掛住了而鎖還在」與「發版正在跑」
# 在別席眼裡**一模一樣** —— 兩個結局共用同一個觀測值,而先發生的那個是錯的。
# 所以鎖自己帶著上限:超過就judged 成 EXPIRED(等同陳舊,放行)並**大聲記紅**。
# ★ 上限寫進鎖檔本身,不是寫在讀取端:讀取端有三個,寫在那裡就是三把尺。
#
# ## 失效方向(不可放寬)
#
# 鎖**讀不到**(檔壞、權限、解析不出)一律 fail-closed 並出聲。
# ★ 探測器的失效值不得等於它表示安全的那個值:讀不到不是「沒有鎖」。
#   「沒有鎖」只有一個形狀——**那個檔不存在**。
#
# ## 用法
#
#   source release-freeze-lock.sh
#   freeze_lock_path                 -> 鎖檔路徑
#   freeze_lock_state                -> 印狀態字;rc 0=判定成功,3=判不出來(fail-closed)
#   freeze_lock_acquire              -> 落鎖;rc 0=拿到,4=已被別人持有(訊息具名),3=判不出來
#   freeze_lock_release              -> 解鎖(只解自己的)
#
# 紅線:每一個 rc 都獨立取,絕不接管線。

# ★ 路徑可由環境覆寫,理由與同 repo 既有的 AIB_RELEASE_STAMP_FILE /
#   AIB_RELEASE_LOGDIR 同一條:**那是搬落點,不是開行為旗標**。
#   本檔沒有任何 `if [ -n "$AIB_TEST_..." ]` 形狀的分支——測試看到的行為
#   與正式路徑逐字相同,只有檔案位置不同。
freeze_lock_path() {
  if [ -n "${AIB_RELEASE_LOCK_FILE:-}" ]; then
    printf '%s\n' "$AIB_RELEASE_LOCK_FILE"
    return 0
  fi
  local common
  common="$(git rev-parse --git-common-dir 2>/dev/null)"
  if [ -z "$common" ]; then
    # 解不出 git 目錄 = 判不出鎖在哪,不給預設值。
    return 3
  fi
  case "$common" in
    /*) printf '%s/aib-release.lock\n' "$common" ;;
    *)  printf '%s/%s/aib-release.lock\n' "$(pwd -P)" "$common" ;;
  esac
}

# 某 pid 的行程啟動時間。查不到(行程已死)回空字串 rc=1。
# ★ 這裡不能用「查不到就回一個預設值」——查不到與「啟動時間是某值」
#   必須是兩個可分辨的結果,否則死掉的行程會拿到一個合法的啟動時間。
freeze_lock_starttime() {
  local pid="$1" out
  case "$pid" in
    ''|*[!0-9]*) return 2 ;;
  esac
  # ★ 固定 LC_ALL=C:`ps -o lstart` 的**字面跟著殼的 locale 走**,而這個值
  #   要跨殼比對(寫鎖的殼與讀鎖的殼可以是不同 locale)。不釘住 locale,
  #   同一個行程在兩個殼裡會得到兩個字串,而「不同」在下游是放行側(缺陷三十)。
  out="$(LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null)"
  [ -n "$out" ] || return 1
  # 壓掉前後空白與內部連續空白,讓寫入與讀回逐字可比。
  printf '%s\n' "$out" | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'
}

# 某 pid 的行程啟動時間,換算成 epoch 秒(整數)。查不到回空字串 rc=1。
# ★ 為什麼要整數:字串版(上面那支)即使釘了 locale,鎖檔裡還躺著**別的 locale
#   寫下的舊值**,而那個比對失敗會被讀成「pid 已被重用」——也就是放行。
#   整數沒有這個自由度:同一個行程在任何殼裡都是同一個數。
# ★ 算不出來要回 rc=1(判不出來),不可回 0 或現在時刻——那會讓「算不出」
#   與「剛啟動」同形。
freeze_lock_start_epoch() {
  local pid="$1" ls out
  case "$pid" in
    ''|*[!0-9]*) return 2 ;;
  esac
  ls="$(freeze_lock_starttime "$pid")" || return 1
  [ -n "$ls" ] || return 1
  # BSD/macOS 先,GNU 次之:兩支都試過才算「算不出來」。
  out="$(LC_ALL=C date -j -f '%a %b %e %T %Y' "$ls" '+%s' 2>/dev/null)" \
    || out="$(LC_ALL=C date -d "$ls" '+%s' 2>/dev/null)" || out=""
  case "$out" in
    ''|*[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "$out"
}

# 讀鎖檔的一個欄位。檔不存在回 rc=1(那是「沒有鎖」);
# 檔在但讀不到/欄位缺 回 rc=3(那是「判不出來」)。
freeze_lock_field() {
  local f="$1" key="$2" line val
  [ -e "$f" ] || return 1
  [ -r "$f" ] || return 3
  val=""
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "${key}="*) val="${line#${key}=}" ;;
    esac
  done < "$f" 2>/dev/null || return 3
  [ -n "$val" ] || return 3
  printf '%s\n' "$val"
}

# 鎖狀態。stdout 印狀態字 + 細節;rc:0=判定成功,3=判不出來(fail-closed)。
#   NONE            沒有鎖(那個檔不存在)
#   HELD <pid> <since> <version>    有效鎖,持有者活著且是同一個行程
#   STALE <理由>    鎖在但已過期(行程死了,或 pid 被重用成別的行程)
#   EXPIRED <理由>  持有者還活著,但窗開太久 —— 超過鎖檔自己記的上限
freeze_lock_state() {
  local f rc pid start since ver now_start
  f="$(freeze_lock_path)" || {
    printf '解不出鎖檔路徑(git rev-parse --git-common-dir 失敗)\n' >&2
    return 3
  }
  if [ ! -e "$f" ]; then
    printf 'NONE\n'
    return 0
  fi
  if [ ! -r "$f" ]; then
    printf '鎖檔存在但讀不到:%s —— 讀不到不是「沒有鎖」,不放行\n' "$f" >&2
    return 3
  fi
  pid="$(freeze_lock_field "$f" pid)"; rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$pid" ]; then
    printf '鎖檔缺 pid 欄或解析失敗(rc=%s):%s\n' "$rc" "$f" >&2
    return 3
  fi
  start="$(freeze_lock_field "$f" start)"; rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$start" ]; then
    # ★ 只有 pid 沒有 start 的鎖,正是 L2 要擋的那種鎖。它不是「比較弱的鎖」,
    #   是**判不出同一性的鎖**——所以是 fail-closed,不是降級成只比 pid。
    printf '鎖檔缺 start 欄(行程啟動時間):%s —— 只有 pid 判不出 pid 重用,不放行\n' "$f" >&2
    return 3
  fi
  case "$pid" in
    ''|*[!0-9]*)
      printf '鎖檔 pid 欄不是數字:%s\n' "$pid" >&2
      return 3 ;;
  esac
  since="$(freeze_lock_field "$f" since)" || since="(未記)"
  ver="$(freeze_lock_field "$f" version)" || ver="(未記)"

  now_start="$(freeze_lock_starttime "$pid")"; rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$now_start" ]; then
    printf 'STALE 持有者 pid=%s 已不存在(鎖起於 %s)\n' "$pid" "$since"
    return 0
  fi
  # ── pid 重用判定:比整數,不比字串(缺陷三十)────────────────────
  # ★ 舊碼直接比 `lstart` 字串,而那個字串跟著**讀鎖那個殼**的 locale 走:
  #   zh_TW 印「日 9/ 6 01:48:19 2026」、C 印「Sun Sep  6 01:48:19 2026」。
  #   於是凍結對誰開由 locale 決定,而判成 STALE 是**放行**——失效形狀是綠。
  # ★ 舊格式鎖(沒有 start_epoch):**不拿字串判**,讓它落到下面當 HELD/EXPIRED。
  #   判不出同一性時預設站失效側;拿一個會跨 locale 誤判的尺去判,比不判更糟。
  local start_epoch now_epoch
  start_epoch="$(freeze_lock_field "$f" start_epoch)" || start_epoch=""
  case "$start_epoch" in
    ''|*[!0-9]*) : ;;
    *)
      now_epoch="$(freeze_lock_start_epoch "$pid")"; rc=$?
      if [ "$rc" -ne 0 ] || [ -z "$now_epoch" ]; then
        printf '鎖檔有 start_epoch 但算不出 pid=%s 現在的啟動 epoch —— 判不出同一性,不放行\n' "$pid" >&2
        return 3
      fi
      if [ "$now_epoch" != "$start_epoch" ]; then
        printf 'STALE pid=%s 仍存在但啟動時間不同(鎖記 %s / 現為 %s)—— pid 已被重用\n' \
          "$pid" "$start_epoch" "$now_epoch"
        return 0
      fi ;;
  esac
  # ── 窗上限:持有者還活著,但窗開太久 ──────────────────────────
  # ★ 上限與起始時刻都取自**鎖檔自己**,不取自讀取端的環境:三個讀取端
  #   各帶一份設定就是三把尺,而其中一把調寬就等於沒有上限。
  local epoch max_sec now age
  epoch="$(freeze_lock_field "$f" epoch)" || epoch=""
  max_sec="$(freeze_lock_field "$f" max_sec)" || max_sec=""
  case "$epoch$max_sec" in
    ''|*[!0-9]*) : ;;   # 舊格式的鎖沒有這兩欄:不判上限,也不因此放行
    *)
      now="$(date -u '+%s')"
      age=$(( now - epoch ))
      if [ "$age" -gt "$max_sec" ]; then
        printf 'EXPIRED pid=%s 仍活著,但凍結窗已開 %s 秒(上限 %s)—— 起於 %s,版本 %s\n' \
          "$pid" "$age" "$max_sec" "$since" "$ver"
        return 0
      fi ;;
  esac
  printf 'HELD %s %s %s\n' "$pid" "$since" "$ver"
  return 0
}

# 窗逾時要**大聲記紅**,不是安靜放行:記在鎖檔旁邊,下一個走進來的人看得到。
# ★ 「發版掛住了而鎖還在」與「發版正在跑」在別席眼裡一模一樣 —— 這一列就是
#   把那兩個結局分開的唯一證物。
freeze_lock_note_expired() {
  local detail="$1" f note
  f="$(freeze_lock_path)" || return 1
  note="${f}.expired.log"
  printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$detail" >> "$note" 2>/dev/null || return 1
  printf '%s\n' "$note"
  return 0
}

# ★★ 這裡本來有一支 freeze_lock_owner_is_ancestor():鎖的持有者若是我的祖先行程
#    就放行,好讓發版腳本自己的 ref 寫入不被自己的鎖擋住。
#    **2026-08-28 a seat 裁掉**,理由值得留在這裡:那是一份白名單,
#    而**白名單會留下另一份更窄的白名單**。改用時間界:
#      凍結窗 = 全套開跑那一刻 -> tag 落地那一刻,而 tag 落地**就是解鎖事件本身**,
#      不是例外。這樣清單長度是 0,沒有洞可以長。
#    所以本檔沒有任何「誰可以通過」的名單,三個消費者也不得自己加一份。

# ══ 逃生提示:那句話對不對,取決於窗開沒開(批7;判準 N1-N5)═══════════
#
# ★ 2026-08-28 `a seat` 在窗開著時實測:`git commit --no-verify` **真 rc=128**,
#   `✖ 發版進行中…要動的 ref(1 個):HEAD`,HEAD 未前進。
#   **成因:commit(以及 push)本身就是一次 ref 更新;`--no-verify` 只繞得過
#   pre-commit / pre-push,繞不過 reference-transaction,而後者沒有 `--no-verify`。**
#
# ★★ 為什麼**不整行刪**(判準 N2;本庫另有裁定「在這個庫裡 `--no-verify` 就是被設計成
#   那道門」):**那句話在沒有窗的時候是對的 —— 缺的是條件,不是那句話。**
#
# ★★★ 為什麼判斷放這裡而不是三支 hook 各寫一份:本席 08-28 自己踩過 ——
#   同一個道理的三份手寫實作,**只有第一份把理由寫出來;被複製的是機制,
#   被丟掉的是理由**。所以三支消費者只呼叫,不各自判。
#
# 用法:freeze_lock_escape_hint commit|push   (自己印到 stderr)
# 三態,**不得壓成兩態**:窗開著 / 沒有窗 / **判不出來**。
freeze_lock_escape_hint() {
  local what="${1:-commit}" state rc
  state="$(freeze_lock_state 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 0 ]; then
    # ★ 判不出來就說判不出來 —— 不得默默給任何一句,那會在錯的一半時候騙人。
    echo "  逃生:**判不出凍結窗的狀態**,所以這裡不敢告訴你 \`--no-verify\` 有沒有用。" >&2
    echo "        先查:bash src/policy/hooks/release-freeze-lock.sh status" >&2
    return 0
  fi
  case "$state" in
    HELD*)
      echo "  ★ 現在有發版凍結窗開著 —— **\`git $what --no-verify\` 不會讓你過**。" >&2
      echo "    $what 本身就是一次 ref 更新,而 reference-transaction 沒有 --no-verify。" >&2
      echo "    要落檔就寫 ~/.ez2-aib/pending/,等窗關。" >&2
      echo "    唯一逃生:rm .git/ai-b/hooks/reference-transaction 後 dm root2" >&2
      echo "              (該檔共用給所有工作樹,是全庫級動作)" >&2
      ;;
    *)
      # 沒有窗(NONE/STALE/EXPIRED):原本那句話在這裡是對的,原字保留。
      echo "  真的要繞:git $what --no-verify(會留在 reflog 裡)" >&2
      ;;
  esac
  return 0
}

# 落鎖。rc:0=拿到,4=已被別人持有,3=判不出來。
freeze_lock_acquire() {
  local f state rc ver start
  f="$(freeze_lock_path)" || {
    printf 'aib-release: 解不出鎖檔路徑,不落鎖也不放行\n' >&2
    return 3
  }
  state="$(freeze_lock_state)"; rc=$?
  if [ "$rc" -ne 0 ]; then
    return 3
  fi
  case "$state" in
    HELD\ *)
      printf 'aib-release: 發版鎖已被持有 —— %s\n' "$state" >&2
      printf '            鎖檔:%s\n' "$f" >&2
      printf '            同一棵樹不得同時跑兩次發版。確認那個行程,不要奪鎖。\n' >&2
      return 4 ;;
    STALE\ *)
      printf 'aib-release: △ 發現陳舊鎖,接手 —— %s\n' "$state" >&2 ;;
    EXPIRED\ *)
      printf 'aib-release: ✖ 上一次的凍結窗**逾時**仍未關閉 —— %s\n' "${state#EXPIRED }" >&2
      _note="$(freeze_lock_note_expired "acquire 時發現逾時窗:${state#EXPIRED }")" \
        && printf 'aib-release:   已記紅 -> %s\n' "$_note" >&2
      printf 'aib-release:   持有者還活著,但窗開太久。接手前先確認那個行程是不是掛住了。\n' >&2 ;;
  esac
  start="$(freeze_lock_starttime "$$")"; rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$start" ]; then
    printf 'aib-release: 取不到自己的行程啟動時間,寫不出可判同一性的鎖 —— 不放行\n' >&2
    return 3
  fi
  # ★ start_epoch 才是讀鎖端真正拿來判的欄位(start 只留給人看)。
  #   算不出來就不寫鎖:寫一把讀鎖端判不出同一性的鎖 = 寫一把會被判成 STALE 的鎖。
  start_epoch="$(freeze_lock_start_epoch "$$")"; rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$start_epoch" ]; then
    printf 'aib-release: 算不出自己的行程啟動 epoch,寫不出跨 locale 可判的鎖 —— 不放行\n' >&2
    return 3
  fi
  ver=""
  [ -r VERSION ] && IFS= read -r ver < VERSION
  mkdir -p "$(dirname "$f")" 2>/dev/null || {
    printf 'aib-release: 建不出鎖檔目錄:%s\n' "$(dirname "$f")" >&2
    return 3
  }
  {
    printf 'pid=%s\n' "$$"
    printf 'start=%s\n' "$start"
    printf 'start_epoch=%s\n' "$start_epoch"
    printf 'since=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'epoch=%s\n' "$(date -u '+%s')"
    printf 'max_sec=%s\n' "${AIB_RELEASE_FREEZE_MAX_SEC:-2700}"
    printf 'version=%s\n' "${ver:-(未記)}"
    printf 'repo=%s\n' "$(pwd -P)"
  } > "$f" || {
    printf 'aib-release: 鎖檔寫不進 %s —— 寫不下來就不放行\n' "$f" >&2
    return 3
  }
  # 寫完讀回:push rc=0 不是落檔證據,鎖也一樣。
  state="$(freeze_lock_state)"; rc=$?
  if [ "$rc" -ne 0 ] || [ "${state%% *}" != "HELD" ]; then
    printf 'aib-release: 鎖寫完讀回不是 HELD(%s)—— 不放行\n' "$state" >&2
    return 3
  fi
  return 0
}

# 解鎖。只解自己的:別人的鎖不動(奪鎖比忘記解鎖更難查)。
freeze_lock_release() {
  local f pid
  f="$(freeze_lock_path)" || return 3
  [ -e "$f" ] || return 0
  pid="$(freeze_lock_field "$f" pid)" || return 3
  if [ "$pid" = "$$" ]; then
    rm -f "$f" || return 3
    return 0
  fi
  return 1
}

# ── 直接執行時的 CLI(被 source 時不生效)────────────────────────────
# ★ 乙(root2 08-28):**不吵的 fail-closed 與不擋的 fail-open 一樣壞,只是壞在另一邊。**
#   一個擋住所有人的閘必須同時給出一條人工解鎖路徑,否則畫面上只有一句 git 錯誤。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-status}" in
    status)
      # ★ B23-c(裁定 RULING-B23 §八/§八之二):作用域跟 cwd 走,不跟腳本位置。
      #   站在別棵 git 樹裡問,得到的是**那棵樹的**正確答案(多半 NONE)—— 失效值剛好是放行值。
      #   `--expect-repo <path>` 讓呼叫者把「我以為我在問哪個 repo」交給機器比:
      #   對不上 ⇒ fail-closed 明說,**兩個路徑都印**(你以為的 / 實際的),而不是回別棵樹的答案。
      #   ★ 不帶旗標 ⇒ 行為逐字不變:既有呼叫者不能一夜全紅(約定要靠人記得,機制不用)。
      shift
      _expect=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --expect-repo)
            [ -n "${2:-}" ] || { echo "status --expect-repo 缺值" >&2; exit 2; }
            _expect="$2"; shift ;;
          *) echo "status 不認得的參數:$1" >&2; exit 2 ;;
        esac
        shift
      done
      # ★ 第四格:站在非 git 目錄仍回「解不出鎖檔路徑」—— 這一格今天是對的,放在比對之前,不准被弄壞。
      _f="$(freeze_lock_path)" || { echo "解不出鎖檔路徑" >&2; exit 3; }
      if [ -n "$_expect" ]; then
        _ecommon="$(git -C "$_expect" rev-parse --git-common-dir 2>/dev/null)" || _ecommon=""
        if [ -z "$_ecommon" ]; then
          echo "解不出 --expect-repo 的 git 目錄:$_expect —— 判不出來,不放行" >&2
          exit 3
        fi
        case "$_ecommon" in
          /*) ;;
          *)  _ecommon="$(cd "$_expect" && cd "$_ecommon" && pwd -P)" || {
                echo "解不出 --expect-repo 的 git 目錄(相對路徑轉不出絕對):$_expect" >&2; exit 3; } ;;
        esac
        _ef="$_ecommon/aib-release.lock"
        # 比 realpath:symlink 兩端字面不同而是同一個檔,不能誤判成不同 repo。
        _ra="$(cd "$(dirname "$_f")" 2>/dev/null && pwd -P)/$(basename "$_f")"
        _re="$(cd "$(dirname "$_ef")" 2>/dev/null && pwd -P)/$(basename "$_ef")"
        if [ "$_ra" != "$_re" ]; then
          echo "鎖檔(你以為的):$_ef"
          echo "鎖檔(實際的)  :$_f"
          # ★ B23-d:rc≠0 的出口整份輸出不得含四個放行/持有字面 —— 掃字面的人(grep -q)會把 fail-closed 讀成放行。
          echo "狀態:MISMATCH 你站的樹不是 --expect-repo 那個 repo —— 這裡量到的狀態是別棵樹的答案,不算"
          echo "rc  :3(fail-closed;作用域跟 cwd 走,不跟腳本位置:cd 進那棵樹或它的任一 worktree 再問)"
          exit 3
        fi
      fi
      echo "鎖檔:$_f"
      _st="$(freeze_lock_state)"; _rc=$?
      echo "狀態:$_st"
      echo "rc  :$_rc(0=判定成功,3=判不出來=fail-closed)"
      [ -e "$_f" ] && { echo "--- 鎖檔內容 ---"; cat "$_f"; }
      exit "$_rc" ;;
    unlock)
      # 人工解鎖:要顯式帶 --force,而且會先把現況印出來。
      _f="$(freeze_lock_path)" || { echo "解不出鎖檔路徑" >&2; exit 3; }
      if [ "${2:-}" != "--force" ]; then
        echo "這會刪掉 ${_f}。" >&2
        echo "先確認持有者那個行程真的不在了(上面 status 會告訴你 pid 與啟動時間)。" >&2
        echo "確定要解:$0 unlock --force" >&2
        exit 2
      fi
      [ -e "$_f" ] || { echo "沒有鎖可解($_f 不存在)"; exit 0; }
      cat "$_f"
      rm -f "$_f" && echo "已解鎖:$_f"
      exit $? ;;
    *)
      echo "用法:$0 [status|unlock --force]" >&2
      exit 2 ;;
  esac
fi
