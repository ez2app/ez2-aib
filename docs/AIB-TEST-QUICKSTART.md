[English](AIB-TEST-QUICKSTART.md) | [繁體中文](AIB-TEST-QUICKSTART.zh-TW.md)

# Getting started with `aib test`: rerun only the tests that changed

> For first-time users. In one sentence: **if none of the files a test read and nothing in its runtime environment changed, the last green result is reused; red always reruns; anything that can't be measured always reruns.**
> So the worst a mistake can cost you is "no savings" — it will never report broken code as good.

## 1. How to run it

```bash
aib test tests/test_foo.py tests/test_bar.py      # run these tests (reuse where possible)
aib test --fresh tests/test_foo.py                # really run everything this time (still records results)
aib test --why tests/test_foo.py                  # don't run; just say whether it would be reused, and which file blocks it
aib test --repo ~/my-project tests/test_x.py      # run in another repository
aib test --estimate                              # don't run; estimate savings per commit from this repo's last 50 commits (needs one prior run)
```

One line per test: `✓ … (reused the last green result)`, `▶ … running`, `└ green` / `└ ✗ RED`; the last line is the total. Output of red tests is printed under the final table.

### Using it in your own project (with its own virtual environment)

```bash
cd ~/my-project
source .venv/bin/activate                          # or: if the repo has .venv/ or venv/, it is used automatically, no need to activate
pip install pytest -e .                            # install everything the project and its tests need into this environment
aib test --repo . tests/test_*.py
```

Which python is used: `AIB_TEST_PYTHON=<path>` > the repo's `.venv/` or `venv/` > an activated `VIRTUAL_ENV` > aib's own.
When it isn't aib's own python, a line is printed at the start: `ℹ Test python: … (version …; pytest …; from: …)`.
That python's version and whether it has pytest count as the "runtime environment": change them and everything reruns. Upgrading a package does too (the package files it reads have changed).
Measured (itsdangerous, 5 tests): first round 3.2 s, everything ran; second round 0.4 s, everything reused; changing one line of `signer.py` ⇒ all 5 reran.

Measured on external projects (10 of them; red/green matched running pytest directly in every case): itsdangerous, markupsafe, more-itertools, click, dateutil,
Flask (19.5 → 5.3 s), requests, django-taggit (46 → 12 s), pytest-xdist (99 → 74 s), humanize (12 → 2.4 s).
All are libraries, and all were measured on Linux only.

## 2. Supported tests

