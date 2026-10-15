"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const domain = require("./ez2-page-domain-contract.js");
const blockContract = require("./ez2-page-block-contract.js");
const fileStore = require("./ez2-page-file-store.js");
const { authorizePageAccess } = require("./ez2-page-tenant-authorization.js");
const { createLocalPageService, createMemoryPageRepository } = require("./ez2-page-local-service.js");

const WORKSPACE_VERSION = "2026-07-24.ez2-page-editable-workspace.v1";
const DRAFT_FIELDS = new Set(["page", "blocks", "routes", "base_revision_id", "created_at", "updated_at"]);
// 262(f)(RL-067,the maintainer 09-17 15:3x 採乙):AI 改名字/自介/在線狀態/社群/套版也走草稿。這兩欄**可選**,掛在與區塊同一份草稿上;
//   沒改就不存(鍵不在);退回整份草稿一起丟;發布時由 registration 把它們套進註冊表(publishDraft 的 apply_extras)。
//   profile 存的是「目標值」不是差異:display_name 字串、bio / status_text 字串(空字串 = 清空)、socials 整份陣列([] = 清空)。
//   appearance 一定兩欄一起(theme + shape),與 PUT /appearance「不得只寫一欄」同一條。
const DRAFT_OPTIONAL_FIELDS = new Set(["profile", "appearance"]);
// 316 v1.1:頭像 / 封面的 asset_id 也是草稿目標值("" = 清空;形狀由 registration 的 validateProfileTarget 守)
const DRAFT_PROFILE_FIELDS = new Set(["display_name", "bio", "status_text", "socials", "avatar_asset_id", "cover_asset_id", "language"]);   // 317 第 19a 項:+language
const DRAFT_APPEARANCE_FIELDS = new Set(["theme", "shape"]);
// 287-B 包二 (d):appearance 多一個可選欄 background(形狀由 design-pack validateBackground 守,這裡只認鍵名);theme / shape 仍必填。
const DRAFT_APPEARANCE_OPTIONAL = new Set(["background", "ai_chat", "avatar_size", "avatar_shape"]);   // ai_chat:290 預留;avatar_size/shape:oneshot-0926
function appearanceShapeOk(appearance) {
  if (!plain(appearance)) return false;
  const keys = Object.keys(appearance);
  if (![...DRAFT_APPEARANCE_FIELDS].every((field) => typeof appearance[field] === "string" && appearance[field])) return false;
  if (!keys.every((key) => DRAFT_APPEARANCE_FIELDS.has(key) || DRAFT_APPEARANCE_OPTIONAL.has(key))) return false;
  const designPack = require("./ez2-page-design-pack.js");
  if (designPack.validateAvatarAppearance({ avatar_size: appearance.avatar_size, avatar_shape: appearance.avatar_shape }).length) return false;
  if (appearance.ai_chat != null && typeof appearance.ai_chat !== "boolean") return false;
  return true;
}
const HISTORY_FIELDS = new Set(["page", "blocks", "routes", "archived_at"]);
// 296(RL-070 債升必做):歷史每筆多存 extras = 那一版上線時的個人檔案 / 套版快照 {profile, appearance}(由 registration 在發布 / 回復時給
//   current_extras),回復上一版時連它一起套回(rollbackLastGood 的 apply_extras);沒存的舊筆 = 只回區塊、個人檔案 / 套版不動(不清空)。
const HISTORY_OPTIONAL = new Set(["extras"]);
function historyExtrasOk(extras) {
  if (!plain(extras) || Object.keys(extras).some((key) => key !== "profile" && key !== "appearance")) return false;
  if (extras.profile != null && (!plain(extras.profile) || Object.keys(extras.profile).some((key) => !DRAFT_PROFILE_FIELDS.has(key)))) return false;
  if (extras.appearance != null && !appearanceShapeOk(extras.appearance)) return false;
  return true;
}
// 276(b)(RL-015/047/054,the maintainer 09-17 13:4x 同意):可編輯型別 4 → 設計包 BLOCKS 派生的全部契約型別(10 種 + 289 五 place = 11:
//   text/image/link/cta + music/video/shop/event/mail/tip)。單源是 design-pack 的 BLOCKS.contract_types——
//   八格示範裡的活動卡、商品架、影片、音樂、電子報、贊助從此 AIB 與編輯器都改得到。
//   317 第 18 項:form 進 BLOCKS(渲染 + /f/ 提交端點落地)⇒ 也在這份名單裡;舊的 allow-form-block 突變留著不再需要。
//   ★ 不在這份名單裡的:
//     頭像/名字認證/在線狀態/社群列(design-pack 的 profile_chrome,contract_types 空:它們是註冊表的 profile 欄位,
//     由 /manage/themes/profile 與 API PUT /profile 改,不是區塊)、存成聯絡人(/slug.vcf 由 profile 自動生成)。
const EDITABLE_BLOCK_TYPES = Object.freeze(require("./ez2-page-design-pack.js").BLOCKS.flatMap((block) => block.contract_types));
const EDITABLE_TYPE_SET = new Set(EDITABLE_BLOCK_TYPES);
const DEFAULT_HISTORY_LIMIT = 20;
const DEFAULT_BLOCK_LIMIT = 50;
// 「整頁沒動過」的形狀:註冊時 provisioning 種的那一顆歡迎文字(beta-registration:「歡迎來到 {名字} 的 Ez2.PAGE。」),
// 一顆、position 0、文字沒改。選套版種示範時只認這個形狀(或真的 0 塊);多一顆、改一個字都算有內容,只換外觀。
// ★ 字面與 beta-registration 的模板是同一句,template-demos smoke 拿註冊出來的真頁對這條正則,漂了會紅。
const WELCOME_TEXT_RE = /^歡迎來到 .+ 的 Ez2\.PAGE。$/;
function isUntouchedWelcome(blocks) {
  return blocks.length === 1 && blocks[0].block_type === "text" && blocks[0].visibility === "public" &&
    plainPayloadText(blocks[0]) !== null && WELCOME_TEXT_RE.test(plainPayloadText(blocks[0]));
}
function plainPayloadText(block) { return block && block.payload && typeof block.payload.text === "string" ? block.payload.text : null; }

function fail(code, details = []) { const error = new Error(code); error.code = code; error.details = details; throw error; }
function copy(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
function plain(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function key(tenantId, pageId) { return String(tenantId) + "\u0000" + String(pageId); }
function validDate(value) { return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value)); }
function requireOk(result, code) { if (!result.ok) fail(code, result.errors); }
function emptyEnvelope() { return { workspace_version: WORKSPACE_VERSION, drafts: {}, history: {} }; }

function normalizedContent(blocks) {
  return JSON.stringify([...blocks]
    .sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id))
    .map((block) => ({ block_type: block.block_type, visibility: block.visibility, payload: block.payload })));
}

