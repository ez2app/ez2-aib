# Compatibility matrix — AIB 1.x data → AIB 2.0 schema

Status: Phase 0 draft, 2026-11-07. Built from a **read-only** look at field names and
types of one real installation (values were not read or copied) plus the code that
writes them. Target tables are spec section 4.3.

Legend for **Fit**:
- **direct** — field maps 1:1 (rename at most).
- **convert** — needs a transform (type, split, id scheme, timestamp unit).
- **warn** — AIB 2.0 has no place for it; migration keeps it in `legacy_json` and
  reports a warning.
- **new** — AIB 2.0 needs it and 1.x has nothing; migration fills a default.

Rules that apply to every row (spec 13.3):
1. The source is never modified. `aib migrate --dry-run` prints counts, conflicts and
   warnings; the real run writes into the new data directory with a rollback manifest.
2. Each migrated row keeps `legacy_ref` (source path + key) and the source `sha256`, so
   the post-migration check can compare counts and hashes.
3. Timestamps become UTC RFC 3339 text in SQLite (`created_at`, `updated_at`).
   1.x mixes epoch seconds (`*_epoch`, `ts`), wall milliseconds (`*_wall_ms`), float
   seconds (`started_at`) and local Taipei strings (`11-07 20:29:24`).
4. No secret value is ever written into SQLite. Secrets go to the OS secret store via
   the Platform Adapter, behind a human approval.

## 1. Seat record

Source: `<state>/instances/<cabin>/seats/w<N>.json` (one per tmux window), schema field `schema`.

| 1.x field | Type | Target | Fit | Note |
|---|---|---|---|---|
| `seat_id` | str (5 hex) | `seats.legacy_seat_code` | convert | 2.0 `seat_id` is a new ULID; the 5-hex code stays as a display code and lookup key |
| `cabin_id` | str (`YYYYMMDD-HHMMSS-XXXX`) | `worlds` / `scenes` | convert | a cabin is a workspace grouping; maps to one World (default) + Scene `cockpit` |
| `role` | str | `seats.role` | direct | role names come from 1.x role library |
| `provider`, `mode`, `model_requested` | str | `seats.provider_pref` (json) | convert | `mode` `dgrs`/`safe` maps to a Provider Adapter permission profile, not a flag string |
| `project_uid`, `project_title` | str | `seats.project_id` → `projects` | convert | projects are not in spec 4.3; propose a `projects` table (see 9) |
| `reports_to` | str (seat code) | `seats.reports_to_seat_id` | convert | resolve after all seats are imported |
| `cwd`, `source_root` | str (path) | `sessions.workdir` | convert | absolute macOS path; keep as-is, flag if missing |
| `pid`, `window_id`, `socket` | int / str | — | warn | tmux runtime handles; meaningless after migration |
| `session_uuid` | str | `sessions.provider_session_ref` | convert | provider's own session id; only `resumable` if the Provider Adapter confirms |
| `born_at`, `started_epoch`, `last_used_epoch` | str / int | `seats.created_at`, `sessions.started_at`, `seats.last_active_at` | convert | units differ |
| `trust`, `fleet_id` | str | `audit_log` | warn | trust is a 1.x attestation level; 2.0 trust comes from approvals |

## 2. Seat continuity

Source: `<state>/identity/seat-continuity/<seat>.json` (108 files on the sampled machine).

| 1.x field | Target | Fit | Note |
|---|---|---|---|
| `seat_id` | `seats.legacy_seat_code` | direct | join key with section 1 |
| `agent_uid`, `persona_uid`, `persona_sha` | `seats.character_id`, `seats.persona_id` + `persona_revision` | convert | `persona_sha` picks the revision; if no revision has that hash, import as a new revision |
| `brain_route_key` | `brain_bindings` | convert | becomes a binding with `read`+`write` for that Character |
| `project_uid`, `project_title` | `seats.project_id` | convert | |
| `session_uuid` | `sessions.provider_session_ref` | convert | |
| `record`, `updated_wall_ms` | `legacy_json`, `updated_at` | warn / convert | |

## 3. Sessions and bindings (identity)