| Kind | How it runs | Status |
|---|---|---|
| Python, file ends with `if __name__ == "__main__":` | executed directly | stable |
| Python, pytest style (without the block above; includes `class TestXxx:`, `async def test_`) | pytest if that python has it; otherwise a small built-in runner runs the `test_*` functions | usable; uses a pytest fixture but pytest isn't installed ⇒ marked red with a message to install it |
| Node (`.js` `.mjs` `.cjs`, using `node:test`) | `node <file>` | first version (all 12 shared acceptance cases pass) |
| JS/TS with jest / mocha (detected from the root package.json; `node_modules/.bin/` must be installed) | jest: `--runInBand --no-watchman`, one file at a time; mocha: `mocha <file>` | usable (luxon, 23 tests, and cliui: red/green matches running the framework directly; on the second round all greens were reused) |
| JS/TS with vitest | `vitest run --pool=forks <file>` | runs correctly but **can't be measured, reruns every time**: vitest 4 / vite 8 find files with native code the tracer can't see |
| bash scripts (started by Python/Node tests) | follow the parent process | stable |
| Go (`go.mod` at the repo root; `*_test.go`) | `go test ./<that file's directory>`, using Go's own cache (this machine's real GOCACHE / GOMODCACHE) | usable (google/uuid: about 0.25 s per test; breaking the code ⇒ all red). Works per package: files in the same directory go red/green together; modules must be downloaded first (`go mod download`) |
| PHP (phpunit in composer.json; `*Test.php` under `tests/`) | `php vendor/bin/phpunit <file>` (run `composer install` first) | usable (sebastian/diff, 17 tests, red/green matches running phpunit directly); the tracer can't read it ⇒ always really runs |
| Other languages (Ruby, Rust, Java…) | declare in `.aib/ship.toml`: `[test]` `files = ["spec/**/*_spec.rb"]`, `command = "bundle exec rspec {file}"` (`{file}` is replaced by that test's relative path) | the tracer can't read it ⇒ always really runs, but pushing packages, releases and the reason ranking all work; for Ruby (minitest/rspec), Go and JS, a summary line saying "nothing ran" is recognized ⇒ listed as unverified (tested with minitest) |

## 3. When results are reused, and when a test always reruns

Always reruns:
- Last time it was red, timed out, or flaky (has been red under the same conditions).
- Any file the test read has changed content, a directory it listed gained or lost files, or a file it looked for and didn't find now exists.
- The runtime environment changed (language version, environment variables, package lock files).
- It touched the network, or started a program that can't be measured (a language with no hook, a child process that was killed).
  Databases: drivers written in C (psycopg2, mysqlclient, grpc, pyzmq, pycurl…) open their own connections without going through Python's socket,
  so merely loading one counts as touching the network. (Earlier versions missed this ⇒ results were reused even when database contents changed.) Pure-Python clients go through socket and were always caught.
- The record expired: 3 days the first time; if it reruns and is still green, the period doubles (6, 12 days), up to 14 days.
- A file the test read contains a date, and that day has passed since the last run.

**Translation files are tracked per string (no setup needed)**: when a project translates with gettext (Django, Flask-Babel and babel all do),
a test that looks up text only through gettext reruns only when **the specific strings it used** change; editing other strings or adding new ones ⇒ reused.
If the test reads the .mo file directly, enumerates the whole catalog, or the translation file is broken ⇒ the whole file counts as before (rerun). .mo is compiled from .po: after editing .po, recompile .mo first.

Projects that keep dictionaries in JSON and write their own lookup functions: declare which files are dictionaries in `.aib/ship.toml` (patterns relative to the repo root):

```toml
[test]
keyed_files = ["locales/*.json"]
```

After that, when a test reads these files with `json.load` / `json.loads` and only uses some keys ⇒ it reruns only when those keys (including the whole value tree under them) change.
Comparing, enumerating, copying or stringifying the whole thing, or content altered before parsing (e.g. Windows line endings converted) ⇒ the whole file counts as before. Undeclared JSON always counts as a whole file.

## 4. Three switches

- Really run everything this round: `AIB_TEST_NO_REUSE=1 aib test …`
- Always really run one test: put `# aib: always-run` in the first 40 lines of the test file
- Don't run what this machine can't do (marked "⊘ unverified", counted as neither green nor red): put in the first 40 lines of the test file
  `# aib: needs darwin`, `needs linux`, `needs ipv6`, `needs tool:<program>`, `needs repo:<name of a sibling repo>`
  ★ A condition may only state a *prerequisite* for the test, never *the thing being tested itself*; otherwise, when it's missing, skipping the test just hides the problem.

## 5. "Unverified" is always visible

- `⊘ … (unverified: this machine lacks …)`: this machine is missing a prerequisite, so it didn't run. Listed on its own line in the summary table; the batch exits with code 4.
- `⊘ N cells skipped, unverified`: a green test contains skipped cases (`skip`). Listed on its own line in the summary table.
- Exit codes: 0 all green; 1 some red; 3 records were not saved; 4 some unverified.

## 6. Pushing to main and releasing: `aib ship` (works for any project, no setup needed)

A plain-language guide for users (what you'll see each time, how to read it, how to save the most): `AIB-SHIP-FOR-USERS.md`.

```bash
cd ~/my-project
aib ship init                     # first step for new users: show the project name and tree, find the version and tests, check that the tests test this tree,
                                  #   run everything once to build records, estimate future savings, list the files that affect the most tests;
                                  #   doesn't push or commit (--no-run only detects)
aib ship package --preview        # doesn't run or push: how many tests are expected to be reused, how long it will take, which file is the main cause
aib ship hotfiles                 # the 10 files read by many tests and changed often (tests reading it × recent changes): batch their changes into one push to save the most
aib ship package                  # push one package to origin's main: version rules → fixes must come with tests → pre-push check → push
aib ship watch                    # leave it running while developing: 10 s after you stop saving, it runs tests in a mirror tree (your working directory is untouched); at push time those results are reused
aib ship stats                    # running totals since the first run (total tests/cases, actual savings per run and per week) + when pushes save the most and what to do
# The pre-push check starts by comparing: how long without aib (one test after another), how long with aib on this machine (and what time it will finish), and a projection with more machines;
#   when done it reconciles (savings from running side by side / extra savings from shadow resume)
aib ship stats --all              # one line per project on this machine (total tests/cases, number of pushes, savings) + grand total
aib ship audit                    # really rerun a sample of "reused" tests and compare results (is reuse correct?); results go into the totals and are listed under each week
aib ship stale [--run]            # tests not really run for over 7 days (the pre-push check only picks affected tests, so untouched ones can go a long time without running, and reds hide there);
                                  #   --run runs them all at once. Recommended weekly (in one catch-up run, 5 tests turned out to have been red all along and 14 had never run in the cloud)
aib ship why                      # tests that "always have to rerun", ranked by reason (count, seconds spent per round), with a plain explanation and what to do
aib ship crosscheck               # check against the whole suite: aib runs one test at a time; this first runs them one by one (reusing where possible), then runs the whole suite once the project's usual way,
                                  #   and compares red/green file by file. A mismatch = tests affect each other (shared state, execution order), so aib's green doesn't mean a full CI run will be green.
                                  #   Supports pytest, jest, vitest, mocha; exit code 0 match / 1 mismatch / 4 couldn't compare. Recommended weekly
aib ship check                    # check main's history for commits that break the rules (for CI)
aib ship release --version 1.4.0  # only a human at a terminal: preview first, release only after typing the version
aib ship package --emergency      # only a human at a terminal: type a reason to skip the pre-push check once; the next package is verified automatically, and no release until then
```

- **Which files count as tests**: for Python, `test_*.py`, `*_test.py`, and `test-*.py` under `tests/`; **if pytest sets `testpaths`, only those paths count**
  (e.g. in Flask, `examples/` holds separately installed examples that pytest doesn't run anyway). For JS/TS (`package.json` at the repo root): `*.test.*`, `*.spec.*`, and files under
  `__tests__/`; mocha projects also count `.js/.mjs/.cjs` directly in `test/`.
- **Where the version is found automatically**: `VERSION` → `pyproject.toml` → `package.json` → `setup.cfg` → a single `__version__`.
  The first push writes what it found to `.aib/ship.toml` (as one commit added to that package); after that it follows that file, which you can edit. Not found ⇒ you can't release, but pushing packages works as usual.
- **A package may not change the version**; only `release` (a human) changes it. So regular pushes only run affected tests, and the version moves once per batch.
- **Code changed but no test changed ⇒ no push**, unless a commit message contains a line `No-Test-Reason: <reason>` (pure refactoring, text-only changes).
- **Pre-push check**: tests likely to fail run first, and it stops at the first red; **any red blocks the push** (exception in aib's own repository: old reds that are also red on main are let through).
  A new red is run again on the same code; if it goes green it's marked "on-and-off" (still not pushed). At the end it prints the 10 slowest tests and the estimated savings.
- **After a reboot, run the same command again**: tests already green on the same code won't run again.
- **What was tested isn't the code being pushed ⇒ no push**: if the test python loads your package from outside this tree (shared venv, `pip install -e` pointing at another clone) ⇒
  the pre-push check reports "unverified" right away. Create the project's own `.venv` and `pip install -e .`, or set `AIB_TEST_PYTHON`.
  Projects installed with `pip install -e .` can push packages, but `aib ship watch` can't be used (the mirror tree can't test the code in the mirror, and it says so).
- **A bad release went out**: `git revert` the bad commits and push them as one package (the fixes-need-tests rule still applies), then have a human release the next version. Don't roll the version back.

## 6.5 Reuse in CI: save GitHub Actions minutes

GitHub's machines bill by the minute, and minutes run out quickly. You can save without your own machines (self-hosted runners) — one command generates the workflow file:

```bash
aib ship ci            # writes .github/workflows/aib-test.yml (won't overwrite an existing one; --force rewrites, --print only prints)
```

It still runs on GitHub's machines, and saves in these ways:

| Where it saves | How |
|---|---|
| Tests that weren't affected don't run | aib's record pool is stored in GitHub's cache (same branch first, then the default branch), so the next run reuses the last green |
| Runs that shouldn't happen don't happen | runs only on pushes to the default branch and on PRs; changes only to `docs/` or `*.md` don't run; a new push to the same branch cancels the older run |
| Installation is cheaper too | pip / npm downloads are cached (small projects spend most of their time installing, not testing) |
| You can see how much was saved | after `aib test --all`, "Without aib → with aib" is added to each run's summary page (the Summary of that run on the Actions web page), plus three lines: **billed minutes** (GitHub rounds each job up to a whole minute; small projects often save seconds but not billed minutes), what "reused" means, and which tests really ran this time |

**PRs from other people (contributors who don't use aib)**:
- Contributors don't need to install aib; the summary page says: "Reused = every file this test reads is unchanged, so its last green still holds; changed, new and previously red tests always run."
- **PRs only read the cache, never write it**: only pushes to the default branch and the weekly full run write it ⇒ many PRs can't crowd out main's records.
- **PRs from forks don't use aib and run everything the normal way** (`python -m pytest` / `npm test` / `go test ./...` / `vendor/bin/phpunit`):
  forks can't get secrets; and a PR's test code runs on the same machine as the record pool, so it could rewrite the record files and fake its own "reused green" for that run.
  Main isn't affected (main can't read a cache written by a PR), but reviewers would see a fake green ⇒ forks never reuse.
- **Every Sunday, everything really runs once** (`--fresh`): refreshes timings and catches occasionally-red tests that have been "hidden by long reuse".

Simulated two CI runs (a fresh clone to the same path each time, with only the cache carrying the record pool between them; itsdangerous, 5 tests):
first run 6.5 s (everything ran and records were built) → second run 0.9 s (all 5 reused; the summary page printed "3.8 s less waiting, about 81%"); the running total travels with the cache.
**Not yet run on real GitHub Actions** (only simulated locally).

- `aib test --all`: runs all tests found the way aib ship finds them (pytest testpaths, JS/Go/PHP, `.aib/ship.toml` declarations);
  **if no tests are found ⇒ exit code 2** (CI must not go green when nothing was tested).
- aib itself is pinned to one commit (`AIB_REF` in the file): a new aib version invalidates all records once, so without pinning every aib update would rerun everything.
  If your copy of aib has commits not yet pushed to GitHub ⇒ it isn't written (CI couldn't fetch it). If the aib repository is private ⇒ add `AIB_TOKEN` under the project's Settings → Secrets.
- The install line (something like `pip install -e . pytest`) is a guess; change it to fit your project.
- **The path must stay the same**: aib identifies a repository by the real path of its git directory. GitHub Actions and GitLab CI check out to the same path every time;
  if your own CI uses a different directory name each time, nothing is reused (results are still correct, just no savings).
- **Don't let caches written by other branches be used by main**: the "last green" in the record pool is what lets tests be skipped. GitHub's cache is isolated per branch
  (main can't read a cache written by a PR), and the generated file relies on that; for other CI systems, check this yourself.
- Red runs are cached too (red rows always rerun; the green tests can be reused next time).
- First run with no cache ⇒ everything really runs; if the machine environment changes (python version, packages, GitHub runner image updates) ⇒ everything reruns, same as locally.
- GitHub's cache has a size limit per repository and entries unused for a while are evicted; once evicted, the next run really runs everything.

## 7. Not yet supported / caveats

- Time dependence where a test **only reads the clock and no files** (e.g. "older than 30 days counts as expired") is covered only by record expiry, so it may take up to 14 days to be noticed.
- Node: worker_threads and native addons (`.node`) are always treated as unmeasurable; TypeScript run through a compile cache hasn't been verified.
- **C libraries that read files themselves** (not through Python's open, so the tracer can't see it): already handled are lxml (parse/iterparse), OpenCV (imread),
  pyarrow (reading parquet/csv/feather/dataset) — the file is recorded when it is read by path; h5py, netCDF4, PyTables, GDAL/rasterio/fiona and soundfile
  are treated as unmeasurable as soon as they are loaded (rerun every time).
  **C extensions not on the list**: on Linux with a C compiler, aib attaches a small library to each test process (a safety net, LD_PRELOAD) that records file opens at the C level
  and compares afterwards — "C level read a file in the repo that Python didn't record" ⇒ that test reruns every time (`aib ship why` names the file).
  On macOS, without a compiler, or for programs like Go that bypass libc ⇒ no safety net, and C extensions not on the list remain a known limitation. To turn it off: `AIB_NATIVE_GUARD=0`.
  Node processes are compared too; but when Node resolves modules it reads `node_modules/**/package.json` without going through the hook, so that part is left to the lock file (a changed lock file reruns everything) —
  **if you edit files inside node_modules by hand without the lock file changing**, aib may not notice (known limitation).
- Windows is not supported.
- Tests that call git: reading history (log/show/cat-file/ls-tree…, where the revision is an ancestor of HEAD), reading the working tree (status/diff/grep), reading config,
  and naming branches/tags can all be measured; anything that modifies the repo (commit/add/checkout…), targets another repo, or reads outside the repo like `--ignored` is treated as unmeasurable.
- Tests that replace PYTHONPATH entirely and then start a python subprocess: the tracer appends itself to the end automatically; if the test brings its own sitecustomize, that test is honestly recorded as unmeasurable.
- Sharing records across machines requires setting up a trusted machine list first (advanced).
