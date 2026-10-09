#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// F 寫回第二大腦(L6-01)——note-format 薄包裝。
//
//   node src/memory/note_format_runner.js <modules_dir>   # 輸入 JSON 走 stdin,輸出 JSON 走 stdout
//
// ## 這支只做一件事
//
// 把 AIB 這側算好的欄位交給 **ez2-ai 的 note-format 正本**(`shared/ez2-note-format.js`
// 與 `shared/ez2-route-category-resolver.js`,由呼叫端先 materialize 到 <modules_dir>),
// 回傳「這篇筆記會長什麼樣 + 驗證器怎麼說」。
//
// ## 三條紀律
//
// 1. **零寫入**:本檔沒有 fs 寫入呼叫,也不 require gateway(gateway 的 --write 是 L6-05
//    的事)。唯一的 fs 動作是 require 那兩支模組。突變探針會把「加一條寫入路徑」打成紅。
// 2. **零規則**:schema 規則(值域、正則、必填、frontmatter 排序)全在 ez2-ai 正本裡;
//    本檔不複製任何一條,壞掉要去正本修,不要在這裡補。
// 3. **validate 要自己跑**:`buildEz2Note()` 在正本裡**不呼叫** `validateEz2NoteRecord()`
//    (全檔只在定義 :148 與匯出 :338 出現),gateway 也不跑——所以底線 routeKey 這種
//    「build 得出來、validate 是紅的」在上游不會有人喊。這裡一定要把 validate 的結果
//    一起回去,讓 python 側的閘一擋掉。方案 §5-T1。
"use strict";

const crypto = require("crypto");
const path = require("path");

function readStdin() {
  const chunks = [];
  const fd = 0;
  const fs = require("fs");
  const buf = Buffer.alloc(1 << 16);
  while (true) {
    let n;
    try {
      n = fs.readSync(fd, buf, 0, buf.length, null);
    } catch (e) {
      if (e.code === "EAGAIN") continue;
      if (e.code === "EOF") break;
      throw e;
    }
    if (!n) break;
    chunks.push(Buffer.from(buf.slice(0, n)));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function main() {
  const modulesDir = process.argv[2];
  if (!modulesDir) {
    process.stdout.write(JSON.stringify({ ok: false, code: "missing_modules_dir" }) + "\n");
    return 2;
  }
  const noteFormat = require(path.join(modulesDir, "shared", "ez2-note-format.js"));
  const resolver = require(path.join(modulesDir, "shared", "ez2-route-category-resolver.js"));

  const req = JSON.parse(readStdin());
  const route = req.route || {};
  const note = req.note || {};

  // 分類身份:查得到才算數。categoryFallbackUsed=true 代表 registry 裡沒登記這條路,
  // category_uid 是 fallbackCategoryUid() 依 routeKey 機械造出來的字——python 側閘一擋。
  const category = resolver.resolveRouteCategory(route, {
    registryPath: req.category_registry_path || undefined,
  });

  const input = {
    created_at: new Date(note.created_at_ms),
    title: note.title,
    routeKey: route.routeKey,
    category_uid: category.category_uid,
    privacy_level: note.privacy_level,     // ★ 由 python 側取「條目 vs 路由」較嚴者,不用 category 的值(方案 §5-T2)
    source_kind: note.source_kind,
    frontmatter: note.extra_frontmatter || {},
    body: note.body,
  };
  if (note.review_status) input.review_status = note.review_status;
  // 09-30 aib memory:標籤照傳給正本(規則、正規化、驗證都在正本 normalizeTags,這裡不碰內容)。
  if (Array.isArray(note.tags)) input.tags = note.tags;
  if (note.created_by_ai !== undefined) input.created_by_ai = note.created_by_ai;

  let built = null;
  let buildError = null;
  try {
    built = noteFormat.buildEz2Note(input, { usedUids: new Set(req.used_uids || []) });
  } catch (e) {
    buildError = String((e && e.message) || e);
  }

  const out = {
    ok: !buildError,
    build_error: buildError,
    category: {
      category_uid: category.category_uid,
      fallback_used: category.categoryFallbackUsed === true,
      registry_uid: category.registry_uid || "",
      privacy_level_of_route: category.privacy_level,
    },
  };
  if (built) {
    // 內容本體不回傳(對齊 gateway 的 printable():只給 sha,不把全文再複製一份)。
    out.note = {
      uid: built.record.uid,
      file_name: built.fileName,
      markdown_sha256: crypto.createHash("sha256").update(built.markdown, "utf8").digest("hex"),
      markdown_bytes: Buffer.byteLength(built.markdown, "utf8"),
      privacy_level: built.record.frontmatter.privacy_level,
      review_status: built.record.frontmatter.review_status,
      frontmatter_keys: Object.keys(built.record.frontmatter).sort(),
    };
    out.validate_errors = noteFormat.validateEz2NoteRecord(built.record);
    // L6-05:apply 要有東西可寫,而「重算一次」就是 TOCTOU。所以計畫階段就把正文交出去,
    // 由 python 側落成 staging 檔;apply 只搬檔並核 sha,不再叫 note-format 算第二次。
    // 預設不給(plan 產物維持 content-free),要就明說 want_content。
    if (req.want_content === true) out.markdown = built.markdown;
  }
  process.stdout.write(JSON.stringify(out) + "\n");
  return 0;
}

try {
  process.exitCode = main();
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, code: "runner_exception",
    detail: String((e && e.message) || e) }) + "\n");
  process.exitCode = 2;
}
