#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 刀 A · 艙解析單源(SPEC-cabin-resolution-single-source-0822.md §三)。
#
# ★ 這支檔是**艙名字面的唯一合法住址**(釘子 R8 的具名例外)。任何人再寫一次
#   `aihub-$N` / `aib-$N` / `= "main"`,R8 掃描當場紅——那正是本刀要消滅的東西:
#   同一個問題有第二個答案時,兩個答案各自都對,合起來就是**位置決定顏色**。
#
# 匯出的對外契約(釘子釘的是這四個值,不是檔名):
#     EZ2_AIB_INSTANCE       艙號        main | <N>
#     EZ2_TMUX_SESSION       外層 session aib-main | aib-<N>
#     EZ2_AIHUB_SOCKET       內層 socket  aihub | aihub-<N>
#     EZ2_AIB_INSTANCE_HOME  狀態家       <狀態根> | <狀態根>/instances/<N>
# 附帶給消費者的述詞(**不要再自己比對 "main"**):
#     EZ2_AIB_CABIN_NAMED    1=具名艙 0=主艙
#
# 優先序:
#     instance : EZ2_AIB_INSTANCE > 由 EZ2_AIHUB_SOCKET 反推 > main
#     session  : EZ2_TMUX_SESSION > 由 instance 導出
#     hub      : EZ2_AIHUB_SOCKET > 由 instance 導出
#     state    : EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib
#                (a seat 08-23 02:3x 題五裁決;與 seats_dir_lib.state_root() 同式)
#
# fail-closed:instance 與 hub 都給且不一致 → 非零並**同時點名兩個值**,不猜。
#   ★ 失效形狀是「靜默取一」:取哪一個都會有一半的呼叫者是對的,所以沒有人會回報。
#
# 輸入驗證(2026-08-27,a seat 派工第2件):**排在環境解析之前**。
#   艙號與 socket 名只准 `a-z` `0-9` `-`;空值與非法字元一律 rc=2 並**點名該值**。
#   ★ 為什麼「空」也要非零:`[ -n ... ]` 把空字串當成「沒給」,於是
#     `EZ2_AIB_INSTANCE=""` 會安靜地退回主艙——而主艙不是回退來源,是衝突,
#     退成功了不會叫。沒給要 `unset`;留一個空字串是**別的意思**。
#   ★ 為什麼驗證要排在最前面:可以被環境問題搶先的安全檢查不是安全檢查
#     (08-23 install-keys.sh 實例:先 source 環境才驗白名單,缺檔讓它在守衛
#     之前結束,而 `assertNotEqual(rc, 0)` 被那個 rc=2 滿足)。
#   ★ 字元集**逐字列出不用 a-z 範圍**:範圍的解讀跟 locale 走,而一個會隨
#     locale 改變答案的驗證器,失效時給的正是「通過」。
#
# 三個 seam(design 釘子提出,root 08-23 02:46 裁定沿用;**只影響本檔,不進產品路徑**):
#   AIB_FAKE_NEW_INSTANCE=<N>  模擬 `new`:不看 socket 也不看 EZ2_AIB_INSTANCE
#   AIB_NO_REVERSE_DERIVE=1    突變一:拿掉反推
#   AIB_CONFLICT_SILENT=1      突變二:不一致時靜默取一

# 艙名字面——全庫只有這五行(改名只改這裡)
# ★ 這個數字與底下五行由 tests/test-cabin-resolution.py 逐字比對:改字面必須同時改測試。
#   (原本寫「四行」而底下是五行——會腐爛的數字要嘛被機器比對,要嘛不要寫。)
AIB_CABIN_PRIMARY="main"
_AIB_CABIN_SESSION_PRIMARY="aib-main"
_AIB_CABIN_SESSION_PREFIX="aib-"
_AIB_CABIN_HUB_PRIMARY="aihub"
_AIB_CABIN_HUB_PREFIX="aihub-"

# 輸入驗證器。$1=來源標籤(印給人看的名字) $2=值;rc 0=好 2=空或非法。
# 兩種拒絕分開講:「空」與「有非法字元」處置一樣而成因完全不同,合成一句話會
# 讓人去猜是哪一種。
aib_cabin_check_name() {
  case "${2-}" in
    "")
      printf 'aib: 艙解析輸入為空:%s(空字串不是「沒給」——沒給請 unset。留著空字串會安靜地退回主艙)\n' \
        "${1-<未具名>}" >&2
      return 2 ;;
    *[!abcdefghijklmnopqrstuvwxyz0123456789-]*)
      printf 'aib: 艙解析輸入含非法字元:%s=%s(只准小寫 a-z、數字 0-9 與 -)\n' \
        "${1-<未具名>}" "$2" >&2
      return 2 ;;
  esac
  return 0
}

