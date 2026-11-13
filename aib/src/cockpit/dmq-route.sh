# shellcheck shell=bash
# B1 S3b(1ced7 11-14):`aib dm-queue <子命令>` 先試 Rust aib-dmq 的那一段。只由 cockpit/aib 的 dm-queue 分支 source。
# - 只有 AIB_DMQ_RUST(逗號清單)列到的 take / close / wait / reopen 才試;預設全關(寫入片對拍綠夠久才預設開,PLAN-b1 r3)。
# - 執行檔:AIB_DMQ_BIN,沒設就 ~/.ez2-aib/bin/aib-dmq;不在或不能執行 ⇒ stderr 一行,這次走 Python。
# - aib-dmq 回 75 = 沒接、什麼都沒動(全球信要送回條 / 沒帶 --seat / 參數要 argparse 判)⇒ 回呼叫端,照舊交給 Python。
# - 其他結束碼 = Rust 已處理 ⇒ 用那個碼結束,不再跑 Python(信可能已經改了,重跑會重做一次)。
# - commit 證據查哪個倉:跟 dm_queue.py 的 _HERE.parents[1] 同一個(cockpit 往上兩層)。
aib_dmq_route() {   # $1=cockpit 資料夾,其餘 = dm-queue 之後的參數;沒接就 return 0
  local here="$1" sub="${2:-}" bin rc
  shift
  case "$sub" in
    take|close|wait|reopen) ;;
    *) return 0 ;;
  esac
  case ",${AIB_DMQ_RUST:-}," in
    *",$sub,"*) ;;
    *) return 0 ;;
  esac
  bin="${AIB_DMQ_BIN:-${HOME:-}/.ez2-aib/bin/aib-dmq}"
  if [ ! -f "$bin" ] || [ ! -x "$bin" ]; then
    echo "aib: AIB_DMQ_RUST lists $sub but $bin is not an executable file; using the Python path" >&2
    return 0
  fi
  rc=0
  AIB_DMQ_REPO="$here/../.." "$bin" "$@" || rc=$?
  if [ "$rc" -eq 75 ]; then
    return 0
  fi
  exit "$rc"
}
