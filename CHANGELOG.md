[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

## 0.1.26

- **Online receiving no longer breaks because the dedup record grows too large**: the dedup record now stores short keys and keeps at most 1,500 entries; old files are converted the first time they are read. Before, the record could pass 64 KB within a week, fail to save, and keep the receiver reconnecting.
- **Held outside messages no longer show the sender's self-chosen name to AI**: for Telegram and unknown aib seats still waiting for owner review, the command line and AI now see the sender as "External Telegram #tag" / "External aib seat #tag"; the tag stays the same for the same sender.
- **Friend visit snapshots can carry collection titles**: only 7 fixed titles are allowed; anything else drops just the titles.

## 0.1.25

- **Replies to a message you sent yourself from the captain room stay in the captain room**: when an online reply points to a message the owner sent in person (Touch ID), it is held for the owner and is not handed to the AI box. The owner can release it to the AI.
- **Telegram, step one**: `aib chat telegram on --seat` (Touch ID) / `off` / `status` / `check` / `allow` / `block` / `send`. Private text messages to your bot go to the seat's queue and are shown only in the captain room (never the AI box); you reply from the captain room. The bot token is stored with `aib secret ask TELEGRAM_BOT_TOKEN` and is never printed. Plainly: the token is on this computer, so an AI running as the same user can read it, and Telegram bot chats are not end-to-end encrypted.
- **`aib chat recent --json [--minutes N]`**: recent online / Telegram private messages sent and received (time, direction, network, contact, result) for the captain room's mail view. No message content.
- **Friend visit snapshots carry a few more fields**: which layout item each public seat is using, which public seats are in a meeting, which area a seat is in (only areas without a shard number), and a short summary of collected decor. Each field is checked on the sending side and again on the receiving side; anything that does not fit is dropped.
- **`aib vault newpost --meta-json` / `--appendable` and `aib vault append`**: plugins can add allow-listed frontmatter fields (core fields such as uid / routeKey / created cannot be set; privacy_level can only be raised; control, direction and zero-width characters are removed; a wrong type rejects the whole call) and append to a note created with `--appendable` under the same write route (64 KB per append). Writes to the vault now take a lock so newpost / append / mkdir / doctor --fix run one at a time.

## 0.1.24

- **Pair your phone as the owner's phone**: `aib chat pair start` (Touch ID) shows a QR code; after the phone sends back the one-time code (10 characters, 5 minutes), a second Touch ID window shows the phone's npub before it is added. Up to 3 phones; `aib chat pair revoke <npub prefix|all>` (Touch ID). Messages from the phone that start with `指令:`, `!` or `gm ` are instructions; everything else is discussion (reply only). Security settings (pairing, devices, owner key, wake, friends, sharing, Touch ID / PIN, secret store, plugins, license) still need Touch ID on the computer. Replayed or out-of-time messages are rejected.
- **Online messages reach the AI box without a Plus licence**: incoming messages from other people's seats are delivered on the basic plan too (other queue features still need Plus).
- **Messages with an unknown type are no longer put in the AI box** (they are logged and not receipted).
- **Who wrote it**: the message frame now shows what the sender says about the author (the owner / drafted by AI and approved / sent automatically by AI), and says plainly that the receiver cannot verify it.
- **World lists the live cabin first**: when one owner has several cabins, the fresh, most recently seen one comes first, so a captain room that reads only the first entry no longer shows an online friend as offline.
- **`aib newproj --approve` shows what changed**: approving records a hash of the goals and red lines; before you confirm, it lists added / removed goals and whether the red lines are the same as last time. The records are not signed, and the screen says so.

## 0.1.23

- **Owner key protected by a passphrase**: `aib chat owner protect` encrypts your owner key at rest (passphrase of at least 12 characters, read only from the terminal; on macOS Touch ID first). Changing your device list then always needs you: the base is the signed list fetched from the relays (an older or missing one is shown in full with a warning), and the changes (+added / −removed, with fingerprints) are shown before you approve. Wrong passphrases lock with growing delays. Restoring an old backup no longer writes the plain key back.
- **Pair another machine**: `aib chat join` on the new machine shows a pairing code; `aib chat devices add <code>` on the owner machine checks the fingerprint, then you approve. `devices revoke` / `devices extend` sign a new list.
- **See whether the other computer is asleep**: contacts and `world` show awake / asleep / offline; sends to a sleeping machine say so; on mains power the relay keeps the Mac awake (`aib chat awake on|off`); messages without a receipt after 24 hours are marked unconfirmed.
- **Friends see each seat's outfit** on your island (the captain room's look code, checked again by the receiver).
- **Right column**: a click during a redraw now waits for the list to finish drawing instead of being lost; on macOS the bottom of the list reminds you where to turn mouse reporting back on (Terminal: View → Allow Mouse Reporting).
- **`aib test` no longer flags the repository under test** as touching the real home folder when worktrees live under `~/.ez2-aib` (regression in 0.1.22).

