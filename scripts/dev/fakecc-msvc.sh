#!/bin/sh
# Fake MSVC cl.exe / lib.exe for `cargo check` / `cargo clippy --target *-windows-msvc` on a non-Windows host:
# creates every requested output file and compiles nothing. Good for build scripts that only need the C step to
# "succeed" (bundled SQLite); check / clippy never link, so nothing ever reads these files. Never use it for a build
# or a test — the result would be a binary with empty objects.
next=0
for a in "$@"; do
  if [ "$next" = 1 ]; then : > "$a"; next=0; continue; fi
  case "$a" in
    -Fo*|/Fo*) : > "${a#??o}";;
    -OUT:*|/OUT:*|-out:*|/out:*) : > "${a#*:}";;
    -o) next=1;;
  esac
done
exit 0
