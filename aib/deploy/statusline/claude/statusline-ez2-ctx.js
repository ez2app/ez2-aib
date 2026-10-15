#!/usr/bin/env node
"use strict";
// Ez2 context 儀表 — Claude Code statusLine adapter
//
// 分級門檻與色碼**沿用 Ez2 AI 桌面殼的 context meter**,不另立第二真相源:
//   門檻  desktop-shell/src/chat-core/context-usage.js  THRESHOLDS={warn:50,hot:75,crit:95}
//   色碼  desktop-shell/src/app/components.css          .ctx-meter-row.ctx-{warn,hot,crit}
// 那邊改了,這裡要跟著改(終端無法做呼吸動畫,crit 改用粗體 + "!" 前綴代償)。
//
// 紅線:這支是狀態列,永遠 fail-open —— 任何例外都印一行可用文字,絕不讓狀態列消失。
require("./ez2-ctx-core.js").runStatusline({ engine: "claude", source: "claude_statusline" });