| Source | 1.x shape | Target | Fit |
|---|---|---|---|
| `identity/sessions/<session>.json` | `{session_id, record, active{agent_uid, bound_wall_ms, event_id, trust, via}, history[]}` | `sessions` + `events` | convert — `history[]` replays as `session.bound` events with original timestamps |
| `identity/bindings.json` | `bindings{<seat>: {persona_uid, route_key}}` | `seats.persona_id`, `brain_bindings` | convert |
| `identity/cards.json` | `cards{<uid>: {code, display_name, emoji, category, dims[], persona_sha, card_path_rel, card_ref, card_updated, default_route_key}}` | `characters` (name, emoji, category) + `personas` / `persona_revisions` (dims, sha, card path) | convert — this is where 1.x merges Character and Persona; 2.0 splits them |
| `identity/user-main-persona.json` | `{persona_uid, set_at, by}` | `protagonists.persona_id` | convert — the real user's persona; **must not** become an AI-driven Character (spec 1.1) |
| `identity/*.provenance.json`, `*.bak-*` | provenance / backups | `audit_log` | warn — import provenance as audit rows, skip backups |

## 4. Persona cards

Source: `<state>/memory/<seat>/context.md` (Markdown: role, UID, emoji, duties, red lines,
knowledge domain) and the persona source behind `cards.json.card_path_rel`.

| 1.x | Target | Fit | Note |
|---|---|---|---|
| Markdown body | `persona_revisions.body_md` | direct | keep the Markdown verbatim; 2.0 stays Markdown + metadata |
| role / UID / emoji header lines | `characters` + `personas` metadata | convert | parsed, original kept in body |
| 12 dimensions (`dims[]`) | `persona_revisions.dims` (json) | direct | |
| red lines | `persona_revisions.body_md` only | warn | a persona can **not** override core policy (spec 4.2); red lines are guidance, never grants |
| `memory/_shared/*.md` | `brains` (shared) + `brain_bindings` | convert | per-binding read/write/share flags start as read-only for everyone except the author |

## 5. Projects

Source: `<state>/projects/proj:<16 hex>/card.json` (+ `plan.jsonl`, `candidates/`, `card.lock`).

| 1.x field | Target | Fit |
|---|---|---|
| `project_uid`, `folder`, `created_by_seat`, `created_wall_ms` | `projects` | convert |
| `layers.constitution{goals[], red_lines[], human_rulings[], andy_rulings[], approved, approved_by, approved_at}` | `projects.constitution` (json) + `approvals` | convert — `approved_by/at` becomes an Approval row with `legacy` provenance, not a fresh grant |
| `layers.work_state{now, next_step, pending[], owners[], seats}` | `tasks` | convert |
| `layers.handoff{…}` | `attachments` / `legacy_json` | warn |
| `layers.memory.entries[]` | `brain_revisions` | convert |
| `repo_fingerprints{}` | `projects.repos` (json) | direct |
| `plan.jsonl` | `events` (`project.plan.*`) | convert |
| old cards missing `human_rulings` | default `[]` | new — known 1.x migration gap |

## 6. Vault (second brain notes)

Source: `$EZ2_VAULT_DIR` (default `~/Ez2/Vault-uid`), one Markdown file per note named by uid,
YAML frontmatter: `uid, aliases, title, orig_name, uid_scheme, routeKey, category_uid,
folder_id, privacy_level, source_kind, created, updated, ez2fmt, tags`; folder ids in
`folder-id-index`.

| 1.x | Target | Fit | Note |
|---|---|---|---|
| file body | `brain_revisions.body_md` (or file kept on disk, row stores path + hash) | direct | spec 2: knowledge stays Markdown; recommend **keep files on disk**, index in SQLite |
| `uid` | `brain_documents.legacy_uid` | direct | wiki links `[[uid]]` keep working |
| `routeKey` | `brains.route_key` | direct | route key = which Brain the note belongs to |
| `privacy_level` | `brain_documents.privacy` | convert | must gate `brain_bindings.share` |
| `folder_id`, `category_uid` | `brain_documents.folder_id` | convert | needs `folder-id-index` |
| `created`, `updated`, `tags`, `aliases`, `title` | same names | direct | |
| `.ez2` sidecar folders | `attachments` | convert | |

Vault notes are not shared between machines (1.x rule). A future merge must handle uid
collisions; out of scope for Phase 1.

