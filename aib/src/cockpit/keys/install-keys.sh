#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.5 快鍵安裝器:把 keymap.json 的 root 層綁定裝上 tmux。
#
#   install-keys.sh [--install] [--dry-run] [--list]
#
# 三件事寫在這裡免得被改回去:
#
# 1. **綁定全在 M- 層,一個裸鍵都不綁。** root 表是全域的,中欄跑著真的
#    AI 對話;裸 0/o/m/? 被搶走,使用者就打不出那些字。E2.12 的裸 ?
#    與 E2.4 的裸 m 是同一條理由,這裡把它變成整組的紀律。
#
# 2. **顯示名隨平台變,綁定名永遠是 M-。** macOS 顯示 Option、其他顯示 Alt
#    (uname -s 判定,EZ2_MODIFIER_LABEL 可覆寫),但 tmux 只認 M-;
#    把 Option-o 寫進 bind-key 會讓整組快鍵靜默失效。
#
# 3. **內層 ai-hub 的 C-a 不由這裡綁。** hub 由 cockpit-upgrade.sh 建立時設定;
#    兩處各綁一次會互相覆蓋,而且誰贏取決於執行順序——那種 bug 沒人查得動。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
MESSAGING="$(cd "$COCKPIT/../messaging" && pwd)"

# ★★ 2026-08-23:**輸入驗證不得排在環境解析後面。**
# 病例(全套 @ a1e6b4e 抓到):這裡原本無條件 `. cockpit-env.sh`,而刀 A 讓它多了
# 一個硬相依;治具沒複製那個檔 → source 失敗 → 整支在白名單檢查**之前**就結束。
# 於是注入測試的第一句 `rc != 0` **通過了**(rc=2 是缺檔,不是被拒),
# 只有第二句「訊息裡有沒有『白名單』」抓到守衛根本沒跑。
# ★ 一個可以被環境問題搶先的安全檢查,不是安全檢查。
# 修法=本檔對 cockpit-env 的兩個相依都在本地解得出來,所以**先驗 keymap,再談環境**:
#   EZ2_COCKPIT_DIR 就是 $COCKPIT(逐字同值);EZ2_PY3 沒繼承到就退回 PATH 上的 python3。
# 真正會動 tmux 的那條路仍然要求環境解析成功(見下面 install 分支),
# 所以這一刀放寬的只有「唯讀模式」,沒有放寬任何會改變狀態的動作。
# ★ **無條件覆寫,不尊重繼承值**——與 cockpit-env.sh:11 逐字同行為。
#   我第一版寫成 `${EZ2_COCKPIT_DIR:-$COCKPIT}`,結果 test-keys-install 的
#   「含空白與單引號的路徑」那支翻紅:它注入一個帶單引號的 EZ2_COCKPIT_DIR,
#   而 cockpit-env 本來會把它蓋掉。尊重繼承值 = 悄悄改了一個既有行為。
export EZ2_COCKPIT_DIR="$COCKPIT"
AIB_KEYS_PY="${EZ2_PY3:-}"
if [ -z "$AIB_KEYS_PY" ]; then
  AIB_KEYS_PY="$(command -v python3 || true)"
fi
if [ -z "$AIB_KEYS_PY" ]; then
  echo "拒絕:找不到 python3,無法驗證 keymap(白名單檢查跑不了就不准往下走)" >&2
  exit 2
fi

# 環境解析延後到真的要動 tmux 時才做;失敗照舊 fail-closed(不吞)。
aib_keys_require_env() {
  . "$COCKPIT/cockpit-env.sh"
}

DRY="${AIB_DRY_RUN:-0}"
MODE="install"

for a in "$@"; do
  case "$a" in
    --install) MODE="install" ;;
    --list) MODE="list" ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# 從 spec 產出 (tmux_key, command) 逐行;路徑佔位在這裡填實
