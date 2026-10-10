[English](README.md) | [繁體中文](README.zh-TW.md)

# aib

**Run only the tests your change affects. Push only when nothing new is red.**

aib records, for every test run, which files the test actually read (Python, bash and Node are traced).
Next time, a test whose inputs did not change reuses its last **green** result instead of running again.
Red results are never reused, and anything aib cannot measure always runs.

On top of that, `aib ship` gives you a pre-push check: run the affected tests, and if something is red,
re-run it on the main branch to tell an old failure from a new one. Only a package with no new red is pushed.
Releases (version bumps) can only be cut by a human at a terminal.

## Quick start

```sh
(curl -fsSL https://aib.ez2.app/install.sh || curl -fsSL https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh) | bash
# macOS: installs into ~/ez2-aib; run the same line again any time to update (the GitHub copy is used if the website is unreachable)
aib update                                          # same thing, once aib is installed (aib update --tools also offers AI CLIs)
# other ways: pipx install git+https://github.com/ez2app/ez2-aib
#             git clone https://github.com/ez2app/ez2-aib ~/ez2-aib && export PATH="$HOME/ez2-aib/bin:$PATH"

cd your-project
aib test tests/test_something.py     # first run: runs and records
aib test tests/test_something.py     # nothing changed: reused
aib test --all                       # the whole suite, same rules
aib ship package --branch my-branch  # pre-push check, then push to main
```

Python 3.9+ on Linux or macOS. Windows is not supported (WSL2 is untested).

## When does aib pay off?

aib runs each test file in its own traced process. That costs a little per file, and it pays off when the
suite is slow and most changes touch only part of it. Real numbers (Linux, 4 cores, wall-clock seconds):

