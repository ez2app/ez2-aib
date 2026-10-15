#!/usr/bin/env node
"use strict";

// Antigravity CLI sends the same provider-observed context_window shape to a
// configured statusLine command. The shared core intentionally ignores email,
// plan_tier, and other identity fields so the gauge store contains no secrets.

// 2026-09-21 the maintainer：AIB 艙內隱藏底部「Model | Ez2 CTX」紅框列。
// 只藏畫面。百分比檔仍要寫，否則右欄在艙裡讀不到這一家。
const tmux = String(process.env.TMUX || "");
const hideStatusline = Boolean(
  process.env.EZ2_AIB_INSTANCE_HOME ||
  process.env.AIB_INSTANCE ||
  process.env.EZ2_AIB_INSTANCE ||
  process.env.EZ2_AIHUB_SOCKET ||
  /aihub-/.test(tmux) ||
  /^aib-/.test(String(process.env.EZ2_TMUX_SESSION || ""))
);

// 2026-10-10 the maintainer:「Codex 和 agy 席也要顯示,全部的 ai 席都要」—— 同 Claude 席:CTX 那一行插 @席位、
// 上面掛「📌 主項目 | 完成%」;艙內量得到百分比就照印(同 Claude 09-30「量到才印」)。
// 席位碼與 📌 行問 aib now --statusline(seat_now.py:窗 id → 席位檔 → 席位碼),問不到就兩樣都不畫。
function seatNow() {
  if (!process.env.TMUX_PANE) return { code: "", now: "", logo: "" };
  const { execFileSync } = require("child_process");
  const fs = require("fs");
  const path = require("path");
  const root = process.env.EZ2_AIB_ROOT || "";
  const py = root && path.join(root, "src", "cockpit", "seat_now.py");
  let out = "";
  try {
    out = py && fs.existsSync(py)
      ? execFileSync("/usr/bin/python3", [py, "--statusline"], { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] })
      : execFileSync("aib", ["now", "--statusline"], { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] });
  } catch (_) {
    return { code: "", now: "", logo: "" };
  }
  const [code, now, logo] = String(out).replace(/\n$/, "").split("\x1f");
  return /^[0-9a-f]{5}$/.test(code || "") ? { code, now: now || "", logo: logo || "" } : { code: "", now: "", logo: "" };
}

require("./ez2-ctx-core.js").runStatusline({
  engine: "agy",
  source: "agy_statusline",
  // The provider payload may include account fields. Only persist the
  // normalized, privacy-safe gauge record, never the raw status-line input.
  snapshot: false,
  quiet: (record) => hideStatusline && record.pct == null,
  decorate: (text, record) => {
    const seat = seatNow();
    if (seat.code && text) text = text.replace(" | ", " | @" + seat.code + " | ");
    // 右欄同一個 AI 標誌(the maintainer 10-10);空兩格:Nerd Font 圖示畫得比一格寬,會蓋掉一格空白
    if (seat.logo && text) text = seat.logo + "  " + text;
    // 📌 那一行不畫在這裡:agy 的狀態列只在有事件時重畫、不支援定時重跑,閒著時會停在舊內容 ⇒
    // 改畫在 hub 窗底部邊框(seat_now.py --border --now-only,tmux 每幾秒重畫)(the maintainer 10-10「agy 也加底部那一行」)
    return text;
  }
});