## 0.1.22

- **Paste API keys into a masked box**: `aib secret ask <NAME>` opens a masked input box, checks the format (and with `--check`, asks the provider whether the key works), then needs Touch ID before the key is stored encrypted. `--push <host>:<path>` also sends it to a server over ssh; the Touch ID prompt shows the real host and port that ssh will connect to, and that connection is pinned (no proxy). The key never appears in arguments, output or logs.
- **The chat relay restarts itself and reports its health**: if the relay stops while online chat is on, it starts again after 5 seconds. `aib chat status --json` adds `healthy` and `why`, and aib reminds you to back up your chat key.
- **One owner, several machines (part 1)**: aib can verify an owner-signed device list and merge each listed machine's roster when looking someone up; mail from a removed machine no longer verifies. Old rosters without a device list work as before.
- **Contacts show the other side's aib version** and which features it cannot receive yet; with several machines it uses the version of the machine that seat lives on.
- **Contacts follow roster changes**: seats removed from a friend's roster drop out of your contacts, so messages no longer go to a closed seat; `aib chat contacts --refresh <npub|all>` fetches again right away.
- **Approved project cards can get goals later**: `aib newproj --propose-goals <uid> --goal …` writes a proposal only; the goals take effect after the human runs `aib newproj --approve <uid>`. Red lines and human rulings stay as approved, and control or invisible characters are stripped from the goals.
- **`aib test` refuses to touch the real home folder**: a test that reads or writes `~/.ez2-aib`, `~/.claude`, `~/.ssh` and similar places is marked red with the paths it touched.
- The screenshot editor drops its old copy-to-clipboard icon; "Clipboard only" next to Save remains.

## 0.1.21

- **Send online messages as the human from the captain room**: `aib chat send <address> --as-human --cabin <cabin>` (text on stdin). On macOS every send needs Touch ID (or the login password); the prompt text is built by aib ("send to <who>: <first 40 chars>"), one prompt at a time, and there is no way to skip it — if Touch ID is off or unavailable the send is refused. Other platforms use the captain-room PIN (pbkdf2-hashed, at least 6 digits, lockout that doubles after each miss up to 1 hour); the PIN only guards against mistakes, not against other programs running as you.
- **Screenshots in a uid vault go to the second brain's gallery**: the screenshot helper finds the gallery by route (`storage.gallery`), creates a "Screenshots" subfolder when missing, and only falls back to the Desktop (with the reason in `status`) when there is no gallery. The screenshot editor can also save to the clipboard only.
- **Friends see your island's sky** (season, weather, time of day, festival, realm); only these keys travel, each a short lowercase name. Values that end with a newline are no longer accepted anywhere in island snapshots.
- **Handoff lists stuck messages**: `aib handoff collect` shows messages from the last 48 hours that never reached the other seat's input box (held because of a draft), both sent and received, so the next session knows what to re-send.

## 0.1.20

- **`aib vault doctor --fix` keeps persona card folder titles**: 0.1.19 dropped the titles of non-`fld_` folders (persona card folders) when rewriting indexes, so card references fell back to raw uids. Retired `fld_` folders are still removed. Do not run `doctor --fix` on 0.1.19.

## 0.1.19