# 艙 UUID 發號(DESIGN-cabin-uuid-20260826 §二;the maintainer 2026-08-26 23:2x 定案)
# 格式 YYYYMMDD-HHMMSS-xxxx:前 15 字=台北出生時間(秒級),末 4 字=hex 亂數。
# ★ 亂數不是裝飾:同秒開三艙是常態(規格 §五 實跑收據),純時間戳那一跑就發出三個同號。
# ★ 每一段都自檢後才輸出——發號失敗要回非零,不能回一個「看起來像 id」的東西。
aib_cabin_new_id() {
  local stamp rand
  stamp="$(TZ=Asia/Taipei date +%Y%m%d-%H%M%S)" || return 1
  case "$stamp" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) return 1 ;;
  esac
  rand="$(LC_ALL=C od -An -N2 -tx1 /dev/urandom | tr -d ' \n')" || return 1
  [ "${#rand}" -eq 4 ] || return 1
  case "$rand" in *[!0-9a-f]*) return 1 ;; esac
  printf '%s-%s\n' "$stamp" "$rand"
}

# 判別式(§二):新艙 id **必含剛好兩個連字號**;舊流水號(純數字)與 main 永遠不含。
# ★ 判連字號個數,不判長度——長度會隨格式演進而腐爛,而「含兩個 -」是格式自己的性質。
aib_cabin_is_new_id() {
  case "${1-}" in
    *-*-*-*) return 1 ;;
    *-*-*)   return 0 ;;
    *)       return 1 ;;
  esac
}

# 主艙判定(工單 AQ-h):解析器擁有 ``AIB_CABIN_PRIMARY``,呼叫端不得再讀那個常數。
# rc 0=主艙 1=不是。空字串不是主艙。
aib_cabin_is_primary() {
  [ -n "${1-}" ] && [ "$1" = "$AIB_CABIN_PRIMARY" ]
}

aib_cabin_session_name() {   # $1=instance
  aib_cabin_check_name "instance" "${1-}" || return 2
  if [ "$1" = "$AIB_CABIN_PRIMARY" ]; then
    printf '%s\n' "$_AIB_CABIN_SESSION_PRIMARY"
  else
    printf '%s%s\n' "$_AIB_CABIN_SESSION_PREFIX" "$1"
  fi
}

aib_cabin_hub_name() {       # $1=instance
  aib_cabin_check_name "instance" "${1-}" || return 2
  if [ "$1" = "$AIB_CABIN_PRIMARY" ]; then
    printf '%s\n' "$_AIB_CABIN_HUB_PRIMARY"
  else
    printf '%s%s\n' "$_AIB_CABIN_HUB_PREFIX" "$1"
  fi
}

aib_cabin_instance_from_hub() {   # $1=hub;解不出來回非零(**不猜**)
  aib_cabin_check_name "hub" "${1-}" || return 2
  case "$1" in
    "$_AIB_CABIN_HUB_PRIMARY") printf '%s\n' "$AIB_CABIN_PRIMARY" ;;
    "$_AIB_CABIN_HUB_PREFIX"?*) printf '%s\n' "${1#$_AIB_CABIN_HUB_PREFIX}" ;;
    *) return 1 ;;
  esac
}

