#!/usr/bin/env bash
# N-ws-userpage knives UP1–UP2 (a9136 11-18; 519d0 19:55 裁 from 5d33a PREREAD-search N1): a user
# folder page reads a shared thread's posts without the text. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
up() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" \
        --expect-fail a_user_page_reads_a_shared_thread_without_the_text \
        -- cargo test -p aibd --locked --test threads_userpage a_user_page_reads_a_shared_thread_without_the_text; }
# UP1 a page's sender gives the text to a user page
up '        with_body: !user,' \
   '        with_body: true, // knife'
# UP2 the thread view gives the text whatever the sender may read
up '                    let body = s.with_body && (!dgrs_reader || dgrs_reads(p, switch_on));' \
   '                    let body = !dgrs_reader || dgrs_reads(p, switch_on); // knife'