function validateDraftExtras(recordKey, draft) {
  const errors = [];
  if (draft.profile !== undefined) {
    const profile = draft.profile;
    if (!plain(profile) || !Object.keys(profile).length || Object.keys(profile).some((field) => !DRAFT_PROFILE_FIELDS.has(field))) errors.push(recordKey + ":workspace_draft_profile_shape_invalid");
    else {
      if (profile.display_name !== undefined && (typeof profile.display_name !== "string" || !profile.display_name)) errors.push(recordKey + ":workspace_draft_profile_display_name_invalid");
      for (const field of ["bio", "status_text", "avatar_asset_id", "cover_asset_id"]) if (profile[field] !== undefined && typeof profile[field] !== "string") errors.push(recordKey + ":workspace_draft_profile_" + field + "_invalid");
      if (profile.socials !== undefined && (!Array.isArray(profile.socials) || profile.socials.some((link) => !plain(link) || typeof link.platform !== "string" || typeof link.url !== "string" || Object.keys(link).length !== 2))) errors.push(recordKey + ":workspace_draft_profile_socials_invalid");
    }
  }
  if (draft.appearance !== undefined) {
    const appearance = draft.appearance;
    if (!appearanceShapeOk(appearance)) errors.push(recordKey + ":workspace_draft_appearance_invalid");
  }
  return errors;
}

function validateEnvelope(envelope) {
  const errors = [];
  if (!plain(envelope) || envelope.workspace_version !== WORKSPACE_VERSION || !plain(envelope.drafts) || !plain(envelope.history) ||
      Object.keys(envelope).some((field) => !["workspace_version", "drafts", "history", "live_extras"].includes(field)) ||
      ("live_extras" in envelope && !plain(envelope.live_extras))) {
    return ["page_workspace_envelope_invalid"];
  }
  // 296:live_extras[recordKey] = 線上那版**發布當下**的 profile / appearance 快照 {revision_id, extras}(下一次發布 / 回復時它就是進歷史的那份;
  //   後台「人按」的立即生效改動不算在那一版裡——回復回的是「發布時的樣子」)
  for (const [recordKey, live] of Object.entries(envelope.live_extras || {})) {
    if (!plain(live) || Object.keys(live).some((field) => field !== "revision_id" && field !== "extras") || typeof live.revision_id !== "string" || !live.revision_id || !historyExtrasOk(live.extras)) errors.push(recordKey + ":workspace_live_extras_invalid");
  }
  for (const [recordKey, draft] of Object.entries(envelope.drafts)) {
    if (!plain(draft) || Object.keys(draft).some((field) => !DRAFT_FIELDS.has(field) && !DRAFT_OPTIONAL_FIELDS.has(field)) || [...DRAFT_FIELDS].some((field) => !(field in draft))) {
      errors.push(recordKey + ":workspace_draft_shape_invalid");
      continue;
    }
    errors.push(...validateDraftExtras(recordKey, draft));
    errors.push(...fileStore.validateRecord(recordKey, { page: draft.page, blocks: draft.blocks, routes: draft.routes }));
    if (draft.page && draft.page.status !== "draft") errors.push(recordKey + ":workspace_draft_status_invalid");
    if (typeof draft.base_revision_id !== "string" || !draft.base_revision_id) errors.push(recordKey + ":workspace_draft_base_invalid");
    if (!validDate(draft.created_at) || !validDate(draft.updated_at)) errors.push(recordKey + ":workspace_draft_time_invalid");
  }
  for (const [recordKey, entries] of Object.entries(envelope.history)) {
    if (!Array.isArray(entries)) { errors.push(recordKey + ":workspace_history_invalid"); continue; }
    for (const entry of entries) {
      if (!plain(entry) || Object.keys(entry).some((field) => !HISTORY_FIELDS.has(field) && !HISTORY_OPTIONAL.has(field)) || [...HISTORY_FIELDS].some((field) => !(field in entry))) {
        errors.push(recordKey + ":workspace_history_entry_invalid");
        continue;
      }
      if ("extras" in entry && !historyExtrasOk(entry.extras)) errors.push(recordKey + ":workspace_history_extras_invalid");   // 296
      errors.push(...fileStore.validateRecord(recordKey, { page: entry.page, blocks: entry.blocks, routes: entry.routes }));
      if (entry.page && !domain.PUBLISHED.has(entry.page.status)) errors.push(recordKey + ":workspace_history_status_invalid");
      if (!validDate(entry.archived_at)) errors.push(recordKey + ":workspace_history_time_invalid");
    }
  }
  return [...new Set(errors)];
}