# 保留一個新艙(紅10,2026-08-27)。**唯一性的保證面在這裡,不在發號器。**
#
# 裸 aib_cabin_new_id 是 15 字時戳 + 16 bit 亂數。同秒 40 連發的生日碰撞約
# 1.18%/輪 —— 那**不是缺陷,是產生器的契約**;把「40 個一定全異」斷言在它身上,
# 那條臂天生會紅,而紅的頻率剛好低到會被當成別的東西。
# 真正保證唯一的是這裡:`mkdir` 原子佔位(同一個名字只有一個行程建得起來)
# + 撞到就**重生一個**(不是 +1;+1 會走到別人正在用的號上)。
#
# 契約:$1 = instances 根。**只用 stdout 回傳,不設任何跨檔全域變數**——
#   成功回 0 且印出保留到的 id;失敗回非零且印出**出事的那個路徑**,
#   讓呼叫端的錯誤訊息仍然點得出是哪一個路徑(訊息逐字不變)。
# ★ 第一版是用全域變數回傳的,被 tests/test-cabin-resolution.py 的
#   `test_no_constant_leaks_outside_resolver` 當場擋下 —— 那支釘子是對的:
#   呼叫端一旦要讀 resolver 擁有的識別字,兩個檔就綁在一起了,
#   而「只是回傳值」是我當時給自己的藉口,不是一個結構上的差別。
aib_cabin_reserve() {
  local root="$1" n tries=0
  n="$(aib_cabin_new_id)" || { printf '%s\n' "$root"; return 1; }
  while :; do
    if mkdir "$root/$n" 2>/dev/null; then
      # 保留目錄只是三個命名空間之一。同名的外層 session 或具名 hub 也必須讓這個
      # 候選作廢,否則 `new` 會靜默接管一個舊 AI。
      if env -u TMUX tmux has-session -t "$(aib_cabin_session_name "$n")" 2>/dev/null || \
         env -u TMUX tmux -L "$(aib_cabin_hub_name "$n")" list-sessions >/dev/null 2>&1; then
        if ! rmdir "$root/$n" 2>/dev/null; then
          printf '%s\n' "$root/$n"; return 1
        fi
      else
        printf '%s\n' "$n"; return 0
      fi
    elif [ ! -e "$root/$n" ] && [ ! -L "$root/$n" ]; then
      # mkdir 失敗而路徑又不存在 = 不是撞號,是環境壞了。出聲,不要繼續轉。
      printf '%s\n' "$root/$n"; return 1
    fi
    tries=$((tries + 1))
    if [ "$tries" -ge 64 ]; then
      # 16 bit 亂數同秒撞 64 次代表發號器壞了,那時要出聲不是繼續轉
      # (規格 §七-1「零重試死循環」)。
      printf '%s\n' "$root/$n"; return 1
    fi
    n="$(aib_cabin_new_id)" || { printf '%s\n' "$root"; return 1; }
  done
}

# 10-25(@74e8b,@abb89 裁):開席第四參數指名一艙時,先對到「真的有這一艙」再往下走。
#   事故:`aib claude safe dev 6ddf --again`(只給艙號末 4 碼)⇒ 當成艙名「6ddf」,ensure 因缺權杖 rc=4 失敗,
#   但開席碼表已建 instances/6ddf/ ⇒ 艙號 6ddf 對到兩個艙,全艙 aib dm 6ddf/… 報 ambiguous-cabin。
#   規則:有 instances/<名>/instance-token ⇒ 照名字;是 4 碼 hex ⇒ 找末 4 碼相同的新式艙,剛好一個就換成那艙;
#   其他(對不到 / 多個 / 指名卻沒權杖)⇒ 拒絕,什麼夾都不建。
# 印解析後的艙名;rc 2 = 拒絕(原因印 stderr)。$1=輸入 $2=instances 根 $3=1 只解析不拒(dry-run 用)
aib_cabin_pick_named() {
  local want="${1-}" root="${2-}" soft="${3-0}" d name hits=() n
  aib_cabin_check_name "instance" "$want" || return 2
  if [ -f "$root/$want/instance-token" ] && [ ! -L "$root/$want/instance-token" ]; then
    printf '%s\n' "$want"; return 0
  fi
  case "$want" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f])
      for d in "$root"/*-"$want"; do
        [ -d "$d" ] && [ ! -L "$d" ] && [ -f "$d/instance-token" ] && [ ! -L "$d/instance-token" ] || continue
        name="${d##*/}"
        aib_cabin_is_new_id "$name" && hits+=("$name")
      done ;;
  esac
  n="${#hits[@]}"
  if [ "$n" -eq 1 ]; then
    printf 'aib: 艙號 %s ⇒ %s\n' "$want" "${hits[0]}" >&2
    printf '%s\n' "${hits[0]}"; return 0
  fi
  if [ "$soft" = "1" ]; then printf '%s\n' "$want"; return 0; fi
  if [ "$n" -gt 1 ]; then
    printf 'aib: 艙號 %s 對到 %s 個艙(%s),不猜;請寫完整艙名\n' "$want" "$n" "${hits[*]}" >&2
  else
    printf 'aib: 找不到艙 %s(%s 底下沒有這一艙或它的權杖);要開新艙請用 new,要看有哪些艙:ls %s\n' "$want" "$root" "$root" >&2
  fi
  return 2
}

aib_cabin_state_root() {     # 題五裁決的三層,與 seats_dir_lib.state_root() 同式
  printf '%s\n' "${EZ2_AIB_STATE_ROOT:-${EZ2_AIB_HOME:-$HOME/.ez2-aib}}"
}

