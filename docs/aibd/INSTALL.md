<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- DRAFT (11-08, C2): user-facing text — Andy reads it before the release is published. -->
# Installing aibd (preview)

aibd is the Rust version of the AIB background service. **This is a preview**: it runs next to the
Python `aib`, it does not replace it, and it is not on PyPI.

| Your computer | File |
|---|---|
| Mac with Apple Silicon (M1 or later) | `aibd-<version>-aarch64-apple-darwin` |
| Mac with an Intel processor | not in this preview yet (no Intel machine to verify it on) |
| Windows x64 (tested on GitHub's Windows machines; real-PC acceptance in progress) | `aibd-<version>-x86_64-pc-windows-msvc.exe` |

All files and `SHA256SUMS` are on the release page:
`https://github.com/ez2app/ez2-aib/releases/tag/aibd-v<version>`

**The files are not signed** (Apple and Windows signing are not set up yet). macOS and Windows may warn
you; the steps below check the file against `SHA256SUMS` before you run it.

## macOS

```sh
V=0.2.0-preview.7
F=aibd-$V-aarch64-apple-darwin
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
cd ~/Downloads \
  && curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
Paste it as one block. If a download fails or the checksum does not match, it stops there: nothing is installed
and nothing is run. To type plain `aibd`, add `~/.local/bin` to your PATH; the full path `~/.local/bin/aibd` works too.

Downloaded with a browser instead of `curl`? macOS marks the file; after the checksum says OK, remove the mark
with `xattr -d com.apple.quarantine ~/.local/bin/aibd`.

## Windows (PowerShell)

```powershell
$ErrorActionPreference = "Stop"
$V = "0.2.0-preview.7"
$F = "aibd-$V-x86_64-pc-windows-msvc.exe"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/$F" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$line = Select-String -SimpleMatch " $F" "$D\SHA256SUMS" | Select-Object -First 1
if (-not $line) { Remove-Item "$D\aibd.exe"; throw "$F is not in SHA256SUMS — file removed" }
$want = ([regex]::Match($line.Line, '^[0-9a-fA-F]{64}')).Value.ToLower()
$got = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if (-not $want -or $want -ne $got) { Remove-Item "$D\aibd.exe"; throw "checksum does not match — file removed" }
Unblock-File "$D\aibd.exe"
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```
Paste it as one block. If the checksum does not match it stops and deletes the download; nothing is run.

If SmartScreen says "Windows protected your PC", that is because the file is not signed. The block above only runs
the file after the checksum matched.

## What `selfcheck` tells you

It prints your platform, what aibd can do there, and every part that is **not verified** on that platform
(for example, starting and managing AI seat processes through aibd is not wired up yet; the code is
written). Exit code 0 means the check itself ran; read the "not verified" lines before relying on a feature.

## Auto-advance (自動推進) — macOS

Auto-advance is two more programs from the same release: `aib-drive` works out what each AIB seat can do
next, and `aib-slice` reads the open pull requests it needs. aibd wakes `aib-drive` every 10 minutes.
It needs the Python `aib` with seats already set up; its records go to `~/.ez2-aib/drive/`.

**Installed, it sends nothing.** Its four switches — `nudge`, `open_review`, `notify_fix`, `dispatch` — are off;
each 10-minute round only writes what it *would* send to `~/.ez2-aib/drive/act-dryrun.jsonl`.
`dispatch` is not open in this preview: leave it off.

### Install

Run the aibd block above first, then:

```sh
(
  set -e
  V=0.2.0-preview.7
  B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
  cd ~/Downloads && curl -fLO "$B/SHA256SUMS"
  for p in aib-drive aib-slice; do
    F=$p-$V-aarch64-apple-darwin
    curl -fLO "$B/$F"
    grep " $F\$" SHA256SUMS | shasum -a 256 -c -
    chmod +x "$F" && mv "$F" ~/.local/bin/$p
  done
  ~/.local/bin/aib-drive switch 2>&1 | head -1
)
```
Paste it as one block. A failed download or a checksum that does not match stops it before that program is moved.

`aib-slice` reads pull requests with the GitHub CLI from a local clone of the repository your seats work on.
Sign in once and tell auto-advance where the clone is (one line, the full path):

```sh
gh auth login
mkdir -p ~/.ez2-aib/drive && echo "$HOME/<path to your clone>" > ~/.ez2-aib/drive/refresh-repo
```
Without this file the scan is refused, the pull-request part of the list stays unknown, and `open_review` /
`notify_fix` never have anything to send.

Then start aibd at login with auto-advance on. This writes `~/Library/LaunchAgents/app.ez2.aibd.plist`:

```sh
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
P=~/Library/LaunchAgents/app.ez2.aibd.plist
cat > "$P" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>app.ez2.aibd</string>
  <key>ProgramArguments</key><array><string>$HOME/.local/bin/aibd</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AIBD_DRIVE_WAKE</key><string>1</string>
    <key>AIB_DRIVE</key><string>$HOME/.local/bin/aib-drive</string>
    <key>AIBD_LOG</key><string>info</string>
    <key>PATH</key><string>$HOME/.local/bin:$HOME/.ez2-aib/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Standard</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/ez2-aibd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ez2-aibd.log</string>
</dict>
</plist>
EOF
launchctl bootout gui/$(id -u)/app.ez2.aibd 2>/dev/null || true
plutil -lint "$P" && launchctl bootstrap gui/$(id -u) "$P" && launchctl list | grep app.ez2.aibd
```

Running it again (to update) first stops the aibd that is running, so the new file is the one loaded.

Two lines in it matter:
- `ProcessType` is `Standard`. With `Background`, macOS slows aibd and the programs it starts so much that a
  round runs past its time limit.
- `PATH` starts with `~/.local/bin`, where `aib` is. Without it, aibd's rounds cannot find `aib` and no message
  is sent even with a switch on.

Check it runs: within 10 minutes `~/.ez2-aib/drive/act-dryrun.jsonl` has a new line, and
`~/.local/bin/aib-drive next --json` lists no source as `unknown`.

### Turn a switch on

```sh
~/.local/bin/aib-drive switch nudge on --by <your seat, 5 letters> --why "<why>"
```
`nudge` reminds a seat of a message still waiting after 5 minutes; `open_review` asks a review seat to review a
pull request that is ready; `notify_fix` tells the author what to fix. Every change is written to
`~/.ez2-aib/drive/switches.jsonl`.

### Stop it

- One kind of message: `aib-drive switch <name> off --by <seat> --why "<why>"`.
- Everything at once, switches kept as they are: `aib-drive switch paused on --by <seat> --why "<why>"`
  (`paused off` to go on).
- aibd itself until the next login: `launchctl bootout gui/$(id -u)/app.ez2.aibd`.

### Remove it

```sh
launchctl bootout gui/$(id -u)/app.ez2.aibd
rm ~/Library/LaunchAgents/app.ez2.aibd.plist ~/.local/bin/aib-drive ~/.local/bin/aib-slice
```
Its records in `~/.ez2-aib/drive/` stay; delete that folder yourself if you no longer want them.

Windows: in this preview auto-advance only lists (`aib-drive next`); it does not send and does not start at login.

## Removing it

macOS: stop and remove auto-advance first if you set it up (above), then `rm ~/.local/bin/aibd` · Windows: `Remove-Item -Recurse "$env:LOCALAPPDATA\aibd"`