| Project | test files | pytest (1 process) | pytest `-n auto` | aib, first run | aib, nothing changed | aib, a core file changed |
|---|---|---|---|---|---|---|
| a large private project | 983 | about 3.3 hours (one file at a time) | — | — | — | 54 minutes for a typical push (`aib ship`) |
| [attrs](https://github.com/python-attrs/attrs) | 26 | 18.8 s | 8.2 s | 15.1 s | **3.0 s** | 11.7 s |
| [click](https://github.com/pallets/click) | 43 | 7.0 s | 6.2 s | 17.2 s | **3.8 s** | 15.2 s |

- **Worth it**: suites that take minutes or more; changes that touch only some modules; CI where each push re-runs everything.
- **Not worth it**: a suite that already finishes in a few seconds — a change to a file every test imports re-runs
  everything, and then aib is slower than plain pytest.
- A side effect: aib runs every test file on its own, so it exposes tests that only pass after another test ran first
  (click has one: `tests/test_utils/test_echo_via_pager.py` fails when run alone, with plain pytest too).

## Languages

Python (unittest, pytest) is traced in full. Node (node:test, jest, mocha), Go and PHP run through aib and get the
pre-push check; how much can be reused depends on what the tracer sees — see `docs/AIB-TEST-QUICKSTART.md` for the current table.
Other languages can declare their test command in `.aib/ship.toml`; they always run, but packaging and releases work.

## The cockpit: AI seats in tmux

`aib` also runs a **cockpit**: several AI coding assistants ("seats") side by side in one tmux window, each with a
role (`dev`, `qa`, `pm`, …), a left / center / right layout, mouse menus, seat-to-seat messages, hand-off notes when a
conversation fills up, and fleet tools (seat identity, dispatch, patrol).

```sh
aib claude safe dev        # open a Claude Code seat named dev, asking before every action
aib codex dgrs qa          # open a Codex seat named qa in allow-all mode (see below)
aib --help-cockpit         # every option
```

- **Requirements:** macOS and tmux. Each AI's own command-line tool must already be installed and logged in
  (`claude`, `codex`, `grok`, …); aib does not install or log in for you. The cockpit has not been tried on Linux yet.
- **bash tests on macOS:** aib traces bash scripts with bash 4.1 or newer. macOS ships bash 3.2, so tests that run bash scripts
  are reported as "not measured" and always rerun (safe, just slower). `brew install bash` makes them reusable.
- **Permission modes, per seat:** `safe` keeps the AI tool's normal protections (it asks before editing files or running
  commands). `dgrs` turns those protections off **for that seat only**, so the AI acts without asking; aib prints a
  warning with the command to reopen the seat in `safe`. You choose for each seat when you open it.
- **Claude** is a trademark of Anthropic; Codex of OpenAI; Grok of xAI. aib is not affiliated with them.

## How safe is reuse?

- Only green results are reused, and only when every input the test read is byte-identical.
- If aib cannot see what a test read (a subprocess it cannot trace, network, a known native extension that reads
  files itself), the test is marked unmeasurable and always runs.
- **Known gap:** a C extension that is *not* on aib's list and reads files from C code is invisible to Python's
  audit hooks. On Linux the native safety net below catches this; **without it (macOS, or no C compiler) a change
  to such a file may not trigger a rerun.** If your tests depend on files read that way, run `aib test --fresh`
  before releasing.
- `aib ship audit` re-runs a random sample of reused tests and compares.

## Privacy

aib never sends your code, test results or anything else to us. Its records stay on your machine
(`~/.ez2-aib/`, plus a small cache in `~/.cache/ez2-aib/`). aib's own network traffic is what `aib ship` does with git:
fetch from and push to **your own** remote.

The cockpit is different in one way: the AI seats you open are the AI vendors' own tools, and they talk to their vendors
as they always do. What the cockpit itself keeps on your machine, all under `~/.ez2-aib/`:
seat records and hand-off notes, seat-to-seat messages, project and role cards, and API keys you add for API-key seats
(in `~/.ez2-aib/secrets/`, folder 0700 / files 0600, encrypted with a key stored next to them — this stops casual reading
and accidental commits, not someone who can already read your home folder). Delete `~/.ez2-aib/` to remove all of it.

## The native safety net (Linux only)

Some tests read files from C code (a native extension, a C library), which Python's audit hooks cannot see.
On Linux, if a C compiler (`cc`, `gcc` or `clang`) is on your PATH, aib compiles a small C file that ships with it
(`aib/tools/verify/ldp-shim/`) into `~/.cache/ez2-aib/` and preloads it (`LD_PRELOAD`) **into test processes only**,
so those reads are recorded too. Without a compiler, or if the library cannot be loaded, the safety net stays off and
aib says why. **Then the gap described above is open:** reads done in C code go unrecorded, so a change to such a file
may not make its tests rerun. It is never available on macOS.
Turn it off with `AIB_NATIVE_GUARD=0`.

## Keeping the temp folder clean

Tests often leave temp files behind, and sometimes background programs (a tmux server a test forgot to stop can run for
hours and eat gigabytes of memory). aib cleans up after the tests it runs:

- Each test gets its own temp folder (`TMPDIR`, under `<tmp>/aibt-…`), deleted when the test ends.
- Background programs a test started and left running are stopped when it ends.
- The summary names the tests that left something behind, so you can fix them at the source.
- `aib tmp` lists what aib and its tests left in the temp folder and any leftover background programs from tests that
  already finished; `aib tmp --apply` removes them. It never touches anything that is not aib's, symbolic links, or
  anything a running program still uses. `aib ship` does this automatically before the pre-push check.

## Paid plugins and licenses

The core (`aib test`, `aib ship`, the cockpit) is free and open source. Some extra plugins are sold separately; they need
a license file signed by Ez2. Licenses are checked offline with a public key that ships with aib (Ed25519); editing a
license file makes it invalid.

```sh
aib license status       # what this machine is licensed for
aib license device-id    # this machine's id for that license (send it to us if your license is bound to machines)
aib license activate     # licenses that activate online: register this machine
aib license deactivate   # free this machine's slot before you move to a new computer
```

The device id is derived from your machine's hardware id and the license id, hashed together; the hardware id itself is
never shown, sent or stored, and the same machine gives a different id under a different license. Online activation
sends exactly two things: the signed license and that device id.

## CI

`aib ship ci` writes a GitHub Actions workflow that keeps aib's records in the Actions cache, so CI runs
only what changed too. Pull requests from forks always run everything (they must not be able to touch the records).

## Uninstall

```sh
rm -rf ~/ez2-aib ~/.local/bin/aib        # if you used the one-line install
pipx uninstall ez2-aib                   # if you installed it with pipx
rm -rf ~/.ez2-aib ~/.cache/ez2-aib       # aib's records and cache (outside your project aib writes only these, plus temporary files in the system temp folder)
rm -rf your-project/.aib                 # only if `aib ship` created it and you no longer want it
```

## Contributing and security

See `CONTRIBUTING.md` and `SECURITY.md`.

## History

AIB is the successor to **ACB**, which we started developing in 2024.
ACB's architecture no longer fit the way it is used today, so instead of patching it further we rebuilt it from the ground up as AIB.
`aib test` and `aib ship` are the open-source core of that rebuild.

## License

Apache License 2.0 — see `LICENSE`.
