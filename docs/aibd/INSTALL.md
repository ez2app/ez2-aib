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
V=0.2.0-preview.1                                   # the version you want
T=aarch64-apple-darwin                              # Apple Silicon only in this preview
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
cd ~/Downloads
curl -fLO "$B/aibd-$V-$T" && curl -fLO "$B/SHA256SUMS"
grep " aibd-$V-$T\$" SHA256SUMS | shasum -a 256 -c -    # must print: OK
chmod +x "aibd-$V-$T"
mkdir -p ~/.local/bin && mv "aibd-$V-$T" ~/.local/bin/aibd
~/.local/bin/aibd --version
~/.local/bin/aibd selfcheck
```

Downloaded with a browser instead of `curl`? macOS marks the file; after the checksum says OK, remove the mark
with `xattr -d com.apple.quarantine ~/.local/bin/aibd`.

## Windows (PowerShell)

```powershell
$V = "0.2.0-preview.1"
$T = "x86_64-pc-windows-msvc"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/aibd-$V-$T.exe" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$want = ((Get-Content "$D\SHA256SUMS") | Where-Object { $_ -match " aibd-$V-$T\.exe$" }).Split(" ")[0]
$got  = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { Remove-Item "$D\aibd.exe"; throw "checksum does not match — file removed" } else { "checksum OK" }
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```

If SmartScreen says "Windows protected your PC", that is because the file is not signed. Only continue if the
checksum above said OK.

## What `selfcheck` tells you

It prints your platform, what aibd can do there, and every part that is **not verified** on that platform
(for example, starting and managing AI seat processes through aibd is not wired up yet; the code is
written). Exit code 0 means the check itself ran; read the "not verified" lines before relying on a feature.

## Removing it

macOS: `rm ~/.local/bin/aibd` · Windows: `Remove-Item -Recurse "$env:LOCALAPPDATA\aibd"`