function createEditableWorkspace(options = {}) {
  if (!options.workspace_file) fail("PAGE_WORKSPACE_FILE_REQUIRED");
  if (!options.page_repository || typeof options.page_repository.get !== "function" || typeof options.page_repository.set !== "function") fail("PAGE_WORKSPACE_PAGE_REPOSITORY_REQUIRED");
  const file = path.resolve(String(options.workspace_file));
  const lockFile = file + ".lock";
  const pages = options.page_repository;
  const mutation = String(options.test_mutation || "");
  const randomBytes = options.random_bytes || crypto.randomBytes;
  const now = options.now || (() => new Date().toISOString());
  const historyLimit = Number.isInteger(options.history_limit) && options.history_limit > 0 ? options.history_limit : DEFAULT_HISTORY_LIMIT;
  const blockLimit = Number.isInteger(options.block_limit) && options.block_limit > 0 ? options.block_limit : DEFAULT_BLOCK_LIMIT;
  // 大綱 303(RL-096):上限每頁算——block_limit_for({tenant_id, page_id}) 由呼叫端(registration)讀 133 權益回一個整數;沒給 / 回的不是正整數 ⇒ 靜態 blockLimit(今天)。
  //   永遠不超過硬上限 blockLimit。fail 的 details = [上限, 現有顆數](給「已達 n 格」用)。
  function limitFor(input) {
    if (typeof options.block_limit_for !== "function") return blockLimit;
    let dynamic;
    try { dynamic = options.block_limit_for({ tenant_id: input && input.tenant_id, page_id: input && input.page_id }); } catch (_) { dynamic = undefined; }
    return Number.isInteger(dynamic) && dynamic > 0 ? Math.min(dynamic, blockLimit) : blockLimit;
  }
  // Demo vs free cap (開發.md): prefer demos NOT counting toward plan cap.
  //   options.count_toward_block_limit(blocks) from registration → templateDemos.countTowardBlockLimit;
  //   absent ⇒ all blocks count (workspace unit smoke / callers without demos).
  //   Knife block-limit-count-demos: force all-count (old RL-096 behavior) ⇒ free-cap L1 must fail.
  function towardLimit(blocks) {
    if (mutation === "block-limit-count-demos") return Array.isArray(blocks) ? blocks.length : 0;
    if (typeof options.count_toward_block_limit === "function") {
      try {
        const n = options.count_toward_block_limit(blocks);
        if (Number.isInteger(n) && n >= 0) return n;
      } catch (_) {}
    }
    return Array.isArray(blocks) ? blocks.length : 0;
  }
  function assertUnderLimit(input, count) {
    if (mutation === "skip-block-limit") return;
    const limit = limitFor(input);
    if (count > limit) fail("PAGE_BLOCKS_LIMIT_REACHED", [limit, count]);
  }

  function newId(prefix) { return prefix + "_" + randomBytes(12).toString("hex"); }
  function instantAfter(...references) {
    const floor = Math.max(...references.map((value) => Date.parse(value)).filter(Number.isFinite), 0);
    const current = Date.parse(now());
    return new Date(Number.isFinite(current) && current > floor ? current : floor + 1).toISOString();
  }

  function readEnvelope() {
    if (!fs.existsSync(file)) return emptyEnvelope();
    let parsed;
    try { parsed = JSON.parse(fs.readFileSync(file, "utf8")); }
    catch (error) { fail("PAGE_WORKSPACE_CORRUPT", [error.message]); }
    if (mutation !== "skip-store-validation") {
      const errors = validateEnvelope(parsed);
      if (errors.length) fail("PAGE_WORKSPACE_CORRUPT", errors);
    }
    return parsed;
  }

  function writeEnvelope(envelope) {
    assertLockOwnership();
    const errors = validateEnvelope(envelope);
    if (errors.length) fail("PAGE_WORKSPACE_WRITE_INVALID", errors);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const body = JSON.stringify(envelope, null, 2) + "\n";
    if (mutation === "skip-atomic-rename") { fs.writeFileSync(file, body, { mode: 0o600 }); return; }
    const temp = file + ".tmp-" + process.pid + "-" + crypto.randomBytes(5).toString("hex");
    let fd = null;
    try {
      fd = fs.openSync(temp, "wx", 0o600);
      fs.writeFileSync(fd, body, "utf8");
      fs.fsyncSync(fd);
      fs.closeSync(fd); fd = null;
      fs.renameSync(temp, file);
    } catch (error) {
      if (fd != null) try { fs.closeSync(fd); } catch (_) {}
      try { fs.unlinkSync(temp); } catch (_) {}
      throw error;
    }
  }

  // The lock records the owner PID so a crashed writer never bricks the
  // editor. Takeover rules: a lock whose recorded owner is provably dead
  // (ESRCH) may be claimed, and an EMPTY lock older than 30s is treated as a
  // writer that died between open and write. A takeover claims the judged
  // inode by atomically renaming it aside (exactly one contender can win a
  // given inode); after acquiring, ownership is inode-verified, and every
  // durable write re-verifies ownership first (assertLockOwnership), so a
  // process that loses the lock in any residual path race aborts before its
  // next write instead of silently clobbering another writer. Path-based
  // locking without OS flock cannot make the lock file itself airtight; the
  // write-time guard is what protects the stores.
  let heldLockIno = null;
  function assertLockOwnership() {
    if (heldLockIno == null || mutation === "skip-lock") return;
    let visibleIno = null;
    try { visibleIno = fs.lstatSync(lockFile).ino; } catch (_) {}
    if (visibleIno !== heldLockIno) fail("PAGE_WORKSPACE_LOCKED");
  }
  function withLock(fn) {
    // PAGE API keeps its receipt, audit and the draft under this same lock.
    // A nested workspace operation may reuse it only while this instance still
    // owns the visible lock file; it must never acquire a second lock.
    if (heldLockIno != null) { assertLockOwnership(); return fn(); }
    if (mutation === "skip-lock") return fn();
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    let fd = null;
    for (let attempt = 0; attempt < 2 && fd == null; attempt += 1) {
      try { fd = fs.openSync(lockFile, "wx", 0o600); }
      catch (error) {
        if (error.code !== "EEXIST") throw error;
        let ownerText;
        let lockStat;
        try { ownerText = fs.readFileSync(lockFile, "utf8"); lockStat = fs.lstatSync(lockFile); }
        catch (readError) { if (readError.code === "ENOENT") continue; fail("PAGE_WORKSPACE_LOCKED"); }
        const owner = Number(ownerText.trim());
        const ownerKnown = /^[1-9][0-9]*$/.test(ownerText.trim()) && Number.isSafeInteger(owner) && owner > 0;
        let stale = false;
        if (ownerKnown) {
          try { process.kill(owner, 0); }
          catch (signalError) { if (signalError.code === "ESRCH") stale = true; }
        } else if (ownerText.length === 0 && Date.now() - lockStat.mtimeMs > 30_000) {
          stale = true;
        }
        if (!stale || attempt > 0) fail("PAGE_WORKSPACE_LOCKED");
        const grave = lockFile + ".stale-" + process.pid + "-" + crypto.randomBytes(4).toString("hex");
        try { fs.renameSync(lockFile, grave); }
        catch (_) { continue; }
        let graveIno = null;
        try { graveIno = fs.lstatSync(grave).ino; } catch (_) {}
        if (graveIno !== lockStat.ino) {
          try { fs.renameSync(grave, lockFile); } catch (_) { try { fs.unlinkSync(grave); } catch (_) {} }
          fail("PAGE_WORKSPACE_LOCKED");
        }
        try { fs.unlinkSync(grave); } catch (_) {}
      }
    }
    if (fd == null) fail("PAGE_WORKSPACE_LOCKED");
    let ownIno = null;
    try {
      fs.writeFileSync(fd, String(process.pid));
      ownIno = fs.fstatSync(fd).ino;
      let visibleIno = null;
      try { visibleIno = fs.lstatSync(lockFile).ino; } catch (_) {}
      if (visibleIno !== ownIno) fail("PAGE_WORKSPACE_LOCKED");
    } catch (error) {
      try { fs.closeSync(fd); } catch (_) {}
      try { if (ownIno != null && fs.lstatSync(lockFile).ino === ownIno) fs.unlinkSync(lockFile); } catch (_) {}
      throw error;
    }
    heldLockIno = ownIno;
    try { return fn(); }
    finally {
      heldLockIno = null;
      try { fs.closeSync(fd); } catch (_) {}
      try { if (fs.lstatSync(lockFile).ino === ownIno) fs.unlinkSync(lockFile); } catch (_) {}
    }
  }

  function authorize(page, actor, operation) {
    const result = authorizePageAccess({ page, actor, operation });
    if (!result.allowed && mutation !== "bypass-authorization") fail("PAGE_AUTH_FORBIDDEN", [result.reason]);
  }

  function publishedRecord(tenantId, pageId) {
    const record = pages.get(key(tenantId, pageId));
    if (!record) fail("PAGE_NOT_FOUND");
    return record;
  }

  function draftFor(envelope, tenantId, pageId) { return envelope.drafts[key(tenantId, pageId)] || null; }

  function assertFresh(draft, published) {
    if (mutation === "skip-stale-check") return;
    if (draft.base_revision_id !== published.page.revision_id) fail("PAGE_DRAFT_STALE", [draft.base_revision_id, published.page.revision_id]);
  }

  function assertDraftRevision(draft, expectedRevisionId) {
    if (mutation === "skip-cas") return;
    if (typeof expectedRevisionId !== "string" || expectedRevisionId !== draft.page.revision_id) fail("PAGE_WORKSPACE_CONFLICT", [draft.page.revision_id]);
  }

  function forkContent(published, at) {
    return {
      blocks: published.blocks.map((block) => Object.assign(copy(block), { version: block.version + 1, updated_at: at })),
      order: [...published.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)).map((block) => block.block_id)
    };
  }

  // Every saved draft is re-anchored as the single legal domain successor of the
  // current published record: version = published + 1 and previous_revision_id
  // always points at the published revision, because draft->draft is not a legal
  // page transition.
  function draftRecordFrom(published, blocks, order, at) {
    const positioned = order.map((blockId, index) => {
      const block = blocks.find((item) => item.block_id === blockId);
      if (!block) fail("PAGE_WORKSPACE_ORDER_UNKNOWN_BLOCK", [blockId]);
      return Object.assign(copy(block), { position: index });
    });
    if (positioned.length !== blocks.length) fail("PAGE_WORKSPACE_ORDER_INCOMPLETE");
    const page = Object.assign(copy(published.page), {
      revision_id: newId("revision"),
      previous_revision_id: published.page.revision_id,
      status: "draft",
      version: published.page.version + 1,
      block_ids: [...order],
      published_at: null,
      updated_at: at
    });
    return { page, blocks: positioned, routes: copy(published.routes) };
  }

  function validateDraftAgainstPublished(published, draftRecord) {
    requireOk(domain.validatePageRecord(draftRecord.page), "PAGE_RECORD_INVALID");
    // 刀 wys-span-unchecked(287-B 包二):契約不驗 span ⇒ cols=4 也存得進去 ⇒ C3 反臂必紅
    requireOk(blockContract.validatePageBlockSet(draftRecord.page, draftRecord.blocks, mutation === "wys-span-unchecked" ? { test_mutation: "allow-any-span" } : {}), "PAGE_BLOCKS_INVALID");
    if (mutation !== "skip-fork-transition") requireOk(domain.validatePageTransition(published.page, draftRecord.page), "PAGE_TRANSITION_INVALID");
    if (mutation !== "skip-block-transitions") {
      const previous = new Map(published.blocks.map((block) => [block.block_id, block]));
      for (const block of draftRecord.blocks) {
        if (previous.has(block.block_id)) requireOk(blockContract.validatePageBlockTransition(previous.get(block.block_id), block), "PAGE_BLOCK_TRANSITION_INVALID");
      }
    }
  }

  // extras(262(f)):沒給 = 沿用既有草稿的 profile / appearance(區塊操作不會把它們洗掉);給了 = 以給的為準,
  //   值 null = 從草稿拿掉那一欄(改回與線上一樣就不存)。
  function saveDraft(envelope, published, draftRecord, existingDraft, at, extras) {
    validateDraftAgainstPublished(published, draftRecord);
    const recordKey = key(draftRecord.page.tenant_id, draftRecord.page.page_id);
    const entry = {
      page: draftRecord.page,
      blocks: draftRecord.blocks,
      routes: draftRecord.routes,
      base_revision_id: published.page.revision_id,
      created_at: existingDraft ? existingDraft.created_at : at,
      updated_at: at
    };
    for (const field of DRAFT_OPTIONAL_FIELDS) {
      // 刀 P1':drop-extras-on-block-op ⇒ 區塊操作把草稿上的個人檔案/套版洗掉。
      const value = extras && field in extras ? extras[field] : existingDraft && mutation !== "drop-extras-on-block-op" ? existingDraft[field] : undefined;
      if (value !== undefined && value !== null) entry[field] = copy(value);
    }
    envelope.drafts[recordKey] = entry;
    writeEnvelope(envelope);
    return copy(envelope.drafts[recordKey]);
  }

  function draftExtrasOf(draft) {
    return { profile: draft && draft.profile ? copy(draft.profile) : null, appearance: draft && draft.appearance ? copy(draft.appearance) : null };
  }
  // 刀 P6:extras-not-a-change ⇒ 只有個人檔案/套版的草稿被當成「沒變更」(dirty:false、發布 PAGE_PUBLISH_NO_CHANGES)。
  function hasExtras(draft) { return mutation !== "extras-not-a-change" && Boolean(draft && (draft.profile || draft.appearance)); }

  function mutateDraft(input, operation, transform) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      if (!draft) fail("PAGE_DRAFT_REQUIRED");
      authorize(draft.page, input.actor, operation);
      assertDraftRevision(draft, input.expected_draft_revision);
      assertFresh(draft, published);
      const at = instantAfter(draft.page.updated_at, published.page.updated_at);
      const working = {
        blocks: draft.blocks.map((block) => Object.assign(copy(block), { version: block.version, updated_at: block.updated_at })),
        order: [...draft.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)).map((block) => block.block_id)
      };
      transform(working, { published, draft, at });
      const rebased = {
        blocks: working.blocks.map((block) => {
          const publishedVersion = published.blocks.find((item) => item.block_id === block.block_id);
          return Object.assign(copy(block), { version: publishedVersion ? publishedVersion.version + 1 : 1, updated_at: at });
        }),
        order: working.order
      };
      const draftRecord = draftRecordFrom(published, rebased.blocks, rebased.order, at);
      return saveDraft(envelope, published, draftRecord, draft, at);
    });
  }

  function status(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    const published = publishedRecord(input.tenant_id, input.page_id);
    authorize(published.page, input.actor, "read_private");
    const envelope = readEnvelope();
    const draft = draftFor(envelope, input.tenant_id, input.page_id);
    const history = envelope.history[key(input.tenant_id, input.page_id)] || [];
    return Object.freeze({
      has_draft: Boolean(draft),
      draft_stale: Boolean(draft && draft.base_revision_id !== published.page.revision_id),
      dirty: Boolean(draft && (normalizedContent(draft.blocks) !== normalizedContent(published.blocks) || hasExtras(draft))),
      draft_revision_id: draft ? draft.page.revision_id : null,
      // 262(f):個人檔案 / 套版的草稿欄(沒改 = null)。渲染預覽與 review 差異行都從這裡拿,不另開讀法。
      draft_profile: draft && draft.profile ? copy(draft.profile) : null,
      draft_appearance: draft && draft.appearance ? copy(draft.appearance) : null,
      draft_updated_at: draft ? draft.updated_at : null,
      draft_block_count: draft ? draft.blocks.length : null,
      published_revision_id: published.page.revision_id,
      published_version: published.page.version,
      published_block_count: published.blocks.length,
      history_depth: history.length,
      can_rollback: history.length > 0
    });
  }

  // 298 AI 操作紀錄:發布時間線(線上那份的 published_at + 歷史每份被換下的 archived_at = 下一次發布的時刻)與有沒有草稿——只讀。
  function publishTimeline(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    const published = publishedRecord(input.tenant_id, input.page_id);
    authorize(published.page, input.actor, "read_private");
    const envelope = readEnvelope();
    const history = envelope.history[key(input.tenant_id, input.page_id)] || [];
    const times = history.map((entry) => entry.archived_at).concat(published.page.published_at ? [published.page.published_at] : []).filter(Boolean);
    return Object.freeze({ has_draft: Boolean(draftFor(envelope, input.tenant_id, input.page_id)), publish_times: Object.freeze([...new Set(times)].sort()) });
  }

  function ensureDraft(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      authorize(published.page, input.actor, "manage_blocks");
      const existing = draftFor(envelope, input.tenant_id, input.page_id);
      if (existing) {
        assertFresh(existing, published);
        return copy(existing);
      }
      const at = instantAfter(published.page.updated_at);
      const fork = forkContent(published, at);
      const draftRecord = draftRecordFrom(published, fork.blocks, fork.order, at);
      return saveDraft(envelope, published, draftRecord, null, at);
    });
  }

  function buildBlock(input, published, at, options = {}) {
    const blockType = String(input.block_type || "");
    // options.any_contract_type:種套版示範用(seedBlocks)。示範內容的音樂/影片/商品/活動/電子報/贊助走契約白名單
    // (317 第 18 項起 form 也畫得出來、也在 EDITABLE 名單裡)。
    const allowed = options.any_contract_type
      ? blockContract.TYPES.includes(blockType)
      : (EDITABLE_TYPE_SET.has(blockType) || mutation === "allow-form-block");
    if (!allowed) fail("PAGE_BLOCK_TYPE_UNSUPPORTED", [blockType]);
    return {
      schema_version: blockContract.VERSION,
      tenant_id: published.page.tenant_id,
      page_id: published.page.page_id,
      block_id: newId("block"),
      block_type: blockType,
      version: 1,
      position: 0,
      visibility: input.visibility == null ? "public" : input.visibility,
      payload: copy(input.payload),
      created_at: at,
      updated_at: at
    };
  }

  function createBlock(input) {
    return mutateDraft(input, "manage_blocks", (working, context) => {
      assertUnderLimit(input, towardLimit(working.blocks) + 1);   // demo 不計入 cap;自有 +1
      const block = buildBlock(input, context.published, context.at);
      const position = Number.isInteger(input.position) ? Math.max(0, Math.min(input.position, working.order.length)) : working.order.length;
      working.blocks.push(block);
      working.order.splice(position, 0, block.block_id);
    });
  }

  // 275(a) v3 第 4 點(艦長 09-17,預設「種」):選套版時,**只有整頁 0 塊**(草稿與線上都 0)才把該套的示範內容
  // 種進草稿;有內容的頁只換外觀。一次寫整組、同一顆 revision;任何一顆型別或 payload 不合契約整組不落。
  // 與 createBlock 同一條 mutateDraft(CAS + 新鮮度 + 契約 + 轉移全走一遍),只是型別白名單放到契約全集。
  function seedBlocks(input) {
    if (!plain(input) || !Array.isArray(input.blocks) || input.blocks.length === 0) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return mutateDraft(input, "manage_blocks", (working, context) => {
      // 空 = 真的 0 塊,或只有註冊種的那顆歡迎文字而且沒動過(線上與草稿都是)。歡迎那顆會被示範內容取代。
      const publishedEmpty = context.published.blocks.length === 0 || isUntouchedWelcome(context.published.blocks);
      const draftEmpty = working.blocks.length === 0 || (isUntouchedWelcome(working.blocks) && normalizedContent(working.blocks) === normalizedContent(context.published.blocks));
      if (!publishedEmpty || !draftEmpty) fail("PAGE_SEED_NOT_EMPTY", [working.blocks.length, context.published.blocks.length]);
      assertUnderLimit(input, towardLimit(input.blocks));   // seed 多為示範簽章 ⇒ toward 0
      working.blocks.length = 0;
      working.order.length = 0;
      for (const spec of input.blocks) {
        const block = buildBlock(spec, context.published, context.at, { any_contract_type: true });
        working.blocks.push(block);
        working.order.push(block.block_id);
      }
    });
  }

  // RL-051(the maintainer 13:0x「套用之後公開頁也應該對應改變」):有內容的頁把該套示範**排在自有區塊之後**種進草稿。
  // 不動既有區塊、不重排;型別走契約全集(form 除外);上限照 blockLimit。不重種由呼叫端用簽章判(registration)。
  function appendBlocks(input) {
    if (!plain(input) || !Array.isArray(input.blocks) || input.blocks.length === 0) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return mutateDraft(input, "manage_blocks", (working, context) => {
      assertUnderLimit(input, towardLimit(working.blocks) + towardLimit(input.blocks));
      for (const spec of input.blocks) {
        const block = buildBlock(spec, context.published, context.at, { any_contract_type: true });
        working.blocks.push(block);
        working.order.push(block.block_id);
      }
    });
  }

  // 316(RL-106 乙):選套版 = 整套重來。**一把鎖、一顆 revision**:草稿(沒有就從線上 fork)裡 `input.is_own(block)` 為真的
  //   顆(自有)原序原樣保留;其餘全丟;前面接上 `input.blocks`(該套示範顆規格)+ `input.spans`(與 blocks 等長,null=滿寬);
  //   extras.appearance = `input.appearance`(undefined = 沿用草稿的;null = 拿掉;物件 = 以它為準)。
  //   冪等:算出的目標(區塊 normalizedContent + span 序列 + appearance)與現草稿全同 ⇒ 不寫、revision 不動、回 {applied:false}。
  //   上限:towardLimit(示範+自有) > blockLimit ⇒ PAGE_BLOCKS_LIMIT_REACHED(示範預設不計入);契約紅(任一顆)⇒ saveDraft 前就丟,零落地。
  //   刀:reapply-drop-own(丟自有 ⇒ R2 紅)、reapply-always-write(不比對就寫 ⇒ R3 紅)、reapply-three-revisions(多寫兩次 ⇒ R5 紅)、
  //       reapply-skip-span(不帶表 ⇒ R6 紅)、reapply-bad-demo(第 3 顆 payload 壞 ⇒ 契約紅,R5 反臂)。
  function applyTemplate(input) {
    if (!plain(input) || !Array.isArray(input.blocks) || typeof input.is_own !== "function") fail("PAGE_WORKSPACE_INPUT_INVALID");
    if (input.spans !== undefined && (!Array.isArray(input.spans) || input.spans.length !== input.blocks.length)) fail("PAGE_WORKSPACE_INPUT_INVALID", ["spans"]);
    if (input.appearance !== undefined && input.appearance !== null && !appearanceShapeOk(input.appearance)) fail("PAGE_WORKSPACE_INPUT_INVALID", ["appearance"]);
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      authorize(draft ? draft.page : published.page, input.actor, "manage_blocks");
      if (draft) { if (input.expected_draft_revision !== undefined) assertDraftRevision(draft, input.expected_draft_revision); assertFresh(draft, published); }
      const at = instantAfter(draft && draft.page.updated_at, published.page.updated_at);
      const source = draft ? draft.blocks : forkContent(published, at).blocks;
      const ordered = [...source].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id));
      const own = mutation === "reapply-drop-own" ? [] : ordered.filter((block) => input.is_own(block)).map((block) => copy(block));
      const demo = input.blocks.map((spec, index) => {
        const block = buildBlock(spec, published, at, { any_contract_type: true });
        const span = mutation === "reapply-skip-span" ? null : (input.spans ? input.spans[index] : null);
        if (span) block.span = copy(span);
        if (mutation === "reapply-bad-demo" && index === 2) block.payload = { bogus: true };
        return block;
      });
      const blocks = demo.concat(own);
      assertUnderLimit(input, towardLimit(blocks));   // applyTemplate: toward ≈ own
      const fingerprint = (list, appearance) => JSON.stringify([list.map((block) => [block.block_type, block.visibility, block.payload, block.span || null]), appearance || null]);
      const targetAppearance = input.appearance === undefined ? (draft && draft.appearance ? draft.appearance : null) : input.appearance;
      if (draft && mutation !== "reapply-always-write" &&
          fingerprint(blocks, targetAppearance) === fingerprint([...draft.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)), draft.appearance || null)) {
        return { applied: false, reason: "already_applied", draft: copy(draft) };
      }
      // 區塊序列已經是目標(只差 appearance,例如同色系換形狀)⇒ 沿用現草稿的顆與 block_id,不重造(id 換了編輯器的錨與別人的 update 都會斷)
      const blocksSame = draft && fingerprint(blocks, null) === fingerprint([...draft.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)), null);
      const finalBlocks = blocksSame ? [...draft.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)).map((block) => copy(block)) : blocks;
      const finalOrder = finalBlocks.map((block) => block.block_id);
      const rebased = finalBlocks.map((block) => {
        const previous = published.blocks.find((item) => item.block_id === block.block_id);
        return Object.assign(copy(block), { version: previous ? previous.version + 1 : 1, updated_at: at });
      });
      const extras = input.appearance === undefined ? undefined : { appearance: input.appearance };
      let saved = saveDraft(envelope, published, draftRecordFrom(published, rebased, finalOrder, at), draft, at, extras);
      if (mutation === "reapply-three-revisions") {
        for (let i = 0; i < 2; i += 1) saved = saveDraft(readEnvelope(), published, draftRecordFrom(published, rebased, finalOrder, at), saved, at, extras);
      }
      return { applied: true, draft: saved, own_count: own.length, demo_count: demo.length };
    });
  }

  function updateBlock(input) {
    return mutateDraft(input, "manage_blocks", (working) => {
      const block = working.blocks.find((item) => item.block_id === input.block_id);
      if (!block) fail("PAGE_BLOCK_NOT_FOUND", [input.block_id]);
      if (input.payload !== undefined) block.payload = copy(input.payload);
      if (input.visibility !== undefined) block.visibility = input.visibility;
      // 287-B 包二:span 是記錄層可選欄;undefined = 不動,null = 拿掉(回滿寬),物件 = 設(契約驗 1–3)。
      if (input.span === null) delete block.span;
      else if (input.span !== undefined) block.span = copy(input.span);
      // 區塊底色:undefined = 不動,null = 拿掉(跟套版),物件 = 設(契約驗 solid|custom)。
      if (input.background === null) delete block.background;
      else if (input.background !== undefined) block.background = copy(input.background);
      // 317 第 8 項:schedule 也是記錄層可選欄;undefined = 不動,null = 拿掉(永遠),物件 = 設(契約驗形狀)
      if (input.schedule === null) delete block.schedule;
      else if (input.schedule !== undefined) block.schedule = copy(input.schedule);
      // 292 / RL-111 甲:編輯器改了 payload ⇒ 這顆不再是抓來的,provenance 清掉(改 visibility / span 不算)
      if (input.payload !== undefined && mutation !== "wys-keep-provenance-on-edit") delete block.provenance;
    });
  }

  function reorderBlocks(input) {
    return mutateDraft(input, "manage_blocks", (working) => {
      if (!Array.isArray(input.order)) fail("PAGE_WORKSPACE_ORDER_INVALID");
      if (input.order.length !== working.order.length || new Set(input.order).size !== input.order.length) fail("PAGE_WORKSPACE_ORDER_INCOMPLETE");
      working.order = [...input.order];
    });
  }

  function moveBlock(input) {
    return mutateDraft(input, "manage_blocks", (working) => {
      const index = working.order.indexOf(input.block_id);
      if (index < 0) fail("PAGE_BLOCK_NOT_FOUND", [input.block_id]);
      const target = input.direction === "up" ? index - 1 : input.direction === "down" ? index + 1 : -1;
      if (target < 0 || target >= working.order.length) return;
      const order = [...working.order];
      [order[index], order[target]] = [order[target], order[index]];
      working.order = order;
    });
  }

  function deleteBlock(input) {
    return mutateDraft(input, "manage_blocks", (working) => {
      const index = working.order.indexOf(input.block_id);
      if (index < 0) fail("PAGE_BLOCK_NOT_FOUND", [input.block_id]);
      working.order.splice(index, 1);
      working.blocks = working.blocks.filter((item) => item.block_id !== input.block_id);
    });
  }

  // Apply an AI request as one workspace transaction. In particular a null
  // expected revision is allowed only when no draft exists, and a rejected
  // later operation cannot leave earlier operations on disk.
  function applyDraftOps(input) {
    if (!plain(input) || !Array.isArray(input.ops) || input.ops.length < 1 || input.ops.length > blockLimit) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      if (input.expected_draft_revision === null && draft) fail("page_api_draft_exists");
      if (input.expected_draft_revision !== null && !draft) fail("PAGE_DRAFT_REQUIRED");
      authorize(draft ? draft.page : published.page, input.actor, "manage_blocks");
      if (draft) { assertDraftRevision(draft, input.expected_draft_revision); assertFresh(draft, published); }
      const at = instantAfter(draft && draft.page.updated_at, published.page.updated_at);
      const initial = draft || { blocks: forkContent(published, at).blocks };
      const working = {
        blocks: copy(initial.blocks),
        order: [...initial.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)).map((block) => block.block_id)
      };
      // 289 (h):provenance.via("search")照契約原樣落進記錄層(create / update 同一支)。刀 search-drop-via:落地時把 via 丟掉
      //   ⇒ 來源行沒有「AI 搜尋 →」、draftHasSearchSource 看不到 ⇒ H5 必紅(AIB 端 H4 同一把刀名)。
      const copyProvenance = (provenance) => { const out = copy(provenance); if (mutation === "search-drop-via" && out) delete out.via; return out; };
      for (const op of input.ops) {
        if (op.op === "create") {
          assertUnderLimit(input, towardLimit(working.blocks) + 1);
          const block = buildBlock(op, published, at);
          // 289 〇 / 292:取回產生的顆帶 provenance(記錄層可選欄,契約驗形狀);手寫的顆沒有。
          if (op.provenance !== undefined && op.provenance !== null) block.provenance = copyProvenance(op.provenance);
          if (op.schedule !== undefined && op.schedule !== null) block.schedule = copy(op.schedule);   // 317 第 8 項
          working.blocks.push(block); working.order.push(block.block_id);
        } else if (op.op === "update") {
          const block = working.blocks.find((item) => item.block_id === op.block_id);
          if (!block) fail("PAGE_BLOCK_NOT_FOUND", [op.block_id]);
          if (op.payload !== undefined) block.payload = copy(op.payload);
          if (op.visibility !== undefined) block.visibility = op.visibility;
          if (op.schedule === null) delete block.schedule;   // 317 第 8 項:null = 拿掉,物件 = 設,缺席 = 不動
          else if (op.schedule !== undefined) block.schedule = copy(op.schedule);
          if (op.span === null) delete block.span;
          else if (op.span !== undefined) block.span = copy(op.span);
          if (op.background === null) delete block.background;
          else if (op.background !== undefined) block.background = copy(op.background);
          // 292:update 帶 provenance = 重抓(換 fetched_at);null = 拿掉來源(變成手寫的顆);
          //   RL-111 甲:帶 payload 而**沒帶** provenance = 內容被人改過 ⇒ 來源也清掉(改過就不是抓來的,ttl 也不該收走它);
          //   只改 visibility / span / background(沒帶 payload)⇒ 不動
          if (op.provenance === null || (op.provenance === undefined && op.payload !== undefined)) delete block.provenance;
          else if (op.provenance !== undefined) block.provenance = copyProvenance(op.provenance);
        } else if (op.op === "move") {
          const index = working.order.indexOf(op.block_id);
          if (index < 0) fail("PAGE_BLOCK_NOT_FOUND", [op.block_id]);
          const target = op.direction === "up" ? index - 1 : op.direction === "down" ? index + 1 : -1;
          if (target >= 0 && target < working.order.length) [working.order[index], working.order[target]] = [working.order[target], working.order[index]];
        } else if (op.op === "delete") {
          const index = working.order.indexOf(op.block_id);
          if (index < 0) fail("PAGE_BLOCK_NOT_FOUND", [op.block_id]);
          working.order.splice(index, 1);
          working.blocks = working.blocks.filter((item) => item.block_id !== op.block_id);
        } else if (op.op === "reorder") {
          if (op.order.length !== working.order.length || new Set(op.order).size !== op.order.length) fail("PAGE_WORKSPACE_ORDER_INCOMPLETE");
          working.order = [...op.order];
        } else fail("PAGE_WORKSPACE_INPUT_INVALID", ["op"]);
      }
      const rebased = working.blocks.map((block) => {
        const previous = published.blocks.find((item) => item.block_id === block.block_id);
        return Object.assign(copy(block), { version: previous ? previous.version + 1 : 1, updated_at: at });
      });
      const next = draftRecordFrom(published, rebased, working.order, at);
      return saveDraft(envelope, published, next, draft, at);
    });
  }

  // 262(f):把個人檔案 / 套版的目標值寫進草稿(沒草稿就從線上版 fork 一份,區塊照抄)。
  //   expected_draft_revision:字串 = CAS;null = 必須還沒有草稿(與 applyDraftOps 同義);undefined = 不比
  //   (PUT /profile、PUT /appearance 的請求體沒有這個鍵——個人檔案不依賴區塊狀態,接在現有草稿上是安全的)。
  //   profile:{欄: 值 | null};null = 從草稿拿掉那一欄(呼叫端算過「與線上相同」)。appearance:{theme, shape} | null。
  //   一次寫成新 revision(與區塊操作同一條 draftRecordFrom 路),review_url 才指得到這一份。
  function updateDraftExtras(input) {
    if (!plain(input) || (input.profile === undefined && input.appearance === undefined)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    if (input.profile !== undefined && input.profile !== null && (!plain(input.profile) || Object.keys(input.profile).some((field) => !DRAFT_PROFILE_FIELDS.has(field)))) fail("PAGE_WORKSPACE_INPUT_INVALID", ["profile"]);
    if (input.appearance !== undefined && input.appearance !== null && !appearanceShapeOk(input.appearance)) fail("PAGE_WORKSPACE_INPUT_INVALID", ["appearance"]);
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      if (input.expected_draft_revision === null && draft) fail("page_api_draft_exists");
      if (typeof input.expected_draft_revision === "string" && !draft) fail("PAGE_DRAFT_REQUIRED");
      authorize(draft ? draft.page : published.page, input.actor, "manage_blocks");
      if (draft) { if (input.expected_draft_revision !== undefined) assertDraftRevision(draft, input.expected_draft_revision); assertFresh(draft, published); }
      const at = instantAfter(draft && draft.page.updated_at, published.page.updated_at);
      const initial = draft || { blocks: forkContent(published, at).blocks };
      const order = [...initial.blocks].sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id)).map((block) => block.block_id);
      const rebased = initial.blocks.map((block) => {
        const previous = published.blocks.find((item) => item.block_id === block.block_id);
        return Object.assign(copy(block), { version: previous ? previous.version + 1 : 1, updated_at: at });
      });
      const extras = {};
      if (input.profile !== undefined) {
        const merged = Object.assign({}, draft && draft.profile ? draft.profile : {}, input.profile || {});
        for (const [field, value] of Object.entries(merged)) if (value === null) delete merged[field];
        extras.profile = input.profile === null || !Object.keys(merged).length ? null : merged;
      }
      if (input.appearance !== undefined) extras.appearance = input.appearance;
      // 沒草稿、而目標值全部與線上相同 ⇒ 不開草稿(「沒改就不存」),回 null 讓呼叫端印「沒有變更」。
      if (!draft && !extras.profile && !extras.appearance) return null;
      const next = draftRecordFrom(published, rebased, order, at);
      const saved = saveDraft(envelope, published, next, draft, at, extras);
      const errors = validateDraftExtras(key(input.tenant_id, input.page_id), saved);
      if (errors.length) fail("PAGE_WORKSPACE_INPUT_INVALID", errors);
      return saved;
    });
  }

  function discardDraft(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      if (!draft) fail("PAGE_DRAFT_REQUIRED");
      authorize(draft.page, input.actor, "manage_blocks");
      assertDraftRevision(draft, input.expected_draft_revision);
      delete envelope.drafts[key(input.tenant_id, input.page_id)];
      writeEnvelope(envelope);
      return Object.freeze({ discarded: true });
    });
  }

  function previewRecord(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    const published = publishedRecord(input.tenant_id, input.page_id);
    const envelope = readEnvelope();
    const draft = draftFor(envelope, input.tenant_id, input.page_id);
    if (!draft) fail("PAGE_DRAFT_REQUIRED");
    authorize(draft.page, input.actor, "read_private");
    const page = Object.assign(copy(draft.page), {
      status: "published_noindex",
      published_at: draft.page.updated_at,
      primary_route_id: published.page.primary_route_id
    });
    return { page, blocks: copy(draft.blocks), routes: copy(draft.routes) };
  }

  // Publish replays the stored draft plus review/publish successors through the
  // canonical local service so every domain, block, and route transition rule is
  // enforced by the existing contracts, then commits with CAS on the previously
  // published revision.
  function publishChain(published, content, actor) {
    const recordKey = key(published.page.tenant_id, published.page.page_id);
    const memory = createMemoryPageRepository();
    memory.set(recordKey, copy(published), null);
    const service = createLocalPageService({ repository: memory });
    service.revise({ page: content.page, blocks: content.blocks, routes: copy(published.routes), actor });
    const reviewAt = instantAfter(content.page.updated_at);
    const reviewPage = Object.assign(copy(content.page), { revision_id: newId("revision"), previous_revision_id: content.page.revision_id, status: "review", version: content.page.version + 1, updated_at: reviewAt });
    const reviewBlocks = content.blocks.map((block) => Object.assign(copy(block), { version: block.version + 1, updated_at: reviewAt }));
    service.revise({ page: reviewPage, blocks: reviewBlocks, routes: copy(published.routes), actor });
    const publishAt = instantAfter(reviewAt);
    const publishedPage = Object.assign(copy(reviewPage), {
      revision_id: newId("revision"),
      previous_revision_id: reviewPage.revision_id,
      status: "published_noindex",
      version: reviewPage.version + 1,
      primary_route_id: published.page.primary_route_id,
      published_at: publishAt,
      updated_at: publishAt
    });
    const publishedBlocks = reviewBlocks.map((block) => Object.assign(copy(block), { version: block.version + 1, updated_at: publishAt }));
    service.publish({ page: publishedPage, blocks: publishedBlocks, routes: published.routes, actor });
    return memory.get(recordKey);
  }

  // Two-phase commit around the canonical page store: phase A durably archives
  // the outgoing published version into history (idempotent by revision_id)
  // BEFORE pages.set, so a crash after the canonical commit can never lose the
  // rollback target; phase B drops the consumed draft. A crash between the
  // phases leaves only a detectable stale draft, never silent content loss.
  // 296:extras = 這一版(被換下的那版)上線時的 profile / appearance 快照;呼叫端沒給就不存(舊行為)。刀 history-drop-extras ⇒ R2 必紅
  function appendHistory(envelope, recordKey, record, archivedAt, extras) {
    const history = envelope.history[recordKey] || [];
    const last = history[history.length - 1];
    if (!last || last.page.revision_id !== record.page.revision_id) {
      const entry = { page: copy(record.page), blocks: copy(record.blocks), routes: copy(record.routes), archived_at: archivedAt };
      if (extras !== undefined && mutation !== "history-drop-extras") {
        if (!historyExtrasOk(extras)) fail("PAGE_WORKSPACE_INPUT_INVALID", ["current_extras"]);
        entry.extras = copy(extras);
      }
      history.push(entry);
    }
    envelope.history[recordKey] = history;
  }

  // 296:被換下那版進歷史時帶的快照 = 它發布當下記的 live_extras(revision 對得上才算);沒有(這個功能之前發的版)⇒ 用呼叫端現在讀的
  //   current_extras 兜底;呼叫端連 current_extras 都沒給 ⇒ 不存(舊行為)。
  function outgoingExtras(envelope, recordKey, published, input) {
    const live = envelope.live_extras && envelope.live_extras[recordKey];
    if (live && live.revision_id === published.page.revision_id && mutation !== "history-extras-from-now") return live.extras;
    return input.current_extras;
  }
  // 296:剛上線的這版,發布當下的 profile / appearance(apply_extras 套完後由呼叫端 read_extras 讀回註冊表);沒給就不記
  function rememberLiveExtras(envelope, recordKey, next, input) {
    if (typeof input.read_extras !== "function") return;
    const extras = input.read_extras();
    if (extras === undefined) return;
    if (!historyExtrasOk(extras)) fail("PAGE_WORKSPACE_INPUT_INVALID", ["read_extras"]);
    if (!plain(envelope.live_extras)) envelope.live_extras = {};
    envelope.live_extras[recordKey] = { revision_id: next.page.revision_id, extras: copy(extras) };
  }

  // Capacity trimming happens in phase B (after the canonical commit) so a
  // publish that fails at pages.set can never evict a real rollback target.
  function trimHistory(envelope, recordKey) {
    const history = envelope.history[recordKey] || [];
    while (history.length > historyLimit) history.shift();
    envelope.history[recordKey] = history;
  }

  function publishDraft(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      const draft = draftFor(envelope, input.tenant_id, input.page_id);
      if (!draft) fail("PAGE_DRAFT_REQUIRED");
      authorize(published.page, input.actor, "publish");
      assertDraftRevision(draft, input.expected_draft_revision);
      assertFresh(draft, published);
      // 262(f):只有個人檔案 / 套版改了(區塊沒動)也算有變更、也能發布。
      if (normalizedContent(draft.blocks) === normalizedContent(published.blocks) && !hasExtras(draft) && mutation !== "allow-noop-publish") fail("PAGE_PUBLISH_NO_CHANGES");
      const recordKey = key(input.tenant_id, input.page_id);
      const next = publishChain(published, { page: copy(draft.page), blocks: copy(draft.blocks), routes: copy(draft.routes) }, input.actor);
      // 262(f)明確順序:區塊鏈已在記憶體裡走完全部契約(上一行)⇒ 先把 profile / appearance 套進註冊表(apply_extras,
      //   呼叫端給;它丟錯整個發布中止,草稿與線上都不動、可重試)⇒ 才寫歷史與 pages.set。反過來(先寫區塊再套註冊表)
      //   失敗時草稿已被消耗、基底也過期,個人檔案的變更會靜靜消失。若 pages.set 在套完之後失敗,個人檔案已上線而區塊沒上、
      //   草稿仍在(基底沒變)⇒ 重按發布會再套一次同樣的值(冪等)。
      if (hasExtras(draft)) {
        if (typeof input.apply_extras !== "function") fail("PAGE_PUBLISH_EXTRAS_UNHANDLED", Object.keys(draftExtrasOf(draft)).filter((field) => draft[field]));
        input.apply_extras(draftExtrasOf(draft));
      }
      appendHistory(envelope, recordKey, published, next.page.published_at, outgoingExtras(envelope, recordKey, published, input));   // 296:連被換下那版的 profile / appearance 一起存
      writeEnvelope(envelope);
      assertLockOwnership();
      pages.set(recordKey, next, published.page.revision_id);
      rememberLiveExtras(envelope, recordKey, next, input);
      delete envelope.drafts[recordKey];
      trimHistory(envelope, recordKey);
      writeEnvelope(envelope);
      return copy(next);
    });
  }

  function rollbackLastGood(input) {
    if (!plain(input)) fail("PAGE_WORKSPACE_INPUT_INVALID");
    return withLock(() => {
      const envelope = readEnvelope();
      const published = publishedRecord(input.tenant_id, input.page_id);
      authorize(published.page, input.actor, "publish");
      if (mutation !== "skip-cas" && (typeof input.expected_published_revision !== "string" || input.expected_published_revision !== published.page.revision_id)) {
        fail("PAGE_WORKSPACE_CONFLICT", [published.page.revision_id]);
      }
      const recordKey = key(input.tenant_id, input.page_id);
      const history = envelope.history[recordKey] || [];
      // A crash between the two phases can leave the CURRENT published
      // revision as the last history entry; skipping it keeps a retried
      // rollback pointing at the real previous version instead of a no-op.
      let snapshotIndex = history.length - 1;
      while (snapshotIndex >= 0 && history[snapshotIndex].page.revision_id === published.page.revision_id) snapshotIndex -= 1;
      if (snapshotIndex < 0) fail("PAGE_ROLLBACK_HISTORY_EMPTY");
      const snapshot = history[snapshotIndex];
      const at = instantAfter(published.page.updated_at);
      const currentBlocks = new Map(published.blocks.map((block) => [block.block_id, block]));
      const blocks = [...snapshot.blocks]
        .sort((a, b) => a.position - b.position || a.block_id.localeCompare(b.block_id))
        .map((block, index) => {
          const current = currentBlocks.get(block.block_id);
          return Object.assign(copy(block), {
            position: index,
            version: current ? current.version + 1 : 1,
            created_at: current ? current.created_at : block.created_at,
            updated_at: at
          });
        });
      const draftRecord = draftRecordFrom(published, blocks, blocks.map((block) => block.block_id), at);
      validateDraftAgainstPublished(published, draftRecord);
      const next = publishChain(published, draftRecord, input.actor);
      // 296:那一筆歷史有存 profile / appearance 快照 ⇒ 連它一起回(順序同 publishDraft:區塊鏈驗完、寫歷史與 pages.set 之前套進註冊表,
      //   丟錯整個回復中止、線上不動);舊筆沒存 ⇒ 只回區塊(不清空)。刀 rollback-ignore-extras ⇒ R2 必紅
      if (snapshot.extras && mutation !== "rollback-ignore-extras") {
        if (typeof input.apply_extras !== "function") fail("PAGE_PUBLISH_EXTRAS_UNHANDLED", Object.keys(snapshot.extras).filter((field) => snapshot.extras[field]));
        input.apply_extras(copy(snapshot.extras));
      }
      appendHistory(envelope, recordKey, published, next.page.published_at, outgoingExtras(envelope, recordKey, published, input));
      writeEnvelope(envelope);
      assertLockOwnership();
      pages.set(recordKey, next, published.page.revision_id);
      rememberLiveExtras(envelope, recordKey, next, input);
      const entries = envelope.history[recordKey];
      const usedIndex = entries.findIndex((entry) => entry.page.revision_id === snapshot.page.revision_id);
      if (usedIndex >= 0) entries.splice(usedIndex, 1);
      trimHistory(envelope, recordKey);
      writeEnvelope(envelope);
      return copy(next);
    });
  }

  return Object.freeze({
    appendBlocks, applyDraftOps, applyTemplate, createBlock, deleteBlock, discardDraft, ensureDraft, moveBlock, previewRecord, seedBlocks,
    publishDraft, publishTimeline, reorderBlocks, rollbackLastGood, runLocked: withLock, status, updateBlock, updateDraftExtras
  });
}

module.exports = { DEFAULT_HISTORY_LIMIT, DRAFT_APPEARANCE_FIELDS, DRAFT_PROFILE_FIELDS, EDITABLE_BLOCK_TYPES, WELCOME_TEXT_RE, WORKSPACE_VERSION, createEditableWorkspace, isUntouchedWelcome, validateEnvelope };