## 7. Captain-room offices

Source: `<state>/state/captain-room/offices.json`, plugin-owned (private plugin).

| 1.x field | Target | Fit | Note |
|---|---|---|---|
| `schema`, `cabin`, `floors[]{n, name, offices[]{id, sku, x, y, w, h, project_uid, type}, items[]}`, `lift`, `stash{sku: count}`, `shelved[]`, `draft` | `scenes.layout` (json, owned by the Scene plugin) | direct | layout stays opaque to core; core only validates size and references (`project_uid`) |
| `open_requests{<id>: {kind, stage, seat, role, persona_card, …}}` | `workflow_runs` | convert | an in-flight "open a seat" request; migrate only finished ones, report unfinished |
| `updated_at` | `scenes.updated_at` | direct | |

## 8. DM queue

Source: `<state>/state/dm-queue/<seat>/q_<16 hex>.json` + `audit.jsonl`, `rev`, `turn.json`,
`triage.json`, `dm-gate.json`; sender log `<state>/logs/dm-send.jsonl`; relay files
`<state>/pending/RELAY-dm-*.txt`.

| 1.x field | Target | Fit | Note |
|---|---|---|---|
| `id` | `messages.legacy_id` | direct | |
| `msg`, `preview` | `messages.body` | direct | long bodies live in RELAY files; import their content into `attachments` |
| `sender`, `sender_seat`, `sender_kind`, `sender_source` | `messages.from_*` | convert | `sender_source` (`pane_pid_chain`) is evidence, goes to `audit_log` |
| `status`, `delivered_at`, `read_at`, `read_by`, `done_at`, `done_evidence`, `done_ref`, `archived_*`, `held_*`, `superseded_*` | `messages.state` + `events` | convert | each timestamp becomes a `message.*` event so state is replayable |
| `rev` | `events.sequence` per message | convert | 1.x optimistic `--if-rev` = 2.0 expected-sequence check |
| `priority`, `kind`, `thread`, `expires`, `wait` | `messages` columns | direct | |
| `priority_sgr`, `priority_label`, `wid`, `session_uuid` | — | warn | terminal rendering / tmux window; drop |
| `audit.jsonl` | `audit_log` | convert | |

## 9. License

Source: `<state>/license.json` (+ `license.json.bak-*`).

| 1.x field | Target | Fit | Note |
|---|---|---|---|
| `license_id`, `issued_at`, `expires_at`, `signature` | `license_entitlements` | convert | 1.x signature scheme stays valid input; 2.0 verifies with its built-in public key list (spec 10.3), both schemes during transition |
| `tier`, `plan` | `license_entitlements.product` / `features` | convert | five tiers Free/Go/Plus/Pro/Ultra; old names only under old `plan_contract_version` |
| `addons[]` | `license_entitlements.features[]` | convert | paid add-ons stay in the private plugin repo; core only checks the feature string |
| `devices[]` | `license_entitlements.device_binding` | convert | |
| `note` | `legacy_json` | warn | |
| — | `offline_grace_until` | new | policy value pending (spec 18) |

Migration must never mint or extend an entitlement; it only copies the signed document
and re-verifies it.

## 10. Proposed additions to spec 4.3

| Table / column | Why |
|---|---|
| `projects` | 1.x projects carry goals, rulings and red lines; they are the unit seats belong to. Spec 4.3 has no place for them. |
| `brain_documents` | spec has `brains` + `brain_revisions`; 1.x Brain = many notes with uid links. Revisions per document, not per brain. |
| `events.global_seq INTEGER PRIMARY KEY` | one cursor for `/events?cursor=` across aggregates (ADR 0003). |
| `legacy_ref`, `legacy_sha256`, `legacy_json` columns on imported tables | dry-run diff, post-migration hash check, nothing silently dropped. |
| `seats.legacy_seat_code` | 5-hex codes are how humans and DMs address seats today. |

## 11. Not migrated

tmux runtime state (`instances/*/cockpit`, sockets, pane pids), heat / power status,
UI state (`ui-state`, `seat-display.json` positions are cosmetic), caches, `*.lock`,
`*.bak-*` backups, `test-leaks.jsonl`, `verify-shadow`. Secrets are moved only through the
secret-store path above, never into SQLite.