- **Cleaner installs and upgrades**: a copy that is 5 or more minor versions behind is backed up and installed fresh; the installer ends with commands you can copy to open your first seat; PATH is no longer added twice to your shell profile.
- **Persona card list refreshes after an upgrade**: `aib update` / `install.sh` rebuild `cards.json` when the program version changes, and `aib doctor` rebuilds one made by an older aib (cards you edited by hand or a changed persona library are reported, never overwritten).
- **Clearer command hints**: `aib dm --help` prints its usage; the status line's read hint includes `--seat`; "folder not found" errors name the full path.
- **Chat**: a third default relay (offchain.pub).
- README: new projects are approved with `aib newproj --approve` (Touch ID on macOS when an AI seat runs it); `safe` mode is explained as each AI's own permission settings.

## 0.1.18

- **Touch ID approval on macOS**: when an AI seat runs `aib newproj --approve`, the Mac shows a Touch ID (or login password) prompt and the project's goals and red lines are approved only after the owner touches the sensor. The prompt text is built by aib, each prompt carries a one-time nonce, only one prompt can be open per machine, and `AIB_TOUCHID=0` turns the path off. Other platforms keep the terminal-only approval.
- **Status line updates itself**: `aib update`, `install.sh` and `aib init` refresh the status line script at the end.
- **`aib vault doctor` syncs titles, aliases and the index** and fixes moved notes; new `aib vault mkdir`; `aib memory write` registers new notes in the index.
- **Handoff, tidy and review notes follow the seat's route** instead of always landing in the default handoff folder (falls back to the default and records why if the seat route cannot be written).
- **`aib newrole --new-seat --model`** picks the model for the new seat.
- **Chat**: seats without a bound persona still get a seat key and appear in the roster; island records gain extension fields with size limits and compatibility checks.
- Traditional Chinese: the brain row button in the right pane now reads 「換第二大腦(專案)」/「綁第二大腦(專案)」; when the pane is too narrow the title gives way and the button stays.

## 0.1.17

- **aib-boards 0.2.1**: new-mail cards pop up only for messages a human sent and for friend requests (friend requests only on the captain seat); the pixel avatar is gone and the icon is back in the title bar.
- **Release checks retry upgrades from PyPI**: right after an upload PyPI's CDN edges can disagree for a few minutes, so the from-PyPI upgrade check retries (6 × 20 s) instead of failing on the first stale answer.
- **Card dates use ISO**: new persona cards and skeleton cards write `updated` as UTC ISO, and the card registry reads ISO dates.
- **`aib vault newpost` no longer writes `orig_name`** for notes it creates (the field stays for notes adopted by `aib vault doctor`).
- Traditional Chinese: 「啟動時間 / 啟動」 is now 「初始」.

## 0.1.16

