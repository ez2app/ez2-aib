[English](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.ko.md)

# How much time aib saves when you push: a guide for users

In one sentence: **before every push, aib reruns only the tests that this change can actually affect and reuses the last results for the rest; every time, it tells you how much this push saved and how much you've saved in total since you started.**

(Command details are in section 6 of `AIB-TEST-QUICKSTART.md`; this guide covers only what you'll see, how to read it, and how to save the most.)

---

## 1. Getting started (about 5 minutes, plus the time to run all your tests once)

```bash
cd ~/your-project
aib ship init
```

It does the following in order, **without pushing or committing**:
1. Shows the project name, finds where the version is kept, and finds your tests.
2. Confirms that the tests test **the code in this folder** (not an installed package of the same name somewhere else); if not, it tells you right away instead of pretending to pass.
3. Runs all tests once and records which files each test read. This run saves no time; it builds the records.
4. Tells you roughly how much you'll save each time from now on, and which files affect the most tests when changed.

## 2. Before each push, you'll see a preview like this

Below is a real push from the aib project itself (**an example; your project's numbers will differ**):

```
This pre-push check: 929 tests
├─ without aib (one test after another): about 3.2 h
├─ with aib (this machine): about 10 min — started 07:01, expected to finish 07:11
└─ more machines together (planned, projected): 2 machines about 8.3 min, 4 machines about 8.3 min
```

How to read it:
- **Without aib**: how long all your tests would take run one after another (the sum of each test's last real run time).
- **With aib**: this time only affected tests run, split into shards that run side by side; the expected duration and finish time.
- **More machines**: a projection of how much faster it would be if idle machines shared the work (the slowest single test is the floor, so beyond some number of machines it gets no faster).

## 3. The reconciliation after the run

```
This pre-push check, in the end
├─ without aib you would wait: about 3.2 h
├─ with aib it actually took: 11.4 min (3 h less waiting, about 94%)
│  └─ extra savings from shadow resume: about 52.7 min (44 tests reused)
└─ since the first run: 6 pushes; without aib 19.6 h, actual 4.9 h, 14.7 h less waiting in total
```

- "Less waiting" has two parts: what **running side by side** (several shards at once) saved, and what **shadow resume** (unaffected tests reuse their last result) saved on top of that.
- "Since the first run" is the running total since you first used aib; every run is added as it actually happened, not estimated.
- For per-run and per-week details: `aib ship stats`.

## 4. When you save the most (and what you can do)

| What you changed | What happens | What you can do |
|---|---|---|
| Ordinary code | only tests that read it rerun; the biggest savings | push as usual |
| Files many tests read (shared config, shared utilities) | every test that reads it reruns | **batch changes to these files into one push**, so other pushes can reuse heavily; `aib ship hotfiles` lists which files they are |
| Translation dictionaries (one key per string) | only tests that **use those strings** rerun | push as usual; if a test compares or copies the whole dictionary, that test still reruns (correctly) |
| Package lock files (requirements, lock files) | everything reruns | upgrade packages all at once, not in several steps |

The last lines of `aib ship stats` use **your own push history** to tell you: for pushes that changed only ordinary code, how many tests were reused on average and what percentage of total test time that was,
and how many of your pushes fell into the "everything reruns" cases.

## 5. Three things you must know

1. **A red result is never reused as green.** A result is reused only if "it was green last time and none of the files it read changed"; tests that read something that can't be measured really run every time.
2. **"Unverified" is not green.** Tests this machine lacks a tool for (e.g. a Mac-only command), or that can't be measured, are honestly listed as "unverified" and never counted as passing.
3. **To check that reuse is correct**: `aib ship audit` really reruns a sample of "reused" tests, compares results, and records them in the totals.

## 6. Not yet supported

- Windows.
- Languages other than Python: how far support goes is in the language table in `AIB-TEST-QUICKSTART.md` (tested with Node, jest/mocha, Go and PHP; vitest runs correctly but reruns every time; Ruby, Rust, Java etc. need the command declared yourself and really run every time).
- Time-dependent tests that only look at the clock and read no files (e.g. "older than 30 days counts as expired") are covered only by record expiry, so they may not rerun until up to 14 days later.