aib_cabin_home() {           # $1=instance $2=狀態根
  aib_cabin_check_name "instance" "${1-}" || return 2
  if [ "$1" = "$AIB_CABIN_PRIMARY" ]; then
    printf '%s\n' "$2"
  else
    printf '%s/instances/%s\n' "$2" "$1"
  fi
}

# 一次算完四元組並匯出。rc: 0=好 2=不一致(fail-closed)
aib_cabin_resolve() {
  local inst="" hub="${EZ2_AIHUB_SOCKET-}" from_hub="" root=""
  local session_name="" hub_name="" home_dir=""

  # ── 輸入驗證,排在任何解析/反推/衝突判斷之前 ──────────────────────────
  # 用 ${VAR+set} 而不是 -n:要分得出「沒設」與「設成空字串」。前者是合法的
  # 「沒給」,後者是**有人給了一個不能用的值**,而今天它會退回主艙且不出聲。
  if [ -n "${EZ2_AIB_INSTANCE+set}" ]; then
    aib_cabin_check_name "EZ2_AIB_INSTANCE" "$EZ2_AIB_INSTANCE" || return 2
  fi
  if [ -n "${EZ2_AIHUB_SOCKET+set}" ]; then
    aib_cabin_check_name "EZ2_AIHUB_SOCKET" "$EZ2_AIHUB_SOCKET" || return 2
  fi
  # AIB_FAKE_NEW_INSTANCE 是 seam 而不是艙輸入:它的「空=關閉」語意是既有的
  # (`aib:220` 的 `${AIB_FAKE_NEW_INSTANCE:-2}` 就這樣讀),所以只驗非空的值。
  # ★ 重寫一行既有邏輯前先問那一行的語意是不是刻意的——這一行是。
  if [ -n "${AIB_FAKE_NEW_INSTANCE-}" ]; then
    aib_cabin_check_name "AIB_FAKE_NEW_INSTANCE" "$AIB_FAKE_NEW_INSTANCE" || return 2
  fi

  if [ -n "${AIB_FAKE_NEW_INSTANCE-}" ]; then
    # `new`:啟動器已 scrub 掉繼承來的定位變數,所以帶著別艙的 socket 也不得被帶偏
    inst="$AIB_FAKE_NEW_INSTANCE"
    hub=""
  else
    if [ -n "$hub" ] && [ "${AIB_NO_REVERSE_DERIVE-}" != "1" ]; then
      from_hub="$(aib_cabin_instance_from_hub "$hub" 2>/dev/null || true)"
    fi
    if [ -n "${EZ2_AIB_INSTANCE-}" ]; then
      inst="$EZ2_AIB_INSTANCE"
      if [ -n "$from_hub" ] && [ "$from_hub" != "$inst" ]; then
        if [ "${AIB_CONFLICT_SILENT-}" = "1" ]; then
          : # 突變二:靜默取 EZ2_AIB_INSTANCE —— 這正是本刀要禁止的行為
        else
          # ★ 兩個值都要印出來:只說「不一致」的訊息會讓人去猜是哪一邊錯
          printf 'aib: 艙解析衝突:EZ2_AIB_INSTANCE=%s 與 EZ2_AIHUB_SOCKET=%s 指向不同的艙(%s vs %s)。不猜,請只留一個。\n' \
            "$inst" "$hub" "$inst" "$from_hub" >&2
          return 2
        fi
      fi
    elif [ -n "$from_hub" ]; then
      inst="$from_hub"
    else
      inst="$AIB_CABIN_PRIMARY"
    fi
  fi

  root="$(aib_cabin_state_root)"
  # 三個導出值先落在區域變數上再匯出:`export X="${Y:-$(f)}"` 會把 f 的 rc 丟掉,
  # 而丟掉的那個 rc 正是本次要加的守衛。宣告與賦值分兩行,同理(`local x=$(f)`
  # 的 rc 是 local 的 rc,恆為 0)。
  session_name="$(aib_cabin_session_name "$inst")" || return 2
  hub_name="$(aib_cabin_hub_name "$inst")" || return 2
  home_dir="$(aib_cabin_home "$inst" "$root")" || return 2
  export EZ2_AIB_INSTANCE="$inst"
  export EZ2_TMUX_SESSION="${EZ2_TMUX_SESSION:-$session_name}"
  export EZ2_AIHUB_SOCKET="${hub:-$hub_name}"
  export EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-$home_dir}"
  if [ "$inst" = "$AIB_CABIN_PRIMARY" ]; then
    export EZ2_AIB_CABIN_NAMED=0
  else
    export EZ2_AIB_CABIN_NAMED=1
  fi
}

