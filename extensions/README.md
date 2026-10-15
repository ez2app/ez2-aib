# Free extensions for aib

Each folder is one extension. Install it with `aib ext install extensions/<name>`. Remove it with `aib ext remove <name>`.

| Folder | Kind | What it does |
|---|---|---|
| `aib-boards` | mod (Claude Code) | `/aib-boards`: one computer per seat, with today's to-dos, projects, CTX and mail cards |
| `aib-languages` | mod (Claude Code) | `/aib-languages`: switch the aib language (繁體中文 · English · 日本語 · 한국어) |
| `aib-seat-hooks` | mod (Codex, Antigravity) | writes the seat's identity into the context when a seat opens and on every turn |
| `ez2-page-oneshot` | skill | build or refresh a whole ez2.page from one sentence (drafts only; you publish) |
| `aib-cabin-regroup` | skill | pull the seats of an older cabin into the cabin you are in |

The extensions that run code (mods and skills) are not signed yet. Until they are, install them with `--allow-unsigned`.

The status line is part of aib itself: run `bash aib/tools/statusline-install.sh` from a one-line install.
