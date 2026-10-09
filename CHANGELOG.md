[English](CHANGELOG.md) | [繁體中文](CHANGELOG.zh-TW.md)

# Changelog

Versions follow the `version` in `pyproject.toml`; a release is a `v<version>` tag, created by a maintainer.

## 0.1.0 — first public release

- `aib test`: runs only the tests whose inputs changed and reuses earlier green results for the rest
  (Python, bash and Node tests; pytest and unittest). Every test gets its own temporary folder and home folder;
  leftovers and background programs are cleaned up and named in the summary.
- `aib ship`: pre-push check, packaging and pushing; tests that blocked an earlier push run first.
- Cockpit (tmux three panes, mouse menu, pop-up menu), fleet management, seat skills (handoff, new project,
  new role, new AI, new bot) and the bug-report skill are built in.
- Paid plugins are separate (installed into `~/.ez2-aib/plugins/`); `aib license` verifies Ed25519-signed licenses,
  device binding and online activation.
- `aib tmp` lists or removes what aib and its tests left in the temp folder, and nothing else.
- One-line install and `aib update`: the website copy first, the copy in this repository if the website is unreachable.