# ---- 艙導向環境變數:單源清單 ----------------------------------------------
#
# ★★★ 這筆債的名字是**兩套優先序**,不是「清單少六個」(艦長 08-28 17:47 改寫)。
#   `seats_dir_lib.py:70` 逐字 `hub or AIB_DM_SOCKET or EZ2_AIHUB_SOCKET`
#   —— 七個消費者解「這是哪一個艙」時**先讀 AIB_DM_SOCKET**,而本檔對 `AIB_DM` 命中 0。
#
# ★★ 同一型出現在**三個位置**,而三份互不同步(qa3 08-28 要求寫進交付:
#   「同型第二次出現,值得的不是修這一支,是問這一型還有第三個位置沒有」):
#     (1) `tests/aib_cabin_wash.py` 用 `EZ2_*` 正則猜 —— 結構上**看不見** `AIB_DM_*`
#     (2) 消費者各自讀原始 env(12 個函式、12 個檔)
#     (3) `src/cockpit/aib` 換艙時**手列**要 unset 哪些 —— 漏 `AIB_DM_SOCKET` /
#         `AIB_DM_TMUX` / `EZ2_AIB_SEAT_DISPLAY` / `EZ2_ROUTE_REGISTRY`
#   本檔成為那唯一一份;(1) 改成跑本函式,(3) 改成呼叫 `aib_cabin_env_wash_scoped`。
#
# ★ 為什麼清單寫在這裡不算「手列」:原判準禁的是**清單活在考卷裡**(會與產品漂移)。
#   寫在單源、而且**被單源自己的洗函式用掉**,拿掉一個名字就有東西壞掉 —— 那才是承重。
#   判準 `qa3/criteria-debt-wash-0828`(W8)+ 艦長 08-28 17:44 派工 + qa3 17:45/17:47 裁。
# 09-26 a seat 補 11 個消費者有讀、單源沒列的名字(test-cabin-single-source-names / test-cabin-read-set 缺口;codex 列 10 個 + FishMac 兩臂多量到 1 個),分類:
#   換艙要清(只進全集):AIB_DM_MCP_DRY、AIB_CODEX_QUOTA_MENU(==1 的行為開關,漏進艙會靜默改行為);
#     EZ2_PANE_W/H、EZ2_ROW_OFFSET、EZ2_H_OFFSET(pane-*.sh 每個 pane 自己 export 的幾何值,不得跨席繼承)。
#   不隨艙變(全集 + roots):AIB_CODEXBOT_SECRETS_FILE、AIB_TMUX_BIN、AIB_CODEXBOT_SILENCE_TIMEOUT、AIB_CODEXBOT_SERVER_DIR、
#     EZ2_AIB_INSTANCES_ROOT(instances 根覆寫,aib_dm_mcp.py:223 / codexbot_threads.py:94;codex 的 10 個名單漏了它,FishMac 兩臂量到)
#     (路徑/二進位/調校覆寫,與 EZ2_AIB_SECRETS_DIR 同性質;換艙清掉=丟使用者設定)。
# 09-26 a seat(N1 (a) 帶出,艦長 22:57 對照):AIB_COMPOSER_NOTICE_DIR —— composer_notice.notice_dir() 一直讀它
#   (基底寫成 `get(...) or …` 掃描器看不見;N1 改成先取值再判,才被量到)。性質=通知落點的路徑覆寫,不隨艙變 ⇒ 全集 + roots。
#   登記未動:tmux 二進位有兩個名字(殼 AIB_TMUX_BIN、Python EZ2_AIB_TMUX_BIN),掃描器只看得到前者。
aib_cabin_env_names() {
  printf '%s\n' \
    EZ2_AIB_INSTANCE EZ2_TMUX_SESSION EZ2_AIHUB_SOCKET EZ2_AIB_INSTANCE_HOME \
    EZ2_AIB_CABIN_NAMED EZ2_AIB_INSTANCE_TOKEN EZ2_COCKPIT_STATE_DIR \
    EZ2_TASKS_DIR EZ2_ROUTE_REGISTRY EZ2_AIB_SEAT_DISPLAY \
    AIB_DM_SOCKET AIB_DM_TMUX \
    AIB_CODEXBOT_THREAD_ID AIB_CODEXBOT_ENDPOINT AIB_CODEXAPP_THREAD_ID AIB_CODEXAPP_ENDPOINT \
    EZ2_ROUTE_CONTRACT_SPEC EZ2_EZ2AI_REPO \
    EZ2_AIB_AI_VIEW EZ2_AIB_PANES \
    EZ2_AIB_HOME EZ2_AIB_STATE_ROOT EZ2_AIB_SECRETS_DIR EZ2_AIB_DESKTOP_DIR \
    EZ2_SEATS_DIR \
    AIB_DM_MCP_DRY AIB_CODEX_QUOTA_MENU \
    EZ2_PANE_W EZ2_PANE_H EZ2_ROW_OFFSET EZ2_H_OFFSET \
    AIB_CODEXBOT_SECRETS_FILE AIB_TMUX_BIN AIB_CODEXBOT_SILENCE_TIMEOUT AIB_CODEXBOT_SERVER_DIR \
    EZ2_AIB_INSTANCES_ROOT AIB_COMPOSER_NOTICE_DIR
}

