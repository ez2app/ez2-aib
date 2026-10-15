[English](https://github.com/ez2app/ez2-aib/blob/main/README.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/README.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/README.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/README.ko.md)

# aib

### AI Basic to AI Best

**Every great AI starts from a good foundation.**

aib stands for *AI Basic*, and that is the whole idea: be the best foundation for AI. Every vendor's AI keeps getting stronger,
but putting them to real work still needs common ground: AIs that work side by side, each with its own persona and memory,
handing over without losing a beat, with a safety net under every change. aib is that ground. On it, you build your own best AI.

Our goal is **the easiest-to-use, most human-friendly foundation for AI tools**: one line to install, no architecture to learn.
Talk to your AIs the way you already do, and let aib handle the rest. We also want leading a team of AIs to feel like a game:
every AI is a virtual buddy you can see, so you can tell what it is busy with and how tired it is, and hand it work with a click
(virtual buddies will come first as a Claude Code mod; still in development and not open to the public yet).

**Many AIs, one workspace: bring in AI tools from any vendor for free, give every seat its own persona, expertise and
project memory, and let any AI pick up where another left off — even after a sudden disconnect.**

- **Bring in any vendor's AI, free.** aib is free and open source<sup>[1]</sup>. AI coding tools<sup>[2]</sup>,
  models you reach with an API key<sup>[3]</sup><sup>[4]</sup> and desktop AI apps (Claude, Codex Desktop, …)
  all open as seats<sup>[5]</sup> that work together. Each AI uses your own account or key; aib charges nothing on top.
- **Every aib has its own cockpit<sup>[6]</sup>; every cockpit has its own seats.** A cockpit is one tmux<sup>[7]</sup> window
  with several AI seats side by side, each with a role (`dev`, `qa`, `pm`, …). Seats message each other and hand work around.
- **Every AI has its own persona and expertise.** Each seat can have its own persona<sup>[8]</sup> (the first brain:
  personality, tone, working principles) and its own expertise (development, testing, planning, …).
- **More than one answer: AIs from different vendors check each other.** Give the same question to AIs from different
  vendors; each answers, then they review each other and point out weak spots and blind spots — you are not stuck with
  a single vendor's view.
- **Write to the second brain<sup>[9]</sup> any time.** In the middle of building or discussing, save key points and
  decisions to the second brain (project memory) whenever you like. Disconnects and crashes lose nothing; when an AI
  gets muddled after a long chat, just reopen the seat and its memory comes right back.
- **Any AI can take over.** A seat can run on Claude today and Codex tomorrow and keep the same persona and memory;
  when a conversation fills up, it leaves a hand-off note for whoever comes next.
- **Keep valuable conversations, fewer AI hallucinations<sup>[10]</sup>.** Points worth keeping from a conversation are
  distilled<sup>[11]</sup> into candidates for the persona and memory, and written in once you approve them. With facts
  written down to check, the AI guesses less from vague recall.
- **Everything stays on your machine.** Seat records, hand-off notes, personas and project memory all live on your own
  machine (`~/.ez2-aib/`<sup>[12]</sup>) and are never uploaded.

The cockpit runs on macOS with tmux. Many AIs on one codebase means many changes, so aib also ships a safety net
that works on its own too (Linux or macOS):

- `aib test` re-runs only the tests<sup>[13]</sup> a change affects (Python, bash and Node<sup>[14]</sup> are traced); the rest
  reuse their last **green**<sup>[15]</sup> result. Red results are never reused, and anything aib cannot measure always runs.
- `aib ship` is the pre-push check<sup>[16]</sup>: only a package with no new red is pushed. Releases can only be cut by a human
  at a terminal<sup>[17]</sup>.

**Notes**
- [1] Open source: the code is public; anyone may read, use and change it.
- [2] AI coding tools: coding assistants from each vendor — Claude Code (Anthropic), Codex (OpenAI), Antigravity (Google), GitHub Copilot (GitHub), Grok (xAI) and others.
- [3] API key: a secret string an AI service gives you, so a program can call that service on your behalf.
- [4] Models reached with an API key: for example DeepSeek, OpenRouter (one entry point to many models) and NVIDIA.
- [5] Seat: one AI's place in the cockpit — one AI per seat, with its own role and memory.
- [6] Cockpit: the workspace aib opens, with several seats side by side.
- [7] tmux: a tool that splits one terminal window into panes and keeps them running after you close the window.
- [8] Persona: the seat's personality, tone and working principles, saved as a persona card and loaded whichever AI opens the seat.
- [9] Second brain: the seat's project memory — a set of notes kept on your own machine.
- [10] AI hallucination: an AI stating something incorrect as if it were true.
- [11] Distill: pick the points worth keeping out of a conversation and turn them into rules or knowledge.
- [12] `~/.ez2-aib/`: a folder in your home folder where aib keeps its records.
- [13] Tests: small programs that automatically check that the code still works.
- [14] Python, bash, Node: three common programming languages / runtimes.
- [15] Green / red: a passing test is green, a failing one is red.
- [16] Pre-push check: running the affected tests before pushing code to the shared version (the main branch).
- [17] Terminal: the window where you type text commands (the Terminal app on macOS).

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh | bash
# installs the latest release into ~/ez2-aib; run the same line again any time to update (Linux: the test safety net only; the cockpit needs macOS)
aib update                                          # same thing, once aib is installed (aib update --tools also offers AI CLIs)
# other ways: pipx install ez2-aib    (no pipx yet? macOS: brew install pipx / Linux: python3 -m pip install --user pipx,
#                                      then pipx ensurepath and open a new terminal)
#             git clone https://github.com/ez2app/ez2-aib ~/ez2-aib && export PATH="$HOME/ez2-aib/bin:$PATH"

cd your-project
aib claude safe dev                  # open a Claude Code seat named dev (it asks before every action)
aib codex safe qa                    # and a Codex seat named qa next to it
aib test tests/test_something.py     # first run: runs and records
aib test tests/test_something.py     # nothing changed: reused
aib test --all                       # the whole suite, same rules
aib ship package --branch my-branch  # pre-push check, then push to main
```

Python<sup>[18]</sup> 3.9+ on Linux or macOS; the cockpit needs macOS and tmux. Windows is not supported (WSL2<sup>[19]</sup> is untested).
The first line is the one-line install<sup>[20]</sup>; the other ways use pipx<sup>[21]</sup>, git<sup>[22]</sup> and PATH<sup>[23]</sup>.

**Notes**
- [18] Python: a programming language; aib is written in it, so it must be installed first.
- [19] WSL2: a Windows feature that runs Linux inside Windows.
- [20] One-line install: `curl … | bash` downloads the installer and runs it right away.
- [21] pipx: a tool that installs Python command-line tools, each kept apart from the others.
- [22] git: the tool that records every change to the code; `git clone` downloads a whole copy.
- [23] PATH: the list of folders your computer searches for commands; adding a folder lets you type `aib`.

## The cockpit: several AIs side by side

The **cockpit** runs several AI coding assistants ("seats") side by side in one tmux window, each with a
role (`dev`, `qa`, `pm`, …), a left / center / right layout, mouse menus, seat-to-seat messages, hand-off notes when a
conversation fills up, and fleet tools<sup>[24]</sup> (seat identity, dispatch, patrol).

```sh
aib claude safe dev        # open a Claude Code seat named dev, asking before every action
aib codex dgrs qa          # open a Codex seat named qa in allow-all mode (see below)
aib --help-cockpit         # every option
```

- **Requirements:** macOS and tmux. Each AI's own command-line tool<sup>[25]</sup> must already be installed and logged in
  (`claude`, `codex`, `grok`, …); aib does not install or log in for you. The cockpit has not been tried on Linux yet.
- **bash tests on macOS:** aib traces bash scripts with bash 4.1 or newer. macOS ships bash 3.2, so tests that run bash scripts
  are reported as "not measured" and always rerun (safe, just slower). `brew install bash`<sup>[26]</sup> makes them reusable.
- **Permission modes, per seat:** `safe` keeps the AI tool's normal protections (it asks before editing files or running
  commands). `dgrs` turns those protections off **for that seat only**, so the AI acts without asking; aib prints a
  warning with the command to reopen the seat in `safe`. You choose for each seat when you open it.
- **Claude** is a trademark of Anthropic; Codex of OpenAI; Grok of xAI. aib is not affiliated with them.

**Notes**
- [24] Fleet tools: features for managing many seats at once — who each seat is, handing out work, regular patrols.
- [25] Command-line tool: a program you use by typing commands in a terminal (for example `claude`, `codex`).
- [26] `brew install bash`: installs a newer bash with Homebrew, the usual way to install software on macOS.

## Planned: across machines and social channels

> These are planned and not usable yet; each will be listed in the CHANGELOG<sup>[27]</sup> when it lands.

- **AIs on different machines talk to each other.** No need to cram every AI onto one computer: AI seats at home, at
  work or on a cloud host<sup>[28]</sup> can message each other.
- **Operate another machine's aib from your own.** From the aib on your computer, run another machine's aib cockpit
  through conversation, or send commands to the aib on that host.
- **Social conversation management.** Bring conversations from LINE, Telegram and other social platforms in and manage
  them; LINE and Telegram already work in internal testing and will open up later.

**Notes**
- [27] CHANGELOG: the record of what each version added or changed.
- [28] Host: a computer that runs programs — your laptop, an office machine, or a rented cloud server.

## In development: faster tests

> This part is still being developed; the numbers and behavior below may change.

aib remembers which files every test read; next time it reruns only the tests a change affects and reuses the last green
result for the rest.

**Measured: our own development of aib** (2026-10-08 to 10-10, 28 pushes<sup>[29]</sup>, about 1,020 tests each)

| How the tests run | Total for 28 pushes | Average per push | Saved vs. one at a time |
|---|---|---|---|
| Without aib, one test at a time<sup>[30]</sup> | 91.1 hours (about 3.8 days<sup>[31]</sup>) | 195 minutes | (baseline) 91.1 hours |
| With aib, same project in a single run<sup>[32]</sup> | 43.8 hours (about 1.8 days) | 94 minutes | saves 47.3 hours (about 2 days, 52%) |
| **With aib, same project split into 3 parts** | **16.6 hours (about 0.7 days)** | **36 minutes** | **saves 74.5 hours (about 3.1 days)** |

- From 91.1 to 43.8 hours is aib's reuse<sup>[33]</sup>: tests a change does not affect are not run again; down to 16.6 hours comes from splitting the remaining tests into 3 parts that run at once.
- 36 minutes is the average over these 28 pushes; once records built up, the last 10 pushes averaged about 18 minutes.
- Is reuse ever wrong? 13 randomly chosen reused tests were really rerun: all 13 gave the same result, 0 were wrongly reused.

**Expected savings per month**<sup>[34]</sup>

| How the tests run | About per month | Saved vs. one at a time |
|---|---|---|
| Without aib, one test at a time | 395 hours (about 16.4 days) | (baseline) 395 hours |
| With aib, same project in a single run | 190 hours (about 7.9 days) | saves 205 hours (about 8.5 days) |
| **With aib, same project split into 3 parts** | **72 hours (about 3 days)** | **saves 323 hours (about 13.5 days)** |

**Small projects (Linux, 4 cores)**

| Project | pytest<sup>[35]</sup> in parallel | aib, first run | Saved on each later run |
|---|---|---|---|
| [attrs](https://github.com/python-attrs/attrs)<sup>[36]</sup> (26 tests) | 8.2 s | 15.1 s | about 5 s (3.0 s when nothing changed) |
| [click](https://github.com/pallets/click)<sup>[37]</sup> (43 tests) | 6.2 s | 17.2 s | about 2 s (3.8 s when nothing changed) |

- The first run records which files each test read, so it is slower than pytest; from the second run on, unaffected tests are reused.
- "Saved on each later run" assumes nothing changed; the more files a change touches, the more tests rerun and the less is saved.
- **Good fit:** a suite that takes minutes or more, where each change touches only part of it. **Poor fit:** a suite that finishes in a few seconds.
- A side effect: each test runs on its own, which exposes tests that only pass after another test ran first.

**Notes**
- [29] Push: sending your finished changes to the version everyone shares.
- [30] One test at a time: 91.1 hours is an estimate that adds up each test's time measured under aib, including tracing overhead, so it runs high.
- [31] Day: counted as 24 hours (an AI can run around the clock).
- [32] Single run: aib runs only the affected tests, one after another; 43.8 hours is the sum of those tests' actual run times.
- [33] Reused: none of the files the test read changed, so its last green result is used instead of running it again.
- [34] Expected savings per month: projected from the measured pace (about 28 pushes a week, about 121 a month) × 52 ÷ 12; fewer pushes save less. Each push saves about 160 minutes compared with one at a time, about 101 of them from aib's reuse.
- [35] pytest: the most common Python testing tool; it runs all tests and reports which passed. "In parallel" runs several processes at once, its fastest mode.
- [36] attrs: a well-known open-source Python package that saves programmers from writing repetitive code.
- [37] click: a well-known open-source Python package for building command-line tools.

## Languages

Python (unittest<sup>[38]</sup>, pytest) is traced in full. Node (node:test, jest, mocha<sup>[39]</sup>), Go and PHP<sup>[40]</sup>
run through aib and get the pre-push check; how much can be reused depends on what the tracer sees — see
`docs/AIB-TEST-QUICKSTART.md` for the current table.
Other languages can declare their test command in `.aib/ship.toml`; they always run, but packaging and releases work.

**Notes**
- [38] unittest: Python's built-in testing tool.
- [39] node:test, jest, mocha: three common testing tools for Node.
- [40] Go, PHP: two more programming languages.

## How safe is reuse?

- Only green results are reused, and only when every input the test read is byte-identical.
- If aib cannot see what a test read (a subprocess<sup>[41]</sup> it cannot trace, network, a known native extension that reads
  files itself), the test is marked unmeasurable and always runs.
- **Known gap:** a C extension<sup>[42]</sup> that is *not* on aib's list and reads files from C code is invisible to Python's
  audit hooks<sup>[43]</sup>. On Linux the native safety net below catches this; **without it (macOS, or no C compiler<sup>[44]</sup>)
  a change to such a file may not trigger a rerun.** If your tests depend on files read that way, run `aib test --fresh`
  before releasing.
- `aib ship audit` re-runs a random sample of reused tests and compares.

**Notes**
- [41] Subprocess: a program that another running program starts.
- [42] C extension: a piece written in C that Python calls; it is fast, but Python cannot see the files it reads.
- [43] Audit hooks: Python's built-in "tell me when a file is opened" mechanism, which aib uses to record what tests read.
- [44] C compiler: the tool that turns C source code into a runnable program (for example `cc`, `gcc`, `clang`).

## Privacy

aib never sends your code, test results or anything else to us. Its records stay on your machine
(`~/.ez2-aib/`, plus a small cache in `~/.cache/ez2-aib/`). aib's own network traffic is what `aib ship` does with git:
fetch from and push to **your own** remote<sup>[45]</sup>.

The cockpit is different in one way: the AI seats you open are the AI vendors' own tools, and they talk to their vendors
as they always do. What the cockpit itself keeps on your machine, all under `~/.ez2-aib/`:
seat records and hand-off notes, seat-to-seat messages, project and role cards, and API keys you add for API-key seats
(in `~/.ez2-aib/secrets/`, folder 0700 / files 0600<sup>[46]</sup>, encrypted with a key stored next to them — this stops casual reading
and accidental commits, not someone who can already read your home folder). Delete `~/.ez2-aib/` to remove all of it.

What aib changes outside its own folders: when you open a seat, it adds a few hooks to that AI tool's settings so the
cockpit can follow the seat — `~/.claude/settings.json` (a status reminder at the start of a conversation, the seat
mailbox, the auto-answer for permission menus, the usage-based memory refresh), `~/.codex/hooks.json`,
`~/.grok/hooks/aib-*.json`, `~/.copilot/hooks/aib-*.json` and `~/.gemini/config/hooks.json`. Your own entries are left
alone, and the file is copied to `<file>.bak-…` before each change. The status reminder only reminds; it blocks the end
of a turn only for seats you list (or `*`) in `~/.ez2-aib/state/seat-now/gate-seats.txt`. `aib init` also puts a
shortcut to your notes on the desktop. `aib uninstall` takes all of these out again.

**Notes**
- [45] Remote (fetch / push): the copy of the code kept online (for example on GitHub); fetch downloads from it, push uploads to it.
- [46] 0700 / 0600: file permissions meaning only your own account can read and write.

## The native safety net (Linux only)

Some tests read files from C code (a native extension, a C library), which Python's audit hooks cannot see.
On Linux, if a C compiler (`cc`, `gcc` or `clang`) is on your PATH, aib compiles a small C file that ships with it
(`aib/tools/verify/ldp-shim/`) into `~/.cache/ez2-aib/` and preloads it (`LD_PRELOAD`<sup>[47]</sup>) **into test processes only**,
so those reads are recorded too. Without a compiler, or if the library cannot be loaded, the safety net stays off and
aib says why. **Then the gap described above is open:** reads done in C code go unrecorded, so a change to such a file
may not make its tests rerun. It is never available on macOS.
Turn it off with `AIB_NATIVE_GUARD=0`.

**Notes**
- [47] `LD_PRELOAD`: a Linux feature that loads a small piece into a program when it starts; aib uses it to record reads done in C.

## Keeping the temp folder clean

Tests often leave temp files behind, and sometimes background programs (a tmux server a test forgot to stop can run for
hours and eat gigabytes of memory). aib cleans up after the tests it runs:

- Each test gets its own temp folder (`TMPDIR`<sup>[48]</sup>, under `<tmp>/aibt-…`), deleted when the test ends.
- Background programs a test started and left running are stopped when it ends.
- The summary names the tests that left something behind, so you can fix them at the source.
- `aib tmp` lists what aib and its tests left in the temp folder and any leftover background programs from tests that
  already finished; `aib tmp --apply` removes them. It never touches anything that is not aib's, symbolic links, or
  anything a running program still uses. `aib ship` does this automatically before the pre-push check.

**Notes**
- [48] `TMPDIR`: the setting that tells programs where to put temporary files.

## CI

`aib ship ci` writes a GitHub Actions<sup>[49]</sup> workflow<sup>[50]</sup> that keeps aib's records in the Actions cache, so CI<sup>[51]</sup>
runs only what changed too. Pull requests<sup>[52]</sup> from forks<sup>[53]</sup> always run everything (they must not be able to touch the records).

**Notes**
- [49] GitHub Actions: GitHub's automation service; push code and it runs your tests automatically.
- [50] Workflow: the settings file for GitHub Actions that lists the steps to run.
- [51] CI (continuous integration): running the tests in the cloud automatically every time someone pushes code.
- [52] Pull request (PR): a request asking the project owner to merge your changes.
- [53] Fork: someone's own copy of your project under their account.

## Troubleshooting

| You see | What it means / what to do |
|---|---|
| `aib: there is no "…" command` | A typo or a command this version does not have. `aib --help` lists them all. |
| `… this feature is still in development` | The command (cross-host fleet, plaza, reports…) is still in development and not open to the public yet. Everything else works without it. |
| `aib newrole` / the second brain asks for `EZ2_ROUTE_REGISTRY` | You set your own vault (`EZ2_VAULT_DIR`, or you have `~/Desktop/Obsidian/Ez2`), so aib will not guess its folders. Either point `EZ2_ROUTE_REGISTRY` to your route contract, or unset `EZ2_VAULT_DIR` to use aib's own vault in `~/.ez2-aib/vault`. |
| The installer says another `aib` is already in `~/.local/bin` | Your existing aib keeps working; the open-source copy sits next to it in `~/ez2-aib`. To try the cockpit with it, use a separate macOS user account. |
| Anything else | `aib support` collects a de-identified report (nothing is sent); attach it to a GitHub issue. |

## Uninstall

```sh
aib uninstall                            # list what would be removed (nothing is touched)
aib uninstall --yes                      # remove this aib (one-line install); another aib on the same computer is left alone
aib uninstall --all --yes                # also remove aib's records and cache (~/.ez2-aib is backed up to ~/ez2-aib-backup-<time>.tar.gz first)
pipx uninstall ez2-aib                   # if you installed it with pipx
rm -rf your-project/.aib                 # only if `aib ship` created it and you no longer want it
```

`aib uninstall --yes` also takes out the hooks aib added to your AI tools' settings (only aib's own entries), the
`.bak-…` copies it made of those files, and the desktop shortcut to your notes. If the aib folder holds work that exists
nowhere else — uncommitted changes, untracked files, stashes, commits not pushed anywhere, other worktrees — that folder
is kept and the reason is listed; add `--discard-unsaved-work` only if you really want it gone. Extensions you added
with `aib ext` live in aib's folder: remove them first with `aib ext remove <name>`.

## Contributing and security

See `CONTRIBUTING.md` and `SECURITY.md`.

## Why local first

If all development depends on a remote host or an online platform, everything stops the moment it goes down or
cannot be reached. aib was born out of exactly that: we moved development off online platforms and back onto our own
computers, so records, memory and work in progress stay on the local machine and keep going when something remote fails.

The AIs themselves still connect to their vendors' services, but seat records, personas and memory live on your
computer — switch to another vendor's AI, or wait for the network to come back, and pick up right where you left off.

## History

AIB is the successor to **ACB**, which we started developing in 2024.
ACB's architecture no longer fit the way it is used today, so instead of patching it further we rebuilt it from the ground up as AIB.
`aib test` and `aib ship` are the open-source core of that rebuild.

## License

Apache License 2.0 — see `LICENSE`.
