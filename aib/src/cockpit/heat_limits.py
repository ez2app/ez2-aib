# SPDX-License-Identifier: Apache-2.0
"""熱門檻的單一來源(11-08 Andy 准、f1fae 有條件同意;PROPOSAL-12 定案 C 同一組數)。

cooldown(右欄降溫四級 / heavy-work-nogo / 開席擋不擋)與 host_lease(short 隊發不發)都讀這裡,
改一個數兩邊一起變。只放數字,不放邏輯。

★ 熱節流 = kernel_task 的 %CPU(machine_status.kernel_task_cpu 兩取樣那一支),不是溫度。
  11-07 夜裡 f457 推送時四個艙一直在 20–47% 之間,舊門檻 20 讓 hot 幾乎常亮(nogo、通知艦長、擋開席),
  一個永遠亮著的警告不帶資訊 ⇒ hot 提到 40;20–39 留成 warm:只記(cooldown.log 每 10 分鐘最多一列)、不擋,
  一週後看紀錄再定。
★ 真正保護機器的動作不放寬:收自動化瀏覽器(reap)仍然只看 CPU 晶粒溫度 ≥ CPU_CRIT_C。
"""

THROTTLE_WARM_PCT = 20.0    # ≥ 這個記一筆 warm(不擋)
THROTTLE_HOT_PCT = 40.0     # ≥ 這個算 hot:nogo 旗標、開席擋、short 隊不發
CPU_CRIT_C = 90.0           # CPU 晶粒溫度 critical:收自動化瀏覽器、short 隊不發
SHORT_LOAD1_MAX = 12.0      # load1 > 這個,short 隊不發
WARM_LOG_EVERY_S = 600      # warm 紀錄節流:同一艙每 10 分鐘最多一列