# 狀態根**輸入**(不是艙導出值):`aib_cabin_state_root` 讀它們(`:167`),
# 而它們**不隨艙變**。換艙時清掉它們會把狀態根打回 `~/.ez2-aib` —— 那是行為變更,
# 不在本刀範圍。`EZ2_AIB_SECRETS_DIR` / `EZ2_AIB_DESKTOP_DIR` 同理(覆寫路徑,不隨艙走)。
# ⇒ 換艙用的子集 = 全集**減掉**這組,由過濾導出,不另列一份。
# `EZ2_EZ2AI_REPO`:cockpit-env.sh:203 從它派生 EZ2_ROUTE_CONTRACT_SPEC(route_register.default_contract_spec 也讀它)。
# 它是使用者可覆寫的**輸入**、不隨艙變,與狀態根同性質:進全集(開艙要洗),不進換艙要清的子集
# (清了會把使用者指的 ez2-ai 倉打回 ~/ez2-ai,那是行為變更)。a seat 09-26 審查一般 1。
aib_cabin_env_roots() {
  printf '%s\n' EZ2_AIB_HOME EZ2_AIB_STATE_ROOT EZ2_AIB_SECRETS_DIR EZ2_AIB_DESKTOP_DIR EZ2_EZ2AI_REPO \
    AIB_CODEXBOT_SECRETS_FILE AIB_TMUX_BIN AIB_CODEXBOT_SILENCE_TIMEOUT AIB_CODEXBOT_SERVER_DIR EZ2_AIB_INSTANCES_ROOT \
    AIB_COMPOSER_NOTICE_DIR
}

# 換艙要清的子集:全集 − 狀態根輸入。**由過濾導出,不是第二份名單。**
aib_cabin_env_names_scoped() {
  local n r skip
  for n in $(aib_cabin_env_names); do
    skip=0
    for r in $(aib_cabin_env_roots); do
      [ "$n" = "$r" ] && skip=1
    done
    [ "$skip" = "0" ] && printf '%s\n' "$n"
  done
  return 0
}

aib_cabin_env_wash_scoped() {
  local n
  for n in $(aib_cabin_env_names_scoped); do
    unset "$n" 2>/dev/null || true
  done
  return 0
}

# 直接執行時當查詢器用(python 消費者走這條,不在別的語言再寫第二份規則):
#   cabin_resolve.sh --hub <inst> | --session <inst> | --print
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1-}" in
    --env-names)        aib_cabin_env_names || exit $? ;;
    --env-names-scoped) aib_cabin_env_names_scoped || exit $? ;;
    --hub)     aib_cabin_hub_name "${2-$AIB_CABIN_PRIMARY}" || exit $? ;;
    --session) aib_cabin_session_name "${2-$AIB_CABIN_PRIMARY}" || exit $? ;;
    --print)
      aib_cabin_resolve || exit $?
      printf 'EZ2_AIB_INSTANCE=%s\nEZ2_TMUX_SESSION=%s\nEZ2_AIHUB_SOCKET=%s\nEZ2_AIB_INSTANCE_HOME=%s\n' \
        "$EZ2_AIB_INSTANCE" "$EZ2_TMUX_SESSION" "$EZ2_AIHUB_SOCKET" "$EZ2_AIB_INSTANCE_HOME" ;;
    *) echo "usage: cabin_resolve.sh --hub <inst> | --session <inst> | --print | --env-names | --env-names-scoped" >&2; exit 2 ;;
  esac
fi