plan() {
  EZ2_KEYMAP="$HERE/keymap.json" EZ2_MSG_DIR="$MESSAGING" "$AIB_KEYS_PY" - <<'PY'
import json, os, shlex, sys

# 白名單:AIB 只需要這幾個 tmux 動詞。新增動詞要在此明示,
# 不得由 keymap 資料自由指定(資料不該能選擇要執行什麼)。
ALLOWED_VERBS = {"run-shell", "previous-window", "next-window",
                 "select-window", "display-menu", "display-popup",
                 "switch-client", "send-keys", "set-option", "if-shell"}

spec = json.load(open(os.environ["EZ2_KEYMAP"], encoding="utf-8"))
cockpit = os.environ["EZ2_COCKPIT_DIR"]
messaging = os.environ["EZ2_MSG_DIR"]
for b in spec["bindings"]:
    if b.get("scope") != "root":
        continue
    keys, cmds = b.get("tmux_keys", []), b.get("commands", [])
    if len(keys) != len(cmds):
        raise SystemExit("keymap %s: tmux_keys 與 commands 數量不符" % b["id"])
    for key, cmd in zip(keys, cmds):
        rendered = cmd.format(cockpit=cockpit, messaging=messaging)
        # 命令拆成 argv 再以 NUL 分隔輸出:安裝端直接餵給 tmux,
        # 不再經過 shell 字串重組(codex 審 2026-08-10 高1:原本走 eval,
        # keymap 內容或含特殊字元的 checkout 路徑即可構成命令注入)。
        argv = shlex.split(rendered)
        if not argv:
            raise SystemExit("keymap %s: 空命令" % b["id"])
        if argv[0] not in ALLOWED_VERBS:
            raise SystemExit("keymap %s: 不在白名單的 tmux 動詞 %r"
                             % (b["id"], argv[0]))
        if os.environ.get("EZ2_KEYS_NUL") == "1":
            # 安裝路徑:NUL 分隔 argv,shell 不重組字串
            sys.stdout.write(key + "\0" + "\0".join(argv) + "\0\0")
        else:
            # --list 是給人看的:維持既有 tab 格式(對外契約不因內部
            # 安全改造而變),白名單與 argv 檢查兩種模式都已跑過。
            print("%s\t%s" % (key, rendered))
PY
}

case "$MODE" in
  list)
    plan || exit 3
    ;;
  install)
    # plan 先落暫存檔並檢查真 exit code:process substitution 會吃掉
    # plan 的失敗(白名單被拒也照樣往下跑)——這正是「接 pipe 吃 exit code」
    # 的變體,由 tests/test-keys-install.py 抓到。
    PLAN_FILE="$(mktemp)"
    trap 'rm -f "$PLAN_FILE"' EXIT
    if ! EZ2_KEYS_NUL=1 plan > "$PLAN_FILE"; then
      echo "拒絕:keymap 計畫產生失敗(白名單或格式檢查未過)" >&2
      exit 3
    fi
    # ★ keymap 已驗過才輪到環境:真的要動 tmux(非 dry-run)之前才解析艙,
    #   解析失敗照舊硬中止——放行的只有唯讀輸出,不含任何狀態變更。
    if [ "$DRY" != "1" ]; then
      # 缺陷十九(09-05):真的要動 tmux 之前擋「暫存樹 / 暫存 state 無沙箱」的安裝。
      # 位置刻意在 plan 白名單檢查**之後**:--dry-run 的白名單治具(test-keys-install
      # 把本腳本單獨複製到 tmp 跑 --dry-run)因此**不相依**這支守衛檔。守衛檔缺席時
      # 具名 fail-closed(非 dry 一律拒),不是靜默 set -e 死在 source。
      if [ -r "$COCKPIT/install-guard.sh" ]; then
        . "$COCKPIT/install-guard.sh"
        aib_refuse_tmp_install install-keys.sh "$HERE" || exit $?
      else
        echo "install-keys.sh: 缺陷十九守衛檔缺席($COCKPIT/install-guard.sh);非 dry 安裝一律 fail-closed 拒裝" >&2
        exit 6
      fi
      aib_keys_require_env
    fi
    # NUL 分隔的 argv 串:key \0 arg1 \0 arg2 \0 \0
    while IFS= read -r -d $'\0' key; do
      [ -n "$key" ] || continue
      case "$key" in
        M-*) ;;
        *) echo "拒絕:非 M- 層綁定 $key" >&2; exit 3 ;;
      esac
      argv=()
      while IFS= read -r -d $'\0' part; do
        [ -n "$part" ] || break
        argv+=("$part")
      done
      if [ "$DRY" = "1" ]; then
        printf '+ tmux bind-key -n %s' "$key"
        printf ' %q' "${argv[@]}"
        printf '\n'
      else
        # 高2:綁定加 session guard——AIB 快鍵是 server-global,
        # 沒有 guard 會攔截使用者其他 tmux session 的同名按鍵。
        tmux bind-key -n "$key" if-shell -F \
          '#{==:#{@aib_cockpit},1}' "$(printf '%q ' "${argv[@]}")" \
          "send-keys $key"
      fi
    done < "$PLAN_FILE"
    ;;
esac