- **New notes and a health check for uid vaults** (licensed add-on `obsidian-labels`): `aib vault newpost` creates a note in uid layout in the chosen folder; `aib vault doctor [--fix --backup-dir]` checks the vault layout. Without the licence they exit 3 and write nothing.
- **Visit a friend's island and captain room**: `aib chat space [<owner> none|island|room]` sets what each contact may see (friends and your own machines: island by default; room is opened per person; strangers and blocked people: nothing). `aib chat island <owner>` / `aib chat room <owner>` read what was shared. Usage, unread counts, IP, host names and message text are always filtered out.
- **The "is this the human" check works on macOS**: in an ordinary terminal it no longer mistakes `/dev/tty` (owned by root) for the real terminal, so commands that only the human may run (such as approving a project's goals) work again; cockpit panes are now also recognised by their terminal, not only by an environment variable.
- **Release checks wait for PyPI**: the release verification waits until PyPI serves the new version before testing upgrades.
- End-to-end sandbox tests for online chat.
- **Seat level on the status line**: when the captain-room extension has written a snapshot within the last day, the CTX line shows the seat's level and resources after `@seat` (for example `@abb89 5⭐ 30 wood/12 stone`); the 📌 line keeps only the main item, steps and update time.
- `aib vault doctor` also reports `updated` values that are not ISO times and fixes the ones it can convert for certain.

## 0.1.15

- **Online chat, friends and contacts**: friend requests, blocking and a contact list (shown in the captain room's address book and inbox); replies from people you wrote to are accepted; your own other machines can be linked.
- **Presence and world map**: `aib chat share` sets what each contact can see (default: friends see online, your own machines see status, strangers see nothing).
- **Identity proofs**: NIP-05 goes into the public roster; social-media proofs are sent only to friends and your own machines.
- **Key backup**: `aib chat backup <file>` / `aib chat restore <file>` encrypt the chat keys with a passphrase you type (the file never holds the private key in clear).
- **Checks and relays**: `aib chat selftest`, custom relays, and `aib chat relays test <relay>`. Without a name and country set, chat does not connect.
- **Fewer lost or noisy receipts**: refusal receipts are rate-limited and listed; a receipt that arrives before its send record is held and matched later.
- **Memory index titles match the note**: the index keeps the cleaned title that is written in the note file.
- **Extensions install after being run**: stray `__pycache__` / `.pyc` files are listed and removed before install or enable instead of blocking it, and seat-info runs without writing bytecode.

## 0.1.14

- **Memory notes can be written on a fresh install**: the bundled note-format files carried a licence header added by the open-source export, so their fingerprint never matched and `aib memory write` always failed on machines without the private source. The check now ignores only that one header line and still enforces the fingerprint.
- **Notes go into vault folders with their own route**: a route added with `folder-add --route-key` uses its parent route's category, so `aib memory write --route <key>` writes into the new folder. `folder-add` reports `route_added=false` when the route was already there.

## 0.1.13

- **Online chat (renamed from global chat) asks once before going online**: the first time it is turned on, aib asks whether to connect to public relays and remembers the answer; saying no is remembered like `aib chat off`. When nobody can answer (non-interactive runs, the installer), it stays offline.
- **Friends only**: messages from strangers are refused with a reason (exit code 2). `aib chat friend add / remove` and `aib chat friends` manage the list.
- **Older versions keep working**: the message header still uses the old tag when sending, so 0.1.12 and earlier can read it; both old and new tags are accepted when reading.
- **New vault folders can carry their own route**: `folder-add --route-key <key>` registers a route for a new folder (permissions inherited from the nearest parent route, never widened), so `aib newproj`, binding and `aib memory write` can use it; `aib newproj --folder` also accepts a folder title.
- **`aib test --changed` no longer crashes** when a helper module is missing; it says the selection could not be measured (exit code 3).

## 0.1.12

- **Bundled extensions install themselves**: the free signed extensions that ship with aib are installed and registered with Claude Code during install, `aib init` and `aib update`.
- **Global chat receipts no longer get dropped**: the sender now records a message as sent before sending it, so a receipt that arrives before the relay's confirmation is no longer mistaken for an impostor.
- **Find who owns an out-of-scope question**: `aib who-owns <keyword or sentence>` searches persona cards, project cards and the vault's full text, ranks the matches with evidence, and suggests reopening the seat that owns it, or creating a new persona or project when nothing matches. `aib scope set / show` records the sites, domains, keywords and aliases a card is responsible for.
- **Seats hand off out-of-scope questions**: the persona pack now tells each seat to run `aib who-owns` first when a question is outside its scope, and to suggest opening the seat that owns it instead of doing it itself.
- **Add and retire vault folders**: `folder-add` and `folder-retire` register new folders in a uid vault, or retire old ones, without editing the index by hand.
- **`aib who-owns` matches more wording**: it splits camelCase and case (RichMenu = richmenu = rich menu), breaks a sentence into words before searching the vault, and reads each card's `scope_aliases`.
- **Licence trust keys**: licences signed by the maintainer's main key or the MacBook Pro backup key are accepted; a test pins the list so any change to it fails until a maintainer approves it.
- **Missing pieces are reported, not hidden**: when the module that recognises the human's own words is missing, memory tidy says so in its receipt and in the check-up instead of silently counting zero.
- **Main persona for the human**: `aib persona main show / set / clear` records which persona card is the human's own; when a seat's memory tidy finds words the human typed, it adds what it learned to that card quietly, with a snapshot and receipt so it can be undone.

## 0.1.11

- **Memory survives moving the vault**: the memory index stores each note's id and a path relative to the vault instead of an absolute path, and looks the note up again when reading; old indexes still work. If a note cannot be found it now says so instead of silently bringing back an empty note. Persona cards are found the same way after the vault moves.

## 0.1.10

- **Global chat (second part)**: receiving (catch-up subscription, verification, import into the queue, delivery status and receipts), read receipts sent in one batch without going through the AI, and observability (audit lines, worker status, clock skew, offline). Still being developed.
- **Prepared at install**: `aib chat prepare` installs the chat library and creates keys on this computer only (nothing goes online); the installer runs it after `aib init`, and a failure does not stop the install.
- **Tests**: the process-table warning tests also pass on Linux machines without a per-user process limit.

## 0.1.9

- **Global chat (first part)**: `aib chat` skeleton, keys, the roster, and sending to a global address (still being developed; the bridge that carries messages is not public yet).
- **Process-table warning**: when the processes of your user reach 80% of the system limit, the right pane shows a red warning with the top 3 commands, and fleet patrol prints it first (measured without starting new processes).
- **Persona sources**: `aib persona sources` entry point.
- **CI**: ordinary pull requests run macOS only on Python 3.12; full runs stay on `main`, integration and release branches.

## 0.1.8

- **Every AI is free**: the free edition no longer limits which AI tools you can use; every provider's sources can be turned on and no seat is refused or marked as paid.
- **Messages between AIs**: cross-host `aib dm` shows the real sender, works with older receivers and reuses one SSH connection (about 45 ms instead of 240–540 ms). Retries resend only to seats that did not get the message; options are no longer dropped; messages no longer vanish; a seat cannot send as another seat; a tool timeout no longer sends twice. When the receiving seat is busy, the message waits in its queue and is delivered when the seat is idle; messages held for a person are announced to the receiving seat when it is idle. A runaway loop is stopped after 8 nested calls.
- **Every AI brand is covered**: Claude, Codex, Grok, Copilot and Antigravity get the context-usage hook, and it is installed by the one-line install, `aib init` and `aib update` (not only when a seat opens; Codex asks you to trust it once with `/hooks`). API-key seats and bridged bots run the same table after each turn (seats whose usage cannot be measured only consolidate memory). A seat that ran `/clear` is still recognized.
- **Context usage is handled by the program**, from one table: memory is consolidated quietly at 30, 50, 70 and 90%; at 80% you are reminded to hand off; at 85% the closing steps (tidy → summary → `aib handoff` + verify) are required before a Claude seat can end its turn (write「先做」in your message to skip that one turn); 95% is the red line. The hook is now installed when a seat opens (it was never installed in the open-source layout).
- **Personas, projects, binding**: new persona cards are kept under their uid and shown by title; `aib newproj --approve` lets you approve a project's goals and red lines yourself; there is one route table, so re-binding another seat no longer drops yours; `bindme repair` reads back what it fixed; auto-binding no longer names things after status lines; `newrole` explains where the card is when binding fails.
- **Second-brain uid layout converter**: plan → apply → verify → undo; lists, links, attachments and `.obsidian` settings are converted; templates keep their names.
- **Opening seats**: a seat opened inside a cabin opens in that cabin; only defined roles are accepted; a missing AI tool is named with how to install and sign in, and no empty window is opened. The right pane marks bridged bots 🤖 and API-key engines 🔑.
- **English UI is English throughout** (`doctor`, `page`, `persona`, the bottom pane, every `--help`), with real Japanese and Korean; internal codes are gone.
- **Tests**: earlier green results are reused only when they still apply (12-hour limit on resuming, red results from other machines still count, a time-zone change reruns, damaged records repair themselves); a few tests that never reused results now do.
- **Install / doctor**: git installs get their hooks; uninstall keeps unsaved work; `aib doctor` marks what it cannot measure as a note instead of a failure.
- **Extensions**: `aib plugins licensed` for paid plugins; the five free extensions are signed, so `--allow-unsigned` is no longer needed.
- **Right pane and screenshots**: the load / CPU temperature / heavy-work readings at the top of the right pane work in the open-source version (the heat rulers ship now), and the Control+Shift+S screenshot helper ships and builds on Command Line Tools 26 (works around the duplicate SwiftBridging module).

## 0.1.7

- aib installed with pip in a virtual environment now updates itself when you open a seat, like pipx and one-line installs already did. **If you installed 0.1.6 or older with pip, run `pip install --upgrade ez2-aib` once.**
- `aib update` on a pip / pipx install now names the right package (`pipx upgrade ez2-aib` or `pip install --upgrade ez2-aib`).
- The captain bar label and the error messages of the captain tools follow your language (Japanese and Korean too), and the label is no longer cut off in English.
- Every release is now checked before it reaches PyPI: the built package is installed in a clean environment and upgraded to from the previous and the oldest available release; after the upload, the same checks run again against PyPI (pipx, pip and the one-line install) on Linux and macOS.

## 0.1.6

- The README now opens with where aib comes from and its slogan, **AI Basic to AI Best**: the easiest-to-use, most human foundation for AI tools. It also previews virtual buddies (a Claude Code mod first), which are still in development.
- Maintainer-only workflow tools are no longer shipped in the open-source version. Commands that rely on them say they are still in development instead of failing.
- The translation files only carry strings for features that ship with aib (about 600 unused strings removed).
- Every release is now checked for parts that should become plugins before it is published.

## 0.1.5

- Opening a seat now also checks aib itself: if a newer aib is out, it is updated in the same 30-second countdown as the AI tools (press s to skip); the new version is used from the next seat. `aib cli` lists aib too. `AIB_CLI_UPDATE=0` turns the check off.
- A brand-new computer no longer gets stuck: aib ships a default route contract and keeps its own vault in `~/.ez2-aib/vault` (created on first use), so `aib newrole` and the second-brain pane work without any extra setup.
- Your own vault (`EZ2_VAULT_DIR`) works too: `aib newrole` uses the default route contract when that vault is new or empty, has its own index, or already has `10-Roles`; otherwise it tells you the two ways to set it up instead of just failing.
- The one-line install runs `aib init` for you.
- Commands that are still in development now say so instead of showing a Python error.
- `aib doctor` is now reachable: it lists what this computer still lacks and how to fix it, checks only the AI tools you installed, and covers the second brain (vault, route contract, note-format modules).
- A mistyped command says "there is no … command" in your language and lists the common ones, instead of printing the full usage in four languages.
- The public repository's CI now installs aib into an empty home on Linux and macOS and runs a new user's first commands, so these dead ends cannot come back unnoticed.
- The ledger's event schemas ship with aib (earlier versions left them out, so anything that writes the ledger refused to run).
- `aib init` no longer crashes on its last step; Python cache files go to `~/.cache/ez2-aib` instead of the install folder.
- `aib room` (the Captain's Room, including `aib room contacts` and `aib room say`) is no longer in the open-source version; it is still in development and not open to the public yet.

## 0.1.4

- The cockpit color theme (`themes/aib-default.json`) is now included; earlier versions left it out, so the cockpit could not find its theme.
- `aib_dm` MCP gains three read-only tools for AI seats: `aib_inbox`, `aib_team`, `aib_tasks` (writing still goes through `aib_dm`).
- ClaudeBot read receipts: a message is marked read once it reaches Claude, instead of staying "delivered" forever.
- Bot bindings come back when a cabin restarts; bot title lines filter control characters; a GrokBot that fails while closing no longer stays stuck.
- Two small verify tools: `sync_receipt` (a receipt per host when syncing a fleet, failures loud) and `classify_env_blocked` (separates reds caused by a missing environment from real ones).
- The old ACB board backend is removed from `outline-to-tasks`.

## 0.1.3

- `aib killswitch on|off|status`: an emergency stop. While it is on, no AI can open a new seat, send `aib dm` or ship; if the switch cannot be read, it counts as on.
- The pre-push check runs the whole suite and then lists everything at once: new reds, tests that were already red, and tests that could not be verified here. `--fail-fast` brings back stopping at the first red.
- Captain's contacts: one list from four sources (who is online, search, your ten most frequent contacts), friends, groups, block and mute, delivery / read / handled / replying states for messages, sending to a group, and `aib room contacts`. `aib room say` speaks to the seats in the same room.
- Translation now uses the AI bound to the seat.
- `aib bindme`: see every seat's persona binding at once and fill in what is missing.
- Each seat's status line shows what it is working on (`aib now`): step, progress and a colored state.
- The screen stops refreshing while you are composing text with an input method.
- An old "main" cabin is no longer brought back by itself; it opens only when you ask for main.
- Connections to other hosts use safe ssh settings: your keys are not forwarded and all forwarding is off.
- Keys are masked before code is sent to an external AI for review.
- A cabin reconnects by itself when Terminal crashes or its window disappears, and a window that is stuck spewing errors is replaced with a new one. While an AI is writing, the input area says so, and you are warned when Terminal recently crashed during input-method typing.
- `aib top`: what is using this machine (top CPU users, which programs started each second and who started them). Log files are capped at 8 MB, compressed, three kept.
- A seat that switches Codex accounts three times in 30 minutes stops and waits; stopping programs by name never stops aib itself; the idle cleanup records what it stops.
- `aib handoff` checks the persona and project first and proposes a binding if one is missing; `aib bindme` reuses a fitting persona card before creating a new one.
- `aib test --guard` / `--scan`: the checks every push must pass are worked out from the rules instead of a hand-kept list.
- NVIDIA seats can rank models hourly and switch to the next model when one stops answering.
- Fixed: Grok seats come back with their own model; `lsof` not found on some Macs; leftover-process cleanup on macOS 27.
- `aib room` (the Captain's Room web page) is not in this version yet; `aib room contacts` and `aib room say` work in the terminal.
- Fixed: handoff receipts across midnight, `seat-info` crashing, a pinned cabin reopening after you closed it, and a flaky screen test.
- `aib uninstall` on a pip or pipx install now names the command that removes it (`pipx uninstall ez2-aib` / `python3 -m pip uninstall ez2-aib`) instead of calling it a developer copy.

## 0.1.2

- New `aib uninstall`: lists what it would remove first; `--yes` removes this aib, `--all` also removes its records (backed up first). Another aib on the same computer is never touched.
- One-line install on a computer that already has another aib: it no longer reports a failure or tells you to replace it. Your existing aib keeps working; the open-source copy sits next to it.
- The installer's hints now point to the GitHub address that works, and no longer show developer-only lines.
- Re-running the one-line install on Linux now says "updating" instead of "installing".
- The one-line install now installs the latest release (the newest `v*` tag), the same version as PyPI, instead of whatever was last merged; re-running it moves to a newer release once one is published, and never goes back.
- Releases: one workflow run creates the tag and the GitHub release (its notes are the CHANGELOG section) and, after a maintainer approves, uploads to PyPI; only one release runs at a time. CI runs on pull requests and on main only.

## 0.1.1

- Docs in four languages: README, CHANGELOG, CONTRIBUTING, SECURITY and both guides now come in English, 繁體中文, 日本語 and 한국어.
- The language links at the top of each document point to GitHub, so they also work on PyPI (they returned 404 there in 0.1.0).
- `aib --help` now opens with what aib is for: many AIs, one workspace.
- One-line install now works on Linux too: it installs the test safety net (`aib test`, `aib ship`); the cockpit still needs macOS.
- README shows how to get pipx first if you do not have it.
- CI also tests Python 3.14 and 3.15.

## 0.1.0 — first public release

- `aib test`: runs only the tests whose inputs changed and reuses earlier green results for the rest
  (Python, bash and Node tests; pytest and unittest). Every test gets its own temporary folder and home folder;
  leftovers and background programs are cleaned up and named in the summary.
- `aib ship`: pre-push check, packaging and pushing; tests that blocked an earlier push run first.
- Cockpit (tmux three panes, mouse menu, pop-up menu), fleet management, seat skills (handoff, new project,
  new role, new AI, new bot) and the bug-report skill are built in.
- `aib tmp` lists or removes what aib and its tests left in the temp folder, and nothing else.
- One-line install downloads `install.sh` from this repository; `aib update` tries the website copy first and falls back
  to this repository's copy if the website does not serve the open-source installer.
- **Revive**: bring a seat or a whole cockpit back in place after a crash or disconnect, and keep going.
  - It brings back the AI model the seat was using (conversation record first, then the old seat file, then the default).
  - Its first answer after reviving starts with a plain title, a summary of about 150 words and suggested next steps.
  - `aib restart` boot menu (macOS): after login, a terminal opens with the cockpits that can be restored — only if one did not come back.
- Panels pause redrawing while you are typing with an input method and have not sent the text yet, which avoids a macOS
  Terminal crash (needs the input method to report composing; without it panels redraw as usual).
- Right pane: once a seat is confirmed able to take input, an older "unconfirmed" no longer overrides it.
- `/aib view bot`: see bot seats' status from another seat (running or not, bound conversation, waiting mail, last send/receive, errors).
- The default model for Claude seats is now Opus 5.5.
